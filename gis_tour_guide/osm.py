# -*- coding: utf-8 -*-
"""OpenStreetMap: Overpass (объекты, магазины, ООПТ, города, транспорт) и классификация тегов."""

import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import geo
from .model import Poi, Settlement
from .net import NetError
from .text import norm_name

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# (ключ, тег, значения, подпись, баллы, нужно ли имя, нужна ли статья/Викиданные)
NATURE_CATEGORIES = [
    ("volcano", "natural", ("volcano",), "Вулкан", 10, False, False),
    ("geyser", "natural", ("geyser",), "Гейзер", 9, False, False),
    ("fumarole", "natural", ("fumarole",), "Фумаролы", 7, False, False),
    ("hot_spring", "natural", ("hot_spring",), "Горячий источник", 8, False, False),
    ("waterfall", "natural", ("waterfall",), "Водопад", 8, False, False),
    ("waterfall", "waterway", ("waterfall",), "Водопад", 8, False, False),
    ("cave", "natural", ("cave_entrance",), "Пещера", 8, False, False),
    ("arch", "natural", ("arch",), "Скальная арка", 7, False, False),
    ("geological", "geological", ("volcanic_caldera_rim", "volcanic_vent", "volcanic_lava_field",
                                  "outcrop", "palaeontological_site", "glacial_erratic", "moraine"),
     "Геологический объект", 6, False, False),
    ("glacier", "natural", ("glacier",), "Ледник", 6, True, False),
    ("peak", "natural", ("peak",), "Вершина", 6, True, False),
    ("viewpoint", "tourism", ("viewpoint",), "Смотровая площадка", 6, False, False),
    ("attraction", "tourism", ("attraction",), "Достопримечательность", 7, True, False),
    ("museum", "tourism", ("museum",), "Музей", 7, True, False),
    ("historic", "historic", None, "Исторический объект", 5, True, False),
    ("spring", "natural", ("spring",), "Родник", 4, False, False),
    ("rock", "natural", ("rock", "stone"), "Скала / камень", 4, True, False),
    ("cliff", "natural", ("cliff",), "Скалы", 4, True, False),
    ("lake", "natural", ("water",), "Озеро", 4, True, False),
    ("beach", "natural", ("beach",), "Пляж", 3, True, False),
    ("hut", "tourism", ("alpine_hut", "wilderness_hut"), "Приют / хижина", 4, False, False),
    ("camp", "tourism", ("camp_site",), "Стоянка / кемпинг", 3, False, False),
    ("artwork", "tourism", ("artwork",), "Арт-объект", 3, True, False),
    ("picnic", "tourism", ("picnic_site",), "Место для пикника", 2, False, False),
]

# В городе объектов тысячи: исторические здания, храмы и фонтаны берём только со статьёй
URBAN_CATEGORIES = [
    ("museum", "tourism", ("museum",), "Музей", 8, True, False),
    ("gallery", "tourism", ("gallery",), "Галерея", 6, True, False),
    ("zoo", "tourism", ("zoo", "aquarium"), "Зоопарк / океанариум", 7, True, False),
    ("attraction", "tourism", ("attraction",), "Достопримечательность", 7, True, False),
    ("viewpoint", "tourism", ("viewpoint",), "Смотровая площадка", 6, False, False),
    ("theme_park", "tourism", ("theme_park",), "Парк развлечений", 5, True, False),
    ("historic", "historic", None, "Исторический объект", 6, True, True),
    ("worship", "amenity", ("place_of_worship",), "Храм", 5, True, True),
    ("theatre", "amenity", ("theatre",), "Театр", 5, True, True),
    ("park", "leisure", ("park", "garden"), "Парк / сад", 4, True, True),
    ("square", "place", ("square",), "Площадь", 4, True, True),
    ("tower", "man_made", ("tower", "lighthouse"), "Башня / маяк", 4, True, True),
    ("fountain", "amenity", ("fountain",), "Фонтан", 3, True, True),
    ("artwork", "tourism", ("artwork",), "Скульптура / арт-объект", 3, True, True),
]

CATEGORIES = NATURE_CATEGORIES   # совместимость: по умолчанию — природа

# Явно природные объекты — для них тип туризма «природный» без проверки застройки
NATURE_KEYS = ("volcano", "geyser", "fumarole", "hot_spring", "waterfall", "cave", "arch",
               "geological", "glacier", "peak", "spring", "rock", "cliff", "lake", "beach")
URBAN_KEYS = ("museum", "gallery", "zoo", "theatre", "worship", "square", "park", "fountain",
              "tower", "theme_park")

# Длительность осмотра, мин
VISIT_MINUTES = {"museum": 75, "gallery": 50, "zoo": 120, "theme_park": 90, "worship": 20,
                 "theatre": 10, "park": 30, "square": 15, "fountain": 10, "artwork": 10,
                 "tower": 20, "viewpoint": 20, "historic": 20, "attraction": 30}

HISTORIC_KINDS = {
    "monument": "Памятник", "memorial": "Мемориал", "ruins": "Руины",
    "archaeological_site": "Археологический памятник", "castle": "Замок",
    "fort": "Крепость", "church": "Храм", "monastery": "Монастырь",
    "wayside_cross": "Поклонный крест", "battlefield": "Место сражения",
    "mine": "Старый рудник", "manor": "Усадьба", "city_gate": "Городские ворота",
}
GEOLOGICAL_KINDS = {
    "volcanic_caldera_rim": "Край кальдеры", "volcanic_vent": "Жерло / кратер",
    "volcanic_lava_field": "Лавовое поле", "outcrop": "Обнажение пород",
    "palaeontological_site": "Палеонтологическое местонахождение",
    "glacial_erratic": "Ледниковый валун", "moraine": "Морена",
}
LAKE_WATERS = ("lake", "reservoir", "pond", "lagoon", "oxbow", None)


def tag_name(tags, lang="ru"):
    return tags.get("name:" + lang) or tags.get("name") or tags.get("name:en") or ""


def categories(mode="nature"):
    return URBAN_CATEGORIES if mode == "urban" else NATURE_CATEGORIES


def classify(tags, mode="nature"):
    """(ключ, подпись, баллы) или None для тегов OSM."""
    for key, tag, values, label, score, need_name, need_wiki in categories(mode):
        value = tags.get(tag)
        if not value or value == "no":
            continue
        if values and value not in values:
            continue
        if need_name and not tag_name(tags):
            continue
        if need_wiki and not (tags.get("wikidata") or tags.get("wikipedia")):
            continue
        if key == "historic" and tags.get("memorial") in ("plaque", "stolperstein"):
            continue
        if key == "lake" and tags.get("water") not in LAKE_WATERS:
            continue
        if key == "historic":
            label = HISTORIC_KINDS.get(value, label)
        elif key == "geological":
            label = GEOLOGICAL_KINDS.get(value, label)
        elif key == "rock" and value == "stone":
            label = "Камень"
        return key, label, score
    return None


HEDGE_AFTER = 7.0     # с — если зеркало молчит, параллельно спрашиваем следующее
QUERY_TIMEOUT = 45    # с — ждём ответа одного зеркала не дольше


def overpass_once(http, url, query, ttl):
    data = http.json(url, data={"data": query}, ttl=ttl, tries=1, timeout=QUERY_TIMEOUT)
    remark = data.get("remark") or ""
    if ("runtime error" in remark or "timed out" in remark) and not data.get("elements"):
        raise NetError("Overpass: " + remark)
    return data.get("elements", [])


def overpass(http, query, ttl=3 * 86400):
    """Запрос к Overpass «с подстраховкой»: первое зеркало, а если оно молчит дольше
    HEDGE_AFTER секунд или ошиблось — следующее параллельно; берём первый успешный ответ."""
    pool = ThreadPoolExecutor(len(OVERPASS_URLS))
    pending, errors, urls = set(), [], list(OVERPASS_URLS)
    try:
        while urls or pending:
            if urls:
                pending.add(pool.submit(overpass_once, http, urls.pop(0), query, ttl))
            started = time.monotonic()
            while pending:
                http.check()
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        return future.result()
                    except NetError as error:
                        errors.append(error)
                if done and urls:
                    break          # ошибка — сразу следующее зеркало
                if urls and time.monotonic() - started > HEDGE_AFTER:
                    break          # долго молчит — подключаем следующее, не бросая это
    finally:
        pool.shutdown(wait=False)
    raise errors[-1] if errors else NetError("Overpass недоступен")


def element_point(element):
    if "lat" in element:
        return element["lon"], element["lat"]
    center = element.get("center")
    if center:
        return center["lon"], center["lat"]
    return None


def bbox_filter(bbox):
    west, south, east, north = bbox
    return "(%.6f,%.6f,%.6f,%.6f)" % (south, west, north, east)


def poi_query(bbox, mode="nature"):
    box = bbox_filter(bbox)
    parts = []
    by_tag = {}
    for _key, tag, values, _label, _score, need_name, need_wiki in categories(mode):
        by_tag.setdefault((tag, need_name, need_wiki), set()).update(values or ("*",))
    for (tag, need_name, need_wiki), values in sorted(by_tag.items(), key=lambda kv: kv[0]):
        named = ('["name"]' if need_name else "") + ('["wikidata"]' if need_wiki else "")
        if "*" in values:
            parts.append('nwr["%s"]%s%s;' % (tag, named, box))
        else:
            parts.append('nwr["%s"~"^(%s)$"]%s%s;' % (tag, "|".join(sorted(values)), named, box))
    return "[out:json][timeout:90];\n(\n%s\n);\nout center tags;" % "\n".join(parts)


def poi_from_element(element, center, lang="ru", mode="nature"):
    tags = element.get("tags") or {}
    found = classify(tags, mode)
    point = element_point(element)
    if not found or not point:
        return None
    key, label, score = found
    name = tag_name(tags, lang)
    wikipedia = tags.get("wikipedia", "")
    wikidata = tags.get("wikidata", "")
    if wikipedia or wikidata:
        score += 5
    if tags.get("description"):
        score += 1
    if not name:
        score -= 2
        name = label
    elevation = None
    try:
        elevation = float(str(tags.get("ele", "")).replace(",", ".").split()[0])
    except (ValueError, IndexError):
        pass
    poi = Poi(
        uid="osm:%s/%s" % (element["type"], element["id"]),
        name=name, lon=point[0], lat=point[1], category=key, kind=label, score=score,
        tags=tags, wikipedia=wikipedia, wikidata=wikidata,
        description=tags.get("description:" + lang) or tags.get("description", ""),
        url="https://www.openstreetmap.org/%s/%s" % (element["type"], element["id"]),
        elevation=elevation,
    )
    poi.distance_km = geo.dist_m(center, poi.point) / 1000.0
    return poi


def find_pois(http, bbox, center, lang="ru", mode="nature"):
    elements = overpass(http, poi_query(bbox, mode))
    pois = []
    for element in elements:
        poi = poi_from_element(element, center, lang, mode)
        if poi and geo.in_bbox(poi.lon, poi.lat, bbox):
            pois.append(poi)
    return dedupe(pois)


def dedupe(pois, radius_m=700):
    """Один объект, нанесённый несколько раз (точка + контур, родник + водопад)."""
    pois = sorted(pois, key=lambda p: -p.score)
    kept = []
    for poi in pois:
        key = norm_name(poi.name)
        twin = None
        for other in kept:
            same_name = key and key == norm_name(other.name)
            same_wiki = poi.wikidata and poi.wikidata == other.wikidata
            if (same_name or same_wiki) and geo.dist_m(poi.point, other.point) < radius_m:
                twin = other
                break
        if twin is None:
            kept.append(poi)
        else:
            for attr in ("wikipedia", "wikidata", "description", "elevation", "popularity"):
                if not getattr(twin, attr) and getattr(poi, attr):
                    setattr(twin, attr, getattr(poi, attr))
    return kept


def rank(pois):
    """Самые популярные первыми (просмотры статьи + интерес по типу), при равенстве — ближе."""
    return sorted(pois, key=lambda p: (-p.rank, p.distance_km))


# --- Обслуживание: магазины, заправки, вода ---

SERVICE_FILTERS = {
    "shop": '["shop"~"^(supermarket|convenience|general|grocery|food|kiosk)$"]',
    "fuel": '["amenity"="fuel"]',
    "water": '["amenity"="drinking_water"]',
    "food": '["amenity"~"^(cafe|restaurant|fast_food)$"]',
    "lodging": '["tourism"~"^(hotel|guest_house|hostel|motel|camp_site|alpine_hut|wilderness_hut)$"]',
}


def nearest_services(http, lon, lat, radii_km=(5, 25, 80)):
    """{вид: {name, lon, lat, km}} — ближайшее, ищем расширяя радиус."""
    found = {}
    for radius in radii_km:
        missing = [k for k in SERVICE_FILTERS if k not in found]
        if not missing:
            break
        around = "(around:%d,%.6f,%.6f)" % (radius * 1000, lat, lon)
        parts = "\n".join("nwr%s%s;" % (SERVICE_FILTERS[k], around) for k in missing)
        query = "[out:json][timeout:60];\n(\n%s\n);\nout center tags 400;" % parts
        try:
            elements = overpass(http, query)
        except NetError:
            break
        for element in elements:
            point = element_point(element)
            tags = element.get("tags") or {}
            if not point:
                continue
            for kind in missing:
                if service_matches(kind, tags):
                    km = geo.dist_m((lon, lat), point) / 1000.0
                    if kind not in found or km < found[kind]["km"]:
                        found[kind] = {"name": tag_name(tags) or tags.get("brand", ""),
                                       "lon": point[0], "lat": point[1], "km": km}
    return found


def service_matches(kind, tags):
    if kind == "shop":
        return tags.get("shop") in ("supermarket", "convenience", "general", "grocery", "food", "kiosk")
    if kind == "fuel":
        return tags.get("amenity") == "fuel"
    if kind == "water":
        return tags.get("amenity") == "drinking_water"
    if kind == "food":
        return tags.get("amenity") in ("cafe", "restaurant", "fast_food")
    if kind == "lodging":
        return tags.get("tourism") in ("hotel", "guest_house", "hostel", "motel", "camp_site",
                                       "alpine_hut", "wilderness_hut")
    return False


def protected_areas(http, lon, lat):
    """ООПТ, внутри которых точка: [(название, описание)]."""
    query = ("[out:json][timeout:40];\nis_in(%.6f,%.6f)->.a;\n(\n"
             'area.a["boundary"~"^(protected_area|national_park)$"];\n'
             'area.a["leisure"="nature_reserve"];\n);\nout tags;') % (lat, lon)
    result = []
    for element in overpass(http, query):
        tags = element.get("tags") or {}
        name = tag_name(tags)
        if not name:
            continue
        kind = tags.get("protection_title") or {
            "national_park": "национальный парк", "nature_reserve": "заповедник / заказник",
        }.get(tags.get("boundary") or tags.get("leisure"), "особо охраняемая территория")
        item = (name, kind)
        if item not in result:
            result.append(item)
    return result


def nearest_town(http, lon, lat, radii_km=(60, 200, 500)):
    """Ближайший город (а если городов нет — посёлок городского типа): (имя, lon, lat)."""
    for radius in radii_km:
        query = ('[out:json][timeout:60];\nnode["place"~"^(city|town)$"](around:%d,%.6f,%.6f);\n'
                 "out 300;") % (radius * 1000, lat, lon)
        try:
            elements = overpass(http, query)
        except NetError:
            return None
        best = None
        for element in elements:
            tags = element.get("tags") or {}
            point = element_point(element)
            if not point or not tag_name(tags):
                continue
            km = geo.dist_m((lon, lat), point) / 1000.0
            # город весит больше посёлка: город в 2 раза дальше всё равно лучше
            weight = km * (0.5 if tags.get("place") == "city" else 1.0)
            if best is None or weight < best[0]:
                best = (weight, tag_name(tags), point)
        if best:
            return best[1], best[2][0], best[2][1]
    return None


# --- Тип туризма, населённые пункты, транспорт ---

def building_count(http, lon, lat, radius_m=700):
    """Сколько зданий вокруг — плотная застройка значит городской туризм."""
    query = '[out:json][timeout:30];way["building"](around:%d,%.6f,%.6f);out count;' % (
        radius_m, lat, lon)
    for element in overpass(http, query):
        if element.get("type") == "count":
            tags = element.get("tags") or {}
            return int(tags.get("ways") or tags.get("total") or 0)
    return 0


def population(tags):
    digits = re.sub(r"[^\d]", "", str(tags.get("population", "")).split(";")[0])
    return int(digits) if digits else 0


def settlement(element, role="near"):
    tags = element.get("tags") or {}
    point = element_point(element)
    if not point or not tag_name(tags):
        return None
    return Settlement(tag_name(tags), point[0], point[1], tags.get("place", ""), population(tags),
                      role)


def region_settlements(http, rel_id):
    """(региональный центр, [города и посёлки региона]) по id отношения границы региона."""
    # один запрос: id центра (out ids — без тегов) и все города и посёлки региона
    query = ('[out:json][timeout:90];rel(%d);node(r:"admin_centre");out ids;'
             'area(%d)->.a;node(area.a)["place"~"^(city|town)$"];out;') % (rel_id, 3600000000 + rel_id)
    capital, centre_ids, towns = None, set(), []
    for element in overpass(http, query, ttl=90 * 86400):
        if "tags" not in element and "lat" not in element:
            centre_ids.add(element.get("id"))
            continue
        item = settlement(element, "major")
        if not item:
            continue
        towns.append(item)
        tags = element.get("tags") or {}
        if element.get("id") in centre_ids or (not centre_ids and capital is None
                                                and tags.get("capital") in ("4", "3", "yes")):
            capital = Settlement(item.name, item.lon, item.lat, item.place, item.population, "capital")
    if capital is None and towns:
        biggest = max(towns, key=lambda t: t.population)
        capital = Settlement(biggest.name, biggest.lon, biggest.lat, biggest.place,
                             biggest.population, "capital")
    if capital is not None:
        same = next((t for t in towns if t.name == capital.name), None)
        if same and not capital.population:
            capital.population = same.population
    return capital, towns


def near_settlements(http, lon, lat, radius_km=30, limit=8):
    """Населённые пункты вокруг объекта: города и посёлки важнее деревень."""
    query = ('[out:json][timeout:60];node["place"~"^(city|town|village)$"](around:%d,%.6f,%.6f);'
             "out;") % (radius_km * 1000, lat, lon)
    found = []
    for element in overpass(http, query):
        item = settlement(element, "near")
        if item:
            km = geo.dist_m((lon, lat), item.point) / 1000.0
            weight = km * {"city": 0.3, "town": 0.6}.get(item.place, 1.0)
            found.append((weight, item))
    found.sort(key=lambda pair: pair[0])
    return [item for _, item in found[:limit]]


def pick_start(capital, towns, point, big=200000):
    """Откуда ехать: региональный центр, но если город от 200 тыс. ближе — от него."""
    options = ([capital] if capital else []) + [t for t in towns if t.population >= big]
    if not options:
        return None, ""
    best = min(options, key=lambda t: geo.dist_m(t.point, point))
    if capital and best.name != capital.name:
        return best, "ближайший к объекту город с населением больше 200 тыс."
    return best, "региональный центр"


TRANSPORT_KINDS = [("subway", "Метро"), ("train", "Ж/д станция"), ("tram", "Трамвай"),
                   ("bus", "Автобус")]


def transport_kind(tags):
    if tags.get("station") == "subway" or tags.get("subway") == "yes" or \
            tags.get("railway") == "subway_entrance":
        return "subway"
    if tags.get("railway") in ("station", "halt"):
        return "train"
    if tags.get("railway") == "tram_stop":
        return "tram"
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        return "bus"
    return None


def transport_near(http, lon, lat, radius_m=1500):
    """Ближайшие остановки: [(вид, название, км)] — по два каждого вида."""
    around = "(around:%d,%.6f,%.6f)" % (radius_m, lat, lon)
    short = "(around:%d,%.6f,%.6f)" % (min(radius_m, 600), lat, lon)
    query = ("[out:json][timeout:40];(\n"
             'nwr["station"="subway"]%s;nwr["railway"~"^(station|halt)$"]%s;'
             'node["railway"="tram_stop"]%s;node["highway"="bus_stop"]%s;'
             ");out center tags 300;") % (around, around, short, short)
    best = {}
    for element in overpass(http, query):
        tags = element.get("tags") or {}
        kind = transport_kind(tags)
        point = element_point(element)
        if not kind or not point or not tag_name(tags):
            continue
        km = geo.dist_m((lon, lat), point) / 1000.0
        items = best.setdefault(kind, {})
        name = tag_name(tags)
        if name not in items or km < items[name][1]:
            items[name] = (point, km)
    result = []
    for kind, label in TRANSPORT_KINDS:
        items = sorted(best.get(kind, {}).items(), key=lambda kv: kv[1][1])[:2]
        result += [(label, name, km, point) for name, (point, km) in items]
    return result


CUISINES = {"russian": "русская", "italian": "итальянская", "pizza": "пицца", "coffee_shop": "кофейня",
            "georgian": "грузинская", "japanese": "японская", "sushi": "суши", "burger": "бургеры",
            "chinese": "китайская", "regional": "местная", "asian": "азиатская"}


def places_to_eat(http, lon, lat, radius_m=700, limit=6):
    """Кафе и рестораны рядом с маршрутом: [(название, вид, км)]."""
    query = ('[out:json][timeout:40];nwr["amenity"~"^(cafe|restaurant)$"]["name"]'
             "(around:%d,%.6f,%.6f);out center tags 200;") % (radius_m, lat, lon)
    found = []
    for element in overpass(http, query):
        tags = element.get("tags") or {}
        point = element_point(element)
        if not point:
            continue
        kind = "кафе" if tags.get("amenity") == "cafe" else "ресторан"
        cuisine = CUISINES.get((tags.get("cuisine") or "").split(";")[0], "")
        found.append((tag_name(tags), kind + (", " + cuisine if cuisine else ""),
                      geo.dist_m((lon, lat), point) / 1000.0))
    found.sort(key=lambda item: item[2])
    return found[:limit]


# --- Один запрос на всё окружение объекта ---

def surroundings_query(bbox, lon, lat, near_km=30):
    """Объекты для природы и города, населённые пункты рядом, ООПТ и регион, плотность застройки."""
    parts = set()
    for mode in ("nature", "urban"):
        for line in poi_query(bbox, mode).split("\n"):
            if line.startswith("nwr["):
                parts.add(line)
    return "\n".join([
        "[out:json][timeout:60];",
        "(", "\n".join(sorted(parts)), ");", "out center tags;",
        'node["place"~"^(city|town|village)$"](around:%d,%.6f,%.6f);' % (near_km * 1000, lat, lon),
        "out;",
        "is_in(%.6f,%.6f)->.a;" % (lat, lon),
        '(area.a["boundary"~"^(protected_area|national_park)$"];area.a["leisure"="nature_reserve"];'
        'area.a["boundary"="administrative"]["admin_level"="4"];);',
        "out tags;",
        'way["building"](around:700,%.6f,%.6f);' % (lat, lon),
        "out count;",
    ])


def surroundings(http, bbox, lon, lat):
    """Разбор общего ответа: {elements, settlements, protected, region, buildings}."""
    elements = overpass(http, surroundings_query(bbox, lon, lat))
    result = {"elements": [], "settlements": [], "protected": [], "region": None, "buildings": None}
    near = []
    for element in elements:
        kind = element.get("type")
        tags = element.get("tags") or {}
        if kind == "count":
            result["buildings"] = int(tags.get("ways") or tags.get("total") or 0)
        elif kind == "area":
            if tags.get("admin_level") == "4" and tags.get("boundary") == "administrative":
                result["region"] = (tag_name(tags), int(element["id"]) - 3600000000)
            elif tag_name(tags):
                title = tags.get("protection_title") or {
                    "national_park": "национальный парк", "nature_reserve": "заповедник / заказник",
                }.get(tags.get("boundary") or tags.get("leisure"), "особо охраняемая территория")
                item = (tag_name(tags), title)
                if item not in result["protected"]:
                    result["protected"].append(item)
        elif kind == "node" and tags.get("place") in ("city", "town", "village"):
            item = settlement(element, "near")
            if item:
                km = geo.dist_m((lon, lat), item.point) / 1000.0
                near.append((km * {"city": 0.3, "town": 0.6}.get(item.place, 1.0), item))
        else:
            result["elements"].append(element)
    near.sort(key=lambda pair: pair[0])
    result["settlements"] = [item for _, item in near[:8]]
    return result


def pois_from(elements, bbox, center, lang="ru", mode="nature"):
    pois = []
    for element in elements:
        poi = poi_from_element(element, center, lang, mode)
        if poi and geo.in_bbox(poi.lon, poi.lat, bbox):
            pois.append(poi)
    return dedupe(pois)


# --- Ночлег ---

LODGING = '["tourism"~"^(hotel|guest_house|hostel|motel|apartment|chalet|camp_site|caravan_site|alpine_hut|wilderness_hut)$"]'
SEGMENTS = [("premium", "Дорогой / премиум"), ("middle", "Средний"), ("budget", "Бюджетный")]
LODGING_KINDS = {"hotel": "гостиница", "guest_house": "гостевой дом", "hostel": "хостел",
                 "motel": "мотель", "apartment": "апартаменты", "chalet": "шале / домик",
                 "camp_site": "кемпинг", "caravan_site": "автокемпинг",
                 "alpine_hut": "горный приют", "wilderness_hut": "избушка"}


def stars(tags):
    match = re.match(r"\s*(\d)", str(tags.get("stars", "")))
    return int(match.group(1)) if match else 0


def segment(tags):
    """Ценовой сегмент по типу и звёздам из OSM (цен в OSM почти нет)."""
    kind = tags.get("tourism")
    name = (tag_name(tags) or "").lower()
    if kind in ("hostel", "camp_site", "caravan_site", "alpine_hut", "wilderness_hut"):
        return "budget"
    if stars(tags) >= 4 or any(word in name for word in ("spa", "resort", "курорт", "luxury", "люкс")):
        return "premium"
    if stars(tags) in (1, 2) or (kind == "guest_house" and not stars(tags)):
        return "budget"
    return "middle"


PRICE_ORDER = {"hotel": 0, "chalet": 1, "apartment": 2, "motel": 3, "guest_house": 4, "hostel": 5,
               "caravan_site": 6, "camp_site": 7, "alpine_hut": 8, "wilderness_hut": 9}


def lodging(http, lon, lat, radii_km=(5, 20, 50)):
    """Ближайший ночлег в трёх ценовых сегментах: [{segment, label, name, kind, stars, km, ...}]."""
    found = []
    for radius in radii_km:
        query = "[out:json][timeout:40];nwr%s(around:%d,%.6f,%.6f);out center tags 300;" % (
            LODGING, radius * 1000, lat, lon)
        found = []
        for element in overpass(http, query):
            tags = element.get("tags") or {}
            point = element_point(element)
            if not point:
                continue
            found.append({
                "segment": segment(tags), "name": tag_name(tags) or LODGING_KINDS.get(tags.get("tourism"), ""),
                "kind": LODGING_KINDS.get(tags.get("tourism"), "ночлег"), "stars": stars(tags),
                "kind_key": tags.get("tourism", ""),
                "km": geo.dist_m((lon, lat), point) / 1000.0, "lon": point[0], "lat": point[1],
                "website": tags.get("website") or tags.get("contact:website", ""),
                "phone": tags.get("phone") or tags.get("contact:phone", ""),
                "url": "https://www.openstreetmap.org/%s/%s" % (element["type"], element["id"]),
            })
        if len({f["segment"] for f in found}) == 3 or (radius == radii_km[-1] and found):
            break
    result, used = [], set()
    for key, label in SEGMENTS:
        options = sorted((f for f in found if f["segment"] == key), key=lambda f: f["km"])
        if not options:
            # сегмента рядом нет — ближайшее по «дороговизне» из оставшегося
            rest = [f for f in found if f["url"] not in used]
            if not rest:
                continue
            price = lambda f: (-f["stars"], PRICE_ORDER.get(f["kind_key"], 5))  # noqa: E731
            options = sorted(rest, key=price if key == "premium" else lambda f: tuple(-v for v in price(f)))
            label += " (ближайший по классу)"
        item = dict(options[0], label=label)
        used.add(item["url"])
        result.append(item)
    return result


# --- Объединение близких объектов в одну остановку ---

GROUP_RADIUS_M = {"nature": 700, "urban": 150}


def name_words(text):
    """Значимые слова (от 5 букв) — «кратер», «горелый», «пещера»…"""
    return {w[:6] for w in norm_name(text).split() if len(w) >= 5}


def related(leader, other):
    if leader.category and leader.category == other.category:
        return True
    if name_words(leader.name) & name_words(other.name):
        return True
    # «кратер вулкана Горелый» относится к вулкану Горелый
    return bool(name_words(leader.name) & name_words(other.description))


def group_close(main, pois, mode="nature"):
    """Близкие и родственные объекты (кратеры одного вулкана, пещеры одного массива)
    сливаются в одну остановку: главный — самый популярный, остальные — в .members."""
    radius = GROUP_RADIUS_M.get(mode, 500)
    leaders = [main]
    for poi in pois:          # pois уже по популярности
        target = None
        for leader in leaders:
            if geo.dist_m(leader.point, poi.point) <= radius and related(leader, poi):
                target = leader
                break
        if target is None:
            leaders.append(poi)
        else:
            target.members.append(poi)
            if target is not main:
                target.popularity = max(target.popularity, poi.popularity)
    return leaders[0], leaders[1:]
