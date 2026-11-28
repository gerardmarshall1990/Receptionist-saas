"""Small planar-geometry helpers for NZTM (EPSG:2193, metres). Pure Python, no GIS deps.

Polygons are Esri JSON style: a list of rings, each ring a list of [x, y] (closed or not).
"""
from __future__ import annotations

import math

Ring = list[list[float]]


def _closed(r: Ring) -> Ring:
    return r if r and r[0] == r[-1] else r + [r[0]]


def ring_signed_area(r: Ring) -> float:
    r = _closed(r)
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:])) / 2


def polygon_area(rings: list[Ring]) -> float:
    """Area of an Esri polygon. Esri outer rings are clockwise (negative signed area) and holes
    counter-clockwise, so area = -(sum of signed areas). Falls back to abs() for odd winding."""
    total = -sum(ring_signed_area(r) for r in rings)
    if total <= 0:
        total = abs(sum(abs(ring_signed_area(r)) * (1 if i == 0 else -1) for i, r in enumerate(rings)))
    return total


def outer_ring(rings: list[Ring]) -> Ring:
    return max(rings, key=lambda r: abs(ring_signed_area(r)))


def centroid(r: Ring) -> tuple[float, float]:
    r = _closed(r)
    # Work relative to the first vertex: avoids catastrophic cancellation for small polygons far
    # from the origin (e.g. a 20 m parcel in lon/lat degrees around 174, -36).
    ox, oy = r[0][0], r[0][1]
    rr = [[x - ox, y - oy] for x, y in r]
    a = ring_signed_area(rr)
    if abs(a) < 1e-18:
        xs, ys = zip(*r)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(rr, rr[1:]):
        c = x1 * y2 - x2 * y1
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    return ox + cx / (6 * a), oy + cy / (6 * a)


def point_in_ring(x: float, y: float, r: Ring) -> bool:
    r = _closed(r)
    inside = False
    for (x1, y1), (x2, y2) in zip(r, r[1:]):
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def point_in_polygon(x: float, y: float, rings: list[Ring]) -> bool:
    return sum(point_in_ring(x, y, r) for r in rings) % 2 == 1


def interior_point(rings: list[Ring]) -> tuple[float, float]:
    """A point guaranteed inside the polygon (centroid if it is inside, else a scanline midpoint)."""
    r = outer_ring(rings)
    cx, cy = centroid(r)
    if point_in_polygon(cx, cy, rings):
        return cx, cy
    rc = _closed(r)
    xs = sorted(x1 + (cy - y1) * (x2 - x1) / (y2 - y1)
                for (x1, y1), (x2, y2) in zip(rc, rc[1:]) if (y1 > cy) != (y2 > cy))
    best = max(((xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)), key=lambda p: p[1] - p[0], default=None)
    if best:
        return (best[0] + best[1]) / 2, cy
    return cx, cy


def scaled(rings: list[Ring], factor: float) -> list[Ring]:
    """Shrink the outer ring towards an interior point. Used as the 'likely building area'."""
    r = outer_ring(rings)
    px, py = interior_point(rings)
    return [[[px + (x - px) * factor, py + (y - py) * factor] for x, y in _closed(r)]]


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(map(tuple, points)))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_rect_dims(rings: list[Ring]) -> tuple[float, float]:
    """(width, depth) of the minimum-area bounding rectangle; width <= depth."""
    hull = convex_hull([tuple(p) for p in outer_ring(rings)])
    if len(hull) < 3:
        return 0.0, 0.0
    best = None
    for i in range(len(hull)):
        (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % len(hull)]
        ang = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(-ang), math.sin(-ang)
        xs = [x * c - y * s for x, y in hull]
        ys = [x * s + y * c for x, y in hull]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if best is None or w * h < best[0] * best[1]:
            best = (w, h)
    return tuple(sorted(best))  # type: ignore[return-value]


def to_geojson(rings: list[Ring]) -> dict:
    return {"type": "Polygon", "coordinates": [_closed(r) for r in rings]}
