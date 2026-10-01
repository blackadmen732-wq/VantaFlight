"""Pre-flight mission checks: is this plan flyable, and what will it cost?

Everything here is pure and side-effect free, so the UI can validate a plan
as the operator edits it, and the runner re-validates before every launch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import (
    BATTERY_LOW_PCT,
    MISSION_MAX_SPEED_M_S,
    MISSION_MAX_WAYPOINTS,
)
from ..safety import Geofence
from .plan import FinishAction, MissionPlan

# Conservative planning figures: real aircraft vary, so the estimate is a
# guide for the operator, not a guarantee. Matches the simulator's drain.
CLIMB_RATE_M_S = 1.5
DESCENT_RATE_M_S = 1.2
BATTERY_PCT_PER_S_AIRBORNE = 0.15
MIN_WAYPOINT_ALTITUDE_M = 1.0


@dataclass
class PlanReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    distance_m: float = 0.0
    estimated_duration_s: float = 0.0
    estimated_battery_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "distance_m": round(self.distance_m, 1),
            "estimated_duration_s": round(self.estimated_duration_s, 1),
            "estimated_battery_pct": round(self.estimated_battery_pct, 1),
        }


def estimate_duration_s(plan: MissionPlan) -> float:
    """Rough flight time: climb, each leg at its speed, loiters, and the finish."""
    if not plan.waypoints:
        return 0.0
    first = plan.waypoints[0]
    seconds = first.altitude / CLIMB_RATE_M_S
    px, py, pz = 0.0, 0.0, first.altitude
    for wp in plan.waypoints:
        horizontal = math.hypot(wp.x - px, wp.y - py)
        vertical = wp.altitude - pz
        v_rate = CLIMB_RATE_M_S if vertical > 0 else DESCENT_RATE_M_S
        seconds += max(horizontal / plan.leg_speed(wp), abs(vertical) / v_rate)
        seconds += wp.hold_s
        px, py, pz = wp.x, wp.y, wp.altitude
    if plan.finish == FinishAction.RETURN_HOME:
        seconds += math.hypot(px, py) / plan.speed_m_s
    if plan.finish != FinishAction.HOLD:
        seconds += pz / DESCENT_RATE_M_S
    return seconds


def check_plan(
    plan: MissionPlan,
    geofence: Geofence | None = None,
    battery_pct: float | None = None,
    battery_reserve_pct: float = BATTERY_LOW_PCT,
) -> PlanReport:
    """Validate ``plan`` against the envelope and (optionally) the battery."""
    fence = geofence or Geofence()
    errors: list[str] = []
    warnings: list[str] = []

    n = len(plan.waypoints)
    if n == 0:
        errors.append("a mission needs at least one waypoint")
    if n > MISSION_MAX_WAYPOINTS:
        errors.append(f"too many waypoints ({n}); the limit is {MISSION_MAX_WAYPOINTS}")
    if not 0.5 <= plan.speed_m_s <= MISSION_MAX_SPEED_M_S:
        errors.append(f"speed must be between 0.5 and {MISSION_MAX_SPEED_M_S:g} m/s")

    for i, wp in enumerate(plan.waypoints, start=1):
        if wp.altitude < MIN_WAYPOINT_ALTITUDE_M:
            errors.append(f"waypoint {i}: altitude must be at least {MIN_WAYPOINT_ALTITUDE_M:g} m")
        breach = fence.violation(wp.x, wp.y, wp.altitude)
        if breach:
            errors.append(f"waypoint {i}: {breach}")
        if wp.speed_m_s is not None and not 0.5 <= wp.speed_m_s <= MISSION_MAX_SPEED_M_S:
            errors.append(f"waypoint {i}: speed must be between 0.5 and {MISSION_MAX_SPEED_M_S:g} m/s")

    report = PlanReport(valid=False, errors=errors, warnings=warnings)
    if errors:
        return report

    report.distance_m = plan.path_length_m()
    report.estimated_duration_s = estimate_duration_s(plan)
    report.estimated_battery_pct = report.estimated_duration_s * BATTERY_PCT_PER_S_AIRBORNE

    if battery_pct is not None:
        remaining = battery_pct - report.estimated_battery_pct
        if remaining < battery_reserve_pct:
            errors.append(
                f"not enough battery: needs ~{report.estimated_battery_pct:.0f}%, "
                f"would land with ~{max(remaining, 0):.0f}% (reserve is {battery_reserve_pct:.0f}%)"
            )
        elif remaining < battery_reserve_pct + 10:
            warnings.append(f"tight battery margin: ~{remaining:.0f}% left at the end")

    report.valid = not errors
    return report
