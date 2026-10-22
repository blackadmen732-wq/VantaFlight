"""A fully simulated drone.

`MockDroneAdapter` implements the universal `DroneAdapter` contract with a
lightweight kinematic model:

* altitude ramps toward a target at a fixed climb/descent rate,
* the aircraft flies horizontally toward a goto target at the requested speed,
* the battery drains over time (faster while airborne),
* modes transition automatically (TAKEOFF -> HOLD at altitude, MISSION -> HOLD
  on arrival, RETURNING -> LANDING over home, LANDING -> disarmed on the ground),
* in OFFBOARD mode it tracks a position + velocity setpoint stream with a
  second-order (acceleration-limited) model, and falls back to HOLD if the
  stream stops for ``OFFBOARD_TIMEOUT_S`` — like PX4's offboard-loss failsafe.

Positions are local ENU metres relative to home: ``x`` east, ``y`` north.
It has no external dependencies, so it runs anywhere. `inject_fault` lets the
Simulation Lab rehearse failsafes (low battery, a stalled link) safely.
"""
from __future__ import annotations

import math
import time
from typing import Callable

import numpy as np

from ..config import RTL_ALTITUDE_M
from ..models import (
    Capabilities,
    ConnectionQuality,
    FlightMode,
    LocalState,
    Telemetry,
)


class DroneError(RuntimeError):
    """Low-level error raised by an adapter (e.g. command sent while offline)."""


class MockDroneAdapter:
    """Simulated drone with a simple, deterministic physics tick."""

    CLIMB_RATE_MPS = 1.5
    LANDING_RATE_MPS = 1.2
    DEFAULT_SPEED_MPS = 5.0
    MAX_SPEED_MPS = 15.0
    GROUND_EPSILON_M = 0.15
    ARRIVAL_EPSILON_M = 0.1
    BATTERY_DRAIN_IDLE = 0.02  # percent per second, connected & idle
    BATTERY_DRAIN_FLYING = 0.15  # percent per second while airborne

    # Offboard tracking (roughly a small racing quad under PX4's controller).
    OFFBOARD_KP = 1.8  # 1/s^2 position gain
    OFFBOARD_KV = 2.6  # 1/s velocity gain
    OFFBOARD_MAX_ACCEL = 6.0  # m/s^2
    OFFBOARD_MAX_YAW_RATE = 120.0  # deg/s
    OFFBOARD_TIMEOUT_S = 1.0
    GRAVITY = 9.81

    FAULT_KINDS = ("battery", "link_stall")

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
        self._ground_speed = 0.0
        self._battery = 100.0
        self._target_altitude = 0.0
        # Horizontal target; None means "stay where you are".
        self._target_xy: tuple[float, float] | None = None
        self._speed = self.DEFAULT_SPEED_MPS
        self._last_tick = self._now()
        self._stall_until: float | None = None
        self._stall_started: float = 0.0
        self._stalled_snapshot: Telemetry | None = None
        self._stalled_local: LocalState | None = None
        # 3-D ENU velocity and attitude (used by local_state / offboard).
        self._vel = np.zeros(3)
        self._pitch = 0.0
        self._offboard = False
        self._sp_pos = np.zeros(3)
        self._sp_vel = np.zeros(3)
        self._sp_yaw_enu = 0.0
        self._last_sp_time = 0.0
        self.offboard_setpoints = 0

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

    def inject_fault(self, kind: str, value: float) -> str:
        """Rehearse a failure in simulation. Returns a description of it.

        * ``battery``: set the battery to ``value`` percent.
        * ``link_stall``: freeze telemetry for ``value`` seconds, as if the
          radio link stopped delivering messages while the aircraft flies on.
        """
        self._tick()
        if kind == "battery":
            self._battery = max(0.0, min(100.0, value))
            return f"battery set to {self._battery:.0f}%"
        if kind == "link_stall":
            if value <= 0:
                raise ValueError("link_stall duration must be positive")
            self._stall_started = self._now()
            self._stall_until = self._stall_started + value
            self._stalled_snapshot = None
            self._stalled_local = None
            return f"telemetry link stalled for {value:.1f}s"
        raise ValueError(f"unknown fault '{kind}'; expected one of {self.FAULT_KINDS}")

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
        self._offboard = False
        self._target_altitude = target_altitude_m
        self._target_xy = None
        self._mode = FlightMode.TAKEOFF

    async def hold(self) -> None:
        self._require_connected()
        self._tick()  # settle current position before latching the hold target
        self._offboard = False
        self._target_altitude = self._altitude
        self._target_xy = None
        self._mode = FlightMode.HOLD

    async def land(self) -> None:
        self._require_connected()
        self._tick()
        self._offboard = False
        self._target_altitude = 0.0
        self._target_xy = None
        self._mode = FlightMode.LANDING

    async def goto(
        self, x: float, y: float, altitude: float, speed_m_s: float | None = None
    ) -> None:
        self._require_connected()
        self._tick()
        self._offboard = False
        if speed_m_s is not None:
            self._speed = max(0.5, min(self.MAX_SPEED_MPS, speed_m_s))
        self._target_xy = (x, y)
        self._target_altitude = altitude
        self._mode = FlightMode.MISSION

    async def return_home(self) -> None:
        self._require_connected()
        self._tick()
        self._offboard = False
        self._target_xy = (0.0, 0.0)
        self._target_altitude = max(self._altitude, RTL_ALTITUDE_M)
        self._speed = self.DEFAULT_SPEED_MPS
        self._mode = FlightMode.RETURNING

    # -- offboard -------------------------------------------------------------
    @property
    def offboard_active(self) -> bool:
        return self._offboard and self._mode == FlightMode.OFFBOARD

    async def start_offboard(
        self, north_m: float, east_m: float, down_m: float, yaw_deg: float,
    ) -> None:
        """Enter offboard mode holding the given NED position (like PX4,
        a setpoint must exist before offboard can start)."""
        self._require_connected()
        self._tick()
        if not self._armed or self._altitude <= self.GROUND_EPSILON_M:
            raise DroneError("offboard requires an armed, airborne aircraft")
        self._sp_pos = np.array([east_m, north_m, -down_m], dtype=np.float64)
        self._sp_vel = np.zeros(3)
        self._sp_yaw_enu = 90.0 - yaw_deg
        self._last_sp_time = self._now()
        self._offboard = True
        self._target_xy = None
        self._mode = FlightMode.OFFBOARD

    async def stop_offboard(self) -> None:
        """Leave offboard: hold the current position (PX4 switches to HOLD)."""
        self._require_connected()
        self._tick()
        if self._offboard:
            self._offboard = False
            self._target_altitude = self._altitude
            self._target_xy = None
            self._mode = FlightMode.HOLD

    async def set_position_velocity_ned(
        self,
        position_ned: tuple[float, float, float],
        velocity_ned: tuple[float, float, float],
        yaw_deg: float,
    ) -> None:
        self._require_connected()
        self._tick()
        if not self._offboard:
            raise DroneError("offboard mode is not active")
        n, e, d = (float(v) for v in position_ned)
        vn, ve, vd = (float(v) for v in velocity_ned)
        self._sp_pos = np.array([e, n, -d])
        self._sp_vel = np.array([ve, vn, -vd])
        self._sp_yaw_enu = 90.0 - float(yaw_deg)
        self._last_sp_time = self._now()
        self.offboard_setpoints += 1

    def local_state(self) -> LocalState:
        self._tick()
        now = self._now()
        if self._stall_until is not None and now < self._stall_until and self._connected:
            if self._stalled_local is None:
                self._stalled_local = self._local_snapshot(self._stall_started)
            return self._stalled_local
        return self._local_snapshot(now)

    def _local_snapshot(self, received_at: float) -> LocalState:
        return LocalState(
            received_at=received_at,
            position_enu=(self._x, self._y, self._altitude),
            velocity_enu=self._vel,
            yaw_enu_deg=(90.0 - self._heading + 180.0) % 360.0 - 180.0,
            pitch_deg=self._pitch,
            valid=self._connected,
        )

    # -- telemetry ----------------------------------------------------------
    def get_telemetry(self) -> Telemetry:
        self._tick()
        now = self._now()
        if self._stall_until is not None:
            if now < self._stall_until and self._connected:
                # The aircraft keeps flying, but we only ever see the last
                # message that arrived before the stall.
                if self._stalled_snapshot is None:
                    self._stalled_snapshot = self._snapshot(link_age=0.0)
                return self._stalled_snapshot.model_copy(
                    update={
                        "link_age_s": round(now - self._stall_started, 2),
                        "connection_quality": ConnectionQuality.POOR,
                    }
                )
            self._stall_until = None
            self._stalled_snapshot = None
        return self._snapshot(link_age=0.0)

    def _snapshot(self, link_age: float) -> Telemetry:
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
            ground_speed=round(self._ground_speed, 3),
            heading=round(self._heading, 1),
            battery_percentage=round(self._battery, 2),
            connection_quality=(
                ConnectionQuality.EXCELLENT
                if self._connected
                else ConnectionQuality.NONE
            ),
            link_age_s=link_age,
        )

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            name="Mock Drone (Simulator)",
            adapter_type="mock",
            is_simulated=True,
            supports_gps=False,
            supports_goto=True,
            supports_return=True,
            supported_capabilities=[
                "arm", "takeoff", "land", "hold", "goto", "return_home", "missions",
                "position", "velocity", "heading", "battery", "simulation",
                "fault_injection", "offboard",
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
            self._ground_speed = 0.0
            return

        # Battery drain.
        airborne = self._altitude > self.GROUND_EPSILON_M
        drain = self.BATTERY_DRAIN_FLYING if airborne else self.BATTERY_DRAIN_IDLE
        self._battery = max(0.0, self._battery - drain * dt)

        if self._offboard:
            self._step_offboard(dt)
            return

        vertical = self._step_vertical(dt)
        horizontal = self._step_horizontal(dt)
        self._ground_speed = abs(horizontal)
        self._velocity = math.copysign(math.hypot(vertical, horizontal), vertical or 1.0)
        rad = math.radians(self._heading)
        self._vel = np.array([math.sin(rad) * horizontal, math.cos(rad) * horizontal, vertical])
        self._pitch = 0.0
        self._settle_mode()

    def _step_offboard(self, dt: float) -> None:
        now = self._now()
        if now - self._last_sp_time > self.OFFBOARD_TIMEOUT_S:
            # Setpoint stream stopped: PX4-style offboard-loss failsafe.
            self._offboard = False
            self._target_altitude = self._altitude
            self._target_xy = None
            self._mode = FlightMode.HOLD
            self._vel = np.zeros(3)
            self._velocity = self._ground_speed = 0.0
            return
        # Integrate in small sub-steps so large ticks stay stable.
        steps = max(1, int(math.ceil(dt / 0.02)))
        h = dt / steps
        pos = np.array([self._x, self._y, self._altitude])
        accel = np.zeros(3)
        for _ in range(steps):
            accel = self.OFFBOARD_KP * (self._sp_pos - pos) + self.OFFBOARD_KV * (self._sp_vel - self._vel)
            norm = float(np.linalg.norm(accel))
            if norm > self.OFFBOARD_MAX_ACCEL:
                accel *= self.OFFBOARD_MAX_ACCEL / norm
            self._vel = self._vel + accel * h
            speed = float(np.linalg.norm(self._vel))
            if speed > self.MAX_SPEED_MPS:
                self._vel *= self.MAX_SPEED_MPS / speed
            pos = pos + self._vel * h
            if pos[2] < 0.0:
                pos[2] = 0.0
                self._vel[2] = max(0.0, self._vel[2])
        self._x, self._y, self._altitude = (float(v) for v in pos)
        self._target_altitude = self._altitude
        # Yaw toward the setpoint yaw, rate limited; heading is compass.
        yaw_enu = 90.0 - self._heading
        err = (self._sp_yaw_enu - yaw_enu + 180.0) % 360.0 - 180.0
        step = max(-self.OFFBOARD_MAX_YAW_RATE * dt, min(self.OFFBOARD_MAX_YAW_RATE * dt, err))
        self._heading = (90.0 - (yaw_enu + step)) % 360.0
        # Forward acceleration tilts the nose down (negative pitch).
        rad = math.radians(90.0 - self._heading)
        forward = accel[0] * math.cos(rad) + accel[1] * math.sin(rad)
        self._pitch = -math.degrees(math.atan2(forward, self.GRAVITY))
        self._ground_speed = float(np.hypot(self._vel[0], self._vel[1]))
        self._velocity = float(np.linalg.norm(self._vel))

    def _step_vertical(self, dt: float) -> float:
        """Move altitude toward its target; return the vertical speed used."""
        delta = self._target_altitude - self._altitude
        if abs(delta) <= self.ARRIVAL_EPSILON_M:
            self._altitude = self._target_altitude
            return 0.0
        rate = self.CLIMB_RATE_MPS if delta > 0 else self.LANDING_RATE_MPS
        step = rate * dt
        if step >= abs(delta):
            self._altitude = self._target_altitude
        else:
            self._altitude += step if delta > 0 else -step
        return rate if delta > 0 else -rate

    def _step_horizontal(self, dt: float) -> float:
        """Move toward the horizontal target; return the ground speed used."""
        if self._target_xy is None or self._altitude <= self.GROUND_EPSILON_M:
            return 0.0
        tx, ty = self._target_xy
        dx, dy = tx - self._x, ty - self._y
        dist = math.hypot(dx, dy)
        if dist <= self.ARRIVAL_EPSILON_M:
            self._x, self._y = tx, ty
            return 0.0
        self._heading = math.degrees(math.atan2(dx, dy)) % 360.0
        step = self._speed * dt
        if step >= dist:
            # Arrived part-way through this tick: report the average speed.
            self._x, self._y = tx, ty
            return dist / dt
        self._x += dx / dist * step
        self._y += dy / dist * step
        return self._speed

    def _horizontal_arrived(self) -> bool:
        if self._target_xy is None:
            return True
        tx, ty = self._target_xy
        return math.hypot(tx - self._x, ty - self._y) <= self.ARRIVAL_EPSILON_M

    def _settle_mode(self) -> None:
        """Apply automatic mode/arm transitions once a target is reached."""
        at_altitude = abs(self._target_altitude - self._altitude) <= self.ARRIVAL_EPSILON_M
        if not (at_altitude and self._horizontal_arrived()):
            return
        if self._mode in (FlightMode.TAKEOFF, FlightMode.MISSION):
            self._mode = FlightMode.HOLD
            self._target_xy = None
        elif self._mode == FlightMode.RETURNING:
            self._target_xy = None
            self._target_altitude = 0.0
            self._mode = FlightMode.LANDING
        elif self._mode == FlightMode.LANDING and self._altitude <= self.GROUND_EPSILON_M:
            self._altitude = 0.0
            self._mode = FlightMode.IDLE
            self._armed = False  # auto-disarm on the ground

    def _require_connected(self) -> None:
        if not self._connected:
            raise DroneError("adapter is not connected")
