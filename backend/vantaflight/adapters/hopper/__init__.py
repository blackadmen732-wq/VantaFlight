"""FTW Robotics Hopper adapter package."""
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
from .vision_source import HopperVisionCameraSource

__all__ = [
    "HopperAdapter",
    "HopperCapabilities",
    "HopperConfig",
    "HopperConnectionState",
    "HopperHealth",
    "HopperOperatingMode",
    "HopperVisionCameraSource",
    "BatteryState",
    "SimpleMissionPlan",
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
