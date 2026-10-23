# -*- coding: utf-8 -*-
"""Сборка экскурсии: поиск объекта, окрестностей, дорог, маршрута и сведений о нём.

Работает без QGIS (кроме декодирования тайлов рельефа) — запускается в фоновой задаче.
"""

import datetime
from dataclasses import dataclass

from . import advice, extras, geo, geocode, osm, routing, terrain, tsp, wiki
from .model import DriveRoute, Leg, Poi, Tour
from .net import Cancelled, NetError
from .text import norm_name

# В автоматический выбор не берём «служебные» места — только достопримечательности
NOT_AUTO = ("camp", "picnic", "hut")
LONG_WALK_M = 25000


@dataclass
class Settings:
    query: str = ""
    place: object = None        # Place, если уже выбран из списка найденных
    start_text: str = ""        # откуда ехать; пусто — ближайший город
    half_km: float = 15.0       # зона поиска: ± от объекта
    max_points: int = 8
    loop: bool = True
    date: str = ""
    people: int = 2
    lang: str = "ru"
    photos: bool = True
    nature: bool = True
    weather: bool = True
    chosen: tuple = None        # uid выбранных вручную; None — выбрать самим


class Planner:

    def __init__(self, http, settings, elevation=None, progress=None, log=None):
        self.http = http
        self.s = settings
        self.elevation = elevation
        self.progress = progress or (lambda pct, text: None)
        self.log = log or (lambda text: None)

    def step(self, pct, text):
        self.http.check()
        self.progress(pct, text)
        self.log(text)

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

    # --- 1. Объект и окрестности ---

    def find_place(self):
        if self.s.place is not None:
            return self.s.place, [self.s.place]
        self.step(2, "Ищу «%s»…" % self.s.query)
        places = geocode.geocode(self.http, self.s.query, self.s.lang)
        if not places:
            raise NetError("Объект «%s» не найден. Уточните название (например, «вулкан Горелый, "
                           "Камчатка»)." % self.s.query)
        return places[0], places

    def run(self):
        place, _ = self.find_place()
        tour = Tour(place=place, half_km=self.s.half_km, loop=self.s.loop,
                    date=self.s.date or (datetime.date.today() + datetime.timedelta(days=1)).isoformat(),
                    people=self.s.people)
        tour.add_source("OpenStreetMap (Nominatim, Overpass)", "https://www.openstreetmap.org/copyright",
                        "ODbL")
        self.log("Объект: %s (%.5f, %.5f) — %s" % (place.name, place.lat, place.lon,
                                                  place.display_name or place.source))
        tour.bbox = geo.bbox_around(place.lon, place.lat, self.s.half_km)

        self.step(6, "Регион…")
        region = self.soft("Граница региона", geocode.region_boundary, self.http,
                           place.lon, place.lat, self.s.lang)
        if region:
            tour.region_name, tour.region_geojson, tour.region_bbox = region
        if place.elevation is None and self.elevation:
            place.elevation = self.soft("Высота объекта", self.elevation.sample, place.lon, place.lat)

        self.step(12, "Ищу достопримечательности в зоне ±%d км…" % self.s.half_km)
        pois = self.soft("Объекты OSM", osm.find_pois, self.http, tour.bbox, place.point,
                         self.s.lang) or []
        self.step(20, "Статьи Википедии об окрестностях…")
        articles = self.soft("Статьи Википедии", wiki.nearby_articles, self.http, tour.bbox,
                             place.point, self.s.lang) or []
        if articles:
            tour.add_source("Википедия", "https://%s.wikipedia.org" % self.s.lang, "CC BY-SA 4.0")
        main = self.main_poi(place)
        candidates = [p for p in wiki.merge_articles(pois, articles) if not self.same_as_main(p, main)]
        tour.candidates = [main] + osm.rank(candidates)
        self.log("Найдено объектов рядом: %d" % len(candidates))

        self.step(26, "Охраняемые территории…")
        tour.protected = self.soft("ООПТ", osm.protected_areas, self.http, place.lon, place.lat) or []

        self.choose(tour)
        self.plan(tour)
        return tour

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
            return True
        return d < 60 and poi.category == main.category

    def choose(self, tour):
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

    # --- 2. Дорога, маршрут, сведения ---

    def plan(self, tour):
        place = tour.place
        main = tour.candidates[0]
        self.step(30, "Дорога до объекта…")
        tour.drive = self.drive_route(place)
        trailhead = tour.drive.trailhead if tour.drive and tour.drive.ok else place.point
        start_name = "Старт"
        if tour.drive and tour.drive.ok:
            nearby = self.soft("Название места старта", geocode.reverse_name, self.http,
                               trailhead[0], trailhead[1], self.s.lang)
            start_name = "Старт: конец дороги" + (" (%s)" % nearby if nearby else "")
            tour.drive.trailhead_name = nearby or tour.drive.trailhead_name
        start = Poi(uid="start", name=start_name, lon=trailhead[0], lat=trailhead[1],
                    category="start", kind="Начало пешего маршрута")
        stops = [start, main] + [p for p in tour.candidates[1:] if p.selected]

        self.step(40, "Порядок обхода %d точек…" % (len(stops) - 1))
        order = tsp.solve(self.walk_matrix(stops), tour.loop)
        tour.stops = [stops[i] for i in order]
        for poi in tour.candidates:
            poi.order = 0
        for number, poi in enumerate(tour.stops):
            poi.order = number

        self.step(46, "Прокладываю пеший маршрут по тропам и рельефу…")
        self.build_legs(tour)

        self.step(62, "Магазины, заправки и вода рядом…")
        tour.services = self.soft("Магазины и заправки", osm.nearest_services, self.http,
                                  trailhead[0], trailhead[1]) or {}

        if self.s.weather:
            self.step(66, "Прогноз погоды…")
            points = [("Начало маршрута", trailhead[0], trailhead[1], tour.totals.get("start_h"))]
            top = tour.totals.get("top_point")
            if top and tour.totals.get("max_h") is not None:
                points.append(("Высшая точка маршрута", top[0], top[1], tour.totals["max_h"]))
            tour.weather = self.soft("Погода", extras.weather, self.http, points, tour.date) or []
            if tour.weather:
                tour.add_source("Open-Meteo", "https://open-meteo.com", "CC BY 4.0")

        self.step(72, "Описания объектов из Википедии…")
        self.soft("Описания", wiki.fill_details, self.http, tour.stops[1:], self.s.lang)

        self.step(78, "Как добраться: Викигид…")
        names = [place.name, tour.drive.trailhead_name if tour.drive else "", place.region,
                 tour.region_name]
        tour.wikivoyage = self.soft("Викигид", wiki.wikivoyage, self.http, names, place.lon,
                                    place.lat, self.s.lang) or {}
        if tour.wikivoyage:
            tour.add_source("Викигид: " + tour.wikivoyage["title"], tour.wikivoyage["url"],
                            "CC BY-SA 3.0")

        if self.s.photos:
            self.step(84, "Фотографии с Викисклада…")
            self.find_photos(tour)
        if self.s.nature:
            self.step(90, "Кого можно встретить (iNaturalist)…")
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

        self.step(96, "Советы: одежда, еда, безопасность…")
        advice.build(tour)
        if tour.totals.get("walk_m", 0) > LONG_WALK_M:
            tour.warnings.append("Пеший маршрут длиннее %d км — уберите часть объектов во "
                                 "вкладке «Объекты» или уменьшите зону." % (LONG_WALK_M // 1000))
        return tour

    def drive_route(self, place):
        start = None
        if self.s.start_text.strip():
            found = self.soft("Начальная точка", geocode.geocode, self.http, self.s.start_text,
                              self.s.lang, 3)
            if found:
                start = (found[0].name, found[0].lon, found[0].lat)
            else:
                self.log("⚠ «%s» не найдено — еду из ближайшего города" % self.s.start_text)
        if start is None:
            start = self.soft("Ближайший город", osm.nearest_town, self.http, place.lon, place.lat)
        if start is None:
            return DriveRoute(note="Не удалось определить, откуда ехать.")
        route = routing.drive(self.http, (start[1], start[2]), start[0], place.point)
        if not route.ok and self.s.start_text.strip():
            town = self.soft("Ближайший город", osm.nearest_town, self.http, place.lon, place.lat)
            if town and town[0] != start[0]:
                self.log("⚠ От «%s» проезда нет (%s) — строю от %s" % (start[0], route.note, town[0]))
                note = route.note
                route = routing.drive(self.http, (town[1], town[2]), town[0], place.point)
                route.note = ("От «%s»: %s Маршрут на машине — от ближайшего города." % (
                    start[0], note)) if route.ok else route.note
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

    def build_legs(self, tour):
        finder = terrain.offtrail_finder(self.elevation)
        pairs = list(zip(range(len(tour.stops)), range(1, len(tour.stops))))
        if tour.loop and len(tour.stops) > 1:
            pairs.append((len(tour.stops) - 1, 0))
        tour.legs = []
        full_profile = []
        offset = 0.0
        for a, b in pairs:
            self.http.check()
            segments = routing.foot_segments(self.http, tour.stops[a].point, tour.stops[b].point,
                                             finder)
            leg = Leg(a=a, b=b, segments=segments)
            for segment in segments:
                prof = terrain.profile(self.elevation, segment.coords) if self.elevation else []
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
        t["hours_stops"] = (len(tour.stops) - 1) * advice.STOP_MINUTES / 60.0
        t["ascent"], t["descent"] = round(up), round(down)
        t["min_h"], t["max_h"] = low, high
        t["start_h"] = full_profile[0][1] if full_profile else None
        if self.elevation:
            for poi in tour.stops:
                if poi.elevation is None:
                    poi.elevation = self.soft("Высота", self.elevation.sample, poi.lon, poi.lat)
            top = max(tour.stops, key=lambda p: p.elevation if p.elevation is not None else -1e9)
            if top.elevation is not None and (high is None or top.elevation > high):
                t["max_h"] = top.elevation
            t["top_point"] = top.point
            tour.dem_name = getattr(self.elevation, "name", "")
            tour.add_source("Рельеф: " + tour.dem_name, getattr(self.elevation, "url", ""), "")
        tour.add_source("Маршруты: OSRM, серверы FOSSGIS", "https://routing.openstreetmap.de",
                        "данные © OpenStreetMap")

    def find_photos(self, tour):
        found = False
        for poi in tour.stops[1:]:
            self.http.check()
            radius, limit = (2500, 8) if poi.is_main else (600, 2)
            poi.photos = self.soft("Фото «%s»" % poi.name, wiki.commons_photos, self.http,
                                   poi.lon, poi.lat, radius, limit) or []
            for photo in poi.photos:
                photo.poi_uid = poi.uid
            found = found or bool(poi.photos)
        seen = set()
        tour.photos = []
        for poi in tour.stops[1:]:
            for photo in poi.photos:
                if photo.page_url not in seen:
                    seen.add(photo.page_url)
                    tour.photos.append(photo)
        if found:
            tour.add_source("Wikimedia Commons", "https://commons.wikimedia.org",
                            "лицензия каждого снимка указана под ним")
