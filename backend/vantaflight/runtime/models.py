"""Runtime data models — profiles, service states, deployment modes."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class DeploymentMode(str, enum.Enum):
    LOCAL_GROUND = "LOCAL_GROUND"
    SPLIT_GROUND = "SPLIT_GROUND"
    ONBOARD_FUTURE = "ONBOARD_FUTURE"


class ServiceState(str, enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


class RuntimeProfile(str, enum.Enum):
    QUALITY = "QUALITY"
    BALANCED = "BALANCED"
    LOW_LATENCY = "LOW_LATENCY"
    COMPETITION = "COMPETITION"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class RuntimeProfileConfig:
    """Tuning knobs controlled by the selected RuntimeProfile."""
    profile: RuntimeProfile = RuntimeProfile.BALANCED
    capture_width: int = 640
    capture_height: int = 480
    processing_width: int = 640
    processing_height: int = 480
    preview_width: int = 320
    preview_height: int = 240
    detector_cadence_frames: int = 3
    tracker_cadence_frames: int = 1
    max_frame_age_s: float = 0.15
    preview_fps: float = 15.0
    twin_rate_hz: float = 10.0
    telemetry_rate_hz: float = 10.0
    recorder_enabled: bool = True
    analyzer_enabled: bool = True
    roi_enabled: bool = True

    @classmethod
    def for_profile(cls, profile: RuntimeProfile) -> RuntimeProfileConfig:
        if profile == RuntimeProfile.QUALITY:
            return cls(
                profile=profile, capture_width=1280, capture_height=720,
                processing_width=1280, processing_height=720,
                preview_width=640, preview_height=360,
                detector_cadence_frames=1, max_frame_age_s=0.25,
                preview_fps=30.0, twin_rate_hz=30.0,
            )
        if profile == RuntimeProfile.LOW_LATENCY:
            return cls(
                profile=profile, capture_width=640, capture_height=480,
                processing_width=320, processing_height=240,
                preview_width=160, preview_height=120,
                detector_cadence_frames=5, max_frame_age_s=0.05,
                preview_fps=10.0, twin_rate_hz=5.0,
            )
        if profile == RuntimeProfile.COMPETITION:
            return cls(
                profile=profile, capture_width=640, capture_height=480,
                processing_width=640, processing_height=480,
                preview_width=320, preview_height=240,
                detector_cadence_frames=3, max_frame_age_s=0.08,
                preview_fps=10.0, twin_rate_hz=10.0,
                analyzer_enabled=False,
            )
        return cls(profile=profile)


@dataclass
class ComputeNodeInfo:
    """Status of a local or remote compute node."""
    node_id: str = "local"
    protocol_version: int = 1
    runtime_version: str = "0.9.0"
    deployment_mode: DeploymentMode = DeploymentMode.LOCAL_GROUND
    camera_ready: bool = False
    vision_ready: bool = False
    race_ready: bool = False
    cpu_load: float = 0.0
    latency_health_ms: float = 0.0
    uptime_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "protocol_version": self.protocol_version,
            "runtime_version": self.runtime_version,
            "deployment_mode": self.deployment_mode.value,
            "camera_ready": self.camera_ready,
            "vision_ready": self.vision_ready,
            "race_ready": self.race_ready,
            "cpu_load": round(self.cpu_load, 2),
            "latency_health_ms": round(self.latency_health_ms, 2),
            "uptime_s": round(self.uptime_s, 1),
        }
