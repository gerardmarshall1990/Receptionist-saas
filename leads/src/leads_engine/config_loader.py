"""Load and validate the versioned YAML config under ``config/``.

Every file is validated by a strict pydantic model: unknown keys, out-of-range
weights, unknown signal names, call windows outside the global hard bounds and
opener templates that are not tied to a real signal all fail loudly. The
combined config gets a stable hash that every run records, so a score can
always be traced back to the exact config that produced it.
"""

from __future__ import annotations

import hashlib
import json
import string
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from leads_engine.signals import SIGNALS

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
Keyword = Annotated[str, StringConstraints(min_length=2, strip_whitespace=True)]
Weight = Annotated[float, Field(ge=0.0, le=1.0)]

OPENER_PLACEHOLDERS = frozenset({"name", "city", "evidence"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _unique_lower(values: list[str], what: str) -> list[str]:
    lowered = [v.lower() for v in values]
    dupes = sorted({v for v in lowered if lowered.count(v) > 1})
    if dupes:
        raise ValueError(f"duplicate {what}: {dupes}")
    return lowered


# ---------------------------------------------------------------- global.yaml


class CallingHoursBounds(Strict):
    earliest: time
    latest: time
    note: str

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.earliest >= self.latest:
            raise ValueError("calling_hours_hard_bounds.earliest must be before latest")
        return self


class ChainDetection(Strict):
    # Overture brand.wikidata set -> known brand -> chain.
    use_brand_wikidata: bool
    # Same website domain at this many distinct locations (ZIPs) -> chain.
    chain_min_locations: Annotated[int, Field(ge=3)]
    # 2 .. chain_min_locations-1 locations -> multiple_locations signal, kept.
    multi_location_min: Annotated[int, Field(ge=2)]
    # A phone shared by more records than this is never used to merge, and is flagged.
    shared_phone_max: Annotated[int, Field(ge=1)]
    # Same normalized name in at least this many locations -> chain if most of those
    # listings share one website (share below), else flagged for review only.
    name_repeat_review_cities: Annotated[int, Field(ge=3)]
    name_network_min_domain_share: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.multi_location_min >= self.chain_min_locations:
            raise ValueError("multi_location_min must be below chain_min_locations")
        return self


class Normalization(Strict):
    # Website on one of these = not the business's own site (treated as no website).
    non_business_site_domains: list[Keyword]
    # Website builders where the business site is a subdomain: domain key = full host.
    hosted_site_domains: list[Keyword]
    # Personal-mail providers: an address here is flagged, never exported without site proof.
    freemail_domains: list[Keyword]
    role_email_prefixes: list[Keyword]

    @field_validator("*")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return _unique_lower(v, "normalization list")


class GlobalConfig(Strict):
    version: int
    countries_enabled: list[Literal["US", "CA", "AU", "GB"]]
    universe_target: Annotated[int, Field(gt=0)]
    # Pinned for reproducibility; bump deliberately and re-run the census.
    overture_release: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}\.\d+$")]
    calling_hours_hard_bounds: CallingHoursBounds
    global_exclusion_keywords: list[Keyword]
    answering_vendor_patterns: list[Keyword]
    chain_detection: ChainDetection
    normalization: Normalization
    pilot_sample: dict[Slug, Annotated[int, Field(gt=0)]]
    # Live Google reviews (Places API Place Details, Enterprise + Atmosphere SKU).
    # Place Details calls per calendar month; 1,000 is Google's free monthly cap.
    google_places_monthly_cap: Annotated[int, Field(ge=0, le=100_000)] = 1000
    watch_list: list[Slug]
    open_questions: list[str] = []

    @field_validator("global_exclusion_keywords", "answering_vendor_patterns")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        return _unique_lower(v, "global keyword list")


# -------------------------------------------------------------- scoring.yaml


class DimensionWeights(Strict):
    pain: Weight
    value: Weight
    reachability: Weight
    fit: Weight

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.pain + self.value + self.reachability + self.fit
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"dimension weights must sum to 1.0, got {total}")
        return self


class TierCutoffs(Strict):
    A: Annotated[float, Field(ge=0, le=100)]
    B: Annotated[float, Field(ge=0, le=100)]
    C: Annotated[float, Field(ge=0, le=100)]

    @model_validator(mode="after")
    def _descending(self) -> Self:
        if not self.A > self.B > self.C:
            raise ValueError("tier cutoffs must satisfy A > B > C")
        return self


class ScoringConfig(Strict):
    version: int
    weights: DimensionWeights
    tiers: TierCutoffs
    # Any of these dimensions scoring zero kills the lead (spec section 5).
    zero_kills: list[Literal["pain", "value", "reachability", "fit"]]
    llm_top_fraction: Annotated[float, Field(gt=0, le=0.25)]
    paid_verify_top_fraction: Annotated[float, Field(gt=0, le=0.10)]
    opener_top_fraction: Annotated[float, Field(gt=0, le=0.05)]
    signal_weights: dict[str, Annotated[float, Field(ge=0)]]

    @field_validator("signal_weights")
    @classmethod
    def _known_signals(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(v) - set(SIGNALS))
        if unknown:
            raise ValueError(f"signal_weights references unknown signals: {unknown}")
        disq = sorted(k for k in v if SIGNALS[k].disqualifier)
        if disq:
            raise ValueError(f"disqualifiers are gates, not weights: {disq}")
        return v

    @model_validator(mode="after")
    def _funnel_narrows(self) -> Self:
        if not self.llm_top_fraction >= self.paid_verify_top_fraction >= self.opener_top_fraction:
            raise ValueError("funnel must narrow: llm >= paid_verify >= opener fractions")
        return self

    def tier_for(self, total: float) -> Literal["A", "B", "C", "D"]:
        if total >= self.tiers.A:
            return "A"
        if total >= self.tiers.B:
            return "B"
        if total >= self.tiers.C:
            return "C"
        return "D"

    def total(self, pain: float, value: float, reachability: float, fit: float) -> float:
        dims = {"pain": pain, "value": value, "reachability": reachability, "fit": fit}
        if any(dims[d] <= 0 for d in self.zero_kills):
            return 0.0
        w = self.weights
        return w.pain * pain + w.value * value + w.reachability * reachability + w.fit * fit


# --------------------------------------------------------------- models.yaml


class ModelRole(Strict):
    provider: Literal["anthropic"]
    model: Annotated[str, StringConstraints(min_length=1)]
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_output_tokens: Annotated[int, Field(gt=0, le=16000)]
    # Prices (USD per million tokens) and per-call token estimates drive the
    # pre-run cost projection. Refresh prices when the model changes; refine the
    # token estimates from real runs (the runs table records actuals).
    input_usd_per_mtok: Annotated[float, Field(ge=0)]
    output_usd_per_mtok: Annotated[float, Field(ge=0)]
    est_input_tokens: Annotated[int, Field(gt=0)]
    # Includes thinking tokens, which bill as output.
    est_output_tokens: Annotated[int, Field(gt=0)]
    prices_checked_on: str

    @model_validator(mode="after")
    def _est_fits(self) -> Self:
        if self.est_output_tokens > self.max_output_tokens:
            raise ValueError("est_output_tokens cannot exceed max_output_tokens")
        return self

    @property
    def est_cost_per_call_usd(self) -> float:
        return (
            self.est_input_tokens * self.input_usd_per_mtok
            + self.est_output_tokens * self.output_usd_per_mtok
        ) / 1_000_000

    def projected_cost_usd(self, calls: int) -> float:
        return calls * self.est_cost_per_call_usd


class ModelsConfig(Strict):
    version: int
    scoring: ModelRole
    opener: ModelRole


# ------------------------------------------------------------ categories/*.yaml


class SourceCodes(Strict):
    # "confirmed" = every subcategory code was seen in the named Overture
    # release's US taxonomy census (docs/reports/taxonomy-<release>.md).
    status: Literal["confirmed", "unconfirmed"]
    overture_release: str
    osm: list[str]
    note: str = ""

    @field_validator("osm")
    @classmethod
    def _osm_tag_format(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t.count("=") != 1]
        if bad:
            raise ValueError(f"OSM tags must look like key=value: {bad}")
        return v


class Subcategory(Strict):
    slug: Slug
    label: str
    include: bool
    # Overture taxonomy.primary codes that map to this subcategory.
    overture_codes: list[Slug]
    reason: str = ""

    @model_validator(mode="after")
    def _has_codes(self) -> Self:
        if not self.overture_codes:
            raise ValueError(f"subcategory {self.slug!r} needs at least one overture code")
        return self


class Exclusions(Strict):
    # Brand names matched at the START of the normalized business name
    # ("Aspen Dental - X" yes, "Twin Aspen Dental" no). Seed lists, incomplete.
    chain_name_patterns: list[Keyword]
    # Brands that appear mid-name ("Barlow Service Experts"). Matched anywhere.
    chain_name_contains: list[Keyword] = []
    # Matched anywhere in the name (supply houses, labs, schools...).
    keywords: list[Keyword]
    note: str = ""

    @field_validator("chain_name_patterns", "chain_name_contains", "keywords")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return _unique_lower(v, "exclusion entries")


class StaffTarget(Strict):
    min: Annotated[int, Field(ge=1)]
    max: Annotated[int, Field(ge=1, le=50)]
    typical: Annotated[int, Field(ge=1)]
    # Above this the lead is disqualified (staff_too_large), not just down-scored.
    hard_max: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _order(self) -> Self:
        if not self.min <= self.typical <= self.max <= self.hard_max:
            raise ValueError("staff_target must satisfy min <= typical <= max <= hard_max")
        return self


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class CallWindow(Strict):
    days: list[Weekday]
    start: time
    end: time
    why: str

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.start >= self.end:
            raise ValueError(f"call window start {self.start} must be before end {self.end}")
        if not self.days:
            raise ValueError("call window needs at least one day")
        return self


class OpenerTemplate(Strict):
    id: Slug
    requires_signal: str
    template: Annotated[str, StringConstraints(min_length=10)]

    @field_validator("requires_signal")
    @classmethod
    def _signal_ok(cls, v: str) -> str:
        if v not in SIGNALS:
            raise ValueError(f"unknown signal {v!r}")
        if not SIGNALS[v].opener_eligible:
            raise ValueError(f"signal {v!r} cannot be the basis of an opener")
        return v

    @field_validator("template")
    @classmethod
    def _placeholders(cls, v: str) -> str:
        fields = {f for _, f, _, _ in string.Formatter().parse(v) if f is not None}
        bad = sorted(fields - OPENER_PLACEHOLDERS)
        if bad:
            raise ValueError(
                f"opener placeholders {bad} not allowed; use {sorted(OPENER_PLACEHOLDERS)}"
            )
        return v


class CategoryConfig(Strict):
    slug: Slug
    label: str
    rank: Annotated[int, Field(ge=1)]
    pilot_phase: Literal["a", "b"] | None
    niche_knowledge_slug: str | None
    value_weight: Weight
    why_converts: list[str]
    source_codes: SourceCodes
    subcategories: list[Subcategory] = []
    pain_keywords: list[Keyword]
    review_pain_keywords: list[Keyword]
    negative_keywords: list[Keyword]
    exclusions: Exclusions
    staff_target: StaffTarget
    best_call_windows: list[CallWindow]
    opener_templates: list[OpenerTemplate]
    open_questions: list[str] = []

    @field_validator("pain_keywords", "review_pain_keywords", "negative_keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("keyword list must not be empty")
        return _unique_lower(v, "keywords")

    @property
    def included_codes(self) -> dict[str, str]:
        """Overture code -> subcategory slug, for included subcategories only."""
        return {c: s.slug for s in self.subcategories if s.include for c in s.overture_codes}

    @property
    def excluded_codes(self) -> dict[str, str]:
        return {c: s.slug for s in self.subcategories if not s.include for c in s.overture_codes}

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        codes = [c for s in self.subcategories for c in s.overture_codes]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"overture codes in more than one subcategory: {dupes}")
        if not self.included_codes:
            raise ValueError("at least one included subcategory is required")
        if not self.best_call_windows:
            raise ValueError("at least one best_call_window is required")
        if not self.opener_templates:
            raise ValueError("at least one opener template is required")
        ids = [o.id for o in self.opener_templates]
        if len(ids) != len(set(ids)):
            raise ValueError("opener template ids must be unique")
        overlap = sorted(set(self.pain_keywords) & set(self.negative_keywords))
        if overlap:
            raise ValueError(f"keyword both pain and negative: {overlap}")
        return self


# ------------------------------------------------------------------- bundle


class CrawlConfig(Strict):
    """config/crawl.yaml: politeness limits (floors enforced here) and signal signatures."""

    version: int
    user_agent: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    robots_agent: Annotated[str, StringConstraints(min_length=3, max_length=60)]
    concurrency_sites: Annotated[int, Field(ge=1, le=64)]
    per_site_delay_s: Annotated[float, Field(ge=1.0)]  # never hammer a small business site
    max_pages_per_site: Annotated[int, Field(ge=1, le=10)]
    max_bytes: Annotated[int, Field(ge=50_000, le=10_000_000)]
    timeout_s: Annotated[float, Field(gt=0, le=60)]
    max_redirects: Annotated[int, Field(ge=0, le=10)]
    max_attempts: Annotated[int, Field(ge=1, le=5)]
    link_kinds: dict[Slug, list[Keyword]]
    link_kind_priority: list[Slug]
    block_markers: list[Keyword]
    parked_markers: list[Keyword]
    booking_signatures: list[Keyword]
    chat_signatures: list[Keyword]

    @field_validator("block_markers", "parked_markers", "booking_signatures", "chat_signatures")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return _unique_lower(v, "crawl signature list")

    @model_validator(mode="after")
    def _kinds(self) -> Self:
        if set(self.link_kind_priority) != set(self.link_kinds):
            raise ValueError("link_kind_priority must list exactly the link_kinds")
        return self


class AppConfig(Strict):
    global_: GlobalConfig
    scoring: ScoringConfig
    models: ModelsConfig
    crawl: CrawlConfig
    categories: dict[str, CategoryConfig]
    config_hash: str

    @model_validator(mode="after")
    def _cross_file(self) -> Self:
        bounds = self.global_.calling_hours_hard_bounds
        for cat in self.categories.values():
            for w in cat.best_call_windows:
                if w.start < bounds.earliest or w.end > bounds.latest:
                    raise ValueError(
                        f"{cat.slug}: call window {w.start}-{w.end} is outside the hard "
                        f"bounds {bounds.earliest}-{bounds.latest}"
                    )
        ranks = sorted(c.rank for c in self.categories.values())
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError(f"category ranks must be 1..N with no gaps, got {ranks}")
        overlap = set(self.categories) & set(self.global_.watch_list)
        if overlap:
            raise ValueError(f"category both active and on watch list: {sorted(overlap)}")
        stale = sorted(
            c.slug
            for c in self.categories.values()
            if c.source_codes.status == "confirmed"
            and c.source_codes.overture_release != self.global_.overture_release
        )
        if stale:
            raise ValueError(
                f"codes confirmed against another Overture release (re-run census): {stale}"
            )
        unknown = sorted(set(self.global_.pilot_sample) - set(self.categories))
        if unknown:
            raise ValueError(f"pilot_sample names unknown categories: {unknown}")
        seen: dict[str, str] = {}
        for cat in self.categories.values():
            for code in [*cat.included_codes, *cat.excluded_codes]:
                if code in seen:
                    raise ValueError(f"overture code {code!r} in both {seen[code]} and {cat.slug}")
                seen[code] = cat.slug
        return self

    def code_map(self, slugs: list[str]) -> dict[str, tuple[str, str]]:
        """Included Overture code -> (category, subcategory) for the given categories."""
        out: dict[str, tuple[str, str]] = {}
        for slug in slugs:
            if slug not in self.categories:
                raise KeyError(f"unknown category {slug!r}")
            for code, sub in self.categories[slug].included_codes.items():
                out[code] = (slug, sub)
        return out

    def pilot_categories(self, phase: str) -> list[CategoryConfig]:
        return sorted(
            (c for c in self.categories.values() if c.pilot_phase == phase), key=lambda c: c.rank
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _hash(raw: dict[str, Any]) -> str:
    blob = json.dumps(raw, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def load_config(config_dir: Path) -> AppConfig:
    raw: dict[str, Any] = {
        "global": _read_yaml(config_dir / "global.yaml"),
        "scoring": _read_yaml(config_dir / "scoring.yaml"),
        "models": _read_yaml(config_dir / "models.yaml"),
        "crawl": _read_yaml(config_dir / "crawl.yaml"),
        "categories": {},
    }
    categories: dict[str, CategoryConfig] = {}
    for path in sorted((config_dir / "categories").glob("*.yaml")):
        data = _read_yaml(path)
        try:
            cat = CategoryConfig.model_validate(data)
        except Exception as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        if cat.slug != path.stem:
            raise ValueError(f"{path.name}: slug {cat.slug!r} must match file name")
        categories[cat.slug] = cat
        raw["categories"][cat.slug] = data
    return AppConfig(
        global_=GlobalConfig.model_validate(raw["global"]),
        scoring=ScoringConfig.model_validate(raw["scoring"]),
        models=ModelsConfig.model_validate(raw["models"]),
        crawl=CrawlConfig.model_validate(raw["crawl"]),
        categories=categories,
        config_hash=_hash(raw),
    )


def resolve_model(cfg: AppConfig, role: Literal["scoring", "opener"], override: str | None) -> str:
    """Env override (LEADS_SCORING_MODEL / LEADS_OPENER_MODEL) wins over YAML."""
    if override:
        return override
    return getattr(cfg.models, role).model  # type: ignore[no-any-return]
