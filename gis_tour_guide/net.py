# -*- coding: utf-8 -*-
"""HTTP с дисковым кэшем, паузами между запросами к одному серверу и отменой.

Открытые сервисы (Nominatim, Overpass, Википедия) просят представляться и не
частить, поэтому все запросы идут через один объект Http.
"""

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = ("GIS-Tour-Guide/0.1 (QGIS plugin; "
              "https://github.com/dzhavrshyan-geo/GIS-Tour-Guide)")

# Минимальная пауза между запросами к серверу, с
HOST_INTERVAL = {
    "nominatim.openstreetmap.org": 1.1,
    "overpass-api.de": 1.0,
    "routing.openstreetmap.de": 0.3,
    "api.inaturalist.org": 1.0,
}


class NetError(Exception):
    pass


class Cancelled(Exception):
    pass


def urllib_transport(url, data, headers, timeout, method=None):
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b""


def qgis_transport(url, data, headers, timeout, method=None):
    """Через сетевой менеджер QGIS (его настройки прокси и сертификатов)."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    request = QNetworkRequest(QUrl(url))
    for key, value in headers.items():
        request.setRawHeader(key.encode(), value.encode())
    blocking = QgsBlockingNetworkRequest()
    if method == "PUT":
        error = blocking.put(request, data)
    elif data is not None:
        error = blocking.post(request, data, True)
    else:
        error = blocking.get(request, True)
    reply = blocking.reply()
    status_attr = getattr(QNetworkRequest, "HttpStatusCodeAttribute", None) or \
        QNetworkRequest.Attribute.HttpStatusCodeAttribute
    no_error = getattr(QgsBlockingNetworkRequest, "NoError", None)
    if no_error is None:
        no_error = QgsBlockingNetworkRequest.ErrorCode.NoError
    status = reply.attribute(status_attr) or 0
    if error != no_error and not status:
        raise NetError(blocking.errorMessage())
    return int(status), bytes(reply.content())


class Http:

    def __init__(self, cache_dir=None, transport=None, is_cancelled=None, timeout=60):
        self.cache_dir = cache_dir
        self.transports = [transport] if transport else [urllib_transport, qgis_transport]
        self.is_cancelled = is_cancelled or (lambda: False)
        self.timeout = timeout
        self.last_call = {}
        self.lock = threading.Lock()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def check(self):
        if self.is_cancelled():
            raise Cancelled()

    @staticmethod
    def build_url(url, params=None):
        if not params:
            return url
        query = urllib.parse.urlencode(params, doseq=True)
        return url + ("&" if "?" in url else "?") + query

    def cache_path(self, url, data):
        if not self.cache_dir:
            return None
        key = hashlib.sha1(url.encode() + b"\0" + (data or b"")).hexdigest()
        return os.path.join(self.cache_dir, key[:2], key)

    def wait_turn(self, url):
        host = urllib.parse.urlsplit(url).hostname or ""
        interval = HOST_INTERVAL.get(host, 0.1)
        with self.lock:
            delay = self.last_call.get(host, 0) + interval - time.monotonic()
            self.last_call[host] = time.monotonic() + max(delay, 0)
        while delay > 0:
            self.check()
            time.sleep(min(delay, 0.2))
            delay -= 0.2

    def get(self, url, params=None, data=None, ttl=7 * 86400, headers=None, tries=2, timeout=None,
            method=None):
        """Байты ответа. data (dict или bytes) — POST. ttl — срок кэша, с (0 — без кэша)."""
        self.check()
        url = self.build_url(url, params)
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode()
        path = self.cache_path(url, data) if ttl else None
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            with open(path, "rb") as handle:
                return handle.read()
        all_headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.7"}
        if data is not None:
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        all_headers.update(headers or {})
        last_error = None
        for attempt in range(tries):
            for transport in list(self.transports):
                self.wait_turn(url)
                try:
                    if method:
                        status, body = transport(url, data, all_headers, timeout or self.timeout, method)
                    else:
                        status, body = transport(url, data, all_headers, timeout or self.timeout)
                except NetError as error:
                    last_error = error
                    continue
                except Exception as error:  # SSL, прокси — пробуем другой способ; тайм-аут — нет
                    last_error = NetError("%s: %s" % (type(error).__name__, error))
                    if is_timeout(error):
                        break
                    continue
                if transport is not self.transports[0]:
                    # этот способ работает (например, SSL через QGIS) — дальше начинаем с него
                    self.transports.remove(transport)
                    self.transports.insert(0, transport)
                if status in (200, 201):
                    if path:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        with open(path, "wb") as handle:
                            handle.write(body)
                    return body
                last_error = NetError("HTTP %s: %s" % (status, url[:120]))
                if status in (400, 401, 403, 404, 409, 422):
                    last_error.status = status
                    last_error.body = body
                    raise last_error
                break  # 429/5xx — подождать и повторить
            self.check()
            if attempt + 1 < tries:
                time.sleep(1.5 * (attempt + 1))
        raise last_error or NetError(url)

    def json(self, url, params=None, data=None, ttl=7 * 86400, headers=None, tries=2, timeout=None,
             method=None):
        body = self.get(url, params, data, ttl, headers, tries, timeout, method)
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as error:
            raise NetError("Не JSON от %s: %s" % (url[:80], error))


def is_timeout(error):
    reason = getattr(error, "reason", error)
    return isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(error).lower()
