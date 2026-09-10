"""Piecewise cubic Hermite racing trajectories."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from uuid import uuid4

import numpy as np
from numpy.typing import ArrayLike

from .models import DesiredTrajectoryState, GateTarget, vector3


@dataclass(frozen=True)
class LookaheadConfig:
    next_weight: float = 0.25
    future_weight: float = 0.08
    exit_distance: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.next_weight <= 1.0:
            raise ValueError("next_weight must be between zero and one")
        if not 0.0 <= self.future_weight <= 1.0:
            raise ValueError("future_weight must be between zero and one")
        if self.next_weight + self.future_weight > 1.0:
            raise ValueError("lookahead weights cannot sum above one")
        if self.exit_distance < 0.0:
            raise ValueError("exit_distance cannot be negative")


def lookahead_gate_position(
    current: GateTarget,
    next_gate: GateTarget | None = None,
    future_gate: GateTarget | None = None,
    config: LookaheadConfig = LookaheadConfig(),
) -> np.ndarray:
    """Aim beyond the current gate while bending toward later gates."""
    current_exit = current.position + current.normal * config.exit_distance
    target = current_exit.copy()
    if next_gate is not None:
        target += config.next_weight * (next_gate.position - current_exit)
    if future_gate is not None:
        target += config.future_weight * (future_gate.position - current_exit)
    return target


class CubicHermiteTrajectory:
    """C1-continuous interpolation through time-stamped 3D waypoints."""

    def __init__(
        self,
        times: ArrayLike,
        positions: ArrayLike,
        velocities: ArrayLike | None = None,
        *,
        trajectory_id: str | None = None,
        planner_confidence: float = 1.0,
    ) -> None:
        self.times = np.asarray(times, dtype=np.float64)
        self.positions = np.asarray(positions, dtype=np.float64)
        if self.times.ndim != 1 or len(self.times) < 2:
            raise ValueError("times must contain at least two values")
        if self.positions.shape != (len(self.times), 3):
            raise ValueError("positions must have shape (len(times), 3)")
        if not np.all(np.isfinite(self.times)) or not np.all(np.isfinite(self.positions)):
            raise ValueError("trajectory values must be finite")
        if np.any(np.diff(self.times) <= 0.0):
            raise ValueError("times must be strictly increasing")
        if velocities is None:
            self.velocities = self._finite_difference_tangents()
        else:
            self.velocities = np.asarray(velocities, dtype=np.float64)
            if self.velocities.shape != self.positions.shape:
                raise ValueError("velocities must match positions")
            if not np.all(np.isfinite(self.velocities)):
                raise ValueError("velocities must be finite")
        self.trajectory_id = trajectory_id or uuid4().hex
        self.planner_confidence = float(planner_confidence)
        if not 0.0 <= self.planner_confidence <= 1.0:
            raise ValueError("planner_confidence must be between zero and one")

    def _finite_difference_tangents(self) -> np.ndarray:
        tangents = np.empty_like(self.positions)
        slopes = np.diff(self.positions, axis=0) / np.diff(self.times)[:, None]
        tangents[0] = slopes[0]
        tangents[-1] = slopes[-1]
        for index in range(1, len(self.times) - 1):
            before = self.times[index] - self.times[index - 1]
            after = self.times[index + 1] - self.times[index]
            tangents[index] = (after * slopes[index - 1] + before * slopes[index]) / (
                before + after
            )
        return tangents

    def evaluate(self, timestamp: float) -> DesiredTrajectoryState:
        timestamp = float(np.clip(timestamp, self.times[0], self.times[-1]))
        index = min(int(np.searchsorted(self.times, timestamp, side="right")) - 1, len(self.times) - 2)
        index = max(index, 0)
        start, end = self.times[index : index + 2]
        duration = end - start
        u = (timestamp - start) / duration
        p0, p1 = self.positions[index : index + 2]
        v0, v1 = self.velocities[index : index + 2]

        position = (
            (2 * u**3 - 3 * u**2 + 1) * p0
            + (u**3 - 2 * u**2 + u) * duration * v0
            + (-2 * u**3 + 3 * u**2) * p1
            + (u**3 - u**2) * duration * v1
        )
        velocity = (
            (6 * u**2 - 6 * u) * p0 / duration
            + (3 * u**2 - 4 * u + 1) * v0
            + (-6 * u**2 + 6 * u) * p1 / duration
            + (3 * u**2 - 2 * u) * v1
        )
        acceleration = (
            (12 * u - 6) * p0 / duration**2
            + (6 * u - 4) * v0 / duration
            + (-12 * u + 6) * p1 / duration**2
            + (6 * u - 2) * v1 / duration
        )
        horizontal_speed = np.linalg.norm(velocity[:2])
        yaw = float(np.arctan2(velocity[1], velocity[0])) if horizontal_speed > 1e-9 else 0.0
        return DesiredTrajectoryState(
            position=position,
            velocity=velocity,
            acceleration=acceleration,
            yaw=yaw,
            timestamp=timestamp,
            trajectory_id=self.trajectory_id,
            planner_confidence=self.planner_confidence,
        )


def trajectory_through_gates(
    start: ArrayLike,
    gates: Sequence[GateTarget],
    *,
    speed: float,
    start_time: float = 0.0,
    lookahead: LookaheadConfig = LookaheadConfig(),
    trajectory_id: str | None = None,
) -> CubicHermiteTrajectory:
    """Build a useful initial racing trajectory from ordered scene targets."""
    if not gates:
        raise ValueError("at least one gate is required")
    if speed <= 0.0 or not np.isfinite(speed):
        raise ValueError("speed must be finite and positive")
    positions = [vector3(start, "start")]
    for index, gate in enumerate(gates):
        following = gates[index + 1] if index + 1 < len(gates) else None
        future = gates[index + 2] if index + 2 < len(gates) else None
        positions.append(lookahead_gate_position(gate, following, future, lookahead))
    distances = np.linalg.norm(np.diff(np.asarray(positions), axis=0), axis=1)
    durations = np.maximum(distances / speed, 1e-3)
    times = start_time + np.concatenate(([0.0], np.cumsum(durations)))
    confidence = min(gate.confidence for gate in gates)
    return CubicHermiteTrajectory(
        times,
        positions,
        trajectory_id=trajectory_id,
        planner_confidence=confidence,
    )
