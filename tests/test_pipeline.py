# -*- coding: utf-8 -*-
"""Весь путь на поддельных ответах сервисов и настоящем QGIS (без окна):
природный сценарий (вулкан Горелый) и городской (Московский Кремль).

Запуск (нужен Python с модулем qgis):  python -m unittest tests.test_pipeline
"""

import os
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


def run_tour(scenario, half_km, max_points=5, elevation=True):
    from fake_services import FakeHttp
    from gis_tour_guide import terrain
    from gis_tour_guide.planner import Planner, Settings
    http = FakeHttp(scenario=scenario)
    stages, log = [], []
    query = "вулкан Горелый" if scenario == "nature" else "Московский Кремль"
    settings = Settings(query=query, half_km=half_km, max_points=max_points, date="2026-10-24",
                        people=3)
    planner = Planner(http, settings, terrain.TerrariumSource(http) if elevation else None,
                      progress=lambda key, fraction: stages.append((key, fraction)), log=log.append)
    return planner.run(), http, stages, log


def build_outputs(tour, http, folder, basemap):
    """То же, что делает окно после фоновой задачи: слои, GeoPackage, макеты, отчёт."""
    from gis_tour_guide import maps, report, terrain
    report.collect_images(http, tour)
    tour.profile_png = report.draw_profile(tour)
    grid = None if tour.urban else terrain.TerrariumSource(http).grid(tour.bbox, 300)
    layers = maps.build_layers(tour)
    saved = maps.save_gpkg(layers, os.path.join(folder, "tour.gpkg"), grid)
    group, added = maps.add_to_project(tour, saved, basemap)
    layouts = maps.make_layouts(tour, added, basemap)
    pictures = {key: maps.render(layout, dpi=60) for key, layout in layouts.items()}
    images = report.gather(tour, pictures)
    html = report.build_html(tour, images)
    pdf = report.write_pdf(tour, images, os.path.join(folder, "report.pdf"))
    return saved, group, added, layouts, pictures, html, pdf


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
class NatureTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.tour, cls.http, cls.stages, cls.log = run_tour("nature", 10)

    @classmethod
    def tearDownClass(cls):
        QgsProject.instance().clear()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_place_mode_region_settlements(self):
        tour = self.tour
        self.assertEqual(tour.place.name, "Горелый")
        self.assertEqual(tour.mode, "nature")
        self.assertEqual(tour.region_name, "Камчатский край")
        self.assertEqual(tour.capital.name, "Петропавловск-Камчатский")
        self.assertEqual(tour.capital.population, 164900)
        roles = {s.name: s.role for s in tour.settlements}
        self.assertEqual(roles["Петропавловск-Камчатский"], "capital")
        self.assertEqual(roles["Елизово"], "major")
        self.assertEqual(roles["Термальный"], "near")

    def test_candidates_ranked_by_popularity(self):
        names = [p.name for p in self.tour.candidates]
        self.assertEqual(names[0], "Горелый")
        self.assertEqual(names.count("Горелый"), 1)
        self.assertNotIn("Далёкая", names)
        self.assertNotIn("Термальный", " ".join(names))
        self.assertEqual(sum("Верблюд" in n for n in names), 1)
        # водопад читают больше всех (300 в день) — первый среди попутных
        self.assertEqual(names[1], "Вилючинский водопад")
        self.assertAlmostEqual(self.tour.candidates[1].popularity, 300)
        self.assertAlmostEqual(self.tour.candidates[0].popularity, 120)

    def test_selection_and_order(self):
        tour = self.tour
        self.assertEqual(tour.stops[0].uid, "start")
        chosen = [p for p in tour.candidates[1:] if p.selected]
        self.assertEqual(len(chosen), 5)
        self.assertFalse(any(p.category == "camp" for p in chosen))
        self.assertEqual([p.order for p in tour.stops], list(range(7)))
        self.assertEqual(len(tour.legs), 7)
        self.assertEqual(tour.legs[-1].b, 0)

    def test_drive_from_regional_centre(self):
        drive = self.tour.drive
        self.assertTrue(drive.ok)
        self.assertEqual(drive.start_name, "Петропавловск-Камчатский")
        self.assertEqual(self.tour.start_reason, "региональный центр")
        self.assertEqual(drive.trailhead_name, "Термальный")
        self.assertGreater(self.tour.services["shop"]["km"], 15)

    def test_cave_reached_off_trail_and_totals(self):
        tour = self.tour
        cave = next(i for i, p in enumerate(tour.stops) if p.name == "Ледяная пещера")
        legs = [leg for leg in tour.legs if cave in (leg.a, leg.b)]
        self.assertTrue(all(leg.offtrail_m > 300 for leg in legs))
        t = tour.totals
        self.assertGreater(t["ascent"], 300)
        self.assertAlmostEqual(t["ascent"], t["descent"], delta=60)
        self.assertGreater(t["max_h"], 1500)
        self.assertTrue(all(p.elevation is not None for p in tour.stops))

    def test_photos_numbered_in_route_order(self):
        tour = self.tour
        self.assertEqual([p.number for p in tour.photos], list(range(1, len(tour.photos) + 1)))
        orders = [next(s.order for s in tour.stops if s.uid == p.poi_uid) for p in tour.photos]
        self.assertEqual(orders, sorted(orders))
        self.assertTrue(all(p.poi_name for p in tour.photos))

    def test_stages_reported(self):
        from gis_tour_guide.planner import COLLECT_STAGES, PLAN_STAGES
        keys = [k for k, _ in self.stages]
        for key, _name, _weight in COLLECT_STAGES + PLAN_STAGES:
            self.assertIn(key, keys)
            self.assertIn((key, 1), self.stages, key)

    def test_advice(self):
        text_ = "\n".join(item for _, items in self.tour.advice for item in items)
        for needle in ("Бурый медведь", "Южно-Камчатский природный парк", "Магазинов поблизости нет",
                       "Гамаши", "каска", "Купальник", "сернистые газы", "МЧС", "на группу из 3"):
            self.assertIn(needle, text_)

    def test_outputs(self):
        saved, group, added, layouts, pictures, html, pdf = build_outputs(
            self.tour, self.http, self.folder, "relief")
        # GeoPackage: векторы и растр рельефа в одном файле
        gpkg = os.path.join(self.folder, "tour.gpkg")
        self.assertTrue(all(layer.source().startswith(gpkg) for key, layer in saved.items()
                            if key != "dem"), [l.source() for l in saved.values()])
        self.assertEqual(saved["dem"].source(), "GPKG:%s:dem" % gpkg)
        self.assertTrue(saved["dem"].isValid())
        self.assertFalse(os.path.exists(os.path.join(self.folder, "photos")))
        # две подгруппы
        self.assertEqual([c.name() for c in group.children()], ["Векторные данные", "Растры и подложки"])
        rasters = [n.name() for n in group.children()[1].children()]
        self.assertIn("Отмывка рельефа", rasters)
        self.assertIn("Подложка: CARTO Light", rasters)     # для обзорной карты
        self.assertNotIn("Рельеф (ЦМР)", [n.name() for n in group.children()[0].children()])
        # три карты, номера фото — на третьей
        self.assertEqual(sorted(layouts), ["overview", "photos", "scheme"])
        self.assertEqual(added["photos"].featureCount(), len(self.tour.photos))
        numbers = sorted(f["number"] for f in added["photos"].getFeatures())
        self.assertEqual(numbers, [p.number for p in self.tour.photos])
        self.assertTrue(all(len(data) > 5000 for data in pictures.values()))
        self.assertEqual(added["settlements"].featureCount(), len(self.tour.settlements))
        self.assertEqual(sorted(c.value() for c in added["walk"].renderer().categories()),
                         ["offtrail", "trail"])
        # отчёт — один HTML со всеми картинками внутри
        self.assertNotIn("src='photos/", html)
        self.assertGreaterEqual(html.count("data:image/jpeg;base64,"), 3 + len(self.tour.photos))
        for needle in ("Природный туризм", "Как добраться", "Р-474", "Маршрут экскурсии",
                       "Профиль высот", "Объекты на маршруте", "Что взять", "Кого можно встретить",
                       "Суслики бегают у подножия", "Фотографии", "Источники", "региональный центр"):
            self.assertIn(needle, html)
        self.assertIn("Фото: №", html)
        self.assertGreater(os.path.getsize(pdf), 50000)
        with open(pdf, "rb") as handle:
            self.assertEqual(handle.read(5), b"%PDF-")

    def test_layer_elevation_matches_tiles(self):
        from fake_services import VOLCANO
        from gis_tour_guide import maps, terrain
        from gis_tour_guide.dem_layer import LayerElevation
        from gis_tour_guide.geo import bbox_around
        grid = terrain.TerrariumSource(self.http).grid(bbox_around(VOLCANO[0], VOLCANO[1], 4), 200)
        path = os.path.join(self.folder, "own_dem.gpkg")
        maps.write_dem(grid, path)
        layer = QgsRasterLayer("GPKG:%s:dem" % path, "Моя ЦМР", "gdal")
        self.assertTrue(layer.isValid())
        source = LayerElevation(layer)
        self.assertAlmostEqual(source.sample(*VOLCANO), 1829, delta=40)
        self.assertIsNone(source.sample(150.0, 40.0))
        own = source.grid(bbox_around(VOLCANO[0], VOLCANO[1], 2), 60)
        r, c = own.cell(*VOLCANO)
        self.assertAlmostEqual(float(own.values[r, c]), 1829, delta=60)


@unittest.skipIf(QgsApplication is None, "нет QGIS")
class UrbanTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.tour, cls.http, cls.stages, cls.log = run_tour("urban", 2)

    @classmethod
    def tearDownClass(cls):
        QgsProject.instance().clear()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_detected_as_city(self):
        self.assertEqual(self.tour.mode, "urban")
        self.assertEqual(self.tour.building_count, 2400)

    def test_top_by_popularity(self):
        chosen = [p.name for p in self.tour.candidates[1:] if p.selected]
        self.assertEqual(chosen, ["Красная площадь", "Собор Василия Блаженного", "Большой театр",
                                  "Царь-пушка", "Государственный исторический музей"])
        names = [p.name for p in self.tour.candidates]
        self.assertNotIn("Доска", names)                       # мемориальные доски не берём
        self.assertNotIn("Церковь без статьи", names)          # в городе — только со статьёй
        self.assertNotIn("Тверская улица", names)
        self.assertNotIn("Охотный Ряд", " ".join(names))
        self.assertIn("Большой Кремлёвский дворец", names)
        self.assertEqual(names.count("Красная площадь"), 1)

    def test_start_at_metro_without_car(self):
        tour = self.tour
        self.assertFalse(tour.drive.ok)
        self.assertEqual(tour.drive.start_name, "Москва")
        self.assertEqual(tour.stops[0].name, "Старт: метро «Площадь Революции»")
        self.assertEqual(tour.transport[0][0], "Метро")
        self.assertFalse(tour.profile)                          # без рельефа
        self.assertFalse(tour.observations)                     # без животных
        self.assertIn(("nature", -1), self.stages)
        self.assertIn(("protected", -1), self.stages)
        self.assertGreater(tour.totals["hours_stops"], 2)       # музей и театр — дольше

    def test_urban_advice(self):
        text_ = "\n".join(item for _, items in self.tour.advice for item in items)
        for needle in ("Удобная обувь", "Mo-Su 11:00-17:00", "вход платный", "https://shm.ru",
                       "Кофейня у ГУМа", "платок", "метро «Площадь Революции»", "купить онлайн"):
            self.assertIn(needle, text_)
        self.assertNotIn("медвед", text_)
        self.assertEqual([title for title, _ in self.tour.advice],
                         ["План дня", "Что взять", "Билеты и время работы", "Где поесть", "Полезно знать"])

    def test_outputs(self):
        _saved, group, added, layouts, _pictures, html, pdf = build_outputs(
            self.tour, self.http, self.folder, "light")
        self.assertNotIn("hillshade", added)
        self.assertEqual([c.value() for c in added["walk"].renderer().categories()], ["trail"])
        self.assertIn("photos", layouts)
        for needle in ("Городской туризм", "Общественный транспорт", "Маршрут прогулки",
                       "Достопримечательности на маршруте", "Часы работы", "Советы и подготовка",
                       "Где поесть", "Из Викигида «Москва»"):
            self.assertIn(needle, html)
        for absent in ("Кого можно встретить", "Профиль высот", "Набор / сброс"):
            self.assertNotIn(absent, html)
        self.assertTrue(os.path.exists(pdf))


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
    """Окно: поиск → подбор попутных объектов (галочки) → экскурсия → результат, GPX, окно загрузки."""

    def test_workflow(self):
        import fake_services
        from gis_tour_guide import task as task_module
        from gis_tour_guide.dialog import TourGuideDialog
        from gis_tour_guide.planner import COLLECT_STAGES
        from qgis.PyQt.QtWidgets import QFileDialog
        folder = tempfile.mkdtemp()
        http = fake_services.FakeHttp()
        original = task_module.Http
        task_module.Http = lambda *args, **kwargs: http
        dialog = TourGuideDialog(FakeIface())
        try:
            dialog.folder_edit.setText(folder)
            dialog.half_km.setValue(8)
            dialog.max_points.setValue(3)
            dialog.basemap.setCurrentIndex(dialog.basemap.findData("relief"))
            dialog.query.setText("вулкан Горелый")
            geocoding = task_module.GeocodeTask("вулкан Горелый", folder)
            self.assertTrue(geocoding.run())
            dialog.places, dialog.places_query = geocoding.places, "вулкан Горелый"
            dialog.found.addItem("Горелый")
            dialog.place_changed()
            self.assertIn("природный туризм", dialog.place_info.text())

            collecting = task_module.CollectTask(dialog.current_settings(dialog.current_place()), folder)
            stages = []
            collecting.stage_changed.connect(lambda key, fraction: stages.append(key))
            self.assertTrue(collecting.run(), collecting.error)
            self.assertEqual([key for key, _, _ in COLLECT_STAGES], list(dict.fromkeys(stages)))
            dialog.collected = collecting.tour
            dialog.fill_checklist(collecting.tour)
            self.assertEqual(len(dialog.checklist.checked_data()), 3)
            self.assertIn("Отмечено 3 из", dialog.checklist.lineEdit().text())
            dialog.max_points.setValue(1)                     # топ-1 без нового поиска
            self.assertEqual(dialog.checklist.checked_data(), [collecting.tour.candidates[1].uid])
            # изменили радиус — подобранные объекты сбрасываются
            dialog.half_km.setValue(9)
            self.assertIsNone(dialog.collected)
            dialog.half_km.setValue(8)
            dialog.collected = collecting.tour
            dialog.fill_checklist(collecting.tour)
            dialog.checklist.set_checked([collecting.tour.candidates[2].uid])

            settings = dialog.current_settings(collecting.tour.place, tuple(dialog.checklist.checked_data()))
            gpx = os.path.join(tempfile.mkdtemp(), "gorely.gpx")
            dialog.temp_dir = os.path.dirname(gpx)
            out = os.path.join(folder, "run")
            os.makedirs(out)
            work = task_module.TourTask(collecting.tour, settings, folder, gpx, None, True)
            self.assertTrue(work.run(), work.error)
            self.assertEqual(len(work.tour.stops), 3)          # старт, главный, один отмеченный
            self.assertIsNotNone(work.dem_grid)
            self.assertTrue(os.path.exists(gpx))
            self.assertFalse(os.path.exists(os.path.join(out, "route.gpx")))
            dialog.progress.start("тест", COLLECT_STAGES)
            dialog.tour_ready(work, out)
            self.assertEqual(sorted(os.listdir(out)), ["report.html", "report.pdf", "tour.gpkg"])
            self.assertTrue(dialog.result.isVisibleTo(dialog))
            self.assertIn("report.html", dialog.result_link.text())
            self.assertEqual(len(dialog.layouts_menu.actions()), 3)
            dialog.open_layout("scheme")
            self.assertEqual(dialog.iface.opened, ["Экскурсия «Горелый» — 2. Карта-схема маршрута"])

            target = os.path.join(folder, "saved.gpx")
            original_dialog = QFileDialog.getSaveFileName
            QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (target, "GPX (*.gpx)"))
            try:
                dialog.save_gpx()
            finally:
                QFileDialog.getSaveFileName = original_dialog
            self.assertTrue(os.path.exists(target))
            dialog.close()                                    # закрыли окно — временный GPX удалён
            self.assertFalse(os.path.exists(gpx))
            self.assertFalse(dialog.gpx_button.isEnabled())
        finally:
            task_module.Http = original
            QgsProject.instance().clear()
            shutil.rmtree(folder, ignore_errors=True)

    def test_progress_dialog(self):
        from gis_tour_guide.progress import ProgressDialog
        progress = ProgressDialog()
        progress.start("Тест", [("a", "Первый", 1), ("b", "Второй", 3), ("c", "Третий", 1)])
        progress.update_stage("a", 1)
        progress.update_stage("b", 0.5)
        self.assertAlmostEqual(progress.overall(), (1 + 1.5) / 5)
        self.assertEqual(progress.rows["b"]["percent"].text(), "50%")
        self.assertIn("Второй", progress.current.text())
        progress.update_stage("c", -1)
        self.assertEqual(progress.rows["c"]["percent"].text(), "пропущено")
        progress.finish()
        self.assertAlmostEqual(progress.overall(), 1.0)
        self.assertTrue(progress.clock.text().startswith("Готово за"))
        self.assertEqual(progress.cancel_button.text(), "Закрыть")


if __name__ == "__main__":
    unittest.main()
