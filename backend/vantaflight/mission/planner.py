"""MissionPlanner — base class for mission-specific planning.

Each mission type subclasses this and implements plan_step(), which
receives the current aircraft state and mission state, and returns
a desired waypoint or trajectory segment. The generic execution
stack (VantaExecution) converts the output to PX4 offboard commands.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np

from .models import MissionPhase, MissionSpec, Waypoint


@dataclass
class MissionCommand:
    target: Waypoint
    phase: MissionPhase
    confidence: float = 1.0


class MissionPlanner(abc.ABC):
    """Base mission planner. Subclass per mission type."""

    @abc.abstractmethod
    def plan_step(
        self,
        position: np.ndarray,
        velocity: np.ndarray,
        mission: MissionSpec,
    ) -> MissionCommand | None:
        ...

    @abc.abstractmethod
    def is_phase_complete(self, position: np.ndarray, mission: MissionSpec) -> bool:
        ...


class WaypointFollower(MissionPlanner):
    """Simple waypoint-following planner shared across mission types."""

    def __init__(self, arrival_radius: float = 1.0) -> None:
        self._arrival_radius = arrival_radius

    def plan_step(
        self,
        position: np.ndarray,
        velocity: np.ndarray,
        mission: MissionSpec,
    ) -> MissionCommand | None:
        waypoints = mission.goal.waypoints
        if mission.waypoints_reached >= len(waypoints):
            return None
        target = waypoints[mission.waypoints_reached]
        return MissionCommand(target=target, phase=mission.phase)

    def is_phase_complete(self, position: np.ndarray, mission: MissionSpec) -> bool:
        waypoints = mission.goal.waypoints
        if mission.waypoints_reached >= len(waypoints):
            return True
        target = waypoints[mission.waypoints_reached]
        target_pos = np.array([target.x, target.y, target.z])
        distance = float(np.linalg.norm(position - target_pos))
        return distance < self._arrival_radius


class SearchPatternPlanner(MissionPlanner):
    """Lawnmower search pattern for Search & Rescue missions."""

    def __init__(self, sweep_spacing: float = 5.0, altitude: float = 10.0) -> None:
        self._sweep_spacing = sweep_spacing
        self._altitude = altitude
        self._pattern: list[Waypoint] = []
        self._pattern_index = 0

    def generate_pattern(self, search_area: tuple[tuple[float, float, float], ...]) -> None:
        if len(search_area) < 2:
            return
        xs = [p[0] for p in search_area]
        ys = [p[1] for p in search_area]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        self._pattern = []
        y = min_y
        forward = True
        while y <= max_y:
            if forward:
                self._pattern.append(Waypoint(min_x, y, self._altitude, label="sweep"))
                self._pattern.append(Waypoint(max_x, y, self._altitude, label="sweep"))
            else:
                self._pattern.append(Waypoint(max_x, y, self._altitude, label="sweep"))
                self._pattern.append(Waypoint(min_x, y, self._altitude, label="sweep"))
            y += self._sweep_spacing
            forward = not forward
        self._pattern_index = 0

    def plan_step(
        self,
        position: np.ndarray,
        velocity: np.ndarray,
        mission: MissionSpec,
    ) -> MissionCommand | None:
        if not self._pattern or self._pattern_index >= len(self._pattern):
            return None
        target = self._pattern[self._pattern_index]
        return MissionCommand(target=target, phase=MissionPhase.EXECUTE)

    def is_phase_complete(self, position: np.ndarray, mission: MissionSpec) -> bool:
        if not self._pattern or self._pattern_index >= len(self._pattern):
            return True
        target = self._pattern[self._pattern_index]
        dist = float(np.linalg.norm(position - np.array([target.x, target.y, target.z])))
        if dist < 2.0:
            self._pattern_index += 1
        return self._pattern_index >= len(self._pattern)
