"""Mission data models — shared across all mission types."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class MissionType(str, enum.Enum):
    RACE = "RACE"
    SEARCH_RESCUE = "SEARCH_RESCUE"
    PACKAGE_DELIVERY = "PACKAGE_DELIVERY"
    EMERGENCY_RESPONSE = "EMERGENCY_RESPONSE"


class MissionPhase(str, enum.Enum):
    PREFLIGHT = "PREFLIGHT"
    TAKEOFF = "TAKEOFF"
    CRUISE = "CRUISE"
    APPROACH = "APPROACH"
    EXECUTE = "EXECUTE"
    RETURN = "RETURN"
    LANDING = "LANDING"
    COMPLETE = "COMPLETE"
    ABORT = "ABORT"


class MissionStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float
    z: float
    speed_m_s: float = 5.0
    heading_deg: float | None = None
    hold_s: float = 0.0
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
            "speed_m_s": round(self.speed_m_s, 2),
            "heading_deg": round(self.heading_deg, 1) if self.heading_deg is not None else None,
            "hold_s": self.hold_s,
            "label": self.label,
        }


@dataclass(frozen=True)
class MissionGoal:
    description: str
    waypoints: tuple[Waypoint, ...] = ()
    search_area: tuple[tuple[float, float, float], ...] = ()
    delivery_target: tuple[float, float, float] | None = None
    return_home: bool = True
    max_duration_s: float = 600.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "waypoints": [w.to_dict() for w in self.waypoints],
            "search_area": [list(v) for v in self.search_area],
            "delivery_target": list(self.delivery_target) if self.delivery_target else None,
            "return_home": self.return_home,
            "max_duration_s": self.max_duration_s,
        }


@dataclass
class MissionSpec:
    mission_id: str
    mission_type: MissionType
    goal: MissionGoal
    status: MissionStatus = MissionStatus.PENDING
    phase: MissionPhase = MissionPhase.PREFLIGHT
    elapsed_s: float = 0.0
    waypoints_reached: int = 0

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "mission_type": self.mission_type.value,
            "goal": self.goal.to_dict(),
            "status": self.status.value,
            "phase": self.phase.value,
            "elapsed_s": round(self.elapsed_s, 3),
            "waypoints_reached": self.waypoints_reached,
        }
