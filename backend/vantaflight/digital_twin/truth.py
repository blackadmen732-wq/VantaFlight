"""Simulator ground truth kept separate from perception inputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


@dataclass(frozen=True)
class AircraftTruth:
    timestamp: float
    position: Vector3
    velocity: Vector3
    orientation_wxyz: Quaternion
    angular_velocity: Vector3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class GateTruth:
    gate_id: str
    course_order: int
    center: Vector3
    orientation_wxyz: Quaternion
    width: float
    height: float


@dataclass(frozen=True)
class SimulatorTruthFrame:
    """Exact simulator state for validation/analysis, never VantaSight input."""

    timestamp: float
    aircraft: AircraftTruth
    gates: tuple[GateTruth, ...] = ()
    course_id: str | None = None
    course_geometry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationFrame:
    """Time-aligned outputs used by VantaForge analysis."""

    timestamp: float
    simulator_truth: SimulatorTruthFrame
    sight_estimate: dict[str, Any] | None = None
    track_prediction: dict[str, Any] | None = None
    race_request: dict[str, Any] | None = None
    px4_simulated_actual: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DigitalTwinTruthStore:
    """Small bounded truth/estimate history for offline comparison."""

    def __init__(self, capacity: int = 2_000) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._truth: list[SimulatorTruthFrame] = []
        self._validation: list[ValidationFrame] = []

    def add_truth(self, frame: SimulatorTruthFrame) -> None:
        if self._truth and frame.timestamp < self._truth[-1].timestamp:
            raise ValueError("simulator truth timestamps must be monotonic")
        self._truth.append(frame)
        del self._truth[:-self._capacity]

    def add_validation(self, frame: ValidationFrame) -> None:
        if frame.simulator_truth not in self._truth:
            raise ValueError("validation must reference recorded simulator truth")
        self._validation.append(frame)
        del self._validation[:-self._capacity]

    @property
    def latest_truth(self) -> SimulatorTruthFrame | None:
        return self._truth[-1] if self._truth else None

    @property
    def validation_frames(self) -> tuple[ValidationFrame, ...]:
        return tuple(self._validation)

    def clear(self) -> None:
        self._truth.clear()
        self._validation.clear()
