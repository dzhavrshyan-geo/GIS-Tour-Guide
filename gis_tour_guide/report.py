# -*- coding: utf-8 -*-
"""Фирменный отчёт об экскурсии: HTML (все картинки внутри файла) и PDF, трек GPX,
профиль высот, загрузка фотографий в память.
"""

import base64
import datetime
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import brand, extras, webmap
from .net import Cancelled, NetError
from .text import clip, esc, fmt_hours, fmt_km, fmt_num, plural

PDF_WIDTH = 690          # ширина картинок во всю страницу PDF (страница — 703 px)

WEB_CSS = """
*{box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;max-width:1000px;margin:0 auto;padding:0 18px 30px;
color:%(ink)s;background:%(paper)s;line-height:1.5}
.top{background:linear-gradient(90deg,%(green)s,#3f7a4c);color:#fff;border-radius:0 0 14px 14px;
padding:16px 22px;margin:0 -18px 18px}
.top td{color:#fff;vertical-align:middle}.brand{color:%(sand)s;font-size:13px;letter-spacing:.5px}
.title{font-size:30px;font-weight:bold;margin:2px 0}.chips span{display:inline-block;background:rgba(255,255,255,.16);
border-radius:12px;padding:2px 10px;margin:4px 6px 0 0;font-size:13px}
h2{color:%(green)s;font-size:21px;margin:30px 0 10px;padding:6px 12px;background:%(green_soft)s;
border-left:6px solid %(green)s;border-radius:4px}
h3{color:%(green_dark)s;margin:16px 0 6px}
.card{background:#fff;border:1px solid %(line)s;border-radius:10px;padding:12px 14px;margin:10px 0;overflow:hidden}
.warn{background:#fff3d6;border-left:5px solid #e2a400;border-radius:4px;padding:8px 12px;margin:8px 0}
.map{width:100%%;border:1px solid %(line)s;border-radius:8px;margin:8px 0}
.tiles td{background:#fff;border:1px solid %(line)s;border-radius:10px;text-align:center;padding:10px}
.tiles{border-spacing:8px;border-collapse:separate;width:100%%;margin:0 -8px}
.big{font-size:24px;font-weight:bold;color:%(green)s}.cap,.small{font-size:13px;color:%(muted)s}
table.grid{border-collapse:collapse;width:100%%;background:#fff}
table.grid td,table.grid th{border:1px solid %(line)s;padding:6px 8px;vertical-align:top;font-size:14px}
table.grid th{background:%(green)s;color:#fff;text-align:left}
table.grid tr:nth-child(even) td{background:%(paper)s}
.num{display:inline-block;min-width:26px;height:26px;border-radius:13px;background:%(green)s;color:#fff;
text-align:center;font-weight:bold;line-height:26px;margin-right:6px}
.num.main{background:%(pin)s}.num.photo{background:%(photo)s}
.poiimg{float:right;width:260px;border-radius:8px;margin:0 0 8px 14px}
.gallery td{vertical-align:top;width:33%%;padding:6px}
.gallery img{width:100%%;border-radius:8px}
.quote{border-left:4px solid %(sand)s;background:#fff;padding:6px 12px;margin:8px 0;border-radius:4px}
a{color:%(green)s;overflow-wrap:anywhere}
.foot{margin-top:30px;padding:12px 16px;background:%(sand_soft)s;border-radius:10px;color:%(muted)s;font-size:13px}
""" % {"ink": brand.INK, "paper": brand.PAPER, "green": brand.GREEN, "sand": brand.SAND,
       "green_soft": brand.GREEN_SOFT, "green_dark": brand.GREEN_DARK, "line": brand.LINE,
       "muted": brand.MUTED, "pin": brand.PIN, "photo": brand.PHOTO, "sand_soft": brand.SAND_SOFT}

# Qt-движок PDF понимает подмножество CSS — только то, что точно работает
PDF_CSS = """
body{font-family:Arial;color:%(ink)s;font-size:10pt}
h2{color:%(green)s;font-size:15pt;margin-top:16px;margin-bottom:6px}
h3{color:%(green_dark)s;font-size:11.5pt;margin-top:10px;margin-bottom:3px}
.brand{color:%(sand)s;font-size:9pt}.title{color:white;font-size:21pt;font-weight:bold}
.chips{color:white;font-size:9pt}.big{font-size:16pt;font-weight:bold;color:%(green)s}
.cap,.small{font-size:8.5pt;color:%(muted)s}
th{background-color:%(green)s;color:white;text-align:left}
.num{color:white;font-weight:bold}
.quote{color:%(ink)s}
a{color:%(green)s;overflow-wrap:anywhere}
""" % {"ink": brand.INK, "green": brand.GREEN, "green_dark": brand.GREEN_DARK, "sand": brand.SAND,
       "muted": brand.MUTED}


# --- Картинки в памяти ---

class Images:
    """Картинки отчёта: для HTML — data:URI, для PDF — ресурсы QTextDocument."""

    def __init__(self):
        self.items = {}

    def add(self, key, data, mime="image/jpeg"):
        if data:
            self.items[key] = (data, mime)
        return key if data else None

    def has(self, key):
        return key in self.items

    def aspect(self, key):
        """Высота / ширина картинки (для PDF: Qt иначе резервирует исходную высоту)."""
        from qgis.PyQt.QtGui import QImage
        image = QImage.fromData(self.items[key][0])
        if image.isNull() or not image.width():
            return 0.75
        return image.height() / float(image.width())

    def src(self, key, pdf=False):
        if pdf:
            return "img:" + key
        data, mime = self.items[key]
        return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def gather(tour, maps=None):
    """Все картинки отчёта: логотип, карты (PNG/JPEG-байты), профиль, фото, виды."""
    images = Images()
    with open(brand.ICON, "rb") as handle:
        images.add("logo", handle.read(), "image/png")
    for key, data in (maps or {}).items():
        images.add("map:" + key, data, sniff_mime(data) if data else "image/jpeg")
    images.add("profile", tour.profile_png, "image/png")
    for poi in tour.stops:
        if poi.image_data:
            images.add("poi:" + poi.uid, poi.image_data, sniff_mime(poi.image_data))
    for photo in tour.photos:
        if photo.data:
            images.add("photo:%d" % photo.number, photo.data, sniff_mime(photo.data))
    for i, obs in enumerate(tour.observations[:24]):
        if obs.data:
            images.add("obs:%d" % i, obs.data, sniff_mime(obs.data))
    return images


def sniff_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def collect_images(http, tour, save_dir=None, log=None, progress=None):
    """Скачать миниатюры (параллельно) в память; save_dir — ещё и на диск."""
    items = [(p, p.thumb_url) for p in tour.photos]
    items += [(poi, poi.image_url) for poi in tour.stops if poi.image_url]
    items += [(o, o.photo_url) for o in tour.observations[:24] if o.photo_url]
    done = [0]

    def fetch(pair):
        item, url = pair
        try:
            data = http.get(url, ttl=365 * 86400)
        except Cancelled:
            raise
        except NetError as error:
            if log:
                log("⚠ Фото не загружено: %s" % error)
            data = None
        done[0] += 1
        if progress:
            progress(done[0] / float(len(items)))
        return data

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(fetch, items))
    for (item, _url), data in zip(items, results):
        if hasattr(item, "image_data"):
            item.image_data = data
        else:
            item.data = data
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        for photo in tour.photos:
            if photo.data:
                ext = {"image/png": "png", "image/webp": "webp"}.get(sniff_mime(photo.data), "jpg")
                name = "%02d_%s.%s" % (photo.number, re.sub(r"[^\w\-]+", "_", photo.poi_name)[:30], ext)
                photo.local_path = os.path.join(save_dir, name)
                with open(photo.local_path, "wb") as handle:
                    handle.write(photo.data)


def write_gpx(tour, path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(gpx_text(tour))


def gpx_text(tour):
    """Трек и точки экскурсии в формате GPX 1.1 (строка)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="GIS Tour Guide (QGIS)" '
             'xmlns="http://www.topografix.com/GPX/1/1">',
             "<metadata><name>%s</name></metadata>" % esc("Экскурсия: " + tour.place.name)]
    for poi in tour.stops:
        lines.append('<wpt lat="%.6f" lon="%.6f">%s<name>%s</name><desc>%s</desc></wpt>' % (
            poi.lat, poi.lon, "<ele>%.0f</ele>" % poi.elevation if poi.elevation is not None else "",
            esc("%d. %s" % (poi.order, poi.name) if poi.order else poi.name), esc(poi.kind)))
    for stop in tour.stops:
        for member in stop.members:
            lines.append('<wpt lat="%.6f" lon="%.6f"><name>%s</name><desc>%s</desc></wpt>' % (
                member.lat, member.lon, esc("%d. %s" % (stop.order, member.name)), esc(member.kind)))
    for item in tour.lodging:
        lines.append('<wpt lat="%.6f" lon="%.6f"><name>%s</name><desc>%s</desc></wpt>' % (
            item["lat"], item["lon"], esc("Ночлег: " + item["name"]), esc(item["label"])))
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
    return "\n".join(lines)


def nice_step(value):
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step:
            return step
    return 1000 * math.ceil(value / 1000)


def image_bytes(image, fmt="PNG", quality=-1):
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, fmt, quality)
    return bytes(data)


def draw_profile(tour, width=900, height=230):
    """PNG (байты) профиля высот с номерами объектов; None, если высот нет."""
    prof = tour.profile
    if len(prof) < 2:
        return None
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    left, right, top, bottom = 56, 14, 16, 30
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

    painter.setFont(QFont(brand.FONT, 8))
    value = low
    while value <= high + 0.1:
        point = xy(0, value)
        painter.setPen(QPen(QColor(brand.LINE), 1))
        painter.drawLine(QPointF(left, point.y()), QPointF(width - right, point.y()))
        painter.setPen(QPen(QColor(brand.MUTED), 1))
        painter.drawText(QRectF(0, point.y() - 8, left - 6, 16),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), "%d м" % value)
        value += step
    step_km = max(1, int(total / 1000 / 8)) if total > 3000 else 0.5
    km = 0
    while km * 1000 <= total:
        point = xy(km * 1000, low)
        painter.drawText(QRectF(point.x() - 22, height - bottom + 5, 44, 14),
                         int(Qt.AlignmentFlag.AlignCenter), ("%g км" % km).replace(".", ","))
        km += step_km
    area = QPainterPath(xy(0, low))
    for distance, value in prof:
        area.lineTo(xy(distance, value))
    area.lineTo(xy(total, low))
    area.closeSubpath()
    gradient = QLinearGradient(0, top, 0, height - bottom)
    gradient.setColorAt(0, QColor(brand.SAND))
    gradient.setColorAt(1, QColor(brand.GREEN_SOFT))
    painter.fillPath(area, gradient)
    painter.setPen(QPen(QColor(brand.GREEN), 2.2))
    line = QPainterPath(xy(*prof[0]))
    for distance, value in prof[1:]:
        line.lineTo(xy(distance, value))
    painter.drawPath(line)
    offset = 0.0
    painter.setFont(QFont(brand.FONT, 8, QFont.Weight.Bold))
    for leg in tour.legs:
        offset += leg.length_m
        stop = tour.stops[leg.b]
        nearest = min(prof, key=lambda p: abs(p[0] - offset))
        point = xy(min(offset, total), nearest[1])
        painter.setPen(QPen(QColor("white"), 1.5))
        painter.setBrush(QColor(brand.PIN if stop.is_main else brand.SLATE if stop.order == 0
                                else brand.GREEN))
        painter.drawEllipse(point, 9, 9)
        painter.drawText(QRectF(point.x() - 9, point.y() - 9, 18, 18), int(Qt.AlignmentFlag.AlignCenter),
                         "С" if stop.order == 0 else str(stop.order))
    painter.end()
    return image_bytes(image)


# --- HTML ---

class Builder:

    def __init__(self, tour, images, pdf=False, basemap=None):
        self.tour = tour
        self.images = images
        self.pdf = pdf
        self.basemap = basemap        # None — без интерактивных карт
        self.out = []

    def map(self, key, title, sheet):
        """Карта: в HTML — интерактивная (лист для печати раскрывается), в PDF — фирменный лист."""
        if self.pdf or self.basemap is None:
            return self.figure("map:" + key)
        printed = self.images.src("map:" + key) if self.images.has("map:" + key) else ""
        base = self.basemap if key != "overview" else (self.basemap if self.basemap in webmap.TILES else "light")
        return webmap.block(key, self.tour, base, title, sheet, printed) or self.figure("map:" + key)

    def add(self, *parts):
        self.out.extend(parts)

    def img(self, key, width=None, cls="", extra=""):
        if not self.images.has(key):
            return ""
        attrs = " class='%s'" % cls if cls and not self.pdf else ""
        if self.pdf and width:
            attrs += " width='%d' height='%d'" % (width, round(width * self.images.aspect(key)))
        return "<img src='%s'%s%s>" % (self.images.src(key, self.pdf), attrs, extra)

    def figure(self, key):
        """Картинка во всю ширину: в PDF — в отдельной ячейке (Qt верно переносит её на страницу)."""
        image = self.img(key, PDF_WIDTH, "map")
        if not image or not self.pdf:
            return image
        return "<table width='100%%' cellspacing='0' cellpadding='0'><tr><td>%s</td></tr></table>" % image

    def h2(self, title, first=False):
        if self.pdf:
            # в PDF — плашка-таблица и каждая большая часть с новой страницы
            brk = "" if first else " style='page-break-before:always'"
            self.add("<table width='100%%' cellpadding='6' cellspacing='0'%s><tr>"
                     "<td bgcolor='%s' width='6'></td><td bgcolor='%s'><span style='font-size:15pt;"
                     "font-weight:bold;color:%s'>%s</span></td></tr></table>" % (
                         brk, brand.GREEN, brand.GREEN_SOFT, brand.GREEN, esc(title)))
        else:
            self.add("<h2>%s</h2>" % esc(title))

    def grid(self, headers, rows):
        if self.pdf:
            head = "".join("<th bgcolor='%s'><font color='white'>%s</font></th>" % (brand.GREEN, esc(h))
                           for h in headers)
            body = "".join("<tr>%s</tr>" % "".join(
                "<td%s>%s</td>" % (" bgcolor='%s'" % brand.PAPER if i % 2 else "", cell)
                for cell in row) for i, row in enumerate(rows))
            return ("<table width='100%%' border='1' cellspacing='0' cellpadding='4' "
                    "style='border-color:%s;border-collapse:collapse'><tr>%s</tr>%s</table>" % (
                        brand.LINE, head, body))
        head = "".join("<th>%s</th>" % esc(h) for h in headers)
        body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell for cell in row) for row in rows)
        return "<table class='grid'><tr>%s</tr>%s</table>" % (head, body)

    def warn(self, text_):
        if self.pdf:
            return ("<table width='100%%' cellpadding='6' cellspacing='0'><tr><td bgcolor='#e2a400' "
                    "width='4'></td><td bgcolor='#fff3d6'>%s</td></tr></table>" % esc(text_))
        return "<div class='warn'>%s</div>" % esc(text_)

    def num(self, value, kind=""):
        color = {"main": brand.PIN, "photo": brand.PHOTO, "start": brand.SLATE}.get(kind, brand.GREEN)
        if self.pdf:
            return "<span style='background-color:%s;color:white;font-weight:bold'>&nbsp;%s&nbsp;</span>&nbsp;" % (
                color, esc(value))
        return "<span class='num %s'>%s</span>" % (kind, esc(value))

    def tiles(self, items):
        if self.pdf:
            cells = "".join("<td align='center' bgcolor='white' style='border:1px solid %s'>"
                            "<span class='big'>%s</span><br><span class='cap'>%s</span></td>" % (
                                brand.LINE, esc(big), esc(cap)) for big, cap in items)
            return ("<table width='100%%' cellspacing='6' cellpadding='8'><tr>%s</tr></table>" % cells)
        cells = "".join("<td><div class='big'>%s</div><div class='cap'>%s</div></td>" % (
            esc(big), esc(cap)) for big, cap in items)
        return "<table class='tiles'><tr>%s</tr></table>" % cells

    def section(self, text_):
        parts = []
        for line in text_.split("\n"):
            if line.startswith("▸ "):
                parts.append("<h3>%s</h3>" % esc(line[2:]))
            else:
                parts.append("<p>%s</p>" % esc(line))
        return "\n".join(parts)

    def link(self, url, title=None):
        return "<a href='%s'>%s</a>" % (esc(url), esc(title or url))


def header(b):
    tour = b.tour
    place = tour.place
    t = tour.totals
    chips = [brand.MODES[tour.mode], tour.region_name or place.region]
    if tour.date:
        chips.append(datetime.date.fromisoformat(tour.date).strftime("%d.%m.%Y"))
    chips.append("%s пешком · ~%s" % (fmt_km(t.get("walk_m", 0)),
                                      fmt_hours(t.get("hours_walk", 0) + t.get("hours_stops", 0))))
    if place.elevation is not None and not tour.urban:
        chips.append("%d м над уровнем моря" % place.elevation)
    logo = b.img("logo", 56) if b.images.has("logo") else ""
    if b.pdf:
        b.add("<table width='100%%' cellpadding='10' cellspacing='0'><tr><td bgcolor='%s' width='70'>%s</td>"
              "<td bgcolor='%s'><span class='brand'>%s</span><br><span class='title'>%s</span><br>"
              "<span class='chips'>%s</span></td></tr></table>" % (
                  brand.GREEN, logo, brand.GREEN, esc(brand.NAME.upper()), esc(place.name),
                  esc(" · ".join(c for c in chips if c))))
    else:
        b.add("<div class='top'><table><tr><td style='padding-right:14px'>%s</td><td>"
              "<div class='brand'>%s</div><div class='title'>%s</div><div class='chips'>%s</div>"
              "</td></tr></table></div>" % (
                  b.img("logo", extra=" width='64'"), esc(brand.NAME.upper()), esc(place.name),
                  "".join("<span>%s</span>" % esc(c) for c in chips if c)))


def about(b):
    tour = b.tour
    main = next((s for s in tour.stops if s.is_main), tour.candidates[0] if tour.candidates else None)
    b.h2("Об объекте", first=True)
    if main is None:
        return
    key = "poi:%s" % main.uid
    image = b.img(key, 260, "poiimg", " align='right'") or b.img(first_photo_key(main), 260, "poiimg",
                                                                 " align='right'")
    text_ = main.extract or main.description or "Описания в открытых источниках не нашлось."
    b.add(("<p>%s%s</p>" if b.pdf else "<div class='card'>%s<p>%s</p>") % (image, esc(text_)))
    facts = [main.kind, "%.5f° с. ш., %.5f° в. д." % (tour.place.lat, tour.place.lon)]
    if main.popularity:
        facts.append("статью читают ~%d раз в день" % main.popularity)
    b.add("<p class='small'>%s%s</p>" % (esc(" · ".join(f for f in facts if f)),
                                         " · " + b.link(main.url, "источник") if main.url else ""))
    if not b.pdf:
        b.add("</div>")
    for warning in tour.warnings:
        b.add(b.warn(warning))


def first_photo_key(poi):
    return "photo:%d" % poi.photos[0].number if poi.photos else ""


def how_to_get(b):
    tour = b.tour
    b.h2("Как добраться")
    b.add(b.map("overview", "%s — как добраться" % tour.place.name, "обзорная карта"))
    drive = tour.drive
    if tour.start_reason and drive and drive.start_name:
        b.add("<p class='small'>Откуда едем: %s (%s).</p>" % (esc(drive.start_name), esc(tour.start_reason)))
    if tour.urban and tour.transport:
        b.add("<h3>Общественный транспорт</h3>")
        b.add(b.grid(["Вид", "Остановка / станция", "До объекта"], [
            [esc(kind), esc(name), fmt_km(km * 1000)] for kind, name, km, _ in tour.transport[:8]]))
    if drive and drive.ok:
        b.add("<h3>На машине от %s — %s, около %s</h3>" % (
            esc(drive.start_name), fmt_km(drive.distance_m), fmt_hours(drive.duration_s / 3600)))
        if drive.roads:
            b.add(b.grid(["№", "Дорога", "Км"], [
                [str(i), esc(label), fmt_km(m)] for i, (label, m) in enumerate(drive.roads, 1)]))
        if drive.last_mile_m > 300 and not tour.urban:
            b.add("<p>Дорога для легковой машины заканчивается в %s от объекта (%s) — дальше пешком "
                  "по маршруту ниже; часть пути, возможно, проходима на внедорожнике.</p>" % (
                      fmt_km(drive.last_mile_m), esc(drive.trailhead_name or "конец дороги")))
    if drive and drive.note:
        b.add(b.warn(drive.note))
    voyage = tour.wikivoyage or {}
    sections = voyage.get("sections") or {}
    if sections.get("get_in"):
        b.add("<h3>Из Викигида «%s»</h3>" % esc(voyage.get("title", "")))
        b.add(b.section(clip(sections["get_in"], 2200)))
    if sections.get("get_around"):
        b.add("<h3>Транспорт на месте</h3>" + b.section(clip(sections["get_around"], 1000)))
    if voyage.get("url"):
        b.add("<p class='small'>Подробнее: %s (CC BY-SA)</p>" % b.link(voyage["url"]))
    services = tour.services or {}
    labels = {"shop": "Магазин", "fuel": "Заправка", "food": "Кафе", "water": "Питьевая вода",
              "lodging": "Ночлег"}
    rows = [[labels[key], esc(services[key]["name"] or "—"), fmt_km(services[key]["km"] * 1000)]
            for key in labels if key in services]
    if rows:
        b.add("<h3>Ближайшее к началу маршрута</h3>" + b.grid(["Что", "Название", "Расстояние"], rows))


def route(b):
    tour = b.tour
    t = tour.totals
    b.h2("Маршрут прогулки" if tour.urban else "Маршрут экскурсии")
    b.add(b.map("scheme", "%s — маршрут" % tour.place.name, "карта-схема маршрута"))
    objects = len(tour.stops) - 1
    tiles = [(fmt_km(t.get("walk_m", 0)), "кольцевой" if tour.loop else "в одну сторону"),
             (fmt_hours(t.get("hours_walk", 0)), "в пути пешком"),
             (fmt_hours(t.get("hours_walk", 0) + t.get("hours_stops", 0)), "с осмотром"),
             (str(objects), plural(objects, "объект", "объекта", "объектов"))]
    if t.get("ascent"):
        tiles.insert(3, ("%d м" % t["ascent"], "набор высоты"))
    b.add(b.tiles(tiles))
    if b.images.has("profile"):
        b.add("<h3>Профиль высот</h3>" + b.figure("profile"))
    rows = []
    for leg in tour.legs:
        x, y = tour.stops[leg.a], tour.stops[leg.b]
        how = []
        trail = leg.length_m - leg.offtrail_m
        if trail > 30:
            how.append(("по улицам %s" if tour.urban else "по дорогам и тропам %s") % fmt_km(trail))
        if leg.offtrail_m > 30:
            how.append("без троп, по рельефу %s" % fmt_km(leg.offtrail_m))
        row = ["%s → %s" % (esc(label(x)), esc(label(y))), fmt_km(leg.length_m),
               esc("; ".join(how) or "рядом")]
        if not tour.urban:
            row.append("+%d / −%d м" % (leg.ascent, leg.descent) if leg.ascent or leg.descent else "—")
        row.append(fmt_hours(leg.hours))
        rows.append(row)
    headers = ["Участок", "Длина", "Как идти"] + ([] if tour.urban else ["Набор / сброс"]) + ["Время"]
    b.add("<h3>По участкам</h3>" + b.grid(headers, rows))
    if tour.dem_name and t.get("offtrail_m", 0) > 30:
        b.add("<p class='small'>Участки без троп проложены по рельефу (%s) по самому быстрому для "
              "пешехода пути: склоны круче 35° и озёра обходятся, болота, ледники и реки (броды) "
              "пересекаются только если иначе намного дольше. Заросли не учитываются.</p>" %
              esc(tour.dem_name))


def stops_section(b):
    tour = b.tour
    b.h2("Достопримечательности на маршруте" if tour.urban else "Объекты на маршруте")
    for poi in tour.stops[1:]:
        image = b.img("poi:%s" % poi.uid, 230, "poiimg", " align='right'") or \
            b.img(first_photo_key(poi), 230, "poiimg", " align='right'")
        facts = [poi.kind]
        if poi.elevation is not None and not tour.urban:
            facts.append("%d м" % poi.elevation)
        facts.append("%s от главного объекта" % fmt_km(poi.distance_km * 1000))
        if poi.popularity >= 1:
            facts.append("~%d просмотров статьи в день" % poi.popularity)
        tags = poi.tags or {}
        extra = []
        if tour.urban:
            if tags.get("opening_hours"):
                extra.append("Часы работы: " + esc(tags["opening_hours"]))
            if tags.get("fee") in ("yes", "no"):
                extra.append("Вход " + ("платный" if tags["fee"] == "yes" else "свободный"))
            site = tags.get("website") or tags.get("contact:website")
            if site:
                extra.append("Сайт: " + b.link(site))
        if poi.members:
            extra.append("Рядом, в той же остановке: " + "; ".join(
                "<b>%s</b> (%s)%s" % (esc(m.name), esc(m.kind.lower()),
                                      " — " + esc(clip(m.extract or m.description, 160))
                                      if (m.extract or m.description) else "")
                for m in poi.members))
        photos = ", ".join("№%d" % p.number for p in poi.photos)
        if photos:
            extra.append("Фото: " + photos)
        text_ = poi.extract or poi.description
        body = ("<h3>%s%s</h3><p class='small'>%s</p>%s<p>%s</p>%s%s" % (
            b.num(poi.order, "main" if poi.is_main else ""), esc(poi.name), esc(" · ".join(facts)),
            image, esc(text_) if text_ else "<i>Описания в открытых источниках нет.</i>",
            "<p class='small'>%s</p>" % " · ".join(extra) if extra else "",
            "<p class='small'>%s</p>" % b.link(poi.url) if poi.url else ""))
        b.add(body if b.pdf else "<div class='card'>%s</div>" % body)


def advice_section(b):
    tour = b.tour
    b.h2("Советы и подготовка" if tour.urban else "Что взять и как подготовиться")
    for title, items in tour.advice:
        b.add("<h3>%s</h3><ul>%s</ul>" % (esc(title), "".join("<li>%s</li>" % esc(i) for i in items)))
    if tour.weather:
        rows = []
        for point in tour.weather:
            for day in point["days"][:4]:
                rows.append(["%s (%s м)" % (esc(point["label"]), "%.0f" % (point["elevation"] or 0)),
                             esc(day["date"]), "%s…%s" % (fmt_t(day["tmin"]), fmt_t(day["tmax"])),
                             "%s мм%s" % (fmt_num(day["precip"] or 0, 1),
                                          " (%d%%)" % day["precip_prob"] if day.get("precip_prob") is not None else ""),
                             "%s м/с" % fmt_num(day["wind"] or 0, 0),
                             esc(extras.WEATHER_CODES.get(day.get("code"), ""))])
        b.add("<h3>Прогноз погоды</h3>" + b.grid(["Точка", "Дата", "°C", "Осадки", "Ветер", "Погода"], rows))
    sections = (tour.wikivoyage or {}).get("sections") or {}
    if sections.get("safety"):
        b.add("<h3>Безопасность (Викигид)</h3>" + b.section(clip(sections["safety"], 1300)))


def photos_section(b):
    tour = b.tour
    photos = [p for p in tour.photos if b.images.has("photo:%d" % p.number)]
    if not photos:
        return
    b.h2("Фотографии")
    b.add("<p class='small'>Номера совпадают с картой-схемой фотографий: каждый снимок стоит там, "
          "где его сделали.</p>")
    b.add(b.map("photos", "Где сделаны фотографии", "карта-схема фотографий"))
    rows = []
    for i in range(0, len(photos), 3):
        cells = []
        for photo in photos[i:i + 3]:
            caption = "%s<b>%s</b><br><span class='cap'>%s%s<br>© %s, %s · %s</span>" % (
                b.num(photo.number, "photo"), esc(photo.poi_name),
                esc(clip(photo.description, 160)), "" if not photo.date else " (%s)" % esc(photo.date),
                esc(photo.author or "автор не указан"), esc(photo.license), b.link(photo.page_url, "Викисклад"))
            anchor = "" if b.pdf else " id='card-%d'" % photo.number
            cells.append("<td valign='top' width='33%%'%s>%s<br>%s</td>" % (
                anchor, b.img("photo:%d" % photo.number, 215, extra="" if b.pdf else " id='photo-%d'" % photo.number),
                caption))
        cells += ["<td></td>"] * (3 - len(cells))
        rows.append("<tr>%s</tr>" % "".join(cells))
    b.add("<table class='gallery' width='100%%' cellpadding='4'>%s</table>" % "".join(rows))


def nature_section(b):
    tour = b.tour
    if tour.observations:
        b.h2("Кого можно встретить")
        b.add("<p class='small'>Подтверждённые наблюдения iNaturalist в зоне поиска.</p>")
        b.add(b.grid(["", "Вид", "Группа", "Наблюдений"], [
            [b.img("obs:%d" % i, 48, extra="" if b.pdf else " width='48'"), "%s <i>%s</i>" % (b.link(o.url, o.name), esc(o.latin)),
             esc(o.group), str(o.count)] for i, o in enumerate(tour.observations[:24])]))
    if tour.observer_notes:
        b.add("<h3>Заметки туристов и натуралистов</h3>")
        for species_, text_, user, date, url, _photo in tour.observer_notes[:10]:
            b.add("<p class='quote'>«%s»<br><span class='cap'>— %s, %s%s · %s</span></p>" % (
                esc(text_), esc(user), esc(date), " · " + esc(species_) if species_ else "",
                b.link(url, "iNaturalist")))


def voyage_more(b):
    tour = b.tour
    sections = (tour.wikivoyage or {}).get("sections") or {}
    keys = [("see", "Достопримечательности", 1300), ("do", "Чем заняться", 1000)]
    keys += [("eat", "Где поесть", 900)] if tour.urban else [("sleep", "Ночлег", 800)]
    if not any(sections.get(k) for k, _, _ in keys):
        return
    b.h2("Ещё из Викигида")
    for key, title, limit in keys:
        if sections.get(key):
            b.add("<h3>%s</h3>%s" % (title, b.section(clip(sections[key], limit))))


def sources(b):
    tour = b.tour
    b.h2("Источники")
    b.add("<ul>%s</ul>" % "".join("<li>%s%s</li>" % (
        b.link(url, name) if url else esc(name), " — " + esc(license_) if license_ else "")
        for name, url, license_ in tour.sources))
    note = ("Сведения собраны автоматически из открытых источников плагином «%s» для QGIS %s. "
            "Перед поездкой проверьте дороги, погоду, часы работы и правила посещения." % (
                brand.NAME, datetime.date.today().strftime("%d.%m.%Y")))
    if b.pdf:
        b.add("<p class='small'>%s</p>" % esc(note))
    else:
        b.add("<div class='foot'>%s</div>" % esc(note))


def days_section(b):
    """«Не успеть за день»: план по дням и три варианта ночлега."""
    tour = b.tour
    if not tour.overnight:
        return
    b.h2("Ночлег и план по дням")
    b.add(b.warn("Вы можете не успеть посмотреть всё за один день: с дорогой выходит ~%s, а светлого "
                 "времени ~%s. Советуем остановиться на ночь поблизости." % (
                     fmt_hours(tour.day_hours), fmt_hours(tour.daylight))))
    rows = []
    for day in tour.days:
        names = ", ".join("%d. %s" % (n, tour.stops[n].name) for n in day["stops"] if n < len(tour.stops))
        rows.append(["День %d" % day["number"], esc(names or "возвращение"), fmt_km(day["km"] * 1000),
                     fmt_hours(day["hours"])])
    b.add("<h3>План по дням</h3>" + b.grid(["День", "Точки", "Пешком", "Время с осмотром"], rows))
    if tour.lodging:
        rows = []
        for item in tour.lodging:
            contacts = []
            if item.get("website"):
                contacts.append(b.link(item["website"], "сайт"))
            if item.get("phone"):
                contacts.append(esc(item["phone"]))
            contacts.append(b.link(item["url"], "на карте"))
            rows.append([esc(item["label"]), "<b>%s</b>" % esc(item["name"]),
                         esc(item["kind"]) + (" · %d★" % item["stars"] if item["stars"] else ""),
                         fmt_km(item["km"] * 1000), " · ".join(contacts)])
        b.add("<h3>Где переночевать — ближайшие варианты</h3>" + b.grid(
            ["Сегмент", "Название", "Тип", "От маршрута", "Контакты"], rows))
        b.add("<p class='small'>Класс жилья определён по типу и звёздам из OpenStreetMap — "
              "цены и свободные места уточняйте у владельцев.</p>")
    else:
        b.add("<p>Гостиниц и приютов рядом в OpenStreetMap не нашлось — поищите ночлег в "
              "ближайшем городе или возьмите палатку.</p>")


NATURE_PARTS = [about, how_to_get, route, days_section, stops_section, advice_section, photos_section,
                nature_section, voyage_more, sources]
URBAN_PARTS = [about, how_to_get, route, days_section, stops_section, advice_section, photos_section,
               voyage_more, sources]


def build_html(tour, images, pdf=False, basemap="light"):
    """Отчёт целиком. Структура зависит от типа туризма (природа / город).

    basemap — подложка интерактивных карт HTML (None — только статичные листы).
    """
    b = Builder(tour, images, pdf, None if pdf else basemap)
    css = PDF_CSS if pdf else WEB_CSS
    b.add("<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
          "<meta name='viewport' content='width=device-width,initial-scale=1'>",
          "<title>%s</title><style>%s</style>%s</head><body>" % (
              esc("%s — %s" % (tour.place.name, brand.NAME)), css,
              webmap.assets() if b.basemap is not None else ""))
    header(b)
    for part in URBAN_PARTS if tour.urban else NATURE_PARTS:
        part(b)
    b.add("</body></html>")
    return "\n".join(b.out)


def write_pdf(tour, images, path):
    """PDF A4 тем же содержимым (движок Qt — без браузера и сторонних библиотек)."""
    from qgis.PyQt.QtCore import QMarginsF, QSizeF, QUrl
    from qgis.PyQt.QtGui import QFont, QImage, QPageLayout, QPageSize, QTextDocument
    from qgis.PyQt.QtPrintSupport import QPrinter

    document = QTextDocument()
    for key, (data, _mime) in images.items.items():
        image = QImage.fromData(data)
        if not image.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl("img:" + key), image)
    document.setDefaultFont(QFont(brand.FONT, 10))
    # страница A4 с полями 12 мм в «экранных» пикселях Qt (96 точек на дюйм)
    px = 96 / 25.4
    document.setPageSize(QSizeF((210 - 24) * px, (297 - 26) * px))
    document.setDocumentMargin(0)
    document.setHtml(build_html(tour, images, pdf=True))
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                      QMarginsF(12, 12, 12, 14), QPageLayout.Unit.Millimeter))
    printer.setDocName("%s — %s" % (tour.place.name, brand.NAME))
    document.print_(printer)
    return path


def label(poi):
    if poi.order == 0:
        return "Старт"
    return "%d. %s" % (poi.order, poi.name)


def fmt_t(value):
    if value is None:
        return "?"
    return "%+d" % round(value)
