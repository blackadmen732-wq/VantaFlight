"""The flight envelope: how far and how high the aircraft may go.

Positions are local ENU metres relative to home (where the session started):
``x`` east, ``y`` north, ``altitude`` up. The same `Geofence` is shared by the
command validator (reject a goto outside the fence), the mission planner
(reject a plan that leaves the fence), and the failsafe guardian (bring the
aircraft back if it drifts out anyway).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import GEOFENCE_MAX_ALTITUDE_M, GEOFENCE_RADIUS_M


@dataclass(frozen=True)
class Geofence:
    radius_m: float = GEOFENCE_RADIUS_M
    max_altitude_m: float = GEOFENCE_MAX_ALTITUDE_M

    def violation(self, x: float, y: float, altitude: float) -> str | None:
        """Describe why a point is outside the fence, or None if it is inside."""
        distance = math.hypot(x, y)
        if distance > self.radius_m:
            return (
                f"{distance:.1f} m from home exceeds the "
                f"{self.radius_m:.0f} m geofence radius"
            )
        if altitude > self.max_altitude_m:
            return (
                f"altitude {altitude:.1f} m exceeds the "
                f"{self.max_altitude_m:.0f} m geofence ceiling"
            )
        return None

    def to_dict(self) -> dict:
        return {"radius_m": self.radius_m, "max_altitude_m": self.max_altitude_m}
