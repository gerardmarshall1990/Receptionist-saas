"""Gates (spec section 2). Pure: SiteFacts + Listing + config -> list[GateResult].

Rules:
* A hard gate passes only on VERIFIED facts. Anything the sources cannot confirm is CHECK
  ("unverified"), never a guessed pass.
* Every result carries a one-line reason.
* Soft flags (hard=False) never reject.
"""
from __future__ import annotations

from typing import Any

from .models import (CHECK, FAIL, FLAG, INCOMPLETE, INDICATED, MISSING, PASS, VERIFIED,
                     GateResult, Listing, SiteFacts)
from .text import dwelling_hint
from .titles import FREEHOLD, shares_access_lot

FLOOD_LABELS = {
    "flood_plain": "flood plain",
    "flood_prone": "flood-prone area",
    "overland_flow_path": "overland flow path",
    "flood_sensitive": "flood sensitive area",
}
OVERLAY_LABELS = {
    "heritage_extent": "Historic Heritage (extent of place)",
    "heritage_place": "Historic Heritage place",
    "sea": "Significant Ecological Area",
    "mana_whenua": "Site of Significance to Mana Whenua",
    "special_character": "Special Character Area",
    "volcanic_viewshaft_regional": "Regional volcanic viewshaft / height-sensitive area",
    "volcanic_viewshaft_local": "Local volcanic viewshaft",
    "notable_tree": "Notable tree",
}


def _money(v: float) -> str:
    return f"${v:,.0f}"


def gate_address(f: SiteFacts) -> GateResult:
    if f.matched_address.ok:
        note = f" ({f.matched_address.note})" if f.matched_address.note else ""
        return GateResult("address", PASS, f"Matched to council property '{f.matched_address.value}'{note}")
    return GateResult("address", CHECK, f"Address not matched: {f.matched_address.note or 'no match'}")


def gate_zone(f: SiteFacts, allowed: list[str], consent: dict[str, str] | None = None) -> GateResult:
    z = f.zone
    if z.status != VERIFIED:
        return GateResult("zone", CHECK, f"Zone unverified: {z.note or 'lookup failed'}")
    if z.value in allowed:
        return GateResult("zone", PASS, f"{z.value} (operative)")
    if consent and z.value in consent:
        return GateResult("zone", CHECK, f"{z.value}: {consent[z.value]}")
    return GateResult("zone", FAIL, f"Zone is {z.value}, not MHS/MHU")


def gate_title(f: SiteFacts) -> GateResult:
    t = f.title_type
    if t.status == VERIFIED and t.value == FREEHOLD:
        return GateResult("title", PASS, f"Freehold (fee simple) per {t.source}")
    if t.status in (VERIFIED, INDICATED) and t.value and t.value != FREEHOLD:
        how = "per" if t.status == VERIFIED else "indicated by"
        return GateResult("title", FAIL, f"{t.value} {how} {t.source}")
    return GateResult("title", CHECK, f"Title type unverified: {t.note or 'no title data'}")


def gate_land(f: SiteFacts, listing: Listing, min_m2: float) -> GateResult:
    a = f.land_area_m2
    if a.status == VERIFIED and a.value is not None:
        if a.value >= min_m2:
            return GateResult("land_area", PASS, f"{a.value:,.0f} m² ≥ {min_m2:,.0f} m² ({a.source})")
        return GateResult("land_area", FAIL, f"{a.value:,.0f} m² < {min_m2:,.0f} m² minimum ({a.source})")
    if listing.land_area_m2:
        return GateResult("land_area", CHECK,
                          f"Parcel area unverified; listing claims {listing.land_area_m2:,.0f} m²")
    return GateResult("land_area", INCOMPLETE, "No land area: parcel not found and listing gives none")


def gate_dwellings(f: SiteFacts, listing: Listing, required: int) -> GateResult:
    n, why = dwelling_hint(listing.description, listing.existing_dwellings)
    if n is None:
        return GateResult("existing_dwellings", CHECK, f"Dwelling count unclear: {why}")
    if listing.existing_dwellings is None:
        # inferred from text only: absence of evidence is not verification
        return GateResult("existing_dwellings", CHECK, f"Probably {n} dwelling ({why}); confirm")
    if n == required:
        return GateResult("existing_dwellings", PASS, why)
    return GateResult("existing_dwellings", FAIL, f"{n} existing dwellings (need exactly {required})")


def gate_flood(f: SiteFacts, layers: list[str]) -> list[GateResult]:
    out: list[GateResult] = []
    core_hits, partial_hits, unknown = [], [], []
    for layer in layers:
        fact = f.flood.get(layer)
        label = FLOOD_LABELS.get(layer, layer)
        if fact is None or fact.status != VERIFIED:
            unknown.append(label)
        elif fact.value == "core":
            core_hits.append(label)
        elif fact.value == "partial":
            partial_hits.append(label)
    if core_hits:
        out.append(GateResult("flood", FAIL, "Likely building area intersects " + ", ".join(core_hits)))
    elif unknown:
        out.append(GateResult("flood", CHECK, "Flood layers not checked: " + ", ".join(unknown)))
    else:
        out.append(GateResult("flood", PASS, "No flood plain / flood-prone / overland flow path on likely building area"))
    if partial_hits:
        out.append(GateResult("flood_partial", FLAG, "Parcel edge touches " + ", ".join(partial_hits) + " (partial)", hard=False))
    return out


def gate_overlays(f: SiteFacts, overlay_cfg: dict[str, str]) -> list[GateResult]:
    out: list[GateResult] = []
    blocking, unknown = [], []
    for layer, mode in overlay_cfg.items():
        fact = f.overlays.get(layer)
        label = OVERLAY_LABELS.get(layer, layer)
        if fact is None or fact.status != VERIFIED:
            if mode == "block":
                unknown.append(label)
            continue
        hits = [h for h in (fact.value or []) if h.get("hit_parcel")]
        if not hits:
            continue
        detail = "; ".join(filter(None, (h.get("detail") for h in hits)))
        text = label + (f" ({detail})" if detail else "")
        if mode == "block":
            blocking.append(text)
        else:
            out.append(GateResult(f"overlay_{layer}", FLAG, text, hard=False))
    if blocking:
        out.insert(0, GateResult("overlays", FAIL, "Blocking overlay: " + "; ".join(blocking)))
    elif unknown:
        out.insert(0, GateResult("overlays", CHECK, "Overlays not checked: " + ", ".join(unknown)))
    else:
        out.insert(0, GateResult("overlays", PASS, "No heritage, SEA or mana whenua overlay"))
    return out


def gate_price(listing: Listing, max_price: float) -> list[GateResult]:
    if listing.price is None:
        txt = f" ('{listing.price_text}')" if listing.price_text else ""
        return [GateResult("price", INCOMPLETE, f"No price{txt}; feaso not run")]
    out = []
    if listing.price <= max_price:
        out.append(GateResult("price", PASS, f"{_money(listing.price)} ≤ max {_money(max_price)}"))
    else:
        out.append(GateResult("price", FAIL, f"{_money(listing.price)} > max {_money(max_price)}"))
    if listing.price_is_indicative:
        out.append(GateResult("price_indicative", FLAG, f"Price is indicative ('{listing.price_text}')", hard=False))
    return out


def soft_flags(f: SiteFacts, listing: Listing, g: dict[str, Any]) -> list[GateResult]:
    out: list[GateResult] = []
    rf = f.road_frontage
    if rf.status == VERIFIED and rf.value is False:
        out.append(GateResult("rear_site", FLAG, "No road frontage found: likely rear site, check access width", hard=False))
    if shares_access_lot(f.legal_description.value):
        out.append(GateResult("shared_access", FLAG, "Title includes a share in an access lot (JOAL/ROW)", hard=False))
    w = f.width_m
    if w.status == VERIFIED and w.value is not None and w.value < g["narrow_site_width_m"]:
        out.append(GateResult("narrow", FLAG, f"Narrow site: ~{w.value:.1f} m minimum width", hard=False))
    if f.multi_parcel.status == VERIFIED and f.multi_parcel.value:
        out.append(GateResult("multi_parcel", FLAG, f"Multiple parcels/titles: {f.title_refs.value}", hard=False))
    fs = f.flood.get("flood_sensitive")
    if g.get("flood_sensitive_is_flag") and fs and fs.status == VERIFIED and fs.value in ("core", "partial"):
        out.append(GateResult("flood_sensitive", FLAG, "Flood sensitive area on parcel", hard=False))
    if f.rub_nearby.status == VERIFIED and f.rub_nearby.value:
        out.append(GateResult("rural_urban_boundary", FLAG, f"Within {g['rub_proximity_m']} m of the Rural Urban Boundary", hard=False))
    if f.pc120_zone.status == VERIFIED and f.pc120_zone.value and f.zone.value and f.pc120_zone.value != f.zone.value:
        out.append(GateResult("pc120", FLAG, f"Proposed PC120 zone differs: {f.pc120_zone.value} (not yet decided)", hard=False))
    if f.zone_other.status == VERIFIED and f.zone_other.value:
        out.append(GateResult("zone_non_operative", FLAG, f"Non-operative zone rows on site: {f.zone_other.value}", hard=False))
    la, pa = listing.land_area_m2, f.land_area_m2
    if la and pa.status == VERIFIED and pa.value and abs(la - pa.value) / pa.value > 0.10:
        out.append(GateResult("area_mismatch", FLAG, f"Listing says {la:,.0f} m², parcel is {pa.value:,.0f} m²", hard=False))
    b = f.building_outlines
    if b.status == VERIFIED and isinstance(b.value, int) and b.value > 1:
        out.append(GateResult("buildings", FLAG, f"{b.value} building outlines on parcel (LINZ): check for a second dwelling", hard=False))
    out.append(GateResult("slope", FLAG, "Slope not assessed: no slope data source wired in", hard=False))
    out.append(GateResult("manual_title_check", FLAG, "Easements, covenants (incl. retirement-village) not checked: read the title", hard=False))
    return out


def evaluate(f: SiteFacts, listing: Listing, gcfg: dict[str, Any]) -> list[GateResult]:
    results = [gate_address(f), gate_zone(f, gcfg["allowed_zones"], gcfg.get("consent_zones")), gate_title(f),
               gate_land(f, listing, gcfg["min_land_m2"]),
               gate_dwellings(f, listing, gcfg["required_existing_dwellings"])]
    results += gate_flood(f, gcfg["flood_layers"])
    results += gate_overlays(f, gcfg["overlays"])
    results += gate_price(listing, gcfg["max_price"])
    results += soft_flags(f, listing, gcfg)
    return results


__all__ = ["evaluate", "MISSING"]
