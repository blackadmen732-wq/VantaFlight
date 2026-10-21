"""High-rate local vehicle state for closed-loop autonomy.

`Telemetry` is the operator-facing, 10 Hz, drone-agnostic snapshot. The
autonomy loop needs more: full 3-D velocity, attitude, and *when* the sample
was received, so it can refuse to act on stale state. Adapters that support
offboard control expose it through ``local_state()``.

Frame: local ENU metres relative to home (x east, y north, z up). Yaw is
counter-clockwise from east; pitch uses the aerospace convention (nose up
positive).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.clock import ClockDomain, TimeStamp


def _vec(value) -> np.ndarray:
    out = np.array(value, dtype=np.float64, copy=True).reshape(3)
    if not np.all(np.isfinite(out)):
        raise ValueError("local state vectors must be finite")
    return out


@dataclass(frozen=True)
class LocalState:
    #: Host monotonic seconds (the adapter's clock) at which this sample arrived.
    received_at: float
    position_enu: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_enu: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw_enu_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    #: False until the vehicle has reported a local position at least once.
    valid: bool = False
    #: Host monotonic seconds at which the vehicle *measured* this state, when
    #: the source reports it (converted through VantaClock). None = unknown,
    #: and the receive time stands in for it (see ``capture_known``).
    capture_s: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_enu", _vec(self.position_enu))
        object.__setattr__(self, "velocity_enu", _vec(self.velocity_enu))
        if self.capture_s is not None and self.capture_s > self.received_at:
            raise ValueError("a state cannot be captured after it was received")

    @property
    def capture_known(self) -> bool:
        return self.capture_s is not None

    @property
    def captured_at(self) -> TimeStamp:
        """When the state was true, on host monotonic time (receive time if unknown)."""
        seconds = self.capture_s if self.capture_s is not None else self.received_at
        return TimeStamp.from_seconds(seconds, ClockDomain.HOST_MONOTONIC)

    @property
    def received_at_ts(self) -> TimeStamp:
        return TimeStamp.from_seconds(self.received_at, ClockDomain.HOST_MONOTONIC)

    def age_s(self, now: float) -> float:
        """Age of the *measurement* (capture time when known, else receipt)."""
        reference = self.capture_s if self.capture_s is not None else self.received_at
        return max(0.0, float(now) - reference)

    def to_dict(self) -> dict:
        return {
            "position_enu": [round(float(v), 3) for v in self.position_enu],
            "velocity_enu": [round(float(v), 3) for v in self.velocity_enu],
            "yaw_enu_deg": round(self.yaw_enu_deg, 2),
            "pitch_deg": round(self.pitch_deg, 2),
            "roll_deg": round(self.roll_deg, 2),
            "valid": self.valid,
        }
