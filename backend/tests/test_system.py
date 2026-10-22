"""VantaSystem in the app: every task visible, safety records reliable, clean shutdown.

These run the real app (lifespan included) and its supervised runtime.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vantaflight.core.system import VantaSystem
from vantaflight.main import create_app
from vantaflight.vision import CameraManager, FramePacket


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not reached")


def worker_names(client) -> set[str]:
    return {w["name"] for w in client.get("/api/runtime/status").json()["workers"]}


def test_every_task_appears_in_runtime_status():
    app = create_app(db_path=":memory:")
    with TestClient(app) as client:
        status = client.get("/api/runtime/status").json()
        assert status["lifecycle"] == "ACTIVE" and status["overall"] == "ACTIVE"
        assert {"flight.tick", "ui.stream", "recorder.writer", "health.monitor", "bus.maintenance"} <= worker_names(client)
        assert status["unmanaged_tasks"] == []
        tick = next(w for w in status["workers"] if w["name"] == "flight.tick")
        assert tick["criticality"] == "SAFETY" and tick["period_ms"] == 100.0
        # The Performance page's services table now lists real workers.
        assert status["services"]["flight.tick"]["state"] == "RUNNING"

        client.post("/api/connect")
        assert client.post("/api/vantarace/start", json={"course_id": "sitl_line_3", "speed_m_s": 3.0}).json()["accepted"]
        wait_for(lambda: "race.loop" in worker_names(client))
        with client.websocket_connect("/ws/telemetry") as ws:
            assert ws.receive_json()["type"] == "telemetry"
            names = worker_names(client)
            assert {"race.loop", "ui.ws_client"} <= names
            assert client.get("/api/runtime/status").json()["unmanaged_tasks"] == []
        wait_for(lambda: "ui.ws_client" not in worker_names(client))
        lease = client.get("/api/authority").json()["leases"]
        assert lease and lease[0]["owner"].startswith("vantarace:") and lease[0]["level"] == "APPROVED_MISSION_AUTOMATION"
        assert client.post("/api/vantarace/abort").json()["accepted"]
        wait_for(lambda: "race.loop" not in worker_names(client))
        assert client.get("/api/authority").json()["leases"] == []


def test_shutdown_leaves_zero_registered_workers_and_closes_the_flight():
    app = create_app(db_path=":memory:")
    system = app.state.system
    with TestClient(app) as client:
        client.post("/api/connect")
        assert client.post("/api/vantarace/start", json={"course_id": "sitl_line_3", "speed_m_s": 3.0}).json()["accepted"]
        wait_for(lambda: client.get("/api/vantarace").json()["state"] == "RUNNING", timeout=20)
        tasks = [w.task for w in system.runtime.workers()]
        flight_id = client.get("/api/diagnostics").json()["flight_id"]
    assert system.runtime.workers() == []
    assert system.runtime.state.value == "STOPPED"
    assert all(t.done() for t in tasks)
    assert app.state.race_session.state.value == "ABORTED", "execution stopped before the link closed"
    assert app.state.controller.adapter is None
    assert app.state.recorder.metrics.running is False
    # The race's closing records reached the database before it closed.
    assert flight_id is not None


def test_failsafe_and_its_authority_takeover_are_persisted_reliably():
    app = create_app(db_path=":memory:")
    with TestClient(app) as client:
        client.post("/api/connect")
        assert client.post("/api/vantarace/start", json={"course_id": "sitl_line_3", "speed_m_s": 3.0}).json()["accepted"]
        wait_for(lambda: client.get("/api/vantarace").json()["state"] == "RUNNING", timeout=20)
        assert client.post("/api/sim/fault", json={"kind": "battery", "value": 20}).json()["accepted"]
        wait_for(lambda: client.get("/api/vantarace").json()["state"] == "ABORTED")
        flight_id = client.get("/api/diagnostics").json()["flight_id"]
        records = client.get(f"/api/vantarace/recording/{flight_id}").json()
        assert records["decode_errors"] == 0
        topics = [r["topic"] for r in records["samples"]]
        assert "safety.failsafe" in topics
        failsafe = next(r for r in records["samples"] if r["topic"] == "safety.failsafe")
        assert failsafe["data"] == {"reason": "battery_low", "action": "return", "message": failsafe["data"]["message"]}
        decisions = [r["data"] for r in records["samples"] if r["topic"] == "authority.decision"]
        assert any(d["action"] == "grant" and d["owner"].startswith("vantarace:") for d in decisions)
        assert any(d["action"] == "revoke" and "failsafe" in d["reason"] for d in decisions)
        assert any(d["action"] == "preempt" and d["level"] == "FAILSAFE" for d in decisions)
        bus = {c["topic"]: c for c in client.get("/api/bus").json()["channels"]}
        for topic in ("flight.event", "execution.command", "safety.failsafe", "authority.decision"):
            assert bus[topic]["mode"] == "RELIABLE_EVENT"
            assert bus[topic]["pending"] == 0 and bus[topic]["rejected"] == 0, bus[topic]
        assert bus["safety.failsafe"]["published"] >= 1
        db = app.state.db
        events = [e["event_type"] for e in db.get_events(flight_id)]
        assert "failsafe" in events and "race_aborted" in events


def test_a_slow_ui_cannot_delay_the_flight_tick(monkeypatch):
    app = create_app(db_path=":memory:")
    hub = app.state.hub

    async def stuck_browser(message):
        await asyncio.sleep(1.0)  # every broadcast takes a second

    monkeypatch.setattr(hub, "broadcast", stuck_browser)
    with TestClient(app) as client:
        def iterations():
            workers = {w["name"]: w for w in client.get("/api/runtime/status").json()["workers"]}
            return workers["flight.tick"]["iterations"], workers["ui.stream"]["iterations"]

        tick0, ui0 = iterations()
        time.sleep(2.0)
        tick1, ui1 = iterations()
        assert tick1 - tick0 >= 15, "the 10 Hz flight tick kept its rate"
        assert ui1 - ui0 <= 1, "while the UI stream was stuck on the browser"
        tick = next(w for w in client.get("/api/runtime/status").json()["workers"] if w["name"] == "flight.tick")
        assert tick["deadline_misses"] == 0


def test_health_endpoint_reports_bus_runtime_vehicle_and_recorder():
    app = create_app(db_path=":memory:")
    with TestClient(app) as client:
        client.post("/api/connect")
        wait_for(lambda: client.get("/api/bus").json()["channels"] and
                 next(c for c in client.get("/api/bus").json()["channels"] if c["topic"] == "flight.telemetry")["published"] > 3)
        health = client.get("/api/health/components").json()
        components = {c["component"]: c for c in health["components"]}
        assert components["vehicle.link"]["dimensions"]["LIVENESS"]["state"] == "HEALTHY"
        assert components["vehicle.link"]["dimensions"]["FRESHNESS"]["state"] == "HEALTHY"
        assert components["bus:flight.telemetry"]["dimensions"]["FRESHNESS"]["state"] == "HEALTHY"
        assert components["runtime:flight.tick"]["state"] == "HEALTHY"
        assert components["recorder"]["state"] == "HEALTHY"
        assert components["runtime"]["dimensions"]["CORRECTNESS"]["state"] == "HEALTHY"
        assert client.get("/api/health").json()["status"] == "ok"  # the legacy check is unchanged


class FastCamera:
    """A camera that produces frames much faster than vision consumes them."""

    source_id = "front-cam"

    def __init__(self) -> None:
        self.sequence = 0

    async def start(self) -> None:
        pass

    async def read(self):
        await asyncio.sleep(0.001)
        self.sequence += 1
        return FramePacket(np.zeros((8, 8, 3), np.uint8), capture_timestamp=time.monotonic(),
                           sequence=self.sequence, source_id=self.source_id)

    async def stop(self) -> None:
        pass


async def test_camera_frames_ride_a_bounded_latest_channel_with_visible_metrics():
    system = VantaSystem.create()
    await system.start()
    system.activate()
    manager = CameraManager(FastCamera(), runtime=system.runtime, bus=system.bus)
    await manager.start()
    depths = []

    async def slow_vision(frame):
        depths.append(manager.frame_buffer.metrics.depth)
        await asyncio.sleep(0.02)  # 50 Hz vision against a ~1 kHz camera
        return frame

    await manager.start_processing(slow_vision)
    await asyncio.sleep(0.5)
    names = {w.name for w in system.runtime.workers()}
    metrics = system.bus.channel("camera.frames.front_cam").metrics()
    health = system.health.component("bus:camera.frames.front_cam")
    await manager.stop()
    after = {w.name for w in system.runtime.workers()}
    report = await system.stop()

    assert max(depths) <= 2, "the camera queue never grows beyond 2 frames"
    assert {"vision.capture:front_cam", "vision.process"} <= names
    assert metrics["published"] > 100
    assert metrics["dropped"]["superseded"] + metrics["dropped"]["stale"] > 50
    assert metrics["depth"] <= 2 and metrics["age_ms"]["p95"] is not None
    assert health is not None and health.state.value == "HEALTHY", "stale frames dropped by design are not a fault"
    assert not ({"vision.capture:front_cam", "vision.process"} & after)
    assert report["remaining"] == [] and system.runtime.workers() == []
