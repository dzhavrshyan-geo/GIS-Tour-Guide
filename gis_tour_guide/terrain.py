# -*- coding: utf-8 -*-
"""Рельеф: высоты из открытых тайлов Terrain Tiles (AWS) или слоя ЦМР,
профиль маршрута, набор высоты, время в пути и путь по рельефу без троп.
"""

import heapq
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import geo

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_NAME = "Terrain Tiles (AWS Open Data: SRTM, GMTED, ETOPO1 и др.)"

OFFTRAIL_SPEED = 0.6      # вне троп — 0.6 от скорости по тропе (Тоблер)
FLAT_SPEED_KMH = 4.0      # если высот нет


def decode_terrarium(data):
    """PNG Terrarium → массив высот (м): (R·256 + G + B/256) − 32768."""
    from qgis.PyQt.QtGui import QImage
    image = QImage.fromData(data, "PNG")
    if image.isNull():
        raise ValueError("не PNG")
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    pointer = image.constBits()
    pointer.setsize(image.bytesPerLine() * height)
    raw = np.frombuffer(pointer, np.uint8).reshape(height, image.bytesPerLine())
    pixels = raw[:, :width * 4].reshape(height, width, 4).astype(np.float32)
    # Format_RGB32 в памяти (little-endian): B, G, R, A
    return pixels[..., 2] * 256.0 + pixels[..., 1] + pixels[..., 0] / 256.0 - 32768.0


class ElevationGrid:
    """Регулярная сетка высот в градусах: values[строка, столбец], строки — с севера."""

    def __init__(self, values, west, north, step_lon, step_lat):
        self.values = values
        self.west, self.north = west, north
        self.step_lon, self.step_lat = step_lon, step_lat
        self.rows, self.cols = values.shape
        mid_lat = north - self.rows * step_lat / 2
        self.dx_m = step_lon * geo.m_per_deg_lon(mid_lat)
        self.dy_m = step_lat * geo.M_PER_DEG_LAT

    @property
    def bbox(self):
        return (self.west, self.north - self.rows * self.step_lat,
                self.west + self.cols * self.step_lon, self.north)

    def lonlat(self, r, c):
        return (self.west + (c + 0.5) * self.step_lon, self.north - (r + 0.5) * self.step_lat)

    def cell(self, lon, lat):
        r = int((self.north - lat) / self.step_lat)
        c = int((lon - self.west) / self.step_lon)
        return min(max(r, 0), self.rows - 1), min(max(c, 0), self.cols - 1)


class TerrariumSource:
    name = TERRARIUM_NAME
    url = "https://registry.opendata.aws/terrain-tiles/"

    def __init__(self, http, max_zoom=13, decode=decode_terrarium):
        self.http = http
        self.max_zoom = max_zoom
        self.decode = decode
        self.tiles = {}
        self.lock = threading.Lock()

    def tile(self, z, x, y):
        n = 2 ** z
        x %= n
        if not 0 <= y < n:
            return None
        key = (z, x, y)
        with self.lock:
            if key in self.tiles:
                return self.tiles[key]
        data = self.http.get(TERRARIUM_URL.format(z=z, x=x, y=y), ttl=365 * 86400)
        heights = self.decode(data)
        with self.lock:
            self.tiles[key] = heights
        return heights

    def prefetch(self, keys, workers=8):
        """Скачать несколько тайлов параллельно (S3 не ограничивает частоту запросов)."""
        keys = [k for k in keys if k not in self.tiles]
        if len(keys) > 1:
            with ThreadPoolExecutor(workers) as pool:
                list(pool.map(lambda k: self.tile(*k), keys))

    def sample(self, lon, lat, z=12):
        fx, fy = geo.lonlat_to_tile(lon, lat, z)
        tile = self.tile(z, int(fx), int(fy))
        if tile is None:
            return None
        px = min(int((fx - int(fx)) * 256), 255)
        py = min(int((fy - int(fy)) * 256), 255)
        return float(tile[py, px])

    def grid(self, bbox, max_cells=200, max_tiles=36):
        west, south, east, north = bbox
        for z in range(self.max_zoom, 1, -1):
            x0, y0 = geo.lonlat_to_tile(west, north, z)
            x1, y1 = geo.lonlat_to_tile(east, south, z)
            tiles = (int(x1) - int(x0) + 1) * (int(y1) - int(y0) + 1)
            if max((x1 - x0), (y1 - y0)) * 256 <= max_cells * 1.6 and tiles <= max_tiles:
                break
        tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
        mosaic = np.full(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.nan, np.float32)
        self.prefetch([(z, tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)])
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile = self.tile(z, tx, ty)
                if tile is not None:
                    mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256,
                           (tx - tx0) * 256:(tx - tx0 + 1) * 256] = tile
        px_w, px_h = (x1 - x0) * 256, (y1 - y0) * 256
        cols = max(2, min(max_cells, int(round(px_w))))
        rows = max(2, min(max_cells, int(round(px_h))))
        step_lon = (east - west) / cols
        step_lat = (north - south) / rows
        lons = west + (np.arange(cols) + 0.5) * step_lon
        lats = north - (np.arange(rows) + 0.5) * step_lat
        n = 2.0 ** z
        px = ((lons + 180.0) / 360.0 * n - tx0) * 256
        lat_r = np.radians(np.clip(lats, -85.0511, 85.0511))
        py = ((1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n - ty0) * 256
        ix = np.clip(px.astype(int), 0, mosaic.shape[1] - 1)
        iy = np.clip(py.astype(int), 0, mosaic.shape[0] - 1)
        return ElevationGrid(mosaic[np.ix_(iy, ix)], west, north, step_lon, step_lat)


# --- Профиль и время ---

def profile(source, coords, step_m=40.0):
    """[(метры от начала, высота)] вдоль линии; точки без высоты пропускаются."""
    points = geo.densify(coords, step_m)
    result = []
    distance = 0.0
    for i, point in enumerate(points):
        if i:
            distance += geo.dist_m(points[i - 1], point)
        try:
            height = source.sample(point[0], point[1])
        except Exception:  # noqa: BLE001 - нет тайла/сети: просто без высоты
            height = None
        if height is not None and not math.isnan(height) and height > -1000:
            result.append((distance, height))
    return result


def climb(prof, threshold=3.0):
    """(набор, сброс, мин, макс) — мелкие колебания (шум ЦМР) не считаются."""
    if not prof:
        return 0.0, 0.0, None, None
    heights = [h for _, h in prof]
    up = down = 0.0
    anchor = heights[0]
    for height in heights[1:]:
        delta = height - anchor
        if abs(delta) >= threshold:
            if delta > 0:
                up += delta
            else:
                down -= delta
            anchor = height
    return up, down, min(heights), max(heights)


def walk_hours(prof, length_m, offtrail=False):
    """Время ходьбы по профилю (Тоблер); без профиля — 4 км/ч."""
    factor = OFFTRAIL_SPEED if offtrail else 1.0
    if len(prof) < 2:
        return length_m / 1000.0 / (FLAT_SPEED_KMH * factor)
    hours = 0.0
    for (d0, h0), (d1, h1) in zip(prof, prof[1:]):
        dx = d1 - d0
        if dx <= 0:
            continue
        hours += dx / 1000.0 / (geo.tobler_kmh((h1 - h0) / dx) * factor)
    covered = prof[-1][0] - prof[0][0]
    if covered < length_m:  # хвосты без высот
        hours += (length_m - covered) / 1000.0 / (FLAT_SPEED_KMH * factor)
    return hours


# --- Путь по рельефу ---

def steep_penalty(degrees):
    """Крутые склоны тяжелы и опасны — сверх Тоблера штрафуем после 20°."""
    if degrees <= 20:
        return 1.0
    return 1.0 + ((degrees - 20.0) / 8.0) ** 2


# Во сколько раз тяжелее идти по клетке: озеро — нельзя, болото, ледник, река (брод)
WATER_COST = {"water": float("inf"), "wetland": 4.0, "glacier": 3.0, "river": 25.0}


def inside(lons, lats, polygon):
    """Какие точки (массивы lon/lat) внутри контура — луч по горизонтали, векторно."""
    result = np.zeros(lons.shape, dtype=bool)
    xs = np.array([p[0] for p in polygon])
    ys = np.array([p[1] for p in polygon])
    x0, y0, x1, y1 = xs[:-1], ys[:-1], xs[1:], ys[1:]
    for ax, ay, bx, by in zip(x0, y0, x1, y1):
        if ay == by:
            continue
        crosses = ((ay > lats) != (by > lats)) & (lons < (bx - ax) * (lats - ay) / (by - ay) + ax)
        result ^= crosses
    return result


def water_mask(grid, features):
    """Множители стоимости шага по клеткам сетки (1 — обычная местность)."""
    mask = np.ones((grid.rows, grid.cols), dtype=float)
    if not features:
        return mask
    west, south, east, north = grid.bbox
    cols = grid.west + (np.arange(grid.cols) + 0.5) * grid.step_lon
    rows = grid.north - (np.arange(grid.rows) + 0.5) * grid.step_lat
    lons, lats = np.meshgrid(cols, rows)
    for kind, polygon in features.get("polygons", []):
        px = [p[0] for p in polygon]
        py = [p[1] for p in polygon]
        if max(px) < west or min(px) > east or max(py) < south or min(py) > north:
            continue
        hit = inside(lons, lats, polygon)
        mask[hit] = np.maximum(mask[hit], WATER_COST.get(kind, 1.0))
    step = min(grid.dx_m, grid.dy_m) / 2.0
    for kind, line in features.get("lines", []):
        for lon, lat in geo.densify(line, step):
            if west <= lon <= east and south <= lat <= north:
                r, c = grid.cell(lon, lat)
                mask[r, c] = max(mask[r, c], WATER_COST.get(kind, 1.0))
    return mask


def least_cost_path(grid, start, end, max_slope_deg=35.0, max_nodes=400000, mask=None):
    """Самый быстрый для пешехода путь по сетке (A*). (точки (lon, lat), часы) или None.

    mask — множители стоимости клеток (вода, болота); inf — непроходимо.
    """
    rows, cols = grid.rows, grid.cols
    heights = grid.values.astype(float).ravel().tolist()
    dx, dy = grid.dx_m, grid.dy_m
    max_tan = math.tan(math.radians(max_slope_deg))
    moves = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                moves.append((dr, dc, math.hypot(dr * dy, dc * dx)))
    r0, c0 = grid.cell(*start)
    r1, c1 = grid.cell(*end)
    goal = r1 * cols + c1
    factors = None
    if mask is not None:
        mask = mask.copy()
        mask[r0, c0] = mask[r1, c1] = 1.0      # стоим на берегу — сами клетки старта и финиша проходимы
        factors = mask.ravel().tolist()
    best_speed = 6.0 * OFFTRAIL_SPEED

    def heuristic(r, c):
        return math.hypot((r - r1) * dy, (c - c1) * dx) / 1000.0 / best_speed

    begin = r0 * cols + c0
    cost = {begin: 0.0}
    parent = {begin: None}
    queue = [(heuristic(r0, c0), 0.0, begin)]
    done = set()
    while queue:
        _, spent, node = heapq.heappop(queue)
        if node in done:
            continue
        if node == goal:
            break
        done.add(node)
        if len(done) > max_nodes:
            return None
        r, c = divmod(node, cols)
        h0 = heights[node]
        for dr, dc, dist in moves:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < rows and 0 <= cc < cols):
                continue
            nxt = rr * cols + cc
            if nxt in done:
                continue
            h1 = heights[nxt]
            if h1 != h1 or h0 != h0:  # NaN — нет данных
                continue
            slope = (h1 - h0) / dist
            if abs(slope) > max_tan:
                continue
            step = dist / 1000.0 / (geo.tobler_kmh(slope) * OFFTRAIL_SPEED)
            step *= steep_penalty(math.degrees(math.atan(abs(slope))))
            if factors is not None:
                factor = factors[nxt]
                if factor == float("inf"):
                    continue
                step *= factor
            total = spent + step
            if total < cost.get(nxt, float("inf")):
                cost[nxt] = total
                parent[nxt] = node
                heapq.heappush(queue, (total + heuristic(rr, cc), total, nxt))
    if goal not in parent:
        return None
    cells = []
    node = goal
    while node is not None:
        cells.append(node)
        node = parent[node]
    cells.reverse()
    points = [grid.lonlat(*divmod(n, cols)) for n in cells]
    points = [tuple(start)] + points[1:-1] + [tuple(end)] if len(points) > 1 else [tuple(start), tuple(end)]
    tolerance = min(dx, dy) * 0.6
    return geo.simplify(points, tolerance), cost.get(goal, 0.0)


def offtrail_finder(source, max_cells=180, max_slope_deg=35.0, water=None):
    """Функция (a, b) → (точки, часы) для routing.foot_segments, или None без ЦМР.

    water — озёра, болота и реки из osm.water_features: их путь обходит или пересекает с трудом.
    """
    if source is None:
        return None

    def find(a, b):
        span = geo.dist_m(a, b)
        margin = max(span * 0.35, 600.0)
        box = geo.bbox_of([a, b], margin)
        try:
            grid = source.grid(box, max_cells=max_cells)
        except Exception:  # noqa: BLE001 - нет тайлов/сети: пойдём по прямой
            return None
        mask = water_mask(grid, water) if water else None
        return least_cost_path(grid, a, b, max_slope_deg, mask=mask)

    return find
