"""Normalized, drone-agnostic data models.

Every adapter (mock, and later PX4/ArduPilot/etc.) reports state through these
models, so the rest of VantaFlight never needs to know which drone it talks to.
"""
from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


class FlightMode(str, Enum):
    """High-level, vendor-neutral flight modes."""

    IDLE = "IDLE"
    TAKEOFF = "TAKEOFF"
    HOLD = "HOLD"
    LANDING = "LANDING"


class ConnectionQuality(str, Enum):
    """Coarse link quality bucket, independent of the underlying transport."""

    NONE = "NONE"
    POOR = "POOR"
    FAIR = "FAIR"
    GOOD = "GOOD"
    EXCELLENT = "EXCELLENT"


class Telemetry(BaseModel):
    """A single normalized telemetry snapshot.

    Units: distances in meters, velocity in m/s, heading in degrees [0, 360),
    battery in percent [0, 100].
    """

    timestamp: float = Field(default_factory=time.time)
    connected: bool = False
    armed: bool = False
    flight_mode: FlightMode = FlightMode.IDLE
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    altitude: float = 0.0
    velocity: float = 0.0
    heading: float = 0.0
    battery_percentage: float = 100.0
    connection_quality: ConnectionQuality = ConnectionQuality.NONE

    @property
    def airborne(self) -> bool:
        """True when the aircraft is meaningfully off the ground."""
        return self.connected and self.altitude > 0.15


class Capabilities(BaseModel):
    """What a given adapter/drone supports.

    Lets the UI and safety layer adapt without hard-coding vendor assumptions.
    """

    name: str
    can_arm: bool = True
    can_takeoff: bool = True
    can_hold: bool = True
    can_land: bool = True
    supports_position: bool = True
    max_altitude_m: float = 120.0
    is_simulated: bool = True


class CommandResult(BaseModel):
    """Result of attempting a command, whether accepted or rejected by safety."""

    command: str
    accepted: bool
    message: str = ""
    timestamp: float = Field(default_factory=time.time)


class FlightEvent(BaseModel):
    """A discrete, human-readable event in a flight's timeline."""

    timestamp: float = Field(default_factory=time.time)
    event_type: str
    message: str
