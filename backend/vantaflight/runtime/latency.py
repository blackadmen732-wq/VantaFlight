"""Typed latency budget and rolling statistics."""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field

import numpy as np


@dataclass
class LatencySnapshot:
    """One end-to-end pipeline timing sample."""
    capture_ns: int = 0
    receive_ns: int = 0
    preprocess_start_ns: int = 0
    preprocess_end_ns: int = 0
    detect_start_ns: int = 0
    detect_end_ns: int = 0
    pose_ns: int = 0
    track_ns: int = 0
    fusion_ns: int = 0
    prediction_ns: int = 0
    planning_ns: int = 0
    command_queue_ns: int = 0
    command_send_ns: int = 0
    ack_ns: int = 0

    @property
    def frame_age_ms(self) -> float:
        if self.receive_ns and self.capture_ns:
            return (self.receive_ns - self.capture_ns) / 1e6
        return 0.0

    @property
    def vision_ms(self) -> float:
        if self.detect_end_ns and self.preprocess_start_ns:
            return (self.detect_end_ns - self.preprocess_start_ns) / 1e6
        return 0.0

    @property
    def planning_ms(self) -> float:
        if self.planning_ns and self.fusion_ns:
            return (self.planning_ns - self.fusion_ns) / 1e6
        return 0.0

    @property
    def command_ms(self) -> float:
        if self.command_send_ns and self.command_queue_ns:
            return (self.command_send_ns - self.command_queue_ns) / 1e6
        return 0.0

    @property
    def end_to_end_ms(self) -> float:
        last = max(self.command_send_ns, self.planning_ns, self.fusion_ns, self.track_ns)
        if last and self.capture_ns:
            return (last - self.capture_ns) / 1e6
        return 0.0

    def to_dict(self) -> dict:
        return {
            "frame_age_ms": round(self.frame_age_ms, 2),
            "vision_ms": round(self.vision_ms, 2),
            "planning_ms": round(self.planning_ms, 2),
            "command_ms": round(self.command_ms, 2),
            "end_to_end_ms": round(self.end_to_end_ms, 2),
        }


@dataclass
class LatencyBudget:
    """Per-stage latency targets for the pipeline."""
    max_frame_age_ms: float = 80.0
    max_vision_ms: float = 20.0
    max_planning_ms: float = 5.0
    max_command_ms: float = 10.0
    max_end_to_end_ms: float = 50.0

    def check(self, snapshot: LatencySnapshot) -> dict[str, bool]:
        return {
            "frame_age_ok": snapshot.frame_age_ms <= self.max_frame_age_ms,
            "vision_ok": snapshot.vision_ms <= self.max_vision_ms,
            "planning_ok": snapshot.planning_ms <= self.max_planning_ms,
            "command_ok": snapshot.command_ms <= self.max_command_ms,
            "end_to_end_ok": snapshot.end_to_end_ms <= self.max_end_to_end_ms,
        }


class LatencyStats:
    """Rolling window latency percentiles."""

    def __init__(self, window: int = 500) -> None:
        self._window = window
        self._frame_age: collections.deque[float] = collections.deque(maxlen=window)
        self._vision: collections.deque[float] = collections.deque(maxlen=window)
        self._planning: collections.deque[float] = collections.deque(maxlen=window)
        self._command: collections.deque[float] = collections.deque(maxlen=window)
        self._end_to_end: collections.deque[float] = collections.deque(maxlen=window)

    def record(self, snapshot: LatencySnapshot) -> None:
        self._frame_age.append(snapshot.frame_age_ms)
        self._vision.append(snapshot.vision_ms)
        self._planning.append(snapshot.planning_ms)
        self._command.append(snapshot.command_ms)
        self._end_to_end.append(snapshot.end_to_end_ms)

    @staticmethod
    def _percentiles(data: collections.deque[float]) -> dict:
        if not data:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
        arr = np.array(data)
        return {
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "p99": round(float(np.percentile(arr, 99)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    def summary(self) -> dict:
        return {
            "samples": len(self._end_to_end),
            "frame_age_ms": self._percentiles(self._frame_age),
            "vision_ms": self._percentiles(self._vision),
            "planning_ms": self._percentiles(self._planning),
            "command_ms": self._percentiles(self._command),
            "end_to_end_ms": self._percentiles(self._end_to_end),
        }
