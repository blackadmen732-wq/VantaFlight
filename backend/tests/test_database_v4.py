"""Tests for Database V4 — mission persistence."""
from __future__ import annotations

from pathlib import Path

import pytest

from vantaflight.data import FlightDatabase


@pytest.fixture
def db():
    database = FlightDatabase(":memory:")
    yield database
    database.close()


class TestMigrationV4:

    def test_schema_version_is_4(self, db: FlightDatabase):
        assert db.schema_version == 4

    def test_missions_table_exists(self, db: FlightDatabase):
        row = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missions'"
        ).fetchone()
        assert row is not None

    def test_mission_waypoints_table_exists(self, db: FlightDatabase):
        row = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mission_waypoints'"
        ).fetchone()
        assert row is not None

    def test_mission_events_table_exists(self, db: FlightDatabase):
        row = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mission_events'"
        ).fetchone()
        assert row is not None

    def test_idempotent_migration(self, tmp_path: Path):
        path = tmp_path / "migrate.db"
        db1 = FlightDatabase(path)
        assert db1.schema_version == 4
        db1.close()
        db2 = FlightDatabase(path)
        assert db2.schema_version == 4
        db2.close()


class TestMissionCRUD:

    def test_save_and_get_mission(self, db: FlightDatabase):
        db.save_mission(
            "m1",
            "RACE",
            {"description": "test race"},
            status="PENDING",
        )
        m = db.get_mission("m1")
        assert m is not None
        assert m["mission_id"] == "m1"
        assert m["mission_type"] == "RACE"
        assert m["status"] == "PENDING"
        assert m["goal"]["description"] == "test race"

    def test_get_nonexistent_returns_none(self, db: FlightDatabase):
        assert db.get_mission("nope") is None

    def test_save_with_waypoints(self, db: FlightDatabase):
        wps = [
            {"x": 1.0, "y": 2.0, "z": 3.0, "speed_m_s": 4.0, "label": "start"},
            {"x": 10.0, "y": 20.0, "z": 5.0},
        ]
        db.save_mission("m2", "PACKAGE_DELIVERY", {"description": "deliver"}, waypoints=wps)
        stored = db.get_mission_waypoints("m2")
        assert len(stored) == 2
        assert stored[0]["x"] == 1.0
        assert stored[0]["label"] == "start"
        assert stored[1]["speed_m_s"] == 5.0

    def test_upsert_replaces_waypoints(self, db: FlightDatabase):
        wps1 = [{"x": 1.0, "y": 0.0, "z": 0.0}]
        wps2 = [{"x": 10.0, "y": 0.0, "z": 0.0}, {"x": 20.0, "y": 0.0, "z": 0.0}]
        db.save_mission("m3", "RACE", {"description": "r"}, waypoints=wps1)
        assert len(db.get_mission_waypoints("m3")) == 1
        db.save_mission("m3", "RACE", {"description": "r"}, waypoints=wps2)
        assert len(db.get_mission_waypoints("m3")) == 2

    def test_update_status_pending_to_active(self, db: FlightDatabase):
        db.save_mission("m4", "SEARCH_RESCUE", {"description": "search"})
        db.update_mission_status("m4", "ACTIVE")
        m = db.get_mission("m4")
        assert m["status"] == "ACTIVE"
        assert m["started_at"] is not None

    def test_update_status_to_complete(self, db: FlightDatabase):
        db.save_mission("m5", "RACE", {"description": "r"})
        db.update_mission_status("m5", "ACTIVE")
        db.update_mission_status("m5", "COMPLETE", phase="COMPLETE", elapsed_s=42.5)
        m = db.get_mission("m5")
        assert m["status"] == "COMPLETE"
        assert m["ended_at"] is not None
        assert m["elapsed_s"] == 42.5
        assert m["phase"] == "COMPLETE"

    def test_update_waypoints_reached(self, db: FlightDatabase):
        db.save_mission("m6", "RACE", {"description": "r"})
        db.update_mission_status("m6", "ACTIVE", waypoints_reached=3)
        assert db.get_mission("m6")["waypoints_reached"] == 3

    def test_record_and_get_mission_events(self, db: FlightDatabase):
        db.save_mission("m7", "EMERGENCY_RESPONSE", {"description": "emergency"})
        db.record_mission_event("m7", "started", phase="TAKEOFF")
        db.record_mission_event(
            "m7", "waypoint_reached", phase="EXECUTE", detail={"wp_index": 0}
        )
        db.record_mission_event("m7", "aborted", detail={"reason": "low battery"})
        events = db.get_mission_events("m7")
        assert len(events) == 3
        assert events[0]["event_type"] == "started"
        assert events[1]["detail"]["wp_index"] == 0
        assert events[2]["detail"]["reason"] == "low battery"

    def test_list_missions_all(self, db: FlightDatabase):
        db.save_mission("a", "RACE", {"description": "a"})
        db.save_mission("b", "SEARCH_RESCUE", {"description": "b"})
        db.save_mission("c", "PACKAGE_DELIVERY", {"description": "c"})
        result = db.list_missions_db()
        assert len(result) == 3

    def test_list_missions_by_status(self, db: FlightDatabase):
        db.save_mission("a", "RACE", {"description": "a"}, status="PENDING")
        db.save_mission("b", "RACE", {"description": "b"}, status="ACTIVE")
        db.save_mission("c", "RACE", {"description": "c"}, status="PENDING")
        result = db.list_missions_db(status="PENDING")
        assert len(result) == 2
        assert all(m["status"] == "PENDING" for m in result)

    def test_list_missions_limit(self, db: FlightDatabase):
        for i in range(10):
            db.save_mission(f"m{i}", "RACE", {"description": f"m{i}"})
        result = db.list_missions_db(limit=3)
        assert len(result) == 3

    def test_mission_events_empty(self, db: FlightDatabase):
        db.save_mission("m8", "RACE", {"description": "r"})
        assert db.get_mission_events("m8") == []

    def test_mission_waypoints_empty(self, db: FlightDatabase):
        db.save_mission("m9", "RACE", {"description": "r"})
        assert db.get_mission_waypoints("m9") == []

    def test_metadata_persisted(self, db: FlightDatabase):
        db.save_mission(
            "m10", "RACE", {"description": "r"},
            metadata={"priority": "high", "tags": ["urgent"]},
        )
        m = db.get_mission("m10")
        assert m["metadata"]["priority"] == "high"
        assert m["metadata"]["tags"] == ["urgent"]

    def test_abort_sets_ended_at(self, db: FlightDatabase):
        db.save_mission("m11", "RACE", {"description": "r"})
        db.update_mission_status("m11", "ACTIVE")
        db.update_mission_status("m11", "ABORTED")
        m = db.get_mission("m11")
        assert m["status"] == "ABORTED"
        assert m["ended_at"] is not None
