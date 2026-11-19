from __future__ import annotations

import string
from pathlib import Path
from typing import Any

import pytest

from leads_engine.config_loader import OPENER_PLACEHOLDERS, load_config, resolve_model
from leads_engine.signals import SIGNALS
from tests.conftest import REAL_CONFIG, Mutator

SPEC_CATEGORIES = {
    "plumbing": 1,
    "hvac": 2,
    "electrical": 3,
    "roofing_restoration": 4,
    "dental": 5,
    "law_small": 6,
    "auto_repair": 7,
}


# ---------------------------------------------------------------- real config


def test_real_config_loads_with_the_seven_spec_categories_in_spec_order() -> None:
    cfg = load_config(REAL_CONFIG)
    assert {slug: c.rank for slug, c in cfg.categories.items()} == SPEC_CATEGORIES


def test_pilot_order_matches_spec() -> None:
    cfg = load_config(REAL_CONFIG)
    assert [c.slug for c in cfg.pilot_categories("a")] == ["plumbing", "hvac"]
    assert [c.slug for c in cfg.pilot_categories("b")] == ["dental"]


def test_every_category_has_the_required_stage0_fields() -> None:
    cfg = load_config(REAL_CONFIG)
    for cat in cfg.categories.values():
        assert 0 < cat.value_weight <= 1, cat.slug
        assert len(cat.pain_keywords) >= 5, cat.slug
        assert cat.review_pain_keywords, cat.slug
        assert cat.exclusions.chain_name_patterns, cat.slug
        assert cat.best_call_windows, cat.slug
        assert cat.opener_templates, cat.slug
        assert cat.staff_target.max <= 20, f"{cat.slug}: spec fit is staff 1-20"
        assert cat.open_questions, f"{cat.slug}: unknowns must be listed"


def test_every_opener_is_one_sentence_and_tied_to_an_eligible_signal() -> None:
    cfg = load_config(REAL_CONFIG)
    for cat in cfg.categories.values():
        for op in cat.opener_templates:
            sig = SIGNALS[op.requires_signal]
            assert sig.opener_eligible
            assert not sig.disqualifier
            fields = {f for _, f, _, _ in string.Formatter().parse(op.template) if f}
            assert fields <= OPENER_PLACEHOLDERS
            body = op.template.rstrip(".?!")
            assert not any(p in body for p in (". ", "? ", "! ")), f"{op.id}: one sentence"


def test_watch_list_and_global_rules() -> None:
    cfg = load_config(REAL_CONFIG)
    assert cfg.global_.countries_enabled == ["US"]
    assert "veterinary" in cfg.global_.watch_list
    assert "ai receptionist" in cfg.global_.global_exclusion_keywords


def test_spec_scoring_formula_and_tiers() -> None:
    sc = load_config(REAL_CONFIG).scoring
    assert (sc.weights.pain, sc.weights.value, sc.weights.reachability, sc.weights.fit) == (
        0.35,
        0.25,
        0.25,
        0.15,
    )
    assert sc.total(100, 100, 100, 100) == pytest.approx(100)
    assert sc.total(80, 60, 40, 20) == pytest.approx(0.35 * 80 + 0.25 * 60 + 0.25 * 40 + 0.15 * 20)
    assert [sc.tier_for(x) for x in (80, 79.99, 60, 59.99, 40, 39.99, 0)] == list("ABBCCDD")


def test_zero_reachability_or_fit_kills_the_lead_regardless_of_pain() -> None:
    sc = load_config(REAL_CONFIG).scoring
    assert sc.total(pain=100, value=100, reachability=0, fit=100) == 0
    assert sc.total(pain=100, value=100, reachability=100, fit=0) == 0
    assert sc.total(pain=0, value=100, reachability=100, fit=100) > 0


def test_config_hash_is_stable_and_changes_with_content(config_copy: Path, mutate: Mutator) -> None:
    a = load_config(config_copy).config_hash
    assert load_config(config_copy).config_hash == a
    mutate("categories/plumbing.yaml", lambda d: d.update(value_weight=0.81))
    assert load_config(config_copy).config_hash != a


def test_model_names_come_from_config_and_env_override_wins() -> None:
    cfg = load_config(REAL_CONFIG)
    assert resolve_model(cfg, "scoring", None) == cfg.models.scoring.model
    assert resolve_model(cfg, "opener", "some-other-model") == "some-other-model"


def test_cost_projection_math() -> None:
    m = load_config(REAL_CONFIG).models.scoring
    expected = (
        m.est_input_tokens * m.input_usd_per_mtok + m.est_output_tokens * m.output_usd_per_mtok
    ) / 1e6
    assert m.est_cost_per_call_usd == pytest.approx(expected)
    assert m.projected_cost_usd(1000) == pytest.approx(1000 * expected)


# ------------------------------------------------------ invalid config fails


def _set(key: str, value: Any) -> Any:
    return lambda d: d.__setitem__(key, value)


def _window(start: str, end: str) -> list[dict[str, Any]]:
    return [{"days": ["mon"], "start": start, "end": end, "why": "x"}]


def _opener(signal: str, template: str = "Hello {name}, quick question.") -> list[dict[str, str]]:
    return [{"id": "x", "requires_signal": signal, "template": template}]


PLUMB = "categories/plumbing.yaml"

# (test id, file, edit, regex the error must match -> proves it failed for the right reason)
BAD_EDITS: list[tuple[str, str, Any, str]] = [
    ("unknown key", PLUMB, _set("surprise", 1), "Extra inputs are not permitted"),
    ("value weight > 1", PLUMB, _set("value_weight", 1.5), "less than or equal to 1"),
    ("empty pain keywords", PLUMB, _set("pain_keywords", []), "must not be empty"),
    ("duplicate keyword", PLUMB, _set("pain_keywords", ["24/7", "24/7"]), "duplicate keywords"),
    ("slug != filename", PLUMB, _set("slug", "plumbers"), "must match file name"),
    ("rank gap", "categories/auto_repair.yaml", _set("rank", 9), "ranks must be 1..N"),
    (
        "staff order",
        PLUMB,
        _set("staff_target", {"min": 5, "typical": 2, "max": 20, "hard_max": 50}),
        "min <= typical",
    ),
    (
        "call window before 8am",
        PLUMB,
        _set("best_call_windows", _window("07:00", "09:00")),
        "outside the hard bounds",
    ),
    (
        "call window reversed",
        PLUMB,
        _set("best_call_windows", _window("10:00", "09:00")),
        "must be before end",
    ),
    (
        "opener on unknown signal",
        PLUMB,
        _set("opener_templates", _opener("made_up")),
        "unknown signal",
    ),
    (
        "opener on a disqualifier",
        PLUMB,
        _set("opener_templates", _opener("uses_answering_service")),
        "cannot be the basis of an opener",
    ),
    (
        "opener with an unsupported placeholder",
        PLUMB,
        _set(
            "opener_templates", _opener("leave_message_text", "Hi {owner_first_name}, quick one.")
        ),
        "placeholders",
    ),
    ("bad OSM tag", PLUMB, lambda d: d["source_codes"].update(osm=["plumber"]), "key=value"),
    (
        "weights don't sum to 1",
        "scoring.yaml",
        lambda d: d["weights"].update(pain=0.5),
        "sum to 1.0",
    ),
    ("tiers not descending", "scoring.yaml", lambda d: d["tiers"].update(B=85), "A > B > C"),
    (
        "unknown signal weight",
        "scoring.yaml",
        lambda d: d["signal_weights"].update(made_up=1),
        "unknown signals",
    ),
    (
        "disqualifier weighted",
        "scoring.yaml",
        lambda d: d["signal_weights"].update(is_chain=1),
        "gates, not weights",
    ),
    ("funnel widens", "scoring.yaml", _set("opener_top_fraction", 0.04), "funnel must narrow"),
    (
        "sampling param (rejected by API)",
        "models.yaml",
        lambda d: d["scoring"].update(temperature=0.2),
        "Extra inputs are not permitted",
    ),
    ("bad effort", "models.yaml", lambda d: d["opener"].update(effort="extreme"), "effort"),
    (
        "category on watch list",
        "global.yaml",
        lambda d: d["watch_list"].append("dental"),
        "watch list",
    ),
    (
        "unsupported country",
        "global.yaml",
        _set("countries_enabled", ["US", "FR"]),
        "countries_enabled",
    ),
]


@pytest.mark.parametrize(("what", "rel", "edit", "match"), BAD_EDITS, ids=[b[0] for b in BAD_EDITS])
def test_invalid_config_fails_loudly(
    what: str, rel: str, edit: Any, match: str, config_copy: Path, mutate: Mutator
) -> None:
    mutate(rel, edit)
    with pytest.raises(ValueError, match=match):  # pydantic ValidationError is a ValueError
        load_config(config_copy)


def test_missing_file_fails(config_copy: Path) -> None:
    (config_copy / "models.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        load_config(config_copy)
