# -*- coding: utf-8 -*-
"""Википедия, Викигид (Wikivoyage), Викиданные и Викисклад (фотографии)."""

import re
import urllib.parse

from . import geo
from .model import Photo, Place, Poi
from .net import NetError
from .text import TYPE_WORDS, clip, norm_name, strip_html

# В описаниях Викиданных: такие статьи — не достопримечательности
NOT_SIGHTS = ("населённый пункт", "посёлок", "поселок", "село ", "село,", "деревня", "город ",
              "город в", "станция", "район", "сельское поселение", "муниципальн", "улица",
              "аэропорт", "школа", "village", "town in", "city in", "municipality", "street",
              "railway station", "river", "река", "ручей", "приток", "хутор", "административн")

CATEGORY_FROM_TYPE = {
    "volcano": ("volcano", "Вулкан", 10), "peak": ("peak", "Вершина", 6),
    "cave": ("cave", "Пещера", 8), "waterfall": ("waterfall", "Водопад", 8),
    "lake": ("lake", "Озеро", 5), "spring": ("spring", "Источник", 6),
    "geyser": ("geyser", "Гейзер", 9), "glacier": ("glacier", "Ледник", 6),
    "rock": ("rock", "Скала", 5), "cliff": ("cliff", "Скалы", 5),
    "valley": ("attraction", "Долина / каньон", 6), "saddle": ("attraction", "Перевал", 5),
    "cape": ("attraction", "Мыс", 5),
}

SECTION_KEYS = [
    ("get_in", ("как добраться", "как доехать", "добраться")),
    ("get_around", ("передвижение", "как передвигаться", "транспорт")),
    ("see", ("достопримечательности", "что посмотреть")),
    ("do", ("чем заняться", "развлечения", "активный отдых", "маршруты")),
    ("buy", ("покупки",)),
    ("eat", ("где поесть", "питание", "еда")),
    ("sleep", ("ночлег", "где остановиться", "жильё", "жилье", "где переночевать")),
    ("safety", ("безопасность", "меры предосторожности", "опасности")),
]


def api(http, host, params, ttl=14 * 86400):
    query = {"format": "json", "formatversion": 2}
    query.update(params)
    data = http.json("https://%s/w/api.php" % host, query, ttl=ttl)
    if "error" in data:
        raise NetError("%s: %s" % (host, data["error"].get("info", data["error"])))
    return data


def page_url(lang, title, site="wikipedia"):
    return "https://%s.%s.org/wiki/%s" % (lang, site, urllib.parse.quote(title.replace(" ", "_")))


def split_wiki_tag(value, default_lang="ru"):
    """«ru:Горелый (вулкан)» → ('ru', 'Горелый (вулкан)')."""
    if not value:
        return None, None
    match = re.match(r"^([a-z\-]{2,12}):(.+)$", value)
    if match:
        return match.group(1), match.group(2)
    return default_lang, value


# Городские объекты по первым словам описания Викиданных
URBAN_WORDS = [
    ("музей", ("museum", "Музей", 8)), ("галерея", ("gallery", "Галерея", 6)),
    ("собор", ("worship", "Собор", 6)), ("храм", ("worship", "Храм", 5)),
    ("церковь", ("worship", "Церковь", 5)), ("монастырь", ("worship", "Монастырь", 6)),
    ("мечеть", ("worship", "Мечеть", 5)), ("синагога", ("worship", "Синагога", 5)),
    ("театр", ("theatre", "Театр", 5)), ("памятник", ("historic", "Памятник", 5)),
    ("монумент", ("historic", "Монумент", 5)), ("кремль", ("historic", "Кремль", 8)),
    ("крепость", ("historic", "Крепость", 7)), ("дворец", ("historic", "Дворец", 7)),
    ("усадьба", ("historic", "Усадьба", 6)), ("башня", ("tower", "Башня", 5)),
    ("площадь", ("square", "Площадь", 5)), ("парк", ("park", "Парк", 4)),
    ("сад", ("park", "Сад", 4)), ("мост", ("historic", "Мост", 4)),
    ("зоопарк", ("zoo", "Зоопарк", 7)), ("здание", ("historic", "Здание", 3)),
    ("особняк", ("historic", "Особняк", 4)), ("фонтан", ("fountain", "Фонтан", 3)),
]


def guess_category(description, mode="nature"):
    words = re.findall(r"\w+", (description or "").lower().replace("ё", "е"))
    if mode == "urban":
        for stem, found in URBAN_WORDS:
            if any(word.startswith(stem) for word in words[:4]):
                return found
    for word in words:
        if word in TYPE_WORDS and TYPE_WORDS[word] in CATEGORY_FROM_TYPE:
            return CATEGORY_FROM_TYPE[TYPE_WORDS[word]]
    return None


def is_sight(description):
    text = (description or "").lower() + " "
    return not any(word in text for word in NOT_SIGHTS)


def search_places(http, query, lang="ru", limit=5):
    """Статьи Википедии с координатами, подходящие под запрос."""
    data = api(http, "%s.wikipedia.org" % lang, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": limit,
        "prop": "coordinates|description|pageprops", "ppprop": "wikibase_item",
        "coprimary": "primary",
    })
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))
    places = []
    for page in pages:
        coords = page.get("coordinates")
        if not coords:
            continue
        found = guess_category(page.get("description"))
        title = page["title"]
        places.append(Place(
            name=re.sub(r"\s*\(.*\)$", "", title), lon=coords[0]["lon"], lat=coords[0]["lat"],
            kind=found[1] if found else clip(page.get("description", ""), 40),
            category=found[0] if found else "",
            display_name="%s — %s" % (title, page.get("description", "")),
            wikipedia="%s:%s" % (lang, title),
            wikidata=(page.get("pageprops") or {}).get("wikibase_item", ""), source="Википедия",
        ))
    return places


def nearby_articles(http, bbox, center, lang="ru", limit=100, mode="nature"):
    """Статьи о достопримечательностях в прямоугольнике — как Poi."""
    return articles_from(nearby_pages(http, bbox, center, lang, limit), bbox, center, lang, mode)


def nearby_pages(http, bbox, center, lang="ru", limit=100):
    """Статьи с координатами в прямоугольнике — как есть (тип туризма ещё неизвестен)."""
    west, south, east, north = bbox
    base = {"action": "query", "generator": "geosearch", "ggslimit": limit,
            "prop": "coordinates|description|pageprops", "ppprop": "wikibase_item",
            "coprimary": "primary"}
    host = "%s.wikipedia.org" % lang
    try:
        data = api(http, host, dict(base, ggsbbox="%.6f|%.6f|%.6f|%.6f" % (north, west, south, east)))
    except NetError:  # слишком большой прямоугольник — круг 10 км
        data = api(http, host, dict(base, ggscoord="%.6f|%.6f" % (center[1], center[0]),
                                    ggsradius=10000))
    return data.get("query", {}).get("pages", [])


def articles_from(pages, bbox, center, lang="ru", mode="nature"):
    pois = []
    for page in pages or []:
        coords = page.get("coordinates")
        description = page.get("description", "")
        if not coords or not is_sight(description):
            continue
        lon, lat = coords[0]["lon"], coords[0]["lat"]
        if not geo.in_bbox(lon, lat, bbox):
            continue
        found = guess_category(description, mode)
        category, kind, score = found or ("attraction", clip(description, 40) or "Статья Википедии", 5)
        title = page["title"]
        pois.append(Poi(
            uid="wiki:%s:%s" % (lang, title), name=re.sub(r"\s*\(.*\)$", "", title),
            lon=lon, lat=lat, category=category, kind=kind[:1].upper() + kind[1:], score=score + 5,
            wikipedia="%s:%s" % (lang, title),
            wikidata=(page.get("pageprops") or {}).get("wikibase_item", ""),
            description=description, url=page_url(lang, title),
            distance_km=geo.dist_m(center, (lon, lat)) / 1000.0,
        ))
    return pois


def merge_articles(pois, articles, radius_m=2000):
    """Статьи, уже нанесённые в OSM (по тегу или имени рядом), не дублируем."""
    result = list(pois)
    for article in articles:
        twin = None
        for poi in pois:
            if poi.wikipedia and poi.wikipedia.split(":", 1)[-1] == article.wikipedia.split(":", 1)[-1]:
                twin = poi
            elif poi.wikidata and poi.wikidata == article.wikidata:
                twin = poi
            elif norm_name(poi.name) == norm_name(article.name) and \
                    geo.dist_m(poi.point, article.point) < radius_m:
                twin = poi
            if twin:
                break
        if twin:
            twin.wikipedia = twin.wikipedia or article.wikipedia
            twin.wikidata = twin.wikidata or article.wikidata
            if twin.score < article.score:
                twin.score = max(twin.score, article.score)
        else:
            result.append(article)
    return result


def wikidata_links(http, qids, lang="ru"):
    """{Q123: ('ru:Название' или '', число статей во всех Википедиях)}."""
    result = {}
    qids = sorted({q for q in qids if q})
    for i in range(0, len(qids), 40):
        data = http.json("https://www.wikidata.org/w/api.php", {
            "action": "wbgetentities", "ids": "|".join(qids[i:i + 40]), "props": "sitelinks",
            "format": "json",
        }, ttl=30 * 86400)
        for qid, entity in (data.get("entities") or {}).items():
            links = entity.get("sitelinks") or {}
            title = ""
            for code in (lang, "en"):
                link = links.get(code + "wiki")
                if link:
                    title = "%s:%s" % (code, link["title"])
                    break
            wikis = sum(1 for key in links if key.endswith("wiki") and key != "commonswiki")
            result[qid] = (title, wikis)
    return result


def titles_from_wikidata(http, qids, lang="ru"):
    """{Q123: 'ru:Название'} — статьи для объектов, у которых в OSM есть только wikidata."""
    return {qid: title for qid, (title, _) in wikidata_links(http, qids, lang).items() if title}


def popularity(http, pois, lang="ru", days=30):
    """Poi.popularity — просмотров статьи в день за последний месяц.

    Если статьи на нужном языке нет, а есть Викиданные — грубая оценка по числу
    Википедий, где об объекте написано (5 просмотров в день за каждую).
    """
    no_title = [p for p in pois if p.wikidata and not p.wikipedia]
    links = wikidata_links(http, [p.wikidata for p in no_title], lang) if no_title else {}
    for poi in no_title:
        title, wikis = links.get(poi.wikidata, ("", 0))
        poi.wikipedia = title
        poi.popularity = 5.0 * wikis
    by_lang = {}
    for poi in pois:
        code, title = split_wiki_tag(poi.wikipedia, lang)
        if title:
            by_lang.setdefault(code, {}).setdefault(title, []).append(poi)
    for code, titles in by_lang.items():
        names = list(titles)
        for i in range(0, len(names), 50):
            chunk = names[i:i + 50]
            data = api(http, "%s.wikipedia.org" % code, {
                "action": "query", "titles": "|".join(chunk), "redirects": 1,
                "prop": "pageviews", "pvipdays": days}, ttl=3 * 86400)
            query = data.get("query", {})
            renamed = {}
            for item in query.get("normalized", []) + query.get("redirects", []):
                renamed[item["to"]] = renamed.get(item["from"], item["from"])
            for page in query.get("pages", []):
                original = page.get("title")
                while original in renamed and original not in titles:
                    original = renamed[original]
                views = [v for v in (page.get("pageviews") or {}).values() if v is not None]
                if not views:
                    continue
                per_day = sum(views) / float(len(views))
                if code != lang:
                    per_day *= 0.5   # статья только на другом языке — для русских туристов менее известен
                for poi in titles.get(original, []):
                    poi.popularity = max(poi.popularity, per_day)


def fill_details(http, pois, lang="ru", sentences_limit=700):
    """Описание, картинка и ссылка из Википедии для выбранных объектов."""
    need_titles = [p for p in pois if not p.wikipedia and p.wikidata]
    if need_titles:
        try:
            mapping = titles_from_wikidata(http, [p.wikidata for p in need_titles], lang)
        except NetError:
            mapping = {}
        for poi in need_titles:
            poi.wikipedia = mapping.get(poi.wikidata, "")
    by_lang = {}
    for poi in pois:
        code, title = split_wiki_tag(poi.wikipedia, lang)
        if title:
            by_lang.setdefault(code, {})[title] = poi
    for code, titles in by_lang.items():
        names = list(titles)
        for i in range(0, len(names), 20):
            chunk = names[i:i + 20]
            try:
                data = api(http, "%s.wikipedia.org" % code, {
                    "action": "query", "titles": "|".join(chunk), "redirects": 1,
                    "prop": "extracts|pageimages|info|description", "exintro": 1,
                    "explaintext": 1, "exlimit": 20, "piprop": "thumbnail",
                    "pithumbsize": 640, "inprop": "url",
                })
            except NetError:
                continue
            query = data.get("query", {})
            renamed = {}
            for item in query.get("normalized", []) + query.get("redirects", []):
                renamed[item["to"]] = renamed.get(item["from"], item["from"])
            for page in query.get("pages", []):
                original = page.get("title")
                while original in renamed and original not in titles:
                    original = renamed[original]
                poi = titles.get(original)
                if poi is None or page.get("missing"):
                    continue
                poi.extract = clip(page.get("extract", ""), sentences_limit)
                if not poi.description:
                    poi.description = page.get("description", "")
                poi.url = page.get("fullurl") or page_url(code, page["title"])
                poi.image_url = (page.get("thumbnail") or {}).get("source", "")


# --- Викигид ---

def parse_sections(extract):
    """Текст статьи с заголовками «== … ==» → {ключ: текст} для нужных разделов."""
    sections = {}
    current = None
    for line in (extract or "").split("\n"):
        heading = re.match(r"^(=+)\s*(.*?)\s*=+$", line.strip())
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).lower()
            if level <= 2:
                current = None
                for key, words in SECTION_KEYS:
                    if any(title.startswith(w) or w == title for w in words):
                        current = key
                        sections.setdefault(key, [])
                        break
            elif current:
                sections[current].append("▸ " + heading.group(2))
            continue
        if current and line.strip():
            sections[current].append(line.strip())
    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


def wikivoyage_page(http, title, lang="ru"):
    data = api(http, "%s.wikivoyage.org" % lang, {
        "action": "query", "titles": title, "redirects": 1, "prop": "extracts|info",
        "explaintext": 1, "exsectionformat": "wiki", "inprop": "url",
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    page = pages[0]
    return {"title": page["title"], "url": page.get("fullurl") or page_url(lang, page["title"], "wikivoyage"),
            "sections": parse_sections(page.get("extract", ""))}


def wikivoyage(http, names, lon, lat, lang="ru"):
    """Статья Викигида об объекте, ближайшем месте или регионе (первая с «Как добраться»)."""
    host = "%s.wikivoyage.org" % lang
    titles = []
    for name in names:
        if not name:
            continue
        try:
            found = api(http, host, {"action": "query", "list": "search", "srsearch": name,
                                     "srlimit": 3}).get("query", {}).get("search", [])
        except NetError:
            continue
        for hit in found:
            if norm_name(name) and norm_name(name) in norm_name(hit["title"]):
                titles.append(hit["title"])
                break
    try:
        near = api(http, host, {"action": "query", "list": "geosearch",
                                "gscoord": "%.6f|%.6f" % (lat, lon), "gsradius": 10000,
                                "gslimit": 3}).get("query", {}).get("geosearch", [])
        titles[1:1] = [hit["title"] for hit in near]
    except NetError:
        pass
    first = None
    seen = set()
    for title in titles:
        if title in seen:
            continue
        seen.add(title)
        try:
            page = wikivoyage_page(http, title, lang)
        except NetError:
            continue
        if not page:
            continue
        if "get_in" in page["sections"]:
            return page
        first = first or page
    return first or {}


# --- Викисклад ---

def commons_photos(http, lon, lat, radius_m=1000, limit=6, width=640):
    radius_m = int(min(max(radius_m, 10), 10000))
    data = api(http, "commons.wikimedia.org", {
        "action": "query", "generator": "geosearch", "ggsnamespace": 6,
        "ggscoord": "%.6f|%.6f" % (lat, lon), "ggsradius": radius_m, "ggslimit": limit * 3,
        "prop": "imageinfo|coordinates", "iiprop": "url|extmetadata", "iiurlwidth": width,
        "iiextmetadatafilter": "ImageDescription|Artist|LicenseShortName|DateTimeOriginal|ObjectName",
    })
    photos = []
    for page in data.get("query", {}).get("pages", []):
        title = page.get("title", "")
        if not re.search(r"\.(jpe?g|png|webp)$", title, re.I):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        coords = (page.get("coordinates") or [{}])[0]

        def value(key):
            return strip_html((meta.get(key) or {}).get("value", ""))

        photos.append(Photo(
            title=title.split(":", 1)[-1], thumb_url=info.get("thumburl") or info.get("url", ""),
            page_url=info.get("descriptionurl", ""), lon=coords.get("lon"), lat=coords.get("lat"),
            description=clip(value("ImageDescription") or value("ObjectName"), 300),
            author=clip(value("Artist"), 80), license=value("LicenseShortName"),
            date=value("DateTimeOriginal")[:10],
        ))
    photos.sort(key=lambda p: geo.dist_m((lon, lat), (p.lon, p.lat)) if p.lon is not None else 1e9)
    return [p for p in photos if p.thumb_url][:limit]
