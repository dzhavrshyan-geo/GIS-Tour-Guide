# -*- coding: utf-8 -*-
"""Мелочи для текста: очистка HTML, обрезка, форматирование чисел по-русски."""

import html
import re

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")


def strip_html(value):
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", value)
    value = html.unescape(_TAG.sub("", value))
    lines = [_SPACES.sub(" ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line)


def clip(value, limit):
    """Обрезать до limit символов по границе предложения (или слова)."""
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    if dot > limit * 0.5:
        return cut[:dot + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(",;:—- ") + "…"


def esc(value):
    return html.escape(str(value if value is not None else ""))


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def fmt_km(meters):
    km = (meters or 0) / 1000.0
    if km < 10:
        return ("%.1f км" % km).replace(".", ",")
    return "%d км" % round(km)


def fmt_hours(hours):
    minutes = int(round((hours or 0) * 60))
    if minutes < 60:
        return "%d мин" % max(minutes, 1)
    h, m = divmod(minutes, 60)
    if m == 0:
        return "%d ч" % h
    return "%d ч %02d мин" % (h, m)


def fmt_num(value, digits=1):
    return ("%.*f" % (digits, value)).replace(".", ",")


def slug(value, limit=40):
    value = re.sub(r"[^\w\-]+", "_", (value or "").strip(), flags=re.UNICODE).strip("_")
    return (value[:limit] or "tour").lower()


def norm_name(value):
    """Имя для сравнения: без регистра, ё→е, без типовых слов и кавычек."""
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[«»\"'()\[\].,]", " ", value)
    words = [w for w in value.split() if w not in TYPE_WORDS]
    return " ".join(words)


# Слова, которыми в запросе обозначают тип объекта (в имени OSM их часто нет)
TYPE_WORDS = {
    "вулкан": "volcano", "сопка": "volcano", "гора": "peak", "пик": "peak", "вершина": "peak",
    "пещера": "cave", "грот": "cave", "водопад": "waterfall", "озеро": "lake",
    "источник": "spring", "источники": "spring", "родник": "spring", "гейзер": "geyser",
    "ледник": "glacier", "скала": "rock", "скалы": "cliff", "перевал": "saddle",
    "каньон": "valley", "ущелье": "valley", "долина": "valley", "мыс": "cape",
    "volcano": "volcano", "mount": "peak", "mt": "peak", "cave": "cave", "lake": "lake",
    "waterfall": "waterfall", "falls": "waterfall",
}
