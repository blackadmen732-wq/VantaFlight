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


def _vec(value) -> np.ndarray:
    out = np.array(value, dtype=np.float64, copy=True).reshape(3)
    if not np.all(np.isfinite(out)):
        raise ValueError("local state vectors must be finite")
    return out


@dataclass(frozen=True)
class LocalState:
    #: Clock time (the adapter's monotonic clock) at which this sample arrived.
    received_at: float
    position_enu: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_enu: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw_enu_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    #: False until the vehicle has reported a local position at least once.
    valid: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_enu", _vec(self.position_enu))
        object.__setattr__(self, "velocity_enu", _vec(self.velocity_enu))

    def age_s(self, now: float) -> float:
        return max(0.0, float(now) - self.received_at)

    def to_dict(self) -> dict:
        return {
            "position_enu": [round(float(v), 3) for v in self.position_enu],
            "velocity_enu": [round(float(v), 3) for v in self.velocity_enu],
            "yaw_enu_deg": round(self.yaw_enu_deg, 2),
            "pitch_deg": round(self.pitch_deg, 2),
            "roll_deg": round(self.roll_deg, 2),
            "valid": self.valid,
        }
