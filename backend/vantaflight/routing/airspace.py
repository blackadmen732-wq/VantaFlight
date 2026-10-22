"""Airspace: where the aircraft may fly, and the shortest safe way through it.

Shortest paths use a *visibility graph*: the only places an optimal path
around polygonal obstacles can bend are the obstacles' convex corners, so we
connect every pair of corner nodes (offset by the safety margin) that can see
each other, then run A* from start to goal. For polygonal zones this gives
the true shortest path at the requested clearance, not a grid approximation,
and it needs no tuning.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from ..config import AIRSPACE_MARGIN_M
from ..safety import Geofence
from .geometry import Point, Zone, circle_polygon


class RouteError(ValueError):
    """No safe route exists (e.g. the goal is inside a no-fly zone)."""


@dataclass(frozen=True)
class SafePath:
    points: list[Point]
    length_m: float

    @property
    def detours(self) -> list[Point]:
        """Intermediate points added to get around zones."""
        return self.points[1:-1]


class Airspace:
    def __init__(
        self,
        zones: list[Zone] | None = None,
        margin_m: float = AIRSPACE_MARGIN_M,
        geofence: Geofence | None = None,
    ) -> None:
        if margin_m < 0:
            raise ValueError("margin must not be negative")
        self.zones = list(zones or [])
        self.margin_m = margin_m
        self.geofence = geofence or Geofence()
        self._nodes: list[Point] | None = None
        self._edges: list[list[tuple[int, float]]] | None = None

    # -- queries ----------------------------------------------------------
    def zone_at(self, p: Point, margin: float | None = None) -> Zone | None:
        """The zone ``p`` is inside of (or within the margin of), if any."""
        m = self.margin_m if margin is None else margin
        for zone in self.zones:
            if zone.contains(p, m):
                return zone
        return None

    def blocking_zone(self, a: Point, b: Point, margin: float | None = None) -> Zone | None:
        """The first zone a straight flight from a to b would violate.

        Planning uses the full safety margin. The command gate passes
        ``margin=0`` ("never enter a zone") so ordinary position error while
        following a planned route can't trip it.
        """
        m = self.margin_m if margin is None else margin
        for zone in self.zones:
            if zone.blocks(a, b, m):
                return zone
        return None

    def with_margin(self, margin_m: float) -> "Airspace":
        return Airspace(self.zones, margin_m, self.geofence)

    def shortest_path(self, a: Point, b: Point) -> SafePath:
        return self._search(a, b, self._visible_nodes(a), self._visible_nodes(b))

    def distance_matrix(self, points: list[Point]) -> tuple[list[list[float]], dict[tuple[int, int], SafePath]]:
        """Pairwise safe distances (and the paths behind them) for ``points``."""
        for i, p in enumerate(points):
            self._require_free(p, f"point {i}")
        visible = [self._visible_nodes(p) for p in points]
        n = len(points)
        dist = [[0.0] * n for _ in range(n)]
        paths: dict[tuple[int, int], SafePath] = {}
        for i in range(n):
            for j in range(i + 1, n):
                path = self._search(points[i], points[j], visible[i], visible[j])
                dist[i][j] = dist[j][i] = path.length_m
                paths[(i, j)] = path
                paths[(j, i)] = SafePath(list(reversed(path.points)), path.length_m)
        return dist, paths

    # -- visibility graph -------------------------------------------------
    def _graph(self) -> tuple[list[Point], list[list[tuple[int, float]]]]:
        if self._nodes is None or self._edges is None:
            nodes = [
                node
                for zone in self.zones
                for node in zone.detour_nodes(self.margin_m)
                if self.zone_at(node) is None and self._in_fence(node)
            ]
            edges: list[list[tuple[int, float]]] = [[] for _ in nodes]
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    if self.blocking_zone(nodes[i], nodes[j]) is None:
                        d = math.dist(nodes[i], nodes[j])
                        edges[i].append((j, d))
                        edges[j].append((i, d))
            self._nodes, self._edges = nodes, edges
        return self._nodes, self._edges

    def _visible_nodes(self, p: Point) -> list[tuple[int, float]]:
        nodes, _ = self._graph()
        return [
            (i, math.dist(p, node))
            for i, node in enumerate(nodes)
            if self.blocking_zone(p, node) is None
        ]

    def _search(
        self, a: Point, b: Point,
        from_a: list[tuple[int, float]], to_b: list[tuple[int, float]],
    ) -> SafePath:
        self._require_free(a, "start")
        self._require_free(b, "destination")
        if self.blocking_zone(a, b) is None:
            return SafePath([a, b], math.dist(a, b))

        nodes, edges = self._graph()
        goal_cost = dict(to_b)  # node -> distance to b, for nodes that see b
        start, goal = len(nodes), len(nodes) + 1
        best = {start: 0.0}
        came_from: dict[int, int] = {}
        frontier = [(math.dist(a, b), 0.0, start)]
        while frontier:
            _, cost, current = heapq.heappop(frontier)
            if current == goal:
                return self._rebuild(came_from, goal, start, a, b, nodes, cost)
            if cost > best.get(current, math.inf):
                continue
            if current == start:
                neighbours = from_a
            else:
                neighbours = list(edges[current])
                if current in goal_cost:
                    neighbours.append((goal, goal_cost[current]))
            for nxt, step in neighbours:
                new_cost = cost + step
                if new_cost < best.get(nxt, math.inf):
                    best[nxt] = new_cost
                    came_from[nxt] = current
                    h = 0.0 if nxt == goal else math.dist(nodes[nxt], b)
                    heapq.heappush(frontier, (new_cost + h, new_cost, nxt))
        raise RouteError(f"no safe route from {_fmt(a)} to {_fmt(b)} around the no-fly zones")

    @staticmethod
    def _rebuild(came_from, goal, start, a, b, nodes, cost) -> SafePath:
        chain = []
        node = came_from[goal]
        while node != start:
            chain.append(nodes[node])
            node = came_from[node]
        return SafePath([a, *reversed(chain), b], cost)

    def _require_free(self, p: Point, what: str) -> None:
        zone = self.zone_at(p)
        if zone is not None:
            raise RouteError(
                f"{what} {_fmt(p)} is inside no-fly zone '{zone.name}' "
                f"(or within its {self.margin_m:g} m safety margin)"
            )

    def _in_fence(self, p: Point) -> bool:
        return math.hypot(*p) <= self.geofence.radius_m

    # -- (de)serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "margin_m": self.margin_m,
            "zones": [
                {"id": z.zone_id, "name": z.name, "vertices": [list(v) for v in z.vertices]}
                for z in self.zones
            ],
        }


def zone_from_spec(spec: dict, index: int = 0) -> Zone:
    """Build a zone from ``{"name", "vertices": [[x, y], ...]}`` or
    ``{"name", "center": [x, y], "radius": r}``."""
    name = str(spec.get("name") or f"Zone {index + 1}")
    zone_id = str(spec.get("id") or f"zone-{index + 1}")
    if "radius" in spec:
        radius = float(spec["radius"])
        if radius <= 0:
            raise ValueError(f"zone '{name}' radius must be positive")
        cx, cy = spec.get("center", (0.0, 0.0))
        return Zone(zone_id, name, circle_polygon(float(cx), float(cy), radius))
    return Zone(zone_id, name, [(float(x), float(y)) for x, y in spec.get("vertices", [])])


def _fmt(p: Point) -> str:
    return f"({p[0]:.1f}, {p[1]:.1f})"
