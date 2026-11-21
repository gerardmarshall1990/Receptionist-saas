"""The compliance gate: one pure function, one record in, pass/blocked + every reason out.

Nothing here touches the database, so every rule is unit-tested with plain records.
A record is exported only if `evaluate(...).status == "pass"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from leads_engine.config_loader import PHONE_RANK, TIER_RANK, ComplianceConfig


@dataclass(frozen=True)
class GateInput:
    business_id: str
    country: str
    tier: str
    dead_reason: str | None
    phone_e164: str | None
    phone_status: str
    phone_flags: tuple[str, ...]
    line_type: str | None
    business_flags: tuple[str, ...]
    source: str
    licenses: tuple[str, ...]
    n_reasons: int
    domain: str | None = None
    email: str | None = None
    email_origin: str | None = None
    dnc_checked: bool = False
    # Suppression values as stored: {"business_id": {...}, "phone_e164": {...}, ...}
    suppression: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    status: str  # "pass" | "blocked"
    blocks: tuple[str, ...]
    warnings: tuple[str, ...]


def suppression_hits(inp: GateInput) -> list[str]:
    checks = (
        ("business_id", inp.business_id),
        ("phone_e164", inp.phone_e164),
        ("email", inp.email.lower() if inp.email else None),
        ("domain", inp.domain),
    )
    return [
        f"suppressed:{kind}"
        for kind, value in checks
        if value and value in inp.suppression.get(kind, set())
    ]


def evaluate(
    inp: GateInput, cfg: ComplianceConfig, profile: Literal["export", "control"] = "export"
) -> GateResult:
    """`export`: the Stage 5 gate. `control`: the Stage 6 random-list arm, which ignores
    everything the score knows (tier, reasons, disqualifiers, crawl-derived flags) but keeps
    every safety rule."""
    blocks: list[str] = []
    warnings: list[str] = []
    ex = cfg.export
    control = profile == "control"

    blocks += suppression_hits(inp)

    rules = cfg.countries.get(inp.country)  # type: ignore[call-overload]
    if rules is None or not rules.enabled:
        blocks.append(f"country_not_enabled:{inp.country}")

    # Score: dead, below the export tier, or nothing on record to say why.
    if not control:
        if inp.dead_reason:
            blocks.append(f"dead:{inp.dead_reason}")
        if TIER_RANK.get(inp.tier, 0) < TIER_RANK[ex.min_tier]:
            blocks.append(f"tier_below_{ex.min_tier}")
        if inp.n_reasons < ex.min_reasons:
            blocks.append("no_evidence_reasons")

    # Phone: present, clean, and at least as verified as the country requires.
    if not inp.phone_e164:
        blocks.append("no_phone")
    bad_flags = sorted(
        f for f in inp.phone_flags if f in ex.blocked_phone_flags or f.startswith("foreign:")
    )
    blocks += [f"phone_flag:{f}" for f in bad_flags]
    min_phone = (
        cfg.control.min_phone_status if control else (rules.min_phone_status if rules else None)
    )
    if min_phone and PHONE_RANK.get(inp.phone_status, 0) < PHONE_RANK[min_phone]:
        blocks.append(f"phone_status_below_{min_phone}")
    flag_rules = cfg.control.blocked_business_flags if control else ex.blocked_business_flags
    blocks += [f"business_flag:{f}" for f in sorted(inp.business_flags) if f in flag_rules]

    # Calling mode / line type / DNC.
    if cfg.calling_mode == "ai_voice":
        allowed = set(rules.ai_voice_line_types) if rules else set()
        if inp.line_type not in allowed:
            blocks.append("line_type_not_cleared_for_ai_voice")
    elif inp.line_type in (None, "unknown"):
        warnings.append("line_type_unknown")
    if not inp.dnc_checked:
        if rules is not None and rules.require_dnc_check:
            blocks.append("dnc_unchecked")
        else:
            warnings.append("dnc_unchecked")

    # Email: optional, but if exported it must be published on their own site.
    if inp.email and inp.email_origin != "business_site":
        blocks.append("email_not_from_own_site")

    # Source and license.
    if inp.source not in ex.allowed_sources:
        blocks.append(f"source_not_allowed:{inp.source}")
    if not inp.licenses:
        blocks.append("no_license")
    else:
        blocks += [f"license_not_allowed:{x}" for x in inp.licenses if x not in ex.allowed_licenses]

    return GateResult("blocked" if blocks else "pass", tuple(blocks), tuple(warnings))
