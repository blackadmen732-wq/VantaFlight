"""Ready-made flight patterns that generate mission plans.

Each generator returns a `MissionPlan` centred on (or starting from) a point
in local metres, so the operator can launch a useful flight in one click and
then tweak the waypoints.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from .plan import FinishAction, MissionPlan, Waypoint


def square(size_m: float = 20.0, altitude_m: float = 10.0, center_x: float = 0.0,
           center_y: float = 0.0) -> MissionPlan:
    """Fly the four corners of a square, then come home."""
    h = size_m / 2
    corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
    return MissionPlan(
        name=f"Square {size_m:g} m",
        waypoints=[
            Waypoint(x=center_x + cx, y=center_y + cy, altitude=altitude_m) for cx, cy in corners
        ],
        finish=FinishAction.RETURN_HOME,
    )


def orbit(radius_m: float = 15.0, altitude_m: float = 10.0, points: int = 12,
          center_x: float = 0.0, center_y: float = 0.0) -> MissionPlan:
    """Circle a point of interest (e.g. to inspect or film it)."""
    points = max(3, min(int(points), 72))
    wps = []
    for i in range(points + 1):  # close the loop
        a = 2 * math.pi * i / points
        wps.append(Waypoint(
            x=round(center_x + radius_m * math.cos(a), 2),
            y=round(center_y + radius_m * math.sin(a), 2),
            altitude=altitude_m,
        ))
    return MissionPlan(name=f"Orbit r={radius_m:g} m", waypoints=wps, finish=FinishAction.RETURN_HOME)


def survey(width_m: float = 40.0, height_m: float = 30.0, spacing_m: float = 10.0,
           altitude_m: float = 15.0, center_x: float = 0.0, center_y: float = 0.0) -> MissionPlan:
    """Lawnmower sweep that covers a rectangle (mapping, search and rescue)."""
    if spacing_m <= 0:
        raise ValueError("spacing must be positive")
    lanes = max(2, int(math.floor(height_m / spacing_m)) + 1)
    x0, x1 = center_x - width_m / 2, center_x + width_m / 2
    y0 = center_y - height_m / 2
    wps: list[Waypoint] = []
    for lane in range(lanes):
        y = round(y0 + min(lane * spacing_m, height_m), 2)
        xs = (x0, x1) if lane % 2 == 0 else (x1, x0)
        wps.extend(Waypoint(x=round(x, 2), y=y, altitude=altitude_m) for x in xs)
    return MissionPlan(
        name=f"Survey {width_m:g}x{height_m:g} m",
        waypoints=wps,
        finish=FinishAction.RETURN_HOME,
    )


PATTERNS: dict[str, Callable[..., MissionPlan]] = {
    "square": square,
    "orbit": orbit,
    "survey": survey,
}


def build_pattern(kind: str, params: dict[str, Any] | None = None) -> MissionPlan:
    if kind not in PATTERNS:
        raise ValueError(f"unknown pattern '{kind}'; expected one of {sorted(PATTERNS)}")
    try:
        return PATTERNS[kind](**(params or {}))
    except TypeError as exc:
        raise ValueError(f"bad parameters for '{kind}': {exc}") from exc
