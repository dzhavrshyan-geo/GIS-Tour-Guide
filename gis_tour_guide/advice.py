# -*- coding: utf-8 -*-
"""Что надеть и что взять: правила по длине маршрута, высоте, погоде, сезону и объектам."""

import datetime
import math

from . import extras, geo
from .text import fmt_hours, fmt_km, fmt_num

STOP_MINUTES = 25          # осмотр одного объекта
SHOP_FAR_KM = 15           # дальше — «магазинов рядом нет»
COLD_COUNTRIES_TICKS = ("ru", "by", "kz", "ua", "fi", "ee", "lv", "lt", "se", "no", "pl")
BEAR_REGIONS = ("камчат", "магадан", "чукот", "сахалин", "хабаров", "приморск", "якут",
                "саха", "красноярск", "иркутск", "бурят", "забайкал", "мурманск", "карел",
                "коми", "алтай", "тыва", "архангельск", "ханты", "ямало", "аляск", "alaska")


def trip_weather(tour):
    """(tmin, tmax, осадки мм, вероятность %, ветер м/с) в день поездки на самой высокой точке."""
    if not tour.weather:
        return None
    point = max(tour.weather, key=lambda w: w.get("elevation") or 0)
    days = point.get("days") or []
    day = next((d for d in days if d["date"] == tour.date), days[0] if days else None)
    if not day or day.get("tmin") is None:
        return None
    return day


def seasonal_temperature(lat, elevation, month):
    """Грубая оценка дневной температуры, если прогноза на дату нет."""
    summer = month in (6, 7, 8) if lat >= 0 else month in (12, 1, 2)
    winter = month in (12, 1, 2) if lat >= 0 else month in (6, 7, 8)
    base = 30 - abs(lat) * 0.45
    if summer:
        base += 6
    elif winter:
        base -= 14
    return base - (elevation or 0) * 0.0065


def water_litres(hours, temp):
    per_hour = 0.4 if temp < 15 else 0.6 if temp < 25 else 0.85
    litres = max(1.0, hours * per_hour)
    return math.ceil(litres * 2) / 2.0


def build(tour):
    """Список разделов [(заголовок, [пункты])] и предупреждения в tour.warnings."""
    t = tour.totals
    date = datetime.date.fromisoformat(tour.date) if tour.date else datetime.date.today()
    month = date.month
    hours_walk = t.get("hours_walk", 0)
    hours_total = hours_walk + t.get("hours_stops", 0)
    max_h = t.get("max_h") if t.get("max_h") is not None else tour.place.elevation
    categories = {s.category for s in tour.stops if s.order or s.is_main}
    people = max(1, tour.people)
    region = (tour.place.region + " " + tour.region_name).lower()
    russia = tour.place.country_code == "ru" or "росси" in tour.place.country.lower()

    day = trip_weather(tour)
    if day:
        tmin, tmax = day["tmin"], day["tmax"]
        rain = (day.get("precip") or 0) >= 1 or (day.get("precip_prob") or 0) >= 50
        wind = day.get("wind") or 0
    else:
        tmax = seasonal_temperature(tour.place.lat, max_h, month)
        tmin = tmax - 9
        rain, wind = True, 0

    plan, clothes, gear, food, safety = [], [], [], [], []

    # --- План дня ---
    if tour.drive and tour.drive.ok:
        drive_h = tour.drive.duration_s / 3600.0
        plan.append("Дорога: %s, около %s в одну сторону." % (fmt_km(tour.drive.distance_m),
                                                              fmt_hours(drive_h)))
    else:
        drive_h = 0
    plan.append("Пешая часть: %s, ходового времени ~%s, с осмотром объектов ~%s." % (
        fmt_km(t.get("walk_m", 0)), fmt_hours(hours_walk), fmt_hours(hours_total)))
    if t.get("ascent"):
        plan.append("Набор высоты ~%d м, сброс ~%d м%s." % (
            t["ascent"], t.get("descent", 0),
            ", высшая точка %d м" % max_h if max_h is not None else ""))
    daylight = geo.daylight_hours(tour.place.lat, date.timetuple().tm_yday)
    need = hours_total + 2 * drive_h
    plan.append("Светлое время %s: ~%s." % (date.strftime("%d.%m"), fmt_hours(daylight)))
    if hours_total > 9:
        plan.append("Пешком больше 9 часов — лучше разбить на 2 дня с ночёвкой.")
        camps = [s.name for s in tour.candidates if s.category in ("camp", "hut")][:3]
        if camps:
            plan.append("Места для ночёвки рядом: " + ", ".join(camps) + ".")
        gear.append("Палатка, спальник (комфорт ≤ %d °C), коврик, горелка с газом." % (
            round(tmin) - 3))
    elif need > daylight - 1:
        plan.append("Дорога и маршрут занимают почти весь световой день — выезжайте "
                    "затемно или заночуйте поблизости.")

    # --- Одежда ---
    feel = tmin - (3 if wind >= 8 else 0)
    if feel < -5:
        clothes += ["Термобельё, тёплый флис, пуховка, ветрозащитная куртка и штаны.",
                    "Тёплая шапка, балаклава, две пары перчаток (тонкие + варежки)."]
    elif feel < 5:
        clothes += ["Термобельё, флис, утеплённая куртка, мембранная куртка от ветра.",
                    "Шапка, перчатки, бафф."]
    elif feel < 14:
        clothes += ["Футболка из синтетики, флиска, ветровка или мембрана.",
                    "Лёгкая шапка и перчатки — на вершине и на ветру холоднее."]
    else:
        clothes += ["Лёгкая синтетическая одежда с длинным рукавом (солнце, клещи, кусты).",
                    "Панама или кепка, флиска на вечер."]
    clothes.append("Сменная пара носков, запасная футболка в гермопакете.")
    if rain:
        clothes.append("Дождевик или мембранная куртка, гермочехол для рюкзака.")
    offtrail = t.get("offtrail_m", 0)
    if offtrail > 300 or (t.get("ascent") or 0) > 300 or "volcano" in categories:
        clothes.append("Треккинговые ботинки с жёсткой подошвой и защитой голеностопа.")
    else:
        clothes.append("Удобные треккинговые кроссовки или ботинки.")
    if "volcano" in categories or "geological" in categories:
        clothes.append("Гамаши (гетры) — от шлака, пепла и песка в ботинках.")
    if max_h is not None and (max_h > 1500 or abs(tour.place.lat) > 55) and month in (4, 5, 6, 10):
        clothes.append("Возможны снежники — гамаши, непромокаемая обувь.")
    if max_h is not None and max_h > 1200 or tmax > 20:
        clothes.append("Солнцезащитные очки и крем SPF 30+ (на высоте и на снегу солнце сильнее).")

    # --- Снаряжение ---
    gear += ["Рюкзак 25–35 л, аптечка (пластыри от мозолей, бинт, обезболивающее, антигистаминное).",
             "Заряженный телефон с офлайн-картой и треком (GPX в папке экскурсии), пауэрбанк.",
             "Налобный фонарик, свисток, нож, спички/зажигалка, спасательное одеяло."]
    if (t.get("ascent") or 0) > 400 or offtrail > 1000:
        gear.append("Треккинговые палки — на спусках по осыпям и шлаку берегут колени.")
    if "cave" in categories:
        gear.append("Для пещеры: каска, два фонаря и запасные батарейки, тёплая куртка "
                    "(внутри обычно +2…+8 °C), перчатки. Не заходите вглубь без опыта и проводника.")
    if "hot_spring" in categories:
        gear.append("Купальник, полотенце, сланцы. Проверяйте температуру воды рукой, "
                    "не купайтесь в источниках с кипящей водой и в одиночку.")
    if "glacier" in categories:
        gear.append("На ледник — только с кошками, верёвкой и опытом: трещины под снегом.")
    if "waterfall" in categories:
        gear.append("У водопадов скользкие камни — обувь с цепкой подошвой, гермопакет для телефона.")
    if "volcano" in categories or "fumarole" in categories or "geyser" in categories:
        safety.append("У фумарол и кратеров — сернистые газы: не спускайтесь в низины кратера, "
                      "держитесь наветренной стороны, при запахе тухлых яиц и головной боли — уходите.")
        safety.append("Перед выходом проверьте сводки вулканической активности (KVERT/ИВиС для "
                      "Камчатки, местное МЧС).")

    # --- Еда и вода ---
    shop = (tour.services or {}).get("shop")
    shop_km = shop["km"] if shop else None
    water = water_litres(hours_total, tmax)
    springs = [s.name for s in tour.stops if s.category in ("spring",)]
    food.append("Вода: ~%s л на человека, на группу из %d — %s л." % (
        fmt_num(water, 1), people, fmt_num(water * people, 1)))
    if springs:
        food.append("По пути есть родники (%s) — можно пополнить воду, но лучше через фильтр "
                    "или кипятить." % ", ".join(springs))
    if "volcano" in categories:
        food.append("На вулканах вода в ручьях бывает кислой и минерализованной — не пейте её.")
    if hours_total > 4:
        food.append("Еда на маршрут: обед (бутерброды, термос с горячим чаем) + перекусы "
                    "каждые 1,5–2 часа: орехи, сухофрукты, шоколад, батончики (~%d г на человека)."
                    % (int(hours_total / 2 + 1) * 80))
    else:
        food.append("Перекус: орехи, батончики, шоколад, термос с чаем.")
    if shop_km is None or shop_km > SHOP_FAR_KM:
        where = ("ближайший — %s, %s" % (shop["name"] or "магазин", fmt_km(shop_km * 1000))
                 if shop else "в радиусе 80 км не найдено")
        food.append("Магазинов поблизости нет (%s) — купите всё заранее и возьмите аварийный "
                    "запас еды на сутки (~600 г на человека) и +1 л воды." % where)
    else:
        food.append("Магазин: %s в %s от начала маршрута." % (shop["name"] or "продукты",
                                                              fmt_km(shop_km * 1000)))
    fuel = (tour.services or {}).get("fuel")
    if tour.drive and tour.drive.ok:
        if not fuel or fuel["km"] > 50:
            food.append("Заправок у конца дороги нет%s — заправьтесь полностью по пути, "
                        "в оба конца нужно %s." % (
                            " ближе %s" % fmt_km(fuel["km"] * 1000) if fuel else "",
                            fmt_km(2 * tour.drive.distance_m)))

    # --- Безопасность ---
    for name, kind in tour.protected:
        safety.append("Объект на ООПТ «%s» (%s) — может понадобиться пропуск или регистрация: "
                      "уточните у администрации заранее." % (name, kind))
    dangers = extras.danger_notes(tour.observations)
    safety += dangers
    if not any("медвед" in d for d in dangers) and any(r in region for r in BEAR_REGIONS):
        safety.append("В регионе водятся медведи — шумите на ходу, еду храните герметично, "
                      "возьмите фальшфейер.")
    if tour.place.country_code in COLD_COUNTRIES_TICKS and month in (4, 5, 6, 7, 8) and \
            not any("клещ" in d for d in dangers):
        safety.append("Сезон клещей — закрытая светлая одежда, репеллент, осмотры. "
                      "Проверьте, нужна ли прививка от энцефалита в этом регионе.")
    if russia and (offtrail > 500 or hours_total > 8 or (max_h or 0) > 1500):
        safety.append("Зарегистрируйте группу в МЧС (онлайн на сайте ГУ МЧС региона) "
                      "и оставьте маршрут близким.")
    else:
        safety.append("Оставьте маршрут и время возвращения близким — связи может не быть.")
    if wind >= 12:
        safety.append("Сильный ветер (%s м/с) — на гребнях и вершинах будет тяжело, "
                      "подумайте о переносе." % fmt_num(wind, 0))
    if day and (day.get("code") or 0) >= 95:
        safety.append("Прогноз гроз — не находитесь на вершинах и гребнях после обеда.")
    if offtrail > 0:
        safety.append("Часть пути (%s) — без троп, по рельефу: идите только в хорошую "
                      "видимость, сверяйтесь с треком." % fmt_km(offtrail))

    sections = [("План дня", plan), ("Одежда", clothes), ("Снаряжение", gear),
                ("Еда и вода", food), ("Безопасность", safety)]
    tour.advice = [(title, items) for title, items in sections if items]
    tour.warnings = dangers + [s for s in safety if s.startswith("Объект на ООПТ")]
    if not day:
        tour.advice[1][1].insert(0, "Прогноза на дату поездки нет — одежда подобрана по сезону "
                                    "(≈ %d…%d °C наверху); проверьте прогноз ближе к выезду."
                                 % (round(tmin), round(tmax)))
    return tour.advice
