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

    @staticmethod
    def _polys(geom: dict) -> list[list]:
        if not geom:
            return []
        if geom["type"] == "Polygon":
            return [geom["coordinates"]]
        if geom["type"] == "MultiPolygon":
            return geom["coordinates"]
        return []


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
