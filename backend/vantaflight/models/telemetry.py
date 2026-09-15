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
    # Availability flags — False means the value above is a placeholder, not a measurement.
    # Safety-critical code MUST check these before trusting the associated field.
    battery_available: bool = True      # False = battery data not yet received
    altitude_available: bool = True     # False = altitude not yet received from device
    velocity_available: bool = True     # False = velocity not yet received from device
    position_available: bool = True     # False = x/y position not yet received

    @property
    def airborne(self) -> bool:
        return self.connected and self.altitude > 0.15 and self.altitude_available


class CapabilityStatus(str, Enum):
    """Per-capability availability reported by a vehicle adapter."""
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
    # Fine-grained capability map; adapters that support it populate this.
    capability_map: dict[str, CapabilityStatus] = Field(default_factory=dict)


class CommandStatus(str, Enum):
    """Precise outcome of a command dispatch attempt.

    Callers must check this rather than assuming success.
    """
    SENT = "SENT"                         # command left VantaFlight toward device
    ACKNOWLEDGED = "ACKNOWLEDGED"         # device confirmed receipt
    REJECTED = "REJECTED"                 # device explicitly refused
    NOT_SENT = "NOT_SENT"                 # decided not to send (safety, state)
    TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"  # no active transport
    TIMED_OUT = "TIMED_OUT"               # no ack within deadline
    EXPIRED = "EXPIRED"                   # command TTL elapsed before dispatch
    UNSUPPORTED = "UNSUPPORTED"           # capability not available
    INTERNAL_ERROR = "INTERNAL_ERROR"     # unexpected failure

    @property
    def transmitted(self) -> bool:
        """True only when the command actually left this process."""
        return self in (CommandStatus.SENT, CommandStatus.ACKNOWLEDGED)


class CommandResult(BaseModel):
    command: str
    accepted: bool          # kept for backward compat; prefer `status`
    status: CommandStatus = CommandStatus.SENT
    message: str = ""
    timestamp: float = Field(default_factory=time.time)

    @classmethod
    def ok(cls, command: str, msg: str = "") -> "CommandResult":
        return cls(command=command, accepted=True, status=CommandStatus.SENT, message=msg)

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


class FlightEvent(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    message: str


class AdapterType(str, Enum):
    MOCK = "mock"
    PX4_SITL = "px4_sitl"
    HOPPER = "hopper"


class TelemetrySource(str, Enum):
    """Where a telemetry value came from — preserved for Digital Twin and Replay."""
    HOPPER_NATIVE = "HOPPER_NATIVE"
    VANTASTATE_ESTIMATED = "VANTASTATE_ESTIMATED"
    CAMERA_DERIVED = "CAMERA_DERIVED"
    SIMULATED = "SIMULATED"
    UNAVAILABLE = "UNAVAILABLE"
