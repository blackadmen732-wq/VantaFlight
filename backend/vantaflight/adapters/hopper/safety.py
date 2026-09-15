"""Hopper safety supervisor — the final command arbiter."""
from __future__ import annotations

from typing import Optional

from .battery import HopperBatteryManager
from .errors import (
    HopperBatteryCritical,
    HopperCommandExpired,
    HopperLinkLost,
    HopperPreflightFailed,
    HopperUnsupportedCapability,
)
from .models import HopperCommand, HopperConnectionState

_ALWAYS_ALLOWED = frozenset({"land", "hold", "emergency_land"})
_LIVE_ONLY = frozenset({"takeoff", "arm", "disarm", "velocity", "position", "relative_move", "yaw"})


class HopperSafetySupervisor:
    """Single gate every outgoing Hopper command must pass through."""

    def __init__(self, battery_manager: Optional[HopperBatteryManager] = None) -> None:
        self._battery = battery_manager or HopperBatteryManager()
        self._hardware_mode_authorized = False
        self._preflight_passed = False

    def authorize_hardware_mode(self) -> None:
        self._hardware_mode_authorized = True

    def revoke_hardware_mode(self) -> None:
        self._hardware_mode_authorized = False
        self._preflight_passed = False

    def set_preflight_passed(self, passed: bool) -> None:
        self._preflight_passed = passed

    def check(
        self,
        command: HopperCommand,
        connection_state: HopperConnectionState,
        control_link_alive: bool,
    ) -> None:
        if command.expired:
            raise HopperCommandExpired(
                f"Command '{command.command_type}' expired before dispatch."
            )
        if command.command_type in _ALWAYS_ALLOWED:
            return
        if self._battery.should_land_now():
            raise HopperBatteryCritical(
                f"Battery critical ({self._battery.percentage:.0f}%); only hold/land are allowed."
            )
        if not control_link_alive:
            raise HopperLinkLost("Control link is not alive; command blocked.")
        if command.command_type in _LIVE_ONLY:
            if not self._hardware_mode_authorized:
                raise HopperUnsupportedCapability(
                    f"'{command.command_type}' requires explicit hardware authorization."
                )
            if not self._preflight_passed:
                raise HopperPreflightFailed(
                    f"'{command.command_type}' requires a passed preflight check."
                )
            if connection_state != HopperConnectionState.LIVE_CONTROL_READY:
                raise HopperUnsupportedCapability(
                    f"'{command.command_type}' requires LIVE_CONTROL_READY; current state is "
                    f"{connection_state.value}."
                )

    def run_preflight(
        self,
        camera_ok: bool,
        battery_pct: float,
        connection_state: HopperConnectionState,
        battery_known: bool = True,
    ) -> list[str]:
        """Validate hardware prerequisites. battery_known defaults True for old callers."""
        failures: list[str] = []
        if not camera_ok:
            failures.append("Camera link is not established.")
        if not battery_known:
            failures.append("Battery level is unavailable; physical flight is blocked.")
        elif battery_pct < self._battery._config.reserve_pct:
            failures.append(
                f"Battery too low to fly ({battery_pct:.0f}% < "
                f"{self._battery._config.reserve_pct:.0f}%)."
            )
        if connection_state in (
            HopperConnectionState.DISCONNECTED,
            HopperConnectionState.ERROR,
        ):
            failures.append("No healthy Hopper connection is available.")
        self._preflight_passed = len(failures) == 0
        return failures
