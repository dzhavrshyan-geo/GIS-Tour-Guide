# -*- coding: utf-8 -*-
"""Данные экскурсии. Координаты — (lon, lat)."""

from dataclasses import dataclass, field


@dataclass
class Place:
    """Найденный объект (результат геокодирования)."""
    name: str
    lon: float
    lat: float
    kind: str = ""              # «Вулкан», «Пещера»…
    category: str = ""          # ключ категории (volcano, cave…)
    display_name: str = ""
    region: str = ""
    country: str = ""
    country_code: str = ""
    osm_id: str = ""            # node/123
    wikipedia: str = ""         # ru:Горелый (вулкан)
    wikidata: str = ""
    elevation: float = None
    bbox: tuple = None          # (west, south, east, north)
    source: str = "OSM"

    @property
    def point(self):
        return (self.lon, self.lat)


@dataclass
class Poi:
    uid: str
    name: str
    lon: float
    lat: float
    category: str = ""
    kind: str = ""
    score: float = 0.0
    tags: dict = field(default_factory=dict)
    wikipedia: str = ""
    wikidata: str = ""
    description: str = ""       # краткое описание (Википедия / OSM)
    extract: str = ""           # подробнее
    url: str = ""
    image_url: str = ""
    image_path: str = ""        # скачанная картинка из Википедии
    elevation: float = None
    distance_km: float = 0.0    # от главного объекта
    selected: bool = False
    is_main: bool = False
    order: int = 0              # номер на маршруте (0 — не на маршруте)
    photos: list = field(default_factory=list)

    @property
    def point(self):
        return (self.lon, self.lat)


@dataclass
class Photo:
    title: str
    thumb_url: str
    page_url: str
    lon: float = None
    lat: float = None
    description: str = ""
    author: str = ""
    license: str = ""
    date: str = ""
    poi_uid: str = ""
    local_path: str = ""
    source: str = "Wikimedia Commons"


@dataclass
class Observation:
    """Вид, который видели рядом (iNaturalist)."""
    name: str
    latin: str
    group: str = ""
    count: int = 0
    photo_url: str = ""
    photo_credit: str = ""
    url: str = ""
    notes: list = field(default_factory=list)   # [(текст, автор, дата, ссылка)]
    local_path: str = ""


@dataclass
class Segment:
    kind: str                   # trail — по дорогам и тропам, offtrail — по рельефу
    coords: list
    length_m: float = 0.0


@dataclass
class Leg:
    a: int                      # индексы в Tour.stops
    b: int
    segments: list = field(default_factory=list)
    length_m: float = 0.0
    offtrail_m: float = 0.0
    ascent: float = 0.0
    descent: float = 0.0
    hours: float = 0.0
    note: str = ""


@dataclass
class DriveRoute:
    start_name: str = ""
    start: tuple = None
    coords: list = field(default_factory=list)
    distance_m: float = 0.0
    duration_s: float = 0.0
    roads: list = field(default_factory=list)   # [(название дороги, метры)]
    trailhead: tuple = None     # где кончается дорога для машины
    trailhead_name: str = ""
    last_mile_m: float = 0.0    # от конца дороги до объекта по прямой
    ok: bool = False
    note: str = ""


@dataclass
class Tour:
    place: Place
    half_km: float = 15.0
    bbox: tuple = None
    region_name: str = ""
    region_geojson: dict = None
    region_bbox: tuple = None
    candidates: list = field(default_factory=list)   # все найденные Poi
    stops: list = field(default_factory=list)        # Poi в порядке обхода (0 — старт)
    legs: list = field(default_factory=list)
    loop: bool = True
    drive: DriveRoute = None
    profile: list = field(default_factory=list)      # [(метры от старта, высота)]
    totals: dict = field(default_factory=dict)
    weather: list = field(default_factory=list)      # [{label, elevation, days:[...]}]
    services: dict = field(default_factory=dict)
    protected: list = field(default_factory=list)    # [(название, класс)]
    wikivoyage: dict = field(default_factory=dict)   # {title, url, sections: {..}}
    photos: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    observer_notes: list = field(default_factory=list)
    advice: list = field(default_factory=list)       # [(раздел, [пункты])]
    warnings: list = field(default_factory=list)
    sources: list = field(default_factory=list)      # [(название, ссылка, лицензия)]
    dem_name: str = ""
    date: str = ""                                   # YYYY-MM-DD
    people: int = 2

    def add_source(self, name, url="", license_=""):
        item = (name, url, license_)
        if item not in self.sources:
            self.sources.append(item)
