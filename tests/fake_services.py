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


NEAR_VILLAGES = [
    {"type": "node", "id": 54, "lat": 52.83, "lon": 158.18, "tags": {"place": "village", "name": "Термальный"}},
    {"type": "node", "id": 55, "lat": 52.91, "lon": 158.22, "tags": {"place": "village", "name": "Паратунка"}},
]
REGION_TOWNS = [
    {"type": "node", "id": 50, "lat": TOWN[1], "lon": TOWN[0], "tags": {
        "place": "city", "name": "Петропавловск-Камчатский", "population": "164900"}},
    {"type": "node", "id": 51, "lat": 53.19, "lon": 158.38, "tags": {
        "place": "town", "name": "Елизово", "population": "38000"}},
    {"type": "node", "id": 52, "lat": 52.93, "lon": 158.40, "tags": {
        "place": "town", "name": "Вилючинск", "population": "21000"}},
    {"type": "node", "id": 53, "lat": 56.32, "lon": 160.84, "tags": {
        "place": "town", "name": "Ключи", "population": "4500"}},
]
NATURE_LODGING = [
    {"type": "node", "id": 701, "lat": 52.92, "lon": 158.21, "tags": {
        "tourism": "hotel", "name": "Паратунка Резорт", "stars": "4", "website": "https://resort.example"}},
    {"type": "node", "id": 702, "lat": 52.60, "lon": 158.09, "tags": {
        "tourism": "guest_house", "name": "Гостевой дом у ключей", "phone": "+7 900 000-00-00"}},
    {"type": "node", "id": 703, "lat": 52.586, "lon": 158.077, "tags": {
        "tourism": "camp_site", "name": "Стоянка у Горелого"}},
    {"type": "node", "id": 704, "lat": 52.83, "lon": 158.18, "tags": {
        "tourism": "hotel", "name": "Гостиница Термальная", "stars": "3"}},
]

# --- Город: Московский Кремль ---

KREMLIN = (37.6175, 55.7520)

URBAN_POIS = {"elements": [
    {"type": "way", "id": 101, "center": {"lat": 55.7539, "lon": 37.6208},
     "tags": {"place": "square", "name": "Красная площадь", "wikidata": "Q10"}},
    {"type": "way", "id": 102, "center": {"lat": 55.7525, "lon": 37.6231},
     "tags": {"amenity": "place_of_worship", "name": "Собор Василия Блаженного", "wikidata": "Q11",
              "wikipedia": "ru:Храм Василия Блаженного", "opening_hours": "Mo-Su 11:00-17:00",
              "fee": "yes"}},
    {"type": "way", "id": 103, "center": {"lat": 55.7553, "lon": 37.6178},
     "tags": {"tourism": "museum", "name": "Государственный исторический музей", "wikidata": "Q12",
              "opening_hours": "Mo,We-Su 10:00-18:00", "website": "https://shm.ru"}},
    {"type": "way", "id": 104, "center": {"lat": 55.7527, "lon": 37.6129},
     "tags": {"leisure": "park", "name": "Александровский сад", "wikidata": "Q13"}},
    {"type": "way", "id": 105, "center": {"lat": 55.7601, "lon": 37.6186},
     "tags": {"amenity": "theatre", "name": "Большой театр", "wikidata": "Q14"}},
    {"type": "node", "id": 106, "lat": 55.7510, "lon": 37.6175,
     "tags": {"historic": "monument", "name": "Царь-пушка", "wikidata": "Q15"}},
    {"type": "node", "id": 107, "lat": 55.7530, "lon": 37.6150,
     "tags": {"historic": "memorial", "memorial": "plaque", "name": "Доска", "wikidata": "Q16"}},
    {"type": "way", "id": 108, "center": {"lat": 55.7600, "lon": 37.6100},
     "tags": {"amenity": "place_of_worship", "name": "Церковь без статьи"}},   # не берём
]}

URBAN_WIKI_GEO = {"query": {"pages": [
    {"title": "Большой Кремлёвский дворец", "description": "дворец в Московском Кремле",
     "coordinates": [{"lat": 55.7499, "lon": 37.6155}], "pageprops": {"wikibase_item": "Q20"}},
    {"title": "Охотный Ряд (станция метро)", "description": "станция Московского метрополитена",
     "coordinates": [{"lat": 55.7577, "lon": 37.6156}]},
    {"title": "Тверская улица", "description": "улица в Москве",
     "coordinates": [{"lat": 55.7600, "lon": 37.6110}]},
    {"title": "Красная площадь", "description": "площадь в Москве",
     "coordinates": [{"lat": 55.7539, "lon": 37.6208}], "pageprops": {"wikibase_item": "Q10"}},
]}}

PAGEVIEWS = {"Горелый (вулкан)": 120, "Вилючинский водопад": 300, "Кратер Активный": 15,
             "Московский Кремль": 5000, "Красная площадь": 3000, "Храм Василия Блаженного": 2500,
             "Государственный исторический музей": 800, "Большой театр": 1500,
             "Большой Кремлёвский дворец": 400, "Александровский сад (Москва)": 300, "Царь-пушка": 900}
WIKIDATA_TITLES = {"Q12": "Государственный исторический музей", "Q13": "Александровский сад (Москва)",
                   "Q14": "Большой театр", "Q15": "Царь-пушка", "Q10": "Красная площадь"}


MOSCOW_TOWNS = [
    {"type": "node", "id": 1, "lat": 55.7558, "lon": 37.6173, "tags": {
        "place": "city", "name": "Москва", "population": "13010112", "capital": "yes"}},
    {"type": "node", "id": 2, "lat": 55.9825, "lon": 37.1814, "tags": {
        "place": "city", "name": "Зеленоград", "population": "250000"}},
]
URBAN_LODGING = [
    {"type": "node", "id": 801, "lat": 55.7586, "lon": 37.6216, "tags": {
        "tourism": "hotel", "name": "Метрополь", "stars": "5"}},
    {"type": "node", "id": 802, "lat": 55.7570, "lon": 37.6300, "tags": {
        "tourism": "hotel", "name": "Отель в центре", "stars": "3"}},
    {"type": "node", "id": 803, "lat": 55.7590, "lon": 37.6250, "tags": {
        "tourism": "hostel", "name": "Хостел на Никольской"}},
]


def fake_jpeg(seed):
    from qgis.PyQt.QtGui import QColor, QImage
    from gis_tour_guide.report import image_bytes
    image = QImage(64, 40, QImage.Format.Format_RGB32)
    image.fill(QColor.fromHsv(seed * 37 % 360, 120, 200))
    return image_bytes(image, "JPG", 80)


class FakeHttp:
    """Минимальная замена net.Http: отвечает по адресу запроса.

    scenario — "nature" (вулкан Горелый) или "urban" (Московский Кремль).
    """

    def __init__(self, fail=(), scenario="nature"):
        self.calls = []
        self.fail = set(fail)
        self.scenario = scenario

    def check(self):
        pass

    def get(self, url, params=None, data=None, ttl=0, headers=None, tries=2, timeout=None):
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        self.calls.append(full)
        host = urllib.parse.urlsplit(url).hostname
        if host in self.fail:
            raise NetError("нет сети: " + host)
        if "terrarium" in url:
            z, x, y = [int(v) for v in url.rsplit(".", 1)[0].split("/")[-3:]]
            return terrarium_png(z, x, y)
        if host == "upload.wikimedia.org" or host == "static.inaturalist.org":
            return fake_jpeg(len(url))
        return json.dumps(self.answer(url, params or {}, data)).encode("utf-8")

    def json(self, url, params=None, data=None, ttl=0, headers=None, tries=2, timeout=None):
        return json.loads(self.get(url, params, data, ttl, headers, tries).decode("utf-8"))

    def answer(self, url, params, data):
        host = urllib.parse.urlsplit(url).hostname
        path = urllib.parse.urlsplit(url).path
        if self.scenario == "urban":
            urban = self.urban_answer(host, path, params, data)
            if urban is not None:
                return urban
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
                        "osm_type": "relation", "osm_id": 151233,
                        "geojson": {"type": "Polygon", "coordinates": [[
                            [155.5, 50.8], [165.0, 50.8], [165.0, 60.0], [155.5, 60.0], [155.5, 50.8]]]}}
            return {"address": {"village": "Термальный"}}
        if host in ("overpass-api.de", "maps.mail.ru", "overpass.kumi.systems"):
            query = data["data"] if isinstance(data, dict) else \
                urllib.parse.parse_qs(data.decode())["data"][0]
            if "is_in" in query and "out center tags" in query:
                # общий запрос: объекты + посёлки рядом + ООПТ и регион + застройка
                return {"elements": OVERPASS_POIS["elements"] + NEAR_VILLAGES + [
                    {"type": "area", "id": 1, "tags": {
                        "boundary": "protected_area", "name": "Южно-Камчатский природный парк",
                        "protection_title": "природный парк"}},
                    {"type": "area", "id": 3600151233, "tags": {
                        "boundary": "administrative", "admin_level": "4", "name": "Камчатский край"}},
                    {"type": "count", "id": 0, "tags": {"ways": "0", "total": "0"}}]}
            if '"tourism"~"^(hotel' in query:
                return {"elements": NATURE_LODGING}
            if "is_in" in query:
                return {"elements": [{"type": "area", "id": 1, "tags": {
                    "boundary": "protected_area", "name": "Южно-Камчатский природный парк",
                    "protection_title": "природный парк"}}]}
            if "out count" in query:
                return {"elements": [{"type": "count", "id": 0, "tags": {"ways": "0", "total": "0"}}]}
            if "admin_centre" in query:
                return {"elements": [{"type": "node", "id": 50}] + REGION_TOWNS}
            if "area(" in query:
                return {"elements": [
                    {"type": "node", "id": 50, "lat": TOWN[1], "lon": TOWN[0], "tags": {
                        "place": "city", "name": "Петропавловск-Камчатский", "population": "164900",
                        "capital": "4"}},
                    {"type": "node", "id": 51, "lat": 53.19, "lon": 158.38, "tags": {
                        "place": "town", "name": "Елизово", "population": "38000"}},
                    {"type": "node", "id": 52, "lat": 52.93, "lon": 158.40, "tags": {
                        "place": "town", "name": "Вилючинск", "population": "21000"}},
                    {"type": "node", "id": 53, "lat": 56.32, "lon": 160.84, "tags": {
                        "place": "town", "name": "Ключи", "population": "4500"}}]}
            if '"place"~"^(city|town|village)$"' in query:
                return {"elements": NEAR_VILLAGES}
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
            if params.get("prop") == "pageviews":
                return pageviews(params)
            if params.get("generator") == "geosearch":
                return WIKI_GEO
            if params.get("generator") == "search":
                return {"query": {"pages": [{"title": "Горелый (вулкан)", "index": 1,
                                             "description": "вулкан на Камчатке",
                                             "coordinates": [{"lat": 52.5581, "lon": 158.0301}]}]}}
            return WIKI_DETAILS
        if host == "www.wikidata.org":
            return {"entities": {q: {"sitelinks": {"ruwiki": {"title": WIKIDATA_TITLES[q]},
                                                   "enwiki": {"title": "x"}, "dewiki": {"title": "y"}}}
                                 for q in params["ids"].split("|") if q in WIKIDATA_TITLES}}
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


    def urban_answer(self, host, path, params, data):
        """Ответы для городского сценария; None — как в природном."""
        if host == "nominatim.openstreetmap.org":
            if path.endswith("/search"):
                return [{"lat": str(KREMLIN[1]), "lon": str(KREMLIN[0]), "name": "Московский Кремль",
                         "category": "historic", "type": "castle", "osm_type": "way", "osm_id": 900,
                         "display_name": "Московский Кремль, Тверской район, Москва, Россия",
                         "address": {"city": "Москва", "state": "Москва", "country": "Россия",
                                     "country_code": "ru"},
                         "extratags": {"wikidata": "Q900", "wikipedia": "ru:Московский Кремль"}}]
            if params.get("zoom") == 5:
                return {"name": "Москва", "boundingbox": ["55.14", "56.02", "36.80", "37.97"],
                        "osm_type": "relation", "osm_id": 102269,
                        "geojson": {"type": "Polygon", "coordinates": [[
                            [36.8, 55.14], [37.97, 55.14], [37.97, 56.02], [36.8, 56.02], [36.8, 55.14]]]}}
            return {"address": {"suburb": "Тверской район"}}
        if host in ("overpass-api.de", "maps.mail.ru", "overpass.kumi.systems"):
            query = data["data"] if isinstance(data, dict) else \
                urllib.parse.parse_qs(data.decode())["data"][0]
            if "is_in" in query and "out center tags" in query:
                return {"elements": URBAN_POIS["elements"] + [
                    {"type": "area", "id": 3600102269, "tags": {
                        "boundary": "administrative", "admin_level": "4", "name": "Москва"}},
                    {"type": "count", "id": 0, "tags": {"ways": "2400"}}]}
            if '"tourism"~"^(hotel' in query:
                return {"elements": URBAN_LODGING}
            if "out count" in query:
                return {"elements": [{"type": "count", "id": 0, "tags": {"ways": "2400"}}]}
            if "admin_centre" in query:
                return {"elements": [{"type": "node", "id": 1}] + MOSCOW_TOWNS}
            if "area(" in query:
                return {"elements": [
                    {"type": "node", "id": 1, "lat": 55.7558, "lon": 37.6173, "tags": {
                        "place": "city", "name": "Москва", "population": "13010112", "capital": "yes"}},
                    {"type": "node", "id": 2, "lat": 55.9825, "lon": 37.1814, "tags": {
                        "place": "city", "name": "Зеленоград", "population": "250000"}}]}
            if '"station"="subway"' in query:
                return {"elements": [
                    {"type": "node", "id": 301, "lat": 55.7577, "lon": 37.6156,
                     "tags": {"station": "subway", "railway": "station", "name": "Охотный Ряд"}},
                    {"type": "node", "id": 302, "lat": 55.7566, "lon": 37.6216,
                     "tags": {"station": "subway", "railway": "station", "name": "Площадь Революции"}},
                    {"type": "node", "id": 303, "lat": 55.7530, "lon": 37.6110,
                     "tags": {"highway": "bus_stop", "name": "Александровский сад"}}]}
            if '"amenity"~"^(cafe|restaurant)$"' in query:
                return {"elements": [
                    {"type": "node", "id": 401, "lat": 55.7545, "lon": 37.6210,
                     "tags": {"amenity": "cafe", "name": "Кофейня у ГУМа", "cuisine": "coffee_shop"}},
                    {"type": "node", "id": 402, "lat": 55.7560, "lon": 37.6190,
                     "tags": {"amenity": "restaurant", "name": "Столовая №57", "cuisine": "russian"}}]}
            if '"place"~"^(city|town|village)$"' in query:
                return {"elements": []}
            if "around:" in query or "is_in" in query:
                return {"elements": []}
            return URBAN_POIS
        if host == "ru.wikipedia.org":
            if params.get("prop") == "pageviews":
                return pageviews(params)
            if params.get("generator") == "geosearch":
                return URBAN_WIKI_GEO
            return {"query": {"pages": [
                {"title": "Московский Кремль", "extract": "Московский Кремль — крепость в центре Москвы.",
                 "fullurl": "https://ru.wikipedia.org/wiki/Московский_Кремль",
                 "thumbnail": {"source": "https://upload.wikimedia.org/kremlin.jpg"}}]}}
        if host == "ru.wikivoyage.org":
            if params.get("list") == "search":
                hits = [{"title": "Москва"}] if params["srsearch"] == "Москва" else []
                return {"query": {"search": hits}}
            if params.get("list") == "geosearch":
                return {"query": {"geosearch": []}}
            return {"query": {"pages": [{"title": "Москва", "fullurl": "https://ru.wikivoyage.org/wiki/Москва",
                                         "extract": "== Как добраться ==\nПоездом до вокзалов.\n"
                                                    "== Где поесть ==\nСтоловые в центре."}]}}
        if host == "api.inaturalist.org":
            raise AssertionError("в городе природу не запрашиваем")
        return None


def pageviews(params):
    pages = []
    for title in params["titles"].split("|"):
        views = PAGEVIEWS.get(title)
        page = {"title": title}
        if views is not None:
            page["pageviews"] = {"2026-10-%02d" % d: views for d in range(1, 31)}
            page["pageviews"]["2026-10-31"] = None
        pages.append(page)
    return {"query": {"pages": pages}}
