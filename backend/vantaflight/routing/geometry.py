"""2D geometry for airspace: no-fly zones, clearance checks, detour nodes.

Everything works in local metres (``x`` east, ``y`` north) and is pure
Python, so it runs anywhere the flight core runs. The functions favour
being exact and predictable over being clever: every check that decides
whether a path is safe is a closed-form segment/polygon test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]

# Relative slack so points placed exactly at the safety margin count as clear.
_EPS = 1e-9


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    return (
        min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
        and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS
    )


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """True if segment p1-p2 touches or crosses segment q1-q2."""
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    if ((d1 > _EPS and d2 < -_EPS) or (d1 < -_EPS and d2 > _EPS)) and (
        (d3 > _EPS and d4 < -_EPS) or (d3 < -_EPS and d4 > _EPS)
    ):
        return True
    return (
        (abs(d1) <= _EPS and _on_segment(p1, q1, q2))
        or (abs(d2) <= _EPS and _on_segment(p2, q1, q2))
        or (abs(d3) <= _EPS and _on_segment(q1, p1, p2))
        or (abs(d4) <= _EPS and _on_segment(q2, p1, p2))
    )


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length_sq))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def segment_segment_distance(p1: Point, p2: Point, q1: Point, q2: Point) -> float:
    if segments_intersect(p1, p2, q1, q2):
        return 0.0
    return min(
        point_segment_distance(p1, q1, q2),
        point_segment_distance(p2, q1, q2),
        point_segment_distance(q1, p1, p2),
        point_segment_distance(q2, p1, p2),
    )


def point_in_polygon(p: Point, vertices: list[Point]) -> bool:
    """Ray casting; points on the boundary count as inside."""
    n = len(vertices)
    for i in range(n):
        if point_segment_distance(p, vertices[i], vertices[(i + 1) % n]) <= _EPS:
            return True
    inside = False
    x, y = p
    for i in range(n):
        (x1, y1), (x2, y2) = vertices[i], vertices[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def signed_area(vertices: list[Point]) -> float:
    n = len(vertices)
    return sum(
        vertices[i][0] * vertices[(i + 1) % n][1] - vertices[(i + 1) % n][0] * vertices[i][1]
        for i in range(n)
    ) / 2.0


def polygon_is_simple(vertices: list[Point]) -> bool:
    """No two non-adjacent edges touch (no self-intersection)."""
    n = len(vertices)
    edges = [(vertices[i], vertices[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if segments_intersect(*edges[i], *edges[j]):
                return False
    return True


def circle_polygon(cx: float, cy: float, radius: float, sides: int = 12) -> list[Point]:
    """A polygon that fully *contains* the circle (vertices just outside it)."""
    r = radius / math.cos(math.pi / sides)
    return [
        (cx + r * math.cos(2 * math.pi * i / sides), cy + r * math.sin(2 * math.pi * i / sides))
        for i in range(sides)
    ]


@dataclass
class Zone:
    """A no-fly zone: a simple polygon the aircraft must keep clear of."""

    zone_id: str
    name: str
    vertices: list[Point]
    bbox: tuple[float, float, float, float] = field(init=False)

    def __post_init__(self) -> None:
        if len(self.vertices) < 3:
            raise ValueError(f"zone '{self.name}' needs at least 3 corners")
        if not polygon_is_simple(self.vertices):
            raise ValueError(f"zone '{self.name}' crosses itself")
        if abs(signed_area(self.vertices)) < 1e-6:
            raise ValueError(f"zone '{self.name}' has no area")
        if signed_area(self.vertices) < 0:  # normalise to counter-clockwise
            self.vertices = list(reversed(self.vertices))
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    @property
    def edges(self) -> list[tuple[Point, Point]]:
        n = len(self.vertices)
        return [(self.vertices[i], self.vertices[(i + 1) % n]) for i in range(n)]

    def contains(self, p: Point, margin: float = 0.0) -> bool:
        """Inside the zone, or closer than ``margin`` to it."""
        if point_in_polygon(p, self.vertices):
            return True
        if margin <= 0:
            return False
        return any(point_segment_distance(p, a, b) < margin - _EPS for a, b in self.edges)

    def blocks(self, a: Point, b: Point, margin: float) -> bool:
        """Does flying straight from a to b come closer than ``margin``?"""
        x0, y0, x1, y1 = self.bbox
        if (
            max(a[0], b[0]) < x0 - margin or min(a[0], b[0]) > x1 + margin
            or max(a[1], b[1]) < y0 - margin or min(a[1], b[1]) > y1 + margin
        ):
            return False
        if point_in_polygon(a, self.vertices) or point_in_polygon(b, self.vertices):
            return True
        if any(segments_intersect(a, b, p, q) for p, q in self.edges):
            return True
        if margin <= 0:
            return False
        limit = margin * (1 - 1e-6) - _EPS
        return any(segment_segment_distance(a, b, p, q) < limit for p, q in self.edges)

    def detour_nodes(self, margin: float) -> list[Point]:
        """Points just outside each convex corner, at least ``margin`` clear.

        Shortest paths around polygons only ever bend at convex corners, so
        these are the only places a detour needs. Gentle corners get a single
        mitre point; sharp corners get a short arc of points so the straight
        hops between them still keep the margin.
        """
        n = len(self.vertices)
        nodes: list[Point] = []
        clearance = max(margin, 0.5) * 1.02
        for i in range(n):
            prev, p, nxt = self.vertices[i - 1], self.vertices[i], self.vertices[(i + 1) % n]
            if _cross(prev, p, nxt) <= _EPS:
                continue  # reflex (or flat) corner: never on a shortest path
            # Outward normals of the two edges meeting at p (polygon is CCW).
            e1 = (p[0] - prev[0], p[1] - prev[1])
            e2 = (nxt[0] - p[0], nxt[1] - p[1])
            a1 = math.atan2(-e1[0], e1[1])  # right-hand normal of e1
            a2 = math.atan2(-e2[0], e2[1])
            span = (a2 - a1) % (2 * math.pi)  # exterior turn at this corner
            steps = max(1, math.ceil(span / (math.pi / 6) - 1e-9))
            radius = clearance / math.cos(span / (2 * steps))
            if steps == 1:
                mid = a1 + span / 2
                nodes.append((p[0] + radius * math.cos(mid), p[1] + radius * math.sin(mid)))
                continue
            for k in range(steps + 1):
                ang = a1 + span * k / steps
                nodes.append((p[0] + radius * math.cos(ang), p[1] + radius * math.sin(ang)))
        return nodes
