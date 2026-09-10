"""Normalized, drone-agnostic data models.

Every adapter (mock, and later PX4/ArduPilot/etc.) reports state through these
models, so the rest of VantaFlight never needs to know which drone it talks to.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FlightMode(str, Enum):
    IDLE = "IDLE"
    TAKEOFF = "TAKEOFF"
    HOLD = "HOLD"
    LANDING = "LANDING"


class ConnectionQuality(str, Enum):
    NONE = "NONE"
    POOR = "POOR"
    FAIR = "FAIR"
    GOOD = "GOOD"
    EXCELLENT = "EXCELLENT"


class Telemetry(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    connected: bool = False
    armed: bool = False
    flight_mode: FlightMode = FlightMode.IDLE
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    altitude: float = 0.0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    velocity: float = 0.0
    ground_speed: float = 0.0
    heading: float = 0.0
    battery_percentage: float = 100.0
    connection_quality: ConnectionQuality = ConnectionQuality.NONE

    @property
    def airborne(self) -> bool:
        return self.connected and self.altitude > 0.15


class Capabilities(BaseModel):
    name: str
    adapter_type: str = "unknown"
    can_arm: bool = True
    can_takeoff: bool = True
    can_hold: bool = True
    can_land: bool = True
    supports_position: bool = True
    supports_velocity: bool = True
    supports_heading: bool = True
    supports_gps: bool = False
    supports_battery: bool = True
    supports_camera: bool = False
    is_simulated: bool = True
    max_altitude_m: float = 120.0
    supported_capabilities: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    command: str
    accepted: bool
    message: str = ""
    timestamp: float = Field(default_factory=time.time)


class FlightEvent(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    message: str


class AdapterType(str, Enum):
    MOCK = "mock"
    PX4_SITL = "px4_sitl"
