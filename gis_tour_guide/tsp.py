# -*- coding: utf-8 -*-
"""Порядок обхода точек (задача коммивояжёра) с фиксированным стартом 0."""

import itertools

EXACT_LIMIT = 12   # до стольких точек — точное решение (Хелд — Карп)


def tour_cost(order, dist, loop):
    cost = sum(dist[a][b] for a, b in zip(order, order[1:]))
    if loop and len(order) > 1:
        cost += dist[order[-1]][order[0]]
    return cost


def held_karp(dist, loop):
    n = len(dist)
    if n <= 2:
        return list(range(n))
    best = {}
    for k in range(1, n):
        best[(1 << k, k)] = (dist[0][k], 0)
    for size in range(2, n):
        for subset in itertools.combinations(range(1, n), size):
            bits = 0
            for k in subset:
                bits |= 1 << k
            for k in subset:
                prev_bits = bits & ~(1 << k)
                best[(bits, k)] = min(
                    (best[(prev_bits, m)][0] + dist[m][k], m) for m in subset if m != k)
    full = (1 << n) - 2
    cost, last = min(
        (best[(full, k)][0] + (dist[k][0] if loop else 0), k) for k in range(1, n))
    order = []
    bits = full
    while last:
        order.append(last)
        bits, last = bits & ~(1 << last), best[(bits, last)][1]
    return [0] + order[::-1]


def nearest_neighbour(dist):
    n = len(dist)
    order, left = [0], set(range(1, n))
    while left:
        here = order[-1]
        nxt = min(left, key=lambda k: dist[here][k])
        order.append(nxt)
        left.remove(nxt)
    return order


def two_opt(order, dist, loop):
    improved = True
    best = tour_cost(order, dist, loop)
    while improved:
        improved = False
        for i in range(1, len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                cost = tour_cost(candidate, dist, loop)
                if cost < best - 1e-9:
                    order, best, improved = candidate, cost, True
    return order


def solve(dist, loop=True):
    """Порядок индексов, начиная с 0. loop — вернуться в старт."""
    if len(dist) <= EXACT_LIMIT:
        return held_karp(dist, loop)
    return two_opt(nearest_neighbour(dist), dist, loop)
