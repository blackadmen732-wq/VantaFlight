"""Timestamp-driven constant-velocity Kalman target tracking."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

import numpy as np

from .concepts import TargetCandidate


class TrackStatus(str, Enum):
    TENTATIVE = "tentative"
    TRACKING = "tracking"
    COASTING = "coasting"
    LOST = "lost"


@dataclass(frozen=True)
class TrackerConfig:
    process_noise: float = 20.0
    measurement_noise: float = 4.0
    confirmation_hits: int = 2
    max_missed: int = 3
    reacquisition_timeout_s: float = 2.0
    mahalanobis_gate: float = 9.21
    min_dt_s: float = 1e-3
    max_dt_s: float = 1.0


@dataclass(frozen=True)
class TrackSnapshot:
    timestamp: float
    position: np.ndarray
    velocity: np.ndarray
    covariance: np.ndarray
    status: TrackStatus
    hits: int
    missed: int
    reacquired: bool = False


class KalmanTargetTracker:
    """2D constant-velocity filter with explicit coast/lost/reacquire states."""

    def __init__(self, config: TrackerConfig | None = None) -> None:
        self.config = config or TrackerConfig()
        self.state = np.zeros(4, dtype=np.float64)
        self.covariance = np.eye(4, dtype=np.float64) * 100.0
        self.timestamp: float | None = None
        self.last_measurement_timestamp: float | None = None
        self.status = TrackStatus.LOST
        self.hits = 0
        self.missed = 0

    @property
    def initialized(self) -> bool:
        return self.timestamp is not None

    def _transition(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        transition = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1.]])
        q = self.config.process_noise
        process = q * np.array(
            [[dt**4 / 4, 0, dt**3 / 2, 0], [0, dt**4 / 4, 0, dt**3 / 2],
             [dt**3 / 2, 0, dt**2, 0], [0, dt**3 / 2, 0, dt**2]]
        )
        return transition, process

    def predict(self, timestamp: float) -> TrackSnapshot:
        if self.timestamp is None:
            raise RuntimeError("tracker has no initial measurement")
        if timestamp < self.timestamp:
            raise ValueError("timestamps must be monotonic")
        elapsed = timestamp - self.timestamp
        if elapsed == 0:
            return self.snapshot(False)
        dt = float(np.clip(elapsed, self.config.min_dt_s, self.config.max_dt_s))
        transition, process = self._transition(dt)
        self.state = transition @ self.state
        self.covariance = transition @ self.covariance @ transition.T + process
        self.timestamp = timestamp
        return self.snapshot(False)

    def innovation(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
        measurement = np.asarray(measurement, np.float64).reshape(2)
        observation = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
        residual = measurement - observation @ self.state
        innovation_cov = observation @ self.covariance @ observation.T + np.eye(2) * self.config.measurement_noise
        distance = float(residual @ np.linalg.solve(innovation_cov, residual))
        return residual, innovation_cov, distance

    def update(self, measurement: np.ndarray | None, timestamp: float) -> TrackSnapshot:
        if measurement is None:
            if self.timestamp is None:
                return self.snapshot(False, timestamp)
            self.predict(timestamp)
            self.missed += 1
            self.status = TrackStatus.LOST if self.missed > self.config.max_missed else TrackStatus.COASTING
            return self.snapshot(False)
        measurement = np.asarray(measurement, np.float64).reshape(2)
        if not np.all(np.isfinite(measurement)):
            raise ValueError("measurement must be finite")
        if self.timestamp is None:
            self.state[:2] = measurement
            self.covariance = np.diag([self.config.measurement_noise] * 2 + [100.0, 100.0])
            self.timestamp = self.last_measurement_timestamp = timestamp
            self.hits, self.missed = 1, 0
            self.status = (
                TrackStatus.TRACKING
                if self.hits >= self.config.confirmation_hits
                else TrackStatus.TENTATIVE
            )
            return self.snapshot(False)

        was_lost = self.status == TrackStatus.LOST
        if timestamp < self.timestamp:
            raise ValueError("timestamps must be monotonic")
        self.predict(timestamp)
        residual, innovation_cov, distance = self.innovation(measurement)
        can_reacquire = (
            self.last_measurement_timestamp is not None
            and timestamp - self.last_measurement_timestamp <= self.config.reacquisition_timeout_s
        )
        if distance > self.config.mahalanobis_gate and not (was_lost and can_reacquire):
            self.missed += 1
            self.status = TrackStatus.LOST if self.missed > self.config.max_missed else TrackStatus.COASTING
            return self.snapshot(False)
        observation = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
        gain = self.covariance @ observation.T @ np.linalg.inv(innovation_cov)
        self.state += gain @ residual
        identity = np.eye(4)
        # Joseph form preserves symmetry/positive semidefiniteness.
        update = identity - gain @ observation
        noise = np.eye(2) * self.config.measurement_noise
        self.covariance = update @ self.covariance @ update.T + gain @ noise @ gain.T
        self.hits += 1
        self.missed = 0
        self.last_measurement_timestamp = timestamp
        self.status = TrackStatus.TRACKING if self.hits >= self.config.confirmation_hits else TrackStatus.TENTATIVE
        return self.snapshot(was_lost)

    def snapshot(self, reacquired: bool = False, timestamp: float | None = None) -> TrackSnapshot:
        return TrackSnapshot(
            self.timestamp if self.timestamp is not None else (timestamp or 0.0),
            self.state[:2].copy(),
            self.state[2:].copy(),
            self.covariance.copy(),
            self.status,
            self.hits,
            self.missed,
            reacquired,
        )


@dataclass
class TargetTrack:
    track_id: str
    profile_id: str
    tracker: KalmanTargetTracker
    last_area_px: float
    candidate: TargetCandidate | None = None


class MultiTargetTracker:
    """Greedy uncertainty-gated tracker map for simultaneous image targets."""

    def __init__(self, config: TrackerConfig | None = None, max_tracks: int = 16) -> None:
        self.config = config or TrackerConfig()
        self.max_tracks = max_tracks
        self.tracks: dict[str, TargetTrack] = {}

    def update(
        self, candidates: list[TargetCandidate], timestamp: float
    ) -> dict[str, TrackSnapshot]:
        unassigned = set(range(len(candidates)))
        snapshots: dict[str, TrackSnapshot] = {}
        for track_id in sorted(self.tracks):
            record = self.tracks[track_id]
            if record.tracker.timestamp is not None and timestamp > record.tracker.timestamp:
                record.tracker.predict(timestamp)
            choices: list[tuple[float, int]] = []
            for index in unassigned:
                candidate = candidates[index]
                if candidate.profile_id != record.profile_id:
                    continue
                _, _, distance = record.tracker.innovation(np.asarray(candidate.center))
                area_ratio = candidate.area_px / max(record.last_area_px, 1e-9)
                if (
                    distance <= self.config.mahalanobis_gate
                    and 0.25 <= area_ratio <= 4.0
                ):
                    choices.append((distance + abs(float(np.log(area_ratio))), index))
            if choices:
                _, index = min(choices)
                candidate = candidates[index]
                unassigned.remove(index)
                record.candidate = candidate
                record.last_area_px = candidate.area_px
                snapshots[track_id] = record.tracker.update(
                    np.asarray(candidate.center), timestamp
                )
            else:
                record.candidate = None
                snapshots[track_id] = record.tracker.update(None, timestamp)

        for index in sorted(unassigned):
            if len(self.tracks) >= self.max_tracks:
                break
            candidate = candidates[index]
            track_id = f"track-{uuid4().hex}"
            tracker = KalmanTargetTracker(self.config)
            snapshot = tracker.update(np.asarray(candidate.center), timestamp)
            self.tracks[track_id] = TargetTrack(
                track_id, candidate.profile_id, tracker, candidate.area_px, candidate
            )
            snapshots[track_id] = snapshot

        expired = [
            track_id for track_id, record in self.tracks.items()
            if record.tracker.status == TrackStatus.LOST
            and record.tracker.missed > self.config.max_missed + 2
        ]
        for track_id in expired:
            del self.tracks[track_id]
        return snapshots
