"""Mission-level state estimation for VantaFlight.

This layer does not replace the vehicle's fast onboard stabilizer/EKF. It
creates a normalized, provenance-aware state for mission planning by combining
available vehicle telemetry with vision-derived target observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Iterable

import numpy as np

from ..models import Telemetry, TelemetrySource


@dataclass(frozen=True)
class TargetObservation:
    target_id: str
    position_world: tuple[float, float, float]
    timestamp: float
    confidence: float
    position_uncertainty_m: float
    source: TelemetrySource = TelemetrySource.CAMERA_DERIVED

    def __post_init__(self) -> None:
        values = (*self.position_world, self.timestamp, self.confidence, self.position_uncertainty_m)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("target observation values must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.position_uncertainty_m < 0.0:
            raise ValueError("position uncertainty cannot be negative")


@dataclass(frozen=True)
class TargetState:
    target_id: str
    position_world: tuple[float, float, float]
    velocity_world: tuple[float, float, float]
    timestamp: float
    confidence: float
    position_uncertainty_m: float
    age_s: float
    source: TelemetrySource


@dataclass(frozen=True)
class StateEstimate:
    timestamp: float
    position_world: tuple[float, float, float]
    velocity_world: tuple[float, float, float]
    heading_deg: float
    position_uncertainty_m: float
    velocity_uncertainty_mps: float
    position_available: bool
    velocity_available: bool
    heading_available: bool
    source: TelemetrySource
    targets: tuple[TargetState, ...] = field(default_factory=tuple)

    @property
    def confidence(self) -> float:
        if not self.position_available:
            return 0.0
        return float(np.clip(math.exp(-self.position_uncertainty_m), 0.0, 1.0))


@dataclass
class _Track:
    position: np.ndarray
    velocity: np.ndarray
    timestamp: float
    confidence: float
    uncertainty: float
    source: TelemetrySource


class VantaStateEstimator:
    """Small deterministic mission-state estimator.

    Vehicle telemetry remains authoritative when it is available. Target
    observations use an alpha-beta update with explicit uncertainty inflation
    during gaps. That is intentionally understandable and deterministic for a
    competition system; a full VIO/SLAM backend can be added behind the same
    interface if real synchronized raw sensors become available later.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.65,
        beta: float = 0.18,
        target_timeout_s: float = 1.5,
        uncertainty_growth_mps: float = 0.35,
    ) -> None:
        if not 0.0 < alpha <= 1.0 or not 0.0 <= beta <= 1.0:
            raise ValueError("alpha/beta out of range")
        if target_timeout_s <= 0 or uncertainty_growth_mps < 0:
            raise ValueError("invalid estimator timing")
        self.alpha = alpha
        self.beta = beta
        self.target_timeout_s = target_timeout_s
        self.uncertainty_growth_mps = uncertainty_growth_mps
        self._targets: dict[str, _Track] = {}

    def update(
        self,
        telemetry: Telemetry,
        observations: Iterable[TargetObservation] = (),
        *,
        now: float | None = None,
    ) -> StateEstimate:
        now = float(time.time() if now is None else now)
        self._ingest_targets(observations)
        targets = self._target_states(now)

        position_available = bool(getattr(telemetry, "position_available", True))
        velocity_available = bool(getattr(telemetry, "velocity_available", True))
        position = (float(telemetry.x), float(telemetry.y), float(telemetry.z)) if position_available else (0.0, 0.0, 0.0)

        if velocity_available:
            heading_rad = math.radians(float(telemetry.heading))
            speed = float(telemetry.velocity)
            velocity = (speed * math.cos(heading_rad), speed * math.sin(heading_rad), 0.0)
        else:
            velocity = (0.0, 0.0, 0.0)

        # Conservative uncertainty: unknown fields are never represented as
        # precise zero. Numbers are mission-level confidence weights, not
        # claims about the Hopper's internal estimator.
        position_uncertainty = 0.20 if position_available else 1000.0
        velocity_uncertainty = 0.30 if velocity_available else 1000.0
        source = TelemetrySource.HOPPER_NATIVE if telemetry.connected else TelemetrySource.UNAVAILABLE
        if getattr(telemetry, "connection_quality", None) and str(telemetry.connection_quality).endswith("POOR"):
            position_uncertainty *= 2.0
            velocity_uncertainty *= 2.0

        return StateEstimate(
            timestamp=now,
            position_world=position,
            velocity_world=velocity,
            heading_deg=float(telemetry.heading),
            position_uncertainty_m=position_uncertainty,
            velocity_uncertainty_mps=velocity_uncertainty,
            position_available=position_available,
            velocity_available=velocity_available,
            heading_available=telemetry.connected,
            source=source,
            targets=targets,
        )

    def _ingest_targets(self, observations: Iterable[TargetObservation]) -> None:
        for obs in observations:
            measurement = np.asarray(obs.position_world, dtype=float)
            old = self._targets.get(obs.target_id)
            if old is None or obs.timestamp <= old.timestamp:
                if old is None:
                    self._targets[obs.target_id] = _Track(
                        position=measurement,
                        velocity=np.zeros(3),
                        timestamp=obs.timestamp,
                        confidence=obs.confidence,
                        uncertainty=obs.position_uncertainty_m,
                        source=obs.source,
                    )
                continue

            dt = obs.timestamp - old.timestamp
            predicted = old.position + old.velocity * dt
            residual = measurement - predicted
            position = predicted + self.alpha * residual
            velocity = old.velocity + (self.beta / max(dt, 1e-6)) * residual
            uncertainty = max(
                obs.position_uncertainty_m,
                (1.0 - self.alpha) * (old.uncertainty + self.uncertainty_growth_mps * dt),
            )
            self._targets[obs.target_id] = _Track(
                position=position,
                velocity=velocity,
                timestamp=obs.timestamp,
                confidence=obs.confidence,
                uncertainty=uncertainty,
                source=obs.source,
            )

    def _target_states(self, now: float) -> tuple[TargetState, ...]:
        result: list[TargetState] = []
        stale: list[str] = []
        for target_id, track in self._targets.items():
            age = max(0.0, now - track.timestamp)
            if age > self.target_timeout_s:
                stale.append(target_id)
                continue
            predicted = track.position + track.velocity * age
            uncertainty = track.uncertainty + self.uncertainty_growth_mps * age
            confidence = float(np.clip(track.confidence * math.exp(-age / self.target_timeout_s), 0.0, 1.0))
            result.append(
                TargetState(
                    target_id=target_id,
                    position_world=tuple(float(v) for v in predicted),
                    velocity_world=tuple(float(v) for v in track.velocity),
                    timestamp=track.timestamp,
                    confidence=confidence,
                    position_uncertainty_m=uncertainty,
                    age_s=age,
                    source=track.source,
                )
            )
        for target_id in stale:
            self._targets.pop(target_id, None)
        result.sort(key=lambda t: t.target_id)
        return tuple(result)
