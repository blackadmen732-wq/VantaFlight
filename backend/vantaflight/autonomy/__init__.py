"""Closed-loop autonomy: camera → vision → scene → planner → execution."""
from .loop import AutonomyLoop, AutonomyConfig, AutonomyState
from .gate_progression import GateProgressionTracker, GatePassEvent

__all__ = [
    "AutonomyConfig",
    "AutonomyLoop",
    "AutonomyState",
    "GatePassEvent",
    "GateProgressionTracker",
]
