# -*- coding: utf-8 -*-
"""Сколько займёт экскурсия, хватит ли одного дня и как разбить её по дням. Без сети."""

import datetime
import math

from . import geo, osm, tsp

STOP_MINUTES = 25            # осмотр объекта на природе
SPEED_KMH = {"nature": 3.2, "urban": 4.2}     # для быстрой оценки до прокладки маршрута
DETOUR = {"nature": 1.3, "urban": 1.25}       # тропы и улицы длиннее прямой
DRIVE_KMH = 55.0
DAY_LIMIT_H = 10.0           # больше этого за день (с дорогой) — советуем ночлег


def visit_hours(poi, mode):
    if poi.category == "start":
        return 0.0
    if mode == "urban":
        own = osm.VISIT_MINUTES.get(poi.category, 20)
        extra = sum(osm.VISIT_MINUTES.get(m.category, 20) // 2 for m in poi.members)
    else:
        own, extra = STOP_MINUTES, 10 * len(poi.members)    # соседние кратеры, пещеры и т. п.
    return (own + extra) / 60.0


def daylight(tour):
    date = datetime.date.fromisoformat(tour.date) if tour.date else datetime.date.today()
    return geo.daylight_hours(tour.place.lat, date.timetuple().tm_yday)


def day_budget(tour):
    """Сколько часов можно гулять за день: световой день без часа запаса, но не больше 10 ч."""
    return max(4.0, min(daylight(tour) - 1.0, DAY_LIMIT_H))


def plan_days(tour):
    """По готовым участкам: tour.days, tour.day_hours, tour.overnight."""
    drive_h = tour.drive.duration_s / 3600.0 if tour.drive and tour.drive.ok else 0.0
    budget = day_budget(tour)
    days, current, hours, km = [], [], 0.0, 0.0
    first_budget = max(3.0, budget - drive_h)        # в первый день ещё дорога туда
    for leg in tour.legs:
        stop = tour.stops[leg.b]
        cost = leg.hours + (visit_hours(stop, tour.mode) if leg.b else 0.0)
        limit = first_budget if not days else budget
        if current and hours + cost > limit:
            days.append({"stops": current, "hours": hours, "km": km})
            current, hours, km = [], 0.0, 0.0
        hours += cost
        km += leg.length_m / 1000.0
        if leg.b:
            current.append(stop.order)
    if current or hours:
        days.append({"stops": current, "hours": hours, "km": km})
    for number, day in enumerate(days, 1):
        day["number"] = number
    tour.days = days
    tour.daylight = daylight(tour)
    total = sum(d["hours"] for d in days)
    tour.day_hours = total + 2 * drive_h
    tour.overnight = len(days) > 1 or tour.day_hours > min(tour.daylight + 1.5, 13.0)
    return days


def estimate(tour, chosen_uids, loop=True, start=None, fast=False):
    """Быстрая оценка до прокладки маршрута: по прямым, с поправкой на извилистость.

    → {walk_km, walk_h, visit_h, drive_h, total_h, daylight, overnight, days}
    """
    mode = tour.mode
    main = tour.candidates[0]
    chosen = set(chosen_uids)
    points = [main] + [p for p in tour.candidates[1:] if p.uid in chosen]
    dist = [[geo.dist_m(a.point, b.point) for b in points] for a in points]
    if len(points) < 2:
        order = [0]
    elif fast:   # для перебора вариантов — эвристика, точное решение тут слишком медленное
        order = tsp.two_opt(tsp.nearest_neighbour(dist), dist, loop)
    else:
        order = tsp.solve(dist, loop)
    walk_m = tsp.tour_cost(order, dist, loop) * DETOUR[mode]
    walk_h = walk_m / 1000.0 / SPEED_KMH[mode]
    visit_h = sum(visit_hours(p, mode) for p in points)
    drive_h = 0.0
    if start is None:
        town, _ = osm.pick_start(tour.capital, tour.settlements, main.point)
        start = town.point if town else None
    if start is not None:
        km = geo.dist_m(start, main.point) / 1000.0
        if not (mode == "urban" and km < 30):
            drive_h = km * 1.35 / DRIVE_KMH
    light = daylight(tour)
    budget = day_budget(tour)
    days = max(1, math.ceil((walk_h + visit_h + drive_h) / budget))
    total = walk_h + visit_h + 2 * drive_h
    return {"walk_km": walk_m / 1000.0, "walk_h": walk_h, "visit_h": visit_h, "drive_h": drive_h,
            "total_h": total, "daylight": light, "days": days,
            "overnight": walk_h + visit_h > budget or total > min(light + 1.5, 13.0)}


# --- Варианты маршрута ---

NOT_AUTO = ("camp", "picnic", "hut")
VARIANTS = [("half", "Полдня", "короткий"), ("day", "Полный день", "на весь день"),
            ("two", "2 дня с ночёвкой", "с ночлегом")]


def variant_budgets(tour, drive_h):
    """Часы на прогулку с осмотром для каждого варианта."""
    budget = day_budget(tour)
    return {"half": 4.5, "day": max(3.0, budget - drive_h), "two": max(6.0, 2 * budget - drive_h)}


def variants(tour, loop=True, limit=20):
    """Три варианта: самые популярные объекты, пока прогулка помещается во время варианта.

    → [{key, title, note, uids, estimate}]
    """
    pool = [p for p in tour.candidates[1:] if p.category not in NOT_AUTO][:limit * 2]
    first = estimate(tour, [], loop)
    budgets = variant_budgets(tour, first["drive_h"])
    result = []
    for key, title, note in VARIANTS:
        chosen, spent = [], estimate(tour, [], loop, fast=True)
        spent = spent["walk_h"] + spent["visit_h"]
        left = list(pool)
        while left and len(chosen) < limit:
            # берём объект, дающий больше всего «популярности» на час добавленного пути
            best = None
            for poi in left:
                trial = estimate(tour, chosen + [poi.uid], loop, fast=True)
                hours = trial["walk_h"] + trial["visit_h"]
                if hours > budgets[key]:
                    continue
                value = (poi.rank + 10.0) / (hours - spent + 0.25)   # +10 — ценность самого визита
                if best is None or value > best[0]:
                    best = (value, poi, hours)
            if best is None:
                break
            chosen.append(best[1].uid)
            spent = best[2]
            left.remove(best[1])
        result.append({"key": key, "title": title, "note": note, "uids": chosen,
                       "estimate": estimate(tour, chosen, loop), "budget": budgets[key]})
    return result
