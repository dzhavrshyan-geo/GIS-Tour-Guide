# -*- coding: utf-8 -*-
"""OpenStreetMap: Overpass (объекты, магазины, ООПТ, города) и классификация тегов."""

from . import geo
from .model import Poi
from .net import NetError
from .text import norm_name

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# (ключ, тег, значения, подпись, баллы, нужно ли имя)
CATEGORIES = [
    ("volcano", "natural", ("volcano",), "Вулкан", 10, False),
    ("geyser", "natural", ("geyser",), "Гейзер", 9, False),
    ("fumarole", "natural", ("fumarole",), "Фумаролы", 7, False),
    ("hot_spring", "natural", ("hot_spring",), "Горячий источник", 8, False),
    ("waterfall", "natural", ("waterfall",), "Водопад", 8, False),
    ("waterfall", "waterway", ("waterfall",), "Водопад", 8, False),
    ("cave", "natural", ("cave_entrance",), "Пещера", 8, False),
    ("arch", "natural", ("arch",), "Скальная арка", 7, False),
    ("geological", "geological", ("volcanic_caldera_rim", "volcanic_vent", "volcanic_lava_field",
                                  "outcrop", "palaeontological_site", "glacial_erratic", "moraine"),
     "Геологический объект", 6, False),
    ("glacier", "natural", ("glacier",), "Ледник", 6, True),
    ("peak", "natural", ("peak",), "Вершина", 6, True),
    ("viewpoint", "tourism", ("viewpoint",), "Смотровая площадка", 6, False),
    ("attraction", "tourism", ("attraction",), "Достопримечательность", 7, True),
    ("museum", "tourism", ("museum",), "Музей", 7, True),
    ("historic", "historic", None, "Исторический объект", 5, True),
    ("spring", "natural", ("spring",), "Родник", 4, False),
    ("rock", "natural", ("rock", "stone"), "Скала / камень", 4, True),
    ("cliff", "natural", ("cliff",), "Скалы", 4, True),
    ("lake", "natural", ("water",), "Озеро", 4, True),
    ("beach", "natural", ("beach",), "Пляж", 3, True),
    ("hut", "tourism", ("alpine_hut", "wilderness_hut"), "Приют / хижина", 4, False),
    ("camp", "tourism", ("camp_site",), "Стоянка / кемпинг", 3, False),
    ("artwork", "tourism", ("artwork",), "Арт-объект", 3, True),
    ("picnic", "tourism", ("picnic_site",), "Место для пикника", 2, False),
]

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


def classify(tags):
    """(ключ, подпись, баллы) или None для тегов OSM."""
    for key, tag, values, label, score, need_name in CATEGORIES:
        value = tags.get(tag)
        if not value or value == "no":
            continue
        if values and value not in values:
            continue
        if need_name and not tag_name(tags):
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


def poi_query(bbox):
    box = bbox_filter(bbox)
    parts = []
    by_tag = {}
    for _key, tag, values, _label, _score, need_name in CATEGORIES:
        by_tag.setdefault((tag, need_name), set()).update(values or ("*",))
    for (tag, need_name), values in sorted(by_tag.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        named = '["name"]' if need_name else ""
        if "*" in values:
            parts.append('nwr["%s"]%s%s;' % (tag, named, box))
        else:
            parts.append('nwr["%s"~"^(%s)$"]%s%s;' % (tag, "|".join(sorted(values)), named, box))
    return "[out:json][timeout:90];\n(\n%s\n);\nout center tags;" % "\n".join(parts)


def poi_from_element(element, center, lang="ru"):
    tags = element.get("tags") or {}
    found = classify(tags)
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


def find_pois(http, bbox, center, lang="ru"):
    elements = overpass(http, poi_query(bbox))
    pois = []
    for element in elements:
        poi = poi_from_element(element, center, lang)
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
            for attr in ("wikipedia", "wikidata", "description", "elevation"):
                if not getattr(twin, attr) and getattr(poi, attr):
                    setattr(twin, attr, getattr(poi, attr))
    return kept


def rank(pois):
    return sorted(pois, key=lambda p: (-p.score, p.distance_km))


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
