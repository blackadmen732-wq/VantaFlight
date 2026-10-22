"""Mission planning, patterns, and end-to-end execution on the simulator."""
from __future__ import annotations

import math

import pytest

from vantaflight.core import FlightController
from vantaflight.data import FlightDatabase
from vantaflight.mission import (
    FinishAction,
    MissionPlan,
    MissionState,
    Waypoint,
    build_pattern,
    check_plan,
    estimate_duration_s,
)
from vantaflight.models import FlightMode
from vantaflight.safety import Geofence

from .conftest import fly, fly_until


def _plan(*points, finish=FinishAction.LAND, **kw) -> MissionPlan:
    return MissionPlan(
        name="test",
        waypoints=[Waypoint(x=x, y=y, altitude=a, **kw) for x, y, a in points],
        finish=finish,
    )


# -- planner -----------------------------------------------------------------
def test_valid_plan_reports_distance_and_cost():
    report = check_plan(_plan((10, 0, 5), (10, 10, 5)))
    assert report.valid, report.errors
    assert report.distance_m == pytest.approx(math.dist((0, 0, 0), (10, 0, 5)) + 10)
    assert report.estimated_duration_s > 0
    assert report.estimated_battery_pct > 0


@pytest.mark.parametrize(
    "plan, needle",
    [
        (MissionPlan(waypoints=[]), "at least one waypoint"),
        (_plan((500, 0, 5)), "geofence radius"),
        (_plan((0, 0, 500)), "geofence ceiling"),
        (_plan((5, 5, 0.2)), "at least 1 m"),
        (MissionPlan(waypoints=[Waypoint(x=1, y=1, altitude=5)], speed_m_s=99), "speed"),
    ],
)
def test_invalid_plans_are_explained(plan, needle):
    report = check_plan(plan)
    assert not report.valid
    assert any(needle in e for e in report.errors), report.errors


def test_battery_reserve_is_enforced():
    long_plan = _plan(*[(x, 0, 10) for x in range(0, 140, 10)], speed_m_s=1.0)
    assert check_plan(long_plan).valid  # fine without knowing the battery
    report = check_plan(long_plan, battery_pct=40)
    assert not report.valid
    assert "not enough battery" in report.errors[0]


def test_custom_geofence_is_respected():
    assert not check_plan(_plan((30, 0, 5)), geofence=Geofence(radius_m=20)).valid


def test_hold_and_finish_change_duration_estimate():
    base = _plan((10, 0, 5), finish=FinishAction.HOLD)
    assert estimate_duration_s(_plan((10, 0, 5), finish=FinishAction.HOLD, hold_s=30)) == \
        pytest.approx(estimate_duration_s(base) + 30)
    assert estimate_duration_s(_plan((10, 0, 5), finish=FinishAction.RETURN_HOME)) > \
        estimate_duration_s(_plan((10, 0, 5), finish=FinishAction.LAND))


# -- patterns ----------------------------------------------------------------
def test_square_pattern():
    plan = build_pattern("square", {"size_m": 20, "altitude_m": 8})
    assert len(plan.waypoints) == 4
    assert {(w.x, w.y) for w in plan.waypoints} == {(-10, -10), (10, -10), (10, 10), (-10, 10)}
    assert all(w.altitude == 8 for w in plan.waypoints)


def test_orbit_pattern_closes_the_loop():
    plan = build_pattern("orbit", {"radius_m": 10, "points": 8})
    assert len(plan.waypoints) == 9
    assert (plan.waypoints[0].x, plan.waypoints[0].y) == (plan.waypoints[-1].x, plan.waypoints[-1].y)
    assert all(math.hypot(w.x, w.y) == pytest.approx(10, abs=0.01) for w in plan.waypoints)


def test_survey_pattern_sweeps_back_and_forth():
    plan = build_pattern("survey", {"width_m": 40, "height_m": 20, "spacing_m": 10})
    xs = [w.x for w in plan.waypoints]
    assert xs == [-20, 20, 20, -20, -20, 20]  # three lanes, alternating direction
    assert check_plan(plan).valid


def test_pattern_errors():
    with pytest.raises(ValueError, match="unknown pattern"):
        build_pattern("spiral")
    with pytest.raises(ValueError, match="bad parameters"):
        build_pattern("square", {"sides": 5})


# -- execution ---------------------------------------------------------------
async def _armed(controller: FlightController):
    await controller.connect()
    assert (await controller.command("arm")).accepted


async def test_mission_from_ground_takes_off_visits_waypoints_and_lands(
    controller: FlightController, clock, db: FlightDatabase
):
    await _armed(controller)
    fid = controller.flight_id
    plan = _plan((10, 0, 6), (10, 10, 6), (0, 10, 4))
    assert (await controller.start_mission(plan)).accepted
    assert controller.mission.state == MissionState.RUNNING

    t = await fly_until(controller, clock, lambda _: controller.mission.state != MissionState.RUNNING)
    status = controller.mission.status()
    assert status["state"] == "COMPLETED", status
    assert status["waypoints_reached"] == 3
    assert status["progress"] == 1.0
    assert (t.x, t.y) == pytest.approx((0, 10), abs=1.0)

    t = await fly_until(controller, clock, lambda t: not t.armed)
    assert t.altitude == 0.0

    events = [e["event_type"] for e in db.get_events(fid)]
    assert events.count("waypoint_reached") == 3
    assert "mission_started" in events and "mission_completed" in events
    commands = [c["command"] for c in db.get_commands(fid)]
    assert commands.count("goto") == 3


async def test_mission_return_home_finish(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((15, 15, 5), finish=FinishAction.RETURN_HOME))
    await fly_until(controller, clock, lambda _: controller.mission.state == MissionState.COMPLETED)
    t = await fly_until(controller, clock, lambda t: not t.armed)
    assert (t.x, t.y) == pytest.approx((0, 0), abs=0.2)


async def test_mission_loiters_at_waypoint(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((5, 0, 5), finish=FinishAction.HOLD, hold_s=10))
    await fly_until(controller, clock, lambda _: controller.mission.status()["phase"] == "LOITER")
    await fly(controller, clock, 9)
    assert controller.mission.state == MissionState.RUNNING
    await fly(controller, clock, 1.5)
    assert controller.mission.state == MissionState.COMPLETED
    assert controller.get_telemetry().flight_mode == FlightMode.HOLD


async def test_pause_and_resume(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((40, 0, 5)))
    await fly_until(controller, clock, lambda t: t.x > 5)
    assert (await controller.pause_mission()).accepted
    await fly(controller, clock, 1)
    x_paused = controller.get_telemetry().x
    await fly(controller, clock, 5)
    assert controller.get_telemetry().x == pytest.approx(x_paused)
    assert controller.mission.state == MissionState.PAUSED

    assert (await controller.resume_mission()).accepted
    await fly_until(controller, clock, lambda _: controller.mission.state == MissionState.COMPLETED)


async def test_pilot_command_overrides_mission(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((40, 0, 5)))
    await fly_until(controller, clock, lambda t: t.x > 5)
    assert (await controller.command("land")).accepted
    assert controller.mission.state == MissionState.ABORTED
    assert "pilot override" in controller.mission.status()["message"]


async def test_abort_holds_position(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((40, 0, 5)))
    await fly_until(controller, clock, lambda t: t.x > 5)
    assert (await controller.abort_mission()).accepted
    assert controller.get_telemetry().flight_mode == FlightMode.HOLD
    assert not (await controller.abort_mission()).accepted  # nothing left to abort


async def test_mission_aborts_on_connection_loss(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((40, 0, 5)))
    await fly(controller, clock, 3)
    controller._connections.adapter.force_connection_loss()
    await fly(controller, clock, 0.2)
    assert controller.mission.state == MissionState.ABORTED


async def test_start_rejections(controller: FlightController):
    assert not (await controller.start_mission(_plan((5, 0, 5)))).accepted  # not connected
    await controller.connect()
    res = await controller.start_mission(_plan((5, 0, 5)))
    assert not res.accepted and "arm" in res.message
    await controller.command("arm")
    res = await controller.start_mission(_plan((900, 0, 5)))
    assert not res.accepted and "geofence" in res.message
    assert (await controller.start_mission(_plan((5, 0, 5)))).accepted
    res = await controller.start_mission(_plan((5, 0, 5)))
    assert not res.accepted and "already" in res.message


async def test_goto_outside_geofence_rejected(controller: FlightController, clock):
    await _armed(controller)
    await controller.command("takeoff", target_altitude_m=5)
    await fly(controller, clock, 5)
    res = await controller.command("goto", x=1000, y=0, altitude=5)
    assert not res.accepted and "geofence" in res.message


async def test_new_session_clears_finished_mission(controller: FlightController, clock):
    await _armed(controller)
    await controller.start_mission(_plan((5, 0, 5)))
    await controller.disconnect()
    assert controller.mission.state == MissionState.ABORTED
    await controller.connect()
    assert controller.mission.state == MissionState.IDLE
