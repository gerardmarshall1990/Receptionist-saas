"""Auckland Council open data (ArcGIS Online, org n4yPwebTjJCmXB6W). Verified 2026-11-28.

All spatial queries are sent in NZTM (EPSG:2193). Note: AC_Property_Query stores geometry in
Web Mercator, so its Shape__Area field overstates area by ~1.56x in Auckland. Never use it;
area is computed here from geometry requested in NZTM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .. import geometry as geo
from ..models import UNVERIFIED, VERIFIED, Fact
from .http import HttpClient, LookupFailed

STREET_TYPES = {
    "rd": "Road", "st": "Street", "ave": "Avenue", "av": "Avenue", "cres": "Crescent", "cr": "Crescent",
    "dr": "Drive", "pl": "Place", "tce": "Terrace", "ter": "Terrace", "ln": "Lane", "gr": "Grove",
    "hwy": "Highway", "pde": "Parade", "cl": "Close", "ct": "Court", "crt": "Court", "sq": "Square",
    "blvd": "Boulevard", "esp": "Esplanade", "wy": "Way", "rise": "Rise", "mews": "Mews", "cct": "Circuit",
    "gdns": "Gardens", "hts": "Heights", "vw": "View", "rdge": "Ridge",
}
ROAD_ZONE = "Road"


def _sql(s: str) -> str:
    return s.replace("'", "''")


@dataclass
class ParsedAddress:
    number: str           # "12", "304A", "1/304A"
    street: str           # "Sandringham Road"
    suburb: str | None


_ADDR = re.compile(r"^\s*(?:(?:flat|unit)\s+)?(?P<num>(?:[A-Za-z]?\d+[A-Za-z]?\s*/\s*)?\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?)\s+(?P<rest>.+)$", re.I)


def parse_address(raw: str) -> ParsedAddress | None:
    s = re.sub(r"\s+", " ", raw.replace("\t", " ")).strip()
    s = re.sub(r",?\s*(new zealand|nz)\s*$", "", s, flags=re.I)
    s = re.sub(r"\b\d{4}\s*$", "", s).strip(" ,")             # postcode
    m = _ADDR.match(s)
    if not m:
        return None
    num = m.group("num").replace(" ", "").upper()
    parts = [p.strip() for p in m.group("rest").split(",") if p.strip()]
    street_part = parts[0]
    suburb = parts[1] if len(parts) > 1 else None
    words = street_part.split()
    # Street suffix expansion, and suburb on the same comma-less segment ("12 Sandringham Rd Kingsland")
    for i, w in enumerate(words):
        key = w.lower().rstrip(".")
        if key in STREET_TYPES or w.capitalize() in STREET_TYPES.values():
            words[i] = STREET_TYPES.get(key, w.capitalize())
            if i < len(words) - 1 and suburb is None:
                suburb = " ".join(words[i + 1:])
            words = words[: i + 1]
            break
    street = " ".join(w if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:] for w in words)
    if suburb and suburb.lower() in ("auckland",):
        suburb = None
    if suburb:
        suburb = re.sub(r"\s+auckland$", "", suburb, flags=re.I).strip() or None
    return ParsedAddress(num, street, suburb)


class Council:
    def __init__(self, http: HttpClient, cfg: dict):
        self.http = http
        self.base = cfg["sources"]["council_base"]
        self.layers = cfg["sources"]["council_layers"]
        self.cache_days = cfg["http"]["cache_days"]["council"]
        self._domains: dict[str, dict[str, dict[Any, str]]] = {}

    def _url(self, key: str) -> str:
        return f"{self.base}/{self.layers[key]}/FeatureServer/0"

    def query(self, key: str, **params) -> list[dict]:
        p = {"f": "json", "outFields": "*", "returnGeometry": "false"}
        p.update({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()})
        data = self.http.request_json("POST", self._url(key) + "/query", p, cache_days=self.cache_days)
        if data.get("exceededTransferLimit"):
            raise LookupFailed(f"{self.layers[key]}: too many features returned")
        return data.get("features", [])

    def domains(self, key: str) -> dict[str, dict[Any, str]]:
        if key not in self._domains:
            meta = self.http.request_json("GET", self._url(key), {"f": "json"}, cache_days=30)
            self._domains[key] = {
                f["name"]: {c["code"]: c["name"] for c in f["domain"].get("codedValues", [])}
                for f in meta.get("fields", []) if f.get("domain") and f["domain"].get("codedValues")
            }
        return self._domains[key]

    def decode(self, key: str, field: str, value: Any) -> Any:
        return self.domains(key).get(field, {}).get(value, value)

    # ---- property / address -------------------------------------------------
    def find_property(self, address: str) -> tuple[dict | None, Fact, list[str]]:
        """Return (feature with NZTM geometry, matched_address fact, candidate list)."""
        pa = parse_address(address)
        if not pa:
            return None, Fact(None, UNVERIFIED, "parser", f"could not parse '{address}' (need number + street)"), []
        where = (f"UPPER(STREETNAME)='{_sql(pa.street.upper())}' AND UPPER(STREETNUMBER)='{_sql(pa.number)}'"
                 " AND SITESTATUS='Current'")
        feats = self.query("property", where=where, outSR=2193, returnGeometry="true")
        if not feats:
            feats = self.query("property", outSR=2193, returnGeometry="true",
                               where=f"UPPER(FORMATTEDADDRESS) LIKE '{_sql(pa.number + ' ' + pa.street).upper()}%' AND SITESTATUS='Current'")
        if pa.suburb and feats:
            sub = pa.suburb.upper()
            narrowed = [f for f in feats if sub in (f["attributes"].get("SUBURBNAME") or "").upper()
                        or sub in (f["attributes"].get("FORMATTEDADDRESS") or "").upper()]
            if narrowed:
                feats = narrowed
        src = "Auckland Council property (AC_Property_Query)"
        if not feats:
            return None, Fact(None, UNVERIFIED, src, f"no council property matches '{pa.number} {pa.street}'"
                              + (f" in {pa.suburb}" if pa.suburb else "")), []
        # Prefer Standard / Primary records; collapse duplicates of the same property
        feats.sort(key=lambda f: (f["attributes"].get("SITETYPE") != "Standard",
                                  f["attributes"].get("ADDRESSTYPE") != "Primary"))
        props = {}
        for f in feats:
            props.setdefault(f["attributes"]["PROPERTYID"], f)
        cands = [f["attributes"]["FORMATTEDADDRESS"] for f in props.values()]
        roots = {f["attributes"].get("PARENTPROPERTYID") or pid for pid, f in props.items()}
        if len(props) > 1 and len(roots) == 1:
            # One underlying lot split into child records (cross-lease flats / unit titles) at this address.
            root = next(iter(roots))
            parent = props.get(root) or next(iter(props.values()))
            children = [f["attributes"] for pid, f in props.items() if pid != root]
            parent = dict(parent, children=children)
            note = f"{len(children)} child property record(s) share this lot"
            return parent, Fact(parent["attributes"]["FORMATTEDADDRESS"], VERIFIED, src, note), cands
        if len(props) > 1:
            same = len(set(cands)) == 1
            hint = ("several council properties share this exact address; enter the flat/unit number"
                    if same else "add the suburb")
            return None, Fact(None, UNVERIFIED, src,
                              f"ambiguous: {len(props)} properties match; {hint} ({'; '.join(cands[:4])})"), cands
        f = next(iter(props.values()))
        if not f.get("geometry", {}).get("rings"):
            return None, Fact(None, UNVERIFIED, src, "council record has no boundary geometry"), cands
        return f, Fact(f["attributes"]["FORMATTEDADDRESS"], VERIFIED, src), cands

    def wgs84_geometry(self, objectid: int) -> list | None:
        feats = self.query("property", where=f"OBJECTID={int(objectid)}", outSR=4326, returnGeometry="true",
                           outFields="OBJECTID")
        return feats[0]["geometry"]["rings"] if feats else None

    # ---- spatial ----------------------------------------------------------------
    def intersects(self, key: str, rings: list, distance_m: float | None = None, where: str = "1=1",
                   out_fields: str = "*") -> list[dict]:
        params = dict(geometry={"rings": rings, "spatialReference": {"wkid": 2193}},
                      geometryType="esriGeometryPolygon", inSR=2193,
                      spatialRel="esriSpatialRelIntersects", where=where, outFields=out_fields)
        if distance_m:
            params.update(distance=distance_m, units="esriSRUnit_Meter")
        return [f["attributes"] for f in self.query(key, **params)]

    def zone(self, parcel: list, core: list) -> tuple[Fact, Fact]:
        src = "Auckland Unitary Plan base zone (operative)"
        rows = self.intersects("zone", core, where="VERSIONSTATUS=4", out_fields="ZONE,NAME,VERSIONSTATUS")
        names = sorted({self.decode("zone", "ZONE", r["ZONE"]) for r in rows} - {ROAD_ZONE})
        other_rows = self.intersects("zone", parcel, where="VERSIONSTATUS<>4", out_fields="ZONE,VERSIONSTATUS")
        other = sorted({f"{self.decode('zone', 'ZONE', r['ZONE'])} ({self.decode('zone', 'VERSIONSTATUS', r['VERSIONSTATUS'])})"
                        for r in other_rows})
        other_fact = Fact(other, VERIFIED, "Auckland Unitary Plan base zone (non-operative rows)")
        if len(names) == 1:
            return Fact(names[0], VERIFIED, src), other_fact
        if not names:
            return Fact(None, UNVERIFIED, src, "no operative zone found on the site"), other_fact
        return Fact(None, UNVERIFIED, src, "split zoned: " + " / ".join(names)), other_fact

    def road_frontage(self, parcel: list, tol_m: float) -> Fact:
        road_code = next((c for c, n in self.domains("zone").get("ZONE", {}).items() if n == ROAD_ZONE), 27)
        rows = self.intersects("zone", parcel, distance_m=tol_m, where=f"ZONE={road_code}", out_fields="OBJECTID")
        return Fact(bool(rows), VERIFIED, "Road zone within %.1f m of boundary" % tol_m)

    def hit_level(self, key: str, parcel: list, core: list, where: str = "1=1") -> Fact:
        src = self.layers[key]
        if self.intersects(key, core, where=where, out_fields="OBJECTID"):
            return Fact("core", VERIFIED, src)
        if self.intersects(key, parcel, where=where, out_fields="OBJECTID"):
            return Fact("partial", VERIFIED, src)
        return Fact("none", VERIFIED, src)

    def overlay(self, key: str, parcel: list, core: list) -> Fact:
        src = self.layers[key]
        rows = self.intersects(key, parcel)
        if not rows:
            return Fact([], VERIFIED, src)
        core_ids = {r["OBJECTID"] for r in self.intersects(key, core, out_fields="OBJECTID")}
        hits = []
        for r in rows:
            bits = [self.decode(key, "TYPE", r.get("TYPE")) if r.get("TYPE") is not None else None,
                    r.get("NAME"), f"Sched {r['SCHEDULE']}" if r.get("SCHEDULE") else None]
            vs = r.get("VERSIONSTATUS")
            if vs is not None and vs != 4:
                bits.append(str(self.decode(key, "VERSIONSTATUS", vs)))
            hits.append({"hit_parcel": True, "hit_core": r["OBJECTID"] in core_ids,
                         "detail": ", ".join(str(b) for b in bits if b)})
        return Fact(hits, VERIFIED, src)

    def pc120_zone(self, core: list) -> Fact:
        rows = self.intersects("pc120", core, out_fields="Zone1,Zone2,Change")
        zones = sorted({r["Zone1"] for r in rows if r.get("Zone1")})
        src = "PC120 (proposed) property summary"
        if len(zones) == 1:
            return Fact(zones[0], VERIFIED, src, (rows[0].get("Change") or "")[:300])
        if not zones:
            return Fact(None, VERIFIED, src, "no PC120 record")
        return Fact(" / ".join(zones), VERIFIED, src)

    def near(self, key: str, parcel: list, distance_m: float) -> Fact:
        return Fact(bool(self.intersects(key, parcel, distance_m=distance_m, out_fields="OBJECTID")),
                    VERIFIED, self.layers[key])


def parcel_shape(rings: list) -> dict[str, float]:
    w, d = geo.min_rect_dims(rings)
    return {"area": geo.polygon_area(rings), "width": w, "depth": d}
