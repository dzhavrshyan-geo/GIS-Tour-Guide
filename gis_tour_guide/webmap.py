# -*- coding: utf-8 -*-
"""Интерактивные карты для HTML-отчёта (Leaflet встроен в файл; из сети — только подложка).

Область ограничена окрестностями маршрута, масштаб — от «всё видно» до улиц, поэтому
карта лёгкая: никаких лишних тайлов за пределами района.
"""

import json
import os

from . import brand
from .text import esc

VENDOR = os.path.join(os.path.dirname(__file__), "vendor", "leaflet")

TILES = {
    "topo": ("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", 17,
             "© OpenTopoMap (CC-BY-SA), © участники OpenStreetMap"),
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, "© участники OpenStreetMap"),
    "light": ("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", 19,
              "© участники OpenStreetMap, © CARTO"),
}

CSS = """
.wm{border:1px solid %(line)s;border-radius:10px;overflow:hidden;margin:10px 0;background:#fff}
.wm-head{background:%(green)s;color:#fff;padding:8px 14px;display:flex;justify-content:space-between;
align-items:center;border-bottom:3px solid %(route)s}
.wm-head b{font-size:16px}.wm-head span{color:%(sand)s;font-size:12px;letter-spacing:.4px}
.wm-map{height:520px;background:%(paper)s}
.wm-pin{width:24px;height:24px;border-radius:50%%;color:#fff;font:bold 12px Arial,sans-serif;
display:flex;align-items:center;justify-content:center;border:2px solid #fff;
box-shadow:0 1px 3px rgba(0,0,0,.45);box-sizing:border-box}
.wm-pin.photo{width:22px;height:22px;font-size:11px}
.wm-dot{width:10px;height:10px;border-radius:50%%;border:2px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.5)}
.wm-label{background:rgba(255,255,255,.85);border:none;box-shadow:none;color:%(green_dark)s;
font:bold 11px Arial,sans-serif;padding:0 3px}
.wm-legend{background:rgba(255,255,255,.95);border:1px solid %(line)s;border-radius:8px;padding:8px 10px;
font:12px Arial,sans-serif;color:%(ink)s;line-height:1.7}
.wm-legend b{color:%(green)s;display:block;border-bottom:2px solid %(route)s;margin-bottom:4px}
.wm-legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.wm-print summary{cursor:pointer;color:%(green)s;padding:6px 14px;font-size:13px}
.leaflet-popup-content{font:13px Arial,sans-serif;color:%(ink)s}
.leaflet-popup-content b{color:%(green)s}
""" % {"line": brand.LINE, "green": brand.GREEN, "route": brand.ROUTE, "sand": brand.SAND,
       "paper": brand.PAPER, "green_dark": brand.GREEN_DARK, "ink": brand.INK}

SCRIPT = r"""
function gtMap(id, d) {
  var map = L.map(id, {zoomSnap: 0.5, attributionControl: true});
  // сначала охват — Leaflet рисует линии только на карте с заданным видом
  var all = [];
  (d.lines || []).forEach(function (l) { if (d.fitLines) { all = all.concat(l.coords); } });
  (d.points || []).forEach(function (p) { if (p.fit !== false) { all.push([p.lat, p.lon]); } });
  var bounds = L.latLngBounds(all.length ? all : [[d.box[1], d.box[0]], [d.box[3], d.box[2]]]);
  map.fitBounds(bounds, {padding: [24, 24]});
  var fit = map.getZoom();
  var outer = d.outer ? L.latLngBounds([[d.outer[1], d.outer[0]], [d.outer[3], d.outer[2]]]) : null;
  // обзорная — можно отдалиться до всего региона; остальные — на шаг от маршрута
  map.setMinZoom(outer ? Math.min(map.getBoundsZoom(outer), fit - 1) : Math.max(fit - 1, 2));
  map.setMaxZoom(d.tiles[1]);
  map.setMaxBounds((outer ? outer.extend(bounds) : bounds).pad(outer ? 0.15 : 0.6));
  L.tileLayer(d.tiles[0], {maxZoom: d.tiles[1], attribution: d.tiles[2] + ' · ГИС-экскурсовод'}).addTo(map);
  function pin(cls, color, text) {
    return L.divIcon({className: '', iconSize: [24, 24], iconAnchor: [12, 12],
      html: '<div class="wm-pin ' + cls + '" style="background:' + color + '">' + text + '</div>'});
  }
  function dot(color) {
    return L.divIcon({className: '', iconSize: [10, 10], iconAnchor: [5, 5],
      html: '<div class="wm-dot" style="background:' + color + '"></div>'});
  }
  (d.region || []).forEach(function (g) {
    L.geoJSON(g, {style: {color: d.c.green_dark, weight: 2, fill: true, fillColor: '#fff', fillOpacity: 0.15}}).addTo(map);
  });
  (d.lines || []).forEach(function (l) {
    var color = l.kind === 'drive' ? d.c.drive : d.c.route;
    L.polyline(l.coords, {color: '#fff', weight: 8, opacity: 0.9}).addTo(map);
    L.polyline(l.coords, {color: color, weight: 4, dashArray: l.kind === 'offtrail' ? '8 7' : null})
      .bindTooltip(l.title || '', {sticky: true}).addTo(map);
  });
  (d.points || []).forEach(function (p) {
    var icon = p.style === 'dot' ? dot(p.color) : pin(p.cls || '', p.color, p.text);
    var m = L.marker([p.lat, p.lon], {icon: icon, title: p.name, riseOnHover: true,
                                      zIndexOffset: p.photo ? 1000 : (p.style === 'dot' ? -500 : 0)}).addTo(map);
    if (p.label) { m.bindTooltip(p.label, {permanent: true, direction: 'right', offset: [12, 0], className: 'wm-label'}); }
    if (p.photo) {
      m.bindPopup(function () {
        var img = document.getElementById('photo-' + p.photo);
        return '<b>Фото ' + p.photo + ' · ' + p.name + '</b><br>' +
          (img ? '<img src="' + img.src + '" style="width:220px;border-radius:6px;margin:4px 0"><br>' : '') +
          (p.popup || '') + '<br><a href="#card-' + p.photo + '">к снимку в отчёте ↓</a>';
      });
    } else if (p.popup) { m.bindPopup('<b>' + p.name + '</b><br>' + p.popup); }
  });
  L.control.scale({imperial: false}).addTo(map);
  var legend = L.control({position: 'bottomright'});
  legend.onAdd = function () {
    var div = L.DomUtil.create('div', 'wm-legend');
    div.innerHTML = '<b>Условные обозначения</b>' + d.legend.map(function (row) {
      return '<i style="' + row[0] + '"></i>' + row[1];
    }).join('<br>');
    return div;
  };
  legend.addTo(map);
}
"""


def assets():
    """CSS и JS Leaflet + фирменный стиль — один раз на отчёт."""
    with open(os.path.join(VENDOR, "leaflet.css"), encoding="utf-8") as handle:
        css = handle.read()
    with open(os.path.join(VENDOR, "leaflet.js"), encoding="utf-8") as handle:
        js = handle.read()
    return "<style>%s\n%s</style>\n<script>%s</script>\n<script>%s</script>" % (css, CSS, js, SCRIPT)


COLORS = {"green": brand.GREEN, "green_dark": brand.GREEN_DARK, "route": brand.ROUTE,
          "drive": brand.DRIVE, "pin": brand.PIN, "slate": brand.SLATE, "photo": brand.PHOTO}
LEGEND = {
    "stop": ("width:16px;height:16px;border-radius:50%%;background:%s;border:2px solid #fff;"
             "box-shadow:0 0 2px #0006" % brand.GREEN, "точки маршрута"),
    "main": ("width:16px;height:16px;border-radius:50%%;background:%s;border:2px solid #fff;"
             "box-shadow:0 0 2px #0006" % brand.PIN, "главный объект"),
    "trail": ("width:26px;height:4px;background:%s" % brand.ROUTE, "по тропам и улицам"),
    "offtrail": ("width:26px;height:0;border-top:4px dashed %s" % brand.ROUTE, "без троп, по рельефу"),
    "photo": ("width:16px;height:16px;border-radius:50%%;background:%s;border:2px solid #fff;"
              "box-shadow:0 0 2px #0006" % brand.PHOTO, "место съёмки, номер фото"),
    "drive": ("width:26px;height:4px;background:%s" % brand.DRIVE, "дорога на машине"),
    "capital": ("width:12px;height:12px;background:%s;border:2px solid #fff" % brand.GREEN_DARK,
                "региональный центр"),
    "town": ("width:10px;height:10px;border-radius:50%%;background:#fff;border:2px solid %s" % brand.GREEN_DARK,
             "населённые пункты"),
    "lodging": ("width:12px;height:12px;background:#8e5a1e;border:2px solid #fff", "ночлег"),
}


def latlon(coords):
    return [[round(c[1], 5), round(c[0], 5)] for c in coords]


def route_lines(tour):
    lines = []
    for number, leg in enumerate(tour.legs, 1):
        a, b = tour.stops[leg.a], tour.stops[leg.b]
        for segment in leg.segments:
            if len(segment.coords) > 1:
                lines.append({"kind": segment.kind, "coords": latlon(segment.coords),
                              "title": "Участок %d: %s → %s" % (number, a.name, b.name)})
    return lines


def stop_points(tour, small=False):
    points = []
    for poi in tour.stops:
        color = brand.PIN if poi.is_main else brand.SLATE if poi.order == 0 else brand.GREEN
        text = "С" if poi.order == 0 else str(poi.order)
        item = {"lat": poi.lat, "lon": poi.lon, "name": poi.name, "color": color,
                "popup": esc(poi.kind) + ("<br>" + esc((poi.extract or poi.description)[:220])
                                          if (poi.extract or poi.description) else "")}
        if small:
            item.update(style="dot", label="")
        else:
            item.update(text=text, label="%s. %s" % (text, poi.name) if poi.order else poi.name)
        points.append(item)
    return points


def scheme_data(tour, basemap):
    kinds = {s.kind for leg in tour.legs for s in leg.segments}
    legend = [LEGEND["main"], LEGEND["stop"], LEGEND["trail"]] + ([LEGEND["offtrail"]] if "offtrail" in kinds else [])
    return {"lines": route_lines(tour), "points": stop_points(tour), "legend": legend, "fitLines": True}


def photos_data(tour, basemap):
    points = stop_points(tour, small=True)
    for photo in tour.photos:
        if photo.lon is None:
            continue
        points.append({"lat": photo.lat, "lon": photo.lon, "name": photo.poi_name, "color": brand.PHOTO,
                       "cls": "photo", "text": str(photo.number), "photo": photo.number,
                       "popup": esc(photo.description[:160])})
    for point in points[:len(tour.stops)]:
        point["fit"] = False
    return {"lines": route_lines(tour), "points": points,
            "legend": [LEGEND["photo"], LEGEND["stop"], LEGEND["trail"]]}


def overview_data(tour, basemap):
    points, legend = [], []
    region = []
    if tour.region_geojson:
        region.append(round_geojson(tour.region_geojson))
    lines = []
    if tour.drive and tour.drive.ok and len(tour.drive.coords) > 1:
        step = max(1, len(tour.drive.coords) // 800)
        lines.append({"kind": "drive", "coords": latlon(tour.drive.coords[::step] + [tour.drive.coords[-1]]),
                      "title": "Дорога от %s" % tour.drive.start_name})
        legend.append(LEGEND["drive"])
    for item in tour.settlements:
        if item.role == "capital":
            points.append({"lat": item.lat, "lon": item.lon, "name": item.name, "style": "dot",
                           "color": brand.GREEN_DARK, "label": item.name})
        else:
            points.append({"lat": item.lat, "lon": item.lon, "name": item.name, "style": "dot",
                           "color": "#fff" if item.role == "major" else brand.SLATE,
                           "label": item.name if item.role == "major" or len(tour.settlements) < 12 else "",
                           "fit": item.role != "major"})
    for item in tour.lodging:
        points.append({"lat": item["lat"], "lon": item["lon"], "name": item["name"], "style": "dot",
                       "color": "#8e5a1e", "label": "",
                       "popup": "%s · %s · %.1f км" % (esc(item["label"]), esc(item["kind"]), item["km"])})
    if tour.lodging:
        legend.append(LEGEND["lodging"])
    points.append({"lat": tour.place.lat, "lon": tour.place.lon, "name": tour.place.name,
                   "color": brand.PIN, "text": "★", "label": tour.place.name})
    legend = [LEGEND["main"], LEGEND["capital"], LEGEND["town"]] + legend
    data = {"lines": lines, "points": points, "region": region, "legend": legend, "fitLines": True}
    box = tour.region_bbox
    if box and box[2] - box[0] < 40 and box[3] - box[1] < 25:   # не через 180-й меридиан
        data["outer"] = list(box)
    return data


def round_geojson(value):
    if isinstance(value, dict):
        return {k: round_geojson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [round_geojson(v) for v in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


BUILDERS = {"overview": overview_data, "scheme": scheme_data, "photos": photos_data}


def block(key, tour, basemap, title, sheet, print_src=""):
    """HTML одной интерактивной карты; print_src — картинка фирменного листа для печати."""
    data = BUILDERS[key](tour, basemap)
    if key == "photos" and not any(p.get("photo") for p in data["points"]):
        return ""
    tiles = TILES.get(basemap if basemap in TILES else "light")
    data.update(tiles=list(tiles), c=COLORS, box=list(tour.bbox))
    map_id = "wm-" + key
    printed = ("<details class='wm-print'><summary>Лист для печати (как в PDF)</summary>"
               "<img src='%s' style='width:100%%'></details>" % print_src) if print_src else ""
    return ("<div class='wm'><div class='wm-head'><b>%s</b><span>%s</span></div>"
            "<div class='wm-map' id='%s'></div>%s</div>"
            "<script>gtMap('%s', %s);</script>") % (
                esc(title), esc(sheet.upper()), map_id, printed, map_id,
                json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
