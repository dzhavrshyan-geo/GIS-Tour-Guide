# -*- coding: utf-8 -*-
"""Фоновые задачи QGIS: поиск объекта, подбор попутных объектов, создание экскурсии."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from . import geo, geocode, report, terrain
from .maps import enum
from .net import Cancelled, Http, NetError
from .planner import Planner

CAN_CANCEL = enum(QgsTask, "CanCancel", "Flag.CanCancel")

# Этапы фоновой задачи после planner.plan и этапы главного потока (слои, карты, отчёт)
TASK_STAGES = [("images", "Загрузка фотографий", 3), ("relief", "Рельеф для карт", 4)]
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
        self.gpx = ""            # трек GPX — в памяти, на диск только по кнопке «Скачать»
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

        self.stage("images", 0)
        report.collect_images(http, tour, self.photos_dir, self.log,
                              lambda part: self.stage("images", part))
        self.stage("images", 1)
        if tour.urban or elevation is None:
            self.stage("relief", -1)
        else:
            self.stage("relief", 0)
            self.dem_grid = self.make_dem(tour, elevation)
            self.stage("relief", 1)
        tour.profile_png = report.draw_profile(tour)
        self.gpx = report.gpx_text(tour)

    def make_dem(self, tour, elevation):
        coords = [c for leg in tour.legs for s in leg.segments for c in s.coords]
        box = geo.bbox_union(tour.bbox, geo.bbox_of(coords, 500)) if coords else tour.bbox
        try:
            return elevation.grid(box, max_cells=900)
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 - без отмывки рельефа тоже можно
            self.log("⚠ Рельеф для карты не получен: %s" % error)
            return None
