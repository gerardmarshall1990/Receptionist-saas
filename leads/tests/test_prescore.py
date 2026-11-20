"""Rule-based scoring: dimension math, gates, tiers, explainability."""

from __future__ import annotations

from typing import Any

import pytest

from leads_engine.config_loader import load_config
from leads_engine.scoring.prescore import score
from tests.conftest import REAL_CONFIG, Mutator

CFG = load_config(REAL_CONFIG)


def biz(**kw: Any) -> dict[str, Any]:
    b: dict[str, Any] = {"category": "plumbing", "flags": [], "is_chain": False,
                         "phone_status": "likely", "email_status": "site_found"}  # fmt: skip
    b.update(kw)
    return b


def test_strong_evidence_lead_lands_in_a() -> None:
    s = score(
        biz(), {"website_works", "owner_operated", "leave_message_text", "phone_only_booking"}, CFG
    )
    assert s.pain == 100  # 10 + 6 >= pain_full_at
    assert s.value == pytest.approx(0.80 * 80)
    assert s.reachability == 85  # likely 70 + site email 15
    assert s.fit == 80  # site works 55 + owner 25
    assert s.total == pytest.approx(0.35 * 100 + 0.25 * 64 + 0.25 * 85 + 0.15 * 80)
    assert s.tier == "A"
    assert s.dead_reason is None


def test_weak_signals_alone_dont_make_a_top_lead() -> None:
    weak = {"website_works", "emergency_247_claim", "no_online_booking", "no_chat_widget"}
    s = score(biz(email_status="none", phone_status="unverified"), weak, CFG)
    assert s.pain == pytest.approx(100 * 4 / 12, abs=0.01)
    assert s.tier in {"C", "D"}


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"phone_status": "none"}, "no reachable phone"),
        ({"phone_status": "invalid"}, "no reachable phone"),
        ({"flags": ["uses_answering_service"]}, "disqualified: uses_answering_service"),
        ({"flags": ["franchise_disclaimer"]}, "disqualified: franchise_disclaimer"),
        ({"flags": ["website_parked"]}, "disqualified: website_parked"),
        ({"is_chain": True}, "disqualified: is_chain"),
    ],
)
def test_dead_leads_score_zero_whatever_the_pain(kw: dict[str, Any], reason: str) -> None:
    s = score(biz(**kw), {"website_works", "leave_message_text", "hiring_receptionist"}, CFG)
    assert s.total == 0
    assert s.tier == "D"
    assert s.dead_reason == reason


def test_penalties_and_bonuses_are_itemised() -> None:
    flags = ["phone_not_on_site", "promises_live_answer", "multiple_locations"]
    s = score(biz(flags=flags), {"website_works", "pricing_hints"}, CFG)
    items = {(c["dimension"], c["item"]): c["points"] for c in s.components}
    assert items[("reachability", "phone_not_on_site")] == -15
    assert items[("fit", "promises_live_answer")] == -30
    assert items[("value", "pricing_hints")] == 10
    assert items[("value", "multiple_locations")] == 15
    assert s.reachability == 70  # 70 + 15 - 15
    assert s.fit == 25  # 55 - 30


def test_uncrawled_business_falls_back_to_no_site_base() -> None:
    s = score(biz(email_status="source_listed", phone_status="unverified"), set(), CFG)
    assert s.pain == 0
    assert s.fit == 35
    assert s.reachability == 50
    assert s.tier == "D"


def test_blocked_site_is_unknown_not_missing() -> None:
    s = score(biz(flags=["crawl_blocked"]), set(), CFG)
    assert s.fit == 45


def test_every_point_is_explained() -> None:
    s = score(
        biz(flags=["generic_name"]), {"website_works", "owner_operated", "limited_hours_text"}, CFG
    )
    by_dim: dict[str, float] = {}
    for c in s.components:
        by_dim[c["dimension"]] = by_dim.get(c["dimension"], 0) + c["points"]
    assert by_dim["fit"] == s.fit
    assert by_dim["reachability"] == s.reachability
    assert by_dim["value"] == s.value
    assert 100 * by_dim["pain"] / CFG.scoring.prescore.pain_full_at == pytest.approx(s.pain)


def test_tier_cutoffs_from_config() -> None:
    assert [CFG.scoring.tier_for(x) for x in (70, 69.99, 60, 40, 39.99)] == list("ABBCD")


def test_prescore_config_must_be_complete(config_copy: Any, mutate: Mutator) -> None:
    mutate("scoring.yaml", lambda d: d["prescore"]["phone_points"].pop("likely"))
    with pytest.raises(ValueError, match="phone_points"):
        load_config(config_copy)
