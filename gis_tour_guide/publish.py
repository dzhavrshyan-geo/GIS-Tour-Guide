# -*- coding: utf-8 -*-
"""Публикация отчёта ссылкой: GitHub Pages в вашем репозитории (бесплатно, без своего сервера).

Нужно один раз: публичный репозиторий (например, «tours») и токен GitHub с правами
«Contents: Read and write» и «Pages: Read and write» на этот репозиторий.
Отчёт кладётся в tours/<объект_дата>/index.html, ссылка — https://<владелец>.github.io/<репозиторий>/…
"""

import base64
import json
import urllib.parse

from .net import NetError

API = "https://api.github.com"
QR_API = "https://api.qrserver.com/v1/create-qr-code/"
TOKEN_KEY = "gis_tour_guide/github_token"
REPO_KEY = "gis_tour_guide/github_repo"


def headers(token):
    return {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}


def explain(error):
    status = getattr(error, "status", None)
    if status == 401:
        return "GitHub не принял токен — проверьте его или создайте новый."
    if status in (403, 404):
        return ("Нет доступа к репозиторию: проверьте его имя (владелец/репозиторий) и что у токена "
                "есть права Contents и Pages на запись.")
    if status == 422:
        return "GitHub отклонил файл (возможно, такой путь уже есть)."
    return "GitHub недоступен: %s" % error


def pages_url(http, token, repo, branch):
    """Адрес сайта GitHub Pages; если Pages выключены — пробуем включить."""
    try:
        return http.json("%s/repos/%s/pages" % (API, repo), ttl=0, tries=1,
                         headers=headers(token))["html_url"]
    except NetError as error:
        if getattr(error, "status", None) != 404:
            raise
    body = json.dumps({"source": {"branch": branch, "path": "/"}}).encode()
    try:
        return http.json("%s/repos/%s/pages" % (API, repo), data=body, ttl=0, tries=1,
                         headers=headers(token))["html_url"]
    except (NetError, KeyError):
        owner, name = repo.split("/", 1)
        return ("https://%s.github.io/" % owner.lower()) if name.lower() == "%s.github.io" % owner.lower() \
            else "https://%s.github.io/%s/" % (owner.lower(), name)


def publish(http, token, repo, folder_name, html):
    """Выложить отчёт; → (адрес страницы, предупреждение или "")."""
    repo = repo.strip().strip("/")
    if repo.count("/") != 1:
        raise NetError("Укажите репозиторий в виде «владелец/репозиторий».")
    try:
        info = http.json("%s/repos/%s" % (API, repo), ttl=0, tries=1, headers=headers(token))
        branch = info.get("default_branch") or "main"
        path = "tours/%s/index.html" % folder_name
        body = json.dumps({"message": "ГИС-экскурсовод: %s" % folder_name, "branch": branch,
                           "content": base64.b64encode(html.encode("utf-8")).decode("ascii")}).encode()
        http.json("%s/repos/%s/contents/%s" % (API, repo, urllib.parse.quote(path)), data=body, ttl=0,
                  tries=1, headers=headers(token), method="PUT", timeout=120)
        site = pages_url(http, token, repo, branch)
    except NetError as error:
        raise NetError(explain(error))
    note = "" if info.get("visibility", "public") == "public" else \
        "Репозиторий закрытый: GitHub Pages для закрытых репозиториев доступны только на платных тарифах."
    return site.rstrip("/") + "/tours/%s/" % urllib.parse.quote(folder_name), note


def qr_png(http, url, size=240):
    """QR-код ссылки (PNG) — чтобы открыть отчёт на телефоне камерой."""
    return http.get(QR_API, {"size": "%dx%d" % (size, size), "data": url, "margin": 8}, ttl=0, tries=1)
