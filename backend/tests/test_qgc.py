"""QGroundControl .plan export and import."""
from __future__ import annotations

import json

import pytest

from vantaflight.mission import FinishAction, MissionPlan, Waypoint, export_plan, import_plan
from vantaflight.mission.qgc import (
    MAV_CMD_DO_CHANGE_SPEED,
    MAV_CMD_NAV_LAND,
    MAV_CMD_NAV_RETURN_TO_LAUNCH,
    MAV_CMD_NAV_TAKEOFF,
    MAV_CMD_NAV_WAYPOINT,
)

HOME = (47.397742, 8.545594, 488.0)

PLAN = MissionPlan(
    name="Inspection",
    speed_m_s=6,
    finish=FinishAction.RETURN_HOME,
    waypoints=[
        Waypoint(x=20, y=10, altitude=12, hold_s=5),
        Waypoint(x=-15, y=40, altitude=15, speed_m_s=3),
        Waypoint(x=0, y=60, altitude=15),
    ],
)


def test_export_structure_matches_qgroundcontrol():
    doc = export_plan(PLAN, HOME)
    json.dumps(doc)  # must be plain JSON
    assert doc["fileType"] == "Plan" and doc["version"] == 1
    mission = doc["mission"]
    assert mission["plannedHomePosition"] == list(HOME)
    commands = [item["command"] for item in mission["items"]]
    assert commands == [
        MAV_CMD_NAV_TAKEOFF,
        MAV_CMD_NAV_WAYPOINT,
        MAV_CMD_DO_CHANGE_SPEED,
        MAV_CMD_NAV_WAYPOINT,
        MAV_CMD_DO_CHANGE_SPEED,  # back to the plan speed
        MAV_CMD_NAV_WAYPOINT,
        MAV_CMD_NAV_RETURN_TO_LAUNCH,
    ]
    assert [item["doJumpId"] for item in mission["items"]] == list(range(1, 8))
    first_wp = mission["items"][1]
    assert first_wp["frame"] == 3 and first_wp["params"][0] == 5  # hold time
    assert first_wp["params"][6] == 12


def test_round_trip_preserves_the_plan():
    result = import_plan(export_plan(PLAN, HOME), name="Inspection")
    plan = result["plan"]
    assert plan.finish == FinishAction.RETURN_HOME
    assert plan.speed_m_s == 6
    assert len(plan.waypoints) == 3
    for got, want in zip(plan.waypoints, PLAN.waypoints):
        assert (got.x, got.y, got.altitude, got.hold_s) == pytest.approx(
            (want.x, want.y, want.altitude, want.hold_s), abs=0.01
        )
    assert plan.waypoints[1].speed_m_s == 3
    assert result["warnings"] == []


def test_land_finish_round_trip():
    plan = PLAN.model_copy(update={"finish": FinishAction.LAND})
    doc = export_plan(plan, HOME)
    assert doc["mission"]["items"][-1]["command"] == MAV_CMD_NAV_LAND
    assert import_plan(doc)["plan"].finish == FinishAction.LAND


def test_zones_and_fence_round_trip():
    zone = [(30.0, 30.0), (50.0, 30.0), (50.0, 50.0)]
    doc = export_plan(PLAN, HOME, zones=[zone], geofence_radius_m=150)
    fence = doc["geoFence"]
    assert fence["polygons"][0]["inclusion"] is False
    assert fence["circles"][0]["inclusion"] is True and fence["circles"][0]["circle"]["radius"] == 150
    zones = import_plan(doc)["zones"]
    assert len(zones) == 1  # the inclusion fence is not a no-fly zone
    assert zones[0]["vertices"] == [pytest.approx(list(v), abs=0.01) for v in zone]


def test_import_reports_unsupported_items():
    doc = export_plan(PLAN, HOME)
    doc["mission"]["items"].insert(2, {"type": "ComplexItem", "complexItemType": "survey"})
    doc["mission"]["items"].insert(3, {"type": "SimpleItem", "command": 206, "params": [0] * 7})
    result = import_plan(doc)
    assert len(result["plan"].waypoints) == 3
    assert any("survey" in w for w in result["warnings"])
    assert any("206" in w for w in result["warnings"])


def test_import_amsl_altitudes():
    doc = export_plan(PLAN, HOME)
    item = doc["mission"]["items"][1]
    item["frame"] = 0
    item["params"][6] = HOME[2] + 12
    assert import_plan(doc)["plan"].waypoints[0].altitude == pytest.approx(12)


def test_import_exclusion_circle():
    doc = export_plan(PLAN, HOME)
    doc["geoFence"]["circles"].append({"circle": {"center": [HOME[0], HOME[1]], "radius": 9}, "inclusion": False})
    zones = import_plan(doc)["zones"]
    assert zones[0]["radius"] == 9 and zones[0]["center"] == pytest.approx([0, 0], abs=1e-6)


@pytest.mark.parametrize(
    "doc, needle",
    [
        ({"fileType": "Mission"}, "not a QGroundControl plan"),
        ({"fileType": "Plan", "mission": {"items": []}}, "plannedHomePosition"),
        ({"fileType": "Plan", "mission": {"plannedHomePosition": [1, 2, 3], "items": []}}, "no waypoints"),
    ],
)
def test_import_rejects_unusable_files(doc, needle):
    with pytest.raises(ValueError, match=needle):
        import_plan(doc)
