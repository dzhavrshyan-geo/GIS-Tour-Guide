# -*- coding: utf-8 -*-
"""Офлайн-пакет для телефона: собственная карта района (MBTiles), трек, отчёт — одним архивом.

Сторонние подложки массово скачивать нельзя (правила OSM, OpenTopoMap), поэтому карта
рисуется самим QGIS: дороги, тропы, вода, лес и здания из OpenStreetMap, рельеф и маршрут.
"""

import os
import sqlite3
import zipfile

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
    QgsMapRendererCustomPainterJob, QgsMapSettings, QgsPointXY, QgsRectangle,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from . import brand, geo, maps
from .report import image_bytes

WORLD = 40075016.68557849
META = 4                      # рисуем блоками 4×4 тайла — в 16 раз меньше отрисовок
MAX_TILES = 3000

# вид → (геометрия, свойства символа); порядок — снизу вверх
BASE_STYLE = [
    ("residential", "Polygon", {"color": "#efe9df", "outline_style": "no"}),
    ("forest", "Polygon", {"color": "#cfe2c3", "outline_style": "no"}),
    ("park", "Polygon", {"color": "#dcebd0", "outline_style": "no"}),
    ("rock", "Polygon", {"color": "#e4ded4", "outline_style": "no"}),
    ("wetland", "Polygon", {"color": "#d6e6e2", "outline_color": "#9cc2bb", "outline_width": "0.2",
                            "style": "dense6"}),
    ("glacier", "Polygon", {"color": "#f2f7fb", "outline_color": "#a9c6dc", "outline_width": "0.3"}),
    ("water", "Polygon", {"color": "#a9cfe6", "outline_color": "#7fb2d3", "outline_width": "0.2"}),
    ("building", "Polygon", {"color": "#d8cfc2", "outline_color": "#b9ad9c", "outline_width": "0.1"}),
    ("river", "LineString", {"line_color": "#7fb2d3", "line_width": "0.6"}),
    ("path", "LineString", {"line_color": "#8b5a2b", "line_width": "0.35", "line_style": "dash"}),
    ("road", "LineString", {"line_color": "#ffffff", "line_width": "0.9"}),
    ("major", "LineString", {"line_color": "#f3c96b", "line_width": "1.4"}),
]


def base_layers(features):
    """Слои офлайн-подложки (в памяти, в проект не добавляются): (площадные, линейные), снизу вверх."""
    polygons, lines = [], []
    for kind, geometry, props in BASE_STYLE:
        items = features.get(kind) or []
        rows = []
        for _tags, coords, closed in items:
            if geometry == "Polygon":
                if not closed:
                    continue
                shape = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in coords]])
            else:
                shape = maps.line(coords)
            rows.append((shape, {"kind": kind}))
        if not rows:
            continue
        layer = maps.memory_layer(geometry, kind, [("kind", str)], rows)
        symbol = (QgsFillSymbol if geometry == "Polygon" else QgsLineSymbol).createSimple(props)
        if kind in ("road", "major"):
            casing = QgsLineSymbol.createSimple({"line_color": "#c9bfae", "line_width": str(
                float(props["line_width"]) + 0.4)})
            casing.appendSymbolLayer(symbol.symbolLayer(0).clone())
            symbol = casing
        layer.renderer().setSymbol(symbol)
        (polygons if geometry == "Polygon" else lines).append(layer)
    return polygons, lines


def stack(route_layers, features, hillshade=None):
    """Порядок для QgsMapSettings (сверху вниз): маршрут, дороги и реки, отмывка, заливки."""
    polygons, lines = base_layers(features)
    return route_layers + lines[::-1] + ([hillshade] if hillshade is not None else []) + polygons[::-1]


def tile_range(bbox, z):
    x0, y0 = geo.lonlat_to_tile(bbox[0], bbox[3], z)
    x1, y1 = geo.lonlat_to_tile(bbox[2], bbox[1], z)
    return int(x0), int(y0), int(x1), int(y1)


def count_tiles(bbox, zmin, zmax):
    total = 0
    for z in range(zmin, zmax + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def zoom_range(bbox, urban=False, max_tiles=MAX_TILES):
    """Уровни масштаба: от «весь район» до подробного, сколько поместится в max_tiles."""
    zmin = 11 if urban else 9
    zmax = 18 if urban else 16
    while zmax > zmin and count_tiles(bbox, zmin, zmax) > max_tiles:
        zmax -= 1
    return zmin, zmax


def render_mbtiles(layers, bbox, path, zmin, zmax, name, progress=None, background=brand.PAPER):
    """Отрисовать тайлы района в MBTiles (PNG, схема TMS)."""
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    db.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    db.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
    meta = {"name": name, "type": "baselayer", "version": "1.0", "format": "png",
            "description": "%s — офлайн-карта района (ГИС-экскурсовод)" % name,
            "attribution": "© участники OpenStreetMap; рельеф — Terrain Tiles",
            "bounds": "%.6f,%.6f,%.6f,%.6f" % bbox, "minzoom": str(zmin), "maxzoom": str(zmax),
            "center": "%.6f,%.6f,%d" % ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, zmin + 2)}
    db.executemany("INSERT INTO metadata VALUES (?, ?)", list(meta.items()))
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setOutputSize(QSize(256 * META, 256 * META))
    settings.setOutputDpi(96)
    settings.setBackgroundColor(QColor(background))
    total = count_tiles(bbox, zmin, zmax)
    done = 0
    for z in range(zmin, zmax + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        size = WORLD / 2 ** z
        for bx in range(x0, x1 + 1, META):
            for by in range(y0, y1 + 1, META):
                west = -WORLD / 2 + bx * size
                north = WORLD / 2 - by * size
                settings.setExtent(QgsRectangle(west, north - META * size, west + META * size, north))
                image = QImage(256 * META, 256 * META, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(QColor(background))
                painter = QPainter(image)
                job = QgsMapRendererCustomPainterJob(settings, painter)
                job.renderSynchronously()
                painter.end()
                rows = []
                for dx in range(META):
                    for dy in range(META):
                        x, y = bx + dx, by + dy
                        if x > x1 or y > y1:
                            continue
                        tile = image.copy(dx * 256, dy * 256, 256, 256)
                        rows.append((z, x, 2 ** z - 1 - y, sqlite3.Binary(image_bytes(tile, "PNG"))))
                db.executemany("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", rows)
                done += len(rows)
                if progress:
                    progress(done / float(max(total, 1)))
    db.commit()
    db.close()
    return path


README = """ГИС-экскурсовод — офлайн-пакет «{name}»

Что внутри
  report.html     отчёт (откройте в браузере телефона; интерактивным картам нужен интернет,
                  листы карт для печати есть внутри и без него)
  report.pdf      тот же отчёт для печати
  {slug}.gpx      трек и точки — OsmAnd, Locus, большинство навигаторов
  {slug}.kml      трек и точки — Organic Maps, Maps.me, Google Earth
  {slug}.mbtiles  офлайн-карта района ({zmin}–{zmax} масштаб) — OsmAnd, Locus Map, Guru Maps

Как открыть на телефоне
  Organic Maps / Maps.me: откройте .kml (или .gpx) из файлов — появится список меток и трек.
  OsmAnd: откройте .gpx — трек; .mbtiles скопируйте в папку OsmAnd/tiles и выберите
          «Настроить карту → Источник карты».
  Locus Map: откройте .mbtiles и .gpx из файлов — карта и трек загрузятся сами.

Карта нарисована по данным OpenStreetMap (© участники OpenStreetMap, ODbL).
Перед поездкой проверьте дороги, погоду и правила посещения.
"""


def make_package(folder, slug, tour, gpx, kml, mbtiles, zmin, zmax):
    """Архив <папка>/<slug>_offline.zip со всем нужным в дороге."""
    path = os.path.join(folder, "%s_offline.zip" % slug)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("report.html", "report.pdf"):
            source = os.path.join(folder, name)
            if os.path.exists(source):
                archive.write(source, name)
        archive.writestr("%s.gpx" % slug, gpx)
        archive.writestr("%s.kml" % slug, kml)
        if mbtiles and os.path.exists(mbtiles):
            archive.write(mbtiles, "%s.mbtiles" % slug, compress_type=zipfile.ZIP_STORED)
        archive.writestr("README.txt", README.format(name=tour.place.name, slug=slug, zmin=zmin, zmax=zmax))
    return path


def area(tour):
    """Район офлайн-карты: маршрут, точки и ночлег с полями."""
    points = [c for leg in tour.legs for s in leg.segments for c in s.coords]
    points += [s.point for s in tour.stops] + [(l["lon"], l["lat"]) for l in tour.lodging if l["km"] < 15]
    return maps.padded(points, 0.15, 1000 if tour.urban else 3000)
