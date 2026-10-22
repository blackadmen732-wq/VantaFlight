"""Small, dependency-free measurement helpers shared by the bus, runtime and health.

Windows are bounded (no metric can grow without limit) and every percentile
is computed from the most recent samples only.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted list (q in [0, 1])."""
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class RollingWindow:
    """The last ``size`` samples of one quantity, with p50/p95/p99."""

    __slots__ = ("_values",)

    def __init__(self, size: int = 256) -> None:
        if size < 1:
            raise ValueError("window size must be positive")
        self._values: deque[float] = deque(maxlen=size)

    def add(self, value: float) -> None:
        if math.isfinite(value):
            self._values.append(float(value))

    def extend(self, values: Iterable[float]) -> None:
        for value in values:
            self.add(value)

    def __len__(self) -> int:
        return len(self._values)

    def clear(self) -> None:
        self._values.clear()

    @property
    def last(self) -> float | None:
        return self._values[-1] if self._values else None

    def percentiles(self) -> dict[str, float | None]:
        if not self._values:
            return {"p50": None, "p95": None, "p99": None, "max": None}
        ordered = sorted(self._values)
        return {
            "p50": percentile(ordered, 0.50),
            "p95": percentile(ordered, 0.95),
            "p99": percentile(ordered, 0.99),
            "max": ordered[-1],
        }


class RateMeter:
    """Event rate (Hz) over a sliding time window, from event timestamps (seconds)."""

    __slots__ = ("_stamps", "_window_s")

    def __init__(self, window_s: float = 5.0, max_events: int = 4096) -> None:
        if window_s <= 0:
            raise ValueError("window must be positive")
        self._window_s = window_s
        self._stamps: deque[float] = deque(maxlen=max_events)

    def mark(self, t_s: float) -> None:
        self._stamps.append(t_s)

    def rate_hz(self, now_s: float) -> float:
        """Mean event rate over the window: (n - 1) intervals between n events.

        A stream that stopped shows up as staleness (age of ``last``), which
        health reports separately; the rate itself only describes the events
        that did arrive inside the window.
        """
        cutoff = now_s - self._window_s
        while self._stamps and self._stamps[0] < cutoff:
            self._stamps.popleft()
        if len(self._stamps) < 2:
            return 0.0
        span = self._stamps[-1] - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0

    @property
    def last(self) -> float | None:
        return self._stamps[-1] if self._stamps else None


def rounded(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def rounded_percentiles(stats: dict[str, float | None], scale: float = 1.0, digits: int = 3) -> dict:
    return {k: rounded(None if v is None else v * scale, digits) for k, v in stats.items()}
