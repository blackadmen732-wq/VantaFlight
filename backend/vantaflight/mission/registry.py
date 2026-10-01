"""MissionRegistry — creates and tracks mission instances."""
from __future__ import annotations

import time

from .models import MissionGoal, MissionPhase, MissionSpec, MissionStatus, MissionType
from .planner import MissionPlanner, SearchPatternPlanner, WaypointFollower


class MissionRegistry:
    """Creates, stores, and looks up active missions."""

    def __init__(self) -> None:
        self._missions: dict[str, MissionSpec] = {}
        self._planners: dict[str, MissionPlanner] = {}
        self._counter = 0

    def restore(self, rows: list[dict]) -> int:
        """Load missions persisted by a previous run (``FlightDatabase`` rows).

        Must run before ``create_mission`` so new IDs continue after the
        highest stored one instead of reusing (and overwriting) old missions.
        Returns the number of missions restored.
        """
        restored = 0
        for row in rows:
            mission_id = str(row["mission_id"])
            try:
                mission_type = MissionType(row["mission_type"])
                goal = MissionGoal.from_dict(row.get("goal") or {})
                status = MissionStatus(row.get("status") or MissionStatus.PENDING.value)
                phase = MissionPhase(row.get("phase") or MissionPhase.PREFLIGHT.value)
            except (KeyError, TypeError, ValueError):
                continue
            # A mission that was running when the app stopped did not finish.
            if status in (MissionStatus.ACTIVE, MissionStatus.PAUSED):
                status = MissionStatus.ABORTED
            self._missions[mission_id] = MissionSpec(
                mission_id=mission_id,
                mission_type=mission_type,
                goal=goal,
                status=status,
                phase=phase,
                elapsed_s=float(row.get("elapsed_s") or 0.0),
                waypoints_reached=int(row.get("waypoints_reached") or 0),
            )
            self._planners[mission_id] = self._build_planner(mission_type, goal)
            self._counter = max(self._counter, _sequence_of(mission_id))
            restored += 1
        return restored

    def create_mission(
        self,
        mission_type: MissionType,
        goal: MissionGoal,
    ) -> MissionSpec:
        self._counter += 1
        mission_id = f"mission_{self._counter:04d}"
        while mission_id in self._missions:  # never hand out a used ID
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


def _sequence_of(mission_id: str) -> int:
    prefix, _, number = mission_id.rpartition("_")
    return int(number) if prefix == "mission" and number.isdigit() else 0
