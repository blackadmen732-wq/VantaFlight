"""A fully simulated drone.

`MockDroneAdapter` implements the universal `DroneAdapter` contract with a
lightweight physics model: altitude ramps toward a target at a fixed climb
rate, the battery drains over time, and modes transition automatically
(TAKEOFF -> HOLD once the target is reached, LANDING -> disarmed once on the
ground). It has no external dependencies, so it runs anywhere.
"""
from __future__ import annotations

import time
from typing import Callable

from ..models import (
    Capabilities,
    ConnectionQuality,
    FlightMode,
    Telemetry,
)


class DroneError(RuntimeError):
    """Low-level error raised by an adapter (e.g. command sent while offline)."""


class MockDroneAdapter:
    """Simulated drone with a simple, deterministic physics tick."""

    CLIMB_RATE_MPS = 1.5
    LANDING_RATE_MPS = 1.2
    GROUND_EPSILON_M = 0.15
    ARRIVAL_EPSILON_M = 0.1
    BATTERY_DRAIN_IDLE = 0.02  # percent per second, connected & idle
    BATTERY_DRAIN_FLYING = 0.15  # percent per second while airborne

    def __init__(
        self,
        adapter_id: str = "mock-0",
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter_id = adapter_id
        self._now = time_source
        self._connected = False
        self._armed = False
        self._mode = FlightMode.IDLE
        self._altitude = 0.0
        self._x = 0.0
        self._y = 0.0
        self._heading = 90.0
        self._velocity = 0.0
        self._battery = 100.0
        self._target_altitude = 0.0
        self._last_tick = self._now()

    # -- link ---------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        self._last_tick = self._now()

    async def disconnect(self) -> None:
        self._connected = False

    def force_connection_loss(self) -> None:
        """Simulate an abrupt link drop (radio/USB unplug, out of range).

        Unlike `disconnect()`, this models an *unexpected* loss with no clean
        shutdown handshake. The onboard state is preserved so a later
        reconnect can resume observing it.
        """
        self._connected = False

    # -- commands -----------------------------------------------------------
    async def arm(self) -> None:
        self._require_connected()
        self._tick()
        self._armed = True

    async def disarm(self) -> None:
        self._require_connected()
        self._tick()
        self._armed = False

    async def takeoff(self, target_altitude_m: float = 5.0) -> None:
        self._require_connected()
        self._tick()
        self._target_altitude = target_altitude_m
        self._mode = FlightMode.TAKEOFF

    async def hold(self) -> None:
        self._require_connected()
        self._tick()  # settle current altitude before latching the hold target
        self._target_altitude = self._altitude
        self._mode = FlightMode.HOLD

    async def land(self) -> None:
        self._require_connected()
        self._tick()
        self._target_altitude = 0.0
        self._mode = FlightMode.LANDING

    # -- telemetry ----------------------------------------------------------
    def get_telemetry(self) -> Telemetry:
        self._tick()
        return Telemetry(
            timestamp=time.time(),
            connected=self._connected,
            armed=self._armed,
            flight_mode=self._mode,
            x=round(self._x, 3),
            y=round(self._y, 3),
            z=round(self._altitude, 3),
            altitude=round(self._altitude, 3),
            velocity=round(self._velocity, 3),
            heading=round(self._heading, 1),
            battery_percentage=round(self._battery, 2),
            connection_quality=(
                ConnectionQuality.EXCELLENT
                if self._connected
                else ConnectionQuality.NONE
            ),
        )

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            name="Mock Drone (Simulator)",
            adapter_type="mock",
            is_simulated=True,
            supports_gps=False,
            supported_capabilities=[
                "arm", "takeoff", "land", "hold",
                "position", "velocity", "heading", "battery", "simulation",
            ],
        )

    # -- physics ------------------------------------------------------------
    def _tick(self) -> None:
        """Advance the simulation to now. Safe to call at any cadence."""
        now = self._now()
        dt = now - self._last_tick
        self._last_tick = now
        if dt <= 0:
            return

        if not self._connected:
            self._velocity = 0.0
            return

        # Battery drain.
        airborne = self._altitude > self.GROUND_EPSILON_M
        drain = self.BATTERY_DRAIN_FLYING if airborne else self.BATTERY_DRAIN_IDLE
        self._battery = max(0.0, self._battery - drain * dt)

        # Altitude control toward target.
        delta = self._target_altitude - self._altitude
        if abs(delta) <= self.ARRIVAL_EPSILON_M:
            self._altitude = self._target_altitude
            self._velocity = 0.0
            self._settle_mode()
            return

        rate = self.CLIMB_RATE_MPS if delta > 0 else self.LANDING_RATE_MPS
        step = rate * dt
        if step >= abs(delta):
            self._altitude = self._target_altitude
        else:
            self._altitude += step if delta > 0 else -step
        self._velocity = rate if delta > 0 else -rate
        self._settle_mode()

    def _settle_mode(self) -> None:
        """Apply automatic mode/arm transitions once a target is reached."""
        at_target = abs(self._target_altitude - self._altitude) <= self.ARRIVAL_EPSILON_M
        if not at_target:
            return
        if self._mode == FlightMode.TAKEOFF:
            self._mode = FlightMode.HOLD
        elif self._mode == FlightMode.LANDING and self._altitude <= self.GROUND_EPSILON_M:
            self._altitude = 0.0
            self._mode = FlightMode.IDLE
            self._armed = False  # auto-disarm on the ground

    def _require_connected(self) -> None:
        if not self._connected:
            raise DroneError("adapter is not connected")
