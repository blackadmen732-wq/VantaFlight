"""Command safety rules.

The safety layer is the single gate every command passes through before it
reaches an adapter. It reasons only about normalized `Telemetry`, so the same
rules protect any drone. Rejections are returned as `SafetyViolation` with a
human-readable reason rather than raised, so callers can surface them in the UI
timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import Telemetry
from .geofence import Geofence

if TYPE_CHECKING:
    from ..routing import Airspace


@dataclass
class SafetyViolation:
    """Why a command was rejected."""

    command: str
    reason: str


class SafetyValidator:
    """Stateless validator: given current telemetry, may a command run?

    Commands that carry a target (``takeoff`` altitude, ``goto`` position) are
    also checked against the geofence, so an out-of-bounds request is refused
    before it ever reaches the aircraft.
    """

    def __init__(self, geofence: Geofence | None = None, airspace: "Airspace | None" = None) -> None:
        self.geofence = geofence or Geofence()
        #: No-fly zones. A goto is refused if its target is inside a zone or
        #: the straight flight there would enter one (the zone itself, not its
        #: planning margin, so normal tracking error on a planned route is fine).
        self.airspace = airspace

    def check(
        self, command: str, telemetry: Telemetry, **params: float
    ) -> SafetyViolation | None:
        """Return a `SafetyViolation` if the command is unsafe, else None."""
        checker = getattr(self, f"_check_{command}", None)
        if checker is None:
            return SafetyViolation(command, f"unknown command '{command}'")
        return checker(telemetry, **params)

    # -- per-command rules --------------------------------------------------
    def _check_arm(self, t: Telemetry, **_) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("arm", "cannot arm while disconnected")
        if t.armed:
            return SafetyViolation("arm", "already armed")
        if not t.health_all_ok:
            return SafetyViolation("arm", "pre-arm health check failed")
        return None

    def _check_disarm(self, t: Telemetry, **_) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("disarm", "cannot disarm while disconnected")
        if t.airborne:
            return SafetyViolation("disarm", "cannot disarm while airborne")
        return None

    def _check_takeoff(
        self, t: Telemetry, target_altitude_m: float = 5.0, **_
    ) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("takeoff", "cannot take off while disconnected")
        if not t.armed:
            return SafetyViolation("takeoff", "cannot take off before arming")
        if t.airborne:
            return SafetyViolation("takeoff", "already airborne")
        if target_altitude_m <= 0:
            return SafetyViolation("takeoff", "takeoff altitude must be above 0 m")
        breach = self.geofence.violation(t.x, t.y, target_altitude_m)
        if breach:
            return SafetyViolation("takeoff", f"target outside geofence: {breach}")
        return None

    def _check_goto(
        self, t: Telemetry, x: float = 0.0, y: float = 0.0, altitude: float = 0.0, **_
    ) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("goto", "cannot fly to a waypoint while disconnected")
        if not t.armed or not t.airborne:
            return SafetyViolation("goto", "take off before flying to a waypoint")
        if not t.position_available:
            return SafetyViolation("goto", "aircraft position unknown; cannot fly to a waypoint safely")
        if altitude < 1.0:
            return SafetyViolation("goto", "waypoint altitude must be at least 1 m")
        breach = self.geofence.violation(x, y, altitude)
        if breach:
            return SafetyViolation("goto", f"waypoint outside geofence: {breach}")
        if self.airspace is not None and self.airspace.zones:
            zone = self.airspace.zone_at((x, y), margin=0.0)
            if zone is not None:
                return SafetyViolation("goto", f"waypoint is inside no-fly zone '{zone.name}'")
            zone = self.airspace.blocking_zone((t.x, t.y), (x, y), margin=0.0)
            if zone is not None:
                return SafetyViolation("goto", f"flying straight there would enter no-fly zone '{zone.name}'")
        return None

    def _check_return_home(self, t: Telemetry, **_) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("return_home", "cannot return home while disconnected")
        if not t.airborne:
            return SafetyViolation("return_home", "already on the ground")
        return None

    def _check_hold(self, t: Telemetry, **_) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("hold", "cannot hold while disconnected")
        if not t.airborne:
            return SafetyViolation("hold", "cannot hold while on the ground")
        return None

    def _check_land(self, t: Telemetry, **_) -> SafetyViolation | None:
        if not t.connected:
            return SafetyViolation("land", "cannot land while disconnected")
        if not t.airborne:
            return SafetyViolation("land", "already on the ground")
        return None
