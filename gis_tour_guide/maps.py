# -*- coding: utf-8 -*-
"""Слои экскурсии в QGIS (GeoPackage с векторами и растром), фирменные стили и
три макета: обзорная карта, карта-схема маршрута, карта-схема фотографий.
"""

import json
import math
import urllib.parse

from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsColorRampShader, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsCoordinateTransformContext, QgsFeature, QgsField, QgsFillSymbol,
    QgsFontMarkerSymbolLayer, QgsGeometry, QgsHillshadeRenderer, QgsLayoutExporter, QgsLayoutFrame,
    QgsLayoutItemAttributeTable, QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
    QgsLayoutItemMapOverview, QgsLayoutItemPage, QgsLayoutItemPicture, QgsLayoutItemScaleBar,
    QgsLayoutItemShape, QgsLayoutMeasurement, QgsLayoutPoint, QgsLayoutSize, QgsLayoutTable,
    QgsLayoutTableStyle, QgsLegendStyle, QgsMapLayerType, QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsPointXY, QgsPrintLayout, QgsProject, QgsProperty, QgsRasterLayer,
    QgsRasterShader, QgsRectangle, QgsRendererCategory, QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
    QgsSingleBandPseudoColorRenderer, QgsSymbolLayer, QgsTextBufferSettings, QgsTextFormat,
    QgsUnitTypes, QgsVectorFileWriter, QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QSize, QVariant
from qgis.PyQt.QtGui import QColor, QFont, QPainter

from . import brand
from .geo import bbox_of, bbox_union
from .text import fmt_hours, fmt_km

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")

BASEMAPS = {
    "topo": ("OpenTopoMap", "https://tile.opentopomap.org/{z}/{x}/{y}.png", 17,
             "подложка © OpenTopoMap (CC-BY-SA), SRTM"),
    "osm": ("OpenStreetMap", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19,
            "подложка © OpenStreetMap"),
    "light": ("CARTO Light", "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", 19,
              "подложка © CARTO"),
}


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


ROLE_LABELS = {"capital": "Региональный центр", "major": "Города региона",
               "near": "Населённые пункты рядом"}


def build_layers(tour):
    """Векторные слои в памяти: {ключ: QgsVectorLayer}."""
    layers = {}
    stops = []
    for poi in tour.stops:
        stops.append((point(poi.lon, poi.lat), {
            "order": poi.order, "name": poi.name, "kind": poi.kind,
            "elevation": float(poi.elevation) if poi.elevation is not None else None,
            "is_main": poi.is_main, "popularity": round(poi.popularity, 1),
            "description": poi.extract or poi.description, "url": poi.url,
            "image": poi.image_url or (poi.photos[0].thumb_url if poi.photos else "")}))
    layers["stops"] = memory_layer("Point", "Точки маршрута", [
        ("order", int), ("name", str), ("kind", str), ("elevation", float), ("is_main", bool),
        ("popularity", float), ("description", str), ("url", str), ("image", str)], stops)

    others = [(point(p.lon, p.lat), {"name": p.name, "kind": p.kind,
                                     "popularity": round(p.popularity, 1),
                                     "distance_km": round(p.distance_km, 2), "url": p.url})
              for p in tour.candidates if not p.order and not p.is_main]
    layers["others"] = memory_layer("Point", "Другие объекты рядом", [
        ("name", str), ("kind", str), ("popularity", float), ("distance_km", float), ("url", str)],
        others)

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
    if drive and drive.start:
        layers["town"] = memory_layer("Point", "Откуда едем", [("name", str)],
                                      [(point(*drive.start), {"name": drive.start_name})])

    layers["object"] = memory_layer("Point", "Объект экскурсии", [("name", str), ("kind", str)], [
        (point(tour.place.lon, tour.place.lat), {"name": tour.place.name, "kind": tour.place.kind})])

    if tour.settlements:
        layers["settlements"] = memory_layer("Point", "Населённые пункты", [
            ("name", str), ("role", str), ("place", str), ("population", int)],
            [(point(s.lon, s.lat), {"name": s.name, "role": s.role, "place": s.place,
                                    "population": s.population}) for s in tour.settlements])

    west, south, east, north = tour.bbox
    zone = QgsGeometry.fromRect(QgsRectangle(west, south, east, north))
    layers["zone"] = memory_layer("Polygon", "Зона поиска ±%s км" % ("%g" % tour.half_km),
                                  [("half_km", float)], [(zone, {"half_km": float(tour.half_km)})])
    if tour.region_geojson:
        geometry = geojson_geometry(tour.region_geojson)
        if geometry and not geometry.isEmpty():
            layers["region"] = memory_layer("MultiPolygon", tour.region_name or "Регион",
                                            [("name", str)], [(geometry, {"name": tour.region_name})])

    photos = [(point(p.lon, p.lat), {"number": p.number, "object": p.poi_name, "title": p.title,
                                     "description": p.description, "author": p.author,
                                     "license": p.license, "image": p.local_path or p.thumb_url,
                                     "page": p.page_url})
              for p in tour.photos if p.lon is not None]
    if photos:
        layers["photos"] = memory_layer("Point", "Фотографии", [
            ("number", int), ("object", str), ("title", str), ("description", str), ("author", str),
            ("license", str), ("image", str), ("page", str)], photos)
    return layers


def save_gpkg(layers, path, dem_grid=None, log=None):
    """Векторы и растр рельефа → один GeoPackage. Возвращает {ключ: слой из файла}."""
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
        if result[0] != QgsVectorFileWriter.NoError:
            if log:
                log("⚠ Не записан слой %s: %s" % (key, result[1]))
            saved[key] = layer
            continue
        first = False
    if dem_grid is not None:
        try:
            write_dem(dem_grid, path)
        except Exception as error:  # noqa: BLE001 - без рельефа карта всё равно будет
            if log:
                log("⚠ Рельеф не записан в GeoPackage: %s" % error)
            dem_grid = None
    for key, layer in layers.items():
        if key in saved:
            continue
        loaded = QgsVectorLayer("%s|layername=%s" % (path, key), layer.name(), "ogr")
        saved[key] = loaded if loaded.isValid() else layer
    if dem_grid is not None:
        raster = QgsRasterLayer("GPKG:%s:dem" % path, "Рельеф (ЦМР)", "gdal")
        if raster.isValid():
            saved["dem"] = raster
    return saved


def write_dem(grid, gpkg_path):
    """ElevationGrid → растровая таблица «dem» в GeoPackage (Float32, тайлы TIFF)."""
    import numpy as np
    from osgeo import gdal, osr
    memory = gdal.GetDriverByName("MEM").Create("", grid.cols, grid.rows, 1, gdal.GDT_Float32)
    memory.SetGeoTransform([grid.west, grid.step_lon, 0, grid.north, 0, -grid.step_lat])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    memory.SetProjection(srs.ExportToWkt())
    band = memory.GetRasterBand(1)
    band.WriteArray(np.where(np.isnan(grid.values), -32768, grid.values).astype(np.float32))
    band.SetNoDataValue(-32768)
    gdal.Translate(gpkg_path, memory, format="GPKG", creationOptions=[
        "RASTER_TABLE=dem", "APPEND_SUBDATASET=YES", "TILE_FORMAT=TIFF",
        "RASTER_IDENTIFIER=Рельеф (ЦМР)"])
    memory = None
    return gpkg_path


# --- Стили ---

def text_format(size=9, color=brand.INK, bold=False, buffer_=True, italic=False):
    fmt = QgsTextFormat()
    font = QFont(brand.FONT)
    font.setBold(bold)
    font.setItalic(italic)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    if buffer_:
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(QColor(255, 255, 255, 235))
        fmt.setBuffer(buf)
    return fmt


def set_labels(layer, field, size=9, color=brand.INK, bold=False, distance=1.8, italic=False,
               expression=False, dd_size=None, dd_bold=None):
    settings = QgsPalLayerSettings()
    settings.fieldName = field
    settings.isExpression = expression
    settings.setFormat(text_format(size, color, bold, italic=italic))
    if layer.geometryType() == enum(QgsWkbTypes, "PointGeometry", "GeometryType.Point"):
        settings.placement = enum(QgsPalLayerSettings, "OrderedPositionsAroundPoint",
                                  "Placement.OrderedPositionsAroundPoint", "AroundPoint")
        settings.dist = distance
    else:
        settings.placement = enum(QgsPalLayerSettings, "Line", "Placement.Line")
    properties = settings.dataDefinedProperties()
    if dd_size:
        properties.setProperty(enum(QgsPalLayerSettings, "Size", "Property.Size"),
                               QgsProperty.fromExpression(dd_size))
    if dd_bold:
        properties.setProperty(enum(QgsPalLayerSettings, "Bold", "Property.Bold"),
                               QgsProperty.fromExpression(dd_bold))
    settings.setDataDefinedProperties(properties)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def symbol_property(name):
    return enum(QgsSymbolLayer, "Property." + name, "Property" + name)


def numbered_marker(fill, size, field_expression, halo=1.2, font_size=None):
    """Кружок с белой каймой и номером внутри (номер берётся из выражения)."""
    symbol = QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "white", "outline_style": "no", "size": str(size + halo)})
    inner = QgsSimpleMarkerSymbolLayer.create({
        "name": "circle", "color": fill, "outline_style": "no", "size": str(size)})
    symbol.appendSymbolLayer(inner)
    number = QgsFontMarkerSymbolLayer(brand.FONT, "1", font_size or size * 0.55, QColor("white"))
    number.setFontStyle("Bold")
    number.setDataDefinedProperty(symbol_property("Character"), QgsProperty.fromExpression(field_expression))
    symbol.appendSymbolLayer(number)
    return symbol, inner


def style_stops(layer):
    symbol, inner = numbered_marker(brand.GREEN, 6.0, "if(\"order\" = 0, 'С', to_string(\"order\"))")
    inner.setDataDefinedProperty(symbol_property("FillColor"), QgsProperty.fromExpression(
        "if(\"is_main\", '%s', if(\"order\" = 0, '%s', '%s'))" % (brand.PIN, brand.SLATE, brand.GREEN)))
    layer.renderer().setSymbol(symbol)
    set_labels(layer, "name", 8.5, brand.GREEN_DARK, True, distance=2.4)
    layer.setMapTipTemplate(
        "<b style='color:%s'>[%% if(\"order\"=0,'Старт',to_string(\"order\")) %%]. [%% \"name\" %%]</b><br>"
        "<i>[%% \"kind\" %%]</i><br>"
        "[%% if(\"image\" <> '', '<img src=\"' || \"image\" || '\" width=\"300\"><br>', '') %%]"
        "[%% left(\"description\", 600) %%]" % brand.GREEN)


def style_object(layer):
    symbol = QgsMarkerSymbol.createSimple({
        "name": "star", "color": brand.PIN, "outline_color": "white", "outline_width": "0.6",
        "size": "8"})
    layer.renderer().setSymbol(symbol)
    set_labels(layer, "name", 10, brand.PIN, True, distance=2.8)


def style_others(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "white", "outline_color": "#8a978e", "outline_width": "0.4",
        "size": "2.4"}))
    set_labels(layer, "name", 6.5, "#5d6d64", italic=True)


def casing_line(color, width, casing=0.9, dash=None):
    symbol = QgsLineSymbol.createSimple({"line_color": "white", "line_width": str(width + casing),
                                         "capstyle": "round", "joinstyle": "round"})
    props = {"line_color": color, "line_width": str(width), "capstyle": "round", "joinstyle": "round"}
    if dash:
        props.update({"use_custom_dash": "1", "customdash": dash, "customdash_unit": "MM"})
    symbol.appendSymbolLayer(QgsSimpleLineSymbolLayer.create(props))
    return symbol


def add_arrows(symbol, color, interval=22):
    arrows = QgsMarkerLineSymbolLayer.create({"interval": str(interval), "placement": "interval",
                                             "rotate": "1", "offset_along_line": str(interval / 2)})
    arrows.setSubSymbol(QgsMarkerSymbol.createSimple({
        "name": "filled_arrowhead", "color": color, "outline_color": "white",
        "outline_width": "0.2", "size": "2.6"}))
    symbol.appendSymbolLayer(arrows)
    return symbol


def style_walk(layer, urban=False):
    kinds = {f["kind"] for f in layer.getFeatures()} or {"trail"}
    categories = []
    if "trail" in kinds:
        categories.append(QgsRendererCategory(
            "trail", add_arrows(casing_line(brand.ROUTE, 1.0), brand.ROUTE),
            "по улицам" if urban else "по дорогам и тропам"))
    if "offtrail" in kinds:
        categories.append(QgsRendererCategory(
            "offtrail", add_arrows(casing_line(brand.ROUTE, 0.9, dash="2.6;1.6"), brand.ROUTE),
            "без троп, по рельефу"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", categories))


def style_drive(layer):
    layer.renderer().setSymbol(casing_line(brand.DRIVE, 1.2, casing=1.0))


def style_town(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "0,0,0,0", "outline_color": brand.DRIVE, "outline_width": "0.8",
        "size": "4.6"}))


def style_settlements(layer):
    capital = QgsMarkerSymbol.createSimple({
        "name": "square", "color": brand.GREEN_DARK, "outline_color": "white", "outline_width": "0.5",
        "size": "3.6"})
    capital.appendSymbolLayer(QgsSimpleMarkerSymbolLayer.create({
        "name": "circle", "color": "white", "outline_style": "no", "size": "1.2"}))
    major = QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "white", "outline_color": brand.GREEN_DARK, "outline_width": "0.6",
        "size": "2.6"})
    near = QgsMarkerSymbol.createSimple({
        "name": "circle", "color": brand.SLATE, "outline_color": "white", "outline_width": "0.3",
        "size": "1.9"})
    layer.setRenderer(QgsCategorizedSymbolRenderer("role", [
        QgsRendererCategory("capital", capital, ROLE_LABELS["capital"]),
        QgsRendererCategory("major", major, ROLE_LABELS["major"]),
        QgsRendererCategory("near", near, ROLE_LABELS["near"]),
    ]))
    set_labels(layer, "name", 7.5, brand.INK, distance=1.4,
               dd_size="CASE WHEN \"role\" = 'capital' THEN 10 WHEN \"role\" = 'major' THEN 8 ELSE 7 END",
               dd_bold="\"role\" = 'capital'")


def style_zone(layer):
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "outline_color": brand.GREEN, "outline_width": "0.5",
        "outline_style": "dash"}))


def style_region(layer):
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,50", "outline_color": brand.GREEN_DARK, "outline_width": "0.7"}))


def style_photos(layer):
    symbol, _ = numbered_marker(brand.PHOTO, 4.6, "to_string(\"number\")", halo=1.0, font_size=2.6)
    layer.renderer().setSymbol(symbol)
    layer.setMapTipTemplate(
        "<b>Фото [% \"number\" %] · [% \"object\" %]</b><br>"
        "[% if(\"image\" <> '', '<img src=\"' || \"image\" || '\" width=\"320\"><br>', '') %]"
        "[% left(\"description\", 300) %]<br><small>© [% \"author\" %], [% \"license\" %]</small>")


def style_dem(layer, kind):
    provider = layer.dataProvider()
    if kind == "hillshade":
        renderer = QgsHillshadeRenderer(provider, 1, 315, 45)
        renderer.setZFactor(1.6)
        layer.setRenderer(renderer)
        layer.setBlendMode(QPainter.CompositionMode.CompositionMode_Multiply)
        layer.renderer().setOpacity(0.6)
        return
    stats = provider.bandStatistics(1)
    low, high = stats.minimumValue, stats.maximumValue
    shader = QgsColorRampShader(low, high)
    shader.setColorRampType(enum(QgsColorRampShader, "Interpolated", "Type.Interpolated"))
    colors = ["#dfe9d3", "#c9dbb4", "#e8dfa0", "#d9c48e", "#c2a27a", "#f4f1ea"]
    shader.setColorRampItemList([QgsColorRampShader.ColorRampItem(
        low + (high - low) * i / (len(colors) - 1), QColor(c)) for i, c in enumerate(colors)])
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader))


VECTOR_ORDER = ["stops", "photos", "object", "others", "settlements", "town", "walk", "drive",
                "zone", "region"]


def basemap_layer(key):
    name, url, zmax, _ = BASEMAPS[key]
    uri = "type=xyz&url=%s&zmax=%d&zmin=0" % (urllib.parse.quote(url, safe=":/"), zmax)
    layer = QgsRasterLayer(uri, "Подложка: " + name, "wms")
    return layer if layer.isValid() else None


def overview_basemap(basemap):
    return basemap if basemap in BASEMAPS else "light"


def add_to_project(tour, layers, basemap="topo", project=None):
    """Группа «Экскурсия: …» с подгруппами «Векторные данные» и «Растры и подложки»."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    title = "Экскурсия: %s" % tour.place.name
    old = root.findGroup(title)
    if old:
        for layer in old.findLayers():
            project.removeMapLayer(layer.layerId())
        root.removeChildNode(old)
    group = root.insertGroup(0, title)
    vectors = group.addGroup("Векторные данные")
    rasters = group.addGroup("Растры и подложки")
    styles = {"stops": style_stops, "object": style_object, "others": style_others,
              "walk": lambda layer: style_walk(layer, tour.urban), "drive": style_drive,
              "town": style_town, "settlements": style_settlements, "zone": style_zone,
              "region": style_region, "photos": style_photos}
    added = {}
    for key in VECTOR_ORDER:
        layer = layers.get(key)
        if layer is None:
            continue
        styles[key](layer)
        project.addMapLayer(layer, False)
        node = vectors.addLayer(layer)
        added[key] = layer
        if key in ("object", "settlements", "town", "drive", "region"):
            node.setItemVisibilityChecked(False)    # для обзорной карты; на канве — схема
    dem = layers.get("dem")
    if dem is not None:
        hillshade = QgsRasterLayer(dem.source(), "Отмывка рельефа", "gdal")
        for kind, raster in (("hillshade", hillshade), ("hypsometry", dem)):
            if raster.isValid():
                style_dem(raster, kind)
                project.addMapLayer(raster, False)
                node = rasters.addLayer(raster)
                added[kind] = raster
                if basemap != "relief":
                    node.setItemVisibilityChecked(False)
    for key in dict.fromkeys([basemap, overview_basemap(basemap)]):
        if key in BASEMAPS:
            base = basemap_layer(key)
            if base:
                project.addMapLayer(base, False)
                node = rasters.addLayer(base)
                added["basemap:" + key] = base
                if key != basemap:
                    node.setItemVisibilityChecked(False)
    if not project.crs().isValid() or len(project.mapLayers()) == len(added):
        project.setCrs(MERCATOR)
    return group, added


# --- Макеты: фирменная рамка ---

PAGE_W, PAGE_H = 297.0, 210.0
MAP_X, MAP_Y, MAP_W, MAP_H = 8.0, 25.5, 197.0, 166.0
PANEL_X, PANEL_W = 209.0, 80.0


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


def place_item(layout, item, x, y, w=None, h=None):
    item.attemptMove(QgsLayoutPoint(x, y))
    if w is not None:
        item.attemptResize(QgsLayoutSize(w, h))
    layout.addLayoutItem(item)
    return item


def add_label(layout, text, x, y, w, h, size, bold=False, color=brand.INK, align_right=False,
              italic=False):
    item = QgsLayoutItemLabel(layout)
    item.setText(text)
    fmt = text_format(size, color, bold, buffer_=False, italic=italic)
    if hasattr(item, "setTextFormat"):
        item.setTextFormat(fmt)
    else:
        item.setFont(fmt.toQFont())
        item.setFontColor(QColor(color))
    if align_right:
        from qgis.PyQt.QtCore import Qt
        item.setHAlign(Qt.AlignmentFlag.AlignRight)
    return place_item(layout, item, x, y, w, h)


def add_rect(layout, x, y, w, h, fill, stroke=None, radius=0.0, stroke_width=0.3):
    shape = QgsLayoutItemShape(layout)
    shape.setShapeType(enum(QgsLayoutItemShape, "Rectangle", "Shape.Rectangle"))
    props = {"color": fill, "outline_style": "no"}
    if stroke:
        props.update({"outline_style": "solid", "outline_color": stroke,
                      "outline_width": str(stroke_width)})
    shape.setSymbol(QgsFillSymbol.createSimple(props))
    if radius:
        shape.setCornerRadius(QgsLayoutMeasurement(radius))
    return place_item(layout, shape, x, y, w, h)


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


def frame(layout, title, subtitle, sheet, attribution_):
    """Шапка с логотипом, оранжевая полоса маршрута, подложка панели, подвал."""
    add_rect(layout, 0, 0, PAGE_W, 20.5, brand.GREEN)
    add_rect(layout, 0, 20.5, PAGE_W, 1.1, brand.ROUTE)
    logo = QgsLayoutItemPicture(layout)
    logo.setPicturePath(brand.ICON)
    place_item(layout, logo, 6.5, 3.2, 14.5, 14.5)
    add_label(layout, title, 25, 2.6, 190, 10, 16.5, True, "white")
    add_label(layout, subtitle, 25, 12.2, 200, 6, 8.5, False, brand.SAND)
    add_label(layout, sheet.upper(), 200, 4, 90, 6, 7.5, True, "white", align_right=True)
    add_label(layout, brand.NAME + " · QGIS", 200, 11, 90, 6, 7.5, False, brand.SAND, align_right=True)
    add_rect(layout, PANEL_X, MAP_Y, PANEL_W, MAP_H, "white", brand.LINE, radius=2.5)
    add_rect(layout, 0, PAGE_H - 13.5, PAGE_W, 13.5, brand.SAND_SOFT)
    add_rect(layout, 0, PAGE_H - 13.5, PAGE_W, 0.5, brand.SAND)
    add_label(layout, attribution_, 8, PAGE_H - 10.5, 235, 8, 6.5, False, brand.MUTED)
    add_label(layout, "Данные собраны автоматически — сверяйтесь с местностью", 190, PAGE_H - 10.5,
              99, 8, 6.5, False, brand.GREEN_DARK, align_right=True, italic=True)


def panel_heading(layout, text, y):
    add_label(layout, text.upper(), PANEL_X + 4, y, PANEL_W - 8, 6, 8.5, True, brand.GREEN)
    add_rect(layout, PANEL_X + 4, y + 5.6, 18, 0.6, brand.ROUTE)


def add_map(layout, layers, rect_3857, x=MAP_X, y=MAP_Y, w=MAP_W, h=MAP_H, background=brand.PAPER):
    item = QgsLayoutItemMap(layout)
    item.attemptMove(QgsLayoutPoint(x, y))
    item.attemptResize(QgsLayoutSize(w, h))
    item.setCrs(MERCATOR)
    item.setLayers(layers)
    item.setKeepLayerSet(True)
    item.setFrameEnabled(True)
    item.setFrameStrokeColor(QColor(brand.GREEN))
    item.setFrameStrokeWidth(QgsLayoutMeasurement(0.4))
    item.setBackgroundColor(QColor(background))
    layout.addLayoutItem(item)
    item.setExtent(fit_aspect(rect_3857, w, h))
    return item


def style_legend(legend):
    group = text_format(8, brand.GREEN_DARK, True, buffer_=False)
    item = text_format(7.5, brand.INK, buffer_=False)
    for style, fmt in ((enum(QgsLegendStyle, "Group", "Style.Group"), group),
                       (enum(QgsLegendStyle, "Subgroup", "Style.Subgroup"), group),
                       (enum(QgsLegendStyle, "SymbolLabel", "Style.SymbolLabel"), item)):
        if hasattr(legend, "rstyle") and hasattr(legend.rstyle(style), "setTextFormat"):
            legend.rstyle(style).setTextFormat(fmt)
        else:
            legend.setStyleFont(style, fmt.toQFont())
    legend.setSymbolWidth(7)
    legend.setSymbolHeight(3.6)


def add_legend(layout, map_item, layers, x, y):
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("")
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.clear()
    for layer in layers:
        root.addLayer(layer)
    legend.setBackgroundEnabled(False)
    legend.setFrameEnabled(False)
    style_legend(legend)
    legend.attemptMove(QgsLayoutPoint(x, y))
    layout.addLayoutItem(legend)
    legend.adjustBoxSize()
    return legend


def legend_height(layers):
    """Высота легенды, мм. QGIS считает её сам только при отрисовке — оцениваем заранее."""
    height = 0.0
    for layer in layers:
        renderer = layer.renderer() if hasattr(layer, "renderer") else None
        if isinstance(renderer, QgsCategorizedSymbolRenderer):
            height += 7.0 + 6.3 * len(renderer.categories())
        elif layer.type() == enum(QgsMapLayerType, "VectorLayer", "Vector") and \
                layer.geometryType() == enum(QgsWkbTypes, "PointGeometry", "GeometryType.Point"):
            height += 8.6
        else:
            height += 6.6
    return height + 3.0


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
    bar.setHeight(1.8)
    if hasattr(bar, "setTextFormat"):
        bar.setTextFormat(text_format(7, brand.INK, buffer_=False))
    if hasattr(bar, "setFillSymbol"):      # QGIS 3.20+
        bar.setFillSymbol(QgsFillSymbol.createSimple({"color": brand.GREEN, "outline_style": "no"}))
        bar.setAlternateFillSymbol(QgsFillSymbol.createSimple({"color": "white", "outline_style": "no"}))
        bar.setLineSymbol(QgsLineSymbol.createSimple({"line_color": brand.GREEN, "line_width": "0.3"}))
    else:
        bar.setFillColor(QColor(brand.GREEN))
        bar.setFillColor2(QColor("white"))
        bar.setLineColor(QColor(brand.GREEN))
    bar.setBackgroundEnabled(True)
    bar.setBackgroundColor(QColor(255, 255, 255, 215))
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


def add_north(layout, map_item, x, y, size=11):
    add_rect(layout, x - 1, y - 1, size + 2, size + 2, "255,255,255,215", brand.LINE, radius=1.5)
    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    picture.setLinkedMap(map_item)
    if hasattr(picture, "setSvgFillColor"):
        picture.setSvgFillColor(QColor(brand.GREEN))
        picture.setSvgStrokeColor(QColor(brand.GREEN_DARK))
    return place_item(layout, picture, x, y, size, size)


def add_table(layout, layer, fields, headings, widths, x, y, w, h, max_rows=40):
    table = QgsLayoutItemAttributeTable.create(layout)
    table.setVectorLayer(layer)
    table.setDisplayedFields(fields)
    columns = table.columns()
    for column, heading, width in zip(columns, headings, widths):
        column.setHeading(heading)
        column.setWidth(width)
    table.setColumns(columns)
    if hasattr(table, "setContentTextFormat"):
        table.setContentTextFormat(text_format(6.8, buffer_=False))
        table.setHeaderTextFormat(text_format(7, "white", True, buffer_=False))
    header = QgsLayoutTableStyle()
    header.enabled = True
    header.cellBackgroundColor = QColor(brand.GREEN)
    even = QgsLayoutTableStyle()
    even.enabled = True
    even.cellBackgroundColor = QColor(brand.PAPER)
    table.setCellStyle(enum(QgsLayoutTable, "HeaderRow", "CellStyleGroup.HeaderRow"), header)
    table.setCellStyle(enum(QgsLayoutTable, "EvenRows", "CellStyleGroup.EvenRows"), even)
    table.setGridColor(QColor(brand.LINE))
    table.setGridStrokeWidth(0.15)
    table.setShowGrid(True)
    table.setVerticalGrid(False)
    table.setCellMargin(0.8)
    table.setWrapBehavior(enum(QgsLayoutTable, "WrapText", "WrapBehavior.WrapText"))
    table.setMaximumNumberOfFeatures(max_rows)
    layout.addMultiFrame(table)
    frame_ = QgsLayoutFrame(layout, table)
    frame_.attemptMove(QgsLayoutPoint(x, y))
    frame_.attemptResize(QgsLayoutSize(w, h))
    table.addFrame(frame_)
    return table


def attribution(tour, basemap):
    parts = ["Данные © участники OpenStreetMap (ODbL)"]
    if basemap in BASEMAPS:
        parts.append(BASEMAPS[basemap][3])
    if tour.dem_name and not tour.urban:
        parts.append("рельеф: " + tour.dem_name)
    parts.append("маршруты: OSRM/FOSSGIS")
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
    elif tour.drive and tour.drive.start:
        points.append(tour.drive.start)
    return bbox_of(points, 12000 if not tour.urban else 1500)


def area(box):
    return max(box[2] - box[0], 1e-9) * max(box[3] - box[1], 1e-9)


def scheme_extent(tour):
    coords = [c for leg in tour.legs for s in leg.segments for c in s.coords]
    box = tour.bbox
    if coords:
        route_box = bbox_of(coords, 300 if not tour.urban else 150)
        # в городе зона бывает куда больше прогулки — показываем саму прогулку
        box = route_box if tour.urban else bbox_union(box, route_box)
    return box


def photos_extent(tour):
    points = [(p.lon, p.lat) for p in tour.photos if p.lon is not None]
    points += [s.point for s in tour.stops]
    return bbox_of(points, 400 if tour.urban else 1200)


def drive_text(tour):
    drive = tour.drive
    lines = []
    if drive and drive.start_name:
        lines.append("Откуда: %s%s" % (drive.start_name, " (%s)" % tour.start_reason if tour.start_reason else ""))
    if drive and drive.ok:
        lines.append("На машине: %s, ~%s" % (fmt_km(drive.distance_m), fmt_hours(drive.duration_s / 3600)))
        for label_, meters in drive.roads[:5]:
            lines.append("• %s — %s" % (label_, fmt_km(meters)))
        if drive.last_mile_m > 300 and not tour.urban:
            lines.append("Дальше пешком: %s до объекта" % fmt_km(drive.last_mile_m))
    elif drive and drive.note:
        lines.append(drive.note)
    for kind, name, km, _ in tour.transport[:4]:
        lines.append("• %s «%s» — %s" % (kind, name, fmt_km(km * 1000)))
    return "\n".join(lines)


def make_layouts(tour, added, basemap="topo", project=None):
    """Три макета (обзорная карта, карта-схема маршрута, карта-схема фото) → {ключ: макет}."""
    project = project or QgsProject.instance()
    result = {}
    name = tour.place.name
    region = tour.region_name or tour.place.region
    over_key = overview_basemap(basemap)
    over_base = [added["basemap:" + over_key]] if "basemap:" + over_key in added else []
    scheme_base = [added["basemap:" + basemap]] if "basemap:" + basemap in added else []
    relief = [added[k] for k in ("hillshade", "hypsometry") if k in added] if basemap == "relief" else []
    mode = brand.MODES[tour.mode]

    # 1. Обзорная карта региона
    layout = new_layout(project, "Экскурсия «%s» — 1. Обзорная карта" % name)
    keys = ["object", "town", "settlements", "drive", "zone", "region"]
    layers = [added[k] for k in keys if k in added] + over_base
    frame(layout, "%s — как добраться" % name, "%s · %s" % (region, mode), "Лист 1 · обзорная карта",
          attribution(tour, over_key))
    map_item = add_map(layout, layers, mercator_rect(overview_extent(tour), project))
    panel_heading(layout, "Условные обозначения", MAP_Y + 3)
    legend_layers = [added[k] for k in keys if k in added]
    add_legend(layout, map_item, legend_layers, PANEL_X + 3, MAP_Y + 10)
    text_y = MAP_Y + 10 + legend_height(legend_layers)
    panel_heading(layout, "Как добраться", text_y)
    add_label(layout, drive_text(tour), PANEL_X + 4, text_y + 8, PANEL_W - 8, MAP_Y + MAP_H - text_y - 10,
              7.3, False, brand.INK)
    add_scalebar(layout, map_item, MAP_X + 3, MAP_Y + MAP_H - 11)
    add_north(layout, map_item, MAP_X + MAP_W - 14, MAP_Y + 3)
    approach = approach_extent(tour)
    if area(overview_extent(tour)) > 20 * area(approach):
        # регион огромный — дорога теряется: врезка с подъездом и её рамка на карте
        ix, iy, iw, ih = MAP_X + MAP_W - 66, MAP_Y + MAP_H - 52, 63, 49
        inset = add_map(layout, layers, mercator_rect(approach, project), ix, iy, iw, ih, "white")
        inset.setFrameStrokeColor(QColor(brand.ROUTE))
        inset.setFrameStrokeWidth(QgsLayoutMeasurement(0.6))
        overview = QgsLayoutItemMapOverview("Врезка", map_item)
        map_item.overviews().addOverview(overview)
        overview.setLinkedMap(inset)
        overview.setFrameSymbol(QgsFillSymbol.createSimple({
            "color": "211,84,0,45", "outline_color": brand.ROUTE, "outline_width": "0.5"}))
        add_rect(layout, ix, iy - 5, 22, 5, brand.ROUTE)
        add_label(layout, "ПОДЪЕЗД", ix + 1.5, iy - 4.6, 20, 4.5, 7, True, "white")
    result["overview"] = layout

    # 2. Карта-схема маршрута
    layout = new_layout(project, "Экскурсия «%s» — 2. Карта-схема маршрута" % name)
    keys = ["stops", "others", "walk"] + ([] if tour.urban else ["zone"])
    layers = [added[k] for k in keys if k in added] + relief + scheme_base
    t = tour.totals
    sub = "%s · %s, %s, ~%s пешком" % (mode, "кольцевой" if tour.loop else "в одну сторону",
                                       fmt_km(t.get("walk_m", 0)), fmt_hours(t.get("hours_walk", 0)))
    if t.get("ascent"):
        sub += ", набор %d м" % t["ascent"]
    frame(layout, "%s — маршрут %s" % (name, "прогулки" if tour.urban else "экскурсии"), sub,
          "Лист 2 · карта-схема маршрута", attribution(tour, basemap))
    map_item = add_map(layout, layers, mercator_rect(scheme_extent(tour), project))
    panel_heading(layout, "Условные обозначения", MAP_Y + 3)
    legend_layers = [added[k] for k in keys if k in added]
    add_legend(layout, map_item, legend_layers, PANEL_X + 3, MAP_Y + 10)
    table_y = MAP_Y + 10 + legend_height(legend_layers)
    panel_heading(layout, "Точки маршрута", table_y)
    if "stops" in added:
        add_table(layout, added["stops"], ["order", "name", "kind"], ["№", "Объект", "Тип"],
                  [5, 36, 24], PANEL_X + 3, table_y + 8, PANEL_W - 6, MAP_Y + MAP_H - table_y - 10)
    add_scalebar(layout, map_item, MAP_X + 3, MAP_Y + MAP_H - 11)
    add_north(layout, map_item, MAP_X + MAP_W - 14, MAP_Y + 3)
    result["scheme"] = layout

    # 3. Карта-схема фотографий
    if "photos" in added:
        layout = new_layout(project, "Экскурсия «%s» — 3. Карта-схема фотографий" % name)
        keys = ["photos", "stops", "walk"]
        layers = [added[k] for k in keys if k in added] + relief + scheme_base
        frame(layout, "%s — где сделаны фотографии" % name,
              "%d фото · номера совпадают с отчётом" % added["photos"].featureCount(),
              "Лист 3 · карта-схема фотографий", attribution(tour, basemap) + "; фото — Викисклад")
        map_item = add_map(layout, layers, mercator_rect(photos_extent(tour), project))
        panel_heading(layout, "Условные обозначения", MAP_Y + 3)
        legend_layers = [added[k] for k in keys if k in added]
        add_legend(layout, map_item, legend_layers, PANEL_X + 3, MAP_Y + 10)
        table_y = MAP_Y + 10 + legend_height(legend_layers)
        panel_heading(layout, "Фотографии", table_y)
        add_table(layout, added["photos"], ["number", "object", "author"], ["№", "Объект", "Автор"],
                  [5, 36, 24], PANEL_X + 3, table_y + 8, PANEL_W - 6, MAP_Y + MAP_H - table_y - 10)
        add_scalebar(layout, map_item, MAP_X + 3, MAP_Y + MAP_H - 11)
        add_north(layout, map_item, MAP_X + MAP_W - 14, MAP_Y + 3)
        result["photos"] = layout
    return result


def render(layout, dpi=130, quality=88):
    """Лист макета → JPEG-байты (для отчёта; на диск не пишется)."""
    from .report import image_bytes
    exporter = QgsLayoutExporter(layout)
    image = exporter.renderPageToImage(0, QSize(), dpi)
    return image_bytes(image, "JPG", quality)
