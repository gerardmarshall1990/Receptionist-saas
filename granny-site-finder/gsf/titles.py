"""Title-type classification. Pure functions.

Authoritative: LINZ NZ Property Titles (layer 50804) fields `type` + `estate_description`.
Secondary:     Auckland Council property record `PROPERTYDESCRIPTION` (legal description).

The council description can *indicate* a non-freehold tenure (it literally says "Flat 1 DP ...,
1/2 SH Lot ..." for a cross-lease, "Unit B UP ..." for a unit title). It can never *confirm*
fee simple: a plain "Lot 10 DP 4165" is consistent with freehold but is not proof, so that case
stays unverified until LINZ is checked.
"""
from __future__ import annotations

import re

from .models import INDICATED, UNVERIFIED, VERIFIED, Fact

FREEHOLD = "Freehold"
CROSS_LEASE = "Cross-lease"
UNIT_TITLE = "Unit title"
LEASEHOLD = "Leasehold"
OTHER = "Other"


def classify_linz_title(title_type: str | None, estate_description: str | None) -> str:
    t = (title_type or "").strip().lower()
    e = (estate_description or "").lower()
    if "unit title" in t or "stratum" in t or "unit plan" in e:
        return UNIT_TITLE
    # Cross-leases: fee simple *share* plus a lease of the flat ("Composite" title type).
    if "cross" in t or "composite" in t or ("share" in e and "lease" in e):
        return CROSS_LEASE
    if "leasehold" in t or (t != "freehold" and "lease" in e):
        return LEASEHOLD
    if t == "freehold" and "fee simple" in e and "share" not in e and "lease" not in e:
        return FREEHOLD
    return OTHER


def title_fact_from_linz(rows: list[dict]) -> Fact:
    """rows: LINZ title records for every title on the parcel."""
    if not rows:
        return Fact(None, UNVERIFIED, "LINZ NZ Property Titles", "no title record intersects the parcel")
    classes = {r.get("title_no"): classify_linz_title(r.get("type"), r.get("estate_description")) for r in rows}
    kinds = set(classes.values())
    detail = "; ".join(f"{k}: {r.get('type')} / {r.get('estate_description')}" for k, r in
                       ((r.get("title_no"), r) for r in rows))
    if kinds == {FREEHOLD}:
        return Fact(FREEHOLD, VERIFIED, "LINZ NZ Property Titles", detail)
    if OTHER in kinds and len(kinds) == 1:
        return Fact(OTHER, UNVERIFIED, "LINZ NZ Property Titles", f"unrecognised title type: {detail}")
    worst = next(k for k in (CROSS_LEASE, UNIT_TITLE, LEASEHOLD, OTHER) if k in kinds)
    return Fact(worst, VERIFIED, "LINZ NZ Property Titles", detail)


_FLAT = re.compile(r"\b(flat|fl)\s*\w+\s*(on\s+)?(dp|deposited plan)\b", re.I)
_SHARE = re.compile(r"\b\d+\s*/\s*\d+\s*(sh|share)\b", re.I)
_UNIT = re.compile(r"\b(unit|accessory unit|au|pu|prin|principal unit)\s+[\w-]+\s+(on\s+)?(up|unit plan|sp|dp|deposited plan)\b|\bup\s*\d{3,}", re.I)
_LEASE = re.compile(r"\b(lease|lse|leasehold)\b", re.I)
_LOT = re.compile(r"\b(pt\s+|part\s+)?(lot|allot(ment)?|sec(tion)?)\s+\w+", re.I)


def title_fact_from_council_records(description: str | None, children: list[dict] | None = None) -> Fact:
    """Classify a site from its own description plus any child records (flats/units on the same lot)."""
    if children:
        for c in children:
            f = title_fact_from_council(c.get("PROPERTYDESCRIPTION"))
            if f.status == INDICATED:
                f.note = f"child record {c.get('FORMATTEDADDRESS')}: {f.note}"
                return f
        return Fact(None, UNVERIFIED, "Auckland Council property record (legal description)",
                    f"lot has {len(children)} child property record(s); title structure unclear, check LINZ")
    return title_fact_from_council(description)


def title_fact_from_council(description: str | None) -> Fact:
    src = "Auckland Council property record (legal description)"
    d = (description or "").strip()
    if not d:
        return Fact(None, UNVERIFIED, src, "no legal description on the council record")
    if _UNIT.search(d):
        return Fact(UNIT_TITLE, INDICATED, src, f"description '{d}' indicates a unit title; confirm on LINZ")
    if _FLAT.search(d):
        return Fact(CROSS_LEASE, INDICATED, src, f"description '{d}' indicates a cross-lease; confirm on LINZ")
    if _LEASE.search(d):
        return Fact(LEASEHOLD, INDICATED, src, f"description '{d}' mentions a lease; confirm on LINZ")
    if _LOT.search(d):
        note = f"description '{d}' is consistent with fee simple but does not prove it; LINZ title not checked"
        if _SHARE.search(d):
            note += " (includes a share in another lot, likely a shared access lot)"
        return Fact(FREEHOLD, UNVERIFIED, src, note)
    return Fact(None, UNVERIFIED, src, f"unrecognised legal description '{d}'")


def shares_access_lot(description: str | None) -> bool:
    d = description or ""
    return bool(_SHARE.search(d)) and not _FLAT.search(d)


def lot_count(description: str | None) -> int:
    d = description or ""
    # count primary lots, excluding "1/4 SH LOT 5" style shares
    stripped = re.sub(r"\b\d+\s*/\s*\d+\s*(sh|share)\s+(in\s+)?(pt\s+)?lot\s+\w+", "", d, flags=re.I)
    return len(re.findall(r"\b(?:pt\s+)?lot\s+\w+", stripped, re.I))
