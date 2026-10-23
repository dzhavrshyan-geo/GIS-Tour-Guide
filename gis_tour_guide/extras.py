# -*- coding: utf-8 -*-
"""Погода (Open-Meteo) и живая природа рядом (iNaturalist)."""

from .model import Observation
from .net import NetError
from .text import clip, strip_html

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
INATURALIST = "https://api.inaturalist.org/v1"

ICONIC_RU = {
    "Mammalia": "Млекопитающие", "Aves": "Птицы", "Reptilia": "Рептилии",
    "Amphibia": "Земноводные", "Actinopterygii": "Рыбы", "Insecta": "Насекомые",
    "Arachnida": "Паукообразные", "Mollusca": "Моллюски", "Plantae": "Растения",
    "Fungi": "Грибы", "Chromista": "Водоросли", "Protozoa": "Простейшие",
}

# Животные, о которых стоит предупредить: латинский род → текст
DANGEROUS = {
    "Ursus": "медведи — шумите на ходу, храните еду герметично, держите при себе фальшфейер "
             "или перцовый спрей; не подходите к медведю и не убегайте от него",
    "Vipera": "гадюки — смотрите под ноги, высокая обувь, не лезьте руками под камни",
    "Gloydius": "щитомордники — смотрите под ноги, высокая обувь",
    "Ixodes": "иксодовые клещи — закрытая светлая одежда, репеллент, осмотры каждые 2 часа",
    "Dermacentor": "клещи — закрытая светлая одежда, репеллент, осмотры каждые 2 часа",
    "Canis": "волки / бродячие собаки — не оставляйте еду открытой",
    "Latrodectus": "каракурты — не садитесь на землю без коврика, вытряхивайте обувь",
}


def weather(http, points, date=None, days=7):
    """Прогноз в точках [(подпись, lon, lat, высота)] → [{label, elevation, days}]."""
    if not points:
        return []
    params = {
        "latitude": ",".join("%.4f" % p[2] for p in points),
        "longitude": ",".join("%.4f" % p[1] for p in points),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,weather_code",
        "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 16,
    }
    if all(p[3] is not None for p in points):
        params["elevation"] = ",".join("%.0f" % p[3] for p in points)
    data = http.json(OPEN_METEO, params, ttl=3 * 3600)
    if isinstance(data, dict):
        data = [data]
    result = []
    for point, item in zip(points, data):
        daily = item.get("daily") or {}
        dates = daily.get("time") or []
        rows = []
        for i, day in enumerate(dates):
            if date and day < date:
                continue

            def value(key):
                values = daily.get(key) or []
                return values[i] if i < len(values) else None

            rows.append({"date": day, "tmax": value("temperature_2m_max"),
                         "tmin": value("temperature_2m_min"),
                         "precip": value("precipitation_sum"),
                         "precip_prob": value("precipitation_probability_max"),
                         "wind": value("wind_speed_10m_max"), "code": value("weather_code")})
            if len(rows) >= days:
                break
        result.append({"label": point[0], "elevation": item.get("elevation", point[3]),
                       "days": rows})
    return result


WEATHER_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "сильная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
    67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
    77: "снежная крупа", 80: "ливень", 81: "ливни", 82: "сильные ливни", 85: "снегопад",
    86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}


def bbox_params(bbox):
    west, south, east, north = bbox
    return {"swlat": "%.5f" % south, "swlng": "%.5f" % west,
            "nelat": "%.5f" % north, "nelng": "%.5f" % east}


def species(http, bbox, limit=24, lang="ru", iconic=None):
    """Виды, которых чаще всего видели в прямоугольнике (подтверждённые наблюдения)."""
    params = dict(bbox_params(bbox), quality_grade="research", per_page=limit, locale=lang)
    if iconic:
        params["iconic_taxa"] = iconic
    data = http.json(INATURALIST + "/observations/species_counts", params, ttl=14 * 86400)
    result = []
    for item in data.get("results", []):
        taxon = item.get("taxon") or {}
        photo = taxon.get("default_photo") or {}
        latin = taxon.get("name", "")
        result.append(Observation(
            name=taxon.get("preferred_common_name") or latin, latin=latin,
            group=ICONIC_RU.get(taxon.get("iconic_taxon_name"), taxon.get("iconic_taxon_name") or ""),
            count=item.get("count", 0), photo_url=photo.get("medium_url") or photo.get("url", ""),
            photo_credit=photo.get("attribution", ""),
            url="https://www.inaturalist.org/taxa/%s" % taxon.get("id", ""),
        ))
    return result


def observer_notes(http, bbox, limit=40, lang="ru"):
    """Заметки наблюдателей с фото: [(вид, текст, автор, дата, ссылка, фото)]."""
    params = dict(bbox_params(bbox), photos="true", per_page=limit, order_by="votes",
                  locale=lang)
    data = http.json(INATURALIST + "/observations", params, ttl=14 * 86400)
    notes = []
    for item in data.get("results", []):
        text = clip(strip_html(item.get("description") or ""), 400)
        if len(text) < 15:
            continue
        taxon = item.get("taxon") or {}
        photos = item.get("photos") or [{}]
        photo = (photos[0].get("url") or "").replace("/square.", "/medium.")
        notes.append((taxon.get("preferred_common_name") or item.get("species_guess") or "",
                      text, (item.get("user") or {}).get("login", ""), item.get("observed_on") or "",
                      item.get("uri") or "", photo))
    return notes


def danger_notes(observations):
    """Предупреждения по наблюдениям опасных животных."""
    result = []
    for genus, text in DANGEROUS.items():
        seen = [o for o in observations if o.latin.split(" ")[0] == genus]
        if seen:
            count = sum(o.count for o in seen)
            names = ", ".join(sorted({o.name for o in seen}))
            result.append("Рядом видели: %s (%d набл. в iNaturalist) — %s." % (names, count, text))
    return result


def safe(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except NetError:
        return None
