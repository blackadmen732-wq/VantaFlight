"""Hopper capability negotiation.

At connect time the adapter discovers what the specific Hopper + active
transport combination actually supports and populates HopperCapabilities.
VantaExecution queries this before issuing any command.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...models import CapabilityStatus

# Canonical capability keys used throughout VantaFlight.
CAP_ARM = "arm"
CAP_DISARM = "disarm"
CAP_TAKEOFF = "takeoff"
CAP_LAND = "land"
CAP_HOLD = "hold"
CAP_RELATIVE_MOVE = "relative_move"
CAP_VELOCITY = "velocity"
CAP_POSITION = "position"
CAP_YAW = "yaw"
CAP_CAMERA = "camera"
CAP_TELEMETRY = "telemetry"
CAP_BATTERY = "battery"
CAP_PROGRAM_UPLOAD = "program_upload"
CAP_PROGRAM_RUN = "program_run"
CAP_PROGRAM_STOP = "program_stop"
CAP_PAYLOAD = "payload"
CAP_RAW_IMU = "raw_imu"
CAP_TOF = "tof"


@dataclass
class HopperCapabilities:
    """Per-capability availability for the connected Hopper instance.

    Hopper's publicly documented interfaces support:
    - Camera streaming over its own Wi-Fi AP
    - Autonomous block/JavaScript programs via Bluetooth (FTWCode.ai)

    Live velocity/position control is UNKNOWN until FTW publishes a
    supported external Python SDK.  Values below reflect that reality.
    """
    arm: CapabilityStatus = CapabilityStatus.UNKNOWN
    disarm: CapabilityStatus = CapabilityStatus.UNKNOWN
    takeoff: CapabilityStatus = CapabilityStatus.UNKNOWN
    land: CapabilityStatus = CapabilityStatus.UNKNOWN
    hold: CapabilityStatus = CapabilityStatus.UNKNOWN
    relative_move: CapabilityStatus = CapabilityStatus.UNKNOWN
    velocity: CapabilityStatus = CapabilityStatus.UNKNOWN
    position: CapabilityStatus = CapabilityStatus.UNKNOWN
    yaw: CapabilityStatus = CapabilityStatus.UNKNOWN
    camera: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    telemetry: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    battery: CapabilityStatus = CapabilityStatus.UNKNOWN
    program_upload: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    program_run: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    program_stop: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    payload: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    raw_imu: CapabilityStatus = CapabilityStatus.UNKNOWN
    tof: CapabilityStatus = CapabilityStatus.UNKNOWN

    def as_map(self) -> dict[str, CapabilityStatus]:
        return {
            CAP_ARM: self.arm,
            CAP_DISARM: self.disarm,
            CAP_TAKEOFF: self.takeoff,
            CAP_LAND: self.land,
            CAP_HOLD: self.hold,
            CAP_RELATIVE_MOVE: self.relative_move,
            CAP_VELOCITY: self.velocity,
            CAP_POSITION: self.position,
            CAP_YAW: self.yaw,
            CAP_CAMERA: self.camera,
            CAP_TELEMETRY: self.telemetry,
            CAP_BATTERY: self.battery,
            CAP_PROGRAM_UPLOAD: self.program_upload,
            CAP_PROGRAM_RUN: self.program_run,
            CAP_PROGRAM_STOP: self.program_stop,
            CAP_PAYLOAD: self.payload,
            CAP_RAW_IMU: self.raw_imu,
            CAP_TOF: self.tof,
        }

    def is_available(self, cap: str) -> bool:
        return self.as_map().get(cap) == CapabilityStatus.SUPPORTED

    def supported_list(self) -> list[str]:
        return [k for k, v in self.as_map().items() if v == CapabilityStatus.SUPPORTED]


def observe_mode_capabilities() -> HopperCapabilities:
    """Capabilities when only camera/telemetry links are established."""
    return HopperCapabilities(
        arm=CapabilityStatus.UNSUPPORTED,
        disarm=CapabilityStatus.UNSUPPORTED,
        takeoff=CapabilityStatus.UNSUPPORTED,
        land=CapabilityStatus.UNSUPPORTED,
        hold=CapabilityStatus.UNSUPPORTED,
        relative_move=CapabilityStatus.UNSUPPORTED,
        velocity=CapabilityStatus.UNSUPPORTED,
        position=CapabilityStatus.UNSUPPORTED,
        yaw=CapabilityStatus.UNSUPPORTED,
        camera=CapabilityStatus.SUPPORTED,
        telemetry=CapabilityStatus.SUPPORTED,
        battery=CapabilityStatus.SUPPORTED,
        program_upload=CapabilityStatus.UNSUPPORTED,
        program_run=CapabilityStatus.UNSUPPORTED,
        program_stop=CapabilityStatus.UNSUPPORTED,
    )


def program_mode_capabilities() -> HopperCapabilities:
    """Capabilities when the program/Bluetooth link is also established.

    Program upload/run/stop remain UNKNOWN — the official FTW Bluetooth
    program-upload interface has not been published yet.  Do not promote
    these to SUPPORTED without a real, verified transport in place.
    """
    caps = observe_mode_capabilities()
    caps.program_upload = CapabilityStatus.UNKNOWN
    caps.program_run = CapabilityStatus.UNKNOWN
    caps.program_stop = CapabilityStatus.UNKNOWN
    return caps


def live_control_capabilities() -> HopperCapabilities:
    """Capabilities when the official FTW live-control SDK is connected.

    This function exists so the architecture is ready; in practice all
    movement capabilities remain UNKNOWN until FTW publishes the SDK.
    """
    caps = program_mode_capabilities()
    # These remain UNKNOWN — do not promote to SUPPORTED without a real interface.
    caps.arm = CapabilityStatus.UNKNOWN
    caps.disarm = CapabilityStatus.UNKNOWN
    caps.takeoff = CapabilityStatus.UNKNOWN
    caps.land = CapabilityStatus.UNKNOWN
    caps.hold = CapabilityStatus.UNKNOWN
    caps.relative_move = CapabilityStatus.UNKNOWN
    caps.velocity = CapabilityStatus.UNKNOWN
    caps.position = CapabilityStatus.UNKNOWN
    caps.yaw = CapabilityStatus.UNKNOWN
    return caps
