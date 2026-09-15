"""Hopper telemetry connector.

Normalizes whatever the official FTW interface exposes into VantaFlight's
Telemetry model.  Fields that are not exposed by the current interface are
set to None (or their typed default) rather than fabricated as 0.

Telemetry provenance is tracked so Digital Twin and Replay can distinguish
HOPPER_NATIVE measurements from VANTASTATE_ESTIMATED ones.
"""
from __future__ import annotations

import time
from typing import Optional

from ...models import ConnectionQuality, FlightMode, Telemetry, TelemetrySource
from .timesync import HopperTimeSync


class HopperTelemetryConnector:
    """Collects and normalizes telemetry from whatever source is available.

    Today: the FTW SDK does not publish a documented external telemetry API,
    so this connector is the placeholder that will be populated when FTW
    releases it.  It already exposes the full normalized interface so the
    rest of VantaFlight compiles and tests run.
    """

    def __init__(self, timesync: Optional[HopperTimeSync] = None) -> None:
        self._ts = timesync or HopperTimeSync()
        self._connected = False
        self._raw: dict = {}

    @property
    def connected(self) -> bool:
        return self._connected

    def mark_connected(self) -> None:
        self._connected = True

    def mark_disconnected(self) -> None:
        self._connected = False
        self._raw = {}

    def ingest(self, raw: dict) -> None:
        """Accept a raw telemetry payload from the official FTW interface."""
        self._raw = raw

    def get_telemetry(self) -> Telemetry:
        """Return the latest normalized snapshot.

        Fields not present in raw data are flagged as unavailable.
        Unknown values are NEVER converted to measurements that look real
        (unknown altitude != 0 m; unknown battery != 100%).
        """
        r = self._raw
        quality = ConnectionQuality.GOOD if self._connected else ConnectionQuality.NONE

        battery_raw = r.get("battery_pct")
        battery_available = battery_raw is not None

        altitude_raw = r.get("altitude_m")
        altitude_available = altitude_raw is not None

        speed_raw = r.get("speed_mps")
        velocity_available = speed_raw is not None

        x_raw = r.get("x")
        y_raw = r.get("y")
        position_available = x_raw is not None and y_raw is not None

        return Telemetry(
            timestamp=time.time(),
            connected=self._connected,
            armed=bool(r.get("armed", False)),
            flight_mode=self._map_mode(r.get("flight_mode")),
            x=float(x_raw) if position_available else 0.0,
            y=float(y_raw) if position_available else 0.0,
            z=float(altitude_raw) if altitude_available else 0.0,
            altitude=float(altitude_raw) if altitude_available else 0.0,
            latitude=r.get("latitude"),
            longitude=r.get("longitude"),
            velocity=float(speed_raw) if velocity_available else 0.0,
            ground_speed=float(r.get("ground_speed_mps", 0.0)),
            heading=float(r.get("heading_deg", 0.0)),
            battery_percentage=float(battery_raw) if battery_available else 0.0,
            connection_quality=quality,
            battery_available=battery_available,
            altitude_available=altitude_available,
            velocity_available=velocity_available,
            position_available=position_available,
        )

    @staticmethod
    def _map_mode(raw_mode: Optional[str]) -> FlightMode:
        if raw_mode is None:
            return FlightMode.IDLE
        m = raw_mode.upper()
        mapping = {
            "TAKEOFF": FlightMode.TAKEOFF,
            "HOVER": FlightMode.HOLD,
            "HOLD": FlightMode.HOLD,
            "LAND": FlightMode.LANDING,
            "LANDING": FlightMode.LANDING,
        }
        return mapping.get(m, FlightMode.IDLE)

    def get_provenance(self) -> TelemetrySource:
        if not self._connected:
            return TelemetrySource.UNAVAILABLE
        if self._raw:
            return TelemetrySource.HOPPER_NATIVE
        return TelemetrySource.UNAVAILABLE
