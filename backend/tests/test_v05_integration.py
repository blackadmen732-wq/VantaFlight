from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vantaflight.data import AsyncRecorder, FlightDatabase
from vantaflight.digital_twin import (
    AircraftTruth,
    DigitalTwinTruthStore,
    SimulatorTruthFrame,
    ValidationFrame,
)
from vantaflight.mavlink import MAVLinkConfig, PhysicalMAVLinkBlocked
from vantaflight.main import create_app
from vantaflight.intelligence import IntelligenceRuntime
from vantaflight.vision import LockState


def test_v3_migration_preserves_existing_flights(tmp_path):
    path = tmp_path / "migration.db"
    db = FlightDatabase(path)
    flight_id = db.start_flight("mock-0", "Mock")
    db.close()

    reopened = FlightDatabase(path)
    assert reopened.schema_version == 4
    assert reopened.get_flight(flight_id)["drone_name"] == "Mock"
    assert "camera_profiles" in reopened.get_v05_counts()
    reopened.close()


def test_v3_course_and_camera_persistence(db: FlightDatabase):
    db.upsert_camera_profile(
        "camera-1", {"resolution": [640, 480]}, calibration_version="cal-2"
    )
    db.save_course(
        {
            "id": "course-1",
            "seed": 7,
            "mode": "SLALOM",
            "safe_volume": {"width": 10},
            "difficulty": {"score": 0.4},
            "gates": [{"id": "gate-1", "course_order": 0}],
        }
    )
    counts = db.get_v05_counts()
    assert counts["camera_profiles"] == 1
    assert counts["courses"] == 1
    assert counts["course_gates"] == 1

    db.save_algorithm_configuration(
        "cfg-1", "conservative", {"confidence_decay": 0.9}, is_default=True
    )
    db.record_parameter_experiment(
        "experiment-1",
        {"confidence_decay": 0.85},
        configuration_id="cfg-1",
        score=0.42,
        status="evaluated",
    )
    db.record_file_reference(None, "camera_video", "/runs/run-1/camera.mp4")
    counts = db.get_v05_counts()
    assert counts["algorithm_configurations"] == 1
    assert counts["parameter_experiments"] == 1


@pytest.mark.asyncio
async def test_async_recorder_flushes_and_rejects_after_stop(db: FlightDatabase):
    db.start_simulation_run("run-1")
    recorder = AsyncRecorder(db, capacity=4, batch_size=2, flush_interval_s=0.01)
    await recorder.start()
    assert recorder.record(
        "vision_measurement",
        {
            "run_id": "run-1",
            "frame_id": "frame-1",
            "captured_at": time.time(),
        },
    )
    assert recorder.record(
        "run_metric",
        {
            "run_id": "run-1",
            "metric_name": "dropped_frames",
            "metric_value": 0,
        },
    )
    await recorder.stop()

    assert recorder.metrics.written_records == 2
    assert recorder.record("run_metric", {"metric_name": "late", "metric_value": 1}) is False
    assert db.get_v05_counts()["vision_measurements"] == 1
    assert db.get_v05_counts()["run_metrics"] == 1


@pytest.mark.asyncio
async def test_async_recorder_start_stop_is_idempotent(db: FlightDatabase):
    recorder = AsyncRecorder(db)
    await recorder.start()
    first_task = recorder._task
    await recorder.start()
    assert recorder._task is first_task
    await recorder.stop()
    await recorder.stop()


def test_simulation_guard_rejects_physical_mavlink_links():
    MAVLinkConfig(system_address="udpin://127.0.0.1:14540")
    with pytest.raises(PhysicalMAVLinkBlocked):
        MAVLinkConfig(system_address="serial:///dev/ttyUSB0")
    with pytest.raises(PhysicalMAVLinkBlocked):
        MAVLinkConfig(system_address="udp://192.168.1.44:14540")
    with pytest.raises(PhysicalMAVLinkBlocked):
        MAVLinkConfig(system_address="udp://127.0.0.1:14000")


def test_truth_store_keeps_truth_outside_perception_inputs():
    store = DigitalTwinTruthStore(capacity=2)
    truth = SimulatorTruthFrame(
        timestamp=10.0,
        aircraft=AircraftTruth(
            timestamp=10.0,
            position=(1.0, 2.0, 3.0),
            velocity=(2.0, 0.0, 0.0),
            orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        ),
    )
    store.add_truth(truth)
    store.add_validation(
        ValidationFrame(
            timestamp=10.1,
            simulator_truth=truth,
            sight_estimate={"position": [1.1, 2.0, 3.0]},
            race_request={"trajectory_id": "t-1"},
        )
    )
    assert store.latest_truth is truth
    assert store.validation_frames[0].sight_estimate is not None

    with pytest.raises(ValueError):
        store.add_truth(
            SimulatorTruthFrame(
                timestamp=9.0,
                aircraft=truth.aircraft,
            )
        )


def test_v05_api_contracts_are_typed_and_image_free():
    app = create_app(db_path=":memory:")
    with TestClient(app) as client:
        assert app.state.recorder.metrics.running is True
        assert client.get("/api/vision/status").json()["lock_state"] == "SEARCHING"
        assert client.get("/api/vision/camera-profiles").json() == []
        assert client.get("/api/vision/tracks").json() == []
        assert client.get("/api/scene").status_code == 200
        assert client.get("/api/race").json()["state"] == "IDLE"
        assert client.get("/api/simulation/status").json()["status"] == "idle"
        assert client.get("/api/run-metrics").json() == []
        assert client.get("/api/experiments").json() == []

        with client.websocket_connect("/ws/telemetry") as ws:
            first = ws.receive_json()
            assert first["type"] == "telemetry"
            assert "image" not in first["data"]
    assert app.state.recorder.metrics.running is False


def test_course_api_generates_validates_persists_and_reloads(tmp_path):
    path = tmp_path / "courses.db"
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        response = client.post(
            "/api/courses/generate",
            json={
                "seed": 17,
                "mode": "SPEED_RUN",
                "gate_count": 5,
                "width": 30,
                "length": 60,
                "height": 15,
                "floor": 0,
                "ceiling": 15,
                "boundary_margin": 1,
            },
        )
        assert response.status_code == 200, response.text
        course_id = response.json()["id"]
        validation = client.get(f"/api/courses/{course_id}/validation").json()
        assert validation["valid"] is True

    restarted = create_app(db_path=str(path))
    with TestClient(restarted) as client:
        loaded = client.get(f"/api/courses/{course_id}")
        assert loaded.status_code == 200
        assert loaded.json()["seed"] == 17


def test_vision_result_maps_to_image_free_runtime_contracts():
    runtime = IntelligenceRuntime()
    result = SimpleNamespace(
        frame=SimpleNamespace(camera_source="synthetic"),
        selected=SimpleNamespace(candidate_id="candidate-1", profile_id="gate"),
        lock=SimpleNamespace(state=LockState.PREDICTIVE_LOCK),
        timeline=SimpleNamespace(end_to_end_s=0.012),
        fusion=SimpleNamespace(
            observed_pose=SimpleNamespace(position_world_m=np.array([1.0, 2.0, 3.0])),
            predicted_pose=SimpleNamespace(position_world_m=np.array([1.1, 2.0, 3.0])),
            velocity=np.array([1.0, 0.0, 0.0]),
            confidence=0.82,
            uncertainty=np.eye(3) * 0.2,
            age_s=0.03,
            timestamp=5.0,
            contributions={"classical": 1.2, "pose": 0.8},
        ),
    )
    metrics = SimpleNamespace(
        frames_captured=10,
        frames_processed=7,
        frames_dropped=3,
        depth=0,
        oldest_frame_age_s=None,
        current_frame_age_s=None,
    )
    runtime.publish_vision_result(result, frame_metrics=metrics)

    assert runtime.vision_status.lock_state == "PREDICTIVE_LOCK"
    assert runtime.vision_status.frame_metrics.dropped_frames == 3
    assert runtime.scene.current is not None
    assert runtime.scene.current.predicted_position.x == pytest.approx(1.1)


def test_race_result_maps_to_typed_normalized_trajectory_contract():
    runtime = IntelligenceRuntime()
    planner = SimpleNamespace(
        state=SimpleNamespace(value="ALIGN"),
        aggression_scale=0.65,
    )
    desired = SimpleNamespace(
        desired_position=np.array([1.0, 2.0, 3.0]),
        desired_velocity=np.array([4.0, 0.0, 0.0]),
        desired_acceleration=np.array([0.5, 0.0, 0.0]),
        desired_yaw=0.2,
        timestamp=8.0,
        trajectory_id="trajectory-1",
        planner_confidence=0.75,
    )
    runtime.publish_race_result(planner, desired)
    payload = runtime.race.model_dump()
    assert payload["trajectory"]["desired_position"]["x"] == 1.0
    assert payload["trajectory"]["planner_confidence"] == 0.75
    assert not {"pwm", "motor", "actuator"} & set(payload["trajectory"])
