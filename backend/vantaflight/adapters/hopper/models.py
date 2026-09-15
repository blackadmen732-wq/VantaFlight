"""Hopper-specific data models.

These are internal to the hopper package; VantaFlight only ever sees the
normalized VantaFlight models (Telemetry, Capabilities, etc.).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class HopperConnectionState(str, Enum):
    """Multi-link connection state.

    Hopper has separate camera (Wi-Fi) and control/program (Bluetooth) links.
    These advance independently; the adapter reports the combined state here.
    """
    DISCONNECTED = "DISCONNECTED"
    DISCOVERING = "DISCOVERING"
    CAMERA_ONLY = "CAMERA_ONLY"       # Wi-Fi camera up; no control link
    OBSERVE = "OBSERVE"               # camera + telemetry; no flight commands
    PROGRAM_READY = "PROGRAM_READY"   # can deploy autonomous programs
    LIVE_CONTROL_READY = "LIVE_CONTROL_READY"  # live movement commands available
    DEGRADED = "DEGRADED"             # partial link loss
    ERROR = "ERROR"


class HopperOperatingMode(str, Enum):
    """High-level operating mode chosen by the operator or mission system."""
    OBSERVE = "OBSERVE"           # camera/telemetry recording; no commands
    MANUAL_ASSIST = "MANUAL_ASSIST"  # pilot flies; VantaFlight records/advises
    PROGRAM_UPLOAD = "PROGRAM_UPLOAD"  # deploy FTW autonomous programs
    LIVE_CONTROL = "LIVE_CONTROL"   # live closed-loop control (requires official SDK)


class HopperHealthState(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    UNAVAILABLE = "UNAVAILABLE"


class BatteryState(str, Enum):
    UNKNOWN = "UNKNOWN"
    NORMAL = "NORMAL"
    RESERVE = "RESERVE"
    RETURN_REQUIRED = "RETURN_REQUIRED"
    CRITICAL = "CRITICAL"
    LANDING = "LANDING"
    LANDED = "LANDED"


class SafetyPriority(int, Enum):
    """Command arbiter priority — higher value wins."""
    NORMAL_COMMAND = 0
    SPEED_OPTIMIZE = 1
    MISSION_OBJECTIVE = 2
    HOLD = 3
    COLLISION_PROTECT = 4
    STATE_INVALID = 5
    LINK_LOST = 6
    MISSION_ABORT = 7
    BATTERY_CRITICAL = 8
    EMERGENCY = 9


@dataclass(frozen=True)
class CommandId:
    value: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class HopperCommand:
    """Typed, immutable command envelope. All commands must carry an expiry."""
    command_id: str
    command_type: str
    issued_at: float
    expires_at: float
    source: str = "vanta_mission"
    mission_id: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(command_type: str, ttl_s: float = 1.0, **params: Any) -> "HopperCommand":
        now = time.monotonic()
        return HopperCommand(
            command_id=str(uuid.uuid4()),
            command_type=command_type,
            issued_at=now,
            expires_at=now + ttl_s,
            params=params,
        )

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at


@dataclass
class HopperHealth:
    overall: HopperHealthState = HopperHealthState.UNAVAILABLE
    control_link: HopperHealthState = HopperHealthState.UNAVAILABLE
    camera_link: HopperHealthState = HopperHealthState.UNAVAILABLE
    telemetry_link: HopperHealthState = HopperHealthState.UNAVAILABLE
    battery: HopperHealthState = HopperHealthState.UNAVAILABLE
    firmware: HopperHealthState = HopperHealthState.UNAVAILABLE
    calibration: HopperHealthState = HopperHealthState.UNAVAILABLE
    command_freshness: HopperHealthState = HopperHealthState.OK
    last_error: Optional[str] = None

    def compute_overall(self) -> HopperHealthState:
        states = [
            self.control_link,
            self.camera_link,
            self.battery,
        ]
        if any(s == HopperHealthState.CRITICAL for s in states):
            return HopperHealthState.CRITICAL
        if any(s == HopperHealthState.DEGRADED for s in states):
            return HopperHealthState.DEGRADED
        if all(s == HopperHealthState.UNAVAILABLE for s in states):
            return HopperHealthState.UNAVAILABLE
        return HopperHealthState.OK


@dataclass
class FramePacket:
    """A single camera frame with provenance metadata."""
    data: bytes
    capture_ts: Optional[float]     # from Hopper, if exposed; else None
    receive_ts: float               # host wall-clock time at receive
    monotonic_ts: float             # host monotonic timestamp
    width: int = 0
    height: int = 0
    encoding: str = "jpeg"

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.monotonic_ts


@dataclass
class HopperMissionInstruction:
    """One step in a compiled Hopper mission program."""
    opcode: str          # TAKEOFF, MOVE_FORWARD, TURN, HOVER, LAND, etc.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class HopperMissionProgram:
    """Compiled program ready for deployment via an official FTW interface."""
    program_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    instructions: list[HopperMissionInstruction] = field(default_factory=list)
    estimated_duration_s: float = 0.0
    max_altitude_m: float = 0.0
    compiled_at: float = field(default_factory=time.time)
    source_mission_id: Optional[str] = None
