# -*- coding: utf-8 -*-
"""Сеть маркированных троп (route=hiking из OSM) и кратчайший путь по ней."""

import heapq

from . import geo

SNAP_M = 400          # дальше этого от тропы — тропа «не наша»


class TrailNetwork:

    def __init__(self, ways):
        self.points = {}           # узел → (lon, lat)
        self.edges = {}            # узел → [(сосед, метры, название тропы)]
        for way in ways or []:
            nodes, coords = way["nodes"], way["coords"]
            for node, coord in zip(nodes, coords):
                self.points[node] = coord
            for (a, ca), (b, cb) in zip(zip(nodes, coords), zip(nodes[1:], coords[1:])):
                length = geo.dist_m(ca, cb)
                self.edges.setdefault(a, []).append((b, length, way.get("name", "")))
                self.edges.setdefault(b, []).append((a, length, way.get("name", "")))

    def __bool__(self):
        return bool(self.edges)

    def nearest(self, point):
        best = None
        for node, coord in self.points.items():
            d = geo.dist_m(point, coord)
            if best is None or d < best[1]:
                best = (node, d)
        return best

    def path(self, a, b):
        """(точки, длина пути по тропе, привязка a, привязка b, названия троп) или None."""
        if not self.edges:
            return None
        start, end = self.nearest(a), self.nearest(b)
        if not start or not end or start[1] > SNAP_M or end[1] > SNAP_M or start[0] == end[0]:
            return None
        dist = {start[0]: 0.0}
        parent = {start[0]: (None, "")}
        queue = [(0.0, start[0])]
        seen = set()
        while queue:
            spent, node = heapq.heappop(queue)
            if node in seen:
                continue
            if node == end[0]:
                break
            seen.add(node)
            for nxt, length, name in self.edges.get(node, []):
                total = spent + length
                if total < dist.get(nxt, float("inf")):
                    dist[nxt] = total
                    parent[nxt] = (node, name)
                    heapq.heappush(queue, (total, nxt))
        if end[0] not in parent:
            return None
        nodes, names = [], []
        node = end[0]
        while node is not None:
            nodes.append(node)
            node, name = parent[node]
            if name and name not in names:
                names.append(name)
        nodes.reverse()
        return [self.points[n] for n in nodes], dist[end[0]], start[1], end[1], names[::-1]
