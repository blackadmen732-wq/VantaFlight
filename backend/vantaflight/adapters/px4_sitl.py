"""PX4 SITL adapter — real simulated flight through MAVSDK."""
from __future__ import annotations

import math
import time

from ..mavlink import MAVSDKClient, MAVSDKError, MAVLinkConfig
from ..models import (
    Capabilities,
    ConnectionQuality,
    FlightMode,
    Telemetry,
)


_PX4_MODE_MAP = {
    "TAKEOFF": FlightMode.TAKEOFF,
    "HOLD": FlightMode.HOLD,
    "LAND": FlightMode.LANDING,
    "RETURN_TO_LAUNCH": FlightMode.LANDING,
}


class PX4SITLAdapter:
    """Adapter bridging VantaFlight to PX4 SITL through MAVSDK."""

    def __init__(
        self,
        adapter_id: str = "px4-sitl-0",
        mavsdk_client: MAVSDKClient | None = None,
        config: MAVLinkConfig | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self._client = mavsdk_client or MAVSDKClient(config)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._client.connected

    async def connect(self) -> None:
        if self._connected and self._client.connected:
            return
        await self._client.connect()
        self._connected = True

    async def disconnect(self) -> None:
        await self._client.disconnect()
        self._connected = False

    async def arm(self) -> None:
        await self._client.arm()

    async def disarm(self) -> None:
        await self._client.disarm()

    async def takeoff(self, target_altitude_m: float = 5.0) -> None:
        await self._client.takeoff(altitude_m=target_altitude_m)

    async def hold(self) -> None:
        await self._client.hold()

    async def land(self) -> None:
        await self._client.land()

    def get_telemetry(self) -> Telemetry:
        px4 = self._client.telemetry
        mode = self._map_flight_mode(px4.flight_mode, px4.in_air)

        vel = math.sqrt(
            px4.velocity_north_m_s ** 2
            + px4.velocity_east_m_s ** 2
            + px4.velocity_down_m_s ** 2
        )

        quality = ConnectionQuality.NONE
        if self._connected and self._client.connected:
            quality = ConnectionQuality.EXCELLENT if px4.health_all_ok else ConnectionQuality.GOOD

        x = px4.longitude_deg if px4.longitude_deg is not None else 0.0
        y = px4.latitude_deg if px4.latitude_deg is not None else 0.0

        return Telemetry(
            timestamp=time.time(),
            connected=self._connected and self._client.connected,
            armed=px4.armed,
            flight_mode=mode,
            x=x,
            y=y,
            z=px4.relative_altitude_m,
            altitude=px4.relative_altitude_m,
            latitude=px4.latitude_deg,
            longitude=px4.longitude_deg,
            velocity=round(vel, 3),
            ground_speed=round(px4.ground_speed_m_s, 3),
            heading=round(px4.heading_deg, 1),
            battery_percentage=round(px4.battery_remaining_percent, 2),
            connection_quality=quality,
        )

    async def start_offboard(self) -> None:
        await self._client.start_offboard()

    async def stop_offboard(self) -> None:
        await self._client.stop_offboard()

    async def set_position_ned(
        self, north_m: float, east_m: float, down_m: float, yaw_deg: float,
    ) -> None:
        await self._client.set_position_ned(north_m, east_m, down_m, yaw_deg)

    async def set_velocity_ned(
        self, north_m_s: float, east_m_s: float, down_m_s: float, yaw_deg: float,
    ) -> None:
        await self._client.set_velocity_ned(north_m_s, east_m_s, down_m_s, yaw_deg)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            name="PX4 SITL",
            adapter_type="px4_sitl",
            is_simulated=True,
            supports_gps=True,
            supported_capabilities=[
                "arm", "takeoff", "land", "hold",
                "position", "velocity", "heading",
                "gps", "battery", "simulation",
                "offboard",
            ],
        )

    @staticmethod
    def _map_flight_mode(px4_mode: str, in_air: bool) -> FlightMode:
        raw = px4_mode.upper().replace(" ", "_")
        if "." in raw:
            raw = raw.rsplit(".", 1)[-1]
        if raw in _PX4_MODE_MAP:
            return _PX4_MODE_MAP[raw]
        if in_air:
            return FlightMode.HOLD
        return FlightMode.IDLE
