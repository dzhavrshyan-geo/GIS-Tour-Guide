# -*- coding: utf-8 -*-
"""Отчёт об экскурсии (HTML), трек GPX, профиль высот, загрузка фотографий."""

import datetime
import math
import os
import re

from . import extras
from .net import Cancelled, NetError
from .text import clip, esc, fmt_hours, fmt_km, fmt_num, plural

STYLE = """
body{font-family:'Segoe UI',Arial,sans-serif;max-width:980px;margin:0 auto;padding:16px;
color:#23302a;background:#fbfaf6;line-height:1.45}
h1{color:#2f5d3a;margin-bottom:2px}h2{color:#2f5d3a;border-bottom:2px solid #d9e4d2;
padding-bottom:3px;margin-top:28px}h3{margin:16px 0 4px}
.sub{color:#66736b;margin-top:0}.map{width:100%;border:1px solid #ccd5c6;margin:8px 0}
.warn{background:#fff3d6;border-left:4px solid #e2a400;padding:6px 10px;margin:6px 0}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #d9e0d4;padding:4px 6px;
vertical-align:top;font-size:94%}th{background:#eef3ea;text-align:left}
.poi{overflow:hidden;margin:10px 0;padding:8px;background:#fff;border:1px solid #e1e6dc}
.poi img{float:right;max-width:260px;margin:0 0 6px 10px}.num{display:inline-block;width:24px;
height:24px;border-radius:12px;background:#2f5d3a;color:#fff;text-align:center;font-weight:bold}
.gallery img{height:150px;margin:3px}.cap{font-size:85%;color:#5c6860}
.small{font-size:85%;color:#5c6860}.quote{border-left:3px solid #c5d3bd;padding-left:8px;margin:6px 0}
"""


def download_images(http, tour, folder, log=None):
    """Миниатюры фото → folder/photos; пути в local_path."""
    os.makedirs(folder, exist_ok=True)
    items = [(p, p.thumb_url, "local_path") for p in tour.photos]
    items += [(poi, poi.image_url, "image_path") for poi in tour.stops if poi.image_url]
    items += [(o, o.photo_url, "local_path") for o in tour.observations[:24] if o.photo_url]
    for index, (item, url, attr) in enumerate(items):
        match = re.search(r"\.(jpe?g|png|webp)(\?|$)", url, re.I)
        path = os.path.join(folder, "%03d.%s" % (index, (match.group(1) if match else "jpg").lower()))
        try:
            data = http.get(url, ttl=365 * 86400)
        except Cancelled:
            raise
        except NetError as error:
            if log:
                log("⚠ Фото не загружено: %s" % error)
            continue
        with open(path, "wb") as handle:
            handle.write(data)
        setattr(item, attr, path)


def write_gpx(tour, path):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="GIS Tour Guide (QGIS)" '
             'xmlns="http://www.topografix.com/GPX/1/1">',
             "<metadata><name>%s</name></metadata>" % esc("Экскурсия: " + tour.place.name)]
    for poi in tour.stops:
        lines.append('<wpt lat="%.6f" lon="%.6f">%s<name>%s</name><desc>%s</desc></wpt>' % (
            poi.lat, poi.lon, "<ele>%.0f</ele>" % poi.elevation if poi.elevation is not None else "",
            esc("%d. %s" % (poi.order, poi.name) if poi.order else poi.name), esc(poi.kind)))
    lines.append("<trk><name>%s</name>" % esc("Пеший маршрут: " + tour.place.name))
    for leg in tour.legs:
        for segment in leg.segments:
            lines.append("<trkseg>")
            lines += ['<trkpt lat="%.6f" lon="%.6f"/>' % (c[1], c[0]) for c in segment.coords]
            lines.append("</trkseg>")
    lines.append("</trk>")
    if tour.drive and tour.drive.ok:
        lines.append("<trk><name>%s</name><trkseg>" % esc("Дорога от " + tour.drive.start_name))
        lines += ['<trkpt lat="%.6f" lon="%.6f"/>' % (c[1], c[0]) for c in tour.drive.coords]
        lines.append("</trkseg></trk>")
    lines.append("</gpx>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def draw_profile(tour, path, width=900, height=230):
    """PNG профиля высот с номерами объектов. False, если высот нет."""
    prof = tour.profile
    if len(prof) < 2:
        return False
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#fbfaf6"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    left, right, top, bottom = 52, 12, 14, 30
    total = prof[-1][0] or 1
    heights = [h for _, h in prof]
    low, high = min(heights), max(heights)
    if high - low < 50:
        high = low + 50
    step = nice_step((high - low) / 4)
    low, high = math.floor(low / step) * step, math.ceil(high / step) * step

    def xy(distance, height_):
        x = left + distance / total * (width - left - right)
        y = top + (high - height_) / (high - low) * (height - top - bottom)
        return QPointF(x, y)

    painter.setFont(QFont("Arial", 8))
    value = low
    while value <= high + 0.1:
        point = xy(0, value)
        painter.setPen(QPen(QColor("#d9e0d4"), 1))
        painter.drawLine(QPointF(left, point.y()), QPointF(width - right, point.y()))
        painter.setPen(QPen(QColor("#4d5a52"), 1))
        painter.drawText(QRectF(0, point.y() - 8, left - 4, 16),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), "%d м" % value)
        value += step
    step_km = max(1, int(total / 1000 / 8))
    km = 0
    while km * 1000 <= total:
        point = xy(km * 1000, low)
        painter.drawText(QRectF(point.x() - 20, height - bottom + 4, 40, 14),
                         int(Qt.AlignmentFlag.AlignCenter), "%d км" % km)
        km += step_km
    area = QPainterPath(xy(0, low))
    for distance, value in prof:
        area.lineTo(xy(distance, value))
    area.lineTo(xy(total, low))
    area.closeSubpath()
    painter.fillPath(area, QColor(120, 160, 110, 110))
    painter.setPen(QPen(QColor("#2f5d3a"), 2))
    line = QPainterPath(xy(*prof[0]))
    for distance, value in prof[1:]:
        line.lineTo(xy(distance, value))
    painter.drawPath(line)
    # номера объектов по длине пути
    offset = 0.0
    painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
    for leg in tour.legs:
        offset += leg.length_m
        stop = tour.stops[leg.b]
        nearest = min(prof, key=lambda p: abs(p[0] - offset))
        point = xy(offset, nearest[1])
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(QColor("#c0392b") if stop.is_main else QColor("#2f5d3a"))
        painter.drawEllipse(point, 8, 8)
        painter.drawText(QRectF(point.x() - 8, point.y() - 8, 16, 16), int(Qt.AlignmentFlag.AlignCenter),
                         "С" if stop.order == 0 else str(stop.order))
    painter.end()
    image.save(path, "PNG")
    return True


def nice_step(value):
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step:
            return step
    return 1000 * math.ceil(value / 1000)


def rel(path, folder):
    return os.path.relpath(path, folder).replace(os.sep, "/") if path else ""


def section(text_):
    """Абзацы текста Викигида → HTML."""
    parts = []
    for line in text_.split("\n"):
        if line.startswith("▸ "):
            parts.append("<h3>%s</h3>" % esc(line[2:]))
        else:
            parts.append("<p>%s</p>" % esc(line))
    return "\n".join(parts)


def build_html(tour, folder, maps=None, profile_png=None):
    """Полный отчёт. maps — {'overview': путь, 'scheme': путь}."""
    maps = maps or {}
    place = tour.place
    t = tour.totals
    out = ["<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>%s</title><style>%s</style></head><body>" % (esc("Экскурсия: " + place.name), STYLE)]
    out.append("<h1>%s</h1>" % esc(place.name))
    sub = [place.kind, tour.region_name or place.region, place.country]
    out.append("<p class='sub'>%s · %.5f° с.ш., %.5f° в.д.%s · дата поездки %s</p>" % (
        esc(", ".join(s for s in sub if s)), place.lat, place.lon,
        " · %d м" % place.elevation if place.elevation is not None else "",
        esc(datetime.date.fromisoformat(tour.date).strftime("%d.%m.%Y") if tour.date else "")))
    main = next((s for s in tour.stops if s.is_main), None)
    if main:
        img = poi_image(main)
        if img:
            out.append("<img src='%s' style='float:right;max-width:340px;margin:0 0 8px 12px'>" %
                       esc(rel(img, folder)))
        text_ = main.extract or main.description
        if text_:
            out.append("<p>%s</p>" % esc(text_))
            if main.url:
                out.append("<p class='small'>Источник: <a href='%s'>%s</a></p>" % (
                    esc(main.url), esc(main.url)))
    for warning in tour.warnings:
        out.append("<div class='warn'>%s</div>" % esc(warning))

    # Как добраться
    out.append("<h2 style='clear:both'>Как добраться</h2>")
    if maps.get("overview"):
        out.append("<img class='map' src='%s'>" % esc(rel(maps["overview"], folder)))
    drive = tour.drive
    if drive and drive.ok:
        out.append("<p><b>На машине от %s:</b> %s, около %s.</p>" % (
            esc(drive.start_name), fmt_km(drive.distance_m), fmt_hours(drive.duration_s / 3600)))
        if drive.roads:
            out.append("<ol>%s</ol>" % "".join(
                "<li>%s — %s</li>" % (esc(label), fmt_km(m)) for label, m in drive.roads))
        if drive.last_mile_m > 300:
            out.append("<p>Дорога для легковой машины заканчивается в %s от объекта "
                       "(%s) — дальше пешком по маршруту ниже; часть пути, возможно, "
                       "проходима на внедорожнике по грунтовке.</p>" % (
                           fmt_km(drive.last_mile_m), esc(drive.trailhead_name or "конец дороги")))
    if drive and drive.note:
        out.append("<div class='warn'>%s</div>" % esc(drive.note))
    voyage = tour.wikivoyage or {}
    sections = voyage.get("sections") or {}
    if sections.get("get_in"):
        out.append("<h3>Из Викигида «%s»</h3>" % esc(voyage.get("title", "")))
        out.append(section(clip(sections["get_in"], 2500)))
    if sections.get("get_around"):
        out.append("<h3>Транспорт на месте</h3>" + section(clip(sections["get_around"], 1200)))
    if voyage.get("url"):
        out.append("<p class='small'>Подробнее: <a href='%s'>%s</a> (CC BY-SA)</p>" % (
            esc(voyage["url"]), esc(voyage["url"])))
    services = tour.services or {}
    labels = {"shop": "Магазин", "fuel": "Заправка", "food": "Кафе", "water": "Питьевая вода",
              "lodging": "Ночлег"}
    rows = ["<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        labels[key], esc(services[key]["name"] or "—"), fmt_km(services[key]["km"] * 1000))
        for key in labels if key in services]
    if rows:
        out.append("<h3>Ближайшее к началу маршрута</h3><table><tr><th>Что</th><th>Название</th>"
                   "<th>Расстояние</th></tr>%s</table>" % "".join(rows))

    # Маршрут экскурсии
    out.append("<h2>Маршрут экскурсии</h2>")
    if maps.get("scheme"):
        out.append("<img class='map' src='%s'>" % esc(rel(maps["scheme"], folder)))
    objects = len(tour.stops) - 1
    out.append("<p><b>%s</b>, %d %s, %s · ходового времени ~%s, с осмотром ~%s%s%s.</p>" % (
        "Кольцевой маршрут" if tour.loop else "Маршрут в одну сторону", objects,
        plural(objects, "объект", "объекта", "объектов"), fmt_km(t.get("walk_m", 0)),
        fmt_hours(t.get("hours_walk", 0)), fmt_hours(t.get("hours_walk", 0) + t.get("hours_stops", 0)),
        " · набор %d м, сброс %d м" % (t["ascent"], t["descent"]) if t.get("ascent") else "",
        " · без троп %s" % fmt_km(t["offtrail_m"]) if t.get("offtrail_m", 0) > 50 else ""))
    if profile_png:
        out.append("<img class='map' src='%s'>" % esc(rel(profile_png, folder)))
    out.append("<table><tr><th>Участок</th><th>Длина</th><th>Как идти</th><th>Набор / сброс</th>"
               "<th>Время</th></tr>")
    for leg in tour.legs:
        a, b = tour.stops[leg.a], tour.stops[leg.b]
        how = []
        trail = leg.length_m - leg.offtrail_m
        if trail > 30:
            how.append("по дорогам и тропам %s" % fmt_km(trail))
        if leg.offtrail_m > 30:
            how.append("без троп, по рельефу %s" % fmt_km(leg.offtrail_m))
        out.append("<tr><td>%s → %s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(label(a)), esc(label(b)), fmt_km(leg.length_m), esc("; ".join(how) or "рядом"),
            "+%d / −%d м" % (leg.ascent, leg.descent) if leg.ascent or leg.descent else "—",
            fmt_hours(leg.hours)))
    out.append("</table>")
    if tour.dem_name:
        out.append("<p class='small'>Участки без троп проложены по рельефу (%s) по самому "
                   "быстрому для пешехода пути с обходом склонов круче 35°. Водоёмы и "
                   "заросли не учитываются — сверяйтесь с местностью.</p>" % esc(tour.dem_name))

    # Объекты
    out.append("<h2>Объекты на маршруте</h2>")
    for poi in tour.stops[1:]:
        out.append("<div class='poi'>")
        image = poi_image(poi)
        if image:
            out.append("<img src='%s'>" % esc(rel(image, folder)))
        out.append("<h3><span class='num'>%d</span> %s</h3><p class='small'>%s%s · %s от "
                   "главного объекта</p>" % (
                       poi.order, esc(poi.name), esc(poi.kind),
                       " · %d м" % poi.elevation if poi.elevation is not None else "",
                       fmt_km(poi.distance_km * 1000)))
        text_ = poi.extract or poi.description
        out.append("<p>%s</p>" % esc(text_) if text_ else
                   "<p class='small'>Описания в открытых источниках нет.</p>")
        if poi.url:
            out.append("<p class='small'><a href='%s'>%s</a></p>" % (esc(poi.url), esc(poi.url)))
        out.append("</div>")

    # Советы
    out.append("<h2>Что взять и как подготовиться</h2>")
    for title, items in tour.advice:
        out.append("<h3>%s</h3><ul>%s</ul>" % (esc(title), "".join(
            "<li>%s</li>" % esc(item) for item in items)))
    if tour.weather:
        out.append("<h3>Прогноз погоды</h3><table><tr><th>Точка</th><th>Дата</th><th>°C</th>"
                   "<th>Осадки</th><th>Ветер</th><th>Погода</th></tr>")
        for point in tour.weather:
            for day in point["days"][:4]:
                out.append("<tr><td>%s (%s м)</td><td>%s</td><td>%s…%s</td><td>%s мм%s</td>"
                           "<td>%s м/с</td><td>%s</td></tr>" % (
                               esc(point["label"]), "%.0f" % (point["elevation"] or 0),
                               esc(day["date"]), fmt_t(day["tmin"]), fmt_t(day["tmax"]),
                               fmt_num(day["precip"] or 0, 1),
                               " (%d%%)" % day["precip_prob"] if day.get("precip_prob") is not None else "",
                               fmt_num(day["wind"] or 0, 0),
                               esc(extras.WEATHER_CODES.get(day.get("code"), ""))))
        out.append("</table>")
    if sections.get("safety"):
        out.append("<h3>Безопасность (Викигид)</h3>" + section(clip(sections["safety"], 1500)))

    # Фото и отзывы
    if tour.photos:
        out.append("<h2>Фотографии</h2><div class='gallery'>")
        for photo in tour.photos:
            if photo.local_path:
                out.append("<a href='%s' title='%s'><img src='%s'></a>" % (
                    esc(photo.page_url), esc(photo.description or photo.title),
                    esc(rel(photo.local_path, folder))))
        out.append("</div><h3>Подписи авторов снимков</h3>")
        for photo in tour.photos:
            if photo.description:
                out.append("<div class='quote'>%s<br><span class='cap'>— %s, %s, %s · "
                           "<a href='%s'>Викисклад</a></span></div>" % (
                               esc(photo.description), esc(photo.author or "автор не указан"),
                               esc(photo.date or ""), esc(photo.license), esc(photo.page_url)))
    if tour.observations:
        out.append("<h2>Кого можно встретить</h2><p class='small'>Подтверждённые наблюдения "
                   "iNaturalist в зоне поиска.</p><table><tr><th></th><th>Вид</th><th>Группа</th>"
                   "<th>Наблюдений</th></tr>")
        for obs in tour.observations[:24]:
            out.append("<tr><td>%s</td><td><a href='%s'>%s</a> <i>%s</i></td><td>%s</td>"
                       "<td>%d</td></tr>" % (
                           "<img src='%s' height='48'>" % esc(rel(obs.local_path, folder))
                           if obs.local_path else "", esc(obs.url), esc(obs.name), esc(obs.latin),
                           esc(obs.group), obs.count))
        out.append("</table>")
    if tour.observer_notes:
        out.append("<h3>Заметки туристов и натуралистов</h3>")
        for species_, text_, user, date, url, _photo in tour.observer_notes[:10]:
            out.append("<div class='quote'>«%s»<br><span class='cap'>— %s, %s%s · "
                       "<a href='%s'>iNaturalist</a></span></div>" % (
                           esc(text_), esc(user), esc(date),
                           " · " + esc(species_) if species_ else "", esc(url)))
    if sections.get("see") or sections.get("do"):
        out.append("<h2>Ещё из Викигида</h2>")
        if sections.get("see"):
            out.append("<h3>Достопримечательности</h3>" + section(clip(sections["see"], 1500)))
        if sections.get("do"):
            out.append("<h3>Чем заняться</h3>" + section(clip(sections["do"], 1200)))
        if sections.get("sleep"):
            out.append("<h3>Ночлег</h3>" + section(clip(sections["sleep"], 800)))

    out.append("<h2>Источники</h2><ul>")
    for name, url, license_ in tour.sources:
        out.append("<li>%s%s%s</li>" % (
            "<a href='%s'>%s</a>" % (esc(url), esc(name)) if url else esc(name),
            " — " + esc(license_) if license_ else "", ""))
    out.append("</ul><p class='small'>Сведения собраны автоматически из открытых источников "
               "плагином «ГИС-экскурсовод» для QGIS %s. Перед поездкой проверьте актуальность "
               "дорог, погоды и правил посещения.</p></body></html>" %
               datetime.date.today().strftime("%d.%m.%Y"))
    return "\n".join(out)


def poi_image(poi):
    """Картинка объекта: из статьи Википедии, иначе первое фото с Викисклада."""
    if poi.image_path:
        return poi.image_path
    for photo in poi.photos:
        if photo.local_path:
            return photo.local_path
    return ""


def label(poi):
    if poi.order == 0:
        return "Старт"
    return "%d. %s" % (poi.order, poi.name)


def fmt_t(value):
    if value is None:
        return "?"
    return "%+d" % round(value)
