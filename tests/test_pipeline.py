# -*- coding: utf-8 -*-
"""Весь путь на поддельных ответах сервисов и настоящем QGIS (без окна).

Запуск (нужен Python с модулем qgis):  python -m unittest tests.test_pipeline
"""

import os
import re
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

try:
    from osgeo import gdal
    from qgis.core import QgsApplication, QgsProject, QgsRasterLayer
    gdal.UseExceptions()
except ImportError:  # pragma: no cover
    QgsApplication = None

APP = None


def setUpModule():
    global APP
    if QgsApplication is None:
        raise unittest.SkipTest("нет QGIS")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()


@unittest.skipIf(QgsApplication is None, "нет QGIS")
class TerrariumTest(unittest.TestCase):

    def test_real_tile_over_gorely(self):
        from gis_tour_guide import terrain
        path = os.path.join(os.path.dirname(__file__), "fixtures", "terrarium_11_1923_671.png")
        with open(path, "rb") as handle:
            heights = terrain.decode_terrarium(handle.read())
        self.assertEqual(heights.shape, (256, 256))
        # вершина Горелого — 1829 м
        self.assertAlmostEqual(float(heights[60:90, 0:20].max()), 1829, delta=15)
        self.assertGreater(float(heights.min()), 300)

    def test_grid_and_sample_from_tiles(self):
        from fake_services import VOLCANO, FakeHttp, elevation
        from gis_tour_guide import geo, terrain
        source = terrain.TerrariumSource(FakeHttp())
        self.assertAlmostEqual(source.sample(*VOLCANO), 1829, delta=15)
        grid = source.grid(geo.bbox_around(VOLCANO[0], VOLCANO[1], 5), max_cells=120)
        self.assertLessEqual(max(grid.rows, grid.cols), 120)
        r, c = grid.cell(*VOLCANO)
        self.assertAlmostEqual(float(grid.values[r, c]), float(elevation(*VOLCANO)), delta=40)


@unittest.skipIf(QgsApplication is None, "нет QGIS")
class PipelineTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fake_services import FakeHttp
        from gis_tour_guide import terrain
        from gis_tour_guide.planner import Planner, Settings
        cls.folder = tempfile.mkdtemp()
        cls.http = FakeHttp()
        cls.messages = []
        settings = Settings(query="вулкан Горелый", half_km=10, max_points=5, date="2026-10-24",
                            people=3)
        planner = Planner(cls.http, settings, terrain.TerrariumSource(cls.http),
                          log=cls.messages.append)
        cls.tour = planner.run()

    @classmethod
    def tearDownClass(cls):
        QgsProject.instance().clear()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_place_region_and_candidates(self):
        tour = self.tour
        self.assertEqual(tour.place.name, "Горелый")
        self.assertEqual(tour.place.category, "volcano")
        self.assertEqual(tour.region_name, "Камчатский край")
        names = [p.name for p in tour.candidates]
        self.assertEqual(names[0], "Горелый")
        self.assertEqual(names.count("Горелый"), 1)                 # OSM и статья слились с главным
        self.assertNotIn("Далёкая", names)                          # вне зоны
        self.assertNotIn("Термальный", " ".join(names))             # посёлок — не достопримечательность
        self.assertEqual(sum("Верблюд" in n for n in names), 1)     # дубль убран
        self.assertIn("Кратер Активный", names)                     # только в Википедии
        main = tour.candidates[0]
        self.assertEqual(main.wikipedia, "ru:Горелый (вулкан)")

    def test_selection_and_order(self):
        tour = self.tour
        self.assertEqual(tour.stops[0].uid, "start")
        self.assertAlmostEqual(tour.stops[0].lon, 158.075)
        chosen = [p for p in tour.candidates[1:] if p.selected]
        self.assertEqual(len(chosen), 5)
        self.assertFalse(any(p.category == "camp" for p in chosen))  # стоянка — не цель экскурсии
        self.assertEqual(len(tour.stops), 7)
        self.assertEqual([p.order for p in tour.stops], list(range(7)))
        self.assertEqual(len(tour.legs), 7)                          # кольцо
        self.assertEqual(tour.legs[-1].b, 0)

    def test_cave_reached_off_trail_by_terrain(self):
        tour = self.tour
        cave = next(i for i, p in enumerate(tour.stops) if p.name == "Ледяная пещера")
        legs = [leg for leg in tour.legs if cave in (leg.a, leg.b)]
        self.assertTrue(all(leg.offtrail_m > 300 for leg in legs))
        segment = next(s for s in legs[0].segments if s.kind == "offtrail")
        self.assertIn(tour.stops[cave].point, (segment.coords[0], segment.coords[-1]))
        # по склону без тропы медленнее, чем 4 км/ч по ровной тропе
        self.assertGreater(legs[0].hours, legs[0].length_m / 4000)
        self.assertGreater(tour.totals["offtrail_m"], 1000)

    def test_totals_profile_and_heights(self):
        t = self.tour.totals
        self.assertGreater(t["walk_m"], 10000)
        self.assertGreater(t["ascent"], 300)
        self.assertAlmostEqual(t["ascent"], t["descent"], delta=60)  # кольцо: вверх ≈ вниз
        self.assertGreater(t["max_h"], 1500)
        self.assertGreater(t["hours_walk"], 3)
        self.assertGreater(len(self.tour.profile), 100)
        self.assertTrue(all(p.elevation is not None for p in self.tour.stops))

    def test_drive_and_services(self):
        drive = self.tour.drive
        self.assertTrue(drive.ok)
        self.assertEqual(drive.start_name, "Петропавловск-Камчатский")
        self.assertEqual([r[0] for r in drive.roads],
                         ["Северо-Восточное шоссе", "Р-474", "Р-474 (Мутновская дорога)",
                          "местные дороги без названия"])
        self.assertEqual(drive.trailhead_name, "Термальный")
        self.assertGreater(self.tour.services["shop"]["km"], 15)

    def test_wiki_voyage_photos_nature(self):
        tour = self.tour
        main = next(p for p in tour.stops if p.is_main)
        self.assertIn("действующий вулкан", main.extract)
        self.assertIn("По трассе на Мутновскую".lower(), tour.wikivoyage["sections"]["get_in"].lower())
        self.assertTrue(tour.photos)
        self.assertEqual(tour.photos[0].description, "Вид на кратер, август")
        self.assertEqual(tour.photos[0].author, "Иван")
        self.assertTrue(all(p.title.endswith(".jpg") for p in tour.photos))
        self.assertIn("Американский суслик", [o.name for o in tour.observations])
        self.assertEqual(len(tour.observer_notes), 1)
        self.assertEqual(tour.weather[1]["label"], "Высшая точка маршрута")

    def test_advice(self):
        tour = self.tour
        text_ = "\n".join(item for _, items in tour.advice for item in items)
        self.assertIn("Бурый медведь", text_)
        self.assertIn("Южно-Камчатский природный парк", text_)
        self.assertIn("Магазинов поблизости нет", text_)
        self.assertIn("Гамаши", text_)
        self.assertIn("каска", text_)                        # пещера
        self.assertIn("Купальник", text_)                    # горячий источник
        self.assertIn("сернистые газы", text_)
        self.assertIn("МЧС", text_)
        self.assertIn("на группу из 3", text_)
        # на вершине −9 °C → тёплая одежда
        self.assertIn("пуховка", text_)
        self.assertTrue(any("медвед" in w for w in tour.warnings))

    def test_layers_layouts_report(self):
        from gis_tour_guide import maps, report, terrain
        tour = self.tour
        folder = os.path.join(self.folder, "out")
        os.makedirs(folder)
        report.download_images(self.http, tour, os.path.join(folder, "photos"))
        self.assertTrue(any(p.local_path for p in tour.photos))
        report.write_gpx(tour, os.path.join(folder, "route.gpx"))
        with open(os.path.join(folder, "route.gpx"), encoding="utf-8") as handle:
            gpx = handle.read()
        self.assertEqual(gpx.count("<wpt"), 7)
        self.assertIn("<trkseg>", gpx)
        profile = os.path.join(folder, "profile.png")
        self.assertTrue(report.draw_profile(tour, profile))

        grid = terrain.TerrariumSource(self.http).grid(tour.bbox, max_cells=300)
        dem = maps.write_dem(grid, os.path.join(folder, "dem.tif"))
        layers = maps.build_layers(tour, folder)
        self.assertEqual(layers["stops"].featureCount(), 7)
        self.assertEqual(layers["others"].featureCount(), len(tour.candidates) - 6)
        self.assertGreater(layers["walk"].featureCount(), 7)
        self.assertEqual(layers["region"].featureCount(), 1)
        saved = maps.save_gpkg(layers, os.path.join(folder, "tour.gpkg"))
        self.assertTrue(all(layer.isValid() for layer in saved.values()))
        self.assertEqual(saved["stops"].source().split("|")[0], os.path.join(folder, "tour.gpkg"))

        group, added = maps.add_to_project(tour, saved, dem, basemap="relief")
        self.assertEqual(group.name(), "Экскурсия: Горелый")
        self.assertIn("hillshade", added)
        self.assertNotIn("basemap", added)
        # повторный запуск заменяет группу, а не плодит копии
        again = maps.save_gpkg(maps.build_layers(tour, folder), os.path.join(folder, "tour2.gpkg"))
        group, added = maps.add_to_project(tour, again, dem, basemap="relief")
        root = QgsProject.instance().layerTreeRoot()
        self.assertEqual(sum(1 for g in root.children() if g.name() == group.name()), 1)

        result = maps.make_layouts(tour, added, folder, "relief")
        for key in ("overview", "scheme"):
            self.assertTrue(os.path.getsize(result[key]) > 20000, key)
            self.assertTrue(os.path.exists(result[key].replace(".png", ".pdf")))
        manager = QgsProject.instance().layoutManager()
        self.assertIsNotNone(manager.layoutByName("Экскурсия «Горелый» — 2. Карта-схема маршрута"))

        html = report.build_html(tour, folder, result, profile)
        for needle in ("Как добраться", "Р-474", "Маршрут экскурсии", "Ледяная пещера",
                       "без троп, по рельефу", "Что взять", "Кого можно встретить",
                       "Суслики бегают у подножия", "Викигид", "overview_map.png", "profile.png",
                       "Прогноз погоды", "Источники"):
            self.assertIn(needle, html)
        for src in re.findall(r"src='([^']+)'", html):
            self.assertTrue(os.path.exists(os.path.join(folder, src)), src)

    def test_layer_elevation_matches_tiles(self):
        from fake_services import VOLCANO
        from gis_tour_guide import maps, terrain
        from gis_tour_guide.dem_layer import LayerElevation
        from gis_tour_guide.geo import bbox_around
        grid = terrain.TerrariumSource(self.http).grid(bbox_around(VOLCANO[0], VOLCANO[1], 4), 200)
        path = maps.write_dem(grid, os.path.join(self.folder, "own_dem.tif"))
        layer = QgsRasterLayer(path, "Моя ЦМР")
        self.assertTrue(layer.isValid())
        source = LayerElevation(layer)
        self.assertAlmostEqual(source.sample(*VOLCANO), 1829, delta=40)
        self.assertIsNone(source.sample(150.0, 40.0))
        own = source.grid(bbox_around(VOLCANO[0], VOLCANO[1], 2), 60)
        r, c = own.cell(*VOLCANO)
        self.assertAlmostEqual(float(own.values[r, c]), 1829, delta=60)


class FakeIface:

    def __init__(self):
        from qgis.gui import QgsMapCanvas, QgsMessageBar
        self.canvas = QgsMapCanvas()
        self.bar = QgsMessageBar()
        self.opened = []

    def mapCanvas(self):  # noqa: N802
        return self.canvas

    def messageBar(self):  # noqa: N802
        return self.bar

    def mainWindow(self):  # noqa: N802
        return None

    def openLayoutDesigner(self, layout):  # noqa: N802
        self.opened.append(layout.name())


@unittest.skipIf(QgsApplication is None, "нет QGIS")
class DialogTest(unittest.TestCase):
    """Фоновая задача целиком (синхронно) и разбор её результата окном."""

    def test_task_and_dialog(self):
        import fake_services
        from gis_tour_guide import task as task_module
        from gis_tour_guide.dialog import TourGuideDialog
        from qgis.PyQt.QtCore import Qt
        folder = tempfile.mkdtemp()
        http = fake_services.FakeHttp()
        original = task_module.Http
        task_module.Http = lambda *args, **kwargs: http
        try:
            dialog = TourGuideDialog(FakeIface())
            dialog.folder_edit.setText(folder)
            dialog.half_km.setValue(8)
            dialog.max_points.setValue(3)
            dialog.basemap.setCurrentIndex(dialog.basemap.findData("relief"))
            places = task_module.GeocodeTask("вулкан Горелый", folder)
            self.assertTrue(places.run())
            dialog.places, dialog.places_query = places.places, "вулкан Горелый"
            dialog.query.setText("вулкан Горелый")
            settings = dialog.current_settings()
            self.assertEqual(settings.place.name, "Горелый")
            work = task_module.TourTask(settings, os.path.join(folder, "run"), folder, None, True)
            self.assertTrue(work.run(), work.error)
            self.assertTrue(os.path.exists(work.dem_path))
            self.assertTrue(os.path.exists(os.path.join(work.folder, "route.gpx")))
            dialog.tour_ready(work)
            self.assertTrue(os.path.exists(os.path.join(work.folder, "report.html")))
            self.assertTrue(dialog.browser.source().toLocalFile().endswith("report.html"))
            self.assertEqual(dialog.tabs.currentIndex(), 2)
            items = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
            self.assertEqual(len(items), len(work.tour.candidates))
            checked = [i for i in items if i.checkState(0) == Qt.CheckState.Checked]
            self.assertEqual(len(checked), 3)
            dialog.open_layout("scheme_layout")
            self.assertEqual(dialog.iface.opened, ["Экскурсия «Горелый» — 2. Карта-схема маршрута"])
            # «Перестроить маршрут» берёт отмеченные в списке объекты
            for item in checked[1:]:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            chosen = dialog.current_settings(chosen=(checked[0].data(0, Qt.ItemDataRole.UserRole),),
                                             place=dialog.tour.place)
            again = task_module.TourTask(chosen, os.path.join(folder, "run2"), folder, None, False)
            self.assertTrue(again.run(), again.error)
            self.assertEqual(len(again.tour.stops), 3)          # старт, главный, один выбранный
            dialog.tour_ready(again)
            self.assertEqual(len(QgsProject.instance().layoutManager().printLayouts()), 2)
        finally:
            task_module.Http = original
            QgsProject.instance().clear()
            shutil.rmtree(folder, ignore_errors=True)


@unittest.skipIf(QgsApplication is None, "нет QGIS")
class OfflineServicesTest(unittest.TestCase):
    """Сервисы недоступны — экскурсия всё равно строится, с предупреждениями в журнале."""

    def test_degrades_gracefully(self):
        from fake_services import FakeHttp
        from gis_tour_guide.planner import Planner, Settings
        http = FakeHttp(fail={"ru.wikipedia.org", "api.inaturalist.org", "api.open-meteo.com",
                              "commons.wikimedia.org", "ru.wikivoyage.org", "routing.openstreetmap.de"})
        log = []
        tour = Planner(http, Settings(query="вулкан Горелый", half_km=8, max_points=3),
                       None, log=log.append).run()
        self.assertFalse(tour.drive.ok)
        self.assertIn("недоступен", tour.drive.note)
        self.assertEqual(len(tour.stops), 5)
        self.assertTrue(all(s.kind == "offtrail" for leg in tour.legs for s in leg.segments))
        self.assertTrue(any(line.startswith("⚠ Статьи Википедии") for line in log))
        self.assertTrue(tour.advice)
        self.assertIn("Прогноза на дату поездки нет", tour.advice[1][1][0])


if __name__ == "__main__":
    unittest.main()
