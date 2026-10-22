"""QGroundControl ``.plan`` files: the format PX4 and ArduPilot users share.

Export lets a VantaFlight route open in QGroundControl (or upload to any
PX4/ArduPilot aircraft through it); import brings existing plans and their
no-fly polygons into VantaFlight. Plan files store latitude/longitude, so
both directions convert around a home position.

Supported: takeoff, waypoints (with hold time), per-leg speed changes,
return-to-launch and land items, exclusion polygons and circles. Anything
else (survey "complex items", camera triggers, DO_JUMP, ...) is skipped on
import and reported in the warnings rather than silently dropped.
"""
from __future__ import annotations

import math
from typing import Any

from ..config import MISSION_ACCEPT_RADIUS_M
from ..geo import global_to_local, local_to_global
from .plan import FinishAction, MissionPlan, Waypoint

# MAVLink command and frame numbers used by QGroundControl.
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_FRAME_GLOBAL = 0  # altitude above mean sea level
MAV_FRAME_MISSION = 2
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3  # altitude above home

_COMMAND_NAMES = {
    MAV_CMD_NAV_WAYPOINT: "waypoint",
    MAV_CMD_NAV_RETURN_TO_LAUNCH: "return to launch",
    MAV_CMD_NAV_LAND: "land",
    MAV_CMD_NAV_TAKEOFF: "takeoff",
    MAV_CMD_DO_CHANGE_SPEED: "change speed",
}

Home = tuple[float, float, float]  # latitude, longitude, altitude AMSL


def export_plan(
    plan: MissionPlan,
    home: Home,
    zones: list[list[tuple[float, float]]] | None = None,
    geofence_radius_m: float | None = None,
) -> dict[str, Any]:
    """Build a QGroundControl plan document for ``plan``."""
    lat0, lon0, alt0 = home
    items: list[dict[str, Any]] = []

    def add(command: int, frame: int, params: list[Any]) -> None:
        items.append({
            "autoContinue": True,
            "command": command,
            "doJumpId": len(items) + 1,
            "frame": frame,
            "params": params,
            "type": "SimpleItem",
        })

    if plan.waypoints:
        first_alt = plan.waypoints[0].altitude
        add(MAV_CMD_NAV_TAKEOFF, MAV_FRAME_GLOBAL_RELATIVE_ALT, [0, 0, 0, None, lat0, lon0, first_alt])

    speed = plan.speed_m_s
    for wp in plan.waypoints:
        leg_speed = plan.leg_speed(wp)
        if not math.isclose(leg_speed, speed):
            add(MAV_CMD_DO_CHANGE_SPEED, MAV_FRAME_MISSION, [1, leg_speed, -1, 0, 0, 0, 0])
            speed = leg_speed
        lat, lon = local_to_global(wp.x, wp.y, lat0, lon0)
        add(
            MAV_CMD_NAV_WAYPOINT, MAV_FRAME_GLOBAL_RELATIVE_ALT,
            [wp.hold_s, MISSION_ACCEPT_RADIUS_M, 0, None, round(lat, 8), round(lon, 8), wp.altitude],
        )

    if plan.finish == FinishAction.RETURN_HOME:
        add(MAV_CMD_NAV_RETURN_TO_LAUNCH, MAV_FRAME_MISSION, [0, 0, 0, 0, 0, 0, 0])
    elif plan.finish == FinishAction.LAND and plan.waypoints:
        last = plan.waypoints[-1]
        lat, lon = local_to_global(last.x, last.y, lat0, lon0)
        add(MAV_CMD_NAV_LAND, MAV_FRAME_GLOBAL_RELATIVE_ALT, [0, 0, 0, None, round(lat, 8), round(lon, 8), 0])

    polygons = [
        {
            "inclusion": False,
            "polygon": [list(map(lambda v: round(v, 8), local_to_global(x, y, lat0, lon0))) for x, y in zone],
            "version": 1,
        }
        for zone in (zones or [])
    ]
    circles = []
    if geofence_radius_m:
        circles.append({
            "circle": {"center": [lat0, lon0], "radius": geofence_radius_m},
            "inclusion": True,
            "version": 1,
        })

    return {
        "fileType": "Plan",
        "geoFence": {"circles": circles, "polygons": polygons, "version": 2},
        "groundStation": "VantaFlight",
        "mission": {
            "cruiseSpeed": plan.speed_m_s,
            "firmwareType": 12,  # PX4; ArduPilot reads the same items
            "hoverSpeed": plan.speed_m_s,
            "items": items,
            "plannedHomePosition": [lat0, lon0, alt0],
            "vehicleType": 2,  # multirotor
            "version": 2,
        },
        "rallyPoints": {"points": [], "version": 2},
        "version": 1,
    }


def import_plan(document: dict[str, Any], name: str = "Imported plan") -> dict[str, Any]:
    """Parse a QGroundControl plan into a `MissionPlan` plus no-fly zones.

    Returns ``{"plan": MissionPlan, "zones": [zone specs], "warnings": [...]}``.
    Raises ValueError if the document is not a usable plan.
    """
    if document.get("fileType") != "Plan" or "mission" not in document:
        raise ValueError("not a QGroundControl plan file (fileType must be 'Plan')")
    mission = document["mission"]
    home = mission.get("plannedHomePosition")
    if not (isinstance(home, list) and len(home) >= 2):
        raise ValueError("plan has no plannedHomePosition")
    lat0, lon0 = float(home[0]), float(home[1])
    alt0 = float(home[2]) if len(home) > 2 and home[2] is not None else 0.0

    speed = float(mission.get("cruiseSpeed") or 5.0)
    waypoints: list[Waypoint] = []
    finish = FinishAction.HOLD
    pending_speed: float | None = None
    skipped: dict[str, int] = {}

    for item in mission.get("items", []):
        if item.get("type") != "SimpleItem":
            label = str(item.get("complexItemType") or item.get("type") or "unknown item")
            skipped[label] = skipped.get(label, 0) + 1
            continue
        command = item.get("command")
        params = list(item.get("params", [])) + [None] * 7
        if command == MAV_CMD_NAV_WAYPOINT:
            lat, lon, alt = params[4], params[5], params[6]
            if lat is None or lon is None or alt is None:
                skipped["waypoint without a position"] = skipped.get("waypoint without a position", 0) + 1
                continue
            if item.get("frame") == MAV_FRAME_GLOBAL:
                alt = float(alt) - alt0
            x, y = global_to_local(float(lat), float(lon), lat0, lon0)
            waypoints.append(Waypoint(
                x=round(x, 2), y=round(y, 2), altitude=float(alt),
                hold_s=max(0.0, float(params[0] or 0.0)),
                speed_m_s=pending_speed,
            ))
            finish = FinishAction.HOLD
        elif command == MAV_CMD_DO_CHANGE_SPEED:
            if params[1] is not None and float(params[1]) > 0:
                pending_speed = float(params[1])
        elif command == MAV_CMD_NAV_RETURN_TO_LAUNCH:
            finish = FinishAction.RETURN_HOME
        elif command == MAV_CMD_NAV_LAND:
            finish = FinishAction.LAND
        elif command == MAV_CMD_NAV_TAKEOFF:
            continue  # VantaFlight takes off to the first waypoint's altitude itself
        else:
            label = _COMMAND_NAMES.get(command, f"MAVLink command {command}")
            skipped[label] = skipped.get(label, 0) + 1

    if not waypoints:
        raise ValueError("plan has no waypoints VantaFlight can fly")

    zones = []
    fence = document.get("geoFence") or {}
    for i, poly in enumerate(fence.get("polygons", [])):
        if poly.get("inclusion", True):
            continue
        vertices = [list(global_to_local(float(lat), float(lon), lat0, lon0)) for lat, lon in poly.get("polygon", [])]
        zones.append({"name": f"Imported zone {len(zones) + 1}", "vertices": vertices})
    for circle in fence.get("circles", []):
        if circle.get("inclusion", True):
            continue
        c = circle.get("circle", {})
        cx, cy = global_to_local(float(c["center"][0]), float(c["center"][1]), lat0, lon0)
        zones.append({"name": f"Imported zone {len(zones) + 1}", "center": [cx, cy], "radius": float(c["radius"])})

    warnings = [f"skipped {n} unsupported item(s): {label}" for label, n in skipped.items()]
    plan = MissionPlan(name=name, waypoints=waypoints, speed_m_s=speed, finish=finish)
    return {"plan": plan, "zones": zones, "warnings": warnings, "home": [lat0, lon0, alt0]}
