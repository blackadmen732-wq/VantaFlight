"""Tests for hardened flight/session lifecycle."""
from __future__ import annotations

import pytest

from vantaflight.core import FlightController
from vantaflight.core.flight_controller import SessionState
from vantaflight.data import FlightDatabase


async def test_flight_id_cleared_after_completed(controller: FlightController, db: FlightDatabase):
    await controller.connect()
    fid = controller.flight_id
    assert fid is not None
    await controller.disconnect()
    assert controller.flight_id is None
    assert controller.session_state == SessionState.COMPLETED


async def test_flight_terminates_once(controller: FlightController, db: FlightDatabase):
    await controller.connect()
    fid = controller.flight_id
    await controller.disconnect()
    assert db.get_flight(fid)["status"] == "completed"
    result = await controller.disconnect()
    assert result.accepted is False


async def test_interrupted_flight_not_completed_on_disconnect(
    controller: FlightController, db: FlightDatabase, clock
):
    await controller.connect()
    await controller.command("arm")
    await controller.command("takeoff", target_altitude_m=5.0)
    clock.advance(10)
    fid = controller.flight_id

    controller._connections.adapter.force_connection_loss()
    controller.get_telemetry()
    assert db.get_flight(fid)["status"] == "interrupted"
    assert controller.session_state == SessionState.INTERRUPTED

    result = await controller.disconnect()
    assert result.accepted is True
    assert db.get_flight(fid)["status"] == "interrupted"


async def test_reconnect_after_interrupted_creates_new_flight(
    controller: FlightController, db: FlightDatabase, clock
):
    await controller.connect()
    first_fid = controller.flight_id
    controller._connections.adapter.force_connection_loss()
    controller.get_telemetry()

    result = await controller.connect()
    assert result.accepted is True
    assert controller.flight_id != first_fid
    assert controller.session_state == SessionState.CONNECTED


async def test_reconnect_after_completed_creates_new_flight(
    controller: FlightController, db: FlightDatabase
):
    await controller.connect()
    first_fid = controller.flight_id
    await controller.disconnect()

    result = await controller.connect()
    assert result.accepted is True
    assert controller.flight_id != first_fid


async def test_commands_rejected_after_terminated_session(
    controller: FlightController, db: FlightDatabase
):
    await controller.connect()
    await controller.disconnect()

    for cmd in ("arm", "disarm", "takeoff", "hold", "land"):
        result = await controller.command(cmd)
        assert result.accepted is False
        assert "ended" in result.message


async def test_telemetry_not_written_after_termination(
    controller: FlightController, db: FlightDatabase, clock
):
    await controller.connect()
    fid = controller.flight_id
    for _ in range(3):
        clock.advance(0.1)
        controller.sample()
    count_before = db.count_telemetry(fid)

    await controller.disconnect()
    for _ in range(5):
        clock.advance(0.1)
        controller.sample()
    count_after = db.count_telemetry(fid)
    assert count_after == count_before


async def test_connection_loss_not_duplicated(
    controller: FlightController, db: FlightDatabase, clock
):
    await controller.connect()
    await controller.command("arm")
    await controller.command("takeoff", target_altitude_m=5.0)
    clock.advance(10)
    fid = controller.flight_id

    controller._connections.adapter.force_connection_loss()
    controller.get_telemetry()
    controller.get_telemetry()
    controller.get_telemetry()

    events = db.get_events(fid)
    loss_events = [e for e in events if e["event_type"] == "connection_lost"]
    assert len(loss_events) == 1


async def test_duplicate_disconnect_is_safe(controller: FlightController):
    await controller.connect()
    r1 = await controller.disconnect()
    assert r1.accepted is True
    r2 = await controller.disconnect()
    assert r2.accepted is False


async def test_session_state_transitions(controller: FlightController, clock):
    assert controller.session_state == SessionState.NO_SESSION

    await controller.connect()
    assert controller.session_state == SessionState.CONNECTED

    await controller.command("arm")
    assert controller.session_state == SessionState.ACTIVE

    await controller.command("takeoff", target_altitude_m=5.0)
    clock.advance(10)

    await controller.command("land")
    clock.advance(10)

    await controller.disconnect()
    assert controller.session_state == SessionState.COMPLETED


async def test_multiple_full_sessions(controller: FlightController, db: FlightDatabase, clock):
    flight_ids = []
    for i in range(3):
        await controller.connect()
        fid = controller.flight_id
        flight_ids.append(fid)
        await controller.command("arm")
        await controller.command("takeoff", target_altitude_m=2.0)
        clock.advance(5)
        await controller.command("land")
        clock.advance(5)
        await controller.disconnect()

    assert len(set(flight_ids)) == 3
    for fid in flight_ids:
        assert db.get_flight(fid)["status"] == "completed"
