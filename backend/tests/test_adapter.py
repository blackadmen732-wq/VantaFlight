"""Tests for the MockDroneAdapter physics and lifecycle."""
from __future__ import annotations

import pytest

from vantaflight.adapters.mock import DroneError, MockDroneAdapter
from vantaflight.models import FlightMode


@pytest.fixture
def adapter(clock):
    return MockDroneAdapter(time_source=clock)


async def test_connect_disconnect(adapter):
    assert adapter.connected is False
    await adapter.connect()
    assert adapter.connected is True
    await adapter.disconnect()
    assert adapter.connected is False


async def test_command_while_disconnected_raises(adapter):
    with pytest.raises(DroneError):
        await adapter.arm()


async def test_arm_disarm(adapter):
    await adapter.connect()
    await adapter.arm()
    assert adapter.get_telemetry().armed is True
    await adapter.disarm()
    assert adapter.get_telemetry().armed is False


async def test_takeoff_climbs_to_target(adapter, clock):
    await adapter.connect()
    await adapter.arm()
    await adapter.takeoff(target_altitude_m=5.0)
    clock.advance(10)  # plenty of time to reach 5 m at 1.5 m/s
    t = adapter.get_telemetry()
    assert t.altitude == pytest.approx(5.0, abs=0.01)
    assert t.flight_mode == FlightMode.HOLD  # auto-transitions on arrival


async def test_takeoff_is_gradual(adapter, clock):
    await adapter.connect()
    await adapter.arm()
    await adapter.takeoff(target_altitude_m=5.0)
    clock.advance(1)
    mid = adapter.get_telemetry()
    assert 0 < mid.altitude < 5.0
    assert mid.flight_mode == FlightMode.TAKEOFF


async def test_hold_maintains_altitude(adapter, clock):
    await adapter.connect()
    await adapter.arm()
    await adapter.takeoff(target_altitude_m=5.0)
    clock.advance(10)
    await adapter.hold()
    clock.advance(5)
    t = adapter.get_telemetry()
    assert t.altitude == pytest.approx(5.0, abs=0.01)
    assert t.flight_mode == FlightMode.HOLD


async def test_land_descends_and_disarms(adapter, clock):
    await adapter.connect()
    await adapter.arm()
    await adapter.takeoff(target_altitude_m=5.0)
    clock.advance(10)
    await adapter.land()
    clock.advance(10)
    t = adapter.get_telemetry()
    assert t.altitude == pytest.approx(0.0, abs=0.01)
    assert t.flight_mode == FlightMode.IDLE
    assert t.armed is False  # auto-disarm on the ground


async def test_battery_drains_over_time(adapter, clock):
    await adapter.connect()
    start = adapter.get_telemetry().battery_percentage
    clock.advance(60)
    later = adapter.get_telemetry().battery_percentage
    assert later < start


async def test_force_connection_loss(adapter):
    await adapter.connect()
    adapter.force_connection_loss()
    assert adapter.connected is False
    assert adapter.get_telemetry().connected is False
