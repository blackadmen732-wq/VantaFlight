"""Automatic failsafes — the layer that acts when nobody else does.

`SafetyValidator` decides whether a *requested* command may run. The
`FailsafeGuardian` is the other half: every telemetry sample it asks "is the
aircraft in trouble right now?" and, if so, says what to do about it. It is
pure logic over normalized `Telemetry`, so it protects every adapter equally
and is unit-testable without a drone.

Rules, highest priority first (only while armed and airborne):

1. Battery at or below the critical level      -> LAND immediately.
2. Battery at or below the low level           -> RETURN home.
3. Outside the geofence                        -> RETURN home.
4. Telemetry older than the stale timeout      -> HOLD position.

Each rule fires once per flight session (it is *latched*) so the guardian
never spams commands at a drone that is already responding. The link rule is
the exception: it re-arms once fresh telemetry returns, because a link can
degrade more than once in a flight.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ..config import BATTERY_CRITICAL_PCT, BATTERY_LOW_PCT, TELEMETRY_STALE_TIMEOUT
from ..models import FlightMode, Telemetry
from .geofence import Geofence


class FailsafeAction(str, enum.Enum):
    HOLD = "hold"
    RETURN = "return"
    LAND = "land"


@dataclass(frozen=True)
class FailsafeConfig:
    battery_low_pct: float = BATTERY_LOW_PCT
    battery_critical_pct: float = BATTERY_CRITICAL_PCT
    link_stale_s: float = TELEMETRY_STALE_TIMEOUT
    geofence: Geofence = field(default_factory=Geofence)

    def to_dict(self) -> dict:
        return {
            "battery_low_pct": self.battery_low_pct,
            "battery_critical_pct": self.battery_critical_pct,
            "link_stale_s": self.link_stale_s,
            "geofence": self.geofence.to_dict(),
        }


@dataclass(frozen=True)
class FailsafeTrigger:
    reason: str  # battery_critical | battery_low | geofence | link_degraded
    action: FailsafeAction
    message: str

    def to_dict(self) -> dict:
        return {"reason": self.reason, "action": self.action.value, "message": self.message}


# Modes in which the aircraft is already doing what a RETURN would ask for.
_RETURNING_OR_LANDING = {FlightMode.RETURNING, FlightMode.LANDING}


class FailsafeGuardian:
    """Watches telemetry and decides when the aircraft must be protected."""

    def __init__(self, config: FailsafeConfig | None = None) -> None:
        self.config = config or FailsafeConfig()
        self._fired: set[str] = set()
        self.last_trigger: FailsafeTrigger | None = None

    def reset(self) -> None:
        """Re-arm every rule (call at the start of each flight session)."""
        self._fired.clear()
        self.last_trigger = None

    def evaluate(self, t: Telemetry) -> FailsafeTrigger | None:
        """Return the failsafe to execute now, or None if all is well."""
        cfg = self.config

        # The link rule re-arms as soon as telemetry is fresh again.
        if t.link_age_s <= cfg.link_stale_s:
            self._fired.discard("link_degraded")

        if not (t.connected and t.armed and t.airborne):
            return None

        # Rules only act on measured values: an adapter that cannot report a
        # battery level or position (unknown, not zero) must not trigger them.
        battery_known = t.battery_available
        if battery_known and t.battery_percentage <= cfg.battery_critical_pct:
            return self._fire(
                "battery_critical", FailsafeAction.LAND,
                f"battery critical ({t.battery_percentage:.0f}%), landing now",
            )

        if battery_known and t.battery_percentage <= cfg.battery_low_pct and t.flight_mode not in _RETURNING_OR_LANDING:
            return self._fire(
                "battery_low", FailsafeAction.RETURN,
                f"battery low ({t.battery_percentage:.0f}%), returning home",
            )

        breach = cfg.geofence.violation(t.x, t.y, t.altitude) if t.position_available else None
        if breach and t.flight_mode not in _RETURNING_OR_LANDING:
            return self._fire(
                "geofence", FailsafeAction.RETURN, f"geofence breach: {breach}, returning home",
            )

        if t.link_age_s > cfg.link_stale_s:
            return self._fire(
                "link_degraded", FailsafeAction.HOLD,
                f"no fresh telemetry for {t.link_age_s:.1f}s, holding position",
            )
        return None

    def _fire(self, reason: str, action: FailsafeAction, message: str) -> FailsafeTrigger | None:
        if reason in self._fired:
            return None
        self._fired.add(reason)
        self.last_trigger = FailsafeTrigger(reason, action, message)
        return self.last_trigger

    def status(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "fired": sorted(self._fired),
            "last_trigger": self.last_trigger.to_dict() if self.last_trigger else None,
        }
