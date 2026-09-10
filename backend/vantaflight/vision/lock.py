"""Deterministic target-lock lifecycle state machine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LockState(str, Enum):
    SEARCHING = "searching"
    ACQUIRING = "acquiring"
    LOCKED = "locked"
    COASTING = "coasting"
    LOST = "lost"


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


class TargetLock:
    def __init__(self, config: LockConfig | None = None) -> None:
        self.config = config or LockConfig()
        if self.config.coast_misses > self.config.lost_misses:
            raise ValueError("coast_misses must not exceed lost_misses")
        self.state = LockState.SEARCHING
        self.hits = self.misses = 0

    def update(self, detected: bool, confidence: float) -> LockSnapshot:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        previous = self.state
        strong = detected and confidence >= self.config.acquire_confidence
        maintain = detected and confidence >= self.config.maintain_confidence
        if strong:
            self.hits += 1
            self.misses = 0
            self.state = LockState.LOCKED if self.hits >= self.config.acquire_hits else LockState.ACQUIRING
        elif maintain and self.state in (LockState.LOCKED, LockState.COASTING):
            self.misses = 0
            self.state = LockState.LOCKED
        else:
            self.hits = 0
            self.misses += 1
            if previous in (LockState.LOCKED, LockState.COASTING):
                self.state = (
                    LockState.LOST if self.misses >= self.config.lost_misses else LockState.COASTING
                )
            elif previous == LockState.LOST:
                self.state = LockState.LOST
            else:
                self.state = LockState.SEARCHING
        return LockSnapshot(self.state, confidence, self.hits, self.misses, self.state != previous)

    def reset(self) -> None:
        self.state = LockState.SEARCHING
        self.hits = self.misses = 0
