"""Course-aware flight run analysis."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .models import Course, Vector, vector3


@dataclass(frozen=True)
class RunSample:
    timestamp: float
    position: Vector
    speed: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp):
            raise ValueError("sample timestamp must be finite")
        object.__setattr__(self, "position", vector3(self.position, "sample position"))
        if self.speed is not None and (not math.isfinite(self.speed) or self.speed < 0):
            raise ValueError("sample speed must be finite and nonnegative")


@dataclass(frozen=True)
class GateMetrics:
    gate_order: int
    passed: bool
    timestamp: float | None
    crossing_speed: float
    center_error: float
    approach_speed: float
    exit_speed: float
    speed_loss: float


@dataclass(frozen=True)
class SegmentMetrics:
    start_gate: int
    end_gate: int
    duration: float
    distance: float
    average_speed: float
    minimum_speed: float
    speed_loss: float


@dataclass(frozen=True)
class RunMetrics:
    duration: float
    distance: float
    average_speed: float
    maximum_speed: float
    minimum_speed: float
    gates_passed: int
    completion_ratio: float
    total_speed_loss: float


@dataclass(frozen=True)
class AnalysisResult:
    run: RunMetrics
    gates: tuple[GateMetrics, ...]
    segments: tuple[SegmentMetrics, ...]
    speed_loss_locations: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": [item.__dict__ for item in self.gates],
            "run": self.run.__dict__,
            "segments": [item.__dict__ for item in self.segments],
            "speed_loss_locations": list(self.speed_loss_locations),
        }


def _sample_from(value: RunSample | Mapping[str, Any] | Sequence[Any]) -> RunSample:
    if isinstance(value, RunSample):
        return value
    if isinstance(value, Mapping):
        position = value.get("position")
        if position is None:
            position = (value["x"], value["y"], value["z"])
        return RunSample(float(value["timestamp"]), position, value.get("speed"))
    if len(value) == 3:
        return RunSample(float(value[0]), value[1], value[2])
    if len(value) in (4, 5):
        return RunSample(float(value[0]), value[1:4], value[4] if len(value) == 5 else None)
    raise ValueError("sample must provide timestamp, position, and optional speed")


class CourseAnalyzer:
    """Calculate run, gate, and segment metrics from timestamped positions."""

    def __init__(self, speed_loss_threshold: float = .75) -> None:
        if speed_loss_threshold < 0:
            raise ValueError("speed loss threshold must be nonnegative")
        self.speed_loss_threshold = float(speed_loss_threshold)

    def analyze(
        self,
        course: Course,
        samples: Iterable[RunSample | Mapping[str, Any] | Sequence[Any]],
    ) -> AnalysisResult:
        data = tuple(_sample_from(item) for item in samples)
        if len(data) < 2:
            raise ValueError("at least two samples are required")
        times = np.asarray([item.timestamp for item in data], dtype=float)
        if np.any(np.diff(times) <= 0):
            raise ValueError("sample timestamps must be strictly increasing")
        positions = np.asarray([item.position for item in data], dtype=float)
        distances = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        dt = np.diff(times)
        derived = np.concatenate(([distances[0] / dt[0]], distances / dt))
        speeds = np.asarray([
            derived[index] if item.speed is None else item.speed
            for index, item in enumerate(data)
        ], dtype=float)
        speeds = np.maximum(speeds, 0.0)

        gate_metrics: list[GateMetrics] = []
        crossing_indices: list[int | None] = []
        search_start = 0
        for gate in course.gates:
            signed = np.asarray([
                gate.opening_coordinates(position)[0] for position in positions
            ])
            found: tuple[int, float, np.ndarray] | None = None
            for index in range(search_start, len(data) - 1):
                if signed[index] <= 0 < signed[index + 1]:
                    denominator = signed[index] - signed[index + 1]
                    alpha = signed[index] / denominator if abs(denominator) > 1e-12 else 0.5
                    crossing = positions[index] + alpha * (positions[index + 1] - positions[index])
                    _, right, up = gate.opening_coordinates(crossing)
                    if abs(right) <= gate.width / 2 and abs(up) <= gate.height / 2:
                        found = (index, float(alpha), crossing)
                        break
            if found is None:
                crossing_indices.append(None)
                gate_metrics.append(GateMetrics(gate.order, False, None, 0.0, math.inf, 0.0, 0.0, 0.0))
                continue

            index, alpha, crossing = found
            search_start = index + 1
            crossing_indices.append(index)
            crossing_speed = float(speeds[index] * (1 - alpha) + speeds[index + 1] * alpha)
            approach = float(np.mean(speeds[max(0, index - 2): index + 1]))
            exit_speed = float(np.mean(speeds[index + 1: min(len(speeds), index + 4)]))
            loss = max(0.0, approach - exit_speed)
            gate_metrics.append(GateMetrics(
                gate_order=gate.order,
                passed=True,
                timestamp=float(times[index] + alpha * dt[index]),
                crossing_speed=crossing_speed,
                center_error=float(np.linalg.norm(crossing - np.asarray(gate.center))),
                approach_speed=approach,
                exit_speed=exit_speed,
                speed_loss=loss,
            ))

        segments: list[SegmentMetrics] = []
        passed = [(metric.gate_order, index) for metric, index in zip(gate_metrics, crossing_indices) if index is not None]
        for (start_order, start), (end_order, end) in zip(passed, passed[1:]):
            assert start is not None and end is not None
            end_index = min(end + 1, len(data) - 1)
            section_distance = float(np.linalg.norm(
                np.diff(positions[start:end_index + 1], axis=0), axis=1
            ).sum())
            duration = float(times[end_index] - times[start])
            section_speeds = speeds[start:end_index + 1]
            start_speed, minimum_speed = float(section_speeds[0]), float(np.min(section_speeds))
            segments.append(SegmentMetrics(
                start_gate=start_order,
                end_gate=end_order,
                duration=duration,
                distance=section_distance,
                average_speed=section_distance / duration if duration > 0 else 0.0,
                minimum_speed=minimum_speed,
                speed_loss=max(0.0, start_speed - minimum_speed),
            ))

        duration = float(times[-1] - times[0])
        total_distance = float(distances.sum())
        losses = tuple(
            metric.gate_order for metric in gate_metrics
            if metric.passed and metric.speed_loss >= self.speed_loss_threshold
        )
        run = RunMetrics(
            duration=duration,
            distance=total_distance,
            average_speed=total_distance / duration,
            maximum_speed=float(np.max(speeds)),
            minimum_speed=float(np.min(speeds)),
            gates_passed=sum(metric.passed for metric in gate_metrics),
            completion_ratio=sum(metric.passed for metric in gate_metrics) / len(course.gates),
            total_speed_loss=float(sum(metric.speed_loss for metric in gate_metrics)),
        )
        return AnalysisResult(run, tuple(gate_metrics), tuple(segments), losses)
