"""Hopper safety supervisor — the command arbiter gate.

Every outgoing flight command passes through this gate.  Priority:

  EMERGENCY (9)        > BATTERY_CRITICAL (8) > LINK_LOST (7)
  > STATE_INVALID (5)  > MISSION_ABORT (7)    > HOLD (3)
  > COLLISION_PROTECT  > MISSION_OBJECTIVE    > SPEED_OPTIMIZE
  > NORMAL_COMMAND (0)

Higher priority always wins.  Mission score / speed never overrides safety.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .battery import HopperBatteryManager
from .errors import (
    HopperBatteryCritical,
    HopperCommandExpired,
    HopperLinkLost,
    HopperPreflightFailed,
    HopperUnsupportedCapability,
)
from .models import HopperCommand, HopperConnectionState, SafetyPriority

logger = logging.getLogger(__name__)

# Command types that are always allowed regardless of mode (safety overrides).
_ALWAYS_ALLOWED = frozenset({"land", "hold", "emergency_land"})

# Command types that require active live-control capability.
_LIVE_ONLY = frozenset({"takeoff", "arm", "disarm", "velocity", "position", "relative_move", "yaw"})


class HopperSafetySupervisor:
    """Single gate all outgoing Hopper commands must pass through."""

    def __init__(self, battery_manager: Optional[HopperBatteryManager] = None) -> None:
        self._battery = battery_manager or HopperBatteryManager()
        self._hardware_mode_authorized: bool = False
        self._preflight_passed: bool = False

    def authorize_hardware_mode(self) -> None:
        """Operator explicitly authorized live flight commands."""
        self._hardware_mode_authorized = True

    def revoke_hardware_mode(self) -> None:
        self._hardware_mode_authorized = False

    def set_preflight_passed(self, passed: bool) -> None:
        self._preflight_passed = passed

    def check(
        self,
        command: HopperCommand,
        connection_state: HopperConnectionState,
        control_link_alive: bool,
    ) -> None:
        """Raise if the command must not be sent.

        This is called by HopperAdapter before dispatching any command.
        Callers should catch specific error types and respond accordingly.
        """
        # Expired commands are never sent — stale flight data causes accidents.
        if command.expired:
            raise HopperCommandExpired(
                f"Command '{command.command_type}' (id={command.command_id}) "
                f"expired {time.monotonic() - command.expires_at:.2f} s ago."
            )

        # Safety overrides: land/hold are always forwarded even in degraded state.
        if command.command_type in _ALWAYS_ALLOWED:
            return

        # Battery critical — refuse all non-safety commands.
        if self._battery.should_land_now():
            raise HopperBatteryCritical(
                f"Battery critical ({self._battery.percentage:.0f}%); "
                "only land/hold commands are accepted."
            )

        # Link must be up for flight commands.
        if not control_link_alive:
            raise HopperLinkLost(
                "Control link is not alive; cannot dispatch command."
            )

        # Live control commands need explicit operator authorization.
        if command.command_type in _LIVE_ONLY:
            if not self._hardware_mode_authorized:
                raise HopperUnsupportedCapability(
                    f"Command '{command.command_type}' requires hardware-mode "
                    "authorization; operator has not granted it."
                )
            if not self._preflight_passed:
                raise HopperPreflightFailed(
                    f"Command '{command.command_type}' requires a passed preflight check."
                )
            if connection_state not in (
                HopperConnectionState.LIVE_CONTROL_READY,
                HopperConnectionState.PROGRAM_READY,
            ):
                raise HopperUnsupportedCapability(
                    f"Command '{command.command_type}' is not available in "
                    f"connection state '{connection_state.value}'."
                )

    def run_preflight(
        self,
        camera_ok: bool,
        battery_pct: float,
        connection_state: HopperConnectionState,
    ) -> list[str]:
        """Run pre-flight checks and return a list of failures (empty = pass)."""
        failures: list[str] = []

        if not camera_ok:
            failures.append("Camera link is not established.")
        if battery_pct < self._battery._config.reserve_pct:
            failures.append(
                f"Battery too low to fly ({battery_pct:.0f}% < "
                f"{self._battery._config.reserve_pct:.0f}%)."
            )
        if connection_state == HopperConnectionState.DISCONNECTED:
            failures.append("No connection to Hopper.")

        self._preflight_passed = len(failures) == 0
        return failures
