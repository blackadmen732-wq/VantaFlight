"""Tests for the FastAPI HTTP + WebSocket surface."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vantaflight.main import create_app


@pytest.fixture
def client():
    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_discover(client: TestClient):
    drones = client.get("/api/discover").json()["drones"]
    assert len(drones) >= 1
    types = {d["adapter_type"] for d in drones}
    assert "mock" in types
    assert drones[0]["transport"] == "SIMULATED"


def test_rest_flight_flow(client: TestClient):
    assert client.post("/api/connect").json()["accepted"] is True
    assert client.post("/api/arm").json()["accepted"] is True
    assert client.post("/api/takeoff", json={"target_altitude_m": 3.0}).json()["accepted"] is True
    # The mock uses a real clock over REST; let it climb off the ground so the
    # airborne-only commands are valid.
    time.sleep(0.5)
    assert client.post("/api/hold").json()["accepted"] is True
    assert client.post("/api/land").json()["accepted"] is True


def test_takeoff_rejected_before_arm(client: TestClient):
    client.post("/api/connect")
    r = client.post("/api/takeoff").json()
    assert r["accepted"] is False


def test_websocket_streams_telemetry(client: TestClient):
    client.post("/api/connect")
    with client.websocket_connect("/ws/telemetry") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "telemetry"
        assert "battery_percentage" in frame["data"]
        assert frame["data"]["connected"] is True
