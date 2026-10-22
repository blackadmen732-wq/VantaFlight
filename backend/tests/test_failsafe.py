"""Failsafe guardian rules and their end-to-end effect on the simulator."""
from __future__ import annotations

import pytest

from vantaflight.mission import MissionPlan, MissionState, Waypoint
from vantaflight.models import FlightMode, Telemetry
from vantaflight.safety import FailsafeAction, FailsafeConfig, FailsafeGuardian, Geofence

from .conftest import fly, fly_until


def _flying(**kw) -> Telemetry:
    base = dict(connected=True, armed=True, altitude=10.0, battery_percentage=80.0,
                flight_mode=FlightMode.HOLD)
    base.update(kw)
    return Telemetry(**base)


@pytest.fixture
def guardian() -> FailsafeGuardian:
    return FailsafeGuardian(FailsafeConfig(
        battery_low_pct=25, battery_critical_pct=12, link_stale_s=3,
        geofence=Geofence(radius_m=100, max_altitude_m=50),
    ))


def test_healthy_flight_triggers_nothing(guardian):
    assert guardian.evaluate(_flying()) is None


def test_on_the_ground_triggers_nothing(guardian):
    assert guardian.evaluate(_flying(altitude=0.0, battery_percentage=5)) is None


@pytest.mark.parametrize(
    "telemetry, reason, action",
    [
        (_flying(battery_percentage=10), "battery_critical", FailsafeAction.LAND),
        (_flying(battery_percentage=20), "battery_low", FailsafeAction.RETURN),
        (_flying(x=150.0), "geofence", FailsafeAction.RETURN),
        (_flying(altitude=60.0), "geofence", FailsafeAction.RETURN),
        (_flying(link_age_s=5.0), "link_degraded", FailsafeAction.HOLD),
    ],
)
def test_rules(guardian, telemetry, reason, action):
    trigger = guardian.evaluate(telemetry)
    assert trigger is not None
    assert (trigger.reason, trigger.action) == (reason, action)


def test_critical_battery_beats_everything(guardian):
    trigger = guardian.evaluate(_flying(battery_percentage=5, x=500.0, link_age_s=10))
    assert trigger.reason == "battery_critical"


def test_rules_are_latched(guardian):
    assert guardian.evaluate(_flying(battery_percentage=20)) is not None
    assert guardian.evaluate(_flying(battery_percentage=19)) is None
    # ...but a worse condition still escalates.
    assert guardian.evaluate(_flying(battery_percentage=10)).action == FailsafeAction.LAND
    guardian.reset()
    assert guardian.evaluate(_flying(battery_percentage=20)) is not None


def test_low_battery_ignored_while_already_returning(guardian):
    assert guardian.evaluate(_flying(battery_percentage=20, flight_mode=FlightMode.RETURNING)) is None


def test_link_rule_rearms_after_recovery(guardian):
    assert guardian.evaluate(_flying(link_age_s=5)) is not None
    assert guardian.evaluate(_flying(link_age_s=6)) is None
    assert guardian.evaluate(_flying(link_age_s=0)) is None  # recovered
    assert guardian.evaluate(_flying(link_age_s=5)) is not None  # degrades again


def test_status_reports_config_and_last_trigger(guardian):
    guardian.evaluate(_flying(battery_percentage=20))
    status = guardian.status()
    assert status["last_trigger"]["reason"] == "battery_low"
    assert status["fired"] == ["battery_low"]
    assert status["config"]["geofence"]["radius_m"] == 100


# -- end to end ----------------------------------------------------------------
async def _airborne_on_mission(controller, clock):
    await controller.connect()
    await controller.command("arm")
    plan = MissionPlan(name="long", waypoints=[Waypoint(x=60, y=0, altitude=8)])
    assert (await controller.start_mission(plan)).accepted
    await fly_until(controller, clock, lambda t: t.x > 10)


async def test_low_battery_aborts_mission_and_returns_home(controller, clock):
    await _airborne_on_mission(controller, clock)
    controller._connections.adapter.inject_fault("battery", 20)
    t = await fly(controller, clock, 0.2)
    assert controller.mission.state == MissionState.ABORTED
    assert "battery_low" in controller.mission.status()["message"]
    assert t.flight_mode == FlightMode.RETURNING or controller.get_telemetry().flight_mode == FlightMode.RETURNING
    events = [e.event_type for e in controller.drain_events()]
    assert "failsafe" in events
    t = await fly_until(controller, clock, lambda t: not t.armed)
    assert (t.x, t.y) == pytest.approx((0, 0), abs=0.2)


async def test_critical_battery_lands_in_place(controller, clock):
    await _airborne_on_mission(controller, clock)
    controller._connections.adapter.inject_fault("battery", 8)
    await fly(controller, clock, 0.2)
    t = controller.get_telemetry()
    assert t.flight_mode == FlightMode.LANDING
    x_at_trigger = t.x
    t = await fly_until(controller, clock, lambda t: not t.armed)
    assert t.x == pytest.approx(x_at_trigger, abs=0.5)


async def test_link_stall_pauses_aircraft(controller, clock):
    await _airborne_on_mission(controller, clock)
    controller._connections.adapter.inject_fault("link_stall", 6)
    await fly(controller, clock, 4)
    assert controller.mission.state == MissionState.ABORTED
    assert controller.guardian.last_trigger.reason == "link_degraded"
    await fly(controller, clock, 3)  # link back
    t = controller.get_telemetry()
    assert t.flight_mode == FlightMode.HOLD
    assert t.link_age_s == 0.0
