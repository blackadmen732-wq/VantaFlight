"""PX4 SITL adapter — real simulated flight through MAVSDK.

PX4 reports global positions (latitude/longitude). VantaFlight works in local
ENU metres relative to home, so this adapter converts both ways using a
flat-earth approximation around the home position — accurate to centimetres
over the few hundred metres a geofenced flight covers.
"""
from __future__ import annotations

import math
import time

from ..geo import global_to_local, local_to_global
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
    "RETURN_TO_LAUNCH": FlightMode.RETURNING,
    "MISSION": FlightMode.MISSION,
}

# Telemetry age thresholds (seconds) for link quality grading.
_QUALITY_BY_AGE = (
    (0.5, ConnectionQuality.EXCELLENT),
    (1.0, ConnectionQuality.GOOD),
    (2.0, ConnectionQuality.FAIR),
)


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

    async def goto(
        self, x: float, y: float, altitude: float, speed_m_s: float | None = None
    ) -> None:
        px4 = self._client.telemetry
        if px4.home_latitude_deg is None or px4.home_longitude_deg is None:
            raise MAVSDKError("PX4_NO_HOME", "home position not known yet; wait for GPS lock")
        lat, lon = local_to_global(x, y, px4.home_latitude_deg, px4.home_longitude_deg)
        home_alt = px4.home_absolute_altitude_m or 0.0
        if speed_m_s is not None:
            await self._client.set_speed(speed_m_s)
        await self._client.goto_location(lat, lon, home_alt + altitude)

    async def return_home(self) -> None:
        await self._client.return_to_launch()

    def home_position(self) -> tuple[float, float, float] | None:
        """PX4's home as (latitude, longitude, altitude AMSL), once known."""
        px4 = self._client.telemetry
        if px4.home_latitude_deg is None or px4.home_longitude_deg is None:
            return None
        return (px4.home_latitude_deg, px4.home_longitude_deg, px4.home_absolute_altitude_m or 0.0)

    def get_telemetry(self) -> Telemetry:
        px4 = self._client.telemetry
        mode = self._map_flight_mode(px4.flight_mode, px4.in_air)

        vel = math.sqrt(
            px4.velocity_north_m_s ** 2
            + px4.velocity_east_m_s ** 2
            + px4.velocity_down_m_s ** 2
        )

        connected = self._connected and self._client.connected
        now = time.time()
        link_age = max(0.0, now - px4.last_message_at) if px4.last_message_at else 0.0
        quality = self._grade_link(connected, link_age, px4.health_all_ok)

        x = y = 0.0
        if (
            px4.latitude_deg is not None and px4.longitude_deg is not None
            and px4.home_latitude_deg is not None and px4.home_longitude_deg is not None
        ):
            x, y = global_to_local(
                px4.latitude_deg, px4.longitude_deg,
                px4.home_latitude_deg, px4.home_longitude_deg,
            )

        return Telemetry(
            timestamp=px4.last_message_at or now,
            connected=connected,
            armed=px4.armed,
            flight_mode=mode,
            x=round(x, 3),
            y=round(y, 3),
            z=px4.relative_altitude_m,
            altitude=px4.relative_altitude_m,
            latitude=px4.latitude_deg,
            longitude=px4.longitude_deg,
            velocity=round(vel, 3),
            ground_speed=round(px4.ground_speed_m_s, 3),
            heading=round(px4.heading_deg, 1),
            battery_percentage=round(px4.battery_remaining_percent, 2),
            connection_quality=quality,
            link_age_s=round(link_age, 2),
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
    @staticmethod
    def _grade_link(connected: bool, link_age: float, health_ok: bool) -> ConnectionQuality:
        if not connected:
            return ConnectionQuality.NONE
        for max_age, quality in _QUALITY_BY_AGE:
            if link_age <= max_age:
                # An unhealthy estimator caps the grade at GOOD.
                if quality == ConnectionQuality.EXCELLENT and not health_ok:
                    return ConnectionQuality.GOOD
                return quality
        return ConnectionQuality.POOR

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            name="PX4 SITL",
            adapter_type="px4_sitl",
            is_simulated=True,
            supports_gps=True,
            supports_goto=True,
            supports_return=True,
            supported_capabilities=[
                "arm", "takeoff", "land", "hold", "goto", "return_home", "missions",
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
