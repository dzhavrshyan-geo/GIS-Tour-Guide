# -*- coding: utf-8 -*-
"""Маршруты OSRM (серверы FOSSGIS: машина и пешеход) и сводка по дорогам."""

from . import geo
from .model import DriveRoute, Segment
from .net import NetError

CAR = "https://routing.openstreetmap.de/routed-car"
FOOT = "https://routing.openstreetmap.de/routed-foot"

# Дальше этого от дорог и троп точка считается «в стороне» — туда идём по рельефу
SNAP_LIMIT_M = 150


def coords_param(points):
    return ";".join("%.6f,%.6f" % (p[0], p[1]) for p in points)


def osrm_route(http, base, points, steps=False):
    url = "%s/route/v1/driving/%s" % (base, coords_param(points))
    params = {"overview": "full", "geometries": "geojson", "steps": "true" if steps else "false"}
    data = http.json(url, params, ttl=14 * 86400)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return data


def osrm_table(http, base, points):
    """(матрица расстояний, м; расстояния привязки к сети, м) или (None, None)."""
    url = "%s/table/v1/driving/%s" % (base, coords_param(points))
    try:
        data = http.json(url, {"annotations": "distance"}, ttl=14 * 86400)
    except NetError:
        return None, None
    if data.get("code") != "Ok" or not data.get("distances"):
        return None, None
    snaps = [w.get("distance", 0) or 0 for w in data.get("sources", [])]
    return data["distances"], snaps


def road_label(step):
    ref = (step.get("ref") or "").strip()
    name = (step.get("name") or "").strip()
    if ref and name and name != ref:
        return "%s (%s)" % (ref.replace(";", ", "), name)
    return ref.replace(";", ", ") or name


def road_summary(steps, min_m=500):
    """Шаги OSRM → [(дорога, метры)]: соседние шаги по одной дороге склеены."""
    roads = []
    for step in steps:
        distance = step.get("distance", 0)
        if distance <= 0:
            continue
        label = road_label(step) or "местные дороги без названия"
        if roads and roads[-1][0] == label:
            roads[-1][1] += distance
        else:
            roads.append([label, distance])
    # короткие куски (развязки, съезды) — к соседу
    merged = []
    for label, distance in roads:
        if merged and (distance < min_m or merged[-1][0] == label):
            merged[-1][1] += distance
        else:
            merged.append([label, distance])
    if len(merged) > 1 and merged[0][1] < min_m:
        merged[1][1] += merged[0][1]
        merged.pop(0)
    return [(label, distance) for label, distance in merged]


def drive(http, start, start_name, dest):
    """Маршрут на машине от start до ближайшей к dest проезжей точки."""
    result = DriveRoute(start_name=start_name, start=start)
    try:
        data = osrm_route(http, CAR, [start, dest], steps=True)
    except NetError as error:
        result.note = "Сервис маршрутов недоступен: %s" % error
        return result
    if not data:
        result.note = ("Проезд на машине не найден — вероятно, нужен самолёт, паром "
                       "или вертолёт (см. «Как добраться»).")
        return result
    route = data["routes"][0]
    waypoints = data.get("waypoints") or []
    result.coords = [tuple(c) for c in route["geometry"]["coordinates"]]
    result.distance_m = route.get("distance", 0)
    result.duration_s = route.get("duration", 0)
    steps = [s for leg in route.get("legs", []) for s in leg.get("steps", [])]
    result.roads = road_summary(steps)
    end = waypoints[-1] if waypoints else {}
    result.trailhead = tuple(end.get("location") or (result.coords[-1] if result.coords else dest))
    result.trailhead_name = end.get("name", "")
    result.last_mile_m = geo.dist_m(result.trailhead, dest)
    first = waypoints[0] if waypoints else {}
    if first.get("distance", 0) > 5000:
        result.note = "Начальная точка в %s от дорог." % ("%.0f км" % (first["distance"] / 1000))
    result.ok = True
    return result


def foot_segments(http, a, b, offtrail_path=None, prefer_trails=1.3):
    """Сегменты пешего перехода a→b.

    По дорогам и тропам (OSRM, пешеходный профиль), а концы, лежащие в стороне
    от троп, и переходы, где тропа сильно петляет, — по рельефу: offtrail_path(a, b)
    возвращает (список точек, часы) или None.
    """
    direct = geo.dist_m(a, b)
    trail = None
    try:
        trail = osrm_route(http, FOOT, [a, b])
    except NetError:
        trail = None

    def offtrail(p, q):
        if geo.dist_m(p, q) < 1:
            return []
        if offtrail_path:
            found = offtrail_path(p, q)
            if found:
                return [Segment("offtrail", found[0], geo.line_length_m(found[0]))]
        return [Segment("offtrail", [p, q], geo.dist_m(p, q))]

    if not trail:
        return offtrail(a, b)
    route = trail["routes"][0]
    coords = [tuple(c) for c in route["geometry"]["coordinates"]]
    waypoints = trail.get("waypoints") or [{}, {}]
    snap_a = waypoints[0].get("distance", 0) or 0
    snap_b = waypoints[-1].get("distance", 0) or 0
    trail_m = route.get("distance", geo.line_length_m(coords))

    # оба конца у одной точки сети или тропа — огромный крюк: идём напрямик
    walk_trail = trail_m + snap_a + snap_b
    if trail_m < 30 or (direct < 6000 and walk_trail > 3.0 * direct * prefer_trails):
        return offtrail(a, b)

    segments = []
    if snap_a > SNAP_LIMIT_M:
        segments += offtrail(a, coords[0])
    elif coords and geo.dist_m(a, coords[0]) > 1:
        coords.insert(0, a)
    tail = []
    if snap_b > SNAP_LIMIT_M:
        tail = offtrail(coords[-1], b)
    elif geo.dist_m(coords[-1], b) > 1:
        coords.append(b)
    segments.append(Segment("trail", coords, geo.line_length_m(coords)))
    return segments + tail
