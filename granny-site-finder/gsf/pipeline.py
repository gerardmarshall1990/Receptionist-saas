"""listing -> facts -> gates -> rents -> feaso -> store. One failing address never stops a batch."""
from __future__ import annotations

import hashlib
import json
import logging
import traceback
from dataclasses import asdict
from typing import Any, Callable, Iterable

from . import feaso as fz
from . import geometry as geo
from .config import feaso_inputs, load_config, load_env, stress_spec
from .db import Store, content_hash
from .gates import evaluate
from .lookups.council import Council, parcel_shape
from .lookups.http import HttpClient, LookupFailed
from .lookups.linz import Linz, Sa2
from .lookups.rents import BondRents, RentResult
from .models import INDICATED, UNVERIFIED, VERIFIED, Fact, Listing, SiteFacts, overall_status
from .titles import lot_count, title_fact_from_council_records

log = logging.getLogger("gsf")


class Pipeline:
    def __init__(self, cfg: dict | None = None, store: Store | None = None,
                 council: Council | None = None, linz: Linz | None = None, sa2: Sa2 | None = None,
                 rents: BondRents | None = None):
        load_env()
        self.cfg = cfg or load_config()
        self.store = store or Store(self.cfg["storage"]["db_path"])
        h = self.cfg["http"]
        self.http = HttpClient(self.store.path, h["timeout_s"], h["retries"], h["backoff_s"], h["min_interval_s"])
        self.council = council or Council(self.http, self.cfg)
        self.linz = linz or Linz(self.http, self.cfg)
        self.sa2 = sa2 or Sa2(self.http, self.cfg)
        self.rents = rents or BondRents(self.http, self.cfg, self.store.path)
        self._rents_ready: str | None = None

    # ---------------------------------------------------------------- facts --
    def _step(self, facts: SiteFacts, name: str, fn: Callable[[], Any], on_fail: Callable[[str], None]):
        try:
            return fn()
        except LookupFailed as e:
            msg = f"{name}: {e}"
        except Exception as e:  # never crash the batch
            log.debug(traceback.format_exc())
            msg = f"{name}: unexpected {type(e).__name__}: {e}"
        facts.errors.append(msg)
        on_fail(msg)
        return None

    def gather_facts(self, listing: Listing) -> SiteFacts:
        f = SiteFacts()
        g = self.cfg["gates"]
        res = self._step(f, "address", lambda: self.council.find_property(listing.address),
                         lambda m: setattr(f, "matched_address", Fact(None, UNVERIFIED, "council", m)))
        if not res:
            return f
        feat, f.matched_address, _cands = res
        if feat is None:
            return f
        a = feat["attributes"]
        rings = feat["geometry"]["rings"]
        core = geo.scaled(rings, g["building_core_scale"])
        src = "Auckland Council property boundary (area computed in NZTM)"
        f.property_id = Fact(a.get("PROPERTYID"), VERIFIED, "council")
        f.legal_description = Fact(a.get("PROPERTYDESCRIPTION"), VERIFIED if a.get("PROPERTYDESCRIPTION") else UNVERIFIED, "council")
        f.title_refs = Fact(a.get("FORMATTEDTITLES"), VERIFIED if a.get("FORMATTEDTITLES") else UNVERIFIED, "council")
        shape = parcel_shape(rings)
        f.land_area_m2 = Fact(round(shape["area"], 1), VERIFIED, src)
        f.width_m = Fact(round(shape["width"], 1), VERIFIED, "minimum bounding rectangle")
        f.depth_m = Fact(round(shape["depth"], 1), VERIFIED, "minimum bounding rectangle")
        titles = [t.strip() for t in (a.get("FORMATTEDTITLES") or "").split(",") if t.strip()]
        f.multi_parcel = Fact(len(titles) > 1 or lot_count(a.get("PROPERTYDESCRIPTION")) > 1, VERIFIED, "council legal description")
        f.parcel_geojson = None

        wgs_rings: list = []

        def wgs():
            w = self.council.wgs84_geometry(a["OBJECTID"])
            if w:
                wgs_rings.extend(w)
                cx, cy = geo.centroid(geo.outer_ring(w))
                f.lon, f.lat = round(cx, 6), round(cy, 6)
                f.parcel_geojson = geo.to_geojson(w)
        self._step(f, "map location", wgs, lambda m: None)

        # Title type: LINZ is authoritative; council description can only indicate non-freehold.
        council_title = title_fact_from_council_records(a.get("PROPERTYDESCRIPTION"), feat.get("children"))
        if council_title.status == INDICATED:
            f.title_type = council_title
            if council_title.value in ("Cross-lease", "Unit title"):
                f.land_area_m2.note = "area is the whole underlying lot, shared with other owners"
        else:
            f.title_type = council_title
        if self.linz.enabled and wgs_rings:
            tx, ty = geo.interior_point(wgs_rings)
            lt = self._step(f, "LINZ titles", lambda: self.linz.titles(tx, ty), lambda m: None)
            if lt is not None and lt.status == VERIFIED:
                f.title_type = lt
            elif lt is not None and f.title_type.status != INDICATED:
                lt.note = f"{lt.note}; council: {f.title_type.note}" if f.title_type.note else lt.note
                f.title_type = lt
            b = self._step(f, "LINZ buildings", lambda: self.linz.buildings(wgs_rings), lambda m: None)
            if b is not None:
                f.building_outlines = b
        else:
            if f.title_type.status != INDICATED:
                f.title_type.note += "; set LINZ_API_KEY to verify"

        def zone():
            f.zone, f.zone_other = self.council.zone(rings, core)
        self._step(f, "zone", zone, lambda m: setattr(f, "zone", Fact(None, UNVERIFIED, "council", m)))

        for layer in g["flood_layers"] + (["flood_sensitive"] if g.get("flood_sensitive_is_flag") else []):
            where = "1=1"
            if layer == "overland_flow_path":
                where = f"CatchmentAreaGroup>={int(g.get('overland_flow_min_catchment_group', 1))}"
            v = self._step(f, layer, lambda layer=layer, where=where: self.council.hit_level(layer, rings, core, where),
                           lambda m: None)
            f.flood[layer] = v if v is not None else Fact(None, UNVERIFIED, layer, f.errors[-1])

        for layer in g["overlays"]:
            v = self._step(f, layer, lambda layer=layer: self.council.overlay(layer, rings, core), lambda m: None)
            f.overlays[layer] = v if v is not None else Fact(None, UNVERIFIED, layer, f.errors[-1])

        v = self._step(f, "road frontage", lambda: self.council.road_frontage(rings, g["road_frontage_tolerance_m"]), lambda m: None)
        if v is not None:
            f.road_frontage = v
        v = self._step(f, "RUB", lambda: self.council.near("rural_urban_boundary", rings, g["rub_proximity_m"]), lambda m: None)
        if v is not None:
            f.rub_nearby = v
        v = self._step(f, "PC120", lambda: self.council.pc120_zone(core), lambda m: None)
        if v is not None:
            f.pc120_zone = v
        px, py = geo.interior_point(rings)
        f.sa2 = self.sa2.lookup(px, py)
        if f.sa2.status == VERIFIED and self.cfg["rents"].get("neighbour_radius_m"):
            nb = self._step(f, "nearby SA2s", lambda: self.sa2.neighbours(px, py, self.cfg["rents"]["neighbour_radius_m"]),
                            lambda m: None)
            if nb:
                f.sa2.value["neighbours"] = nb

        return f

    # ---------------------------------------------------------------- rents --
    def _rent(self, f: SiteFacts, beds: int | None, kind: str) -> RentResult:
        rc = self.cfg["rents"]
        stat = rc["unit_rent_stat"] if kind == "unit" else rc["existing_rent_stat"]
        types = rc["unit_dwelling_types"] if kind == "unit" else rc["existing_dwelling_types"]
        if f.sa2.status != VERIFIED:
            return RentResult(None, stat, "?", None, str(beds or "?"), None, None, None, note="SA2 unknown")
        if self._rents_ready is None:
            try:
                self._rents_ready = self.rents.ensure_loaded()
            except LookupFailed as e:
                return RentResult(None, stat, f.sa2.value["code"], f.sa2.value["name"], str(beds), None, None, None,
                                  note=f"bond data unavailable: {e}")
        return self.rents.lookup(f.sa2.value["code"], f.sa2.value["name"], beds, types, stat,
                                 neighbours=f.sa2.value.get("neighbours"))

    def rents_for(self, listing: Listing, f: SiteFacts) -> tuple[dict, dict]:
        rc = self.cfg["rents"]
        beds = listing.bedrooms if listing.bedrooms is not None else rc.get("default_bedrooms_if_unknown")
        if listing.existing_rent_override:
            ex = {"weekly": listing.existing_rent_override, "label": f"manual override ${listing.existing_rent_override:,.0f}/wk", "ok": True}
        else:
            r = self._rent(f, beds, "existing")
            assumed = listing.bedrooms is None and beds is not None
            ex = asdict(r) | {"label": ("ASSUMED %s-bed (listing gives no bedrooms): " % beds if assumed else "") + r.label(),
                              "ok": r.ok, "bedrooms_assumed": assumed}
        if listing.unit_rent_override:
            un = {"weekly": listing.unit_rent_override, "label": f"manual override ${listing.unit_rent_override:,.0f}/wk", "ok": True}
        else:
            r = self._rent(f, rc["unit_bedrooms"], "unit")
            un = asdict(r) | {"label": r.label(), "ok": r.ok}
        return ex, un

    # ---------------------------------------------------------------- feaso --
    def feaso(self, listing: Listing, ex: dict, un: dict, overrides: dict | None = None) -> dict:
        out = {"existing_rent": ex, "unit_rent": un}
        missing = []
        if listing.price is None:
            missing.append("price")
        if not ex.get("ok"):
            missing.append(f"existing rent ({ex.get('note') or ex.get('label')})")
        if not un.get("ok"):
            missing.append(f"unit rent ({un.get('note') or un.get('label')})")
        if missing:
            return out | {"status": "incomplete", "note": "missing " + "; ".join(missing), "report": None}
        try:
            ov = dict(overrides or {})
            if listing.cv and "cv" not in ov:
                ov["cv"] = listing.cv
            inp = feaso_inputs(self.cfg, listing.price, ex["weekly"], un["weekly"], ov)
            rep = fz.run_with_stress(inp, stress_spec(self.cfg))
        except fz.FeasoInputError as e:
            return out | {"status": "incomplete", "note": str(e), "report": None}
        return out | {"status": "ok", "note": None, "report": rep.to_dict()}

    # ---------------------------------------------------------------- run ----
    def process(self, listing: Listing, refresh_facts: bool = True) -> dict:
        lid, _changed = self.store.upsert_listing(listing)
        try:
            prev = self.store.site(lid)
            if not refresh_facts and prev and prev["facts"]:
                facts = SiteFacts.from_dict(prev["facts"])
            else:
                facts = self.gather_facts(listing)
            gates = evaluate(facts, listing, self.cfg["gates"])
            ex, un = self.rents_for(listing, facts)
            fe = self.feaso(listing, ex, un)
            overall = overall_status(gates)
        except Exception as e:  # last-resort guard: record, don't crash the batch
            log.exception("evaluation failed")
            facts = SiteFacts(errors=[f"pipeline: {type(e).__name__}: {e}"])
            from .models import CHECK, GateResult
            gates = [GateResult("pipeline", CHECK, f"Evaluation error: {type(e).__name__}: {e}")]
            fe = {"status": "incomplete", "note": "evaluation error", "report": None}
            overall = CHECK
        ev_hash = hashlib.sha256((content_hash(listing) + json.dumps(self.cfg, sort_keys=True, default=str)).encode()).hexdigest()
        self.store.save_evaluation(lid, facts.to_dict(), overall, [g.to_dict() for g in gates], fe, ev_hash)
        return self.store.site(lid)

    def run_batch(self, listings: Iterable[Listing], progress: Callable[[int, Listing, dict], None] | None = None) -> list[dict]:
        out = []
        for i, l in enumerate(listings):
            try:
                s = self.process(l)
            except Exception as e:   # e.g. DB write failure: report and continue
                log.exception("failed: %s", l.address)
                s = {"listing": l.to_dict(), "overall": "error", "error": str(e)}
            out.append(s)
            if progress:
                progress(i, l, s)
        return out

    def recompute(self, listing_id: int) -> dict | None:
        """Re-run gates + feaso from stored facts (after config or listing edits)."""
        l = self.store.listing(listing_id)
        return self.process(l, refresh_facts=False) if l else None
