"""API integration tests for V0.9 endpoints."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vantaflight.main import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


class TestHardwareModeAPI:
    def test_get_hardware_mode(self, client):
        resp = client.get("/api/hardware-mode")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "SIMULATION"
        assert data["is_simulation"] is True

    def test_transition_to_observe(self, client):
        resp = client.post("/api/hardware-mode/transition", json={
            "target": "HARDWARE_OBSERVE", "reason": "test"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "HARDWARE_OBSERVE"
        assert data["can_observe"] is True

    def test_invalid_transition(self, client):
        resp = client.post("/api/hardware-mode/transition", json={
            "target": "HARDWARE_COMMAND_LOCKED"
        })
        assert resp.status_code == 400

    def test_invalid_mode_value(self, client):
        resp = client.post("/api/hardware-mode/transition", json={
            "target": "NONEXISTENT"
        })
        assert resp.status_code == 422

    def test_full_transition_cycle(self, client):
        client.post("/api/hardware-mode/transition", json={"target": "HARDWARE_OBSERVE"})
        client.post("/api/hardware-mode/transition", json={"target": "HARDWARE_COMMAND_LOCKED"})
        resp = client.get("/api/hardware-mode")
        assert resp.json()["can_command"] is True

        client.post("/api/hardware-mode/transition", json={"target": "SIMULATION"})
        resp = client.get("/api/hardware-mode")
        assert resp.json()["mode"] == "SIMULATION"


class TestPreflightAPI:
    def test_preflight_disconnected(self, client):
        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_passed"] is False
        assert data["critical_failures"] >= 1
        assert isinstance(data["checks"], list)

    def test_preflight_has_required_checks(self, client):
        resp = client.get("/api/preflight")
        data = resp.json()
        check_names = {c["name"] for c in data["checks"]}
        assert "connection" in check_names
        assert "battery" in check_names
        assert "adapter" in check_names


class TestEnvironmentAPI:
    def test_environment_check(self, client):
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "checks" in data
        assert "python" in data["checks"]
        assert data["checks"]["python"]["ok"] is True
        assert "numpy" in data["checks"]
        assert data["checks"]["numpy"]["ok"] is True


class TestTrainingAPI:
    def test_list_campaigns_empty(self, client):
        resp = client.get("/api/training/campaigns")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_campaign(self, client):
        resp = client.post("/api/training/campaigns", json={
            "name": "Test Campaign",
            "description": "API test",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Campaign"
        assert "campaign_id" in data

    def test_campaign_lifecycle(self, client):
        create = client.post("/api/training/campaigns", json={"name": "Lifecycle"})
        cid = create.json()["campaign_id"]

        start = client.post(f"/api/training/campaigns/{cid}/start")
        assert start.status_code == 200

        pause = client.post(f"/api/training/campaigns/{cid}/pause")
        assert pause.status_code == 200

        cancel = client.post(f"/api/training/campaigns/{cid}/cancel")
        assert cancel.status_code == 200

    def test_fault_profiles(self, client):
        resp = client.get("/api/training/fault-profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "clean" in data


class TestReplayAPI:
    def test_list_flights(self, client):
        resp = client.get("/api/replay/flights")
        assert resp.status_code == 200
        assert "flights" in resp.json()

    def test_replay_status_idle(self, client):
        resp = client.get("/api/replay/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "IDLE"

    def test_replay_controls_without_load(self, client):
        play = client.post("/api/replay/play")
        assert play.status_code == 200

        pause = client.post("/api/replay/pause")
        assert pause.status_code == 200

        stop = client.post("/api/replay/stop")
        assert stop.status_code == 200


class TestExistingEndpointsStillWork:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_telemetry(self, client):
        resp = client.get("/api/telemetry")
        assert resp.status_code == 200

    def test_diagnostics(self, client):
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200

    def test_runtime_status(self, client):
        resp = client.get("/api/runtime/status")
        assert resp.status_code == 200

    def test_runtime_performance(self, client):
        resp = client.get("/api/runtime/performance")
        assert resp.status_code == 200

    def test_runtime_hardware(self, client):
        resp = client.get("/api/runtime/hardware")
        assert resp.status_code == 200

    def test_autonomy_status(self, client):
        resp = client.get("/api/autonomy/status")
        assert resp.status_code == 200
