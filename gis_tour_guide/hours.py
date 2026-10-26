# -*- coding: utf-8 -*-
"""Часы работы из OpenStreetMap (opening_hours) на конкретную дату — частые формы записи.

Понимает: «24/7», «Mo-Fr 10:00-18:00; Sa,Su 11:00-17:00», «Tu-Su 10:00-18:00; Mo off»,
«May-Sep Mo-Su 09:00-20:00», «10:00-18:00» (каждый день), «off». Праздники (PH) и сложные
правила пропускает — тогда ответ «не знаю».
"""

import re

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTH_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
             "октября", "ноября", "декабря"]
TIME = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}(,\d{1,2}:\d{2}-\d{1,2}:\d{2})*$")


def expand(spec, names):
    """«Mo,We-Fr» → {0, 2, 3, 4}; None — не разобрали."""
    result = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in names or b not in names:
                return None
            i, j = names.index(a), names.index(b)
            result.update(range(i, j + 1) if i <= j else list(range(i, len(names))) + list(range(0, j + 1)))
        elif part in names:
            result.add(names.index(part))
        else:
            return None
    return result


def months_in(spec):
    """Месяцы правила (номера 1–12) или None — правило без месяцев."""
    found = expand(spec, MONTHS)
    return {m + 1 for m in found} if found is not None else None


def status(value, date):
    """("open", "10:00-18:00") / ("closed", "") / (None, "") — не разобрали.

    Правила OSM читаются по порядку; более позднее, подходящее под дату, побеждает.
    """
    value = (value or "").strip()
    if not value:
        return None, ""
    if value == "24/7":
        return "open", "круглосуточно"
    answer, known = None, 0
    for rule in [r.strip() for r in value.split(";") if r.strip()]:
        tokens = rule.split()
        if tokens and tokens[0].startswith("PH"):
            continue                                   # праздники — не знаем
        known += 1
        months = None
        if tokens and months_in(tokens[0]) is not None:
            months = months_in(tokens.pop(0))
        if months is not None and date.month not in months:
            continue
        days = set(range(7))
        if tokens and expand(tokens[0], DAYS) is not None:
            days = expand(tokens.pop(0), DAYS)
        if date.weekday() not in days:
            continue
        rest = " ".join(tokens)
        if rest in ("off", "closed"):
            answer = ("closed", "")
        elif TIME.match(rest.replace(" ", "")):
            answer = ("open", rest.replace(" ", ""))
        elif not rest and months is not None:
            answer = ("open", "")
        else:
            return None, ""                            # сложное правило
    if answer is None:
        # правила есть, но ни одно не подошло к дате — значит в этот день закрыто
        return ("closed", "") if known else (None, "")
    return answer


def describe(name, value, date):
    """Человеческая строка о часах работы в день поездки (или None)."""
    state, times = status(value, date)
    day = DAY_RU[date.weekday()]
    if state == "open":
        return "%s — в день поездки (%s) открыто%s." % (name, day, ", " + times.replace("-", "–") if times else "")
    if state == "closed":
        return "%s — в день поездки (%s, %d %s) ЗАКРЫТО по данным OpenStreetMap." % (
            name, day, date.day, MONTH_GEN[date.month - 1])
    return None
