# -*- coding: utf-8 -*-
"""Поездка по региону на несколько дней: порядок объектов, переезды, план по дням, общий трек.

Без QGIS: собирается в фоновой задаче из готовых экскурсий (Tour) по каждому объекту.
"""

import re

from . import geo, schedule, tsp
from .text import esc, fmt_hours, fmt_km

DRIVE_DETOUR = 1.35


def order_bases(start, tours, loop=True):
    """Порядок объектов, при котором переезды короче всего (старт фиксирован)."""
    points = [start] + [t.place.point for t in tours]
    dist = [[geo.dist_m(a, b) * DRIVE_DETOUR for b in points] for a in points]
    order = tsp.solve(dist, loop)
    return [tours[i - 1] for i in order[1:]]


def plan_days(trip):
    """План поездки по дням: переезды, дни экскурсий каждого объекта, ночлег каждую ночь.

    День заканчивается, когда кончается светлое время (schedule.day_budget), — короткая
    экскурсия и переезд к следующему объекту помещаются в один день.
    """
    budget = max(schedule.day_budget(t) for t in trip.tours) if trip.tours else 10.0
    days, current, hours, where = [], [], 0.0, [None]

    def close(on_trail=False):
        nonlocal current, hours
        if current:
            days.append({"number": len(days) + 1, "items": current, "hours": hours, "tour": where[0],
                         "on_trail": on_trail})
        current, hours = [], 0.0

    def add(text_, cost, tour):
        nonlocal hours
        if current and hours + cost > budget:
            close()
        current.append(text_)
        hours += cost
        where[0] = tour

    for index, tour in enumerate(trip.tours):
        drive = trip.drives[index] if index < len(trip.drives) else None
        if drive and drive.ok:
            add("Переезд %s → %s: %s, ~%s" % (drive.start_name, tour.place.name, fmt_km(drive.distance_m),
                                              fmt_hours(drive.duration_s / 3600.0)), drive.duration_s / 3600.0,
                tour)
        for number, day in enumerate(tour.days or [{"stops": [], "hours": 0, "km": 0}]):
            if number:
                close(on_trail=not tour.urban)       # многодневная экскурсия: ночь у маршрута
            names = ", ".join(tour.stops[n].name for n in day["stops"] if 0 < n < len(tour.stops))
            what = ("экскурсия — " + names) if names else "обратный путь к старту"
            add("%s: %s (%s пешком, ~%s)" % (tour.place.name, what, fmt_km(day["km"] * 1000),
                                            fmt_hours(day["hours"])), day["hours"], tour)
    back = trip.drives[len(trip.tours)] if len(trip.drives) > len(trip.tours) else None
    if back and back.ok:
        add("Возвращение в %s: %s, ~%s" % (trip.start_name, fmt_km(back.distance_m),
                                          fmt_hours(back.duration_s / 3600.0)), back.duration_s / 3600.0,
            where[0])
    close()
    for day in days:
        tour, on_trail = day.pop("tour"), day.pop("on_trail")
        if day is days[-1]:
            break
        lodging = tour.lodging if tour is not None else []
        item = lodging[min(1, len(lodging) - 1)] if lodging else None
        if on_trail:
            day["items"].append("Ночлег: в палатке у маршрута" + (
                " или «%s» (%s от объекта)" % (item["name"], fmt_km(item["km"] * 1000)) if item else ""))
        elif item:
            day["items"].append("Ночлег: %s (%s, %s от объекта) — другие варианты в отчёте объекта" % (
                item["name"], item["kind"], fmt_km(item["km"] * 1000)))
    trip.days = days
    return days


def totals(trip):
    drive_m = sum(d.distance_m for d in trip.drives if d and d.ok)
    drive_h = sum(d.duration_s for d in trip.drives if d and d.ok) / 3600.0
    walk_m = sum(t.totals.get("walk_m", 0) for t in trip.tours)
    return {"drive_m": drive_m, "drive_h": drive_h, "walk_m": walk_m, "days": len(trip.days),
            "objects": sum(len(t.stops) - 1 for t in trip.tours)}


# --- Общий трек поездки ---

def gpx_text(trip, tour_gpx):
    """Один GPX на всю поездку: точки всех объектов, переезды и пешие маршруты."""
    wpts, trks = [], []
    for text_ in tour_gpx:
        wpts += re.findall(r"<wpt .*?</wpt>", text_, re.S)
        trks += re.findall(r"<trk>.*?</trk>", text_, re.S)
    drives = []
    for drive in trip.drives:
        if drive and drive.ok:
            drives.append("<trk><name>%s</name><trkseg>%s</trkseg></trk>" % (
                esc("Переезд от " + drive.start_name),
                "".join('<trkpt lat="%.6f" lon="%.6f"/>' % (c[1], c[0]) for c in drive.coords)))
    trks = [t for t in trks if "Дорога от" not in t]          # дороги из отдельных экскурсий — заменены переездами
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>',
                      '<gpx version="1.1" creator="GIS Tour Guide (QGIS)" xmlns="http://www.topografix.com/GPX/1/1">',
                      "<metadata><name>%s</name></metadata>" % esc(trip.title)] + wpts + drives + trks + ["</gpx>"])


def kml_text(trip, tour_kml):
    """Один KML: папка на каждый объект + переезды."""
    styles = re.findall(r"<Style .*?</Style>", tour_kml[0], re.S) if tour_kml else []
    styles.append('<Style id="transfer"><LineStyle><color>ffb87f2c</color><width>4</width></LineStyle></Style>')
    folders = []
    for tour, text_ in zip(trip.tours, tour_kml):
        inner = re.findall(r"<Folder>.*?</Folder>", text_, re.S)
        folders.append("<Folder><name>%s</name>%s</Folder>" % (esc(tour.place.name), "".join(inner)))
    drives = []
    for drive in trip.drives:
        if drive and drive.ok:
            drives.append("<Placemark><name>%s</name><styleUrl>#transfer</styleUrl><LineString><tessellate>1"
                          "</tessellate><coordinates>%s</coordinates></LineString></Placemark>" % (
                              esc("Переезд от " + drive.start_name),
                              " ".join("%.6f,%.6f" % (c[0], c[1]) for c in drive.coords)))
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
                      "<name>%s</name>" % esc(trip.title)] + styles + folders +
                     ["<Folder><name>Переезды</name>%s</Folder>" % "".join(drives), "</Document></kml>"])
