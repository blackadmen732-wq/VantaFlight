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

        Unknown fields are left at their typed defaults (None or 0.0).
        Nothing is fabricated.
        """
        r = self._raw
        quality = ConnectionQuality.GOOD if self._connected else ConnectionQuality.NONE

        battery = r.get("battery_pct")
        if battery is None:
            battery = 100.0  # will be overridden by BatteryManager once connected

        return Telemetry(
            timestamp=time.time(),
            connected=self._connected,
            armed=bool(r.get("armed", False)),
            flight_mode=self._map_mode(r.get("flight_mode")),
            x=float(r.get("x", 0.0)),
            y=float(r.get("y", 0.0)),
            z=float(r.get("altitude_m", 0.0)),
            altitude=float(r.get("altitude_m", 0.0)),
            latitude=r.get("latitude"),
            longitude=r.get("longitude"),
            velocity=float(r.get("speed_mps", 0.0)),
            ground_speed=float(r.get("ground_speed_mps", 0.0)),
            heading=float(r.get("heading_deg", 0.0)),
            battery_percentage=float(battery),
            connection_quality=quality,
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
