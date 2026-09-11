"""MissionRegistry — creates and tracks mission instances."""
from __future__ import annotations

import time

from .models import MissionGoal, MissionSpec, MissionStatus, MissionType
from .planner import MissionPlanner, SearchPatternPlanner, WaypointFollower


class MissionRegistry:
    """Creates, stores, and looks up active missions."""

    def __init__(self) -> None:
        self._missions: dict[str, MissionSpec] = {}
        self._planners: dict[str, MissionPlanner] = {}
        self._counter = 0

    def create_mission(
        self,
        mission_type: MissionType,
        goal: MissionGoal,
    ) -> MissionSpec:
        self._counter += 1
        mission_id = f"mission_{self._counter:04d}"
        spec = MissionSpec(
            mission_id=mission_id,
            mission_type=mission_type,
            goal=goal,
        )
        self._missions[mission_id] = spec
        self._planners[mission_id] = self._build_planner(mission_type, goal)
        return spec

    def get(self, mission_id: str) -> MissionSpec | None:
        return self._missions.get(mission_id)

    def get_planner(self, mission_id: str) -> MissionPlanner | None:
        return self._planners.get(mission_id)

    def start(self, mission_id: str) -> MissionSpec:
        spec = self._missions.get(mission_id)
        if spec is None:
            raise ValueError(f"mission {mission_id} not found")
        if spec.status != MissionStatus.PENDING:
            raise RuntimeError(f"cannot start mission in status {spec.status.value}")
        spec.status = MissionStatus.ACTIVE
        return spec

    def complete(self, mission_id: str) -> MissionSpec:
        spec = self._missions.get(mission_id)
        if spec is None:
            raise ValueError(f"mission {mission_id} not found")
        spec.status = MissionStatus.COMPLETE
        return spec

    def abort(self, mission_id: str) -> MissionSpec:
        spec = self._missions.get(mission_id)
        if spec is None:
            raise ValueError(f"mission {mission_id} not found")
        spec.status = MissionStatus.ABORTED
        return spec

    def list_missions(self) -> list[dict]:
        return [m.to_dict() for m in self._missions.values()]

    @staticmethod
    def _build_planner(mission_type: MissionType, goal: MissionGoal) -> MissionPlanner:
        if mission_type == MissionType.SEARCH_RESCUE:
            planner = SearchPatternPlanner()
            if goal.search_area:
                planner.generate_pattern(goal.search_area)
            return planner
        return WaypointFollower()
