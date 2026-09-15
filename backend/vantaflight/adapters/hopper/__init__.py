"""FTW Robotics Hopper adapter package.

Public surface:
  HopperAdapter           — main facade; implements DroneAdapter
  HopperConfig            — top-level configuration dataclass
  HopperConnectionState   — multi-link connection state enum
  HopperOperatingMode     — OBSERVE / PROGRAM_UPLOAD / LIVE_CONTROL
  HopperCapabilities      — per-capability availability
  HopperHealth            — per-link health snapshot
  SimpleMissionPlan       — thin mission plan type accepted by the compiler
  Errors:  HopperNotFound, HopperUnsupportedCapability, HopperBatteryCritical, …

Nothing outside this package should import from sub-modules directly.
"""
from .adapter import HopperAdapter
from .capabilities import HopperCapabilities
from .config import HopperConfig
from .errors import (
    HopperBatteryCritical,
    HopperCameraUnavailable,
    HopperCommandExpired,
    HopperControlUnavailable,
    HopperError,
    HopperLinkLost,
    HopperNotFound,
    HopperPreflightFailed,
    HopperProgramValidationFailed,
    HopperTelemetryUnavailable,
    HopperUnsupportedCapability,
)
from .models import (
    BatteryState,
    HopperConnectionState,
    HopperHealth,
    HopperOperatingMode,
)
from .programs import SimpleMissionPlan

__all__ = [
    "HopperAdapter",
    "HopperCapabilities",
    "HopperConfig",
    "HopperConnectionState",
    "HopperHealth",
    "HopperOperatingMode",
    "BatteryState",
    "SimpleMissionPlan",
    # errors
    "HopperError",
    "HopperNotFound",
    "HopperUnsupportedCapability",
    "HopperControlUnavailable",
    "HopperCameraUnavailable",
    "HopperTelemetryUnavailable",
    "HopperCommandExpired",
    "HopperLinkLost",
    "HopperBatteryCritical",
    "HopperPreflightFailed",
    "HopperProgramValidationFailed",
]
