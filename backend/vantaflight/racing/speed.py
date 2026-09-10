"""Constraint-aware racing speed profiles."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class SpeedEnvelopeConfig:
    min_speed: float = 0.5
    max_speed: float = 15.0
    max_lateral_acceleration: float = 8.0
    max_forward_acceleration: float = 5.0
    max_braking_acceleration: float = 7.0
    max_vertical_speed: float = 4.0
    clearance_for_full_speed: float = 3.0
    uncertainty_scale: float = 1.0
    aggression: float = 0.5

    def __post_init__(self) -> None:
        positive = (
            "min_speed",
            "max_speed",
            "max_lateral_acceleration",
            "max_forward_acceleration",
            "max_braking_acceleration",
            "max_vertical_speed",
            "clearance_for_full_speed",
            "uncertainty_scale",
        )
        if any(not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0 for name in positive):
            raise ValueError("speed envelope limits must be finite and positive")
        if self.min_speed > self.max_speed:
            raise ValueError("min_speed cannot exceed max_speed")
        if not 0.0 <= self.aggression <= 1.0:
            raise ValueError("aggression must be between zero and one")


def speed_envelope(
    distances: ArrayLike,
    curvature: ArrayLike,
    vertical_gradient: ArrayLike,
    clearance: ArrayLike,
    confidence: ArrayLike,
    uncertainty: ArrayLike,
    *,
    initial_speed: float = 0.0,
    final_speed: float | None = None,
    config: SpeedEnvelopeConfig = SpeedEnvelopeConfig(),
) -> NDArray[np.float64]:
    """Compute point speeds, then enforce acceleration and braking reachability."""
    distance = np.asarray(distances, dtype=np.float64)
    arrays = [
        np.asarray(value, dtype=np.float64)
        for value in (curvature, vertical_gradient, clearance, confidence, uncertainty)
    ]
    if distance.ndim != 1 or len(distance) < 2:
        raise ValueError("distances must contain at least two points")
    if any(value.shape != distance.shape for value in arrays):
        raise ValueError("all constraints must match distances")
    if any(not np.all(np.isfinite(value)) for value in [distance, *arrays]):
        raise ValueError("speed envelope inputs must be finite")
    if np.any(np.diff(distance) <= 0.0):
        raise ValueError("distances must be strictly increasing")
    curve, vertical, space, trust, error = arrays
    if np.any(curve < 0) or np.any(space <= 0) or np.any(error < 0):
        raise ValueError("curvature/uncertainty cannot be negative and clearance must be positive")
    if np.any((trust < 0) | (trust > 1)):
        raise ValueError("confidence must be between zero and one")

    # Aggression continuously opens every dynamic limit, not merely top speed.
    scale = 0.55 + 0.45 * config.aggression
    top_speed = config.max_speed * scale
    lateral_acceleration = config.max_lateral_acceleration * scale
    forward_acceleration = config.max_forward_acceleration * scale
    braking_acceleration = config.max_braking_acceleration * scale

    curve_cap = np.sqrt(lateral_acceleration / np.maximum(curve, 1e-12))
    vertical_cap = config.max_vertical_speed * scale / np.maximum(np.abs(vertical), 1e-12)
    clearance_factor = np.clip(space / config.clearance_for_full_speed, 0.0, 1.0)
    confidence_factor = 0.35 + 0.65 * trust
    uncertainty_factor = np.exp(-error / config.uncertainty_scale)
    local_cap = np.minimum.reduce(
        (
            np.full_like(distance, top_speed),
            curve_cap,
            vertical_cap,
            top_speed * clearance_factor,
            top_speed * confidence_factor,
            top_speed * uncertainty_factor,
        )
    )
    speeds = np.maximum(np.minimum(local_cap, config.max_speed), config.min_speed)
    speeds[0] = min(speeds[0], max(float(initial_speed), 0.0))

    segment_lengths = np.diff(distance)
    for index, segment in enumerate(segment_lengths, start=1):
        reachable = np.sqrt(max(0.0, speeds[index - 1] ** 2 + 2.0 * forward_acceleration * segment))
        speeds[index] = min(speeds[index], reachable)

    if final_speed is not None:
        if not np.isfinite(final_speed) or final_speed < 0.0:
            raise ValueError("final_speed must be finite and non-negative")
        speeds[-1] = min(speeds[-1], float(final_speed))
    for index in range(len(speeds) - 2, -1, -1):
        reachable = np.sqrt(
            max(0.0, speeds[index + 1] ** 2 + 2.0 * braking_acceleration * segment_lengths[index])
        )
        speeds[index] = min(speeds[index], reachable)
    return speeds
