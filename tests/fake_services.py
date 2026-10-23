# -*- coding: utf-8 -*-
"""Поддельные ответы открытых сервисов: окрестности вулкана Горелый (Камчатка).

Координаты и данные приблизительные — только для проверки разбора ответов
и логики плагина без сети.
"""

import json
import math
import urllib.parse

from gis_tour_guide import geo
from gis_tour_guide.net import NetError

VOLCANO = (158.030, 52.558)
TOWN = (158.650, 53.020)
TRAILHEAD = (158.075, 52.585)      # конец проезжей дороги
CAVE = (158.010, 52.548)           # в стороне от троп
OFF_NETWORK = {CAVE}


def np_dist(lon, lat, point):
    import numpy as np
    p1, p2 = np.radians(lat), math.radians(point[1])
    dl = np.radians(lon - point[0])
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * math.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * geo.EARTH_R * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def elevation(lon, lat):
    """Конус вулкана 1829 м на плато 600 м и гора «Верблюд» 1100 м (lon, lat — числа или массивы)."""
    import numpy as np
    cone = np.maximum(0.0, 1229 - np_dist(lon, lat, VOLCANO) * 0.32)
    hill = np.maximum(0.0, 500 - np_dist(lon, lat, (158.075, 52.530)) * 0.25)
    return 600 + cone + hill


TILE_CACHE = {}


def terrarium_png(z, x, y):
    import numpy as np
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    from qgis.PyQt.QtGui import QImage
    if (z, x, y) in TILE_CACHE:
        return TILE_CACHE[(z, x, y)]
    n = 2.0 ** z
    px = (np.arange(256) + 0.5) / 256
    lon = (x + px) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + px) / n))))
    lon2, lat2 = np.meshgrid(lon, lat)
    value = elevation(lon2, lat2) + 32768
    r = (value // 256).astype(np.uint32)
    g = (value % 256).astype(np.uint32)
    b = ((value - np.floor(value)) * 256).astype(np.uint32)
    argb = np.ascontiguousarray((0xFF << 24) | (r << 16) | (g << 8) | b, dtype=np.uint32)
    image = QImage(argb.tobytes(), 256, 256, 256 * 4, QImage.Format.Format_RGB32)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    TILE_CACHE[(z, x, y)] = bytes(data)
    return TILE_CACHE[(z, x, y)]


OVERPASS_POIS = {"elements": [
    {"type": "node", "id": 1, "lat": 52.5581, "lon": 158.0302,
     "tags": {"natural": "volcano", "name": "Горелый", "ele": "1829",
              "wikipedia": "ru:Горелый (вулкан)", "wikidata": "Q1"}},
    {"type": "node", "id": 2, "lat": 52.530, "lon": 158.075,
     "tags": {"natural": "peak", "name": "Верблюд", "ele": "1100"}},
    {"type": "node", "id": 3, "lat": CAVE[1], "lon": CAVE[0],
     "tags": {"natural": "cave_entrance", "name": "Ледяная пещера",
              "description": "Лавовая пещера со льдом"}},
    {"type": "way", "id": 4, "center": {"lat": 52.600, "lon": 158.120},
     "tags": {"waterway": "waterfall", "name": "Вилючинский водопад", "wikidata": "Q2"}},
    {"type": "node", "id": 5, "lat": 52.590, "lon": 158.060,
     "tags": {"natural": "hot_spring", "name": "Горячие ключи"}},
    {"type": "node", "id": 6, "lat": 52.586, "lon": 158.076,
     "tags": {"tourism": "camp_site", "name": "Стоянка у Горелого"}},
    {"type": "node", "id": 7, "lat": 52.540, "lon": 158.050,
     "tags": {"natural": "peak"}},                       # без имени — не берём
    {"type": "node", "id": 8, "lat": 52.575, "lon": 158.000,
     "tags": {"tourism": "viewpoint"}},
    {"type": "node", "id": 9, "lat": 52.531, "lon": 158.0752,
     "tags": {"natural": "peak", "name": "гора Верблюд"}},   # дубль
    {"type": "node", "id": 10, "lat": 52.990, "lon": 158.600,
     "tags": {"natural": "peak", "name": "Далёкая"}},     # вне зоны
]}

WIKI_GEO = {"query": {"pages": [
    {"pageid": 11, "title": "Горелый (вулкан)", "description": "вулкан на Камчатке",
     "coordinates": [{"lat": 52.558, "lon": 158.030}], "pageprops": {"wikibase_item": "Q1"}},
    {"pageid": 12, "title": "Вилючинский водопад", "description": "водопад на Камчатке",
     "coordinates": [{"lat": 52.6002, "lon": 158.1203}], "pageprops": {"wikibase_item": "Q2"}},
    {"pageid": 13, "title": "Термальный (посёлок)", "description": "посёлок в Камчатском крае",
     "coordinates": [{"lat": 52.580, "lon": 158.090}]},
    {"pageid": 14, "title": "Кратер Активный", "description": "кратер вулкана Горелый",
     "coordinates": [{"lat": 52.562, "lon": 158.026}]},
]}}

WIKI_DETAILS = {"query": {"pages": [
    {"title": "Горелый (вулкан)", "extract": "Горелый — действующий вулкан на юге Камчатки. "
     "Состоит из трёх слившихся конусов с 11 кратерами.", "description": "вулкан",
     "fullurl": "https://ru.wikipedia.org/wiki/Горелый_(вулкан)",
     "thumbnail": {"source": "https://upload.wikimedia.org/gorely.jpg"}},
    {"title": "Вилючинский водопад", "extract": "Водопад высотой около 40 м.",
     "fullurl": "https://ru.wikipedia.org/wiki/Вилючинский_водопад"},
]}}

WIKIVOYAGE_TEXT = """Южная Камчатка — район Камчатского края.

== Понять ==
Вулканы и горячие источники.

== Как добраться ==
Из Петропавловска-Камчатского по трассе на Мутновскую ГеоЭС, далее по грунтовке.
=== На автобусе ===
Регулярных автобусов нет, ездят туристические вахтовки.

== Безопасность ==
Медведи! Регистрируйтесь в МЧС.
"""


def car_route():
    coords = [list(TOWN), [158.40, 52.90], [158.20, 52.70], list(TRAILHEAD)]
    steps = [
        {"distance": 9000, "name": "Северо-Восточное шоссе", "ref": ""},
        {"distance": 200, "name": "", "ref": ""},
        {"distance": 52000, "name": "", "ref": "Р-474"},
        {"distance": 3000, "name": "Мутновская дорога", "ref": "Р-474"},
        {"distance": 21000, "name": "", "ref": ""},
    ]
    return {"code": "Ok", "routes": [{
        "distance": 85200, "duration": 6800, "geometry": {"coordinates": coords},
        "legs": [{"steps": steps}]}],
        "waypoints": [{"location": list(TOWN), "distance": 3},
                      {"location": list(TRAILHEAD), "distance": 3800, "name": "Мутновская дорога"}]}


def snap(point):
    return 800.0 if tuple(point) in OFF_NETWORK else 20.0


def parse_coords(url):
    part = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    return [tuple(float(v) for v in pair.split(",")) for pair in part.split(";")]


class FakeHttp:
    """Минимальная замена net.Http: отвечает по адресу запроса."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def check(self):
        pass

    def get(self, url, params=None, data=None, ttl=0, headers=None, tries=2):
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        self.calls.append(full)
        host = urllib.parse.urlsplit(url).hostname
        if host in self.fail:
            raise NetError("нет сети: " + host)
        if "terrarium" in url:
            z, x, y = [int(v) for v in url.rsplit(".", 1)[0].split("/")[-3:]]
            return terrarium_png(z, x, y)
        if host == "upload.wikimedia.org" or host == "static.inaturalist.org":
            return b"\xff\xd8\xff\xe0fakejpeg"
        return json.dumps(self.answer(url, params or {}, data)).encode("utf-8")

    def json(self, url, params=None, data=None, ttl=0, headers=None, tries=2):
        return json.loads(self.get(url, params, data, ttl, headers, tries).decode("utf-8"))

    def answer(self, url, params, data):
        host = urllib.parse.urlsplit(url).hostname
        path = urllib.parse.urlsplit(url).path
        if host == "nominatim.openstreetmap.org":
            if path.endswith("/search"):
                if "Петропавловск" in params["q"]:
                    return [{"lat": str(TOWN[1]), "lon": str(TOWN[0]), "name": "Петропавловск-Камчатский",
                             "category": "place", "type": "city", "osm_type": "node", "osm_id": 50}]
                return [{"lat": "52.5580", "lon": "158.0300", "name": "Горелый", "category": "natural",
                         "type": "volcano", "osm_type": "node", "osm_id": 1,
                         "display_name": "Горелый, Елизовский район, Камчатский край, Россия",
                         "address": {"state": "Камчатский край", "country": "Россия",
                                     "country_code": "ru"},
                         "extratags": {"ele": "1829", "wikipedia": "ru:Горелый (вулкан)",
                                       "wikidata": "Q1"},
                         "boundingbox": ["52.55", "52.57", "158.02", "158.04"]}]
            if params.get("zoom") == 5:
                return {"name": "Камчатский край", "boundingbox": ["50.8", "65.0", "155.5", "174.5"],
                        "geojson": {"type": "Polygon", "coordinates": [[
                            [155.5, 50.8], [165.0, 50.8], [165.0, 60.0], [155.5, 60.0], [155.5, 50.8]]]}}
            return {"address": {"village": "Термальный"}}
        if host in ("overpass-api.de", "maps.mail.ru", "overpass.kumi.systems"):
            query = data["data"] if isinstance(data, dict) else \
                urllib.parse.parse_qs(data.decode())["data"][0]
            if "is_in" in query:
                return {"elements": [{"type": "area", "id": 1, "tags": {
                    "boundary": "protected_area", "name": "Южно-Камчатский природный парк",
                    "protection_title": "природный парк"}}]}
            if '"place"~' in query:
                return {"elements": [{"type": "node", "id": 50, "lat": TOWN[1], "lon": TOWN[0],
                                      "tags": {"place": "city", "name": "Петропавловск-Камчатский"}}]}
            if "around:" in query:
                if "around:80000" in query:
                    return {"elements": [{"type": "node", "id": 60, "lat": 52.95, "lon": 158.40,
                                          "tags": {"shop": "convenience", "name": "Продукты"}},
                                         {"type": "node", "id": 61, "lat": 52.96, "lon": 158.41,
                                          "tags": {"amenity": "fuel", "name": "АЗС"}}]}
                return {"elements": []}
            return OVERPASS_POIS
        if host == "ru.wikipedia.org":
            if params.get("generator") == "geosearch":
                return WIKI_GEO
            if params.get("generator") == "search":
                return {"query": {"pages": [{"title": "Горелый (вулкан)", "index": 1,
                                             "description": "вулкан на Камчатке",
                                             "coordinates": [{"lat": 52.5581, "lon": 158.0301}]}]}}
            return WIKI_DETAILS
        if host == "www.wikidata.org":
            return {"entities": {}}
        if host == "ru.wikivoyage.org":
            if params.get("list") == "search":
                hits = [{"title": params["srsearch"]}] if "Камчат" in params["srsearch"] else []
                return {"query": {"search": hits}}
            if params.get("list") == "geosearch":
                return {"query": {"geosearch": []}}
            return {"query": {"pages": [{"title": "Южная Камчатка", "extract": WIKIVOYAGE_TEXT,
                                         "fullurl": "https://ru.wikivoyage.org/wiki/Южная_Камчатка"}]}}
        if host == "commons.wikimedia.org":
            lat, lon = [float(v) for v in params["ggscoord"].split("|")]
            return {"query": {"pages": [
                {"title": "File:Gorely %.3f.jpg" % lat, "coordinates": [{"lat": lat + 0.001, "lon": lon}],
                 "imageinfo": [{"thumburl": "https://upload.wikimedia.org/thumb/%.3f.jpg" % lat,
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:%.3f" % lat,
                                "extmetadata": {
                                    "ImageDescription": {"value": "<p>Вид на кратер, <b>август</b></p>"},
                                    "Artist": {"value": "<a href='x'>Иван</a>"},
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "DateTimeOriginal": {"value": "2019-08-10 12:00"}}}]},
                {"title": "File:Map.svg", "imageinfo": [{"thumburl": "x"}]},
            ]}}
        if host == "routing.openstreetmap.de":
            points = parse_coords(url)
            if "routed-car" in url:
                return car_route()
            if "/table/" in url:
                distances = [[geo.dist_m(a, b) * 1.3 for b in points] for a in points]
                return {"code": "Ok", "distances": distances,
                        "sources": [{"distance": snap(p)} for p in points]}
            a, b = points
            # тропа проходит через середину со смещением — чтобы было что рисовать
            mid = ((a[0] + b[0]) / 2 + 0.003, (a[1] + b[1]) / 2)
            snapped_a = (a[0] + 0.008, a[1]) if snap(a) > 100 else a
            snapped_b = (b[0] + 0.008, b[1]) if snap(b) > 100 else b
            coords = [snapped_a, mid, snapped_b]
            return {"code": "Ok", "routes": [{"distance": geo.line_length_m(coords) * 1.0,
                                              "geometry": {"coordinates": [list(c) for c in coords]}}],
                    "waypoints": [{"location": list(snapped_a), "distance": snap(a)},
                                  {"location": list(snapped_b), "distance": snap(b)}]}
        if host == "api.open-meteo.com":
            lats = params["latitude"].split(",")
            items = []
            for i, _ in enumerate(lats):
                items.append({"elevation": 600 + i * 1200, "daily": {
                    "time": ["2026-10-24", "2026-10-25"],
                    "temperature_2m_max": [6.0 - i * 8, 5.0], "temperature_2m_min": [-1.0 - i * 8, -2.0],
                    "precipitation_sum": [0.2, 4.0], "precipitation_probability_max": [10, 80],
                    "wind_speed_10m_max": [6.0 + i * 6, 9.0], "weather_code": [2, 61]}})
            return items if len(items) > 1 else items[0]
        if host == "api.inaturalist.org":
            if path.endswith("species_counts"):
                return {"results": [
                    {"count": 31, "taxon": {"id": 1, "name": "Spermophilus parryii",
                                            "preferred_common_name": "Американский суслик",
                                            "iconic_taxon_name": "Mammalia",
                                            "default_photo": {"medium_url": "https://static.inaturalist.org/s.jpg"}}},
                    {"count": 4, "taxon": {"id": 2, "name": "Ursus arctos",
                                           "preferred_common_name": "Бурый медведь",
                                           "iconic_taxon_name": "Mammalia"}},
                ]}
            return {"results": [
                {"description": "Суслики бегают у подножия, совсем не боятся людей.",
                 "species_guess": "Суслик", "user": {"login": "kamchatka_hiker"},
                 "observed_on": "2023-07-15", "uri": "https://www.inaturalist.org/observations/1",
                 "photos": [{"url": "https://static.inaturalist.org/photos/1/square.jpg"}]},
                {"description": "ок", "user": {"login": "x"}},
            ]}
        raise NetError("Неизвестный адрес в тесте: " + url)
