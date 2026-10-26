# -*- coding: utf-8 -*-
"""Части без QGIS: геометрия, тексты, порядок обхода, OSM, маршруты, рельеф, сеть.

Запуск:  python -m unittest discover tests
"""

import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gis_tour_guide import geo, net, osm, routing, terrain, text, tsp, wiki  # noqa: E402
from gis_tour_guide.model import Poi, Settlement  # noqa: E402


class GeoTest(unittest.TestCase):

    def test_distance_and_bbox(self):
        # 1° широты ≈ 111 км
        self.assertAlmostEqual(geo.haversine_m(158, 52, 158, 53) / 1000, 111.2, delta=0.3)
        west, south, east, north = geo.bbox_around(158.03, 52.558, 10)
        self.assertAlmostEqual(geo.haversine_m(158.03, south, 158.03, north) / 1000, 20, delta=0.2)
        self.assertAlmostEqual(geo.haversine_m(west, 52.558, east, 52.558) / 1000, 20, delta=0.2)

    def test_tiles_roundtrip(self):
        x, y = geo.lonlat_to_tile(158.03, 52.558, 12)
        lon, lat = geo.tile_to_lonlat(x, y, 12)
        self.assertAlmostEqual(lon, 158.03, places=6)
        self.assertAlmostEqual(lat, 52.558, places=6)

    def test_simplify_and_densify(self):
        line = [(0, 0), (0.001, 0.00001), (0.002, 0), (0.002, 0.01)]
        self.assertEqual(geo.simplify(line, 5), [(0, 0), (0.002, 0), (0.002, 0.01)])
        dense = geo.densify([(0, 0), (0, 0.01)], 100)
        self.assertEqual(len(dense), 13)

    def test_tobler_and_daylight(self):
        self.assertAlmostEqual(geo.tobler_kmh(-0.05), 6.0)
        self.assertLess(geo.tobler_kmh(0.3), 2.5)
        self.assertAlmostEqual(geo.daylight_hours(0, 80), 12.1, delta=0.2)
        self.assertGreater(geo.daylight_hours(60, 172), 18)
        self.assertEqual(geo.daylight_hours(80, 172), 24.0)


class TextTest(unittest.TestCase):

    def test_formatting(self):
        self.assertEqual(text.fmt_km(3456), "3,5 км")
        self.assertEqual(text.fmt_km(85200), "85 км")
        self.assertEqual(text.fmt_hours(0.5), "30 мин")
        self.assertEqual(text.fmt_hours(2.25), "2 ч 15 мин")
        self.assertEqual(text.plural(21, "объект", "объекта", "объектов"), "объект")
        self.assertEqual(text.plural(12, "объект", "объекта", "объектов"), "объектов")
        self.assertEqual(text.plural(3, "объект", "объекта", "объектов"), "объекта")

    def test_html_and_names(self):
        self.assertEqual(text.strip_html("<p>Вид&nbsp;на <b>кратер</b></p><p>2</p>"), "Вид\xa0на кратер\n2")
        self.assertEqual(text.norm_name("Вулкан «Горелый»"), "горелый")
        self.assertEqual(text.norm_name("гора Верблюд"), text.norm_name("Верблюд"))
        self.assertEqual(text.clip("Раз. Два. Три четыре пять.", 12), "Раз. Два.")


class TspTest(unittest.TestCase):

    def matrix(self, points):
        return [[math.dist(a, b) for b in points] for a in points]

    def test_exact_loop_visits_square_in_order(self):
        points = [(0, 0), (10, 10), (0, 10), (10, 0)]
        order = tsp.solve(self.matrix(points), loop=True)
        self.assertEqual(order[0], 0)
        self.assertAlmostEqual(tsp.tour_cost(order, self.matrix(points), True), 40)

    def test_open_path_ends_far_away(self):
        points = [(0, 0), (3, 0), (1, 0), (2, 0)]
        self.assertEqual(tsp.solve(self.matrix(points), loop=False), [0, 2, 3, 1])

    def test_heuristic_matches_exact_on_circle(self):
        points = [(math.cos(a) * 10, math.sin(a) * 10) for a in
                  [i * 2 * math.pi / 16 for i in (0, 5, 9, 2, 14, 7, 11, 3, 13, 1, 8, 15, 4, 10, 6, 12)]]
        dist = self.matrix(points)
        order = tsp.solve(dist, loop=True)
        self.assertEqual(sorted(order), list(range(16)))
        perimeter = 16 * 2 * 10 * math.sin(math.pi / 16)
        self.assertAlmostEqual(tsp.tour_cost(order, dist, True), perimeter, places=6)


class OsmTest(unittest.TestCase):

    def test_classify(self):
        self.assertEqual(osm.classify({"natural": "volcano"})[0], "volcano")
        self.assertIsNone(osm.classify({"natural": "peak"}))           # без имени
        self.assertEqual(osm.classify({"natural": "peak", "name": "Верблюд"})[1], "Вершина")
        self.assertEqual(osm.classify({"historic": "ruins", "name": "Форт"})[1], "Руины")
        self.assertIsNone(osm.classify({"natural": "water", "water": "river", "name": "Река"}))
        self.assertEqual(osm.classify({"waterway": "waterfall"})[0], "waterfall")

    def test_query_contains_every_category(self):
        query = osm.poi_query((158, 52, 158.1, 52.1))
        self.assertIn('nwr["historic"]["name"](52.000000,158.000000,52.100000,158.100000);', query)
        self.assertIn("cave_entrance", query)
        self.assertIn('nwr["natural"~"^(beach|cliff|glacier|peak|rock|stone|water)$"]["name"]', query)

    def test_poi_and_dedupe(self):
        center = (158.03, 52.558)
        first = osm.poi_from_element({"type": "node", "id": 1, "lat": 52.53, "lon": 158.075, "tags": {
            "natural": "peak", "name": "Верблюд", "ele": "1100 m"}}, center)
        twin = osm.poi_from_element({"type": "node", "id": 2, "lat": 52.531, "lon": 158.075, "tags": {
            "natural": "peak", "name": "гора Верблюд", "wikidata": "Q5"}}, center)
        self.assertEqual(first.elevation, 1100)
        kept = osm.dedupe([first, twin])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].wikidata, "Q5")
        self.assertEqual(kept[0].elevation, 1100)


class UrbanAndStartTest(unittest.TestCase):

    def test_urban_categories_need_article(self):
        self.assertEqual(osm.classify({"amenity": "place_of_worship", "name": "Храм", "wikidata": "Q1"},
                                      "urban")[0], "worship")
        self.assertIsNone(osm.classify({"amenity": "place_of_worship", "name": "Храм"}, "urban"))
        self.assertIsNone(osm.classify({"historic": "memorial", "memorial": "plaque", "name": "Доска",
                                        "wikidata": "Q2"}, "urban"))
        self.assertIsNone(osm.classify({"natural": "volcano"}, "urban"))
        query = osm.poi_query((37.6, 55.7, 37.7, 55.8), "urban")
        self.assertIn('nwr["historic"]["name"]["wikidata"]', query)
        self.assertIn('nwr["tourism"~"^(aquarium|attraction|gallery|museum|theme_park|zoo)$"]["name"]', query)

    def test_rank_by_popularity(self):
        museum = Poi("a", "Музей", 0, 0, score=13, popularity=800)
        theatre = Poi("b", "Театр", 0, 0, score=10, popularity=1500)
        cave = Poi("c", "Пещера", 0, 0, score=8)
        peak = Poi("d", "Вершина", 0, 0, score=6)
        self.assertEqual([p.name for p in osm.rank([cave, museum, peak, theatre])],
                         ["Театр", "Музей", "Пещера", "Вершина"])

    def test_pick_start(self):
        point = (158.03, 52.56)
        capital = Settlement("Центр", 158.65, 53.02, "city", 160000, "capital")
        far_big = Settlement("Большой далёкий", 165.0, 60.0, "city", 300000, "major")
        start, reason = osm.pick_start(capital, [far_big], point)
        self.assertEqual((start.name, reason), ("Центр", "региональный центр"))
        near_big = Settlement("Большой рядом", 158.10, 52.60, "city", 250000, "major")
        small = Settlement("Маленький рядом", 158.04, 52.57, "town", 20000, "major")
        start, reason = osm.pick_start(capital, [far_big, near_big, small], point)
        self.assertEqual(start.name, "Большой рядом")
        self.assertIn("200 тыс", reason)
        self.assertEqual(osm.pick_start(None, [], point), (None, ""))

    def test_population_and_settlements(self):
        self.assertEqual(osm.population({"population": "1 234 567"}), 1234567)
        self.assertEqual(osm.population({"population": "12000;2010"}), 12000)
        self.assertEqual(osm.population({}), 0)

    def test_popularity_from_pageviews(self):
        sys.path.insert(0, os.path.dirname(__file__))
        from fake_services import FakeHttp
        pois = [Poi("a", "Водопад", 0, 0, wikipedia="ru:Вилючинский водопад"),
                Poi("b", "Музей", 0, 0, wikidata="Q12"),
                Poi("c", "Без статьи", 0, 0)]
        wiki.popularity(FakeHttp(), pois)
        self.assertAlmostEqual(pois[0].popularity, 300)
        self.assertEqual(pois[1].wikipedia, "ru:Государственный исторический музей")
        self.assertAlmostEqual(pois[1].popularity, 800)
        self.assertEqual(pois[2].popularity, 0)


class HedgedOverpassTest(unittest.TestCase):

    def test_slow_mirror_does_not_block(self):
        import threading
        import time

        class Slow:
            def __init__(self):
                self.release = threading.Event()

            def check(self):
                pass

            def json(self, url, **_kwargs):
                if url == osm.OVERPASS_URLS[0]:
                    self.release.wait(10)          # первое зеркало «висит»
                    return {"elements": [{"id": "slow"}]}
                return {"elements": [{"id": "fast"}]}

        http = Slow()
        old = osm.HEDGE_AFTER
        osm.HEDGE_AFTER = 0.3
        try:
            started = time.monotonic()
            self.assertEqual(osm.overpass(http, "q"), [{"id": "fast"}])
            self.assertLess(time.monotonic() - started, 3)
        finally:
            osm.HEDGE_AFTER = old
            http.release.set()

    def test_error_moves_to_next_mirror_at_once(self):
        class Failing:
            calls = []

            def check(self):
                pass

            def json(self, url, **_kwargs):
                self.calls.append(url)
                if url != osm.OVERPASS_URLS[-1]:
                    raise net.NetError("HTTP 504")
                return {"elements": [1]}

        self.assertEqual(osm.overpass(Failing(), "q"), [1])
        self.assertEqual(Failing.calls, osm.OVERPASS_URLS)

    def test_lodging_segments(self):
        self.assertEqual(osm.segment({"tourism": "hotel", "stars": "5"}), "premium")
        self.assertEqual(osm.segment({"tourism": "hotel", "name": "Spa Resort"}), "premium")
        self.assertEqual(osm.segment({"tourism": "hotel", "stars": "3"}), "middle")
        self.assertEqual(osm.segment({"tourism": "hotel"}), "middle")
        self.assertEqual(osm.segment({"tourism": "hostel"}), "budget")
        self.assertEqual(osm.segment({"tourism": "guest_house"}), "budget")
        self.assertEqual(osm.segment({"tourism": "wilderness_hut"}), "budget")


class SeasonTest(unittest.TestCase):

    def test_opening_hours(self):
        import datetime
        from gis_tour_guide import hours
        mon, tue, jul = datetime.date(2026, 10, 26), datetime.date(2026, 10, 27), datetime.date(2026, 7, 7)
        self.assertEqual(hours.status("Mo,We-Su 10:00-18:00", tue), ("closed", ""))
        self.assertEqual(hours.status("Tu-Su 10:00-18:00; Mo off", mon), ("closed", ""))
        self.assertEqual(hours.status("Tu-Su 10:00-18:00; Mo off", tue), ("open", "10:00-18:00"))
        self.assertEqual(hours.status("May-Sep Mo-Su 09:00-20:00", jul), ("open", "09:00-20:00"))
        self.assertEqual(hours.status("May-Sep Mo-Su 09:00-20:00", mon), ("closed", ""))
        self.assertEqual(hours.status("24/7", mon), ("open", "круглосуточно"))
        self.assertEqual(hours.status("PH off", mon), (None, ""))
        self.assertEqual(hours.status("sunrise-sunset", mon), (None, ""))
        self.assertEqual(hours.status("", mon), (None, ""))

    def test_seasonal_roads(self):
        self.assertEqual(osm.months_of("no @ (Nov-Apr)"), {11, 12, 1, 2, 3, 4})
        self.assertIn("закрыта", osm.season_note({"highway": "track", "name": "Дорога",
                                                  "motor_vehicle:conditional": "no @ (Nov-Apr)"}, 12))
        self.assertIsNone(osm.season_note({"highway": "track", "motor_vehicle:conditional": "no @ (Nov-Apr)"}, 7))
        self.assertIn("летом", osm.season_note({"highway": "track", "winter_road": "yes"}, 7))
        self.assertIsNone(osm.season_note({"mountain_pass": "yes", "name": "П"}, 7))
        self.assertIn("цепи", osm.season_note({"mountain_pass": "yes", "name": "П"}, 1))


class GroupingTest(unittest.TestCase):

    def test_close_related_objects_become_one_stop(self):
        main = Poi("main", "Горелый", 158.030, 52.558, category="volcano")
        crater = Poi("a", "Кратер Активный", 158.026, 52.562, category="attraction",
                     description="кратер вулкана Горелый", popularity=15)
        cave1 = Poi("b", "Пещера Большая", 158.100, 52.500, category="cave", popularity=5)
        cave2 = Poi("c", "Пещера Малая", 158.103, 52.501, category="cave", popularity=1)
        far_cave = Poi("d", "Пещера Дальняя", 158.200, 52.500, category="cave")
        lake = Poi("e", "Озеро", 158.101, 52.5005, category="lake")
        leader, rest = osm.group_close(main, [crater, cave1, cave2, far_cave, lake])
        self.assertEqual([m.name for m in leader.members], ["Кратер Активный"])
        self.assertEqual([p.name for p in rest], ["Пещера Большая", "Пещера Дальняя", "Озеро"])
        self.assertEqual([m.name for m in rest[0].members], ["Пещера Малая"])


class RoutingTest(unittest.TestCase):

    def test_road_summary_merges_and_drops_ramps(self):
        steps = [{"distance": 9000, "name": "Шоссе"}, {"distance": 150, "name": "Съезд"},
                 {"distance": 52000, "ref": "Р-474"}, {"distance": 3000, "ref": "Р-474"},
                 {"distance": 21000}]
        roads = routing.road_summary(steps)
        self.assertEqual([r[0] for r in roads], ["Шоссе", "Р-474", "местные дороги без названия"])
        self.assertEqual(roads[0][1], 9150)
        self.assertEqual(roads[1][1], 55000)

    def test_road_label(self):
        self.assertEqual(routing.road_label({"ref": "Р-474", "name": "Мутновская"}), "Р-474 (Мутновская)")
        self.assertEqual(routing.road_label({"ref": "A1;E30"}), "A1, E30")


class FakeFootHttp:
    """Пешеходный OSRM: точка b далеко от троп или тропа делает крюк."""

    def __init__(self, route):
        self.route = route

    def json(self, url, params=None, ttl=0, **_):
        return self.route


class FootSegmentsTest(unittest.TestCase):

    def test_off_network_end_goes_by_terrain(self):
        a, b, snapped = (158.0, 52.0), (158.02, 52.0), (158.01, 52.0)
        http = FakeFootHttp({"code": "Ok", "routes": [{"distance": 690, "geometry": {
            "coordinates": [list(a), list(snapped)]}}], "waypoints": [{"distance": 5}, {"distance": 690}]})
        calls = []

        def finder(p, q):
            calls.append((p, q))
            return [p, ((p[0] + q[0]) / 2, p[1] + 0.001), q], 0.5

        segments = routing.foot_segments(http, a, b, finder)
        self.assertEqual([s.kind for s in segments], ["trail", "offtrail"])
        self.assertEqual(calls, [(snapped, b)])
        self.assertEqual(segments[-1].coords[-1], b)

    def test_huge_detour_replaced_by_direct_line(self):
        a, b = (158.0, 52.0), (158.01, 52.0)
        http = FakeFootHttp({"code": "Ok", "routes": [{"distance": 9000, "geometry": {
            "coordinates": [list(a), [158.0, 52.05], list(b)]}}],
            "waypoints": [{"distance": 5}, {"distance": 5}]})
        segments = routing.foot_segments(http, a, b, None)
        self.assertEqual([s.kind for s in segments], ["offtrail"])

    def test_no_route_service(self):
        class Down:
            def json(self, *args, **kwargs):
                raise net.NetError("нет сети")
        segments = routing.foot_segments(Down(), (0, 0), (0, 0.01), None)
        self.assertEqual(segments[0].kind, "offtrail")


class WikiTest(unittest.TestCase):

    def test_sections(self):
        sections = wiki.parse_sections(
            "Intro\n== Понять ==\nx\n== Как добраться ==\nПо трассе.\n=== На автобусе ===\nНет.\n"
            "== Ночлег ==\nКемпинг\n== Безопасность ==\nМедведи")
        self.assertEqual(sections["get_in"], "По трассе.\n▸ На автобусе\nНет.")
        self.assertEqual(sections["sleep"], "Кемпинг")
        self.assertEqual(sections["safety"], "Медведи")
        self.assertNotIn("understand", sections)

    def test_sights_filter_and_types(self):
        self.assertFalse(wiki.is_sight("посёлок в Камчатском крае"))
        self.assertTrue(wiki.is_sight("кратер вулкана"))
        self.assertEqual(wiki.guess_category("действующий вулкан")[0], "volcano")
        self.assertEqual(wiki.split_wiki_tag("en:Mount Gorely"), ("en", "Mount Gorely"))

    def test_merge_articles(self):
        pois = [Poi("osm:1", "Вилючинский водопад", 158.12, 52.6, wikidata="Q2")]
        articles = [Poi("wiki:1", "Вилючинский водопад", 158.1201, 52.6, wikidata="Q2",
                        wikipedia="ru:Вилючинский водопад", score=13),
                    Poi("wiki:2", "Кратер Активный", 158.026, 52.562, score=10)]
        merged = wiki.merge_articles(pois, articles)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].wikipedia, "ru:Вилючинский водопад")
        self.assertEqual(merged[0].score, 13)


class TerrainTest(unittest.TestCase):

    def grid_with_wall(self):
        """Плато 500 м, посередине стена-хребет 900 м с проходом у южного края."""
        values = np.full((60, 60), 500.0, np.float32)
        values[:50, 30] = 900.0
        return terrain.ElevationGrid(values, 158.0, 52.1, 0.0005, 0.0003)

    def test_path_goes_around_the_wall(self):
        grid = self.grid_with_wall()
        start, end = grid.lonlat(10, 5), grid.lonlat(10, 55)
        path, hours = terrain.least_cost_path(grid, start, end)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], end)
        rows = [grid.cell(*p)[0] for p in path]
        self.assertGreaterEqual(max(rows), 50)       # обошли через проход
        self.assertGreater(hours, 0)

    def test_lake_is_avoided_and_river_crossed_once(self):
        values = np.full((60, 60), 500.0, np.float32)
        grid = terrain.ElevationGrid(values, 158.0, 52.1, 0.0005, 0.0003)
        start, end = grid.lonlat(30, 5), grid.lonlat(30, 55)
        w, s_, e, n = grid.bbox
        lake = [(158.012, 52.0880), (158.018, 52.0880), (158.018, 52.0940), (158.012, 52.0940), (158.012, 52.0880)]
        river = [(158.024, s_), (158.024, n)]
        mask = terrain.water_mask(grid, {"polygons": [("water", lake)], "lines": [("river", river)]})
        self.assertEqual(mask[grid.cell(158.015, 52.091)], float("inf"))
        path, hours = terrain.least_cost_path(grid, start, end, mask=mask)
        dense = geo.densify(path, 10)
        inside = terrain.inside(np.array([p[0] for p in dense]), np.array([p[1] for p in dense]), lake)
        self.assertFalse(inside.any())
        plain, plain_hours = terrain.least_cost_path(grid, start, end)
        self.assertGreater(hours, plain_hours)        # обход озера и брод — дольше

    def test_blocked_returns_none(self):
        grid = self.grid_with_wall()
        grid.values[:, 30] = 2000.0
        self.assertIsNone(terrain.least_cost_path(grid, grid.lonlat(10, 5), grid.lonlat(10, 55)))

    def test_profile_climb_and_time(self):
        class Slope:
            def sample(self, lon, lat):
                return 500 + (lat - 52.0) * 110574 * 0.1     # подъём 10 %

        coords = [(158.0, 52.0), (158.0, 52.02)]
        prof = terrain.profile(Slope(), coords, 50)
        up, down, low, high = terrain.climb(prof)
        self.assertAlmostEqual(up, 221, delta=3)
        self.assertEqual(down, 0)
        hours = terrain.walk_hours(prof, geo.line_length_m(coords))
        flat = terrain.walk_hours([], geo.line_length_m(coords))
        self.assertGreater(hours, 2.2 / 5.04 * 0.99)   # медленнее, чем по ровному у Тоблера
        self.assertAlmostEqual(flat, 2.211 / 4.0, places=2)
        self.assertAlmostEqual(terrain.walk_hours([], 1000, offtrail=True), 1 / 2.4, places=3)


class HttpTest(unittest.TestCase):

    def test_cache_and_errors(self):
        calls = []

        def transport(url, data, headers, timeout):
            calls.append((url, data, headers["User-Agent"]))
            if "missing" in url:
                return 404, b""
            return 200, b'{"ok": 1}'

        folder = tempfile.mkdtemp()
        http = net.Http(folder, transport=transport)
        self.assertEqual(http.json("https://example.org/a", {"q": "вулкан"}), {"ok": 1})
        self.assertEqual(http.json("https://example.org/a", {"q": "вулкан"}), {"ok": 1})
        self.assertEqual(len(calls), 1)
        self.assertIn("q=%D0%B2", calls[0][0])
        self.assertTrue(calls[0][2].startswith("GIS-Tour-Guide"))
        http.json("https://example.org/b", data={"data": "[out:json];"})
        self.assertEqual(calls[-1][1], b"data=%5Bout%3Ajson%5D%3B")
        with self.assertRaises(net.NetError):
            http.get("https://example.org/missing")

    def test_cancel(self):
        http = net.Http(transport=lambda *a: (200, b"{}"), is_cancelled=lambda: True)
        with self.assertRaises(net.Cancelled):
            http.get("https://example.org/")


if __name__ == "__main__":
    unittest.main()
