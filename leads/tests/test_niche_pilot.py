"""Tier 1 niche pilot: v16 niche signals, pending gate, additive load, batch crawl claims,
merged export (base rows untouched) and protected exports."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import pytest
from psycopg import sql

from leads_engine import db
from leads_engine.compliance.export import merge_export, prune_exports, run_export
from leads_engine.compliance.gate import evaluate
from leads_engine.crawl import runner
from leads_engine.crawl.extract import extract
from leads_engine.universe.build import build
from leads_engine.universe.load import load, load_additive
from tests.conftest import needs_db
from tests.test_build import raw
from tests.test_compliance import CFG, GOOD, _export, _seed
from tests.test_crawl_extract import _p


def _sig(text: str, category: str) -> set[str]:
    return set(extract(_p(text), {"name_key": "x", "category": category}, CFG).signals)


@pytest.mark.parametrize(
    ("text", "category", "signal", "expected"),
    [
        ("Locked out? We offer 24/7 locksmith service across Phoenix.", "locksmith", "emergency_247_claim", True),
        ("Fast emergency lockout help, day or night.", "locksmith", "emergency_247_claim", True),
        ("24/7 towing and roadside assistance.", "towing", "emergency_247_claim", True),
        ("We provide emergency septic pumping for backups.", "septic", "emergency_247_claim", True),
        ("Broken spring? Same day emergency garage door repair.", "garage_door", "emergency_247_claim", True),
        # A vet sending owners to another hospital is not the clinic's own 24/7 claim.
        ("After hours, please contact the emergency vet at Metro Animal ER.", "veterinary", "emergency_247_claim", False),
        ("We are accepting new patients! Call to schedule.", "chiropractic", "new_patients_welcome", True),
        ("New clients always welcome at our clinic.", "veterinary", "new_patients_welcome", True),
        ("New patient special: exam and x-rays for $49.", "dental", "new_patients_welcome", True),
        ("We are not accepting new patients at this time.", "dental", "new_patients_welcome", False),
        ("Dr. Lee is no longer accepting new patients.", "chiropractic", "new_patients_welcome", False),
    ],
)  # fmt: skip
def test_v16_niche_signals(text: str, category: str, signal: str, expected: bool) -> None:
    assert (signal in _sig(text, category)) is expected


def test_pending_batch_never_passes() -> None:
    assert evaluate(GOOD, CFG.compliance).status == "pass"
    pending = evaluate(replace(GOOD, niche_batch="tier1-pilot"), CFG.compliance)
    assert pending.status == "pending"
    assert "pending:dnc_line_type_country_rules" in pending.warnings
    blocked = evaluate(replace(GOOD, niche_batch="tier1-pilot", tier="C"), CFG.compliance)
    assert blocked.status == "blocked"
    # The control arm ignores batches (it never exports).
    assert (
        evaluate(replace(GOOD, niche_batch="tier1-pilot"), CFG.compliance, "control").status
        == "pass"
    )


def _locksmith_built(tmp: Path) -> Path:
    rows = [
        raw("L1", "Ace Lock & Key", code="key_and_locksmith", websites=["acelock.com"], phones=["+16022740191"]),
        raw("L2", "Bolt Locksmith", code="key_and_locksmith", websites=["boltlock.com"], phones=["+16022740192"]),
        raw("L3", "Key Pros", code="key_and_locksmith", websites=["keypros.com"], phones=["+16022740193"]),
        # Same website as an existing plumber: a duplicate of an existing business, skipped.
        raw("L4", "Alpha Locks", code="key_and_locksmith", websites=["alphaplumbing.com"], phones=["+16022740194"]),
    ]  # fmt: skip
    out = tmp / "built_lock.parquet"
    build(tmp / "x", CFG, ["locksmith"], "2026-11-18.0", out, "seed", rows_in=rows)
    return out


def _snapshot(url: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as c:
        return c.execute(
            "select id, name, category, flags, in_pilot_sample, phone_status, email_status,"
            " updated_at, niche_batch from businesses where niche_batch is null order by id"
        ).fetchall()


@pytest.mark.db
@needs_db
def test_additive_load_leaves_existing_rows_untouched(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    before = _snapshot(fresh_db)
    built = _locksmith_built(tmp_path)
    counts = load_additive(fresh_db, built, "tier1-pilot", per_niche=2, seed="s")
    assert counts["inserted"] == 2  # 3 new locksmiths, capped at 2
    assert counts["dup_of_existing"] == 1
    assert _snapshot(fresh_db) == before
    with psycopg.connect(fresh_db) as c:
        assert c.execute(
            "select count(*) from businesses where niche_batch = 'tier1-pilot' and in_pilot_sample"
        ).fetchone() == (2,)
        assert c.execute("select count(*) from phones p join businesses b on b.id = p.business_id"
                         " where b.niche_batch = 'tier1-pilot'").fetchone() == (2,)  # fmt: skip
    # Idempotent: nothing new on a second run; existing rows still untouched.
    assert load_additive(fresh_db, built, "tier1-pilot", per_niche=2, seed="s")["inserted"] == 0
    # A later core load never flags or deletes the batch.
    core = tmp_path / "built.parquet"
    load(fresh_db, core, "all")
    with psycopg.connect(fresh_db) as c:
        assert c.execute(
            "select count(*) from businesses where niche_batch = 'tier1-pilot'"
            " and not ('not_in_latest_build' = any(flags))"
        ).fetchone() == (2,)


@pytest.mark.db
@needs_db
def test_batch_claims_only_batch_sites(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    load_additive(fresh_db, _locksmith_built(tmp_path), "tier1-pilot", per_niche=5, seed="s")
    runner.enqueue(fresh_db, "all", {c: 1.0 for c in CFG.categories})
    with psycopg.connect(fresh_db, autocommit=True) as c:
        got = {d for d, _ in runner.claim(c, "w", 100, max_attempts=3, niche_batch="tier1-pilot")}
        assert got == {"acelock.com", "boltlock.com", "keypros.com"}
        rest = {d for d, _ in runner.claim(c, "w", 100, max_attempts=3)}
    assert "alphaplumbing.com" in rest
    assert not rest & got


@pytest.mark.db
@needs_db
def test_merge_keeps_base_rows_and_marks_batch_pending(fresh_db: str, tmp_path: Path) -> None:
    ids = _seed(fresh_db, tmp_path)
    base = _export(fresh_db, tmp_path)
    load_additive(fresh_db, _locksmith_built(tmp_path), "tier1-pilot", per_niche=5, seed="s")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("update phones p set status = 'likely' from businesses b"
                  " where b.id = p.business_id and b.niche_batch = 'tier1-pilot'")  # fmt: skip
        c.execute("update businesses set phone_status = 'likely' where niche_batch = 'tier1-pilot'")
        for (bid,) in c.execute(
            "select id from businesses where niche_batch = 'tier1-pilot'"
        ).fetchall():
            c.execute(
                "insert into scores (business_id, pain, value, reachability, fit, total, tier,"
                " components, model_version) values (%s, 50, 50, 50, 50, 70, 'B', '[]', 'test')",
                (bid,),
            )
            c.execute(
                "insert into reasons (business_id, signal, dimension, evidence, source_url, weight,"
                " stage) values (%s, 'leave_message_text', 'pain', 'Leave a message after the tone',"
                " 'https://x.test/contact', 10, 'stage2')",
                (bid,),
            )
    # A plain export never writes pending rows.
    plain = run_export(fresh_db, CFG, where=sql.SQL("b.niche_batch is not null"), params=[],
                       origin="cli", out_root=tmp_path / "exports", dry_run=True)  # fmt: skip
    assert plain.records == []
    assert plain.counts.get("pending") == 3

    res = merge_export(fresh_db, CFG, base=base.path, batch="tier1-pilot", out_root=tmp_path / "m")
    merged = pq.read_table(res.path / "leads.parquet").to_pylist()
    base_rows = pq.read_table(base.path / "leads.parquet").to_pylist()
    core = [{k: v for k, v in r.items() if k not in ("niche_batch", "subcategory")}
            for r in merged if r["niche_batch"] == "core-7"]  # fmt: skip
    assert core == base_rows
    batch = [r for r in merged if r["niche_batch"] == "tier1-pilot"]
    assert len(batch) == 3
    assert {r["compliance_status"] for r in batch} == {"pending"}
    assert all(r["why_reasons"] for r in batch)
    assert ids  # seeded

    # Both exports are protected from pruning.
    _export(fresh_db, tmp_path)
    _export(fresh_db, tmp_path)
    prune_exports(fresh_db, keep=1)
    assert (base.path / "leads.parquet").exists()
    assert (res.path / "leads.parquet").exists()
    with psycopg.connect(fresh_db) as c:
        assert c.execute("select count(*) from exports where protected").fetchone() == (2,)
    db.migrate(fresh_db)  # idempotent
