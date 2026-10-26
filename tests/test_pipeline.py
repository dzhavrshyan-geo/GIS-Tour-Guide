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
        # кратер вулкана в 500 м — не отдельная точка, а часть остановки «Горелый»
        main = self.tour.candidates[0]
        self.assertEqual([m.name for m in main.members], ["Кратер Активный"])
        self.assertNotIn("Кратер Активный", names)

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

    def test_offtrail_path_avoids_lake(self):
        import numpy as np
        from fake_services import LAKE
        from gis_tour_guide import geo, terrain
        segments = [s for leg in self.tour.legs for s in leg.segments if s.kind == "offtrail"]
        self.assertTrue(segments)
        dense = [c for s in segments for c in geo.densify(s.coords, 20)]
        inside = terrain.inside(np.array([p[0] for p in dense]), np.array([p[1] for p in dense]), LAKE)
        self.assertFalse(inside.any())

    def test_marked_trail_used(self):
        marked = [s for leg in self.tour.legs for s in leg.segments if s.kind == "marked"]
        self.assertEqual({s.name for s in marked}, {"Тропа к водопаду"})
        self.assertEqual(len(self.tour.marked_trails), 2)

    def test_kml_export(self):
        import xml.etree.ElementTree as ET
        from gis_tour_guide import report
        root = ET.fromstring(report.kml_text(self.tour))
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        names = [n.text for n in root.iter("{http://www.opengis.net/kml/2.2}name")]
        self.assertIn("6. Вилючинский водопад", names)
        main = next(p for p in self.tour.stops if p.is_main)
        self.assertIn("%d. Кратер Активный" % main.order, names)   # соседи остановки — тоже точками
        lines = root.findall(".//k:LineString", ns)
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("Тропа к водопаду" in (n or "") for n in names))

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

    def test_collect_is_few_requests(self):
        from fake_services import FakeHttp
        from gis_tour_guide.planner import Planner, Settings
        http = FakeHttp()
        place = self.tour.place
        Planner(http, Settings(half_km=10)).collect(place)
        overpass = [c for c in http.calls if "interpreter" in c]
        self.assertEqual(len(overpass), 2)          # общий запрос + населённые пункты региона

    def test_overnight_days_and_lodging(self):
        tour = self.tour
        self.assertTrue(tour.overnight)
        self.assertGreater(tour.day_hours, tour.daylight)
        self.assertGreaterEqual(len(tour.days), 2)
        visited = [n for day in tour.days for n in day["stops"]]
        self.assertEqual(sorted(set(visited)), list(range(1, len(tour.stops))))
        self.assertEqual([l["segment"] for l in tour.lodging], ["premium", "middle", "budget"])
        self.assertEqual([l["name"] for l in tour.lodging],
                         ["Паратунка Резорт", "Гостиница Термальная", "Стоянка у Горелого"])
        text_ = "\n".join(item for _, items in tour.advice for item in items)
        self.assertIn("не успеть посмотреть всё за один день", text_)
        self.assertIn("Паратунка Резорт", text_)

    def test_estimate_before_route(self):
        from gis_tour_guide import schedule
        tour = self.tour
        chosen = [p.uid for p in tour.candidates[1:] if p.selected]
        guess = schedule.estimate(tour, chosen)
        self.assertTrue(guess["overnight"])
        self.assertGreater(guess["drive_h"], 1)
        self.assertGreater(guess["walk_km"], 5)
        few = schedule.estimate(tour, [])
        self.assertLess(few["walk_km"], guess["walk_km"])

    def test_season_and_access(self):
        access = "\n".join(self.tour.access)
        self.assertIn("Перевал «Вилючинский перевал» (820 м)", access)
        self.assertNotIn("Далёкий", access)                    # не на нашей дороге
        self.assertIn("«Зимник на Горелый» — зимник", access)
        self.assertEqual(self.tour.closed_today,
                         ["ООПТ «Южно-Камчатский природный парк» в эту дату закрыта для посещения (Jun-Sep)."])
        self.assertIn(self.tour.closed_today[0], self.tour.warnings)
        self.assertEqual(self.tour.advice[1][0], "Сезон и доступность")

    def test_route_variants(self):
        from gis_tour_guide import schedule
        half, day, two = schedule.variants(self.tour)
        self.assertEqual([v["key"] for v in (half, day, two)], ["half", "day", "two"])
        for variant in (half, day, two):
            guess = variant["estimate"]
            self.assertLessEqual(guess["walk_h"] + guess["visit_h"], variant["budget"] + 1e-6)
        self.assertLessEqual(len(half["uids"]), len(day["uids"]))
        self.assertLess(len(day["uids"]), len(two["uids"]))
        self.assertTrue(set(half["uids"]) <= set(two["uids"]) or len(half["uids"]) <= 1)

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
                         ["marked", "offtrail", "trail"])
        self.assertNotIn("zone", added)                       # зону поиска на карты не выносим
        self.assertEqual(added["lodging"].featureCount(), 3)
        self.assertEqual(added["photos"].renderer().type(), "pointDisplacement")
        self.assertEqual(added["stops"].renderer().type(), "pointDisplacement")
        # отчёт — один HTML со всеми картинками внутри
        self.assertNotIn("src='photos/", html)
        self.assertGreaterEqual(html.count("data:image/jpeg;base64,"), 3 + len(self.tour.photos))
        for needle in ("Природный туризм", "Как добраться", "Р-474", "Маршрут экскурсии",
                       "Профиль высот", "Объекты на маршруте", "Что взять", "Кого можно встретить",
                       "Суслики бегают у подножия", "Фотографии", "Источники", "региональный центр"):
            self.assertIn(needle, html)
        self.assertIn("Фото: №", html)
        # интерактивные карты: Leaflet внутри файла, три карты, лист для печати рядом
        self.assertIn("Leaflet", html)
        self.assertEqual(html.count("gtMap('wm-"), 3)
        self.assertIn("Лист для печати", html)
        self.assertIn("id='photo-1'", html)
        self.assertIn("Ночлег и план по дням", html)
        self.assertIn("Рядом, в той же остановке: <b>Кратер Активный</b>", html)
        self.assertIn("по маркированной тропе «Тропа к водопаду»", html)
        self.assertIn("trails", added)
        self.assertIn("marked", [c.value() for c in added["walk"].renderer().categories()])
        self.assertIn("Паратунка Резорт", html)
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
                                  "Государственный исторический музей", "Большой Кремлёвский дворец"])
        # Царь-пушка — в 110 м, внутри Кремля: одна остановка с главным объектом
        self.assertEqual([m.name for m in self.tour.candidates[0].members], ["Царь-пушка"])
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
        self.assertIn(("lodging", -1), self.stages)            # за день успеваем — ночлег не ищем
        self.assertFalse(self.tour.overnight)
        self.assertGreater(tour.totals["hours_stops"], 2)       # музей и театр — дольше

    def test_urban_advice(self):
        text_ = "\n".join(item for _, items in self.tour.advice for item in items)
        for needle in ("Удобная обувь", "Mo-Su 11:00-17:00", "вход платный", "https://shm.ru",
                       "Кофейня у ГУМа", "платок", "метро «Площадь Революции»", "купить онлайн"):
            self.assertIn(needle, text_)
        self.assertNotIn("медвед", text_)
        self.assertEqual([title for title, _ in self.tour.advice],
                         ["План дня", "Сезон и доступность", "Что взять", "Билеты и время работы",
                          "Где поесть", "Полезно знать"])
        # 24.10.2026 — суббота: музей открыт
        self.assertIn("Государственный исторический музей — в день поездки (сб) открыто, 10:00–18:00.",
                      self.tour.access)

    def test_closed_on_trip_date(self):
        from fake_services import FakeHttp
        from gis_tour_guide.planner import Planner, Settings
        tour = Planner(FakeHttp(scenario="urban"), Settings(query="Кремль", half_km=2, max_points=5,
                                                            date="2026-10-27")).run()   # вторник
        closed = [w for w in tour.warnings if "ЗАКРЫТО" in w]
        self.assertEqual(closed, ["Государственный исторический музей — в день поездки (вт, 27 октября) "
                                  "ЗАКРЫТО по данным OpenStreetMap."])

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
        # без маршрутизатора — по рельефу или по маркированным тропам из OSM
        self.assertTrue({s.kind for leg in tour.legs for s in leg.segments} <= {"offtrail", "marked"})
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
            self.assertTrue(dialog.estimate_label.isVisibleTo(dialog))
            self.assertIn("Ориентировочно", dialog.estimate_label.text())
            # варианты: полдня / день / два дня — щелчок ставит галочки
            self.assertTrue(all(b.isEnabled() for b in dialog.variant_buttons.values()))
            self.assertIn("Полный день — ", dialog.variant_buttons["day"].text())
            dialog.apply_variant("half")
            self.assertEqual(dialog.checklist.checked_data(), dialog.variant_data["half"]["uids"])
            self.assertTrue(dialog.variant_buttons["half"].isChecked())
            dialog.recheck_top()
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
            out = os.path.join(folder, "run")
            os.makedirs(out)
            work = task_module.TourTask(collecting.tour, settings, folder, None, True)
            self.assertTrue(work.run(), work.error)
            self.assertEqual(len(work.tour.stops), 3)          # старт, главный, один отмеченный
            self.assertIsNotNone(work.dem_grid)
            self.assertTrue(work.gpx.startswith("<?xml"))      # трек — в памяти, не на диске
            dialog.progress.start("тест", COLLECT_STAGES)
            dialog.tour_ready(work, out)
            self.assertTrue(dialog.gpx_button.isEnabled())
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
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), work.gpx)
            kml = os.path.join(folder, "saved")
            QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (kml, "KML — Organic Maps (*.kml)"))
            try:
                dialog.save_gpx()
            finally:
                QFileDialog.getSaveFileName = original_dialog
            with open(kml + ".kml", encoding="utf-8") as handle:
                self.assertIn("<kml", handle.read())
            dialog.close()                                    # закрыли окно — трек забыт
            self.assertEqual(dialog.gpx, "")
            self.assertFalse(dialog.gpx_button.isEnabled())
        finally:
            task_module.Http = original
            QgsProject.instance().clear()
            shutil.rmtree(folder, ignore_errors=True)

    def test_trip(self):
        """Поездка на несколько дней: два объекта, переезды, ночлег, общий отчёт и трек."""
        import fake_services
        from gis_tour_guide import task as task_module
        from gis_tour_guide.dialog import TourGuideDialog
        from gis_tour_guide.task import TRIP_STAGES
        folder = tempfile.mkdtemp()
        http = fake_services.FakeHttp()
        original = task_module.Http
        task_module.Http = lambda *args, **kwargs: http
        dialog = TourGuideDialog(FakeIface())
        try:
            dialog.folder_edit.setText(folder)
            dialog.half_km.setValue(8)
            dialog.max_points.setValue(2)
            dialog.query.setText("вулкан Горелый")
            dialog.trip_mode.setChecked(True)
            self.assertFalse(dialog.run_button.isEnabled())          # нужен ещё хотя бы один объект
            dialog.trip_names.setPlainText("вулкан Вилючинский\nвулкан горелый\n")
            self.assertEqual(dialog.trip_list(), ["вулкан Горелый", "вулкан Вилючинский"])
            self.assertTrue(dialog.run_button.isEnabled())
            self.assertEqual(dialog.run_button.text(), "Создать поездку")
            self.assertFalse(dialog.collect_button.isEnabled())

            work = task_module.TripTask(dialog.trip_list(), dialog.current_settings(None), folder, None, True)
            stages = []
            work.stage_changed.connect(lambda key, fraction: stages.append(key))
            self.assertTrue(work.run(), work.error)
            self.assertEqual(list(dict.fromkeys(stages)), [k for k, _, _ in TRIP_STAGES][:5])
            trip = work.trip
            self.assertEqual(len(trip.tours), 2)
            self.assertEqual(trip.start_name, "Петропавловск-Камчатский")
            self.assertEqual(len(trip.drives), 3)                    # туда, между объектами, обратно
            self.assertTrue(all(t.lodging for t in trip.tours))     # ночлег у каждого объекта
            self.assertGreaterEqual(len(trip.days), 2)
            self.assertIn("Переезд", trip.days[0]["items"][0])
            self.assertIn("Горелый", work.gpx)
            self.assertIn("Вилючинский", work.gpx)
            self.assertIn("<Folder><name>Переезды</name>", work.kml)

            out = os.path.join(folder, "trip")
            os.makedirs(out)
            dialog.progress.start("тест", TRIP_STAGES)
            dialog.trip_ready(work, out)
            self.assertTrue(dialog.gpx_button.isEnabled())
            self.assertFalse(dialog.offline_button.isEnabled())
            self.assertIn("Поездка готова", dialog.result_title.text())
            names = sorted(os.listdir(out))
            self.assertEqual(names[-3:], ["report.html", "report.pdf", "trip.gpkg"])
            subfolders = [n for n in names if os.path.isdir(os.path.join(out, n))]
            self.assertEqual(len(subfolders), 2)
            for sub in subfolders:
                self.assertRegex(sub, r"^[12]_[a-z0-9_]+$")
                self.assertEqual(sorted(os.listdir(os.path.join(out, sub))),
                                 ["report.html", "report.pdf", "tour.gpkg"])
            with open(os.path.join(out, "report.html"), encoding="utf-8") as handle:
                html = handle.read()
            for sub in subfolders:
                self.assertIn("%s/report.html" % sub, html)
            self.assertIn("План по дням", html)
            self.assertEqual(len(dialog.layouts_menu.actions()), 7)   # карта поездки + 3 × 2
            dialog.open_layout("trip")
            self.assertIn("карта поездки", dialog.iface.opened[-1])

            # ссылкой публикуются и отчёты объектов — относительные ссылки работают
            from gis_tour_guide import publish
            dialog.settings.setValue(publish.REPO_KEY, "dzhavrshyan-geo/tours")
            dialog.session_token = "good-token"
            started = []
            dialog.run_task = lambda task, *args: started.append(task)
            dialog.share()
            share = started[0]
            self.assertEqual(sorted(share.files), sorted(s + "/report.html" for s in subfolders))
            self.assertTrue(share.run(), share.error)
            self.assertEqual(len(http.published_all), 3)
            self.assertTrue(http.published_all[-1].endswith("/index.html"))
        finally:
            task_module.Http = original
            QgsProject.instance().clear()
            shutil.rmtree(folder, ignore_errors=True)

    def test_real_button_flow_enables_gpx(self):
        """Как пользователь: «Создать экскурсию» → фоновые задачи QGIS → результат и GPX."""
        import time
        import fake_services
        from gis_tour_guide import task as task_module
        from gis_tour_guide.dialog import TourGuideDialog
        folder = tempfile.mkdtemp()
        http = fake_services.FakeHttp()
        original = task_module.Http
        task_module.Http = lambda *args, **kwargs: http
        dialog = TourGuideDialog(FakeIface())
        try:
            dialog.folder_edit.setText(folder)
            dialog.half_km.setValue(10)
            dialog.max_points.setValue(2)
            dialog.confirm_radius = lambda place: True
            dialog.query.setText("вулкан Горелый")
            dialog.show()
            dialog.create_tour()
            started = time.monotonic()
            while not dialog.result.isVisible() and time.monotonic() - started < 120:
                APP.processEvents()
                time.sleep(0.02)
            self.assertTrue(dialog.result.isVisible())
            self.assertTrue(dialog.gpx_button.isEnabled())
            dialog.progress.on_button()                       # «Закрыть» окно загрузки
            APP.processEvents()
            self.assertTrue(dialog.gpx_button.isEnabled())

            # офлайн-пакет: архив с отчётом, треками и собственной картой района
            import sqlite3
            import zipfile
            dialog.make_offline()
            started = time.monotonic()
            while not dialog.offline_path and time.monotonic() - started < 120:
                APP.processEvents()
                time.sleep(0.02)
            self.assertTrue(dialog.offline_path.endswith("_offline.zip"))
            with zipfile.ZipFile(dialog.offline_path) as archive:
                names = sorted(archive.namelist())
                self.assertEqual(names, ["README.txt", "report.html", "report.pdf", "горелый.gpx",
                                         "горелый.kml", "горелый.mbtiles"])
                tiles_path = os.path.join(folder, "check.mbtiles")
                with open(tiles_path, "wb") as handle:
                    handle.write(archive.read("горелый.mbtiles"))
            db = sqlite3.connect(tiles_path)
            meta = dict(db.execute("SELECT name, value FROM metadata"))
            self.assertEqual(meta["format"], "png")
            count = db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
            self.assertGreater(count, 20)
            self.assertLessEqual(count, 3000)
            from qgis.PyQt.QtGui import QImage
            blob = db.execute("SELECT tile_data FROM tiles WHERE zoom_level = ? LIMIT 1",
                              (int(meta["maxzoom"]) - 3,)).fetchone()[0]
            image = QImage.fromData(bytes(blob))
            self.assertEqual((image.width(), image.height()), (256, 256))
            db.close()

            # публикация ссылкой (GitHub Pages)
            import base64
            from gis_tour_guide import publish
            shown = []
            dialog.settings.setValue(publish.REPO_KEY, "dzhavrshyan-geo/tours")
            dialog.session_token = "good-token"
            dialog.show_share = shown.append
            dialog.share()
            started = time.monotonic()
            while not dialog.share_url and time.monotonic() - started < 60:
                APP.processEvents()
                time.sleep(0.02)
            from gis_tour_guide.text import latin_slug
            folder_name = latin_slug(os.path.basename(dialog.folder))
            self.assertTrue(folder_name.startswith("gorelyy_"))
            self.assertEqual(dialog.share_url, "https://dzhavrshyan-geo.github.io/tours/tours/%s/" % folder_name)
            path, body = http.published
            self.assertEqual(path, "/repos/dzhavrshyan-geo/tours/contents/tours/%s/index.html" % folder_name)
            self.assertIn("<!DOCTYPE html>", base64.b64decode(body["content"]).decode("utf-8"))
            self.assertTrue(shown[0].qr)
        finally:
            dialog.hide()
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
