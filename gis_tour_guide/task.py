# -*- coding: utf-8 -*-
"""Фоновые задачи QGIS: поиск объекта, подбор попутных объектов, создание экскурсии."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from . import geo, geocode, osm, report, terrain
from .maps import enum
from .net import Cancelled, Http, NetError
from .planner import Planner

CAN_CANCEL = enum(QgsTask, "CanCancel", "Flag.CanCancel")

# Этапы фоновой задачи после planner.plan и этапы главного потока (слои, карты, отчёт)
TASK_STAGES = [("images", "Загрузка фотографий", 3), ("relief", "Рельеф для карт", 4)]
OFFLINE_STAGES = [("base", "Дороги, тропы и вода для офлайн-карты", 3),
                  ("tiles", "Офлайн-карта района (MBTiles)", 6), ("zip", "Архив для телефона", 1)]
FINISH_STAGES = [("layers", "Слои и GeoPackage", 2), ("maps", "Карты-схемы", 6),
                 ("report", "Отчёт HTML и PDF", 3)]


class BaseTask(QgsTask):
    log_message = pyqtSignal(str)
    stage_changed = pyqtSignal(str, float)

    def __init__(self, title, cache_dir):
        super().__init__(title, CAN_CANCEL)
        self.cache_dir = cache_dir
        self.error = ""

    def log(self, text):
        self.log_message.emit(text)

    def stage(self, key, fraction):
        self.stage_changed.emit(key, float(fraction))

    def http(self):
        return Http(self.cache_dir, is_cancelled=self.isCanceled)

    def run(self):
        try:
            self.work()
            return True
        except Cancelled:
            return False
        except NetError as error:
            self.error = str(error)
        except Exception:  # noqa: BLE001 - покажем в журнале
            self.error = traceback.format_exc()
        return False

    def work(self):
        raise NotImplementedError


class GeocodeTask(BaseTask):

    def __init__(self, query, cache_dir, lang="ru"):
        super().__init__("ГИС-экскурсовод: поиск «%s»" % query, cache_dir)
        self.query, self.lang = query, lang
        self.places = []

    def work(self):
        self.places = geocode.geocode(self.http(), self.query, self.lang)


class CollectTask(BaseTask):
    """«Подобрать попутные объекты»: регион, тип туризма, объекты, их популярность."""

    def __init__(self, settings, cache_dir):
        super().__init__("ГИС-экскурсовод: попутные объекты", cache_dir)
        self.settings = settings
        self.tour = None

    def work(self):
        planner = Planner(self.http(), self.settings, progress=self.stage, log=self.log)
        self.tour = planner.collect(self.settings.place)


class TourTask(BaseTask):
    """«Создать экскурсию»: всё, что не трогает проект QGIS (маршрут, сведения, фото, рельеф)."""

    def __init__(self, tour, settings, cache_dir, elevation=None, use_terrain_tiles=True,
                 photos_dir=None):
        super().__init__("ГИС-экскурсовод: %s" % tour.place.name, cache_dir)
        self.tour = tour
        self.settings = settings
        self.gpx = ""            # трек GPX и KML — в памяти, на диск только по кнопке «Скачать»
        self.kml = ""
        self.elevation = elevation
        self.use_terrain_tiles = use_terrain_tiles
        self.photos_dir = photos_dir
        self.dem_grid = None

    def work(self):
        http = self.http()
        elevation = self.elevation
        if elevation is None and self.use_terrain_tiles and not self.tour.urban:
            elevation = terrain.TerrariumSource(http)
        planner = Planner(http, self.settings, elevation, progress=self.stage, log=self.log)
        tour = planner.plan(self.tour)
        self.dem_grid = finish_tour(http, tour, elevation, self.photos_dir, self.stage, self.log)
        self.gpx = report.gpx_text(tour)
        self.kml = report.kml_text(tour)


def finish_tour(http, tour, elevation, photos_dir, stage, log):
    """После маршрута: фото в память, рельеф для карт, профиль высот. → сетка ЦМР или None."""
    stage("images", 0)
    report.collect_images(http, tour, photos_dir, log, lambda part: stage("images", part))
    stage("images", 1)
    dem_grid = None
    if tour.urban or elevation is None:
        stage("relief", -1)
    else:
        stage("relief", 0)
        coords = [c for leg in tour.legs for s in leg.segments for c in s.coords]
        box = geo.bbox_union(tour.bbox, geo.bbox_of(coords, 500)) if coords else tour.bbox
        try:
            dem_grid = elevation.grid(box, max_cells=900)
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 - без отмывки рельефа тоже можно
            log("⚠ Рельеф для карты не получен: %s" % error)
        stage("relief", 1)
    tour.profile_png = report.draw_profile(tour)
    return dem_grid


class OfflineTask(BaseTask):
    """Векторы OSM для собственной офлайн-подложки (рисуется потом в главном потоке)."""

    def __init__(self, bbox, urban, cache_dir):
        super().__init__("ГИС-экскурсовод: офлайн-карта", cache_dir)
        self.bbox, self.urban = bbox, urban
        self.features = {}

    def work(self):
        self.stage("base", 0)
        self.features = osm.offline_base(self.http(), self.bbox, self.urban)
        self.stage("base", 1)


class PublishTask(BaseTask):
    """Выложить отчёт на GitHub Pages и получить QR-код ссылки."""

    def __init__(self, html, repo, token, folder_name, cache_dir, files=None):
        super().__init__("ГИС-экскурсовод: публикация отчёта", cache_dir)
        self.html, self.repo, self.token, self.folder_name = html, repo, token, folder_name
        self.files = files or {}
        self.url, self.note, self.qr = "", "", None

    def work(self):
        from . import publish
        http = Http(None, is_cancelled=self.isCanceled, timeout=120)     # без кэша
        self.url, self.note = publish.publish(http, self.token, self.repo, self.folder_name, self.html,
                                               self.files)
        try:
            self.qr = publish.qr_png(http, self.url)
        except NetError:
            self.qr = None


TRIP_STAGES = [("places", "Поиск объектов поездки", 2), ("collect", "Попутные объекты у каждого", 4),
               ("order", "Порядок объектов и переезды", 3), ("plan", "Экскурсии по объектам", 10),
               ("media", "Фото и рельеф", 4), ("outputs", "Карты и отчёты по объектам", 8),
               ("trip", "Итоговый отчёт поездки", 2)]


class TripTask(BaseTask):
    """«Поездка по региону»: экскурсии у нескольких объектов, переезды между ними, план по дням."""

    def __init__(self, names, settings, cache_dir, elevation=None, use_terrain_tiles=True):
        super().__init__("ГИС-экскурсовод: поездка", cache_dir)
        self.names = [n.strip() for n in names if n.strip()]
        self.settings = settings
        self.elevation = elevation
        self.use_terrain_tiles = use_terrain_tiles
        self.trip = None
        self.dem_grids = []
        self.gpx = self.kml = ""

    def work(self):
        import copy
        from . import osm, routing, trip as trip_module
        from .model import Trip
        http = self.http()
        s = self.settings
        places, missing = [], []
        for number, name in enumerate(self.names):
            self.stage("places", number / float(len(self.names)))
            found = geocode.geocode(http, name, s.lang)
            if found:
                places.append(found[0])
                self.log("%s → %s (%s)" % (name, found[0].name, found[0].region or found[0].display_name))
            else:
                missing.append(name)
                self.log("⚠ «%s» не найдено — пропускаю" % name)
        if not places:
            raise NetError("Ни один объект поездки не найден.")
        self.stage("places", 1)

        tours = []
        for number, place in enumerate(places):
            self.stage("collect", number / float(len(places)))
            base = copy.copy(s)
            base.place, base.chosen = place, None
            tours.append(Planner(http, base, log=self.log).collect(place))
        self.stage("collect", 1)

        self.stage("order", 0)
        if s.start_text.strip():
            found = geocode.geocode(http, s.start_text, s.lang, 3)
            start = (found[0].name, found[0].lon, found[0].lat) if found else None
            reason = "указано вами"
        else:
            start = None
        if not s.start_text.strip() or start is None:
            town, reason = osm.pick_start(tours[0].capital, tours[0].settlements, tours[0].place.point)
            start = (town.name, town.lon, town.lat) if town else (places[0].name, places[0].lon, places[0].lat)
        ordered = trip_module.order_bases((start[1], start[2]), tours, s.loop)
        trip = Trip(title="Поездка: " + " — ".join(t.place.name for t in ordered), start_name=start[0],
                    start=(start[1], start[2]), start_reason=reason, date=s.date, loop=s.loop, missing=missing)
        self.stage("order", 1)

        elevation = self.elevation
        if elevation is None and self.use_terrain_tiles:
            elevation = terrain.TerrariumSource(http)
        previous = start
        for number, tour in enumerate(ordered):
            self.stage("plan", number / float(len(ordered)))
            base = copy.copy(s)
            base.place, base.chosen, base.start_text = tour.place, None, ""
            base.start_point, base.lodging_always = previous, True
            Planner(http, base, None if tour.urban else elevation, log=self.log).plan(tour)
            trip.drives.append(tour.drive)
            head = tour.stops[0].point if tour.stops else tour.place.point
            previous = (tour.place.name, head[0], head[1])
            trip.tours.append(tour)
        if s.loop:
            trip.drives.append(routing.drive(http, (previous[1], previous[2]), previous[0], trip.start))
        self.stage("plan", 1)

        self.stage("media", 0)
        for number, tour in enumerate(trip.tours):
            self.dem_grids.append(finish_tour(http, tour, None if tour.urban else elevation, None,
                                              lambda key, part: None, self.log))
            self.stage("media", (number + 1) / float(len(trip.tours)))
        trip_module.plan_days(trip)
        self.gpx = trip_module.gpx_text(trip, [report.gpx_text(t) for t in trip.tours])
        self.kml = trip_module.kml_text(trip, [report.kml_text(t) for t in trip.tours])
        self.trip = trip
