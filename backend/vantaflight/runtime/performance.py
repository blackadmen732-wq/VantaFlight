"""VantaPerformanceManager — adaptive degradation with hysteresis."""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from .latency import LatencySnapshot, LatencyStats


class AdaptiveLevel(int, enum.Enum):
    FULL = 0
    REDUCED_PREVIEW = 1
    REDUCED_TWIN = 2
    PAUSED_ANALYZER = 3
    REDUCED_PERSISTENCE = 4
    REDUCED_DETECTOR = 5
    INCREASED_TRACKING = 6
    REDUCED_RESOLUTION = 7
    SMALLER_ROI = 8
    DISABLED_NEURAL = 9


@dataclass
class PerformanceState:
    level: AdaptiveLevel = AdaptiveLevel.FULL
    frame_age_p95_ms: float = 0.0
    pipeline_p95_ms: float = 0.0
    cpu_pressure: float = 0.0
    memory_pressure: float = 0.0
    detector_cost_ms: float = 0.0
    tracker_cost_ms: float = 0.0
    frame_drops: int = 0
    queue_depth: int = 0
    transition_count: int = 0
    last_transition_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            "level": self.level.name,
            "frame_age_p95_ms": round(self.frame_age_p95_ms, 2),
            "pipeline_p95_ms": round(self.pipeline_p95_ms, 2),
            "cpu_pressure": round(self.cpu_pressure, 2),
            "memory_pressure": round(self.memory_pressure, 2),
            "frame_drops": self.frame_drops,
            "queue_depth": self.queue_depth,
            "transition_count": self.transition_count,
        }


class VantaPerformanceManager:
    """Monitors pipeline health and adjusts workload with hysteresis.

    Escalates one level when pressure thresholds are exceeded for
    `escalation_hold_s` consecutive seconds. De-escalates only after
    `recovery_hold_s` seconds of sustained recovery below thresholds.
    """

    def __init__(
        self,
        frame_age_threshold_ms: float = 80.0,
        pipeline_threshold_ms: float = 30.0,
        cpu_threshold: float = 0.85,
        escalation_hold_s: float = 2.0,
        recovery_hold_s: float = 5.0,
    ) -> None:
        self._frame_age_threshold = frame_age_threshold_ms
        self._pipeline_threshold = pipeline_threshold_ms
        self._cpu_threshold = cpu_threshold
        self._escalation_hold = escalation_hold_s
        self._recovery_hold = recovery_hold_s
        self._state = PerformanceState()
        self._stats = LatencyStats(window=200)
        self._pressure_since: float | None = None
        self._recovery_since: float | None = None
        self._frame_drops_total = 0

    @property
    def state(self) -> PerformanceState:
        return self._state

    @property
    def stats(self) -> LatencyStats:
        return self._stats

    @property
    def level(self) -> AdaptiveLevel:
        return self._state.level

    @property
    def is_degraded(self) -> bool:
        return self._state.level.value > 0

    def update(
        self,
        snapshot: LatencySnapshot,
        *,
        cpu_pressure: float = 0.0,
        memory_pressure: float = 0.0,
        frame_drops: int = 0,
        queue_depth: int = 0,
    ) -> AdaptiveLevel:
        now = time.monotonic()
        self._stats.record(snapshot)

        summary = self._stats.summary()
        self._state.frame_age_p95_ms = summary["frame_age_ms"]["p95"]
        self._state.pipeline_p95_ms = summary["end_to_end_ms"]["p95"]
        self._state.cpu_pressure = cpu_pressure
        self._state.memory_pressure = memory_pressure
        self._state.frame_drops = frame_drops
        self._state.queue_depth = queue_depth

        under_pressure = (
            self._state.frame_age_p95_ms > self._frame_age_threshold
            or self._state.pipeline_p95_ms > self._pipeline_threshold
            or cpu_pressure > self._cpu_threshold
        )

        if under_pressure:
            self._recovery_since = None
            if self._pressure_since is None:
                self._pressure_since = now
            elif now - self._pressure_since >= self._escalation_hold:
                self._escalate(now)
                self._pressure_since = now
        else:
            self._pressure_since = None
            if self._recovery_since is None:
                self._recovery_since = now
            elif now - self._recovery_since >= self._recovery_hold:
                self._deescalate(now)
                self._recovery_since = now

        return self._state.level

    def _escalate(self, now: float) -> None:
        current = self._state.level.value
        max_level = max(level.value for level in AdaptiveLevel)
        if current < max_level:
            self._state.level = AdaptiveLevel(current + 1)
            self._state.transition_count += 1
            self._state.last_transition_time = now

    def _deescalate(self, now: float) -> None:
        current = self._state.level.value
        if current > 0:
            self._state.level = AdaptiveLevel(current - 1)
            self._state.transition_count += 1
            self._state.last_transition_time = now

    def reset(self) -> None:
        self._state = PerformanceState()
        self._stats = LatencyStats(window=200)
        self._pressure_since = None
        self._recovery_since = None
