"""Thin MAVSDK wrapper.

Centralizes all MAVSDK calls behind a mockable boundary so PX4SITLAdapter
never calls MAVSDK directly and unit tests never need a live PX4 instance.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, TypeVar

from .config import MAVLinkConfig

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def _first_matching(
    stream: AsyncIterator[_T], predicate: Callable[[_T], bool], timeout: float
) -> _T:
    """Return the first stream item satisfying ``predicate``.

    Raises ``asyncio.TimeoutError`` after ``timeout`` seconds. Works on Python
    3.10 (``asyncio.timeout`` only exists from 3.11).
    """

    async def _scan() -> _T:
        async for item in stream:
            if predicate(item):
                return item
        raise ConnectionError("stream ended")

    return await asyncio.wait_for(_scan(), timeout)


class MAVSDKError(RuntimeError):
    """Structured error from the MAVSDK layer."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def battery_percent(remaining: float) -> float:
    """MAVSDK 2+ reports ``remaining_percent`` as 0..100 (1.x used 0..1).

    PX4 sends -1 when the level is unknown; that and anything out of range is
    clamped so a failsafe never sees 1600 % or a negative charge.
    """
    if remaining is None or remaining != remaining or remaining < 0:
        return 0.0
    return min(float(remaining), 100.0)


@dataclass
class PX4Telemetry:
    connected: bool = False
    armed: bool = False
    in_air: bool = False
    flight_mode: str = "UNKNOWN"
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    absolute_altitude_m: float = 0.0
    relative_altitude_m: float = 0.0
    velocity_north_m_s: float = 0.0
    velocity_east_m_s: float = 0.0
    velocity_down_m_s: float = 0.0
    ground_speed_m_s: float = 0.0
    heading_deg: float = 0.0
    battery_remaining_percent: float = 100.0
    health_all_ok: bool = False
    # Home (launch) position; local x/y/altitude are measured from here.
    home_latitude_deg: Optional[float] = None
    home_longitude_deg: Optional[float] = None
    home_absolute_altitude_m: Optional[float] = None
    #: Wall-clock time of the last message received from PX4 (0 = never).
    last_message_at: float = 0.0

    def touch(self) -> None:
        self.last_message_at = time.time()


class MAVSDKClient:
    """Mockable MAVSDK boundary for PX4 SITL control."""

    def __init__(self, config: MAVLinkConfig | None = None) -> None:
        self._config = config or MAVLinkConfig()
        self._system = None
        self._connected = False
        self._telemetry = PX4Telemetry()
        self._telemetry_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def telemetry(self) -> PX4Telemetry:
        return self._telemetry

    async def connect(self) -> None:
        try:
            from mavsdk import System
        except ImportError:
            raise MAVSDKError(
                "PX4_NOT_FOUND",
                "mavsdk package is not installed. Install with: pip install \"mavsdk>=2,<4\"",
            )

        self._system = System()
        try:
            await self._system.connect(system_address=self._config.system_address)
        except Exception as e:
            raise MAVSDKError("PX4_CONNECTION_TIMEOUT", str(e))

        try:
            await _first_matching(
                self._system.core.connection_state(),
                lambda state: state.is_connected,
                self._config.connection_timeout,
            )
            self._connected = True
        except (asyncio.TimeoutError, TimeoutError):
            raise MAVSDKError("PX4_CONNECTION_TIMEOUT", "timed out waiting for PX4 connection")

        health_ok = False
        try:
            await _first_matching(
                self._system.telemetry.health(),
                lambda h: h.is_global_position_ok and h.is_home_position_ok,
                self._config.health_timeout,
            )
            health_ok = True
        except Exception:  # timeout or stream error: proceed, but say so below
            pass

        if not health_ok:
            logger.warning("PX4 health not fully ready, proceeding with caution")

        self._telemetry.connected = True
        self._telemetry.health_all_ok = health_ok
        self._telemetry.touch()
        self._telemetry_task = asyncio.create_task(self._stream_telemetry())

    async def disconnect(self) -> None:
        if self._telemetry_task is not None:
            self._telemetry_task.cancel()
            try:
                await self._telemetry_task
            except asyncio.CancelledError:
                pass
            self._telemetry_task = None
        self._connected = False
        self._telemetry = PX4Telemetry()
        self._system = None

    async def arm(self) -> None:
        self._require_connected()
        try:
            await self._system.action.arm()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"arm failed: {e}")

    async def disarm(self) -> None:
        self._require_connected()
        try:
            await self._system.action.disarm()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"disarm failed: {e}")

    async def takeoff(self, altitude_m: float = 5.0) -> None:
        self._require_connected()
        try:
            await self._system.action.set_takeoff_altitude(altitude_m)
            await self._system.action.takeoff()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"takeoff failed: {e}")

    async def hold(self) -> None:
        self._require_connected()
        try:
            await self._system.action.hold()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"hold failed: {e}")

    async def land(self) -> None:
        self._require_connected()
        try:
            await self._system.action.land()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"land failed: {e}")

    async def goto_location(
        self, latitude_deg: float, longitude_deg: float,
        absolute_altitude_m: float, yaw_deg: float = float("nan"),
    ) -> None:
        self._require_connected()
        try:
            await self._system.action.goto_location(
                latitude_deg, longitude_deg, absolute_altitude_m, yaw_deg
            )
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"goto failed: {e}")

    async def set_speed(self, speed_m_s: float) -> None:
        self._require_connected()
        try:
            await self._system.action.set_current_speed(speed_m_s)
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"set speed failed: {e}")

    async def return_to_launch(self) -> None:
        self._require_connected()
        try:
            await self._system.action.return_to_launch()
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"return to launch failed: {e}")

    async def _stream_telemetry(self) -> None:
        if self._system is None:
            return

        tel = self._telemetry

        def _position(pos) -> None:
            tel.latitude_deg = pos.latitude_deg
            tel.longitude_deg = pos.longitude_deg
            tel.absolute_altitude_m = pos.absolute_altitude_m
            tel.relative_altitude_m = pos.relative_altitude_m

        def _velocity(vel) -> None:
            tel.velocity_north_m_s = vel.north_m_s
            tel.velocity_east_m_s = vel.east_m_s
            tel.velocity_down_m_s = vel.down_m_s
            tel.ground_speed_m_s = (vel.north_m_s**2 + vel.east_m_s**2) ** 0.5

        def _heading(hdg) -> None:
            tel.heading_deg = hdg.heading_deg

        def _battery(bat) -> None:
            tel.battery_remaining_percent = battery_percent(bat.remaining_percent)

        def _armed(is_armed) -> None:
            tel.armed = is_armed

        def _in_air(in_air) -> None:
            tel.in_air = in_air

        def _flight_mode(mode) -> None:
            tel.flight_mode = str(mode)

        def _home(home) -> None:
            tel.home_latitude_deg = home.latitude_deg
            tel.home_longitude_deg = home.longitude_deg
            tel.home_absolute_altitude_m = home.absolute_altitude_m

        px4 = self._system.telemetry
        streams: dict[str, tuple[Callable[[], AsyncIterator], Callable]] = {
            "position": (px4.position, _position),
            "velocity": (px4.velocity_ned, _velocity),
            "heading": (px4.heading, _heading),
            "battery": (px4.battery, _battery),
            "armed": (px4.armed, _armed),
            "in_air": (px4.in_air, _in_air),
            "flight_mode": (px4.flight_mode, _flight_mode),
            "home": (px4.home, _home),
        }

        async def _pump(name: str, open_stream, apply) -> None:
            try:
                async for msg in open_stream():
                    apply(msg)
                    tel.touch()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("%s stream lost: %s", name, e)
                self._connected = False

        tasks = [
            asyncio.create_task(_pump(name, open_stream, apply))
            for name, (open_stream, apply) in streams.items()
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    async def start_offboard(self) -> None:
        self._require_connected()
        try:
            from mavsdk.offboard import PositionNedYaw
            initial = PositionNedYaw(0.0, 0.0, 0.0, 0.0)
            await self._system.offboard.set_position_ned(initial)
            await self._system.offboard.start()
        except ImportError:
            raise MAVSDKError("MAVSDK_NOT_FOUND", "mavsdk offboard module not available")
        except Exception as e:
            raise MAVSDKError("PX4_OFFBOARD_FAILED", f"offboard start failed: {e}")

    async def stop_offboard(self) -> None:
        self._require_connected()
        try:
            await self._system.offboard.stop()
        except Exception as e:
            raise MAVSDKError("PX4_OFFBOARD_FAILED", f"offboard stop failed: {e}")

    async def set_position_ned(
        self, north_m: float, east_m: float, down_m: float, yaw_deg: float,
    ) -> None:
        self._require_connected()
        try:
            from mavsdk.offboard import PositionNedYaw
            await self._system.offboard.set_position_ned(
                PositionNedYaw(north_m, east_m, down_m, yaw_deg)
            )
        except ImportError:
            raise MAVSDKError("MAVSDK_NOT_FOUND", "mavsdk offboard module not available")
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"set_position_ned failed: {e}")

    async def set_velocity_ned(
        self, north_m_s: float, east_m_s: float, down_m_s: float, yaw_deg: float,
    ) -> None:
        self._require_connected()
        try:
            from mavsdk.offboard import VelocityNedYaw
            await self._system.offboard.set_velocity_ned(
                VelocityNedYaw(north_m_s, east_m_s, down_m_s, yaw_deg)
            )
        except ImportError:
            raise MAVSDKError("MAVSDK_NOT_FOUND", "mavsdk offboard module not available")
        except Exception as e:
            raise MAVSDKError("PX4_COMMAND_REJECTED", f"set_velocity_ned failed: {e}")

    def _require_connected(self) -> None:
        if not self._connected or self._system is None:
            raise MAVSDKError("PX4_NOT_FOUND", "not connected to PX4")
