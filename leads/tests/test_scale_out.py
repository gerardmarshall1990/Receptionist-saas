"""Stage 7 scale-out safety: reloads keep referenced rows; the crawl queue goes best-first."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from leads_engine import db
from leads_engine.compliance.gate import evaluate
from leads_engine.config_loader import load_config
from leads_engine.crawl import runner
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import REAL_CONFIG, needs_db
from tests.test_build import raw
from tests.test_compliance import GOOD

CFG = load_config(REAL_CONFIG)
pytestmark = [pytest.mark.db, needs_db]


def _load(url: str, tmp: Path, rows: list[dict[str, object]], tag: str) -> dict[str, int]:
    out = tmp / f"built_{tag}.parquet"
    build(tmp / f"x_{tag}", CFG, list(CFG.categories), "2026-11-18.0", out, "seed", rows_in=rows)
    return load(url, out, "all")


def test_reload_keeps_rows_an_experiment_references(fresh_db: str, tmp_path: Path) -> None:
    db.migrate(fresh_db)
    rows = [raw(f"p{i}", f"Plumber {chr(65 + i)}", region="US-TX", city="Austin", postcode=f"787{i:02d}")
            for i in range(4)]  # fmt: skip
    _load(fresh_db, tmp_path, rows, "a")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        kept = str(c.execute("select id from businesses where source_id = 'p0'").fetchone()[0])  # type: ignore[index]
        c.execute("insert into experiments (id, name, seed, params, config_hash)"
                  " values ('11111111-1111-1111-1111-111111111111', 'x', 's', '{}', 'h')")  # fmt: skip
        c.execute("insert into experiment_assignments (experiment_id, business_id, arm, call_order)"
                  " values ('11111111-1111-1111-1111-111111111111', %s, 'scored', 1)", (kept,))  # fmt: skip

    # A later build no longer contains p0 (e.g. now a chain or a duplicate) nor p1.
    counts = _load(fresh_db, tmp_path, rows[2:], "b")
    assert counts["removed_not_in_scope"] == 1  # p1: nothing references it
    assert counts["kept_not_in_latest_build"] == 1  # p0: the experiment does
    with psycopg.connect(fresh_db) as c:
        flags = c.execute("select flags from businesses where id = %s", (kept,)).fetchone()
        assert flags is not None
        assert "not_in_latest_build" in flags[0]

    # Back in a later build: the flag clears.
    _load(fresh_db, tmp_path, rows, "c")
    with psycopg.connect(fresh_db) as c:
        flags = c.execute("select flags from businesses where id = %s", (kept,)).fetchone()
        assert flags is not None
        assert "not_in_latest_build" not in flags[0]


def test_superseded_rows_are_never_exported_or_used_as_control() -> None:
    from dataclasses import replace

    old = replace(GOOD, business_flags=("not_in_latest_build",))
    assert "business_flag:not_in_latest_build" in evaluate(old, CFG.compliance).blocks
    assert evaluate(old, CFG.compliance, "control").status == "blocked"


def test_crawl_queue_claims_the_most_valuable_sites_first(fresh_db: str, tmp_path: Path) -> None:
    db.migrate(fresh_db)
    rows = [
        raw("law", "Lee Law Office", code="personal_injury_law", websites=["leelaw.com"], region="US-TX", city="Austin", postcode="78701"),
        raw("car", "Smith Auto Repair", code="automotive_repair", websites=["smithauto.com"], region="US-TX", city="Austin",
            postcode="78702"),
        raw("nop", "No Phone Plumbing", websites=["nophone.com"], phones=None, region="US-TX", city="Austin",
            postcode="78703"),
    ]  # fmt: skip
    _load(fresh_db, tmp_path, rows, "a")
    weights = {slug: c.value_weight for slug, c in CFG.categories.items()}
    assert runner.enqueue(fresh_db, "all", weights) == 3
    assert runner.enqueue(fresh_db, "all", weights) == 0  # idempotent
    with psycopg.connect(fresh_db, autocommit=True) as c:
        # One at a time: each claim must take the best remaining site.
        got = [runner.claim(c, "w1", 1, 3)[0][0] for _ in range(3)]
    # law (0.95) + phone beats auto repair (0.55) + phone beats plumbing (0.80) without a phone.
    assert got == ["leelaw.com", "smithauto.com", "nophone.com"]
