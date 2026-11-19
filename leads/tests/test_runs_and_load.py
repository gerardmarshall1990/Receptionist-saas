from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from leads_engine import db
from leads_engine.config_loader import load_config
from leads_engine.runs import BudgetExceededError, Run, recorded_run
from leads_engine.universe.build import build
from leads_engine.universe.load import load_sample
from tests.conftest import REAL_CONFIG, needs_db
from tests.test_build import raw

# ------------------------------------------------------------------ budget


def test_budget_is_a_hard_stop() -> None:
    run = Run("r", "s", budget_usd=1.0, dry_run=False)
    run.add_cost(0.6)
    run.add_cost(0.4)
    with pytest.raises(BudgetExceededError):
        run.add_cost(0.01)
    assert run.cost_usd == pytest.approx(1.0)
    with pytest.raises(ValueError, match=">= 0"):
        run.add_cost(-1)


def test_zero_budget_allows_free_work_only() -> None:
    run = Run("r", "s", budget_usd=0.0, dry_run=False)
    run.add_cost(0.0)
    with pytest.raises(BudgetExceededError):
        run.add_cost(0.001)


@pytest.mark.db
@needs_db
def test_recorded_run_statuses(fresh_db: str) -> None:
    db.migrate(fresh_db)
    with recorded_run(fresh_db, "t.ok", "h", dry_run=True, budget_usd=0, params={"a": 1}) as run:
        run.counts = {"rows": 3}
    with (
        pytest.raises(RuntimeError),
        recorded_run(fresh_db, "t.fail", "h", dry_run=False, budget_usd=0),
    ):
        raise RuntimeError("boom")
    with (
        pytest.raises(BudgetExceededError),
        recorded_run(fresh_db, "t.budget", "h", dry_run=False, budget_usd=1) as r,
    ):
        r.add_cost(2)
    with psycopg.connect(fresh_db) as conn:
        got = {
            s: (st, c, e)
            for s, st, c, e in conn.execute("select stage, status, counts, error from runs")
        }
    assert got["t.ok"] == ("succeeded", {"rows": 3}, None)
    assert got["t.fail"][0] == "failed"
    assert "boom" in (got["t.fail"][2] or "")
    assert got["t.budget"][0] == "stopped_budget"


# -------------------------------------------------------------------- load


def _built(tmp_path: Path, names: list[str]) -> Path:
    cfg = load_config(REAL_CONFIG)
    rows = [raw(f"id-{n}", n, emails=["info@x.com"], websites=["x.com"] if i == 0 else None,
                postcode=f"7{i:04d}") for i, n in enumerate(names)]  # fmt: skip
    out = tmp_path / f"built_{len(names)}.parquet"
    build(
        tmp_path / "unused",
        cfg,
        ["plumbing", "hvac", "dental"],
        "2026-11-18.0",
        out,
        "seed",
        rows_in=rows,
    )
    return out


@pytest.mark.db
@needs_db
def test_load_is_idempotent_and_tracks_sample_changes(fresh_db: str, tmp_path: Path) -> None:
    db.migrate(fresh_db)
    first = _built(tmp_path, ["Alpha Plumbing", "Bravo Plumbing", "Charlie Plumbing"])
    c1 = load_sample(fresh_db, first)
    assert c1["businesses"] == 3
    load_sample(fresh_db, first)  # rerun: same rows, no duplicates
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select count(*) from businesses").fetchone() == (3,)
        assert conn.execute("select count(*) from phones").fetchone() == (3,)
        row = conn.execute(
            "select licenses, attribution, in_pilot_sample from businesses where name = 'Alpha Plumbing'"
        ).fetchone()
        assert row is not None
        assert row[0] == ["CDLA-Permissive-2.0"]
        assert "Overture Maps Foundation" in row[1]
        assert row[2] is True
        origin = conn.execute("select distinct origin from emails").fetchall()
        assert origin == [("source_listed",)]  # never mistaken for site-verified

    second = _built(tmp_path, ["Alpha Plumbing", "Bravo Plumbing"])  # Charlie left the sample
    c2 = load_sample(fresh_db, second)
    assert c2["removed_not_in_scope"] == 1
    with psycopg.connect(fresh_db) as conn:
        names = sorted(r[0] for r in conn.execute("select name from businesses"))
    assert names == ["Alpha Plumbing", "Bravo Plumbing"]


@pytest.mark.db
@needs_db
def test_load_all_scope_and_contact_status(fresh_db: str, tmp_path: Path) -> None:
    from leads_engine.universe.load import load

    db.migrate(fresh_db)
    cfg = load_config(REAL_CONFIG)
    rows = [
        raw("a", "Alpha Plumbing", emails=["info@alpha.com"], websites=["alpha.com"]),
        raw("b", "Bravo Plumbing", phones=None, postcode="78702"),
        raw("c", "Aspen Dental", code="dental_clinic", postcode="78703"),  # excluded chain
    ]
    out = tmp_path / "b.parquet"
    build(tmp_path / "x", cfg, ["plumbing", "dental"], "2026-11-18.0", out, "s", rows_in=rows)
    counts = load(fresh_db, out, "all")
    assert counts["businesses"] == 2  # excluded rows never reach Postgres
    with psycopg.connect(fresh_db) as conn:
        got = dict(
            (n, (ps, es))
            for n, ps, es in conn.execute("select name, phone_status, email_status from businesses")
        )
    assert got == {
        "Alpha Plumbing": ("unverified", "source_listed"),
        "Bravo Plumbing": ("none", "none"),
    }
