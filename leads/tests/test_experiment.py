"""Stage 6: statistics (hand-checked), control profile, assignment, outcome import, readout."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest

from leads_engine import db
from leads_engine.compliance.gate import evaluate
from leads_engine.config_loader import load_config
from leads_engine.experiment.assign import CALL_LIST_FIELDS, create, quotas, write_call_list
from leads_engine.experiment.outcomes import import_csv
from leads_engine.experiment.readout import ArmStats, readout, verdict
from leads_engine.experiment.stats import (
    Attempt,
    lead_outcome,
    required_n,
    two_proportion_p,
    wilson,
)
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import REAL_CONFIG, needs_db
from tests.test_build import raw
from tests.test_compliance import GOOD

CFG = load_config(REAL_CONFIG)


# ------------------------------------------------------------------ statistics


def test_wilson_matches_reference_values() -> None:
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    lo, hi = wilson(0, 10)
    assert lo == pytest.approx(0, abs=1e-12)
    assert hi == pytest.approx(0.2775, abs=1e-4)
    assert wilson(0, 0) == (0.0, 1.0)


def test_two_proportion_p_value() -> None:
    # 4% vs 2% at n=1000 each: pooled 3%, z = 2.62, p = 0.0088 (worked by hand).
    assert two_proportion_p(40, 1000, 20, 1000) == pytest.approx(0.0088, abs=5e-4)
    assert two_proportion_p(20, 1000, 20, 1000) == pytest.approx(1.0)
    assert two_proportion_p(0, 0, 1, 10) == 1.0


def test_required_n_matches_the_plan() -> None:
    assert required_n(0.02, 0.04) == 1141  # 2% -> 4% demo rate, 80% power
    assert required_n(0.30, 0.40) == 356
    assert required_n(0.05, 0.05) is None


def test_lead_outcome_collapses_attempts() -> None:
    o = lead_outcome(
        [Attempt("no_answer"), Attempt("voicemail"), Attempt("decision_maker", demo_booked=True)]
    )
    assert o == {"dialed": True, "reached": True, "conversation": True, "demo": True, "trial": False,
                 "paid": False, "bad_data": False, "has_service": False, "do_not_call": False}  # fmt: skip
    gk = lead_outcome([Attempt("gatekeeper")])
    assert gk["reached"]
    assert not gk["conversation"]
    assert lead_outcome([Attempt("wrong_number")])["bad_data"]
    assert lead_outcome([])["dialed"] is False


def test_quotas_split_by_pool_mix() -> None:
    q = quotas(Counter(plumbing=1352, dental=1176, hvac=1142), 1000)
    assert sum(q.values()) == 1000
    assert q == {"plumbing": 368, "dental": 321, "hvac": 311}


def _arm(dialed: int, demos: int) -> ArmStats:
    return ArmStats(assigned=dialed, dialed=dialed, attempts=dialed, hits=Counter(demo=demos))


def test_verdict_needs_enough_data_then_significance() -> None:
    code, text = verdict(_arm(100, 10), _arm(100, 1), CFG)
    assert code == "not_enough_data"
    assert "300 per arm" in text
    assert verdict(_arm(1000, 40), _arm(1000, 20), CFG)[0] == "scored_wins"
    assert verdict(_arm(1000, 20), _arm(1000, 40), CFG)[0] == "control_wins"
    code, text = verdict(_arm(1000, 25), _arm(1000, 20), CFG)
    assert code == "no_difference_yet"
    assert "dialled per arm" in text


# ------------------------------------------------------------------ control profile


def test_control_profile_ignores_score_but_keeps_safety() -> None:
    weak = replace(GOOD, tier="D", n_reasons=0, dead_reason="no fit", phone_status="unverified",
                   business_flags=("phone_not_on_site",))  # fmt: skip
    assert evaluate(weak, CFG.compliance).status == "blocked"
    assert evaluate(weak, CFG.compliance, "control").status == "pass"
    for unsafe in (
        replace(weak, business_flags=("shared_phone",)),
        replace(weak, phone_flags=("toll_free",)),
        replace(weak, suppression={"phone_e164": {weak.phone_e164 or ""}}),
        replace(weak, phone_e164=None, phone_status="none"),
        replace(weak, country="CA"),
    ):
        assert evaluate(unsafe, CFG.compliance, "control").status == "blocked"


# ------------------------------------------------------------------ Postgres


def _seed(url: str, tmp: Path) -> dict[str, str]:
    db.migrate(url)
    rows = [
        raw(
            f"p{i}",
            f"Plumber {chr(65 + i)} Co",
            region="US-TX",
            city="Austin",
            postcode=f"787{i:02d}",
        )
        for i in range(12)
    ]
    rows += [raw(f"h{i}", f"Heating {chr(65 + i)} Inc", code="hvac_service", region="US-CO", city="Denver",
                 postcode=f"802{i:02d}", lat=39.7, lng=-104.9) for i in range(12)]  # fmt: skip
    out = tmp / "built.parquet"
    build(tmp / "x", CFG, ["plumbing", "hvac"], "2026-11-18.0", out, "seed", rows_in=rows)
    load(url, out, "all")
    with psycopg.connect(url, autocommit=True) as c:
        ids = {r[0]: str(r[1]) for r in c.execute("select name, id from businesses")}
        for name, bid in ids.items():
            top = name.split()[1] in "ABCD"  # 4 per category are scored A/B with confirmed phones
            tier, total = ("A", 80) if top else ("D", 20)
            c.execute(
                "insert into scores (business_id, pain, value, reachability, fit, total, tier, components,"
                " model_version) values (%s, 50, 50, 50, 50, %s, %s, '[]', 'test')",
                (bid, total, tier),
            )
            if top:
                c.execute("update phones set status = 'likely' where business_id = %s", (bid,))
                c.execute(
                    "insert into reasons (business_id, signal, dimension, evidence, source_url, weight, stage)"
                    " values (%s, 'phone_only_booking', 'pain', 'Call to book', 'https://x.test/', 6, 'stage2')",
                    (bid,),
                )
    return ids


def _write_outcomes(path: Path, rows: list[list[Any]]) -> Path:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "lead_ref",
                "called_at",
                "caller",
                "disposition",
                "demo_booked",
                "trial_started",
                "paid",
                "notes",
            ]
        )
        w.writerows(rows)
    return path


@pytest.mark.db
@needs_db
def test_create_freezes_balanced_blinded_arms(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    res = create(fresh_db, CFG, "t1", "seed-1", tmp_path / "exp", per_arm=4)
    assert res["quotas"] == {"hvac": 2, "plumbing": 2}
    with psycopg.connect(fresh_db) as c:
        rows = c.execute(
            "select a.arm, b.category, a.tier_at_assignment, a.business_id from experiment_assignments a"
            " join businesses b on b.id = a.business_id"
        ).fetchall()
        assert Counter((arm, cat) for arm, cat, _, _ in rows) == {
            ("scored", "plumbing"): 2, ("scored", "hvac"): 2, ("control", "plumbing"): 2, ("control", "hvac"): 2,
        }  # fmt: skip
        assert {t for arm, _, t, _ in rows if arm == "scored"} == {"A"}
        assert len({bid for *_, bid in rows}) == 8  # nobody in both arms
        with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
            c.execute("update experiment_assignments set arm = 'control'")
    with pytest.raises(ValueError, match="already exists"):
        create(fresh_db, CFG, "t1", "seed-1", tmp_path / "exp", per_arm=4)

    # Blinded: no arm, tier, score, reason or opener columns.
    with (tmp_path / "exp" / "t1" / "call_list.csv").open() as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames or ()) == CALL_LIST_FIELDS
        assert len(list(reader)) == 8
    assert "do_not_call" in (tmp_path / "exp" / "t1" / "PROTOCOL.md").read_text()

    # Same seed + same data -> same picks.
    create(fresh_db, CFG, "t2", "seed-1", tmp_path / "exp", per_arm=4)
    with psycopg.connect(fresh_db) as c:
        sets = c.execute(
            "select e.name, array_agg(a.business_id::text order by a.business_id) from experiment_assignments a"
            " join experiments e on e.id = a.experiment_id group by e.name"
        ).fetchall()
    assert sets[0][1] == sets[1][1]


@pytest.mark.db
@needs_db
def test_outcomes_import_validates_dedupes_and_suppresses(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    create(fresh_db, CFG, "t1", "seed-1", tmp_path / "exp", per_arm=4)
    with psycopg.connect(fresh_db) as c:
        leads = [
            str(r[0])
            for r in c.execute("select business_id from experiment_assignments order by call_order")
        ]
    good = _write_outcomes(tmp_path / "o.csv", [
        [leads[0], "2026-12-01T09:15:00-06:00", "al", "no_answer", "no", "no", "no", ""],
        [leads[0], "2026-12-02T16:20:00-06:00", "al", "decision_maker", "yes", "no", "no", "booked Tue"],
        [leads[1], "2026-12-01T09:30:00-06:00", "al", "do_not_call", "", "", "", "asked to stop"],
    ])  # fmt: skip
    counts = import_csv(fresh_db, "t1", good, "dialer-x", 3)
    assert counts["inserted"] == 3
    assert counts["suppression_rows_added"] == 2  # business + phone
    assert import_csv(fresh_db, "t1", good, "dialer-x", 3)["already_imported"] == 3

    # The rewritten call list drops the opted-out lead.
    write_call_list(fresh_db, CFG, "t1", tmp_path / "exp")
    with (tmp_path / "exp" / "t1" / "call_list.csv").open() as fh:
        refs = [r["lead_ref"] for r in csv.DictReader(fh)]
    assert leads[1] not in refs
    assert len(refs) == 7

    for bad, msg in (
        ([leads[2], "2026-12-01T09:15:00", "al", "voicemail", "no", "no", "no", ""], "time zone"),
        ([leads[2], "2026-12-01T09:15:00Z", "al", "maybe", "no", "no", "no", ""], "disposition"),
        (["00000000-0000-0000-0000-000000000000", "2026-12-01T09:15:00Z", "al", "voicemail", "", "", "", ""],
         "not in this experiment"),
        ([leads[2], "2026-12-01T09:15:00Z", "al", "voicemail", "sure", "no", "no", ""], "yes/no"),
    ):  # fmt: skip
        f = _write_outcomes(
            tmp_path / "bad.csv",
            [[leads[3], "2026-12-03T10:00:00Z", "al", "voicemail", "", "", "", ""], bad],
        )
        with pytest.raises(ValueError, match=msg):
            import_csv(fresh_db, "t1", f, "dialer-x", 3)
    with psycopg.connect(fresh_db) as c:
        assert c.execute("select count(*) from outcomes").fetchone() == (
            3,
        )  # bad files wrote nothing


@pytest.mark.db
@needs_db
def test_readout_end_to_end_with_simulated_outcomes(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    create(fresh_db, CFG, "t1", "seed-1", tmp_path / "exp", per_arm=4)
    with psycopg.connect(fresh_db) as c:
        arms = c.execute("select business_id::text, arm from experiment_assignments").fetchall()
    rows = []
    for i, (bid, arm) in enumerate(arms):
        demo = "yes" if arm == "scored" else "no"
        disp = "decision_maker" if arm == "scored" else "voicemail"
        rows.append([bid, f"2026-12-01T10:{i:02d}:00-06:00", "al", disp, demo, "no", "no", ""])
    import_csv(fresh_db, "t1", _write_outcomes(tmp_path / "o.csv", rows), "sim", 3)

    path, res = readout(fresh_db, CFG, "t1", tmp_path / "reports")
    assert res["verdict"] == "not_enough_data"  # 4 per arm < 300
    assert res["scored"]["demo"] == 4
    assert res["control"]["demo"] == 0
    text = path.read_text()
    assert "Not enough data yet" in text
    assert "| **Demo booked** | 100.0% (4/4;" in text

    small = CFG.model_copy(
        update={"experiment": CFG.experiment.model_copy(update={"min_dialed_per_arm": 4})}
    )
    _, res2 = readout(fresh_db, small, "t1", tmp_path / "reports")
    assert res2["verdict"] == "scored_wins"  # 4/4 vs 0/4: p = 0.005


def test_call_list_has_no_column_that_differs_by_arm() -> None:
    # Fields that are systematically present for scored leads only would unblind callers.
    assert not {"website", "email", "tier", "score", "arm", "opener", "why_reasons"} & set(
        CALL_LIST_FIELDS
    )
