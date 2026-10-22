from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import threading
import time

import pytest
from pydantic import ValidationError

from vantaflight.api_models import CourseGenerationRequest
from vantaflight.course_lab import SafeVolume
from vantaflight.data import AsyncRecorder, FlightDatabase, RecorderState
from vantaflight.digital_twin import (
    AircraftTruth,
    DigitalTwinTruthStore,
    SimulatorTruthFrame,
    ValidationFrame,
)
from vantaflight.data import database as database_module


def _metric(name: str, value: float = 1.0) -> dict:
    return {
        "kind": "run_metric",
        "payload": {"metric_name": name, "metric_value": value},
    }


def _course(*, seed: int = 1, gates: list[dict] | None = None) -> dict:
    return {
        "id": "course-1",
        "seed": seed,
        "mode": "SLALOM",
        "safe_volume": {"width": 30},
        "difficulty": {"score": 0.5},
        "gates": gates or [{"id": "gate-1", "course_order": 0}],
    }


def test_heterogeneous_batch_rolls_back_every_record_on_error(
    db: FlightDatabase,
) -> None:
    records = [
        {
            "kind": "vision_measurement",
            "payload": {"frame_id": "frame-1", "captured_at": 1.0},
        },
        _metric("valid-before-error"),
        {"kind": "unsupported", "payload": {}},
    ]

    with pytest.raises(ValueError, match="unsupported"):
        db.record_v05_batch(records)

    counts = db.get_v05_counts()
    assert counts["vision_measurements"] == 0
    assert counts["run_metrics"] == 0

    db.record_v05_batch([_metric("later-commit")])
    assert db.get_v05_counts()["run_metrics"] == 1


@pytest.mark.asyncio
async def test_recorder_enters_terminal_failed_state_and_drains_queue(
    db: FlightDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_started = threading.Event()
    release_write = threading.Event()

    def fail_write(records: list[dict]) -> None:
        write_started.set()
        assert release_write.wait(1)
        raise sqlite3.OperationalError("injected write failure")

    monkeypatch.setattr(db, "record_v05_batch", fail_write)
    recorder = AsyncRecorder(db, capacity=8, batch_size=1, flush_interval_s=0.01)
    await recorder.start()
    assert recorder.record("run_metric", _metric("first")["payload"])
    assert await asyncio.to_thread(write_started.wait, 1)

    assert recorder.record("run_metric", _metric("queued-1")["payload"])
    assert recorder.record("run_metric", _metric("queued-2")["payload"])
    release_write.set()

    for _ in range(100):
        if recorder.metrics.state is RecorderState.FAILED:
            break
        await asyncio.sleep(0.001)

    assert recorder.metrics.state is RecorderState.FAILED
    assert recorder.record("run_metric", _metric("late")["payload"]) is False
    await recorder.stop()

    metrics = recorder.metrics
    assert metrics.state is RecorderState.FAILED
    assert metrics.accepted_records == 3
    assert metrics.written_records == 0
    assert metrics.failed_records == 3
    assert metrics.dropped_records == 1
    assert metrics.queue_depth == 0
    assert metrics.running is False
    assert "injected write failure" in (metrics.last_error or "")
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await recorder.start()


def test_reads_wait_for_atomic_batch_commit(
    db: FlightDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_record_reached = threading.Event()
    release_writer = threading.Event()
    reader_started = threading.Event()
    real_dumps = database_module.json.dumps
    dump_count = 0

    def blocking_dumps(value: object, *args: object, **kwargs: object) -> str:
        nonlocal dump_count
        dump_count += 1
        if dump_count == 2:
            second_record_reached.set()
            assert release_writer.wait(1)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(database_module.json, "dumps", blocking_dumps)
    records = [
        {
            "kind": "target_track",
            "payload": {
                "target_id": f"target-{index}",
                "timestamp": float(index),
            },
        }
        for index in range(2)
    ]

    def read_counts() -> dict[str, int]:
        reader_started.set()
        return db.get_v05_counts()

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(db.record_v05_batch, records)
        if not second_record_reached.wait(1):
            writer.result(timeout=1)
            pytest.fail("writer did not reach the second record")
        reader = executor.submit(read_counts)
        assert reader_started.wait(1)
        time.sleep(0.02)
        assert not reader.done()

        release_writer.set()
        writer.result(timeout=1)
        assert reader.result(timeout=1)["target_tracks"] == 2


def test_save_course_updates_referenced_parent_without_replace(
    db: FlightDatabase,
) -> None:
    db.save_course(_course(seed=1))
    db.start_simulation_run("run-1", course_id="course-1")

    db.save_course(
        _course(
            seed=2,
            gates=[
                {"id": "gate-a", "course_order": 0},
                {"id": "gate-b", "course_order": 1},
            ],
        )
    )

    assert db.get_course("course-1")["seed"] == 2
    counts = db.get_v05_counts()
    assert counts["courses"] == 1
    assert counts["course_gates"] == 2
    assert counts["simulation_runs"] == 1


def test_truth_validation_requires_the_recorded_frame_instance() -> None:
    store = DigitalTwinTruthStore()
    truth = SimulatorTruthFrame(
        timestamp=10.0,
        course_id="course-1",
        aircraft=AircraftTruth(
            timestamp=10.0,
            position=(1.0, 2.0, 3.0),
            velocity=(0.0, 0.0, 0.0),
            orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        ),
    )
    store.add_truth(truth)
    lookalike = replace(truth)
    assert lookalike == truth
    assert lookalike is not truth

    with pytest.raises(ValueError, match="recorded simulator truth"):
        store.add_validation(
            ValidationFrame(timestamp=10.1, simulator_truth=lookalike)
        )

    store.add_validation(ValidationFrame(timestamp=10.1, simulator_truth=truth))
    assert len(store.validation_frames) == 1


def test_course_request_derives_ceiling_from_floor_and_height() -> None:
    request = CourseGenerationRequest(seed=1, floor=5.0, height=20.0)
    volume = SafeVolume(
        dimensions=(request.width, request.length, request.height),
        floor=request.floor,
        ceiling=request.ceiling,
        boundary_margin=request.boundary_margin,
    )

    assert request.ceiling is None
    assert volume.floor == 5.0
    assert volume.ceiling == 25.0
    assert volume.height == 20.0


def test_course_request_rejects_conflicting_height_and_ceiling() -> None:
    with pytest.raises(ValidationError, match="ceiling must equal floor \\+ height"):
        CourseGenerationRequest(seed=1, floor=5.0, height=20.0, ceiling=20.0)
