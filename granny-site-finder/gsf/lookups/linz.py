"""LINZ Data Service (WFS) and Stats NZ SA2 lookups.

LINZ WFS requires an API key (HTTP 401 without one). Layer ids and field names were verified on
2026-11-28 via the open LDS metadata API:
  50772 NZ Primary Parcels  (id, appellation, titles, survey_area, calc_area, shape)
  50804 NZ Property Titles  (title_no, status, type, estate_description, shape)
  101290 NZ Building Outlines (building_id, use, shape)
The WFS GetFeature calls below follow the documented Koordinates WFS 2.0 interface but have NOT
been run live (no key was available). Without LINZ_API_KEY, title type stays 'unverified'.
"""
from __future__ import annotations

import os

from .. import geometry as geo
from ..models import UNVERIFIED, VERIFIED, Fact
from ..titles import title_fact_from_linz
from .http import HttpClient, LookupFailed


class Linz:
    def __init__(self, http: HttpClient, cfg: dict):
        self.http = http
        self.base = cfg["sources"]["linz_wfs"]
        self.layers = cfg["sources"]["linz_layers"]
        self.cache_days = cfg["http"]["cache_days"]["linz"]
        self.key = os.environ.get("LINZ_API_KEY", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _features(self, layer_id: int, rings: list) -> list[dict]:
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        # Koordinates accepts the key as "Authorization: key <KEY>"; bbox axis order for EPSG:2193 in WFS 2.0 is N,E.
        url = self.base
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"layer-{layer_id}", "outputFormat": "json", "srsName": "EPSG:2193",
            "bbox": f"{min(ys)},{min(xs)},{max(ys)},{max(xs)},urn:ogc:def:crs:EPSG::2193",
            "count": 200,
        }
        data = self.http.request_json("GET", url, params, cache_days=self.cache_days,
                                      headers={"Authorization": f"key {self.key}"},
                                      cache_url_label=f"linz-wfs-layer-{layer_id}")
        return data.get("features", [])

    @staticmethod
    def _polys(geom: dict) -> list[list]:
        if not geom:
            return []
        if geom["type"] == "Polygon":
            return [geom["coordinates"]]
        if geom["type"] == "MultiPolygon":
            return geom["coordinates"]
        return []

    @staticmethod
    def _xy(rings: list) -> list:
        # GeoJSON from WFS in EPSG:2193 may be (N, E); normalise to (E, N) by magnitude check.
        def fix(p):
            x, y = p[0], p[1]
            return [y, x] if x > 4_000_000 else [x, y]
        return [[fix(p) for p in r] for r in rings]

    def titles(self, rings: list) -> Fact:
        if not self.enabled:
            return Fact(None, UNVERIFIED, "LINZ NZ Property Titles", "LINZ_API_KEY not set; title type not checked")
        px, py = geo.interior_point(rings)
        rows = []
        for f in self._features(self.layers["titles"], rings):
            if any(geo.point_in_polygon(px, py, self._xy(poly)) for poly in self._polys(f.get("geometry"))):
                p = f.get("properties", {})
                if (p.get("status") or "").upper() in ("LIVE", ""):
                    rows.append(p)
        return title_fact_from_linz(rows)

    def buildings(self, rings: list, min_area_m2: float = 30) -> Fact:
        if not self.enabled:
            return Fact(None, UNVERIFIED, "LINZ NZ Building Outlines", "LINZ_API_KEY not set")
        n = 0
        for f in self._features(self.layers["buildings"], rings):
            for poly in self._polys(f.get("geometry")):
                poly = self._xy(poly)
                cx, cy = geo.centroid(poly[0])
                if geo.point_in_polygon(cx, cy, rings) and geo.polygon_area(poly) >= min_area_m2:
                    n += 1
        return Fact(n, VERIFIED, "LINZ NZ Building Outlines", f"buildings >= {min_area_m2} m² with centroid on parcel")


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
