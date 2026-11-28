"""Domain models. Every looked-up fact carries its own verification status and source,
so the gates can refuse to pass anything that is not verified."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VERIFIED = "verified"        # read directly from an authoritative source
INDICATED = "indicated"      # read from a secondary official record (e.g. council property description)
UNVERIFIED = "unverified"    # lookup failed / ambiguous / source cannot confirm
MISSING = "missing"          # input not supplied (e.g. listing has no price)


@dataclass
class Fact:
    value: Any = None
    status: str = UNVERIFIED
    source: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Fact":
        return cls(**d) if d else cls()


@dataclass
class Listing:
    address: str
    source: str = "paste"
    source_id: str | None = None
    url: str | None = None
    price: float | None = None
    price_text: str | None = None          # e.g. "Enquiries over $900,000"
    price_is_indicative: bool = False      # True for "enquiries over", "from", etc.
    bedrooms: int | None = None
    bathrooms: float | None = None
    land_area_m2: float | None = None
    property_type: str | None = None
    listing_date: str | None = None
    description: str | None = None
    existing_dwellings: int | None = None  # explicit count if the user knows it
    existing_rent_override: float | None = None
    unit_rent_override: float | None = None

    def key(self) -> tuple[str, str]:
        return (self.source, self.source_id or normalise_key(self.address))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_key(address: str) -> str:
    return " ".join(address.lower().replace(",", " ").split())


@dataclass
class Overlay:
    layer: str
    label: str
    hit_core: bool
    hit_parcel: bool
    detail: str = ""


@dataclass
class SiteFacts:
    matched_address: Fact = field(default_factory=Fact)
    property_id: Fact = field(default_factory=Fact)
    legal_description: Fact = field(default_factory=Fact)
    title_refs: Fact = field(default_factory=Fact)
    title_type: Fact = field(default_factory=Fact)
    land_area_m2: Fact = field(default_factory=Fact)
    multi_parcel: Fact = field(default_factory=Fact)
    width_m: Fact = field(default_factory=Fact)
    depth_m: Fact = field(default_factory=Fact)
    road_frontage: Fact = field(default_factory=Fact)
    zone: Fact = field(default_factory=Fact)
    zone_other: Fact = field(default_factory=Fact)     # non-operative zone rows (proposed/appeal)
    pc120_zone: Fact = field(default_factory=Fact)
    existing_dwellings: Fact = field(default_factory=Fact)
    building_outlines: Fact = field(default_factory=Fact)
    flood: dict[str, Fact] = field(default_factory=dict)       # layer -> Fact(value="core"|"partial"|"none")
    overlays: dict[str, Fact] = field(default_factory=dict)    # layer -> Fact(value=[Overlay dicts])
    rub_nearby: Fact = field(default_factory=Fact)
    sa2: Fact = field(default_factory=Fact)                    # value={"code","name"}
    lat: float | None = None
    lon: float | None = None
    parcel_geojson: dict | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Fact):
                d[k] = v.to_dict()
            elif isinstance(v, dict) and v and all(isinstance(x, Fact) for x in v.values()):
                d[k] = {kk: vv.to_dict() for kk, vv in v.items()}
            else:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SiteFacts":
        s = cls()
        for k, v in d.items():
            cur = getattr(s, k, None)
            if isinstance(cur, Fact):
                setattr(s, k, Fact.from_dict(v))
            elif k in ("flood", "overlays"):
                setattr(s, k, {kk: Fact.from_dict(vv) for kk, vv in (v or {}).items()})
            else:
                setattr(s, k, v)
        return s


# Gate statuses
PASS, FAIL, CHECK, FLAG, INCOMPLETE = "pass", "fail", "check", "flag", "incomplete"


@dataclass
class GateResult:
    gate: str
    status: str          # pass | fail | check | flag | incomplete
    reason: str          # one line, human readable. Always non-empty.
    hard: bool = True    # soft flags never reject

    def __post_init__(self):
        if not self.reason or not self.reason.strip():
            raise ValueError(f"gate {self.gate} produced an empty reason")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def overall_status(results: list[GateResult]) -> str:
    hard = [r for r in results if r.hard]
    if any(r.status == FAIL for r in hard):
        return FAIL
    if any(r.status == INCOMPLETE for r in hard):
        return INCOMPLETE
    if any(r.status == CHECK for r in hard):
        return CHECK
    return PASS
