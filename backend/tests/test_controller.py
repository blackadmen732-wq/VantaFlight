"""End-to-end tests of the FlightController flow through safety and SQLite."""
from __future__ import annotations

from vantaflight.core import FlightController
from vantaflight.data import FlightDatabase
from vantaflight.models import FlightMode


async def _fly_to_hold(controller: FlightController, clock) -> None:
    await controller.connect()
    await controller.command("arm")
    await controller.command("takeoff", target_altitude_m=5.0)
    clock.advance(10)


async def test_connect_opens_flight(controller: FlightController):
    result = await controller.connect()
    assert result.accepted is True
    assert controller.connected is True
    assert controller.flight_id is not None


async def test_disconnect_closes_flight(controller: FlightController, db: FlightDatabase):
    await controller.connect()
    fid = controller.flight_id
    result = await controller.disconnect()
    assert result.accepted is True
    assert controller.connected is False
    assert db.get_flight(fid)["status"] == "completed"


async def test_full_flight_flow(controller: FlightController, clock):
    await controller.connect()
    assert (await controller.command("arm")).accepted

    assert (await controller.command("takeoff", target_altitude_m=5.0)).accepted
    clock.advance(2)
    rising = controller.get_telemetry()
    assert rising.altitude > 0
    assert rising.flight_mode == FlightMode.TAKEOFF

    clock.advance(10)
    at_top = controller.get_telemetry()
    assert at_top.altitude == 5.0

    assert (await controller.command("hold")).accepted
    clock.advance(3)
    assert controller.get_telemetry().altitude == 5.0

    assert (await controller.command("land")).accepted
    clock.advance(10)
    landed = controller.get_telemetry()
    assert landed.altitude == 0.0
    assert landed.armed is False


async def test_takeoff_rejected_before_arm(controller: FlightController):
    await controller.connect()
    result = await controller.command("takeoff")
    assert result.accepted is False
    assert "arming" in result.message


async def test_takeoff_rejected_while_disconnected(controller: FlightController):
    result = await controller.command("takeoff")
    assert result.accepted is False


async def test_disarm_rejected_while_airborne(controller: FlightController, clock):
    await _fly_to_hold(controller, clock)
    result = await controller.command("disarm")
    assert result.accepted is False
    assert "airborne" in result.message


async def test_telemetry_when_disconnected(controller: FlightController):
    t = controller.get_telemetry()
    assert t.connected is False


async def test_sqlite_records_samples_events_commands(
    controller: FlightController, db: FlightDatabase, clock
):
    await _fly_to_hold(controller, clock)
    fid = controller.flight_id

    # Sampling persists telemetry rows.
    for _ in range(5):
        clock.advance(0.1)
        controller.sample()
    assert db.count_telemetry(fid) >= 5

    # Commands (accepted + rejected) are recorded.
    await controller.command("disarm")  # rejected: airborne
    commands = db.get_commands(fid)
    names = [c["command"] for c in commands]
    assert "arm" in names and "takeoff" in names and "disarm" in names
    assert any(c["accepted"] == 0 for c in commands)

    # Events are recorded (connect, arm, takeoff at least).
    events = db.get_events(fid)
    assert len(events) >= 3


async def test_simulated_connection_loss(controller: FlightController, db: FlightDatabase, clock):
    await _fly_to_hold(controller, clock)
    fid = controller.flight_id

    # Drop the link abruptly.
    controller._connections.adapter.force_connection_loss()  # noqa: SLF001
    t = controller.get_telemetry()
    assert t.connected is False

    # The flight is marked interrupted and a connection_lost event is recorded.
    assert db.get_flight(fid)["status"] == "interrupted"
    event_types = [e["event_type"] for e in db.get_events(fid)]
    assert "connection_lost" in event_types


async def test_reconnect_after_loss(controller: FlightController, clock):
    await controller.connect()
    first_flight = controller.flight_id
    controller._connections.adapter.force_connection_loss()  # noqa: SLF001
    controller.get_telemetry()  # observe the loss -> clean state
    assert controller.get_telemetry().connected is False

    # Reconnect opens a brand-new flight and restores a healthy link.
    result = await controller.connect()
    assert result.accepted is True
    assert controller.connected is True
    assert controller.flight_id != first_flight
    assert controller.get_telemetry().connected is True


async def test_no_double_connect(controller: FlightController):
    await controller.connect()
    result = await controller.connect()
    assert result.accepted is False
