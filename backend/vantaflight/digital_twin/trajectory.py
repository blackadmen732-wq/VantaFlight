"""Rolling trajectory buffer for the digital twin."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrajectoryPoint:
    timestamp: float
    x: float
    y: float
    z: float
    heading: float


class TrajectoryBuffer:
    """Fixed-size rolling buffer of recent positions."""

    def __init__(self, max_points: int = 500) -> None:
        self._points: deque[TrajectoryPoint] = deque(maxlen=max_points)
        self.max_points = max_points

    def add(self, ts: float, x: float, y: float, z: float, heading: float) -> None:
        self._points.append(TrajectoryPoint(ts, x, y, z, heading))

    def clear(self) -> None:
        self._points.clear()

    @property
    def points(self) -> list[TrajectoryPoint]:
        return list(self._points)

    def __len__(self) -> int:
        return len(self._points)
