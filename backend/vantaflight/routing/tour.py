"""Visiting order: the shortest way to fly through every stop.

This is the travelling-salesman problem with a fixed start (home), either
returning home at the end or finishing at the last stop.

* Up to ``EXACT_LIMIT`` stops we solve it **exactly** with Held-Karp dynamic
  programming (O(2^n * n^2)): the answer is provably the shortest order.
* Beyond that we build starting tours (nearest-neighbour plus a few
  seeded shuffles) and polish each with 2-opt (reverse a stretch) and Or-opt
  (move a run of 1-3 stops) until no single move helps, keeping the best.
  On tens of stops this lands within a few percent of optimal in well
  under a second.

All functions take a distance matrix, so the costs are the *safe* flying
distances around no-fly zones, not straight lines.
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

EXACT_LIMIT = 12
RESTARTS = 4

Matrix = list[list[float]]


@dataclass(frozen=True)
class Tour:
    order: list[int]  # indices of stops (1..n) in visiting order; 0 is home
    length_m: float
    method: str  # "exact" | "heuristic" | "fixed"


def tour_length(dist: Matrix, order: list[int], return_home: bool) -> float:
    path = [0, *order, *([0] if return_home else [])]
    return sum(dist[a][b] for a, b in zip(path, path[1:]))


def solve(dist: Matrix, return_home: bool = True) -> Tour:
    """Best visiting order for stops 1..n of ``dist`` starting at 0."""
    n = len(dist) - 1
    if n <= 0:
        return Tour([], 0.0, "exact")
    if n <= EXACT_LIMIT:
        order = held_karp(dist, return_home)
        method = "exact"
    else:
        rng = random.Random(n)  # deterministic: same input, same route
        starts = [nearest_neighbour(dist)]
        for _ in range(RESTARTS - 1):
            shuffled = list(range(1, n + 1))
            rng.shuffle(shuffled)
            starts.append(shuffled)
        order = min(
            (improve(dist, start, return_home) for start in starts),
            key=lambda o: tour_length(dist, o, return_home),
        )
        method = "heuristic"
    return Tour(order, tour_length(dist, order, return_home), method)


def held_karp(dist: Matrix, return_home: bool) -> list[int]:
    """Exact shortest order by dynamic programming over subsets."""
    n = len(dist) - 1
    full = (1 << n) - 1
    # best[mask][j]: shortest path from home through the stops in mask, ending at stop j+1.
    best = [[math.inf] * n for _ in range(1 << n)]
    parent = [[-1] * n for _ in range(1 << n)]
    for j in range(n):
        best[1 << j][j] = dist[0][j + 1]
    for mask in range(1, 1 << n):
        row = best[mask]
        for j in range(n):
            cost = row[j]
            if cost == math.inf or not (mask >> j) & 1:
                continue
            for k in range(n):
                if (mask >> k) & 1:
                    continue
                nxt = mask | (1 << k)
                c = cost + dist[j + 1][k + 1]
                if c < best[nxt][k]:
                    best[nxt][k] = c
                    parent[nxt][k] = j
    finish = [
        best[full][j] + (dist[j + 1][0] if return_home else 0.0) for j in range(n)
    ]
    last = min(range(n), key=finish.__getitem__)
    order, mask = [], full
    while last != -1:
        order.append(last + 1)
        prev = parent[mask][last]
        mask &= ~(1 << last)
        last = prev
    return list(reversed(order))


def nearest_neighbour(dist: Matrix) -> list[int]:
    n = len(dist) - 1
    unvisited = set(range(1, n + 1))
    order, here = [], 0
    while unvisited:
        here = min(unvisited, key=lambda k: (dist[here][k], k))
        order.append(here)
        unvisited.remove(here)
    return order


def improve(dist: Matrix, order: list[int], return_home: bool) -> list[int]:
    """Local search with 2-opt and Or-opt moves until no move shortens the tour."""
    order = list(order)
    best = tour_length(dist, order, return_home)
    improved = True
    while improved:
        improved = False
        # 2-opt: reverse order[i..j].
        for i, j in itertools.combinations(range(len(order)), 2):
            candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
            length = tour_length(dist, candidate, return_home)
            if length < best - 1e-9:
                order, best, improved = candidate, length, True
        # Or-opt: move a run of 1-3 stops elsewhere (optionally reversed).
        for size in (1, 2, 3):
            for i in range(len(order) - size + 1):
                run, rest = order[i:i + size], order[:i] + order[i + size:]
                for j in range(len(rest) + 1):
                    if j == i:
                        continue
                    for piece in (run, run[::-1]):
                        candidate = rest[:j] + piece + rest[j:]
                        length = tour_length(dist, candidate, return_home)
                        if length < best - 1e-9:
                            order, best, improved = candidate, length, True
                            break
                    else:
                        continue
                    break
    return order
