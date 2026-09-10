"""Typed, normalized inputs and outputs for racing planners."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray


Vector3 = NDArray[np.float64]


def vector3(value: ArrayLike, name: str) -> Vector3:
    """Return a finite, owned three-vector."""
    result = np.array(value, dtype=np.float64, copy=True)
    if result.shape != (3,):
        raise ValueError(f"{name} must have shape (3,), got {result.shape}")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    return result


@dataclass(frozen=True)
class AircraftState:
    """Estimator output in a local right-handed Cartesian frame."""

    position: ArrayLike
    velocity: ArrayLike
    acceleration: ArrayLike = field(default_factory=lambda: np.zeros(3))
    yaw: float = 0.0
    timestamp: float = 0.0
    position_uncertainty: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", vector3(self.position, "position"))
        object.__setattr__(self, "velocity", vector3(self.velocity, "velocity"))
        object.__setattr__(self, "acceleration", vector3(self.acceleration, "acceleration"))
        for name in ("yaw", "timestamp", "position_uncertainty"):
            value = float(getattr(self, name))
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.position_uncertainty < 0.0:
            raise ValueError("position_uncertainty cannot be negative")


class TargetSlot(str, Enum):
    CURRENT = "CURRENT"
    NEXT = "NEXT"
    FUTURE = "FUTURE"


@runtime_checkable
class SceneTarget(Protocol):
    """Structural input accepted from a vision or simulation scene."""

    position: ArrayLike
    normal: ArrayLike
    clearance: float
    confidence: float
    uncertainty: float


@runtime_checkable
class RacingScene(Protocol):
    """Scene-like input exposing an ordered gate horizon."""

    CURRENT: SceneTarget | None
    NEXT: SceneTarget | None
    FUTURE: SceneTarget | None


@dataclass(frozen=True)
class GateTarget:
    """Normalized scene target; normals point through the gate."""

    position: ArrayLike
    normal: ArrayLike
    clearance: float
    confidence: float = 1.0
    uncertainty: float = 0.0
    slot: TargetSlot = TargetSlot.CURRENT
    target_id: str = ""

    def __post_init__(self) -> None:
        position = vector3(self.position, "position")
        normal = vector3(self.normal, "normal")
        # Scaling first avoids overflow for finite normals near float64 limits.
        scale = float(np.max(np.abs(normal)))
        if scale == 0.0:
            raise ValueError("normal cannot be zero")
        unit_scale = normal / scale
        magnitude = float(np.linalg.norm(unit_scale))
        if magnitude <= 1e-9:
            raise ValueError("normal cannot be zero")
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "normal", unit_scale / magnitude)
        for name in ("clearance", "confidence", "uncertainty"):
            value = float(getattr(self, name))
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.clearance <= 0.0:
            raise ValueError("clearance must be positive")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if self.uncertainty < 0.0:
            raise ValueError("uncertainty cannot be negative")
        object.__setattr__(self, "slot", TargetSlot(self.slot))

    @classmethod
    def from_scene(cls, target: SceneTarget, slot: TargetSlot) -> "GateTarget":
        return cls(
            position=target.position,
            normal=target.normal,
            clearance=target.clearance,
            confidence=target.confidence,
            uncertainty=target.uncertainty,
            slot=slot,
            target_id=str(getattr(target, "target_id", "")),
        )


@dataclass(frozen=True)
class DesiredTrajectoryState:
    """High-level setpoint. Deliberately contains no actuator commands."""

    position: ArrayLike
    velocity: ArrayLike
    acceleration: ArrayLike
    yaw: float
    timestamp: float
    trajectory_id: str
    planner_confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", vector3(self.position, "position"))
        object.__setattr__(self, "velocity", vector3(self.velocity, "velocity"))
        object.__setattr__(self, "acceleration", vector3(self.acceleration, "acceleration"))
        for name in ("yaw", "timestamp", "planner_confidence"):
            value = float(getattr(self, name))
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if not self.trajectory_id:
            raise ValueError("trajectory_id cannot be empty")
        if not 0.0 <= self.planner_confidence <= 1.0:
            raise ValueError("planner_confidence must be between zero and one")

    @property
    def desired_position(self) -> Vector3:
        return self.position

    @property
    def desired_velocity(self) -> Vector3:
        return self.velocity

    @property
    def desired_acceleration(self) -> Vector3:
        return self.acceleration

    @property
    def desired_yaw(self) -> float:
        return self.yaw

    def to_dict(self) -> dict[str, object]:
        """Serialize to the frontend DesiredTrajectoryState contract."""

        def xyz(vector: ArrayLike) -> dict[str, float]:
            value = vector3(vector, "vector")
            return {"x": float(value[0]), "y": float(value[1]), "z": float(value[2])}

        return {
            "desired_position": xyz(self.position),
            "desired_velocity": xyz(self.velocity),
            "desired_acceleration": xyz(self.acceleration),
            "desired_yaw": self.yaw,
            "timestamp": self.timestamp,
            "trajectory_id": self.trajectory_id,
            "planner_confidence": self.planner_confidence,
        }
