# -*- coding: utf-8 -*-
"""Сборка экскурсии в два шага, без QGIS (кроме декодирования тайлов рельефа).

1. collect() — «Подобрать попутные объекты»: регион, населённые пункты, тип туризма
   (город или природа), достопримечательности в зоне, их популярность, ООПТ.
2. plan() — «Создать экскурсию»: откуда ехать, дорога, порядок обхода, пеший маршрут,
   а параллельно — магазины/транспорт, описания, Викигид, погода, фото, природа; советы.

Ход работы сообщается по этапам: progress(ключ этапа, доля 0…1 или −1 «пропущен»).
"""

import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import advice, extras, geo, geocode, osm, routing, terrain, tsp, wiki
from .model import DriveRoute, Leg, Poi, Tour
from .net import Cancelled, NetError
from .text import norm_name

# В автоматический выбор не берём «служебные» места — только достопримечательности
NOT_AUTO = ("camp", "picnic", "hut")
LONG_WALK_M = {"nature": 25000, "urban": 12000}
URBAN_BUILDINGS = 150        # столько зданий в 700 м — уже город
NO_DRIVE_KM = 30             # в городе ближе этого — без машины, на транспорте

# (ключ, название, ожидаемая доля времени)
COLLECT_STAGES = [
    ("region", "Регион и населённые пункты", 3),
    ("kind", "Тип туризма: город или природа", 1),
    ("osm", "Достопримечательности OpenStreetMap", 4),
    ("wiki", "Статьи Википедии рядом", 3),
    ("popular", "Популярность объектов", 3),
    ("protected", "Охраняемые территории", 1),
]
PLAN_STAGES = [
    ("start", "Откуда ехать и дорога", 4),
    ("order", "Оптимальный порядок обхода", 2),
    ("legs", "Пеший маршрут по тропам и рельефу", 10),
    ("around", "Магазины, транспорт, кафе", 3),
    ("texts", "Описания объектов и Викигид", 3),
    ("weather", "Прогноз погоды", 1),
    ("photos", "Фотографии с Викисклада", 4),
    ("nature", "Животные и растения рядом", 2),
    ("advice", "Советы: одежда, еда, безопасность", 1),
]


@dataclass
class Settings:
    query: str = ""
    place: object = None        # Place, если уже выбран из списка найденных
    start_text: str = ""        # откуда ехать; пусто — региональный центр / крупный город
    half_km: float = 15.0       # зона поиска: ± от объекта
    max_points: int = 8
    mode: str = "auto"          # auto / nature / urban
    loop: bool = True
    date: str = ""
    people: int = 2
    lang: str = "ru"
    photos: bool = True
    nature: bool = True
    weather: bool = True
    chosen: tuple = None        # uid выбранных вручную; None — топ по популярности


class Planner:

    def __init__(self, http, settings, elevation=None, progress=None, log=None):
        self.http = http
        self.s = settings
        self.elevation = elevation
        self.progress = progress or (lambda key, fraction: None)
        self.log = log or (lambda text: None)

    def stage(self, key, fraction=0.0):
        self.http.check()
        self.progress(key, fraction)

    def soft(self, what, call, *args, **kwargs):
        """Необязательный шаг: сбой сети — предупреждение в журнал, работа идёт дальше."""
        try:
            return call(*args, **kwargs)
        except Cancelled:
            raise
        except NetError as error:
            self.log("⚠ %s: %s" % (what, error))
        except Exception as error:  # noqa: BLE001 - неожиданный ответ сервиса
            self.log("⚠ %s: %s: %s" % (what, type(error).__name__, error))
        return None

    # --- Полный прогон (для тестов и простого использования) ---

    def run(self):
        place, _ = self.find_place()
        tour = self.collect(place)
        self.plan(tour)
        return tour

    def find_place(self):
        if self.s.place is not None:
            return self.s.place, [self.s.place]
        places = geocode.geocode(self.http, self.s.query, self.s.lang)
        if not places:
            raise NetError("Объект «%s» не найден. Уточните название (например, «вулкан Горелый, "
                           "Камчатка»)." % self.s.query)
        return places[0], places

    # --- 1. Попутные объекты ---

    def collect(self, place):
        tour = Tour(place=place, half_km=self.s.half_km, loop=self.s.loop,
                    date=self.s.date or (datetime.date.today() + datetime.timedelta(days=1)).isoformat(),
                    people=self.s.people)
        tour.add_source("OpenStreetMap (Nominatim, Overpass)", "https://www.openstreetmap.org/copyright",
                        "ODbL")
        self.log("Объект: %s (%.5f, %.5f) — %s" % (place.name, place.lat, place.lon,
                                                  place.display_name or place.source))
        tour.bbox = geo.bbox_around(place.lon, place.lat, self.s.half_km)

        self.stage("region")
        region = self.soft("Граница региона", geocode.region_boundary, self.http,
                           place.lon, place.lat, self.s.lang)
        if region:
            tour.region_name, tour.region_geojson, tour.region_bbox, tour.region_osm_id = region
        self.stage("region", 0.4)
        self.find_settlements(tour)
        self.stage("region", 1)

        self.stage("kind")
        tour.mode = self.detect_mode(tour)
        self.log("Тип туризма: %s" % ("городской" if tour.urban else "природный"))
        self.stage("kind", 1)

        self.stage("osm")
        pois = self.soft("Объекты OSM", osm.find_pois, self.http, tour.bbox, place.point,
                         self.s.lang, tour.mode) or []
        self.stage("osm", 1)
        self.stage("wiki")
        articles = self.soft("Статьи Википедии", wiki.nearby_articles, self.http, tour.bbox,
                             place.point, self.s.lang, 300 if tour.urban else 100, tour.mode) or []
        if articles:
            tour.add_source("Википедия", "https://%s.wikipedia.org" % self.s.lang, "CC BY-SA 4.0")
        main = self.main_poi(place)
        candidates = [p for p in wiki.merge_articles(pois, articles) if not self.same_as_main(p, main)]
        self.stage("wiki", 1)

        self.stage("popular")
        self.soft("Популярность", wiki.popularity, self.http, [main] + candidates, self.s.lang)
        tour.candidates = [main] + osm.rank(candidates)
        self.log("Найдено объектов рядом: %d" % len(candidates))
        self.stage("popular", 1)

        if tour.urban:
            self.stage("protected", -1)
        else:
            self.stage("protected")
            tour.protected = self.soft("ООПТ", osm.protected_areas, self.http,
                                       place.lon, place.lat) or []
            self.stage("protected", 1)
        self.choose(tour)
        return tour

    def find_settlements(self, tour):
        place = tour.place
        capital, towns = None, []
        if tour.region_osm_id:
            found = self.soft("Населённые пункты региона", osm.region_settlements, self.http,
                              tour.region_osm_id)
            if found:
                capital, towns = found
        near = self.soft("Населённые пункты рядом", osm.near_settlements, self.http,
                         place.lon, place.lat) or []
        tour.capital = capital
        towns = sorted(towns, key=lambda t: -t.population)
        chosen = [capital] if capital else []
        chosen += [t for t in towns if t.population >= 200000] + towns[:10] + near
        seen = set()
        tour.settlements = []
        for item in chosen:
            if item.name not in seen:
                seen.add(item.name)
                tour.settlements.append(item)

    def detect_mode(self, tour):
        if self.s.mode in ("nature", "urban"):
            return self.s.mode
        place = tour.place
        if place.category in osm.NATURE_KEYS:
            return "nature"
        count = self.soft("Плотность застройки", osm.building_count, self.http, place.lon, place.lat)
        tour.building_count = count if count is not None else -1
        if count is None:
            return "urban" if place.category in osm.URBAN_KEYS and place.city else "nature"
        if count >= URBAN_BUILDINGS or (count >= 60 and place.category in osm.URBAN_KEYS):
            return "urban"
        return "nature"

    @staticmethod
    def main_poi(place):
        return Poi(uid="main", name=place.name, lon=place.lon, lat=place.lat,
                   category=place.category, kind=place.kind or "Главный объект", score=100,
                   wikipedia=place.wikipedia, wikidata=place.wikidata, elevation=place.elevation,
                   is_main=True, selected=True,
                   url="https://www.openstreetmap.org/" + place.osm_id if place.osm_id.strip("/") else "")

    @staticmethod
    def same_as_main(poi, main):
        d = geo.dist_m(poi.point, main.point)
        if poi.wikidata and poi.wikidata == main.wikidata:
            if not main.wikipedia:
                main.wikipedia = poi.wikipedia
            return True
        if norm_name(poi.name) == norm_name(main.name) and d < 3000:
            for attr in ("wikipedia", "wikidata", "elevation"):
                if not getattr(main, attr) and getattr(poi, attr):
                    setattr(main, attr, getattr(poi, attr))
            if poi.tags and not main.tags:
                main.tags = poi.tags
            return True
        return d < 60 and poi.category == main.category

    def choose(self, tour):
        """Отметить выбранные: вручную (chosen) или топ-N самых популярных."""
        others = tour.candidates[1:]
        if self.s.chosen is not None:
            chosen = set(self.s.chosen)
            for poi in others:
                poi.selected = poi.uid in chosen
        else:
            count = 0
            for poi in others:
                poi.selected = count < self.s.max_points and poi.category not in NOT_AUTO
                count += poi.selected

    # --- 2. Экскурсия ---

    def plan(self, tour):
        if self.s.chosen is not None:
            self.choose(tour)
        main = tour.candidates[0]
        elevation = None if tour.urban else self.elevation

        self.stage("start")
        start_point, start_name = self.start_and_drive(tour)
        self.stage("start", 1)
        start = Poi(uid="start", name=start_name, lon=start_point[0], lat=start_point[1],
                    category="start", kind="Начало пешего маршрута")
        stops = [start, main] + [p for p in tour.candidates[1:] if p.selected]

        self.stage("order")
        order = tsp.solve(self.walk_matrix(stops), tour.loop)
        tour.stops = [stops[i] for i in order]
        for poi in tour.candidates:
            poi.order = 0
        for number, poi in enumerate(tour.stops):
            poi.order = number
        self.stage("order", 1)

        self.stage("legs")
        self.build_legs(tour, elevation)
        self.stage("legs", 1)

        jobs = [("around", self.job_around), ("texts", self.job_texts),
                ("weather", self.job_weather if self.s.weather else None),
                ("photos", self.job_photos if self.s.photos else None),
                ("nature", self.job_nature if self.s.nature and not tour.urban else None)]
        with ThreadPoolExecutor(len(jobs)) as pool:
            futures = []
            for key, job in jobs:
                if job is None:
                    self.stage(key, -1)
                    continue
                futures.append(pool.submit(self.run_job, key, job, tour))
            for future in futures:
                future.result()   # Cancelled и ошибки — наружу

        self.stage("advice")
        advice.build(tour)
        limit = LONG_WALK_M[tour.mode]
        if tour.totals.get("walk_m", 0) > limit:
            tour.warnings.append("Пеший маршрут длиннее %d км — снимите галочки с части объектов "
                                 "или уменьшите зону поиска." % (limit // 1000))
        self.stage("advice", 1)
        return tour

    def run_job(self, key, job, tour):
        self.stage(key)
        job(tour)
        self.stage(key, 1)

    def start_and_drive(self, tour):
        """Точка начала пешего маршрута и её название; заполняет tour.drive."""
        place = tour.place
        start, reason = None, ""
        if self.s.start_text.strip():
            found = self.soft("Начальная точка", geocode.geocode, self.http, self.s.start_text,
                              self.s.lang, 3)
            if found:
                start, reason = (found[0].name, found[0].lon, found[0].lat), "указано вами"
            else:
                self.log("⚠ «%s» не найдено — еду из регионального центра" % self.s.start_text)
        if start is None:
            town, why = osm.pick_start(tour.capital, tour.settlements, place.point)
            if town:
                start, reason = (town.name, town.lon, town.lat), why
        if start is None:
            found = self.soft("Ближайший город", osm.nearest_town, self.http, place.lon, place.lat)
            if found:
                start, reason = found, "ближайший город"
        tour.start_reason = reason
        self.stage("start", 0.3)

        if tour.urban:
            tour.transport = self.soft("Транспорт", osm.transport_near, self.http,
                                       place.lon, place.lat) or []
            if tour.transport:
                tour.add_source("Остановки и станции — OpenStreetMap")
        if start is None:
            tour.drive = DriveRoute(note="Не удалось определить, откуда ехать.")
        elif tour.urban and geo.dist_m((start[1], start[2]), place.point) < NO_DRIVE_KM * 1000:
            tour.drive = DriveRoute(start_name=start[0], start=(start[1], start[2]),
                                    note="Объект в городе — удобнее на общественном транспорте.")
        else:
            tour.drive = self.drive_route(place, start)
        self.stage("start", 0.8)

        if tour.urban:
            near = [t for t in tour.transport if t[2] <= 1.2]
            if near:
                kind, name, _km, point = near[0]
                return point, "Старт: %s «%s»" % (kind.lower(), name)
            return place.point, "Старт"
        if tour.drive.ok:
            trailhead = tour.drive.trailhead
            nearby = self.soft("Название места старта", geocode.reverse_name, self.http,
                               trailhead[0], trailhead[1], self.s.lang)
            tour.drive.trailhead_name = nearby or tour.drive.trailhead_name
            return trailhead, "Старт: конец дороги" + (" (%s)" % nearby if nearby else "")
        return place.point, "Старт"

    def drive_route(self, place, start):
        route = routing.drive(self.http, (start[1], start[2]), start[0], place.point)
        if not route.ok:
            town = self.soft("Ближайший город", osm.nearest_town, self.http, place.lon, place.lat)
            if town and town[0] != start[0]:
                self.log("⚠ От «%s» проезда нет (%s) — строю от %s" % (start[0], route.note, town[0]))
                note = route.note
                route = routing.drive(self.http, (town[1], town[2]), town[0], place.point)
                if route.ok:
                    route.note = ("От «%s»: %s Маршрут на машине — от ближайшего города." % (
                        start[0], note))
        self.log("Дорога: %s" % (("%s, %.0f км" % (route.start_name, route.distance_m / 1000))
                                 if route.ok else route.note))
        return route

    def walk_matrix(self, stops):
        points = [s.point for s in stops]
        table, snaps = routing.osrm_table(self.http, routing.FOOT, points)
        n = len(points)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                direct = geo.dist_m(points[i], points[j])
                value = table[i][j] if table else None
                if value is None:
                    matrix[i][j] = direct * 1.6
                else:
                    # путь вне троп считаем вдвое «дороже»
                    off = 2.0 * (snaps[i] + snaps[j])
                    matrix[i][j] = min(value + off, direct * 2.5)
        return matrix

    def build_legs(self, tour, elevation):
        finder = terrain.offtrail_finder(elevation)
        pairs = list(zip(range(len(tour.stops)), range(1, len(tour.stops))))
        if tour.loop and len(tour.stops) > 1:
            pairs.append((len(tour.stops) - 1, 0))
        tour.legs = []
        full_profile = []
        offset = 0.0
        for number, (a, b) in enumerate(pairs):
            self.stage("legs", number / float(len(pairs)))
            segments = routing.foot_segments(self.http, tour.stops[a].point, tour.stops[b].point,
                                             finder)
            leg = Leg(a=a, b=b, segments=segments)
            for segment in segments:
                prof = terrain.profile(elevation, segment.coords) if elevation else []
                up, down, _, _ = terrain.climb(prof)
                leg.ascent += up
                leg.descent += down
                leg.hours += terrain.walk_hours(prof, segment.length_m, segment.kind == "offtrail")
                leg.length_m += segment.length_m
                if segment.kind == "offtrail":
                    leg.offtrail_m += segment.length_m
                full_profile += [(offset + d, h) for d, h in prof]
                offset += segment.length_m
            tour.legs.append(leg)
        tour.profile = full_profile
        up, down, low, high = terrain.climb(full_profile)
        t = tour.totals
        t["walk_m"] = sum(leg.length_m for leg in tour.legs)
        t["offtrail_m"] = sum(leg.offtrail_m for leg in tour.legs)
        t["hours_walk"] = sum(leg.hours for leg in tour.legs)
        if tour.urban:
            minutes = sum(osm.VISIT_MINUTES.get(s.category, 20) for s in tour.stops[1:])
        else:
            minutes = (len(tour.stops) - 1) * advice.STOP_MINUTES
        t["hours_stops"] = minutes / 60.0
        t["ascent"], t["descent"] = round(up), round(down)
        t["min_h"], t["max_h"] = low, high
        t["start_h"] = full_profile[0][1] if full_profile else None
        if elevation:
            for poi in tour.stops:
                if poi.elevation is None:
                    poi.elevation = self.soft("Высота", elevation.sample, poi.lon, poi.lat)
            top = max(tour.stops, key=lambda p: p.elevation if p.elevation is not None else -1e9)
            if top.elevation is not None and (high is None or top.elevation > high):
                t["max_h"] = top.elevation
            t["top_point"] = top.point
            tour.dem_name = getattr(elevation, "name", "")
            tour.add_source("Рельеф: " + tour.dem_name, getattr(elevation, "url", ""), "")
        tour.add_source("Маршруты: OSRM, серверы FOSSGIS", "https://routing.openstreetmap.de",
                        "данные © OpenStreetMap")

    # --- Параллельные задания ---

    def job_around(self, tour):
        start = tour.stops[0].point
        if tour.urban:
            coords = [c for leg in tour.legs for s in leg.segments for c in s.coords] or [start]
            middle = (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))
            tour.food = self.soft("Кафе", osm.places_to_eat, self.http, middle[0], middle[1]) or []
        else:
            tour.services = self.soft("Магазины и заправки", osm.nearest_services, self.http,
                                      start[0], start[1]) or {}

    def job_texts(self, tour):
        place = tour.place
        self.soft("Описания", wiki.fill_details, self.http, tour.stops[1:], self.s.lang)
        if tour.urban:
            names = [place.city, place.name, place.region, tour.region_name]
        else:
            names = [place.name, tour.drive.trailhead_name if tour.drive else "", place.region,
                     tour.region_name]
        tour.wikivoyage = self.soft("Викигид", wiki.wikivoyage, self.http, names, place.lon,
                                    place.lat, self.s.lang) or {}
        if tour.wikivoyage:
            tour.add_source("Викигид: " + tour.wikivoyage["title"], tour.wikivoyage["url"],
                            "CC BY-SA 3.0")

    def job_weather(self, tour):
        start = tour.stops[0].point
        points = [("Начало маршрута", start[0], start[1], tour.totals.get("start_h"))]
        top = tour.totals.get("top_point")
        if not tour.urban and top and tour.totals.get("max_h") is not None:
            points.append(("Высшая точка маршрута", top[0], top[1], tour.totals["max_h"]))
        tour.weather = self.soft("Погода", extras.weather, self.http, points, tour.date) or []
        if tour.weather:
            tour.add_source("Open-Meteo", "https://open-meteo.com", "CC BY 4.0")

    def job_photos(self, tour):
        def fetch(poi):
            radius, limit = ((800, 6) if tour.urban else (2500, 8)) if poi.is_main else \
                ((250, 2) if tour.urban else (600, 2))
            return self.soft("Фото «%s»" % poi.name, wiki.commons_photos, self.http,
                             poi.lon, poi.lat, radius, limit) or []

        stops = tour.stops[1:]
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(fetch, stops))
        seen = set()
        tour.photos = []
        for poi, photos in zip(stops, results):
            poi.photos = []
            for photo in photos:
                if photo.page_url in seen:
                    continue
                seen.add(photo.page_url)
                photo.poi_uid, photo.poi_name = poi.uid, poi.name
                poi.photos.append(photo)
                tour.photos.append(photo)
        # Номера — по порядку обхода; на карте фотографий и в отчёте они совпадают
        for number, photo in enumerate(tour.photos, 1):
            photo.number = number
        if tour.photos:
            tour.add_source("Wikimedia Commons", "https://commons.wikimedia.org",
                            "лицензия каждого снимка указана под ним")

    def job_nature(self, tour):
        tour.observations = self.soft("iNaturalist", extras.species, self.http, tour.bbox,
                                      18, self.s.lang) or []
        animals = self.soft("iNaturalist", extras.species, self.http, tour.bbox, 40,
                            self.s.lang, "Mammalia,Reptilia,Arachnida") or []
        known = {o.latin for o in tour.observations}
        tour.observations += [o for o in animals if o.latin not in known]
        tour.observer_notes = self.soft("Заметки iNaturalist", extras.observer_notes,
                                        self.http, tour.bbox, 40, self.s.lang) or []
        if tour.observations:
            tour.add_source("iNaturalist", "https://www.inaturalist.org",
                            "лицензии наблюдений указаны их авторами")
