"""Mission architecture — shared perception/execution stack with mission-specific planners.

Stack: VantaSense → VantaState → VantaMission → VantaMotion → VantaExecution → adapter

Mission types share perception and execution; each type provides its own
planner (VantaMission subclass) and optional payload handler.
"""
from .models import (
    MissionGoal,
    MissionPhase,
    MissionSpec,
    MissionStatus,
    MissionType,
    Waypoint,
)
from .planner import MissionPlanner
from .registry import MissionRegistry

__all__ = [
    "MissionGoal",
    "MissionPhase",
    "MissionPlanner",
    "MissionRegistry",
    "MissionSpec",
    "MissionStatus",
    "MissionType",
    "Waypoint",
]
