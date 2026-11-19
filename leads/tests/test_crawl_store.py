"""Queue -> crawl (local server) -> extract -> Postgres, end to end."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import psycopg
import pytest

from leads_engine import db
from leads_engine.config_loader import load_config
from leads_engine.crawl import runner
from leads_engine.crawl.store import run_extract
from tests.conftest import REAL_CONFIG, needs_db
from tests.test_crawl_fetcher import Site, html, plumber_site, site  # noqa: F401 (fixture)

CFG = load_config(REAL_CONFIG)
FAST = CFG.crawl.model_copy(update={"per_site_delay_s": 1.0, "max_pages_per_site": 3})

pytestmark = [pytest.mark.db, needs_db]


def _business(
    conn: psycopg.Connection[tuple[Any, ...]],
    name: str,
    website: str,
    domain: str,
    phone: str | None,
) -> str:
    row = conn.execute(
        "insert into businesses (source, source_id, source_release, name, name_key, category, subcategory,"
        " source_category_code, country, state, city, tz, phone, website, domain, licenses, attribution,"
        " in_pilot_sample) values ('overture', %s, 'r', %s, lower(%s), 'plumbing', 'plumbing', 'plumbing',"
        " 'US', 'TX', 'Austin', 'America/Chicago', %s, %s, %s, array['CDLA-Permissive-2.0'], 'a', true)"
        " returning id",
        (name, name, name.replace("'", ""), phone, website, domain),
    ).fetchone()
    assert row is not None
    if phone:
        conn.execute(
            "insert into phones (business_id, e164, sources, valid_format) values (%s, %s, array['overture'], true)",
            (row[0], phone),
        )
    return str(row[0])


def test_crawl_and_extract_end_to_end(fresh_db: str, site: Site, tmp_path: Path) -> None:  # noqa: F811
    db.migrate(fresh_db)
    plumber_site(site)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        joe = _business(
            conn, "Joe's Plumbing", site.base + "/", "joesplumbing.test", "+15124741234"
        )
        # second location of the same business shares the site: crawled once
        joe2 = _business(conn, "Joe's Plumbing North", site.base + "/", "joesplumbing.test", None)
        dead = _business(conn, "Dead Site Plumbing", "http://127.0.0.1:9/", "deadsite.test", None)
    assert runner.enqueue(fresh_db, "pilot") == 2
    assert runner.enqueue(fresh_db, "pilot") == 0  # idempotent

    counts = asyncio.run(runner.run(fresh_db, FAST, tmp_path, max_sites=10, time_limit_s=120))
    assert counts["claimed"] == 2
    assert counts["done"] == 1
    with psycopg.connect(fresh_db) as conn:
        st = {str(r[0]): str(r[1]) for r in conn.execute("select domain, status from crawl_sites")}
        assert st == {"joesplumbing.test": "done", "deadsite.test": "error"}
        assert conn.execute("select count(*) from pages").fetchone() == (3,)
        files = conn.execute("select distinct parquet_file from pages").fetchall()
        assert all(Path(f[0]).exists() for f in files)
        # Politeness measured on real time: idle gap between a response and the next request.
        gaps = conn.execute(
            "select extract(epoch from requested_at - lag(fetched_at) over (partition by domain order by fetched_at))"
            " from pages"
        ).fetchall()
        measured = [float(g[0]) for g in gaps if g[0] is not None]
        assert measured
        assert min(measured) >= FAST.per_site_delay_s - 0.01

    out = run_extract(fresh_db, CFG, tmp_path)
    assert out["businesses"] == 2  # the dead site is still retryable: not extracted yet
    with psycopg.connect(fresh_db) as conn:
        sigs = {
            r[0]: r[1]
            for r in conn.execute(
                "select signal, value from signals where business_id = %s", (joe,)
            )
        }
        assert sigs["leave_message_text"] is True
        assert sigs["no_online_booking"] is True
        reasons = conn.execute(
            "select signal, evidence, source_url from reasons where business_id = %s and stage = 'stage2'",
            (joe,),
        ).fetchall()
        assert {r[0] for r in reasons} >= {
            "leave_message_text",
            "emergency_247_claim",
            "phone_only_booking",
        }
        assert all(r[1] and r[2] for r in reasons)  # every reason cites evidence + page
        emails = conn.execute(
            "select email, origin, source_url from emails where business_id = %s order by email",
            (joe,),
        ).fetchall()
        assert ("office@joesplumbing.com", "business_site") in [(e[0], e[1]) for e in emails]
        phone = conn.execute(
            "select sources, agree_count, status from phones where business_id = %s and e164 = '+15124741234'",
            (joe,),
        ).fetchone()
        assert phone is not None
        assert sorted(phone[0]) == ["overture", "website"]
        assert phone[1] == 2
        assert phone[2] == "likely"  # corroborated, not "verified" (that needs the T4 lookup)
        b = conn.execute(
            "select phone_status, email_status, crawled_at from businesses where id = %s", (joe,)
        ).fetchone()
        assert b is not None
        assert b[:2] == ("likely", "site_found")
        assert b[2] is not None
        # second location: no Overture phone -> gets the site's phone, same signals
        b2 = conn.execute("select phone from businesses where id = %s", (joe2,)).fetchone()
        assert b2 == ("+15124741234",)
        assert conn.execute(
            "select crawled_at from businesses where id = %s", (dead,)
        ).fetchone() == (None,)

    # Re-extracting converges: same row counts, nothing duplicated.
    def snapshot() -> tuple[object, ...]:
        with psycopg.connect(fresh_db) as conn:
            return tuple(
                conn.execute(f"select count(*) from {t}").fetchone()
                for t in ("signals", "reasons", "emails", "phones")
            )

    before = snapshot()
    run_extract(fresh_db, CFG, tmp_path)
    assert snapshot() == before


def test_queue_claims_respect_status_and_stale_locks(fresh_db: str) -> None:
    db.migrate(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute(
            "insert into crawl_sites (domain, start_url, status, attempts, locked_at) values"
            " ('pending.test', 'http://a', 'pending', 0, null),"
            " ('blocked.test', 'http://b', 'blocked', 1, null),"
            " ('done.test', 'http://c', 'done', 1, null),"
            " ('stale.test', 'http://d', 'running', 1, now() - interval '1 hour'),"
            " ('fresh.test', 'http://e', 'running', 1, now()),"
            " ('retry.test', 'http://f', 'error', 1, null),"
            " ('spent.test', 'http://g', 'error', 3, null)"
        )
        conn.execute("update crawl_sites set next_attempt_at = now() - interval '1 minute'")
        got = {d for d, _ in runner.claim(conn, "w", 100, max_attempts=3)}
    assert got == {"pending.test", "stale.test", "retry.test"}  # never blocked/done/fresh/spent


def test_local_pool_timeout_does_not_use_up_an_attempt(fresh_db: str, tmp_path: Path) -> None:
    from leads_engine.crawl.fetcher import LOCAL_ERROR, SiteResult

    db.migrate(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute(
            "insert into crawl_sites (domain, start_url) values ('a.test', 'http://a.test/')"
        )
        assert runner.claim(conn, "w", 1, 3) == [("a.test", "http://a.test/")]
        buf = runner.Buffer(tmp_path, "run")
        buf.add(SiteResult("a.test", "error", detail=LOCAL_ERROR, retryable=True))
        buf.flush(conn, 3)
        row = conn.execute("select status, attempts from crawl_sites").fetchone()
    assert row == ("pending", 0)


def test_circuit_breaker_stops_on_repeated_local_errors(
    fresh_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from leads_engine.crawl import fetcher

    db.migrate(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute(
            "insert into crawl_sites (domain, start_url)"
            " select 's' || g || '.test', 'http://s' || g || '.test/' from generate_series(1, 40) g"
        )

    async def broken(*args: object, **kw: object) -> fetcher.SiteResult:
        return fetcher.SiteResult(str(args[2]), "error", detail=fetcher.LOCAL_ERROR, retryable=True)

    monkeypatch.setattr(runner, "crawl_site", broken)
    with pytest.raises(RuntimeError, match="consecutive local"):
        asyncio.run(runner.run(fresh_db, FAST, tmp_path, max_sites=1000, time_limit_s=60))
    with psycopg.connect(fresh_db) as conn:
        rows = conn.execute("select status, max(attempts) from crawl_sites group by 1").fetchall()
    assert rows == [("pending", 0)]  # nothing burned, nothing stuck in "running"
