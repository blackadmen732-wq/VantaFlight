"""Configuration for the HopperAdapter and its connectors."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HopperCameraConfig:
    # Hopper serves its camera over its own Wi-Fi AP.
    # FTW documents the camera as viewable at http://192.168.2.1.
    # The specific stream path (MJPEG, HLS, etc.) is kept pluggable
    # because FTW has not published an external stream endpoint spec.
    base_url: str = "http://192.168.2.1"
    stream_path: str = "/"           # overridden when FTW publishes the path
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 2.0
    max_frame_age_s: float = 0.5     # frames older than this are discarded
    reconnect_delay_s: float = 3.0
    frame_buffer_size: int = 2       # always keep the freshest frame


@dataclass
class HopperBatteryConfig:
    # Published FTW hardware thresholds.
    reserve_pct: float = 30.0        # controller flashes red at ~30%
    critical_pct: float = 10.0       # solid red / "land now" at ~10%
    # VantaFlight adopts a more conservative mission threshold.
    mission_reserve_pct: float = 35.0


@dataclass
class HopperVehicleProfile:
    """Published FTW Hopper physical parameters (defaults from FTW docs)."""
    width_m: float = 0.178           # ~7 inches
    length_m: float = 0.178          # ~7 inches
    height_m: float = 0.05
    mass_g: float = 65.0             # with battery
    payload_limit_g: float = 30.0
    # Camera geometry — populated after physical calibration.
    camera_mount_angle_deg: float = 90.0   # 45 or 90
    camera_offset_x_m: float = 0.0
    camera_offset_y_m: float = 0.0
    camera_offset_z_m: float = 0.0
    # Hook geometry for payload alignment.
    hook_offset_x_m: float = 0.0
    hook_offset_y_m: float = 0.0
    hook_offset_z_m: float = 0.0


@dataclass
class HopperConfig:
    camera: HopperCameraConfig = field(default_factory=HopperCameraConfig)
    battery: HopperBatteryConfig = field(default_factory=HopperBatteryConfig)
    vehicle: HopperVehicleProfile = field(default_factory=HopperVehicleProfile)
    adapter_id: str = "hopper-0"
    # Maximum age for a command before the watchdog rejects it.
    command_ttl_s: float = 1.0
    # How long before declaring a link dead.
    link_timeout_s: float = 5.0
