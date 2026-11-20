"""Rule-based pre-score: Pain x Value x Reachability x Fit (spec section 5).

Pure: a business row + its true signals + config -> dimension scores, total, tier
and an itemised list of every point added or removed (stored as `components`), so a
score is always explainable. All numbers come from config/scoring.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from leads_engine.config_loader import AppConfig
from leads_engine.signals import SIGNALS, Dimension

UNKNOWN_SITE_FLAGS = {"crawl_blocked", "robots_disallowed", "website_offsite", "crawl_failed"}


@dataclass
class Score:
    pain: float
    value: float
    reachability: float
    fit: float
    total: float
    tier: str
    dead_reason: str | None
    components: list[dict[str, Any]] = field(default_factory=list)


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, round(x, 2)))


def score(b: dict[str, Any], true_signals: set[str], cfg: AppConfig) -> Score:
    sc, ps = cfg.scoring, cfg.scoring.prescore
    w = sc.signal_weights
    flags = set(b.get("flags") or [])
    comp: list[dict[str, Any]] = []

    def add(dim: str, label: str, pts: float) -> float:
        if pts:
            comp.append({"dimension": dim, "item": label, "points": pts})
        return pts

    # Pain: evidence points, saturating at pain_full_at.
    pain_pts = sum(
        add("pain", s, w.get(s, 0))
        for s in sorted(true_signals)
        if s in SIGNALS and SIGNALS[s].dimension is Dimension.PAIN and not SIGNALS[s].disqualifier
    )
    pain = _clamp(100 * pain_pts / ps.pain_full_at)

    # Value: category weight, plus price/locations evidence.
    cat = cfg.categories.get(b.get("category", ""))
    value = add(
        "value",
        "category value weight",
        round((cat.value_weight if cat else 0) * ps.value_base_scale, 2),
    )
    if "pricing_hints" in true_signals:
        value += add("value", "pricing_hints", w.get("pricing_hints", 0))
    if "multiple_locations" in true_signals or "multiple_locations" in flags:
        value += add("value", "multiple_locations", w.get("multiple_locations", 0))
    value = _clamp(value)

    # Reachability: phone status, email, penalties.
    phone_status = b.get("phone_status") or "none"
    reach = add("reachability", f"phone {phone_status}", ps.phone_points.get(phone_status, 0))
    if reach > 0:
        email_status = b.get("email_status") or "none"
        email_pts: float = ps.email_points.get(email_status, 0.0)
        reach += add("reachability", f"email {email_status}", email_pts)
        for f, pen in ps.reachability_penalties.items():
            if f in flags:
                reach += add("reachability", f, -pen)
    reach = _clamp(reach)

    # Fit: does a working site show an owner-run small business? Disqualifiers kill.
    dq = sorted(set(ps.disqualifying_flags) & flags) or (["is_chain"] if b.get("is_chain") else [])
    if dq:
        fit = 0.0
        comp.append({"dimension": "fit", "item": f"disqualified: {', '.join(dq)}", "points": 0})
    else:
        if "website_works" in true_signals:
            base_key = "site_works"
        elif flags & UNKNOWN_SITE_FLAGS:
            base_key = "site_unknown"
        else:
            base_key = "no_site"
        fit = add("fit", f"website: {base_key}", ps.fit_base[base_key])
        if "owner_operated" in true_signals:
            fit += add("fit", "owner_operated", w.get("owner_operated", 0))
        for f, pen in ps.fit_penalties.items():
            if f in flags:
                fit += add("fit", f, -pen)
        fit = _clamp(fit)

    total = round(sc.total(pain, value, reach, fit), 2)
    dead = None
    if dq:
        dead = f"disqualified: {', '.join(dq)}"
    elif reach <= 0:
        dead = "no reachable phone"
    elif fit <= 0:
        dead = "no fit"
    return Score(pain, value, reach, fit, total, sc.tier_for(total), dead, comp)
