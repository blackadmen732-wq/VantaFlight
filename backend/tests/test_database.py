"""Tests for local SQLite persistence."""
from __future__ import annotations

from pathlib import Path

from vantaflight.data import FlightDatabase
from vantaflight.models import CommandResult, FlightEvent, Telemetry


def test_wal_mode_enabled(tmp_path: Path):
    db = FlightDatabase(tmp_path / "wal.db")
    assert db.journal_mode().lower() == "wal"
    db.close()


def test_flight_lifecycle_and_recording(db: FlightDatabase):
    flight_id = db.start_flight("mock-0", "Mock Drone")
    assert db.get_flight(flight_id)["status"] == "active"

    db.record_telemetry(flight_id, Telemetry(connected=True, altitude=1.0))
    db.record_telemetry(flight_id, Telemetry(connected=True, altitude=2.0))
    assert db.count_telemetry(flight_id) == 2

    db.record_event(flight_id, FlightEvent(event_type="connected", message="hi"))
    assert len(db.get_events(flight_id)) == 1

    db.record_command(flight_id, CommandResult(command="arm", accepted=True))
    db.record_command(
        flight_id, CommandResult(command="takeoff", accepted=False, message="nope")
    )
    cmds = db.get_commands(flight_id)
    assert len(cmds) == 2
    assert cmds[0]["accepted"] == 1
    assert cmds[1]["accepted"] == 0

    db.end_flight(flight_id, "completed")
    flight = db.get_flight(flight_id)
    assert flight["status"] == "completed"
    assert flight["ended_at"] is not None


def test_persists_to_disk(tmp_path: Path):
    path = tmp_path / "persist.db"
    db = FlightDatabase(path)
    flight_id = db.start_flight("mock-0", "Mock Drone")
    db.record_telemetry(flight_id, Telemetry(connected=True))
    db.close()

    reopened = FlightDatabase(path)
    assert reopened.count_telemetry(flight_id) == 1
    reopened.close()
