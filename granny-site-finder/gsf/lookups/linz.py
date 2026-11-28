"""LINZ Data Service (WFS) and Stats NZ SA2 lookups.

LINZ WFS requires an API key (HTTP 401 without one). Layer ids and field names were verified on
2026-11-28 via the open LDS metadata API:
  50772 NZ Primary Parcels  (id, appellation, titles, survey_area, calc_area, shape)
  50804 NZ Property Titles  (title_no, status, type, estate_description, shape)
  101290 NZ Building Outlines (building_id, use, shape)
Queried through the LDS vector query API (point/radius). Without LINZ_API_KEY, title type stays
'unverified'.
"""
from __future__ import annotations

import math
import os
import re

from .. import geometry as geo
from ..models import UNVERIFIED, VERIFIED, Fact
from ..titles import title_fact_from_linz
from .http import HttpClient, LookupFailed


class Linz:
    """LINZ Data Service via the vector query API (verified live 2026-11-28 with a key).
    GET /services/query/v1/vector.json?layer=<id>&x=<lon>&y=<lat>&radius=<m>
    Auth: "Authorization: key <KEY>". (The WFS endpoint does not accept header auth.)"""

    QUERY = "https://data.linz.govt.nz/services/query/v1/vector.json"

    def __init__(self, http: HttpClient, cfg: dict):
        self.http = http
        self.layers = cfg["sources"]["linz_layers"]
        self.cache_days = cfg["http"]["cache_days"]["linz"]
        self.key = os.environ.get("LINZ_API_KEY", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _query(self, layer_id: int, lon: float, lat: float, radius_m: float = 0, geometry: bool = False) -> list[dict]:
        params = {"layer": layer_id, "x": round(lon, 7), "y": round(lat, 7), "radius": int(math.ceil(radius_m)),
                  "max_results": 100, "geometry": "true" if geometry else "false", "with_field_names": "true"}
        data = self.http.request_json("GET", self.QUERY, params, cache_days=self.cache_days,
                                      headers={"Authorization": f"key {self.key}"})
        layer = ((data.get("vectorQuery") or {}).get("layers") or {}).get(str(layer_id)) or {}
        if "error" in layer:
            raise LookupFailed(f"LINZ layer {layer_id}: {layer['error']}")
        return layer.get("features", [])

    def titles(self, lon: float, lat: float) -> Fact:
        if not self.enabled:
            return Fact(None, UNVERIFIED, "LINZ NZ Property Titles", "LINZ_API_KEY not set; title type not checked")
        rows = [f["properties"] for f in self._query(self.layers["titles"], lon, lat)
                if (f["properties"].get("status") or "LIVE").upper() == "LIVE"]
        return title_fact_from_linz(rows)

    def buildings(self, wgs_rings: list, min_area_m2: float = 30) -> Fact:
        if not self.enabled:
            return Fact(None, UNVERIFIED, "LINZ NZ Building Outlines", "LINZ_API_KEY not set")
        import math
        ring = geo.outer_ring(wgs_rings)
        lon0, lat0 = geo.centroid(ring)
        kx, ky = 111320 * math.cos(math.radians(lat0)), 110574       # metres per degree
        to_m = lambda r: [[(x - lon0) * kx, (y - lat0) * ky] for x, y in r]  # noqa: E731
        parcel_m = [to_m(r) for r in wgs_rings]
        radius = max(math.hypot(x, y) for x, y in parcel_m[0]) + 5
        n = 0
        for f in self._query(self.layers["buildings"], lon0, lat0, radius, geometry=True):
            for poly in self._polys(f.get("geometry")):
                pm = [to_m(r) for r in poly]
                cx, cy = geo.centroid(pm[0])
                if geo.point_in_polygon(cx, cy, parcel_m) and abs(geo.ring_signed_area(pm[0])) >= min_area_m2:
                    n += 1
        return Fact(n, VERIFIED, "LINZ NZ Building Outlines", f"buildings >= {min_area_m2} m² with centroid on parcel")

    def lot_area(self, lon: float, lat: float, description: str | None, title_refs: str | None,
                 council_area: float) -> Fact | None:
        """Area of the lots the owner actually holds, from LINZ Primary Parcels. Needed when the council
        boundary also includes shares in common/access lots ("1/34 SH LOT 3018 DP ..."). Returns None when
        not applicable or no key."""
        if not self.enabled or not description:
            return None
        own = own_lots(description)
        if not own or not re.search(r"\bSH(ARE)?\b", description, re.I):
            return None
        refs = {t.strip().upper() for t in (title_refs or "").split(",") if t.strip()}
        radius = min(1000, int(math.sqrt(max(council_area, 100)) * 1.5 + 60))
        params = {"layer": self.layers["parcels"], "x": round(lon, 7), "y": round(lat, 7), "radius": radius,
                  "max_results": 100, "geometry": "false", "with_field_names": "true"}
        data = self.http.request_json("GET", self.QUERY, params, cache_days=self.cache_days,
                                      headers={"Authorization": f"key {self.key}"})
        feats = (((data.get("vectorQuery") or {}).get("layers") or {}).get(str(self.layers["parcels"])) or {}).get("features", [])
        total, found = 0.0, set()
        for f in feats:
            pr = f["properties"]
            app = norm_appellation(pr.get("appellation"))
            if app not in own:
                continue
            ptitles = {t.strip().upper() for t in (pr.get("titles") or "").split(",") if t.strip()}
            if refs and ptitles and not (refs & ptitles):
                continue
            if app in found and not refs:
                continue
            found.add(app)
            total += float(pr.get("survey_area") or pr.get("calc_area") or 0)
        if found != own:
            return Fact(None, UNVERIFIED, "LINZ Primary Parcels",
                        f"could not find all owned lots ({', '.join(sorted(own - found))}); council area includes shared lots")
        return Fact(round(total, 1), VERIFIED, "LINZ Primary Parcels (owned lots only, excluding shared lots)",
                    f"{' + '.join(sorted(own))}")

    @staticmethod
    def _polys(geom: dict) -> list[list]:
        if not geom:
            return []
        if geom["type"] == "Polygon":
            return [geom["coordinates"]]
        if geom["type"] == "MultiPolygon":
            return geom["coordinates"]
        return []


_LOT = re.compile(r"\b(?:(PT|PART)\s+)?(LOT|SEC(?:TION)?|ALLOT(?:MENT)?)\s+(\w+)\s+(DP|SO|LT)\s*(\d+)", re.I)
_SHARE_LOT = re.compile(r"\b\d+\s*/\s*\d+\s*(?:SH|SHARE)\s+(?:IN\s+)?(?:(?:PT|PART)\s+)?(?:LOT|SEC(?:TION)?|ALLOT(?:MENT)?|AU|AREA)\s+\w+\s+(?:DP|SO|LT|UP)\s*\d+", re.I)


def norm_appellation(a: str | None) -> str:
    m = _LOT.search(a or "")
    if not m:
        return ""
    kind = {"SEC": "SEC", "SECTION": "SEC", "ALLOT": "ALLOT", "ALLOTMENT": "ALLOT"}.get(m.group(2).upper(), "LOT")
    return f"{'PT ' if m.group(1) else ''}{kind} {m.group(3).upper()} {m.group(4).upper()} {m.group(5)}"


def own_lots(description: str) -> set[str]:
    """Lots held outright (shares in other lots removed)."""
    d = _SHARE_LOT.sub(" ", description or "")
    return {norm_appellation(m.group(0)) for m in _LOT.finditer(d)}


class Sa2:
    """Stats NZ SA2 2018 boundaries. Tenancy Services bond data Location Ids match SA2 2018 codes
    (1623 of 1627 ids; the 4 extra are later splits)."""

    def __init__(self, http: HttpClient, cfg: dict):
        self.http = http
        self.url = cfg["sources"]["sa2_layer"]
        self.cache_days = cfg["http"]["cache_days"]["sa2"]

    def neighbours(self, x: float, y: float, radius_m: float) -> list[str]:
        params = {"f": "json", "geometry": f"{x},{y}", "geometryType": "esriGeometryPoint", "inSR": 2193,
                  "spatialRel": "esriSpatialRelIntersects", "distance": radius_m, "units": "esriSRUnit_Meter",
                  "outFields": "SA22018_V1_00", "returnGeometry": "false"}
        data = self.http.request_json("POST", self.url + "/query", params, cache_days=self.cache_days)
        return [str(f["attributes"]["SA22018_V1_00"]) for f in data.get("features", [])]

    def lookup(self, x: float, y: float) -> Fact:
        params = {"f": "json", "geometry": f"{x},{y}", "geometryType": "esriGeometryPoint", "inSR": 2193,
                  "spatialRel": "esriSpatialRelIntersects", "outFields": "SA22018_V1_00,SA22018_V1_NAME",
                  "returnGeometry": "false"}
        try:
            data = self.http.request_json("POST", self.url + "/query", params, cache_days=self.cache_days)
        except LookupFailed as e:
            return Fact(None, UNVERIFIED, "Stats NZ SA2 2018", str(e))
        feats = data.get("features", [])
        if not feats:
            return Fact(None, UNVERIFIED, "Stats NZ SA2 2018", "point not in any SA2")
        a = feats[0]["attributes"]
        return Fact({"code": str(a["SA22018_V1_00"]), "name": a["SA22018_V1_NAME"]}, VERIFIED, "Stats NZ SA2 2018")
