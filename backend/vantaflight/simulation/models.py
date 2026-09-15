"""Simulation data models — world, gates, sessions, faults."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

import numpy as np


class SimSessionState(str, enum.Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class FaultType(str, enum.Enum):
    CAMERA_DELAY = "CAMERA_DELAY"
    FRAME_DROP = "FRAME_DROP"
    BLUR = "BLUR"
    NOISE = "NOISE"
    OCCLUSION = "OCCLUSION"
    LIGHTING_CHANGE = "LIGHTING_CHANGE"
    PACKET_DELAY = "PACKET_DELAY"
    TELEMETRY_PAUSE = "TELEMETRY_PAUSE"
    PX4_DISCONNECT = "PX4_DISCONNECT"
    CAMERA_DISCONNECT = "CAMERA_DISCONNECT"


@dataclass
class FaultConfig:
    fault_type: FaultType
    probability: float = 0.0
    duration_s: float = 0.0
    magnitude: float = 0.0
    seed: int = 0

    def to_dict(self) -> dict:
        return {
            "fault_type": self.fault_type.value,
            "probability": self.probability,
            "duration_s": self.duration_s,
            "magnitude": self.magnitude,
            "seed": self.seed,
        }


@dataclass
class GazeboGate:
    """A gate placed in the simulation world."""
    gate_id: str
    position: np.ndarray
    normal: np.ndarray
    width: float = 1.5
    height: float = 1.5
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    color_bgr: tuple[int, int, int] = (0, 128, 255)
    order: int = 0

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "position": self.position.tolist(),
            "normal": self.normal.tolist(),
            "width": self.width,
            "height": self.height,
            "yaw_deg": round(self.yaw_deg, 1),
            "pitch_deg": round(self.pitch_deg, 1),
            "order": self.order,
        }


@dataclass
class SimulationWorld:
    """Describes a fully configured simulation environment."""
    course_id: str
    gates: list[GazeboGate] = field(default_factory=list)
    start_position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    start_yaw_deg: float = 0.0
    gravity: float = 9.81
    wind_speed_ms: float = 0.0
    wind_direction_deg: float = 0.0

    @property
    def gate_ids(self) -> list[str]:
        return [g.gate_id for g in self.gates]

    def gate_by_id(self, gate_id: str) -> GazeboGate | None:
        for g in self.gates:
            if g.gate_id == gate_id:
                return g
        return None

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "num_gates": len(self.gates),
            "gates": [g.to_dict() for g in self.gates],
            "start_position": self.start_position.tolist(),
            "start_yaw_deg": self.start_yaw_deg,
        }


@dataclass
class SimSessionConfig:
    """Configuration for a simulation run."""
    course_id: str = ""
    seed: int = 42
    max_time_s: float = 300.0
    faults: list[FaultConfig] = field(default_factory=list)
    record: bool = True
    headless: bool = False

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "seed": self.seed,
            "max_time_s": self.max_time_s,
            "faults": [f.to_dict() for f in self.faults],
            "record": self.record,
            "headless": self.headless,
        }
