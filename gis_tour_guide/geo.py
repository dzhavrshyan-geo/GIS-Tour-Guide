# -*- coding: utf-8 -*-
"""Геометрия на сфере, тайлы Web Mercator, скорость пешехода. Без QGIS.

Координаты везде — пары (lon, lat), как в GeoJSON, если не сказано иное.
"""

import math

EARTH_R = 6371008.8
M_PER_DEG_LAT = 110574.0


def haversine_m(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def dist_m(a, b):
    return haversine_m(a[0], a[1], b[0], b[1])


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def bbox_around(lon, lat, half_km):
    """Квадрат ±half_km от точки: (west, south, east, north)."""
    dlat = half_km * 1000.0 / M_PER_DEG_LAT
    dlon = half_km * 1000.0 / max(m_per_deg_lon(lat), 1.0)
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def bbox_of(points, margin_m=0.0):
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    west, south, east, north = min(lons), min(lats), max(lons), max(lats)
    if margin_m:
        mid = (south + north) / 2
        dlat = margin_m / M_PER_DEG_LAT
        dlon = margin_m / max(m_per_deg_lon(mid), 1.0)
        west, south, east, north = west - dlon, south - dlat, east + dlon, north + dlat
    return (west, south, east, north)


def bbox_union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def in_bbox(lon, lat, bbox):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def line_length_m(coords):
    return sum(dist_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def densify(coords, step_m):
    """Точки через ~step_m метров (концы исходных отрезков сохраняются)."""
    if len(coords) < 2:
        return list(coords)
    out = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        d = dist_m(a, b)
        n = max(1, int(math.ceil(d / step_m)))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def simplify(coords, tol_m):
    """Дуглас — Пекер в локальной равнопромежуточной проекции."""
    if len(coords) < 3:
        return list(coords)
    lat0 = coords[0][1]
    kx, ky = m_per_deg_lon(lat0), M_PER_DEG_LAT
    pts = [(c[0] * kx, c[1] * ky) for c in coords]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, best_k = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, best_k = d, k
        if best > tol_m:
            keep[best_k] = True
            stack.append((i, best_k))
            stack.append((best_k, j))
    return [c for c, k in zip(coords, keep) if k]


def tobler_kmh(slope):
    """Скорость пешехода по тропе (км/ч) при уклоне dh/dx — функция Тоблера."""
    return 6.0 * math.exp(-3.5 * abs(slope + 0.05))


# --- Тайлы Web Mercator ---

def lonlat_to_tile(lon, lat, z):
    """Дробные координаты тайла (x, y) на уровне z."""
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x, y, z):
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


# --- Солнце ---

def daylight_hours(lat, day_of_year):
    """Продолжительность светового дня (ч) — приближённая формула."""
    decl = math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))
    # учёт рефракции и диска солнца: −0.83°
    x = (math.sin(math.radians(-0.83)) - math.sin(math.radians(lat)) * math.sin(decl)) / (
        math.cos(math.radians(lat)) * math.cos(decl))
    if x <= -1:
        return 24.0
    if x >= 1:
        return 0.0
    return 2 * math.degrees(math.acos(x)) / 15.0
