"""Stage 5: compliance gate (pure), openers, export contract, and the export against Postgres."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pyarrow.parquet as pq
import pytest
from psycopg import sql

from leads_engine import db
from leads_engine.compliance.export import (
    CONTRACT_FIELDS,
    SCHEMA,
    best_call_window,
    check_all,
    run_export,
)
from leads_engine.compliance.gate import GateInput, evaluate
from leads_engine.compliance.openers import display_name, pick_opener
from leads_engine.compliance_commands import add_suppressions, normalize_match
from leads_engine.config_loader import AppConfig, load_config
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import REAL_CONFIG, Mutator, needs_db
from tests.test_build import raw

CFG = load_config(REAL_CONFIG)
GOOD = GateInput(
    business_id="b1",
    country="US",
    tier="A",
    dead_reason=None,
    phone_e164="+15125550123",
    phone_status="likely",
    phone_flags=(),
    line_type=None,
    business_flags=("multiple_locations",),
    source="overture",
    licenses=("CDLA-Permissive-2.0",),
    n_reasons=3,
    domain="joesplumbing.com",
    email="office@joesplumbing.com",
    email_origin="business_site",
)


def ai_voice(cfg: AppConfig) -> AppConfig:
    return cfg.model_copy(
        update={"compliance": cfg.compliance.model_copy(update={"calling_mode": "ai_voice"})}
    )


# ------------------------------------------------------------------ gate


def test_clean_record_passes_with_honest_warnings() -> None:
    r = evaluate(GOOD, CFG.compliance)
    assert r.status == "pass"
    assert r.blocks == ()
    assert set(r.warnings) == {"line_type_unknown", "dnc_unchecked"}


@pytest.mark.parametrize(
    ("change", "block"),
    [
        ({"suppression": {"business_id": {"b1"}}}, "suppressed:business_id"),
        ({"suppression": {"phone_e164": {"+15125550123"}}}, "suppressed:phone_e164"),
        ({"suppression": {"email": {"office@joesplumbing.com"}}}, "suppressed:email"),
        ({"suppression": {"domain": {"joesplumbing.com"}}}, "suppressed:domain"),
        ({"country": "CA"}, "country_not_enabled:CA"),
        (
            {"dead_reason": "disqualified: uses_answering_service"},
            "dead:disqualified: uses_answering_service",
        ),
        ({"tier": "C"}, "tier_below_B"),
        ({"n_reasons": 0}, "no_evidence_reasons"),
        ({"phone_e164": None, "phone_status": "none"}, "no_phone"),
        ({"phone_status": "unverified"}, "phone_status_below_likely"),
        ({"phone_flags": ("toll_free",)}, "phone_flag:toll_free"),
        ({"phone_flags": ("foreign:MX",)}, "phone_flag:foreign:MX"),
        ({"business_flags": ("shared_phone",)}, "business_flag:shared_phone"),
        ({"business_flags": ("phone_not_on_site",)}, "business_flag:phone_not_on_site"),
        ({"email_origin": "source_listed"}, "email_not_from_own_site"),
        ({"source": "scraped_maps"}, "source_not_allowed:scraped_maps"),
        ({"licenses": ()}, "no_license"),
        ({"licenses": ("proprietary",)}, "license_not_allowed:proprietary"),
    ],
)
def test_every_violation_is_blocked(change: dict[str, Any], block: str) -> None:
    r = evaluate(replace(GOOD, **change), CFG.compliance)
    assert r.status == "blocked"
    assert block in r.blocks


def test_suppression_email_match_ignores_case() -> None:
    inp = replace(
        GOOD, email="Office@JoesPlumbing.com", suppression={"email": {"office@joesplumbing.com"}}
    )
    assert "suppressed:email" in evaluate(inp, CFG.compliance).blocks


def test_ai_voice_mode_blocks_unknown_and_mobile_lines() -> None:
    cfg = ai_voice(CFG).compliance
    assert "line_type_not_cleared_for_ai_voice" in evaluate(GOOD, cfg).blocks
    assert (
        "line_type_not_cleared_for_ai_voice"
        in evaluate(replace(GOOD, line_type="mobile"), cfg).blocks
    )
    assert evaluate(replace(GOOD, line_type="landline"), cfg).status == "pass"


def test_dnc_requirement_blocks_until_checked(config_copy: Path, mutate: Mutator) -> None:
    mutate("compliance.yaml", lambda d: d["countries"]["US"].update(require_dnc_check=True))
    cfg = load_config(config_copy).compliance
    assert "dnc_unchecked" in evaluate(GOOD, cfg).blocks
    assert evaluate(replace(GOOD, dnc_checked=True), cfg).status == "pass"


def test_compliance_config_refuses_countries_not_enabled_globally(
    config_copy: Path, mutate: Mutator
) -> None:
    mutate("compliance.yaml", lambda d: d["countries"]["GB"].update(enabled=True))
    with pytest.raises(ValueError, match="compliance enables countries"):
        load_config(config_copy)


# ------------------------------------------------------------------ openers


def test_display_name_trims_legal_suffix_and_shouting() -> None:
    assert display_name("ACME PLUMBING, LLC") == "Acme Plumbing"
    assert (
        display_name("Coziahr Heating & Air Conditioning, Inc.")
        == "Coziahr Heating & Air Conditioning"
    )
    assert display_name("Inc.") == "Inc."  # never empties a name


def test_opener_uses_strongest_stored_signal() -> None:
    cat = CFG.categories["plumbing"]
    w = CFG.scoring.signal_weights
    text, tid = pick_opener(
        cat, ["emergency_247_claim", "leave_message_text"], w, "JOE'S PLUMBING LLC", "Austin"
    ) or ("", "")
    assert tid == "leave_a_message"  # weight 10 beats 2
    text, tid = pick_opener(cat, ["emergency_247_claim"], w, "Joe's Plumbing LLC", None) or ("", "")
    assert text.startswith("I saw Joe's Plumbing offers emergency service")


def test_no_genuine_reason_means_no_opener() -> None:
    w = CFG.scoring.signal_weights
    assert pick_opener(CFG.categories["plumbing"], ["no_chat_widget"], w, "Joe", None) is None
    # dental's limited-hours template needs {evidence}: skipped, never paraphrased.
    assert (
        pick_opener(CFG.categories["dental"], ["limited_hours_text"], w, "Smile Co", None) is None
    )
    assert pick_opener(None, ["leave_message_text"], w, "Joe", None) is None


def test_best_call_window_text() -> None:
    assert (
        best_call_window(CFG.categories["plumbing"], "America/Chicago")
        == "Mon-Fri 08:00-09:00, 16:00-18:00 (America/Chicago)"
    )
    assert best_call_window(CFG.categories["plumbing"], None) is None


def test_contract_matches_spec_section_10() -> None:
    assert CONTRACT_FIELDS == (
        "business_id", "name", "category", "country", "state", "city", "tz", "phone_e164",
        "phone_status", "line_type", "email", "email_status", "website", "owner_name", "score",
        "tier", "why_reasons", "opener", "best_call_window", "source", "license", "crawled_at",
        "verified_at", "compliance_status",
    )  # fmt: skip
    assert SCHEMA.names == list(CONTRACT_FIELDS)


def test_suppression_values_are_normalised() -> None:
    assert normalize_match("phone_e164", "(512) 555-0123") == "+15125550123"
    assert normalize_match("email", " Office@Joes.com ") == "office@joes.com"
    assert normalize_match("domain", "https://www.Joes.com/contact") == "joes.com"
    with pytest.raises(ValueError, match="not a valid phone"):
        normalize_match("phone_e164", "not a phone")


# ------------------------------------------------------------------ export (Postgres)


def _seed(url: str, tmp: Path) -> dict[str, str]:
    db.migrate(url)
    rows = [
        raw("p1", "Alpha Plumbing LLC", websites=["alphaplumbing.com"], region="US-TX", city="Austin",
            postcode="78701", phones=["+15124780101"]),
        raw("p2", "Bravo Plumbing", websites=["bravoplumbing.com"], region="US-TX", city="Dallas",
            postcode="75201", lat=32.78, lng=-96.8, phones=["+12147480102"]),
        raw("p3", "Charlie Plumbing", websites=["charlieplumbing.com"], region="US-FL", city="Tampa",
            postcode="33601", lat=27.95, lng=-82.46, phones=["+18132230103"]),
        raw("p4", "Delta Plumbing", websites=["deltaplumbing.com"], region="US-FL", city="Miami",
            postcode="33101", lat=25.76, lng=-80.19, phones=["+13053740104"]),
    ]  # fmt: skip
    out = tmp / "built.parquet"
    build(tmp / "x", CFG, ["plumbing"], "2026-11-18.0", out, "seed", rows_in=rows)
    load(url, out, "all")
    with psycopg.connect(url, autocommit=True) as c:
        ids = {r[0]: str(r[1]) for r in c.execute("select name, id from businesses")}
        # p1, p2, p4: phone confirmed on their site; p3: listed only.
        c.execute(
            "update phones set status = 'likely' where business_id <> %s",
            (ids["Charlie Plumbing"],),
        )
        c.execute(
            "update businesses b set phone_status = p.status from phones p where p.business_id = b.id"
        )
        for name, tier, total in (("Alpha Plumbing LLC", "A", 80), ("Bravo Plumbing", "B", 65),
                                  ("Charlie Plumbing", "A", 75), ("Delta Plumbing", "C", 50)):  # fmt: skip
            c.execute(
                "insert into scores (business_id, pain, value, reachability, fit, total, tier, components,"
                " model_version) values (%s, 50, 50, 50, 50, %s, %s, '[]', 'test')",
                (ids[name], total, tier),
            )
            c.execute(
                "insert into reasons (business_id, signal, dimension, evidence, source_url, weight, stage)"
                " values (%s, 'leave_message_text', 'pain', 'Please leave a message after the tone',"
                " %s, 10, 'stage2')",
                (ids[name], "https://example.test/contact"),
            )
        c.execute(
            "insert into emails (business_id, email, origin, source_url, syntax_ok, is_role_based,"
            " is_freemail, domain_matches_site) values (%s, 'office@alphaplumbing.com', 'business_site',"
            " 'https://alphaplumbing.com/contact', true, true, false, true)",
            (ids["Alpha Plumbing LLC"],),
        )
    return ids


def _export(url: str, tmp: Path, **kw: Any) -> Any:
    return run_export(
        url, CFG, where=sql.SQL("true"), params=[], origin="cli", out_root=tmp / "exports", **kw
    )


@pytest.mark.db
@needs_db
def test_export_writes_only_passing_records(fresh_db: str, tmp_path: Path) -> None:
    ids = _seed(fresh_db, tmp_path)
    res = _export(fresh_db, tmp_path)
    # Delta is tier C (not a candidate); Charlie's phone isn't on its site.
    assert res.counts["candidates"] == 3
    assert res.counts["pass"] == 2
    assert res.blocked_by == {"phone_status_below_likely": 1}
    names = {r["name"] for r in res.records}
    assert names == {"Alpha Plumbing LLC", "Bravo Plumbing"}

    alpha = next(r for r in res.records if r["name"] == "Alpha Plumbing LLC")
    assert alpha["email"] == "office@alphaplumbing.com"
    assert alpha["why_reasons"][0]["evidence"] == "Please leave a message after the tone"
    assert alpha["opener"].startswith("Your site asks callers to leave a message")
    assert alpha["line_type"] == "unknown"
    assert alpha["compliance_status"] == "pass"
    assert alpha["owner_name"] is None

    files = {p.name for p in res.path.iterdir()}
    assert files == {"leads.parquet", "leads.csv", "manifest.json"}
    table = pq.read_table(res.path / "leads.parquet")
    assert table.schema.names == list(CONTRACT_FIELDS)
    assert set(table.column("business_id").to_pylist()) == {
        ids["Alpha Plumbing LLC"],
        ids["Bravo Plumbing"],
    }
    with (res.path / "leads.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert [*rows[0]] == list(CONTRACT_FIELDS)
    assert json.loads(rows[0]["why_reasons"])[0]["signal"] == "leave_message_text"
    manifest = json.loads((res.path / "manifest.json").read_text())
    assert manifest["counts"]["pass"] == 2
    assert manifest["attribution"]

    with psycopg.connect(fresh_db) as c:
        items: dict[str, str] = dict(
            c.execute("select business_id::text, status from export_items").fetchall()
        )
        assert items[ids["Charlie Plumbing"]] == "blocked"
        assert c.execute("select n_passed from exports").fetchone() == (2,)
        # Audit rows hold exactly what was exported.
        row = c.execute(
            "select row from export_items where business_id = %s", (ids["Alpha Plumbing LLC"],)
        ).fetchone()
        assert row is not None
        assert row[0]["phone_e164"] == alpha["phone_e164"]


@pytest.mark.db
@needs_db
def test_suppression_always_wins(fresh_db: str, tmp_path: Path) -> None:
    ids = _seed(fresh_db, tmp_path)
    check_all(fresh_db, CFG)
    with psycopg.connect(fresh_db) as c:
        assert c.execute(
            "select compliance_status from businesses where id = %s", (ids["Bravo Plumbing"],)
        ).fetchone() == ("pass",)
    # Suppressed after scoring and after the stored check: blocked in browsing and export.
    add_suppressions(fresh_db, [("phone_e164", "(214) 748-0102", "asked not to be called")], "test")
    add_suppressions(fresh_db, [("domain", "www.alphaplumbing.com", "opt-out")], "test")
    with psycopg.connect(fresh_db) as c:
        assert c.execute(
            "select compliance_status from businesses where id = %s", (ids["Bravo Plumbing"],)
        ).fetchone() == ("blocked",)
    res = _export(fresh_db, tmp_path)
    assert res.counts.get("pass", 0) == 0
    assert res.blocked_by["suppressed:phone_e164"] == 1
    assert res.blocked_by["suppressed:domain"] == 1
    assert pq.read_table(res.path / "leads.parquet").num_rows == 0


@pytest.mark.db
@needs_db
def test_export_is_repeatable_and_dry_run_writes_nothing(fresh_db: str, tmp_path: Path) -> None:
    _seed(fresh_db, tmp_path)
    dry = _export(fresh_db, tmp_path, dry_run=True)
    assert dry.path is None
    with psycopg.connect(fresh_db) as c:
        assert c.execute("select count(*) from exports").fetchone() == (0,)
    a = _export(fresh_db, tmp_path)
    b = _export(fresh_db, tmp_path)
    assert a.export_id != b.export_id
    strip = [{k: v for k, v in r.items()} for r in a.records]
    assert strip == b.records == dry.records


def test_no_opener_when_listing_name_is_not_on_the_site() -> None:
    from leads_engine.compliance.export import contract_record

    row: dict[str, Any] = {
        "id": "b1", "name": "Dennis D. Camp P.A.", "category": "law_small", "country": "US", "state": "FL",
        "city": "Ocala", "tz": "America/New_York", "e164": "+13523511234", "phone_status": "likely",
        "line_type": None, "email": None, "email_status": "none", "website": "https://x.test",
        "total": 72, "tier": "A", "licenses": ["CDLA-Permissive-2.0"], "source": "overture",
        "crawled_at": None, "verified_at": None, "flags": [],
        "reasons": [{"signal": "leave_message_text", "evidence": "after hours voicemail", "source_url": "https://x.test"}],
    }  # fmt: skip
    assert contract_record(row, CFG)["opener"]
    row["flags"] = ["name_not_on_site"]
    assert contract_record(row, CFG)["opener"] is None


@pytest.mark.db
@needs_db
def test_prune_keeps_headers_manifests_and_newest(fresh_db: str, tmp_path: Path) -> None:
    from leads_engine.compliance.export import prune_exports

    _seed(fresh_db, tmp_path)
    runs = [_export(fresh_db, tmp_path) for _ in range(3)]
    assert prune_exports(fresh_db, keep=1)["exports"] == 2
    with psycopg.connect(fresh_db) as c:
        left = c.execute("select export_id, count(*) from export_items group by 1").fetchall()
        assert [str(r[0]) for r in left] == [runs[-1].export_id]
        assert c.execute("select count(*) from exports where pruned_at is not null").fetchone() == (
            2,
        )
        assert c.execute("select count(*) from exports").fetchone() == (3,)
    for r in runs[:2]:
        assert (r.path / "manifest.json").exists()
        assert not (r.path / "leads.parquet").exists()
    assert (runs[-1].path / "leads.parquet").exists()
    assert prune_exports(fresh_db, keep=1)["exports"] == 0  # idempotent
