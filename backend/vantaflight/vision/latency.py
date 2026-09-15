"""Per-frame latency timeline and capture-time state prediction."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LatencyTimeline:
    STAGES = (
        "capture", "receive", "preprocess", "detect", "pose", "track",
        "fusion", "planning", "command",
    )

    capture_timestamp: float
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, timestamp: float) -> None:
        if timestamp < self.capture_timestamp:
            raise ValueError("timeline marks cannot precede capture")
        if self.marks and timestamp < max(self.marks.values()):
            raise ValueError("timeline marks must be monotonic")
        self.marks[name] = timestamp

    @property
    def end_to_end_s(self) -> float:
        return (max(self.marks.values()) - self.capture_timestamp) if self.marks else 0.0

    def stage_durations_ms(self) -> dict[str, float]:
        previous = self.capture_timestamp
        durations: dict[str, float] = {}
        for name, timestamp in self.marks.items():
            durations[name] = (timestamp - previous) * 1000
            previous = timestamp
        return durations

    def stage_timestamps(self) -> dict[str, float | None]:
        """Return the canonical timeline, including stages not reached yet."""
        return {
            stage: (
                self.capture_timestamp if stage == "capture" else self.marks.get(stage)
            )
            for stage in self.STAGES
        }


def predict_position_for_latency(
    position: np.ndarray,
    velocity: np.ndarray,
    capture_timestamp: float,
    now: float,
    command_latency_s: float = 0.0,
    max_horizon_s: float = 1.0,
) -> np.ndarray:
    """Project a constant-velocity estimate from capture to command execution."""
    if command_latency_s < 0 or max_horizon_s < 0:
        raise ValueError("latency and horizon must be non-negative")
    horizon = float(np.clip(now - capture_timestamp + command_latency_s, 0, max_horizon_s))
    return np.asarray(position, np.float64) + np.asarray(velocity, np.float64) * horizon
