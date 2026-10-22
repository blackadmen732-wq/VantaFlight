"""Missions.

Two layers live here and share one package:

* Autonomous waypoint missions (plan, check, fly): ``MissionPlan``,
  ``check_plan``, ``MissionRunner``, the survey/orbit/square patterns and
  QGroundControl ``.plan`` import/export. These are what the operator flies.
* The V0.9 mission architecture (mission types, goals, strategy and the
  per-type planners): ``MissionSpec``, ``MissionGoal``, ``MissionRegistry``.
  Its waypoint and phase types are exported as ``GoalWaypoint`` and
  ``GoalPhase`` so they do not shadow the flyable ``Waypoint`` and the
  runner's ``MissionPhase``.

Stack: VantaSense → VantaState → VantaMission → VantaMotion → VantaExecution → adapter
"""
from .checks import PlanReport, check_plan, estimate_duration_s
from .models import (
    MissionGoal,
    MissionSpec,
    MissionStatus,
    MissionType,
)
from .models import MissionPhase as GoalPhase
from .models import Waypoint as GoalWaypoint
from .patterns import PATTERNS, build_pattern
from .plan import FinishAction, MissionPlan, Waypoint
from .planner import MissionPlanner
from .qgc import export_plan, import_plan
from .registry import MissionRegistry
from .runner import MissionPhase, MissionRunner, MissionState

__all__ = [
    "FinishAction",
    "GoalPhase",
    "GoalWaypoint",
    "MissionGoal",
    "MissionPhase",
    "MissionPlan",
    "MissionPlanner",
    "MissionRegistry",
    "MissionRunner",
    "MissionSpec",
    "MissionState",
    "MissionStatus",
    "MissionType",
    "PATTERNS",
    "PlanReport",
    "Waypoint",
    "build_pattern",
    "check_plan",
    "estimate_duration_s",
    "export_plan",
    "import_plan",
]
