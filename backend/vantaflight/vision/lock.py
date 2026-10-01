"""Deterministic target-lock lifecycle state machine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LockState(str, Enum):
    SEARCHING = "SEARCHING"
    CANDIDATE = "CANDIDATE"
    DETECTED = "DETECTED"
    CONFIRMED = "CONFIRMED"
    TRACKED = "TRACKED"
    POSE_LOCKED = "POSE_LOCKED"
    PREDICTIVE_LOCK = "PREDICTIVE_LOCK"
    RACE_LOCK = "RACE_LOCK"
    DEGRADED = "DEGRADED"
    LOST = "LOST"
    # V0.5 preview compatibility aliases.
    ACQUIRING = CANDIDATE
    LOCKED = CONFIRMED
    COASTING = DEGRADED


@dataclass(frozen=True)
class LockConfig:
    acquire_confidence: float = 0.65
    maintain_confidence: float = 0.45
    acquire_hits: int = 3
    coast_misses: int = 2
    lost_misses: int = 4


@dataclass(frozen=True)
class LockSnapshot:
    state: LockState
    confidence: float
    consecutive_hits: int
    consecutive_misses: int
    changed: bool
    evidence: tuple[str, ...] = ()


class TargetLock:
    def __init__(self, config: LockConfig | None = None) -> None:
        self.config = config or LockConfig()
        if self.config.coast_misses > self.config.lost_misses:
            raise ValueError("coast_misses must not exceed lost_misses")
        self.state = LockState.SEARCHING
        self.hits = self.misses = 0

    def update(
        self,
        detected: bool,
        confidence: float,
        *,
        track_confirmed: bool = False,
        pose_valid: bool = False,
        predictive: bool = False,
        race_ready: bool = False,
        evidence: tuple[str, ...] = (),
    ) -> LockSnapshot:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        previous = self.state
        strong = detected and confidence >= self.config.acquire_confidence
        maintain = detected and confidence >= self.config.maintain_confidence
        if strong:
            self.hits += 1
            self.misses = 0
            confirmed = (
                self.hits >= self.config.acquire_hits
                or previous in (
                    LockState.CONFIRMED, LockState.TRACKED, LockState.POSE_LOCKED,
                    LockState.PREDICTIVE_LOCK, LockState.RACE_LOCK, LockState.DEGRADED,
                )
            )
            if confirmed and race_ready and predictive and pose_valid and track_confirmed:
                self.state = LockState.RACE_LOCK
            elif confirmed and predictive and pose_valid and track_confirmed:
                self.state = LockState.PREDICTIVE_LOCK
            elif confirmed and pose_valid and track_confirmed:
                self.state = LockState.POSE_LOCKED
            elif confirmed and track_confirmed:
                self.state = LockState.TRACKED
            elif self.hits >= self.config.acquire_hits:
                self.state = LockState.CONFIRMED
            elif self.hits >= 2:
                self.state = LockState.DETECTED
            else:
                self.state = LockState.CANDIDATE
        elif maintain and self.state not in (
            LockState.SEARCHING, LockState.CANDIDATE, LockState.DETECTED, LockState.LOST
        ):
            self.misses = 0
            # Maintain the strongest evidence-backed state already reached.
        else:
            self.hits = 0
            self.misses += 1
            if previous in (
                LockState.CONFIRMED, LockState.TRACKED, LockState.POSE_LOCKED,
                LockState.PREDICTIVE_LOCK, LockState.RACE_LOCK, LockState.DEGRADED,
            ):
                if self.misses >= self.config.lost_misses:
                    self.state = LockState.LOST
                elif self.misses >= self.config.coast_misses:
                    self.state = LockState.DEGRADED
                else:
                    self.state = previous
            elif previous == LockState.LOST:
                self.state = LockState.LOST
            else:
                self.state = LockState.SEARCHING
        return LockSnapshot(
            self.state, confidence, self.hits, self.misses, self.state != previous, evidence
        )

    def reset(self) -> None:
        self.state = LockState.SEARCHING
        self.hits = self.misses = 0
