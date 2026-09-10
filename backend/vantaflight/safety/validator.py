"""Command safety rules.

The safety layer is the single gate every command passes through before it
reaches an adapter. It reasons only about normalized `Telemetry`, so the same
rules protect any drone. Rejections are returned as `SafetyViolation` with a
human-readable reason rather than raised, so callers can surface them in the UI
timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from ..models import Telemetry


@dataclass
class SafetyViolation:
    """Why a command was rejected."""

    command: str
    reason: str


class SafetyValidator:
    """Stateless validator: given current telemetry, may a command run?"""

    def check(
        self,
        command: str,
        telemetry: Telemetry,
        **command_context: Any,
    ) -> SafetyViolation | None:
        """Return a `SafetyViolation` if the command is unsafe, else None."""
        checker = getattr(self, f"_check_{command}", None)
        if checker is None:
            return SafetyViolation(command, f"unknown command '{command}'")
        return checker(telemetry, **command_context)

    # -- per-command rules --------------------------------------------------
    def _check_arm(self, t: Telemetry, **_: Any) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("arm", "cannot arm while disconnected")
        if t.armed:
            return SafetyViolation("arm", "already armed")
        return None

    def _check_disarm(self, t: Telemetry, **_: Any) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("disarm", "cannot disarm while disconnected")
        if t.airborne:
            return SafetyViolation("disarm", "cannot disarm while airborne")
        return None

    def _check_takeoff(
        self,
        t: Telemetry,
        *,
        target_altitude_m: float = 5.0,
        max_altitude_m: float = 120.0,
        **_: Any,
    ) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("takeoff", "cannot take off while disconnected")
        if not t.armed:
            return SafetyViolation("takeoff", "cannot take off before arming")
        if t.airborne:
            return SafetyViolation("takeoff", "already airborne")
        if isinstance(target_altitude_m, bool):
            return SafetyViolation("takeoff", "target altitude must be a finite number")
        try:
            target = float(target_altitude_m)
        except (TypeError, ValueError):
            return SafetyViolation("takeoff", "target altitude must be a finite number")
        if not math.isfinite(target):
            return SafetyViolation("takeoff", "target altitude must be a finite number")
        if target <= 0.15:
            return SafetyViolation("takeoff", "target altitude must be above 0.15 m")
        if target > max_altitude_m:
            return SafetyViolation(
                "takeoff",
                f"target altitude exceeds adapter limit of {max_altitude_m:g} m",
            )
        return None

    def _check_hold(self, t: Telemetry, **_: Any) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("hold", "cannot hold while disconnected")
        if not t.airborne:
            return SafetyViolation("hold", "cannot hold while on the ground")
        return None

    def _check_land(self, t: Telemetry, **_: Any) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("land", "cannot land while disconnected")
        if not t.airborne:
            return SafetyViolation("land", "already on the ground")
        return None
