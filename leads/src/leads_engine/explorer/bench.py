"""Explorer benchmark at full scale on a THROWAWAY database.

Copies the real leads database (template copy), clones its businesses until the
target row count is reached (same category/state/flag distribution, new ids and
source ids), then times the explorer's API calls. The target database name must
end in "_bench" and is dropped and recreated each run; the real database is only
read (as a template).
"""

from __future__ import annotations

import statistics
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from leads_engine.config_loader import AppConfig
from leads_engine.settings import Settings

PATHS: list[tuple[str, str]] = [
    ("Default list (score sort)", "/api/leads"),
    ("Category + state, name sort", "/api/leads?category=plumbing&state=TX&sort=name"),
    ("Search 'rooter'", "/api/leads?q=rooter"),
    ("Search 'smith' (common)", "/api/leads?q=smith"),
    ("Callable now", "/api/leads?callable_now=true"),
    ("Flag filter, locations sort", "/api/leads?flag=multiple_locations&sort=locations"),
    ("Rare: WY + has email", "/api/leads?state=WY&has_email=yes"),
    ("State/city sort", "/api/leads?sort=place"),
    ("Deep page (cursor, page 50)", "__deep__"),
    ("Count, no filter", "/api/count"),
    ("Count, dental + email", "/api/count?category=dental&has_email=yes"),
    ("Count, WY", "/api/count?state=WY"),
    ("Facets, no filter", "/api/facets"),
    ("Facets, hvac (state+subcategory)", "/api/facets?category=hvac&facet=state&facet=subcategory"),
    ("Lead detail", "__detail__"),
]

_CLONE_COLS = (
    "source, source_release, name, name_key, category, subcategory, source_category_code, country,"
    " state, city, address, postcode, lat, lng, tz, phone, website, domain, staff_estimate, is_chain,"
    " location_count, flags, source_confidence, source_datasets, licenses, attribution,"
    " merged_source_ids, phone_status, email_status, score, tier"
)


def _admin_url(url: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


def prepare(source_url: str, bench_url: str, target_rows: int) -> dict[str, Any]:
    src_db = source_url.rpartition("/")[2]
    bench_db = bench_url.rpartition("/")[2]
    if not bench_db.endswith("_bench") or bench_db == src_db:
        raise ValueError("bench database name must end with '_bench' and differ from the source")
    t0 = time.monotonic()
    with psycopg.connect(_admin_url(source_url), autocommit=True) as admin:
        admin.execute(
            sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(bench_db))
        )
        admin.execute(
            sql.SQL("create database {} template {}").format(
                sql.Identifier(bench_db), sql.Identifier(src_db)
            )
        )
    with psycopg.connect(bench_url, autocommit=True) as conn:
        row = conn.execute("select count(*) from businesses").fetchone()
        base = int(row[0]) if row else 0
        if base == 0:
            raise ValueError("source database has no businesses to clone")
        copies = 0
        while True:
            row = conn.execute("select count(*) from businesses").fetchone()
            have = int(row[0]) if row else 0
            if have >= target_rows:
                break
            copies += 1
            take = min(base, target_rows - have)
            q = (
                f"insert into businesses (source_id, in_pilot_sample, {_CLONE_COLS})"  # noqa: S608
                f" select source_id || ':bench{copies}', false, {_CLONE_COLS}"
                " from businesses where source_id not like '%%:bench%%' order by id limit %s"
            )
            conn.execute(q, (take,))
        conn.execute("vacuum analyze businesses")
        size = conn.execute("select pg_total_relation_size('businesses')").fetchone()
    return {
        "rows": have,
        "base_rows": base,
        "seconds": round(time.monotonic() - t0),
        "businesses_bytes": int(size[0]) if size else 0,
    }


def run(
    settings: Settings, cfg: AppConfig, bench_url: str, repeats: int = 7
) -> list[dict[str, Any]]:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from fastapi.testclient import TestClient

    from leads_engine.explorer.app import create_app

    s = settings.model_copy(update={"database_url": bench_url, "ui_token": None})
    client = TestClient(
        create_app(s, cfg, agg_cache_seconds=0)
    )  # measure real queries, not the cache
    first = client.get("/api/leads?sort=name&limit=50").json()
    lead_id = first["items"][0]["id"]
    results = []
    for label, path in PATHS:
        times = []
        for _ in range(repeats):
            t = time.perf_counter()
            if path == "__deep__":
                cur = first["next_cursor"]
                for _page in range(49):
                    cur = client.get(f"/api/leads?sort=name&limit=50&cursor={cur}").json()[
                        "next_cursor"
                    ]
                times.append((time.perf_counter() - t) * 1000 / 49)
                continue
            p = f"/api/leads/{lead_id}" if path == "__detail__" else path
            r = client.get(p)
            times.append((time.perf_counter() - t) * 1000)
            if r.status_code != 200:
                raise RuntimeError(f"{p}: HTTP {r.status_code} {r.text[:200]}")
        times.sort()
        results.append({
            "label": label, "path": path,
            "p50": statistics.median(times), "p95": times[max(0, round(0.95 * len(times)) - 1)],
        })  # fmt: skip
    return results


def report(prep: dict[str, Any], results: list[dict[str, Any]], target_ms: float) -> str:
    lines = [
        "# Lead explorer benchmark",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by `leads ui bench`. Throwaway database with"
        f" **{prep['rows']:,} businesses** (the {prep['base_rows']:,} real kept rows cloned to scale, same"
        f" category/state/flag mix). Businesses table incl. indexes: {prep['businesses_bytes'] / 1e9:.2f} GB."
        " Times are end-to-end API calls (FastAPI + Postgres 16, same machine), 7 runs each.",
        "",
        f"Target: p95 under {target_ms:.0f} ms.",
        "",
        "| call | p50 ms | p95 ms | ok |",
        "|---|---|---|---|",
    ]
    for r in results:
        ok = "yes" if r["p95"] <= target_ms else "**no**"
        lines.append(f"| {r['label']} | {r['p50']:.0f} | {r['p95']:.0f} | {ok} |")
    lines += [
        "",
        'Notes: counts are exact up to 50,000 matches and a labelled planner estimate ("≈") beyond that.',
        "Counts and facets are cached for 60 s per filter (cleared on suppress); every timed call here is",
        "measured on a fresh app with an empty cache, so the numbers are uncached.",
        "Deep page = average time per page while walking 49 pages with the cursor.",
        "",
    ]
    return "\n".join(lines)


def existing(bench_url: str) -> dict[str, Any]:
    """Reuse a bench DB from an earlier run; apply pending migrations so indexes match."""
    from leads_engine import db

    db.migrate(bench_url)
    with psycopg.connect(bench_url, autocommit=True) as conn:
        conn.execute("analyze businesses")
        n = conn.execute(
            "select count(*), count(*) filter (where source_id not like '%:bench%') from businesses"
        ).fetchone()
        size = conn.execute("select pg_total_relation_size('businesses')").fetchone()
    if n is None or size is None:
        raise RuntimeError("bench database is empty or missing")
    return {"rows": n[0], "base_rows": n[1], "seconds": 0, "businesses_bytes": size[0]}


def main(
    settings: Settings,
    cfg: AppConfig,
    bench_url: str,
    target_rows: int,
    out: Path,
    reuse: bool = False,
) -> list[dict[str, Any]]:
    prep = existing(bench_url) if reuse else prepare(settings.database_url, bench_url, target_rows)
    results = run(settings, cfg, bench_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(prep, results, 300))
    return results
