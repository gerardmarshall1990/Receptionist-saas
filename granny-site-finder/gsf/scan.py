"""Whole-of-Auckland site scan: every parcel that physically fits house + 2 units, for sale or not.

Stages (each cached under data/scan/, so the scan resumes after interruption):
  1 parcels   all current standalone council properties >~565 m2 (centroid + legal description)
  2 zone      centroid in an allowed (MHS/MHU) or consent (Mixed Use/THAB) operative zone
  3 title     drop council descriptions that show cross-lease / unit title
  4 area      exact NZTM boundary, true area >= min_land_m2
  5 hazards   flood plain / flood-prone / heritage / SEA / mana whenua on the likely building area
              (layers downloaded once, tested locally with Shapely)
  6 olfp      overland flow path on the likely building area (live query, survivors only)
  7 linz      LINZ title type at the parcel (vector query); only Freehold survives
Output: data/scan/auckland_sites.csv (+ .xlsx)

Needs the optional dependency `shapely` (pip install shapely). Usage:
  LINZ_API_KEY=... python -m gsf.scan [--limit N] [--workers 8]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from shapely.geometry import Point, Polygon, shape
from shapely.strtree import STRtree

from . import geometry as geo
from .config import load_config, load_env
from .titles import INDICATED, title_fact_from_council, title_fact_from_linz

S = requests.Session()
S.headers["User-Agent"] = "granny-site-finder/scan"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def q(url: str, params: dict, tries: int = 5) -> dict:
    for i in range(tries):
        try:
            r = S.post(url + "/query", data={"f": "json", **params}, timeout=120)
            d = r.json()
            if "error" in d:
                raise RuntimeError(d["error"])
            return d
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * 2 ** i)
    return {}


def paged(url: str, params: dict, page: int = 2000, workers: int = 6, label: str = "") -> list[dict]:
    n = q(url, {**params, "returnCountOnly": "true"})["count"]
    offs = list(range(0, n, page))
    out: list[dict] = []

    def one(o):
        return q(url, {**params, "orderByFields": "OBJECTID", "resultOffset": o, "resultRecordCount": page})["features"]
    with ThreadPoolExecutor(workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(one, o) for o in offs])):
            out.extend(f.result())
            if i % 20 == 0:
                log(f"  {label}: {len(out):,}/{n:,}")
    return out


def cached(path: str, fn):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    data = fn()
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)
    return data


def esri_poly(rings) -> Polygon | None:
    try:
        outer = geo.outer_ring(rings)
        holes = [r for r in rings if r is not outer]
        p = Polygon(outer, holes)
        return p if p.is_valid else p.buffer(0)
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process the first N zone-qualifying parcels (testing)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    cfg = load_config()
    g = cfg["gates"]
    base = cfg["sources"]["council_base"]
    L = {k: f"{base}/{v}/FeatureServer/0" for k, v in cfg["sources"]["council_layers"].items()}
    out_dir = os.path.join(os.path.dirname(cfg["storage"]["db_path"]), "scan")
    os.makedirs(out_dir, exist_ok=True)
    P = lambda name: os.path.join(out_dir, name)  # noqa: E731

    # 1 parcels ---------------------------------------------------------------
    log("stage 1: parcels")
    parcels = cached(P("1_parcels.json"), lambda: [
        {**f["attributes"], "x": f["centroid"]["x"], "y": f["centroid"]["y"]}
        for f in paged(L["property"], {
            "where": "SITESTATUS='Current' AND SITETYPE='Standard' AND PARENTPROPERTYID IS NULL AND Shape__Area>=880",
            "outFields": "OBJECTID,PROPERTYID,FORMATTEDADDRESS,PROPERTYDESCRIPTION,FORMATTEDTITLES",
            "returnGeometry": "false", "returnCentroid": "true", "outSR": 2193}, label="parcels")
        if f.get("centroid")])
    log(f"  {len(parcels):,} parcels")

    # 2 zone ------------------------------------------------------------------
    log("stage 2: zones")
    zone_names = {18: "Residential - Mixed Housing Suburban Zone", 60: "Residential - Mixed Housing Urban Zone",
                  12: "Business - Mixed Use Zone", 8: "Residential - Terrace Housing and Apartment Building Zone"}
    allowed = set(g["allowed_zones"]) | set((g.get("consent_zones") or {}).keys())
    codes = [c for c, n in zone_names.items() if n in allowed]
    zones = cached(P("2_zones.json"), lambda: [
        {"zone": f["attributes"]["ZONE"], "rings": f["geometry"]["rings"]}
        for f in paged(L["zone"], {"where": f"VERSIONSTATUS=4 AND ZONE IN ({','.join(map(str, codes))})",
                                   "outFields": "OBJECTID,ZONE", "returnGeometry": "true", "outSR": 2193,
                                   "geometryPrecision": 1}, page=500, label="zones")
        if f.get("geometry")])
    zpolys, zcodes = [], []
    for z in zones:
        p = esri_poly(z["rings"])
        if p is not None and not p.is_empty:
            zpolys.append(p)
            zcodes.append(z["zone"])
    ztree = STRtree(zpolys)
    zoned = []
    for pc in parcels:
        pt = Point(pc["x"], pc["y"])
        for i in ztree.query(pt, predicate="within"):
            pc["zone"] = zone_names[zcodes[i]]
            zoned.append(pc)
            break
    log(f"  {len(zoned):,} in {', '.join(zone_names[c].split(' - ')[-1] for c in codes)}")

    # 3 title prefilter -------------------------------------------------------
    t3 = []
    for pc in zoned:
        f = title_fact_from_council(pc.get("PROPERTYDESCRIPTION"))
        if f.status == INDICATED:
            continue
        t3.append(pc)
    log(f"stage 3: {len(t3):,} after removing council-indicated cross-lease/unit titles")
    if a.limit:
        t3 = t3[: a.limit]

    # 4 exact area ------------------------------------------------------------
    log("stage 4: exact boundaries")

    def geoms():
        ids = [pc["OBJECTID"] for pc in t3]
        res = {}

        def batch(chunk):
            d = q(L["property"], {"objectIds": ",".join(map(str, chunk)), "outFields": "OBJECTID",
                                  "returnGeometry": "true", "outSR": 2193, "geometryPrecision": 2})
            return {f["attributes"]["OBJECTID"]: f["geometry"]["rings"] for f in d["features"] if f.get("geometry")}
        chunks = [ids[i:i + 200] for i in range(0, len(ids), 200)]
        with ThreadPoolExecutor(a.workers) as ex:
            for i, f in enumerate(as_completed([ex.submit(batch, c) for c in chunks])):
                res.update({str(k): v for k, v in f.result().items()})
                if i % 50 == 0:
                    log(f"  boundaries {len(res):,}/{len(ids):,}")
        return res
    rings_by_id = cached(P(f"4_geoms{'_lim' if a.limit else ''}.json"), geoms)
    t4 = []
    for pc in t3:
        r = rings_by_id.get(str(pc["OBJECTID"]))
        if not r:
            continue
        area = geo.polygon_area(r)
        if area >= g["min_land_m2"]:
            pc["area"] = round(area, 1)
            pc["rings"] = r
            t4.append(pc)
    log(f"  {len(t4):,} with true area >= {g['min_land_m2']} m2")

    # 5 hazards (bulk) --------------------------------------------------------
    log("stage 5: hazard layers")
    hz_layers = {"flood_plain": ("1=1", 300), "flood_prone": ("1=1", 1000),
                 "heritage_extent": ("1=1", 500), "sea": ("TYPE IN (1,4)", 300), "mana_whenua": ("1=1", 200)}
    trees = {}
    for key, (where, page) in hz_layers.items():
        feats = cached(P(f"5_{key}.json"), lambda key=key, where=where, page=page: [
            f["geometry"]["rings"] for f in paged(L[key], {"where": where, "outFields": "OBJECTID", "returnGeometry": "true",
                                                           "outSR": 2193, "geometryPrecision": 1,
                                                           "maxAllowableOffset": 0.5}, page=page, label=key)
            if f.get("geometry") and f["geometry"].get("rings")])
        polys = [p for p in (esri_poly(r) for r in feats) if p is not None and not p.is_empty]
        trees[key] = (STRtree(polys), polys)
        log(f"  {key}: {len(polys):,} polygons")
    t5 = []
    for pc in t4:
        parcel = esri_poly(pc["rings"])
        core = esri_poly(geo.scaled(pc["rings"], g["building_core_scale"]))
        if parcel is None or core is None:
            continue
        fail, partial = [], []
        for key, (tree, polys) in trees.items():
            if len(tree.query(core, predicate="intersects")):
                fail.append(key)
            elif key.startswith("flood") and len(tree.query(parcel, predicate="intersects")):
                partial.append(key)
        if fail:
            continue
        pc["flags"] = [f"{k} touches parcel edge" for k in partial]
        pc["core_rings"] = [list(core.exterior.coords)]
        t5.append(pc)
    log(f"  {len(t5):,} clear of flood plain/flood-prone/heritage/SEA/mana whenua on the building area")

    # 6 OLFP (live, survivors only) -------------------------------------------
    log("stage 6: overland flow paths")
    minc = int(g.get("overland_flow_min_catchment_group", 1))

    def olfp(pc):
        geom = json.dumps({"rings": pc["core_rings"], "spatialReference": {"wkid": 2193}})
        d = q(L["overland_flow_path"], {"geometry": geom, "geometryType": "esriGeometryPolygon", "inSR": 2193,
                                        "spatialRel": "esriSpatialRelIntersects",
                                        "where": f"CatchmentAreaGroup>={minc}", "returnCountOnly": "true"})
        return pc["OBJECTID"], d.get("count", 0)
    olfp_hits = cached(P(f"6_olfp{'_lim' if a.limit else ''}.json"), lambda: dict(
        (str(k), v) for k, v in _pool(olfp, t5, a.workers, "olfp")))
    t6 = [pc for pc in t5 if not olfp_hits.get(str(pc["OBJECTID"]))]
    log(f"  {len(t6):,} with no overland flow path on the building area")

    # 7 LINZ titles -----------------------------------------------------------
    log("stage 7: LINZ titles")
    key = os.environ.get("LINZ_API_KEY", "")
    lon_lat = cached(P(f"7_lonlat{'_lim' if a.limit else ''}.json"), lambda: _lonlat(L["property"], [pc["OBJECTID"] for pc in t6], a.workers))

    # 7a shared-lot sites: council boundary includes common lots -> use LINZ owned-lot area
    from .lookups.http import HttpClient
    from .lookups.linz import Linz
    lz = Linz(HttpClient(os.path.join(out_dir, "linz_cache.sqlite"), timeout_s=60, min_interval_s=0.05), cfg)

    def lot_area(pc):
        ll = lon_lat.get(str(pc["OBJECTID"]))
        if not ll:
            return pc["OBJECTID"], None
        f = lz.lot_area(ll[0], ll[1], pc.get("PROPERTYDESCRIPTION"), pc.get("FORMATTEDTITLES"), pc["area"])
        return pc["OBJECTID"], None if f is None else {"value": f.value, "status": f.status}
    shared = [pc for pc in t6 if re.search(r"\bSH(ARE)?\b", pc.get("PROPERTYDESCRIPTION") or "", re.I)]
    lots = cached(P(f"7a_lotarea{'_lim' if a.limit else ''}.json"), lambda: dict(
        (str(k), v) for k, v in _pool(lot_area, shared, max(2, a.workers // 2), "lot area")))
    kept = []
    for pc in t6:
        la = lots.get(str(pc["OBJECTID"]))
        if la and la["status"] == "verified":
            pc["council_area"] = pc["area"]
            pc["area"] = la["value"]
            if pc["area"] < g["min_land_m2"]:
                continue
        elif la:
            pc.setdefault("flags", []).append("land area unverified (shared lots)")
        kept.append(pc)
    log(f"  {len(shared):,} sites with shares in common lots re-measured; {len(kept):,} remain >= {g['min_land_m2']} m2")
    t6 = kept

    _write(P("auckland_sites_pre_linz.csv"), t6, g, lon_lat, title_default="unverified (LINZ pending)")
    log(f"  interim list written: {len(t6):,} sites (titles pending)")

    def linz(pc):
        ll = lon_lat.get(str(pc["OBJECTID"]))
        if not ll or not key:
            return pc["OBJECTID"], None
        tx, ty = ll
        for attempt in range(6):
            try:
                r = S.get("https://data.linz.govt.nz/services/query/v1/vector.json",
                          params={"layer": 50804, "x": tx, "y": ty, "radius": 0, "max_results": 20, "geometry": "false",
                                  "with_field_names": "true"}, headers={"Authorization": f"key {key}"}, timeout=60)
            except requests.RequestException:
                time.sleep(2 * 2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After") or 2 * 2 ** attempt))
                continue
            break
        if r.status_code != 200:
            return pc["OBJECTID"], {"error": r.status_code}
        feats = (((r.json().get("vectorQuery") or {}).get("layers") or {}).get("50804") or {}).get("features", [])
        rows = [f["properties"] for f in feats if (f["properties"].get("status") or "LIVE").upper() == "LIVE"]
        fact = title_fact_from_linz(rows)
        return pc["OBJECTID"], {"value": fact.value, "status": fact.status, "note": fact.note[:200]}
    titles = cached(P(f"7_titles{'_lim' if a.limit else ''}.json"), lambda: dict(
        (str(k), v) for k, v in _pool(linz, t6, a.workers, "linz")))
    final = []
    for pc in t6:
        t = titles.get(str(pc["OBJECTID"])) or {}
        pc["title"] = t.get("value") or "unverified"
        pc["title_note"] = t.get("note", "")
        if pc["title"] in ("Freehold", "unverified"):
            pc["lon"], pc["lat"] = (lon_lat.get(str(pc["OBJECTID"])) or (None, None))
            final.append(pc)
    log(f"  {len(final):,} freehold (or unverified) after LINZ")

    # output ------------------------------------------------------------------
    fields = ["address", "zone", "zone_status", "land_m2", "title", "legal_description", "title_refs", "flags",
              "property_id", "lat", "lon", "map"]
    path = P("auckland_sites.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for pc in sorted(final, key=lambda p: (p["zone"] not in g["allowed_zones"], -p["area"])):
            w.writerow({"address": pc["FORMATTEDADDRESS"], "zone": pc["zone"],
                        "zone_status": "pass" if pc["zone"] in g["allowed_zones"] else "check (consent)",
                        "land_m2": pc["area"], "title": pc["title"], "legal_description": pc.get("PROPERTYDESCRIPTION"),
                        "title_refs": pc.get("FORMATTEDTITLES"), "flags": "; ".join(pc.get("flags", [])),
                        "property_id": pc.get("PROPERTYID"), "lat": pc.get("lat"), "lon": pc.get("lon"),
                        "map": f"https://www.google.com/maps?q={pc.get('lat')},{pc.get('lon')}" if pc.get("lat") else ""})
    summary = {"parcels": len(parcels), "zoned": len(zoned), "title_prefilter": len(t3), "area": len(t4),
               "hazards": len(t5), "olfp": len(t6), "final": len(final)}
    with open(P("summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    log("done", summary, "->", path)
    return 0


def _write(path, sites, g, lon_lat, title_default=None):
    fields = ["address", "zone", "zone_status", "land_m2", "title", "legal_description", "title_refs", "flags",
              "property_id", "lat", "lon", "map"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for pc in sorted(sites, key=lambda p: (p["zone"] not in g["allowed_zones"], -p["area"])):
            lon, lat = lon_lat.get(str(pc["OBJECTID"])) or (None, None)
            w.writerow({"address": pc["FORMATTEDADDRESS"], "zone": pc["zone"],
                        "zone_status": "pass" if pc["zone"] in g["allowed_zones"] else "check (consent)",
                        "land_m2": pc["area"], "title": pc.get("title") or title_default,
                        "legal_description": pc.get("PROPERTYDESCRIPTION"), "title_refs": pc.get("FORMATTEDTITLES"),
                        "flags": "; ".join(pc.get("flags", [])), "property_id": pc.get("PROPERTYID"),
                        "lat": lat, "lon": lon, "map": f"https://www.google.com/maps?q={lat},{lon}" if lat else ""})


def _pool(fn, items, workers, label):
    out = []
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for i, f in enumerate(as_completed(futs)):
            try:
                out.append(f.result())
            except Exception as e:  # noqa: BLE001
                log(f"  {label} error: {e}")
            if i % 500 == 0:
                log(f"  {label} {i:,}/{len(items):,}")
    return out


def _lonlat(url, ids, workers):
    res = {}

    def batch(chunk):
        d = q(url, {"objectIds": ",".join(map(str, chunk)), "outFields": "OBJECTID", "returnGeometry": "false",
                    "returnCentroid": "true", "outSR": 4326})
        return {str(f["attributes"]["OBJECTID"]): (round(f["centroid"]["x"], 7), round(f["centroid"]["y"], 7))
                for f in d["features"] if f.get("centroid")}
    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(batch, ids[i:i + 500]) for i in range(0, len(ids), 500)]):
            res.update(f.result())
    return res


if __name__ == "__main__":
    sys.exit(main())
