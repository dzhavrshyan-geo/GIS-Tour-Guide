# -*- coding: utf-8 -*-
"""OpenStreetMap: Overpass (объекты, магазины, ООПТ, города, транспорт) и классификация тегов."""

import re

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


def overpass(http, query, ttl=3 * 86400):
    last = None
    for url in OVERPASS_URLS:
        try:
            data = http.json(url, data={"data": query}, ttl=ttl, tries=1)
        except NetError as error:
            last = error
            continue
        remark = data.get("remark") or ""
        if "runtime error" in remark and not data.get("elements"):
            last = NetError("Overpass: " + remark)
            continue
        return data.get("elements", [])
    raise last or NetError("Overpass недоступен")


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
    capital = None
    try:
        elements = overpass(http, '[out:json][timeout:60];rel(%d);node(r:"admin_centre");out;'
                            % rel_id, ttl=90 * 86400)
        found = [settlement(e, "capital") for e in elements]
        capital = next((c for c in found if c), None)
    except NetError:
        pass
    towns = []
    elements = overpass(http, '[out:json][timeout:120];area(%d)->.a;'
                        'node(area.a)["place"~"^(city|town)$"];out;' % (3600000000 + rel_id),
                        ttl=90 * 86400)
    for element in elements:
        item = settlement(element, "major")
        if item:
            towns.append(item)
            if capital is None and (element.get("tags") or {}).get("capital") in ("4", "3", "yes"):
                capital = Settlement(item.name, item.lon, item.lat, item.place, item.population,
                                     "capital")
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
