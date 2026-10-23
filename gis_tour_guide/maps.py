# -*- coding: utf-8 -*-
"""Слои экскурсии в QGIS (GeoPackage + стили), макеты двух карт и их экспорт."""

import json
import math
import os
import urllib.parse

from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsFontMarkerSymbolLayer, QgsGeometry, QgsHillshadeRenderer,
    QgsLayoutExporter, QgsLayoutFrame, QgsLayoutItemAttributeTable, QgsLayoutItemLabel,
    QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemMapOverview, QgsLayoutItemPage, QgsLayoutItemPicture,
    QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsLineSymbol, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsPointXY, QgsPrintLayout, QgsProject, QgsProperty, QgsRasterLayer,
    QgsRectangle, QgsRendererCategory, QgsSymbolLayer, QgsTextBufferSettings, QgsTextFormat,
    QgsUnitTypes, QgsVectorFileWriter, QgsWkbTypes, QgsCoordinateTransformContext, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
    QgsFillSymbol, QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont

from .geo import bbox_of, bbox_union
from .text import fmt_hours, fmt_km

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")

BASEMAPS = {
    "topo": ("OpenTopoMap", "https://tile.opentopomap.org/{z}/{x}/{y}.png", 17,
             "Подложка © OpenTopoMap (CC-BY-SA), данные © участники OpenStreetMap, SRTM"),
    "osm": ("OpenStreetMap", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19,
            "Подложка и данные © участники OpenStreetMap"),
}
GREEN, RED, BLUE, DARK = "#2f5d3a", "#c0392b", "#2c7fb8", "#34495e"


def enum(owner, *names):
    """Значение перечисления под новым или старым именем (QGIS 3.22 … 3.40)."""
    for name in names:
        value = owner
        try:
            for part in name.split("."):
                value = getattr(value, part)
            return value
        except AttributeError:
            continue
    raise AttributeError(names[0])


# --- Слои ---

FIELD_TYPES = {int: QVariant.Int, float: QVariant.Double, str: QVariant.String, bool: QVariant.Bool}


def memory_layer(kind, name, fields, rows):
    """rows — [(QgsGeometry, {поле: значение})]."""
    layer = QgsVectorLayer("%s?crs=EPSG:4326" % kind, name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField(f, FIELD_TYPES[t]) for f, t in fields])
    layer.updateFields()
    features = []
    for geometry, values in rows:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        for key, value in values.items():
            feature[key] = value
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def point(lon, lat):
    return QgsGeometry.fromPointXY(QgsPointXY(lon, lat))


def line(coords):
    return QgsGeometry.fromPolylineXY([QgsPointXY(c[0], c[1]) for c in coords])


def geojson_geometry(value):
    try:
        from osgeo import ogr
        geometry = ogr.CreateGeometryFromJson(json.dumps(value))
        return QgsGeometry.fromWkt(geometry.ExportToWkt()) if geometry else None
    except Exception:  # noqa: BLE001 - без GDAL/кривая геометрия: без границы региона
        return None


def build_layers(tour, folder):
    """Слои в памяти: {ключ: QgsVectorLayer}."""
    layers = {}
    stops = []
    for poi in tour.stops:
        stops.append((point(poi.lon, poi.lat), {
            "order": poi.order, "name": poi.name, "kind": poi.kind,
            "elevation": float(poi.elevation) if poi.elevation is not None else None,
            "is_main": poi.is_main, "description": poi.extract or poi.description,
            "url": poi.url, "photo": photo_path(poi)}))
    layers["stops"] = memory_layer("Point", "Точки маршрута", [
        ("order", int), ("name", str), ("kind", str), ("elevation", float), ("is_main", bool),
        ("description", str), ("url", str), ("photo", str)], stops)

    others = [(point(p.lon, p.lat), {"name": p.name, "kind": p.kind, "score": float(p.score),
                                     "distance_km": round(p.distance_km, 2), "url": p.url})
              for p in tour.candidates if not p.order and not p.is_main]
    layers["others"] = memory_layer("Point", "Другие объекты рядом", [
        ("name", str), ("kind", str), ("score", float), ("distance_km", float), ("url", str)], others)

    walk = []
    for number, leg in enumerate(tour.legs, 1):
        a, b = tour.stops[leg.a], tour.stops[leg.b]
        for segment in leg.segments:
            if len(segment.coords) > 1:
                walk.append((line(segment.coords), {
                    "leg": number, "kind": segment.kind, "from_stop": a.name, "to_stop": b.name,
                    "length_m": round(segment.length_m)}))
    layers["walk"] = memory_layer("LineString", "Пеший маршрут", [
        ("leg", int), ("kind", str), ("from_stop", str), ("to_stop", str), ("length_m", int)], walk)

    drive = tour.drive
    if drive and drive.ok and len(drive.coords) > 1:
        layers["drive"] = memory_layer("LineString", "Дорога на машине", [
            ("start", str), ("length_km", float), ("hours", float)],
            [(line(drive.coords), {"start": drive.start_name,
                                   "length_km": round(drive.distance_m / 1000, 1),
                                   "hours": round(drive.duration_s / 3600, 2)})])
        layers["town"] = memory_layer("Point", "Откуда едем", [("name", str)],
                                      [(point(*drive.start), {"name": drive.start_name})])

    layers["object"] = memory_layer("Point", "Объект экскурсии", [("name", str), ("kind", str)], [
        (point(tour.place.lon, tour.place.lat), {"name": tour.place.name, "kind": tour.place.kind})])

    west, south, east, north = tour.bbox
    zone = QgsGeometry.fromRect(QgsRectangle(west, south, east, north))
    layers["zone"] = memory_layer("Polygon", "Зона поиска ±%d км" % tour.half_km,
                                  [("half_km", float)], [(zone, {"half_km": float(tour.half_km)})])
    if tour.region_geojson:
        geometry = geojson_geometry(tour.region_geojson)
        if geometry and not geometry.isEmpty():
            layers["region"] = memory_layer("MultiPolygon", tour.region_name or "Регион",
                                            [("name", str)], [(geometry, {"name": tour.region_name})])

    photos = [(point(p.lon, p.lat), {"title": p.title, "description": p.description,
                                     "author": p.author, "license": p.license,
                                     "photo": p.local_path, "page": p.page_url})
              for p in tour.photos if p.lon is not None and p.local_path]
    if photos:
        layers["photos"] = memory_layer("Point", "Фотографии (Викисклад)", [
            ("title", str), ("description", str), ("author", str), ("license", str),
            ("photo", str), ("page", str)], photos)
    return layers


def photo_path(poi):
    if poi.image_path:
        return poi.image_path
    for photo in poi.photos:
        if photo.local_path:
            return photo.local_path
    return ""


def save_gpkg(layers, path, log=None):
    """Слои из памяти → GeoPackage; возвращает {ключ: слой из файла}."""
    saved = {}
    first = True
    for key, layer in layers.items():
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = key
        options.fileEncoding = "UTF-8"
        options.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                        else QgsVectorFileWriter.CreateOrOverwriteLayer)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsCoordinateTransformContext(), options)
        error = result[0]
        if error != QgsVectorFileWriter.NoError:
            if log:
                log("⚠ Не записан слой %s: %s" % (key, result[1]))
            saved[key] = layer
            continue
        first = False
        loaded = QgsVectorLayer("%s|layername=%s" % (path, key), layer.name(), "ogr")
        saved[key] = loaded if loaded.isValid() else layer
    return saved


def write_dem(grid, path):
    """ElevationGrid → GeoTIFF (EPSG:4326)."""
    import numpy as np
    from osgeo import gdal, osr
    driver = gdal.GetDriverByName("GTiff")
    data = driver.Create(path, grid.cols, grid.rows, 1, gdal.GDT_Float32, ["COMPRESS=DEFLATE"])
    data.SetGeoTransform([grid.west, grid.step_lon, 0, grid.north, 0, -grid.step_lat])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    data.SetProjection(srs.ExportToWkt())
    band = data.GetRasterBand(1)
    values = np.where(np.isnan(grid.values), -32768, grid.values).astype(np.float32)
    band.WriteArray(values)
    band.SetNoDataValue(-32768)
    data.FlushCache()
    data = None
    return path


# --- Стили ---

def text_format(size=9, color="#1d2a22", bold=False, buffer_=True):
    fmt = QgsTextFormat()
    font = QFont("Arial")
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    if buffer_:
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.9)
        buf.setColor(QColor(255, 255, 255, 230))
        fmt.setBuffer(buf)
    return fmt


def set_labels(layer, field_or_expression, size=9, color="#1d2a22", bold=False, is_expression=False,
               distance=1.6):
    settings = QgsPalLayerSettings()
    settings.fieldName = field_or_expression
    settings.isExpression = is_expression
    settings.setFormat(text_format(size, color, bold))
    if layer.geometryType() == enum(QgsWkbTypes, "PointGeometry", "GeometryType.Point"):
        settings.placement = enum(QgsPalLayerSettings, "AroundPoint", "Placement.AroundPoint")
        settings.dist = distance
    else:
        settings.placement = enum(QgsPalLayerSettings, "Line", "Placement.Line")
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def symbol_property(name):
    return enum(QgsSymbolLayer, "Property." + name, "Property" + name)


def style_stops(layer):
    symbol = QgsMarkerSymbol.createSimple({
        "name": "circle", "color": GREEN, "outline_color": "white", "outline_width": "0.5",
        "size": "6.2"})
    symbol.symbolLayer(0).setDataDefinedProperty(symbol_property("FillColor"), QgsProperty.fromExpression(
        "if(\"is_main\", '%s', if(\"order\" = 0, '%s', '%s'))" % (RED, DARK, GREEN)))
    number = QgsFontMarkerSymbolLayer("Arial", "1", 3.4, QColor("white"))
    number.setFontStyle("Bold")
    number.setDataDefinedProperty(symbol_property("Character"), QgsProperty.fromExpression(
        "if(\"order\" = 0, 'С', to_string(\"order\"))"))
    symbol.appendSymbolLayer(number)
    layer.renderer().setSymbol(symbol)
    set_labels(layer, "name", 9, "#1d2a22", True, distance=2.2)
    layer.setMapTipTemplate(
        "<b>[% if(\"order\"=0,'Старт',to_string(\"order\")) %]. [% \"name\" %]</b><br>"
        "<i>[% \"kind\" %]</i>[% if(\"elevation\" is not null, ', ' || round(\"elevation\") || ' м', '') %]<br>"
        "[% if(\"photo\" <> '', '<img src=\"file:///' || replace(\"photo\", '\\\\', '/') || "
        "'\" width=\"300\"><br>', '') %][% left(\"description\", 600) %]")


def style_object(layer):
    symbol = QgsMarkerSymbol.createSimple({
        "name": "star", "color": RED, "outline_color": "white", "outline_width": "0.5", "size": "7"})
    layer.renderer().setSymbol(symbol)
    set_labels(layer, "name", 10, RED, True, distance=2.5)


def style_others(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "#ffffff", "outline_color": "#7f8c8d", "outline_width": "0.4",
        "size": "2.4"}))
    set_labels(layer, "name", 7, "#5d6d64")


def style_walk(layer):
    trail = QgsLineSymbol.createSimple({"line_color": "#d35400", "line_width": "0.9",
                                        "capstyle": "round", "joinstyle": "round"})
    offtrail = QgsLineSymbol.createSimple({"line_color": "#8e44ad", "line_width": "0.8",
                                           "line_style": "dash", "capstyle": "round"})
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("trail", trail, "по дорогам и тропам"),
        QgsRendererCategory("offtrail", offtrail, "без троп, по рельефу"),
    ]))


def style_drive(layer):
    layer.renderer().setSymbol(QgsLineSymbol.createSimple({
        "line_color": BLUE, "line_width": "1.1", "capstyle": "round", "joinstyle": "round"}))


def style_town(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "square", "color": BLUE, "outline_color": "white", "size": "4"}))
    set_labels(layer, "name", 9, BLUE, True)


def style_zone(layer):
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "outline_color": "#555555", "outline_width": "0.5",
        "outline_style": "dash"}))


def style_region(layer):
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,40", "outline_color": "#4d4d4d", "outline_width": "0.6"}))


def style_photos(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "triangle", "color": "#f39c12", "outline_color": "white", "size": "2.8"}))
    layer.setMapTipTemplate(
        "[% if(\"photo\" <> '', '<img src=\"file:///' || replace(\"photo\", '\\\\', '/') || "
        "'\" width=\"320\"><br>', '') %][% left(\"description\", 300) %]<br>"
        "<small>© [% \"author\" %], [% \"license\" %]</small>")


def style_dem(layer, kind):
    provider = layer.dataProvider()
    if kind == "hillshade":
        renderer = QgsHillshadeRenderer(provider, 1, 315, 45)
        renderer.setZFactor(1.5)
        layer.setRenderer(renderer)
        layer.renderer().setOpacity(0.55)
        return
    stats = provider.bandStatistics(1)
    low, high = stats.minimumValue, stats.maximumValue
    shader = QgsColorRampShader(low, high)
    shader.setColorRampType(enum(QgsColorRampShader, "Interpolated", "Type.Interpolated"))
    colors = ["#6aa86a", "#b5cf8a", "#e8dfa0", "#c9a477", "#a07858", "#ffffff"]
    shader.setColorRampItemList([QgsColorRampShader.ColorRampItem(
        low + (high - low) * i / (len(colors) - 1), QColor(c)) for i, c in enumerate(colors)])
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader))


STYLES = {"stops": style_stops, "object": style_object, "others": style_others,
          "walk": style_walk, "drive": style_drive, "town": style_town, "zone": style_zone,
          "region": style_region, "photos": style_photos}
ORDER = ["stops", "object", "photos", "others", "walk", "drive", "town", "zone", "region"]


def basemap_layer(key):
    name, url, zmax, _ = BASEMAPS[key]
    uri = "type=xyz&url=%s&zmax=%d&zmin=0" % (urllib.parse.quote(url, safe=":/"), zmax)
    layer = QgsRasterLayer(uri, name, "wms")
    return layer if layer.isValid() else None


def add_to_project(tour, layers, dem_path=None, basemap="topo", project=None):
    """Группа «Экскурсия: …» со слоями. Возвращает (группа, {ключ: слой})."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    title = "Экскурсия: %s" % tour.place.name
    old = root.findGroup(title)
    if old:
        for layer in old.findLayers():
            project.removeMapLayer(layer.layerId())
        root.removeChildNode(old)
    group = root.insertGroup(0, title)
    added = {}
    for key in ORDER:
        layer = layers.get(key)
        if layer is None:
            continue
        STYLES[key](layer)
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        added[key] = layer
    if dem_path and os.path.exists(dem_path):
        for kind, name in (("hillshade", "Отмывка рельефа"), ("hypsometry", "Высоты (ЦМР)")):
            raster = QgsRasterLayer(dem_path, name)
            if raster.isValid():
                style_dem(raster, kind)
                project.addMapLayer(raster, False)
                node = group.addLayer(raster)
                added[kind] = raster
                if basemap != "relief":
                    node.setItemVisibilityChecked(False)
    if basemap in BASEMAPS:
        base = basemap_layer(basemap)
        if base:
            project.addMapLayer(base, False)
            group.addLayer(base)
            added["basemap"] = base
    if not project.crs().isValid() or len(project.mapLayers()) == len(added):
        project.setCrs(MERCATOR)
    return group, added


# --- Макеты ---

def mercator_rect(bbox, project):
    transform = QgsCoordinateTransform(WGS84, MERCATOR, project)
    return transform.transformBoundingBox(QgsRectangle(*bbox))


def fit_aspect(rect, width, height, margin=0.06):
    rect = QgsRectangle(rect)
    rect.scale(1 + margin)
    target = width / height
    if rect.width() / max(rect.height(), 1) > target:
        grow = rect.width() / target - rect.height()
        rect.setYMinimum(rect.yMinimum() - grow / 2)
        rect.setYMaximum(rect.yMaximum() + grow / 2)
    else:
        grow = rect.height() * target - rect.width()
        rect.setXMinimum(rect.xMinimum() - grow / 2)
        rect.setXMaximum(rect.xMaximum() + grow / 2)
    return rect


def add_label(layout, text, x, y, w, h, size, bold=False, color="#1d2a22"):
    item = QgsLayoutItemLabel(layout)
    item.setText(text)
    fmt = text_format(size, color, bold, buffer_=False)
    if hasattr(item, "setTextFormat"):
        item.setTextFormat(fmt)
    else:
        item.setFont(fmt.toQFont())
        item.setFontColor(QColor(color))
    item.attemptMove(QgsLayoutPoint(x, y))
    item.attemptResize(QgsLayoutSize(w, h))
    layout.addLayoutItem(item)
    return item


def new_layout(project, name):
    manager = project.layoutManager()
    old = manager.layoutByName(name)
    if old:
        manager.removeLayout(old)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    page = layout.pageCollection().page(0)
    page.setPageSize("A4", enum(QgsLayoutItemPage, "Landscape", "Orientation.Landscape"))
    manager.addLayout(layout)
    return layout


def add_map(layout, layers, rect_3857, x, y, w, h):
    item = QgsLayoutItemMap(layout)
    item.attemptMove(QgsLayoutPoint(x, y))
    item.attemptResize(QgsLayoutSize(w, h))
    item.setCrs(MERCATOR)
    item.setLayers(layers)
    item.setKeepLayerSet(True)
    item.setFrameEnabled(True)
    layout.addLayoutItem(item)
    item.setExtent(fit_aspect(rect_3857, w, h))
    return item


def add_legend(layout, map_item, layers, x, y, w=None):
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Условные обозначения")
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.clear()
    for layer in layers:
        root.addLayer(layer)
    legend.setBackgroundEnabled(True)
    legend.setBackgroundColor(QColor(255, 255, 255, 225))
    legend.setFrameEnabled(True)
    legend.attemptMove(QgsLayoutPoint(x, y))
    layout.addLayoutItem(legend)
    legend.adjustBoxSize()
    return legend


def add_scalebar(layout, map_item, x, y):
    bar = QgsLayoutItemScaleBar(layout)
    bar.setStyle("Single Box")
    bar.setLinkedMap(map_item)
    bar.setUnits(enum(QgsUnitTypes, "DistanceKilometers", "DistanceUnit.Kilometers"))
    bar.setUnitLabel("км")
    bar.setNumberOfSegmentsLeft(0)
    bar.setNumberOfSegments(4)
    width_km = map_item.extent().width() / 1000.0 * math.cos(math.radians(
        mercator_lat(map_item.extent().center().y())))
    bar.setUnitsPerSegment(nice_step(width_km / 12))
    bar.setBackgroundEnabled(True)
    bar.setBackgroundColor(QColor(255, 255, 255, 210))
    bar.attemptMove(QgsLayoutPoint(x, y))
    layout.addLayoutItem(bar)
    bar.update()
    return bar


def mercator_lat(y):
    return math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)


def nice_step(value):
    if value <= 0:
        return 1
    power = 10 ** math.floor(math.log10(value))
    for k in (1, 2, 2.5, 5, 10):
        if value <= k * power:
            return k * power
    return 10 * power


def add_north(layout, map_item, x, y, size=14):
    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    picture.setLinkedMap(map_item)
    picture.attemptMove(QgsLayoutPoint(x, y))
    picture.attemptResize(QgsLayoutSize(size, size))
    picture.setBackgroundEnabled(True)
    picture.setBackgroundColor(QColor(255, 255, 255, 190))
    layout.addLayoutItem(picture)
    return picture


def add_stops_table(layout, layer, x, y, w, h):
    table = QgsLayoutItemAttributeTable.create(layout)
    table.setVectorLayer(layer)
    table.setDisplayedFields(["order", "name", "kind"])
    columns = table.columns()
    for column, heading in zip(columns, ("№", "Объект", "Тип")):
        column.setHeading(heading)
    table.setColumns(columns)
    if hasattr(table, "setContentTextFormat"):
        table.setContentTextFormat(text_format(7, buffer_=False))
        table.setHeaderTextFormat(text_format(7, bold=True, buffer_=False))
    table.setMaximumNumberOfFeatures(40)
    layout.addMultiFrame(table)
    frame = QgsLayoutFrame(layout, table)
    frame.attemptMove(QgsLayoutPoint(x, y))
    frame.attemptResize(QgsLayoutSize(w, h))
    table.addFrame(frame)
    return table


def attribution(tour, basemap):
    parts = ["Данные © участники OpenStreetMap (ODbL)"]
    if basemap in BASEMAPS:
        parts = [BASEMAPS[basemap][3]]
    if tour.dem_name:
        parts.append("рельеф: " + tour.dem_name)
    parts.append("маршруты: OSRM/FOSSGIS · ГИС-экскурсовод для QGIS")
    return "; ".join(parts)


def overview_extent(tour):
    points = [tour.place.point]
    if tour.drive and tour.drive.ok:
        points += tour.drive.coords[::max(1, len(tour.drive.coords) // 200)] + [tour.drive.start]
    box = bbox_of(points, 15000 if len(points) > 1 else 120000)
    region = tour.region_bbox
    # регион через 180-й меридиан (Чукотка) или огромный — не растягиваем на полмира
    if region and region[2] - region[0] < 40 and region[3] - region[1] < 25:
        box = bbox_union(box, region)
    return box


def approach_extent(tour):
    """Подъезд крупнее: дорога от города и объект."""
    points = [tour.place.point]
    if tour.drive and tour.drive.ok:
        points += tour.drive.coords + [tour.drive.start]
    return bbox_of(points, 8000)


def area(box):
    return max(box[2] - box[0], 1e-9) * max(box[3] - box[1], 1e-9)


def scheme_extent(tour):
    coords = [c for leg in tour.legs for s in leg.segments for c in s.coords]
    box = tour.bbox
    if coords:
        box = bbox_union(box, bbox_of(coords, 300))
    return box


def make_layouts(tour, added, folder, basemap="topo", project=None, export=True, log=None):
    """Два макета: обзорная карта региона и карта-схема окрестностей. → {ключ: путь к PNG}."""
    project = project or QgsProject.instance()
    result = {}
    name = tour.place.name
    base = [added["basemap"]] if "basemap" in added else []
    relief = [added[k] for k in ("hillshade", "hypsometry") if k in added]

    # 1. Обзорная карта
    layout = new_layout(project, "Экскурсия «%s» — 1. Обзорная карта" % name)
    keys = ["object", "town", "drive", "zone", "region"]
    layers = [added[k] for k in keys if k in added] + base
    map_item = add_map(layout, layers, mercator_rect(overview_extent(tour), project), 8, 24, 281, 172)
    add_label(layout, "%s — как добраться" % name, 8, 5, 230, 10, 18, True, GREEN)
    drive = tour.drive
    sub = tour.region_name or tour.place.region
    if drive and drive.ok:
        sub = "%s · от %s %s на машине, ~%s" % (sub, drive.start_name, fmt_km(drive.distance_m),
                                                fmt_hours(drive.duration_s / 3600))
    add_label(layout, sub, 8, 15, 270, 7, 10, False, "#4d5a52")
    add_legend(layout, map_item, [added[k] for k in keys if k in added], 11, 27)
    add_scalebar(layout, map_item, 11, 182)
    add_north(layout, map_item, 271, 27)
    approach = approach_extent(tour)
    if area(overview_extent(tour)) > 20 * area(approach):
        # регион огромный — дорога теряется: врезка с подъездом и её рамка на карте
        inset = add_map(layout, layers, mercator_rect(approach, project), 196, 113, 90, 80)
        inset.setBackgroundColor(QColor("white"))
        frame = QgsLayoutItemMapOverview("Врезка", map_item)
        map_item.overviews().addOverview(frame)
        frame.setLinkedMap(inset)
        frame.setFrameSymbol(QgsFillSymbol.createSimple({
            "color": "192,57,43,50", "outline_color": RED, "outline_width": "0.5"}))
        add_label(layout, "Подъезд крупнее", 197, 108, 88, 5, 8, True, RED)
    add_label(layout, attribution(tour, "osm" if basemap == "osm" else basemap), 8, 198, 281, 6, 6.5,
              False, "#5d6d64")
    result["overview_layout"] = layout

    # 2. Карта-схема окрестностей
    layout = new_layout(project, "Экскурсия «%s» — 2. Карта-схема маршрута" % name)
    keys = ["stops", "photos", "others", "walk", "zone"]
    layers = [added[k] for k in keys if k in added]
    scheme_layers = layers + (relief if basemap == "relief" else []) + base
    map_item = add_map(layout, scheme_layers, mercator_rect(scheme_extent(tour), project),
                       8, 24, 200, 172)
    add_label(layout, "%s — маршрут экскурсии" % name, 8, 5, 280, 10, 18, True, GREEN)
    t = tour.totals
    sub = "%s, %s, ходового времени ~%s" % (
        "кольцевой" if tour.loop else "в одну сторону", fmt_km(t.get("walk_m", 0)),
        fmt_hours(t.get("hours_walk", 0)))
    if t.get("ascent"):
        sub += ", набор %d м" % t["ascent"]
    add_label(layout, sub + " · зона поиска ±%d км" % tour.half_km, 8, 15, 280, 7, 10, False, "#4d5a52")
    add_legend(layout, map_item, [added[k] for k in ("stops", "walk", "others", "zone") if k in added],
               212, 24)
    if "stops" in added:
        add_stops_table(layout, added["stops"], 212, 84, 77, 112)
    add_scalebar(layout, map_item, 11, 182)
    add_north(layout, map_item, 191, 27)
    add_label(layout, attribution(tour, basemap), 8, 198, 281, 6, 6.5, False, "#5d6d64")
    result["scheme_layout"] = layout

    if export:
        for key, layout_key in (("overview", "overview_layout"), ("scheme", "scheme_layout")):
            path = os.path.join(folder, "%s_map.png" % key)
            if export_layout(result[layout_key], path, log):
                result[key] = path
    return result


def export_layout(layout, png_path, log=None, dpi=150):
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    status = exporter.exportToImage(png_path, settings)
    if status != QgsLayoutExporter.Success:
        if log:
            log("⚠ Не удалось сохранить %s: %s" % (os.path.basename(png_path), exporter.errorMessage()))
        return False
    pdf = QgsLayoutExporter.PdfExportSettings()
    pdf.dpi = 200
    exporter.exportToPdf(os.path.splitext(png_path)[0] + ".pdf", pdf)
    return True
