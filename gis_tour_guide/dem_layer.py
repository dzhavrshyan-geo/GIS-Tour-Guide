# -*- coding: utf-8 -*-
"""Высоты из растрового слоя ЦМР проекта (любая СК) — тот же интерфейс, что у TerrariumSource."""

import numpy as np
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject,
    QgsRectangle,
)

from .terrain import ElevationGrid

NUMPY_TYPES = {
    "Byte": np.uint8, "Int8": np.int8, "UInt16": np.uint16, "Int16": np.int16,
    "UInt32": np.uint32, "Int32": np.int32, "Float32": np.float32, "Float64": np.float64,
}


class LayerElevation:
    url = ""

    def __init__(self, layer, band=1):
        # Провайдер клонируем в главном потоке: сам слой из фоновой задачи трогать нельзя
        self.provider = layer.dataProvider().clone()
        self.band = band
        self.name = "слой ЦМР «%s»" % layer.name()
        self.extent = layer.extent()
        self.pixel = (layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY())
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        context = QgsProject.instance().transformContext()
        self.to_layer = QgsCoordinateTransform(wgs84, layer.crs(), context)

    def sample(self, lon, lat):
        try:
            p = self.to_layer.transform(QgsPointXY(lon, lat))
        except Exception:  # noqa: BLE001 - точка вне области определения СК
            return None
        if not self.extent.contains(p):
            return None
        value, ok = self.provider.sample(p, self.band)
        return float(value) if ok else None

    def grid(self, bbox, max_cells=200):
        west, south, east, north = bbox
        cols = rows = max_cells
        step_lon = (east - west) / cols
        step_lat = (north - south) / rows
        xs = np.empty((rows, cols))
        ys = np.empty((rows, cols))
        for r in range(rows):
            lat = north - (r + 0.5) * step_lat
            for c in range(cols):
                p = self.to_layer.transform(QgsPointXY(west + (c + 0.5) * step_lon, lat))
                xs[r, c], ys[r, c] = p.x(), p.y()
        rect = QgsRectangle(xs.min(), ys.min(), xs.max(), ys.max()).intersect(self.extent)
        values = np.full((rows, cols), np.nan, np.float32)
        if rect.isEmpty():
            return ElevationGrid(values, west, north, step_lon, step_lat)
        width = int(min(max(rect.width() / self.pixel[0], 2), 1200))
        height = int(min(max(rect.height() / self.pixel[1], 2), 1200))
        block = self.provider.block(self.band, rect, width, height)
        data = block_array(block, width, height)
        ix = ((xs - rect.xMinimum()) / rect.width() * width).astype(int)
        iy = ((rect.yMaximum() - ys) / rect.height() * height).astype(int)
        inside = (ix >= 0) & (ix < width) & (iy >= 0) & (iy < height)
        values[inside] = data[iy[inside], ix[inside]]
        return ElevationGrid(values, west, north, step_lon, step_lat)


def block_array(block, width, height):
    dtype = np.float32
    for name, candidate in NUMPY_TYPES.items():
        if block.dataType() == getattr(Qgis.DataType, name, None):
            dtype = candidate
    data = np.frombuffer(bytes(block.data()), dtype=dtype)
    if data.size != width * height:
        data = np.array([[block.value(r, c) for c in range(width)] for r in range(height)])
    data = data.reshape(height, width).astype(np.float32)
    if block.hasNoDataValue():
        data[data == np.float32(block.noDataValue())] = np.nan
    return data
