"""Command safety rules.

The safety layer is the single gate every command passes through before it
reaches an adapter. It reasons only about normalized `Telemetry`, so the same
rules protect any drone. Rejections are returned as `SafetyViolation` with a
human-readable reason rather than raised, so callers can surface them in the UI
timeline.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Telemetry


@dataclass
class SafetyViolation:
    """Why a command was rejected."""

    command: str
    reason: str


class SafetyValidator:
    """Stateless validator: given current telemetry, may a command run?"""

    def check(self, command: str, telemetry: Telemetry) -> SafetyViolation | None:
        """Return a `SafetyViolation` if the command is unsafe, else None."""
        checker = getattr(self, f"_check_{command}", None)
        if checker is None:
            return SafetyViolation(command, f"unknown command '{command}'")
        return checker(telemetry)

    # -- per-command rules --------------------------------------------------
    def _check_arm(self, t: Telemetry) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("arm", "cannot arm while disconnected")
        if t.armed:
            return SafetyViolation("arm", "already armed")
        if not t.health_all_ok:
            return SafetyViolation("arm", "pre-arm health check failed")
        return None

    def _check_disarm(self, t: Telemetry) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("disarm", "cannot disarm while disconnected")
        if t.airborne:
            return SafetyViolation("disarm", "cannot disarm while airborne")
        return None

    def _check_takeoff(self, t: Telemetry) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("takeoff", "cannot take off while disconnected")
        if not t.armed:
            return SafetyViolation("takeoff", "cannot take off before arming")
        if t.airborne:
            return SafetyViolation("takeoff", "already airborne")
        return None

    def _check_hold(self, t: Telemetry) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("hold", "cannot hold while disconnected")
        if not t.airborne:
            return SafetyViolation("hold", "cannot hold while on the ground")
        return None

    def _check_land(self, t: Telemetry) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("land", "cannot land while disconnected")
        if not t.airborne:
            return SafetyViolation("land", "already on the ground")
        return None
