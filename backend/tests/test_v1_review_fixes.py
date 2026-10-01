"""Regressions for the five review findings on the V1 integration PR."""
from __future__ import annotations

import sqlite3
import sys

import pytest
from starlette.testclient import TestClient

from vantaflight.adapters.base import DroneAdapter
from vantaflight.connection import ConnectionManager
from vantaflight.core import FlightController
from vantaflight.core.flight_controller import SessionState
from vantaflight.data import FlightDatabase
from vantaflight.main import create_app
from vantaflight.mission import GoalWaypoint, MissionGoal, MissionRegistry, MissionType
from vantaflight.models import CommandResult, CommandStatus
from vantaflight.safety import FailsafeGuardian
from vantaflight.models import Telemetry

from .test_hopper_adapter import _make_camera_only_adapter


# -- 1. camera-only Hopper sessions stay connected ------------------------------
async def test_camera_only_hopper_telemetry_reports_connected():
    adapter = _make_camera_only_adapter()
    await adapter.connect()
    assert adapter.connected

    t = adapter.get_telemetry()
    assert t.connected is True
    # ...but nothing is measured, so nothing may look real.
    assert not t.battery_available
    assert not t.altitude_available
    assert not t.position_available
    assert not t.airborne


async def test_camera_only_hopper_session_is_not_terminated(db: FlightDatabase):
    adapter = _make_camera_only_adapter()
    manager = ConnectionManager(adapter_factory=lambda d: adapter)
    controller = FlightController(db, connection_manager=manager)

    drones = await manager.discover()
    hopper = next(d for d in drones if d.adapter_type.value == "hopper")
    result = await controller.connect(hopper)
    assert result.accepted, result.message

    for _ in range(5):
        await controller.tick()
    assert controller.session_state == SessionState.CONNECTED
    assert controller.connected


def test_failsafes_ignore_unknown_battery_and_position():
    guardian = FailsafeGuardian()
    t = Telemetry(
        connected=True, armed=True, altitude=2.0, z=2.0,
        battery_percentage=0.0, battery_available=False,
        x=9999.0, y=9999.0, position_available=False,
    )
    assert guardian.evaluate(t) is None


# -- 2. training runs persist without an invalid course foreign key -------------
def test_training_runs_are_persisted(tmp_path):
    db_path = tmp_path / "train.db"
    with TestClient(create_app(db_path=str(db_path))) as client:
        cid = client.post("/api/training/campaigns", json={"name": "Persist"}).json()["campaign_id"]
        client.post(f"/api/training/campaigns/{cid}/start")
        run = client.post(f"/api/training/campaigns/{cid}/run-next").json()
        run_id = run["run_id"]

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT course_id, status, metadata_json FROM simulation_runs WHERE id = ?", (run_id,)
    ).fetchone()
    conn.close()
    assert row is not None, "training run was silently dropped"
    course_id, status, metadata = row
    assert course_id is None
    assert status in ("completed", "failed")
    assert "course_mode" in metadata


# -- 3. mission IDs survive restarts -------------------------------------------
def _create_mission(client: TestClient, description: str) -> str:
    resp = client.post(
        "/api/missions",
        json={"mission_type": "RACE", "description": description, "waypoints": [{"x": 1, "y": 2, "z": 3}]},
    )
    assert resp.status_code == 200
    return resp.json()["mission_id"]


def test_missions_are_restored_and_ids_never_reused(tmp_path):
    db_path = str(tmp_path / "missions.db")
    with TestClient(create_app(db_path=db_path)) as client:
        first = _create_mission(client, "first")
        client.post(f"/api/missions/{first}/start")

    with TestClient(create_app(db_path=db_path)) as client:
        listed = {m["mission_id"]: m for m in client.get("/api/missions").json()["missions"]}
        assert first in listed
        assert listed[first]["goal"]["description"] == "first"
        # It was running when the app stopped, so it did not finish.
        assert listed[first]["status"] == "ABORTED"

        second = _create_mission(client, "second")
        assert second != first

    db = FlightDatabase(db_path)
    try:
        assert db.get_mission(first)["goal"]["description"] == "first"
        assert db.get_mission(first)["status"] == "ABORTED"
        assert db.get_mission(second)["goal"]["description"] == "second"
    finally:
        db.close()


def test_registry_restore_skips_existing_ids():
    registry = MissionRegistry()
    goal = MissionGoal(description="g", waypoints=(GoalWaypoint(1, 2, 3),))
    registry.restore([
        {"mission_id": "mission_0007", "mission_type": "RACE", "goal": goal.to_dict(), "status": "COMPLETE"},
    ])
    spec = registry.create_mission(MissionType.RACE, goal)
    assert spec.mission_id == "mission_0008"
    assert registry.get("mission_0007").status.value == "COMPLETE"


# -- 4. commands that fail before transmission are never recorded as SENT -------
class _ExplodingAdapter(DroneAdapter):
    """Accepts the connection, then refuses every command before sending it."""

    adapter_id = "exploding"

    def __init__(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def arm(self):
        raise RuntimeError("refused before transmission")

    async def disarm(self):
        raise RuntimeError("refused")

    async def takeoff(self, target_altitude_m: float = 5.0):
        raise RuntimeError("refused")

    async def hold(self):
        raise RuntimeError("refused")

    async def land(self):
        raise RuntimeError("refused")

    def get_telemetry(self) -> Telemetry:
        return Telemetry(connected=self._connected)

    def get_capabilities(self):
        from vantaflight.adapters import MockDroneAdapter

        return MockDroneAdapter().get_capabilities()


async def test_pre_dispatch_failure_is_not_recorded_as_sent(db: FlightDatabase):
    adapter = _ExplodingAdapter()
    manager = ConnectionManager(adapter_factory=lambda d: adapter)
    controller = FlightController(db, connection_manager=manager)
    target = (await manager.discover())[0]
    assert (await controller.connect(target)).accepted

    result = await controller.command("arm")
    assert not result.accepted
    assert result.status == CommandStatus.INTERNAL_ERROR
    assert not result.status.transmitted


def test_refused_command_result_defaults_to_rejected_not_sent():
    assert CommandResult(command="x", accepted=False, message="no").status == CommandStatus.REJECTED
    assert CommandResult(command="x", accepted=True).status == CommandStatus.SENT
    explicit = CommandResult(command="x", accepted=False, status=CommandStatus.TIMED_OUT)
    assert explicit.status == CommandStatus.TIMED_OUT


# -- 5. the declared minimum Python (3.10) passes the environment check ---------
def test_environment_accepts_python_310(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 0, "final", 0))
    with TestClient(create_app(db_path=str(tmp_path / "env.db"))) as client:
        assert client.get("/api/environment").json()["checks"]["python"]["ok"] is True


# -- request bodies defined inside create_app() were never parsed ---------------
def test_request_bodies_are_parsed(tmp_path):
    with TestClient(create_app(db_path=str(tmp_path / "bodies.db"))) as client:
        assert client.post("/api/missions", json={"mission_type": "RACE"}).status_code == 200
        assert client.post("/api/replay/seek", json={"time_offset": 1.5}).status_code == 200
        assert client.post("/api/replay/speed", json={"speed": 2.0}).status_code == 200
