"""Mission plans: an ordered list of waypoints plus what to do at the end.

Coordinates are local ENU metres relative to home (where the session
started): ``x`` east, ``y`` north, ``altitude`` above home. The same plan
therefore flies identically on the simulator and on PX4.
"""
from __future__ import annotations

import enum
import math

from pydantic import BaseModel, Field

from ..config import MISSION_DEFAULT_SPEED_M_S


class FinishAction(str, enum.Enum):
    LAND = "land"            # land at the last waypoint
    RETURN_HOME = "return_home"  # fly home and land there
    HOLD = "hold"            # stay hovering at the last waypoint


class Waypoint(BaseModel):
    x: float = Field(description="metres east of home")
    y: float = Field(description="metres north of home")
    altitude: float = Field(description="metres above home")
    hold_s: float = Field(default=0.0, ge=0.0, le=600.0, description="loiter time on arrival")
    speed_m_s: float | None = Field(default=None, description="leg speed; plan default if unset")


class MissionPlan(BaseModel):
    name: str = "Untitled mission"
    waypoints: list[Waypoint]
    speed_m_s: float = MISSION_DEFAULT_SPEED_M_S
    finish: FinishAction = FinishAction.RETURN_HOME

    def leg_speed(self, wp: Waypoint) -> float:
        return wp.speed_m_s if wp.speed_m_s is not None else self.speed_m_s

    def path_length_m(self, start: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> float:
        """3D length of the path from ``start`` through every waypoint."""
        total = 0.0
        px, py, pz = start
        for wp in self.waypoints:
            total += math.dist((px, py, pz), (wp.x, wp.y, wp.altitude))
            px, py, pz = wp.x, wp.y, wp.altitude
        return total
