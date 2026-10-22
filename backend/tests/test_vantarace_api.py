"""HTTP surface of the VantaRace closed loop (mock simulator, real clock)."""
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


def wait(client: TestClient, predicate, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        status = client.get("/api/vantarace").json()
        if predicate(status):
            return status
        time.sleep(0.1)
    raise AssertionError(f"timed out; last status: {status.get('state')} {status.get('message')}")


def test_courses_and_check(client: TestClient):
    courses = client.get("/api/vantarace/courses").json()["courses"]
    ids = {c["course_id"] for c in courses}
    assert {"sitl_line_3", "sitl_slalom_4", "sitl_arc_5"} <= ids
    assert all(c["gates"] for c in courses)
    assert client.post("/api/vantarace/check", json={"course_id": "sitl_line_3"}).json()["valid"]
    bad = client.post(
        "/api/vantarace/check",
        json={"course": {"gates": [{"x": 12, "y": 0, "z": 5}, {"x": 12, "y": 12, "z": 5}]}},
    ).json()
    assert not bad["valid"] and bad["errors"]
    assert client.post("/api/vantarace/check", json={"course_id": "nope"}).status_code == 422


def test_idle_status_and_refusals(client: TestClient):
    status = client.get("/api/vantarace").json()
    assert status["state"] == "IDLE"
    assert client.get("/api/autonomy/status").json()["state"] == "IDLE"
    res = client.post("/api/vantarace/start", json={"course_id": "sitl_line_3"}).json()
    assert not res["accepted"]  # not connected
    assert not client.post("/api/vantarace/pause").json()["accepted"]
    assert not client.post("/api/vantarace/abort").json()["accepted"]
    assert not client.post("/api/vantarace/fault", json={"kind": "vision_blackout"}).json()["accepted"]
    bad = client.post("/api/vantarace/start", json={"course_id": "sitl_line_3", "speed_m_s": 99}).json()
    assert not bad["accepted"] and "speed" in bad["message"]


def test_start_run_pause_resume_abort_over_http(client: TestClient):
    assert client.post("/api/connect").json()["accepted"]
    res = client.post(
        "/api/vantarace/start",
        json={"course_id": "sitl_line_3", "perception": "vision", "speed_m_s": 3.0},
    ).json()
    assert res["accepted"], res
    status = wait(client, lambda s: s["state"] == "RUNNING", timeout=15)
    assert status["total_gates"] == 3
    assert client.get("/api/telemetry").json()["flight_mode"] == "OFFBOARD"
    wait(client, lambda s: (s["stats"]["setpoints_sent"] or 0) > 10, timeout=5)

    fault = client.post("/api/vantarace/fault", json={"kind": "low_confidence", "duration_s": 1.0, "value": 0.05}).json()
    assert fault["accepted"]
    assert not client.post("/api/vantarace/fault", json={"kind": "bogus"}).json()["accepted"]

    assert client.post("/api/vantarace/pause").json()["accepted"]
    assert client.get("/api/vantarace").json()["state"] == "PAUSED"
    assert client.post("/api/vantarace/resume").json()["accepted"]
    assert client.post("/api/vantarace/abort").json()["accepted"]
    status = client.get("/api/vantarace").json()
    assert status["state"] == "ABORTED"
    assert client.get("/api/telemetry").json()["flight_mode"] == "HOLD"

    flight_id = client.get("/api/diagnostics").json()["flight_id"]
    recording = client.get(f"/api/vantarace/recording/{flight_id}").json()
    kinds = {s["kind"] for s in recording["samples"]}
    assert {"race_sample", "race_event", "race_summary"} <= kinds
