"""HTTP surface for missions, return-home, failsafes and fault injection."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vantaflight.main import create_app
from vantaflight.config import SOFTWARE_VERSION

PLAN = {"name": "api", "waypoints": [{"x": 2, "y": 0, "altitude": 3}], "finish": "hold"}


@pytest.fixture
def client():
    with TestClient(create_app(db_path=":memory:")) as c:
        yield c


def test_pattern_endpoint(client):
    r = client.post("/api/mission/pattern", json={"kind": "square", "params": {"size_m": 10}}).json()
    assert r["ok"] and len(r["plan"]["waypoints"]) == 4
    bad = client.post("/api/mission/pattern", json={"kind": "nope"}).json()
    assert not bad["ok"] and "unknown pattern" in bad["error"]


def test_validate_endpoint(client):
    ok = client.post("/api/mission/validate", json=PLAN).json()
    assert ok["valid"] and ok["distance_m"] > 0
    far = dict(PLAN, waypoints=[{"x": 5000, "y": 0, "altitude": 3}])
    assert not client.post("/api/mission/validate", json=far).json()["valid"]


def test_validate_rejects_malformed_plan(client):
    assert client.post("/api/mission/validate", json={"waypoints": [{"x": 1}]}).status_code == 422


def test_mission_lifecycle_over_http(client):
    assert client.get("/api/mission").json()["state"] == "IDLE"
    assert not client.post("/api/mission/start", json=PLAN).json()["accepted"]  # not connected
    client.post("/api/connect")
    client.post("/api/arm")
    assert client.post("/api/mission/start", json=PLAN).json()["accepted"]
    assert client.get("/api/mission").json()["state"] == "RUNNING"
    time.sleep(0.6)  # real clock: let it climb off the ground
    assert client.post("/api/mission/pause").json()["accepted"]
    assert client.get("/api/mission").json()["state"] == "PAUSED"
    assert client.post("/api/mission/abort").json()["accepted"]
    assert client.get("/api/mission").json()["state"] == "ABORTED"


def test_mission_frames_on_websocket(client):
    client.post("/api/connect")
    client.post("/api/arm")
    client.post("/api/mission/start", json=PLAN)
    with client.websocket_connect("/ws/telemetry") as ws:
        for _ in range(50):
            frame = ws.receive_json()
            if frame["type"] == "mission":
                assert frame["data"]["state"] == "RUNNING"
                assert frame["data"]["plan"]["name"] == "api"
                break
        else:
            pytest.fail("no mission frame received")


def test_return_home_requires_flight(client):
    client.post("/api/connect")
    r = client.post("/api/return-home").json()
    assert not r["accepted"] and "ground" in r["message"]


def test_failsafe_status(client):
    s = client.get("/api/failsafe").json()
    assert s["config"]["battery_low_pct"] > s["config"]["battery_critical_pct"]
    assert s["last_trigger"] is None


def test_sim_fault(client):
    assert not client.post("/api/sim/fault", json={"kind": "battery", "value": 50}).json()["accepted"]
    client.post("/api/connect")
    r = client.post("/api/sim/fault", json={"kind": "battery", "value": 50}).json()
    assert r["accepted"]
    assert client.get("/api/telemetry").json()["battery_percentage"] == pytest.approx(50, abs=0.5)
    assert not client.post("/api/sim/fault", json={"kind": "bogus", "value": 1}).json()["accepted"]


def test_diagnostics_include_link_and_mission(client):
    d = client.get("/api/diagnostics").json()
    assert d["version"] == SOFTWARE_VERSION
    assert d["mission_state"] == "IDLE"
    assert "link_age_s" in d and "failsafe" in d


def test_websocket_replays_recent_events_on_connect(client):
    client.post("/api/connect")
    client.post("/api/arm")
    with client.websocket_connect("/ws/telemetry") as ws:
        events = []
        for _ in range(20):  # live broadcasts may interleave with the replay
            frame = ws.receive_json()
            if frame["type"] == "event":
                events.append(frame["data"]["event_type"])
            if len(events) == 2:
                break
    assert events == ["connected", "arm"]
