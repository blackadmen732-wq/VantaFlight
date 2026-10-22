"""Deterministic race-mode state machine."""
from __future__ import annotations

from enum import Enum


class RaceState(str, Enum):
    IDLE = "IDLE"
    SEARCH = "SEARCH"
    ACQUIRE = "ACQUIRE"
    LOCK = "LOCK"
    ALIGN = "ALIGN"
    ACCELERATE = "ACCELERATE"
    PASS = "PASS"
    NEXT = "NEXT"
    RECOVER = "RECOVER"
    COMPLETE = "COMPLETE"


class RaceEvent(str, Enum):
    START = "START"
    TARGET_SEEN = "TARGET_SEEN"
    TARGET_ACQUIRED = "TARGET_ACQUIRED"
    TARGET_LOCKED = "TARGET_LOCKED"
    ALIGNED = "ALIGNED"
    AT_SPEED = "AT_SPEED"
    GATE_PASSED = "GATE_PASSED"
    ADVANCE = "ADVANCE"
    TARGET_LOST = "TARGET_LOST"
    RECOVERED = "RECOVERED"
    COURSE_COMPLETE = "COURSE_COMPLETE"
    RESET = "RESET"


_TRANSITIONS: dict[tuple[RaceState, RaceEvent], RaceState] = {
    (RaceState.IDLE, RaceEvent.START): RaceState.SEARCH,
    (RaceState.SEARCH, RaceEvent.TARGET_SEEN): RaceState.ACQUIRE,
    (RaceState.ACQUIRE, RaceEvent.TARGET_ACQUIRED): RaceState.LOCK,
    (RaceState.LOCK, RaceEvent.TARGET_LOCKED): RaceState.ALIGN,
    (RaceState.ALIGN, RaceEvent.ALIGNED): RaceState.ACCELERATE,
    (RaceState.ACCELERATE, RaceEvent.AT_SPEED): RaceState.PASS,
    (RaceState.PASS, RaceEvent.GATE_PASSED): RaceState.NEXT,
    (RaceState.NEXT, RaceEvent.ADVANCE): RaceState.SEARCH,
    (RaceState.NEXT, RaceEvent.COURSE_COMPLETE): RaceState.COMPLETE,
    (RaceState.RECOVER, RaceEvent.RECOVERED): RaceState.SEARCH,
}


class RaceStateMachine:
    """Small explicit machine whose output depends only on state and event."""

    def __init__(self, initial_state: RaceState = RaceState.IDLE) -> None:
        self._state = RaceState(initial_state)

    @property
    def state(self) -> RaceState:
        return self._state

    def transition(self, event: RaceEvent) -> RaceState:
        event = RaceEvent(event)
        if event is RaceEvent.RESET:
            self._state = RaceState.IDLE
            return self._state
        if event is RaceEvent.TARGET_LOST and self._state not in {
            RaceState.IDLE,
            RaceState.COMPLETE,
        }:
            self._state = RaceState.RECOVER
            return self._state
        key = (self._state, event)
        if key not in _TRANSITIONS:
            raise ValueError(f"event {event.value} is invalid in state {self._state.value}")
        self._state = _TRANSITIONS[key]
        return self._state
