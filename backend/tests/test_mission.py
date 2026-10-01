"""Tests for Mission Architecture — models, planners, and registry."""
from __future__ import annotations

import numpy as np
import pytest

from vantaflight.mission.models import (
    MissionGoal,
    MissionPhase,
    MissionSpec,
    MissionStatus,
    MissionType,
    Waypoint,
)
from vantaflight.mission.planner import (
    MissionCommand,
    SearchPatternPlanner,
    WaypointFollower,
)
from vantaflight.mission.registry import MissionRegistry


class TestWaypoint:

    def test_serialization(self):
        w = Waypoint(1.0, 2.0, 3.0, speed_m_s=4.0, heading_deg=90.0, label="start")
        d = w.to_dict()
        assert d["x"] == 1.0
        assert d["heading_deg"] == 90.0
        assert d["label"] == "start"

    def test_frozen(self):
        w = Waypoint(0, 0, 0)
        with pytest.raises(AttributeError):
            w.x = 99


class TestMissionGoal:

    def test_serialization(self):
        goal = MissionGoal(
            description="Deliver to rooftop",
            waypoints=(Waypoint(0, 0, 10), Waypoint(50, 50, 10)),
            delivery_target=(50.0, 50.0, 0.0),
            return_home=True,
            max_duration_s=300.0,
        )
        d = goal.to_dict()
        assert d["description"] == "Deliver to rooftop"
        assert len(d["waypoints"]) == 2
        assert d["delivery_target"] == [50.0, 50.0, 0.0]


class TestMissionSpec:

    def test_defaults(self):
        spec = MissionSpec(
            mission_id="m1",
            mission_type=MissionType.RACE,
            goal=MissionGoal(description="test"),
        )
        assert spec.status == MissionStatus.PENDING
        assert spec.phase == MissionPhase.PREFLIGHT
        assert spec.waypoints_reached == 0

    def test_serialization(self):
        spec = MissionSpec(
            mission_id="m1",
            mission_type=MissionType.SEARCH_RESCUE,
            goal=MissionGoal(description="search area"),
            status=MissionStatus.ACTIVE,
            phase=MissionPhase.EXECUTE,
        )
        d = spec.to_dict()
        assert d["mission_type"] == "SEARCH_RESCUE"
        assert d["status"] == "ACTIVE"


class TestWaypointFollower:

    def test_follows_waypoints_in_order(self):
        planner = WaypointFollower(arrival_radius=1.0)
        goal = MissionGoal(
            description="3-waypoint route",
            waypoints=(
                Waypoint(10, 0, 5),
                Waypoint(20, 0, 5),
                Waypoint(30, 0, 5),
            ),
        )
        spec = MissionSpec("m1", MissionType.PACKAGE_DELIVERY, goal, status=MissionStatus.ACTIVE)
        pos = np.array([0, 0, 5.0])
        vel = np.zeros(3)

        cmd = planner.plan_step(pos, vel, spec)
        assert cmd is not None
        assert cmd.target.x == 10

    def test_returns_none_when_all_reached(self):
        planner = WaypointFollower()
        goal = MissionGoal(description="empty", waypoints=())
        spec = MissionSpec("m1", MissionType.RACE, goal)
        cmd = planner.plan_step(np.zeros(3), np.zeros(3), spec)
        assert cmd is None

    def test_phase_complete_at_final_waypoint(self):
        planner = WaypointFollower(arrival_radius=1.0)
        goal = MissionGoal(description="one wp", waypoints=(Waypoint(5, 0, 0),))
        spec = MissionSpec("m1", MissionType.RACE, goal)
        assert not planner.is_phase_complete(np.array([0, 0, 0.0]), spec)
        assert planner.is_phase_complete(np.array([5.0, 0, 0.0]), spec)


class TestSearchPatternPlanner:

    def test_generates_lawnmower_pattern(self):
        planner = SearchPatternPlanner(sweep_spacing=5.0, altitude=10.0)
        area = ((0, 0, 0), (20, 0, 0), (20, 10, 0), (0, 10, 0))
        planner.generate_pattern(area)
        assert len(planner._pattern) >= 4

    def test_returns_commands_from_pattern(self):
        planner = SearchPatternPlanner(sweep_spacing=5.0)
        area = ((0, 0, 0), (10, 10, 0))
        planner.generate_pattern(area)

        goal = MissionGoal(description="search", search_area=area)
        spec = MissionSpec("m1", MissionType.SEARCH_RESCUE, goal)
        cmd = planner.plan_step(np.zeros(3), np.zeros(3), spec)
        assert cmd is not None
        assert cmd.phase == MissionPhase.EXECUTE

    def test_empty_search_area(self):
        planner = SearchPatternPlanner()
        planner.generate_pattern(())
        assert planner._pattern == []


class TestMissionRegistry:

    def test_create_and_list(self):
        reg = MissionRegistry()
        goal = MissionGoal(description="test mission")
        spec = reg.create_mission(MissionType.RACE, goal)
        assert spec.mission_id.startswith("mission_")
        assert len(reg.list_missions()) == 1

    def test_start_and_complete(self):
        reg = MissionRegistry()
        spec = reg.create_mission(MissionType.RACE, MissionGoal(description="test"))
        reg.start(spec.mission_id)
        assert reg.get(spec.mission_id).status == MissionStatus.ACTIVE
        reg.complete(spec.mission_id)
        assert reg.get(spec.mission_id).status == MissionStatus.COMPLETE

    def test_abort(self):
        reg = MissionRegistry()
        spec = reg.create_mission(MissionType.EMERGENCY_RESPONSE, MissionGoal(description="emergency"))
        reg.start(spec.mission_id)
        reg.abort(spec.mission_id)
        assert reg.get(spec.mission_id).status == MissionStatus.ABORTED

    def test_cannot_start_twice(self):
        reg = MissionRegistry()
        spec = reg.create_mission(MissionType.RACE, MissionGoal(description="test"))
        reg.start(spec.mission_id)
        with pytest.raises(RuntimeError):
            reg.start(spec.mission_id)

    def test_planner_created_for_search(self):
        reg = MissionRegistry()
        area = ((0, 0, 0), (10, 10, 0))
        goal = MissionGoal(description="search", search_area=area)
        spec = reg.create_mission(MissionType.SEARCH_RESCUE, goal)
        planner = reg.get_planner(spec.mission_id)
        assert isinstance(planner, SearchPatternPlanner)

    def test_planner_created_for_delivery(self):
        reg = MissionRegistry()
        goal = MissionGoal(
            description="deliver",
            waypoints=(Waypoint(10, 10, 5),),
            delivery_target=(10, 10, 0),
        )
        spec = reg.create_mission(MissionType.PACKAGE_DELIVERY, goal)
        planner = reg.get_planner(spec.mission_id)
        assert isinstance(planner, WaypointFollower)

    def test_nonexistent_mission(self):
        reg = MissionRegistry()
        assert reg.get("nonexistent") is None
        with pytest.raises(ValueError):
            reg.start("nonexistent")

    def test_mission_types_cover_all(self):
        assert set(MissionType) == {
            MissionType.RACE,
            MissionType.SEARCH_RESCUE,
            MissionType.PACKAGE_DELIVERY,
            MissionType.EMERGENCY_RESPONSE,
        }
