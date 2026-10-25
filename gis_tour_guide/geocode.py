# -*- coding: utf-8 -*-
"""Поиск объекта по названию: Nominatim, затем Википедия. Граница региона."""

from . import geo, osm, wiki
from .model import Place
from .net import NetError
from .text import TYPE_WORDS, norm_name

NOMINATIM = "https://nominatim.openstreetmap.org"

# Тип из запроса → категории OSM, которые ему соответствуют
TYPE_MATCH = {
    "volcano": ("volcano", "peak", "geological"), "peak": ("peak", "volcano"),
    "cave": ("cave",), "waterfall": ("waterfall",), "lake": ("lake",),
    "spring": ("spring", "hot_spring"), "geyser": ("geyser",), "glacier": ("glacier",),
}


def query_type(query):
    for word in query.lower().replace("ё", "е").split():
        if word in TYPE_WORDS:
            return TYPE_WORDS[word]
    return None


def place_from_nominatim(item, lang="ru"):
    tags = dict(item.get("extratags") or {})
    tags[item.get("category") or item.get("class", "")] = item.get("type", "")
    names = item.get("namedetails") or {}
    name = names.get("name:" + lang) or item.get("name") or names.get("name") or \
        item.get("display_name", "").split(",")[0]
    found = osm.classify(dict(tags, name=name))
    address = item.get("address") or {}
    bbox = None
    if item.get("boundingbox"):
        south, north, west, east = [float(v) for v in item["boundingbox"]]
        bbox = (west, south, east, north)
    elevation = None
    try:
        elevation = float(str(tags.get("ele", "")).replace(",", "."))
    except ValueError:
        pass
    return Place(
        name=name, lon=float(item["lon"]), lat=float(item["lat"]),
        kind=found[1] if found else (item.get("type") or "").replace("_", " "),
        category=found[0] if found else "",
        display_name=item.get("display_name", ""),
        region=address.get("state") or address.get("region") or address.get("province") or
        address.get("county", ""),
        city=address.get("city") or address.get("town") or address.get("village", ""),
        country=address.get("country", ""), country_code=(address.get("country_code") or "").lower(),
        osm_id="%s/%s" % (item.get("osm_type", ""), item.get("osm_id", "")),
        wikipedia=tags.get("wikipedia", ""), wikidata=tags.get("wikidata", ""),
        elevation=elevation, bbox=bbox,
    )


def nominatim_search(http, query, lang="ru", limit=8):
    params = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1,
              "extratags": 1, "namedetails": 1, "accept-language": lang}
    return [place_from_nominatim(item, lang)
            for item in http.json(NOMINATIM + "/search", params, ttl=30 * 86400)]


def geocode(http, query, lang="ru", limit=8):
    """Список подходящих мест, лучшие первыми."""
    query = query.strip()
    wanted = query_type(query)
    places = []
    try:
        places = nominatim_search(http, query, lang, limit)
        if not places and wanted:
            # «вулкан Горелый» → «Горелый», но только объекты нужного типа
            bare = " ".join(w for w in query.split() if w.lower().replace("ё", "е") not in TYPE_WORDS)
            if bare:
                places = [p for p in nominatim_search(http, bare, lang, limit * 2)
                          if p.category in TYPE_MATCH.get(wanted, (wanted,))]
    except NetError:
        pass  # остаётся Википедия
    try:
        for place in wiki.search_places(http, query, lang):
            if not any(geo.dist_m(place.point, p.point) < 3000 for p in places):
                places.append(place)
    except NetError:
        pass

    def weight(place):
        score = 0
        if wanted and place.category in TYPE_MATCH.get(wanted, (wanted,)):
            score -= 10
        if norm_name(place.name) == norm_name(query):
            score -= 3
        if place.category:
            score -= 2
        return score

    places.sort(key=weight)  # sort устойчивая: внутри равных — порядок Nominatim
    return places


def region_boundary(http, lon, lat, lang="ru"):
    """(название, GeoJSON-геометрия, bbox, id отношения OSM) — субъект/штат, где лежит точка."""
    params = {"lat": "%.6f" % lat, "lon": "%.6f" % lon, "zoom": 5, "format": "jsonv2",
              "polygon_geojson": 1, "polygon_threshold": 0.005, "accept-language": lang}
    item = http.json(NOMINATIM + "/reverse", params, ttl=90 * 86400)
    if not item or "error" in item:
        return "", None, None, 0
    bbox = None
    if item.get("boundingbox"):
        south, north, west, east = [float(v) for v in item["boundingbox"]]
        bbox = (west, south, east, north)
    name = item.get("name") or item.get("display_name", "").split(",")[0]
    rel_id = int(item.get("osm_id") or 0) if item.get("osm_type") == "relation" else 0
    return name, item.get("geojson"), bbox, rel_id


def reverse_name(http, lon, lat, lang="ru", zoom=14):
    """Ближайший населённый пункт/место — для подписи точки начала тропы."""
    params = {"lat": "%.6f" % lat, "lon": "%.6f" % lon, "zoom": zoom, "format": "jsonv2",
              "accept-language": lang, "addressdetails": 1}
    try:
        item = http.json(NOMINATIM + "/reverse", params, ttl=90 * 86400)
    except NetError:
        return ""
    address = item.get("address") or {}
    for key in ("village", "hamlet", "town", "city", "locality", "isolated_dwelling",
                "suburb", "county"):
        if address.get(key):
            return address[key]
    return ""
