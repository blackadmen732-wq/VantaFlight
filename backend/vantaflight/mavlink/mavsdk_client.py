"""Thin MAVSDK wrapper.

Centralizes all MAVSDK calls behind a mockable boundary so PX4SITLAdapter
never calls MAVSDK directly and unit tests never need a live PX4 instance.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import MAVLinkConfig

logger = logging.getLogger(__name__)


class MAVSDKError(RuntimeError):
    """Structured error from the MAVSDK layer."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


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
                "mavsdk package is not installed. Install with: pip install mavsdk",
            )

        self._system = System()
        try:
            await self._system.connect(system_address=self._config.system_address)
        except Exception as e:
            raise MAVSDKError("PX4_CONNECTION_TIMEOUT", str(e))

        try:
            async with asyncio.timeout(self._config.connection_timeout):
                async for state in self._system.core.connection_state():
                    if state.is_connected:
                        self._connected = True
                        break
                    await asyncio.sleep(0.1)
        except (asyncio.TimeoutError, TimeoutError):
            raise MAVSDKError("PX4_CONNECTION_TIMEOUT", "timed out waiting for PX4 connection")

        health_ok = False
        try:
            async with asyncio.timeout(self._config.health_timeout):
                async for health in self._system.telemetry.health():
                    if health.is_global_position_ok and health.is_home_position_ok:
                        health_ok = True
                        break
                    await asyncio.sleep(0.5)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        except Exception:
            pass

        if not health_ok:
            logger.warning("PX4 health not fully ready, proceeding with caution")

        self._telemetry.connected = True
        self._telemetry.health_all_ok = health_ok
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

    async def _stream_telemetry(self) -> None:
        if self._system is None:
            return

        async def _position():
            try:
                async for pos in self._system.telemetry.position():
                    self._telemetry.latitude_deg = pos.latitude_deg
                    self._telemetry.longitude_deg = pos.longitude_deg
                    self._telemetry.absolute_altitude_m = pos.absolute_altitude_m
                    self._telemetry.relative_altitude_m = pos.relative_altitude_m
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("position stream lost: %s", e)
                self._connected = False

        async def _velocity():
            try:
                async for vel in self._system.telemetry.velocity_ned():
                    self._telemetry.velocity_north_m_s = vel.north_m_s
                    self._telemetry.velocity_east_m_s = vel.east_m_s
                    self._telemetry.velocity_down_m_s = vel.down_m_s
                    self._telemetry.ground_speed_m_s = (
                        vel.north_m_s**2 + vel.east_m_s**2
                    ) ** 0.5
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("velocity stream lost: %s", e)
                self._connected = False

        async def _heading():
            try:
                async for hdg in self._system.telemetry.heading():
                    self._telemetry.heading_deg = hdg.heading_deg
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("heading stream lost: %s", e)
                self._connected = False

        async def _battery():
            try:
                async for bat in self._system.telemetry.battery():
                    self._telemetry.battery_remaining_percent = (
                        bat.remaining_percent * 100 if bat.remaining_percent >= 0 else 0
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("battery stream lost: %s", e)
                self._connected = False

        async def _armed():
            try:
                async for is_armed in self._system.telemetry.armed():
                    self._telemetry.armed = is_armed
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("armed stream lost: %s", e)
                self._connected = False

        async def _in_air():
            try:
                async for in_air in self._system.telemetry.in_air():
                    self._telemetry.in_air = in_air
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("in_air stream lost: %s", e)
                self._connected = False

        async def _flight_mode():
            try:
                async for mode in self._system.telemetry.flight_mode():
                    self._telemetry.flight_mode = str(mode)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("flight_mode stream lost: %s", e)
                self._connected = False

        tasks = [
            asyncio.create_task(fn())
            for fn in [_position, _velocity, _heading, _battery, _armed, _in_air, _flight_mode]
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
