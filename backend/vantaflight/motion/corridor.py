"""Safe flight corridor generation.

The corridor is a geometric safety envelope around a mission route. It is not
an obstacle-avoidance oracle: callers must supply clearance already known from
the field/world model. VantaMotion can optimize inside this envelope while
VantaExecution remains the final safety gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class CorridorSegment:
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    radius_m: float
    speed_limit_mps: float
    clearance_m: float

    def __post_init__(self) -> None:
        values = (*self.start, *self.end, self.radius_m, self.speed_limit_mps, self.clearance_m)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("corridor values must be finite")
        if self.radius_m <= 0 or self.speed_limit_mps <= 0 or self.clearance_m < 0:
            raise ValueError("invalid corridor limits")

    @property
    def length_m(self) -> float:
        return float(np.linalg.norm(np.asarray(self.end) - np.asarray(self.start)))

    def contains(self, point: Sequence[float]) -> bool:
        p = np.asarray(point, dtype=float)
        a = np.asarray(self.start, dtype=float)
        b = np.asarray(self.end, dtype=float)
        ab = b - a
        denom = float(np.dot(ab, ab))
        if denom <= 1e-12:
            return float(np.linalg.norm(p - a)) <= self.radius_m
        t = float(np.clip(np.dot(p - a, ab) / denom, 0.0, 1.0))
        nearest = a + t * ab
        return float(np.linalg.norm(p - nearest)) <= self.radius_m


@dataclass(frozen=True)
class FlightCorridor:
    segments: tuple[CorridorSegment, ...]

    @property
    def length_m(self) -> float:
        return sum(s.length_m for s in self.segments)

    def contains(self, point: Sequence[float]) -> bool:
        return any(segment.contains(point) for segment in self.segments)

    def local_speed_limit(self, point: Sequence[float]) -> float | None:
        matches = [s.speed_limit_mps for s in self.segments if s.contains(point)]
        return min(matches) if matches else None


class SafeCorridorGenerator:
    """Build a conservative tube around an ordered 3D route."""

    def __init__(
        self,
        *,
        vehicle_radius_m: float = 0.13,
        safety_margin_m: float = 0.10,
        nominal_speed_mps: float = 2.0,
    ) -> None:
        if vehicle_radius_m <= 0 or safety_margin_m < 0 or nominal_speed_mps <= 0:
            raise ValueError("invalid corridor generator configuration")
        self.vehicle_radius_m = vehicle_radius_m
        self.safety_margin_m = safety_margin_m
        self.nominal_speed_mps = nominal_speed_mps

    def generate(
        self,
        route: Iterable[Sequence[float]],
        *,
        available_clearance_m: float | Sequence[float] = 1.0,
        speed_limits_mps: float | Sequence[float] | None = None,
    ) -> FlightCorridor:
        points = [tuple(float(v) for v in p[:3]) for p in route]
        if len(points) < 2:
            raise ValueError("route requires at least two points")
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
            raise ValueError("route points must be finite XYZ triples")

        count = len(points) - 1
        clearances = self._expand(available_clearance_m, count, "clearance")
        speeds = self._expand(
            self.nominal_speed_mps if speed_limits_mps is None else speed_limits_mps,
            count,
            "speed",
        )

        segments: list[CorridorSegment] = []
        required = self.vehicle_radius_m + self.safety_margin_m
        for i in range(count):
            clearance = clearances[i]
            if clearance <= required:
                raise ValueError(
                    f"segment {i} has {clearance:.3f} m clearance but requires more than {required:.3f} m"
                )
            usable_radius = clearance - self.vehicle_radius_m
            segments.append(
                CorridorSegment(
                    start=points[i],
                    end=points[i + 1],
                    radius_m=max(self.safety_margin_m, usable_radius),
                    speed_limit_mps=speeds[i],
                    clearance_m=clearance,
                )
            )
        return FlightCorridor(tuple(segments))

    @staticmethod
    def _expand(value: float | Sequence[float], count: int, name: str) -> list[float]:
        if isinstance(value, (int, float)):
            values = [float(value)] * count
        else:
            values = [float(v) for v in value]
            if len(values) != count:
                raise ValueError(f"{name} must provide exactly {count} values")
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError(f"{name} values must be finite and positive")
        return values
