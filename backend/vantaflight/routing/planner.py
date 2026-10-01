"""Route planning: turn "visit these places" into a safe, efficient mission.

Given the stops to visit and the airspace, `plan_route`:

1. works out the safe flying distance between every pair of stops (and
   home), routing around no-fly zones,
2. finds the best visiting order (exact for up to 12 stops),
3. inserts the detour points each leg needs, and plans a safe way home,
4. budgets the battery leg by leg and finds the *point of no return*: the
   first stop from which the aircraft could no longer get home with the
   reserve intact.

The result is an ordinary `MissionPlan`, so it flies on any adapter and
exports to QGroundControl.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import BATTERY_LOW_PCT, MISSION_DEFAULT_SPEED_M_S, RTL_ALTITUDE_M
from ..mission.plan import FinishAction, MissionPlan, Waypoint
from ..mission.checks import (
    BATTERY_PCT_PER_S_AIRBORNE,
    CLIMB_RATE_M_S,
    DESCENT_RATE_M_S,
)
from .airspace import Airspace, SafePath
from .tour import solve, tour_length

HOME = (0.0, 0.0)


@dataclass
class StopBudget:
    stop: int  # index into the caller's stops
    arrival_battery_pct: float
    battery_to_get_home_pct: float
    margin_pct: float  # battery left after getting home from here, minus the reserve

    def to_dict(self) -> dict:
        return {
            "stop": self.stop,
            "arrival_battery_pct": round(self.arrival_battery_pct, 1),
            "battery_to_get_home_pct": round(self.battery_to_get_home_pct, 1),
            "margin_pct": round(self.margin_pct, 1),
        }


@dataclass
class RoutePlan:
    plan: MissionPlan
    order: list[int]  # caller's stop indices, in flying order
    method: str  # how the order was chosen: exact | heuristic | fixed
    given_order_m: float  # safe distance flying the stops in the order given
    distance_m: float  # safe distance of the planned route
    detour_points: int
    duration_s: float
    battery_used_pct: float
    battery_start_pct: float
    budgets: list[StopBudget] = field(default_factory=list)
    point_of_no_return: int | None = None  # caller's stop index, if any
    warnings: list[str] = field(default_factory=list)

    @property
    def saved_m(self) -> float:
        return max(0.0, self.given_order_m - self.distance_m)

    @property
    def feasible(self) -> bool:
        return self.point_of_no_return is None

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.model_dump(mode="json"),
            "order": self.order,
            "method": self.method,
            "given_order_m": round(self.given_order_m, 1),
            "distance_m": round(self.distance_m, 1),
            "saved_m": round(self.saved_m, 1),
            "saved_pct": round(100 * self.saved_m / self.given_order_m, 1) if self.given_order_m else 0.0,
            "detour_points": self.detour_points,
            "duration_s": round(self.duration_s, 1),
            "battery_used_pct": round(self.battery_used_pct, 1),
            "battery_start_pct": round(self.battery_start_pct, 1),
            "battery_end_pct": round(self.battery_start_pct - self.battery_used_pct, 1),
            "feasible": self.feasible,
            "point_of_no_return": self.point_of_no_return,
            "budgets": [b.to_dict() for b in self.budgets],
            "warnings": self.warnings,
        }


def plan_route(
    stops: list[Waypoint],
    airspace: Airspace,
    *,
    optimize_order: bool = True,
    finish: FinishAction = FinishAction.RETURN_HOME,
    speed_m_s: float = MISSION_DEFAULT_SPEED_M_S,
    battery_pct: float | None = None,
    reserve_pct: float = BATTERY_LOW_PCT,
    name: str = "Optimized route",
) -> RoutePlan:
    """Plan the best safe route through ``stops``. Raises `RouteError`."""
    if not stops:
        raise ValueError("give at least one stop to visit")
    for i, s in enumerate(stops):
        if math.hypot(s.x, s.y) > airspace.geofence.radius_m:
            raise ValueError(f"stop {i + 1} is outside the {airspace.geofence.radius_m:g} m geofence")

    points = [HOME, *[(s.x, s.y) for s in stops]]
    dist, paths = airspace.distance_matrix(points)
    returning = finish == FinishAction.RETURN_HOME

    given = list(range(1, len(stops) + 1))
    given_length = tour_length(dist, given, returning)
    if optimize_order:
        tour = solve(dist, returning)
        order, method = tour.order, tour.method
    else:
        order, method = given, "fixed"

    waypoints: list[Waypoint] = []
    detours = 0
    prev_idx, prev_alt = 0, stops[order[0] - 1].altitude
    for idx in order:
        stop = stops[idx - 1]
        leg = paths[(prev_idx, idx)]
        via_alt = max(prev_alt, stop.altitude)
        for x, y in leg.detours:
            waypoints.append(Waypoint(x=round(x, 2), y=round(y, 2), altitude=via_alt, kind="via"))
            detours += 1
        waypoints.append(stop.model_copy(update={"kind": "stop"}))
        prev_idx, prev_alt = idx, stop.altitude

    warnings: list[str] = []
    plan_finish = finish
    home_leg = paths[(prev_idx, 0)]
    if returning and home_leg.detours:
        # Native return-to-launch flies straight home; fly the safe path
        # ourselves instead and land at home.
        home_alt = max(prev_alt, RTL_ALTITUDE_M)
        for x, y in home_leg.detours:
            waypoints.append(Waypoint(x=round(x, 2), y=round(y, 2), altitude=home_alt, kind="via"))
            detours += 1
        waypoints.append(Waypoint(x=0.0, y=0.0, altitude=home_alt, kind="via"))
        plan_finish = FinishAction.LAND
        warnings.append("the straight line home crosses a no-fly zone, so the route flies around it and lands at home")

    plan = MissionPlan(name=name, waypoints=waypoints, speed_m_s=speed_m_s, finish=plan_finish)
    route_length = tour_length(dist, order, returning)

    # -- battery budget -------------------------------------------------------
    start = 100.0 if battery_pct is None else battery_pct
    if battery_pct is None:
        warnings.append("battery level unknown (not connected); assumed a full battery")
    budgets, used_s = [], stops[order[0] - 1].altitude / CLIMB_RATE_M_S
    point_of_no_return = None
    prev_idx, prev_alt = 0, stops[order[0] - 1].altitude
    for idx in order:
        stop = stops[idx - 1]
        leg = paths[(prev_idx, idx)]
        used_s += _leg_seconds(leg, prev_alt, stop.altitude, stop.speed_m_s or speed_m_s)
        arrival = start - used_s * BATTERY_PCT_PER_S_AIRBORNE
        home_s = paths[(idx, 0)].length_m / speed_m_s + max(stop.altitude, RTL_ALTITUDE_M) / DESCENT_RATE_M_S
        to_home = home_s * BATTERY_PCT_PER_S_AIRBORNE
        margin = arrival - to_home - reserve_pct
        budgets.append(StopBudget(idx - 1, arrival, to_home, margin))
        if margin < 0 and point_of_no_return is None:
            point_of_no_return = idx - 1
        used_s += stop.hold_s
        prev_idx, prev_alt = idx, stop.altitude
    if returning:
        home_alt = max(prev_alt, RTL_ALTITUDE_M)
        used_s += _leg_seconds(paths[(prev_idx, 0)], prev_alt, home_alt, speed_m_s)
        used_s += home_alt / DESCENT_RATE_M_S
    elif finish == FinishAction.LAND:
        used_s += prev_alt / DESCENT_RATE_M_S

    if point_of_no_return is not None:
        warnings.insert(0, (
            f"stop {point_of_no_return + 1} is past the point of no return: from there the aircraft "
            f"could not get home with the {reserve_pct:g}% reserve"
        ))

    return RoutePlan(
        plan=plan,
        order=[i - 1 for i in order],
        method=method,
        given_order_m=given_length,
        distance_m=route_length,
        detour_points=detours,
        duration_s=used_s,
        battery_used_pct=used_s * BATTERY_PCT_PER_S_AIRBORNE,
        battery_start_pct=start,
        budgets=budgets,
        point_of_no_return=point_of_no_return,
        warnings=warnings,
    )


def _leg_seconds(leg: SafePath, from_alt: float, to_alt: float, speed: float) -> float:
    vertical = to_alt - from_alt
    v_rate = CLIMB_RATE_M_S if vertical > 0 else DESCENT_RATE_M_S
    return max(leg.length_m / speed, abs(vertical) / v_rate)
