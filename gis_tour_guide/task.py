# -*- coding: utf-8 -*-
"""Фоновые задачи QGIS: поиск объекта и сборка экскурсии (сеть не тормозит окно)."""

import os
import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from . import geo, geocode, report, terrain
from .net import Cancelled, Http, NetError
from .maps import enum
from .planner import Planner

CAN_CANCEL = enum(QgsTask, "CanCancel", "Flag.CanCancel")


class GeocodeTask(QgsTask):
    log_message = pyqtSignal(str)

    def __init__(self, query, cache_dir, lang="ru"):
        super().__init__("ГИС-экскурсовод: поиск «%s»" % query, CAN_CANCEL)
        self.query, self.cache_dir, self.lang = query, cache_dir, lang
        self.places = []
        self.error = ""

    def run(self):
        try:
            http = Http(self.cache_dir, is_cancelled=self.isCanceled)
            self.places = geocode.geocode(http, self.query, self.lang)
            return True
        except Cancelled:
            return False
        except NetError as error:
            self.error = str(error)
        except Exception:  # noqa: BLE001 - покажем в журнале
            self.error = traceback.format_exc()
        return False


class TourTask(QgsTask):
    """Всё, что не трогает проект QGIS: данные, маршрут, фото, ЦМР, профиль, GPX."""
    log_message = pyqtSignal(str)

    def __init__(self, settings, folder, cache_dir, elevation=None, use_terrain_tiles=True,
                 relief=True):
        super().__init__("ГИС-экскурсовод: %s" % (settings.query or settings.place.name),
                         CAN_CANCEL)
        self.settings = settings
        self.folder = folder
        self.cache_dir = cache_dir
        self.elevation = elevation
        self.use_terrain_tiles = use_terrain_tiles
        self.relief = relief
        self.tour = None
        self.dem_path = None
        self.profile_png = None
        self.error = ""

    def log(self, text):
        self.log_message.emit(text)

    def run(self):
        try:
            os.makedirs(self.folder, exist_ok=True)
            http = Http(self.cache_dir, is_cancelled=self.isCanceled)
            elevation = self.elevation
            if elevation is None and self.use_terrain_tiles:
                elevation = terrain.TerrariumSource(http)
            planner = Planner(http, self.settings, elevation,
                              progress=lambda pct, _text: self.setProgress(pct * 0.9), log=self.log)
            tour = planner.run()
            self.log("Скачиваю фотографии…")
            report.download_images(http, tour, os.path.join(self.folder, "photos"), self.log)
            report.write_gpx(tour, os.path.join(self.folder, "route.gpx"))
            if elevation is not None and self.relief:
                self.log("Рельеф для карты-схемы…")
                self.dem_path = self.make_dem(tour, elevation)
            if tour.profile:
                path = os.path.join(self.folder, "profile.png")
                if report.draw_profile(tour, path):
                    self.profile_png = path
            self.tour = tour
            self.setProgress(95)
            return True
        except Cancelled:
            return False
        except NetError as error:
            self.error = str(error)
        except Exception:  # noqa: BLE001 - покажем в журнале
            self.error = traceback.format_exc()
        return False

    def make_dem(self, tour, elevation):
        from .maps import write_dem
        coords = [c for leg in tour.legs for s in leg.segments for c in s.coords]
        box = geo.bbox_union(tour.bbox, geo.bbox_of(coords, 500)) if coords else tour.bbox
        try:
            grid = elevation.grid(box, max_cells=900)
            return write_dem(grid, os.path.join(self.folder, "dem.tif"))
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 - без отмывки рельефа тоже можно
            self.log("⚠ Рельеф для карты не получен: %s" % error)
            return None
