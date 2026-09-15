"""Normalized, drone-agnostic data models."""
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


class TelemetrySource(str, Enum):
    HOPPER_NATIVE = "HOPPER_NATIVE"
    VANTASTATE_ESTIMATED = "VANTASTATE_ESTIMATED"
    CAMERA_DERIVED = "CAMERA_DERIVED"
    SIMULATED = "SIMULATED"
    UNAVAILABLE = "UNAVAILABLE"


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
    health_all_ok: bool = True
    # Availability flags distinguish placeholders from measurements. Safety
    # code must check them before trusting the corresponding value.
    battery_available: bool = True
    altitude_available: bool = True
    velocity_available: bool = True
    position_available: bool = True

    @property
    def airborne(self) -> bool:
        return self.connected and self.altitude_available and self.altitude > 0.15


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    DEGRADED = "DEGRADED"


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
    capability_map: dict[str, CapabilityStatus] = Field(default_factory=dict)


class CommandStatus(str, Enum):
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    NOT_SENT = "NOT_SENT"
    TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"
    TIMED_OUT = "TIMED_OUT"
    EXPIRED = "EXPIRED"
    UNSUPPORTED = "UNSUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @property
    def transmitted(self) -> bool:
        return self in (CommandStatus.SENT, CommandStatus.ACKNOWLEDGED)


class CommandResult(BaseModel):
    command: str
    accepted: bool
    status: CommandStatus = CommandStatus.SENT
    message: str = ""
    timestamp: float = Field(default_factory=time.time)

    @classmethod
    def ok(cls, command: str, msg: str = "") -> "CommandResult":
        return cls(command=command, accepted=True, status=CommandStatus.SENT, message=msg)

    @classmethod
    def acknowledged(cls, command: str, msg: str = "") -> "CommandResult":
        return cls(command=command, accepted=True, status=CommandStatus.ACKNOWLEDGED, message=msg)

    @classmethod
    def no_transport(cls, command: str) -> "CommandResult":
        return cls(
            command=command,
            accepted=False,
            status=CommandStatus.TRANSPORT_UNAVAILABLE,
            message=f"No active transport; '{command}' was NOT transmitted to any device.",
        )

    @classmethod
    def unsupported(cls, command: str, reason: str = "") -> "CommandResult":
        return cls(
            command=command,
            accepted=False,
            status=CommandStatus.UNSUPPORTED,
            message=reason or f"'{command}' is not supported by the active adapter.",
        )

    @classmethod
    def rejected(cls, command: str, reason: str) -> "CommandResult":
        return cls(command=command, accepted=False, status=CommandStatus.REJECTED, message=reason)


class FlightEvent(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    message: str


class AdapterType(str, Enum):
    MOCK = "mock"
    PX4_SITL = "px4_sitl"
    HOPPER = "hopper"
