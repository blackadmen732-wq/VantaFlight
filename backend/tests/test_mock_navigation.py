"""Horizontal flight, return-to-launch, and fault injection on the simulator."""
from __future__ import annotations

import math

import pytest

from vantaflight.adapters import MockDroneAdapter
from vantaflight.config import RTL_ALTITUDE_M
from vantaflight.models import ConnectionQuality, FlightMode


@pytest.fixture
async def airborne(clock):
    drone = MockDroneAdapter(time_source=clock)
    await drone.connect()
    await drone.arm()
    await drone.takeoff(5.0)
    clock.advance(10)
    assert drone.get_telemetry().flight_mode == FlightMode.HOLD
    return drone


async def test_goto_flies_to_target_and_settles(airborne, clock):
    await airborne.goto(30.0, 40.0, 8.0, speed_m_s=10.0)
    clock.advance(1)
    mid = airborne.get_telemetry()
    assert mid.flight_mode == FlightMode.MISSION
    assert mid.ground_speed == pytest.approx(10.0)
    assert 0 < math.hypot(mid.x, mid.y) < 50

    clock.advance(10)
    t = airborne.get_telemetry()
    assert (t.x, t.y, t.altitude) == pytest.approx((30.0, 40.0, 8.0))
    assert t.flight_mode == FlightMode.HOLD
    clock.advance(1)
    assert airborne.get_telemetry().ground_speed == 0.0


async def test_heading_follows_direction_of_travel(airborne, clock):
    await airborne.goto(0.0, 20.0, 5.0)  # due north
    clock.advance(0.5)
    assert airborne.get_telemetry().heading == pytest.approx(0.0)
    here = airborne.get_telemetry()
    await airborne.goto(here.x - 50.0, here.y, 5.0)  # due west
    clock.advance(0.5)
    assert airborne.get_telemetry().heading == pytest.approx(270.0)


async def test_speed_is_clamped(airborne, clock):
    await airborne.goto(100.0, 0.0, 5.0, speed_m_s=500.0)
    clock.advance(1)
    assert airborne.get_telemetry().ground_speed == MockDroneAdapter.MAX_SPEED_MPS


async def test_hold_stops_horizontal_motion(airborne, clock):
    await airborne.goto(50.0, 0.0, 5.0)
    clock.advance(2)
    await airborne.hold()
    stopped_at = airborne.get_telemetry().x
    clock.advance(5)
    assert airborne.get_telemetry().x == pytest.approx(stopped_at)


async def test_return_home_flies_back_climbs_and_lands(airborne, clock):
    await airborne.goto(20.0, 0.0, 5.0)
    clock.advance(10)
    await airborne.return_home()
    t = airborne.get_telemetry()
    assert t.flight_mode == FlightMode.RETURNING

    clock.advance(2)
    assert airborne.get_telemetry().altitude > 5.0  # climbing toward RTL altitude
    clock.advance(10)
    t = airborne.get_telemetry()
    assert (t.x, t.y) == pytest.approx((0.0, 0.0))
    assert t.flight_mode == FlightMode.LANDING

    clock.advance(RTL_ALTITUDE_M / MockDroneAdapter.LANDING_RATE_MPS + 2)
    t = airborne.get_telemetry()
    assert t.altitude == 0.0
    assert t.flight_mode == FlightMode.IDLE
    assert t.armed is False


async def test_fault_battery(airborne):
    assert "15%" in airborne.inject_fault("battery", 15)
    assert airborne.get_telemetry().battery_percentage == pytest.approx(15.0, abs=0.1)


async def test_fault_link_stall_freezes_telemetry_then_recovers(airborne, clock):
    await airborne.goto(50.0, 0.0, 5.0)
    clock.advance(1)
    airborne.inject_fault("link_stall", 4)
    frozen_x = airborne.get_telemetry().x
    clock.advance(2)
    t = airborne.get_telemetry()
    assert t.x == frozen_x  # we see stale data...
    assert t.link_age_s == pytest.approx(2.0)
    assert t.connection_quality == ConnectionQuality.POOR

    clock.advance(3)
    t = airborne.get_telemetry()
    assert t.link_age_s == 0.0  # ...until the link comes back
    assert t.x > frozen_x  # and the aircraft kept flying meanwhile
    assert t.connection_quality == ConnectionQuality.EXCELLENT


async def test_fault_rejects_unknown_kind(airborne):
    with pytest.raises(ValueError):
        airborne.inject_fault("gremlins", 1)
    with pytest.raises(ValueError):
        airborne.inject_fault("link_stall", 0)


def test_capabilities_advertise_navigation():
    caps = MockDroneAdapter().get_capabilities()
    assert caps.supports_goto and caps.supports_return
    assert "missions" in caps.supported_capabilities
