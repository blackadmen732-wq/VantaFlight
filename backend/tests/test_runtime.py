"""VantaRuntime: registered workers, deadlines on a fake clock, restarts, ordered shutdown."""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from vantaflight.core.health import HealthDimension, HealthState
from vantaflight.core.runtime import (
    Criticality,
    ResourceBudget,
    RestartPolicy,
    RuntimeState,
    RuntimeStoppedError,
    VantaRuntime,
    WorkerState,
    gather_children,
    worker_health,
)


class FakeTime:
    """A clock plus a sleep that advances it: the runtime runs deterministically."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)
        await asyncio.sleep(0)


async def settle(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def runtime_with_fake_time() -> tuple[VantaRuntime, FakeTime]:
    fake = FakeTime()
    runtime = VantaRuntime(fake, sleep=fake.sleep)
    await runtime.start()
    runtime.mark_ready()
    runtime.activate()
    return runtime, fake


async def test_lifecycle_and_worker_registration():
    runtime = VantaRuntime()
    assert runtime.state is RuntimeState.STARTING
    await runtime.start()
    runtime.mark_ready()
    assert runtime.state is RuntimeState.READY
    runtime.activate()
    assert runtime.state is RuntimeState.ACTIVE

    done = asyncio.Event()

    async def job():
        await done.wait()

    worker = runtime.spawn("demo.job", job, owner="tests", criticality=Criticality.NORMAL)
    await settle()
    status = runtime.status()
    assert [w["name"] for w in status["workers"]] == ["demo.job"]
    assert status["workers"][0]["state"] == "RUNNING" and status["unmanaged_tasks"] == []
    done.set()
    await worker.task
    assert runtime.workers() == [], "a finished worker leaves the registry"
    assert runtime.status()["recently_finished"][-1]["state"] == "COMPLETED"


async def test_worker_names_are_unique_unless_asked_otherwise():
    runtime, _ = await runtime_with_fake_time()
    hold = asyncio.Event()

    async def job():
        await hold.wait()

    runtime.spawn("flight.tick", job, owner="tests")
    with pytest.raises(RuntimeError, match="already registered"):
        runtime.spawn("flight.tick", job, owner="tests")  # a second failsafe loop would be a bug
    second = runtime.spawn("vision.capture:cam", job, owner="tests", singleton=False)
    third = runtime.spawn("vision.capture:cam", job, owner="tests", singleton=False)
    assert (second.name, third.name) == ("vision.capture:cam", "vision.capture:cam#2")
    with pytest.raises(ValueError):
        runtime.spawn("Bad Name", job, owner="tests")
    await runtime.shutdown()


async def test_periodic_worker_is_deterministic_and_counts_deadline_misses():
    runtime, fake = await runtime_with_fake_time()
    calls = []

    def step():
        calls.append(fake.t)
        if len(calls) in (5, 6):
            fake.t += 0.25  # this step overran its 100 ms deadline

    worker = runtime.spawn_periodic(
        "flight.tick", step, period_s=0.1, owner="tests", criticality=Criticality.SAFETY
    )
    while len(calls) < 12:
        await asyncio.sleep(0)
    assert calls[:4] == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert worker.deadline_misses == 2
    assert worker.skipped_periods >= 2, "an overrun skips periods instead of bursting to catch up"
    assert runtime.state is RuntimeState.DEGRADED, "a SAFETY worker missing deadlines degrades the runtime"
    health = worker_health(worker, fake.t)
    assert health.dimension(HealthDimension.RESOURCE).state is HealthState.DEGRADED
    status = worker.to_dict()
    assert status["deadline_ms"] == 100.0 and status["duration_ms"]["max"] == pytest.approx(250.0)
    fake.t += 20.0  # the misses age out of the recent window
    assert runtime.state is RuntimeState.ACTIVE
    await runtime.shutdown()


async def test_periodic_step_errors_are_counted_and_the_loop_continues():
    runtime, fake = await runtime_with_fake_time()
    count = {"n": 0}

    def step():
        count["n"] += 1
        if count["n"] == 3:
            raise ValueError("bad sample")

    worker = runtime.spawn_periodic("flight.tick", step, period_s=0.1, owner="tests")
    while count["n"] < 6:
        await asyncio.sleep(0)
    assert worker.iteration_errors == 1 and worker.state is WorkerState.RUNNING
    assert "bad sample" in worker.last_error
    assert worker_health(worker, fake.t).dimension(HealthDimension.CORRECTNESS).state is HealthState.DEGRADED
    await runtime.shutdown()


async def test_restarts_are_bounded_never_blind():
    runtime, fake = await runtime_with_fake_time()
    starts = []

    async def flaky():
        starts.append(fake.t)
        raise RuntimeError("camera unplugged")

    worker = runtime.spawn(
        "vision.capture:cam", flaky, owner="tests", criticality=Criticality.CRITICAL,
        restart=RestartPolicy.limited(2, window_s=60.0, backoff_s=0.5),
    )
    await worker.task
    assert len(starts) == 3, "first run + 2 restarts, then give up"
    assert starts[1] - starts[0] == pytest.approx(0.5) and starts[2] - starts[1] == pytest.approx(1.0)
    assert worker.state is WorkerState.FAILED and worker.restarts == 2
    assert runtime.worker("vision.capture:cam") is worker, "a failed worker stays visible"
    assert runtime.state is RuntimeState.DEGRADED
    assert runtime.clear_failed("vision.capture:cam")
    assert runtime.state is RuntimeState.ACTIVE


async def test_shutdown_stops_least_critical_first_and_leaves_zero_workers():
    runtime, _ = await runtime_with_fake_time()
    order = []

    def job(name):
        async def run():
            try:
                await asyncio.Event().wait()
            finally:
                order.append(name)

        return run

    for name, crit in (
        ("flight.tick", Criticality.SAFETY), ("ui.stream", Criticality.BEST_EFFORT),
        ("race.loop", Criticality.CRITICAL), ("recorder.writer", Criticality.NORMAL),
    ):
        runtime.spawn(name, job(name), owner="tests", criticality=crit)
    await settle()
    report = await runtime.shutdown()
    assert order == ["ui.stream", "recorder.writer", "race.loop", "flight.tick"]
    assert report["stuck"] == [] and report["remaining"] == []
    assert runtime.workers() == [] and runtime.state is RuntimeState.STOPPED
    with pytest.raises(RuntimeStoppedError):
        runtime.spawn("late.job", job("late"), owner="tests")


async def test_unregistered_tasks_are_reported_children_are_not():
    runtime, _ = await runtime_with_fake_time()
    release = asyncio.Event()

    async def child():
        await release.wait()

    async def parent():
        await gather_children(child(), child())

    worker = runtime.spawn("px4.telemetry:udp", parent, owner="tests")
    await settle()
    assert len(worker.children) == 2
    assert runtime.unmanaged_tasks() == []
    rogue = asyncio.get_running_loop().create_task(child(), name="ghost-camera-loop")
    await settle()
    assert any("ghost-camera-loop" in t for t in runtime.unmanaged_tasks())
    health = {c.component: c for c in runtime.health()}
    assert health["runtime"].dimension(HealthDimension.CORRECTNESS).state is HealthState.DEGRADED
    release.set()
    await rogue
    await worker.task
    assert worker.children == set()


async def test_stopping_a_worker_cancels_its_children():
    runtime, _ = await runtime_with_fake_time()
    cancelled = []

    async def child(i):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(i)
            raise

    async def parent():
        await gather_children(child(1), child(2))

    runtime.spawn("px4.telemetry:udp", parent, owner="tests")
    await settle()
    assert await runtime.stop_worker("px4.telemetry:udp")
    assert sorted(cancelled) == [1, 2] and runtime.workers() == []


async def test_one_failing_child_cancels_its_siblings():
    runtime, _ = await runtime_with_fake_time()
    cancelled = []

    async def sibling():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append("sibling")
            raise

    async def broken():
        await asyncio.sleep(0)
        raise OSError("stream lost")

    async def parent():
        await gather_children(sibling(), broken())

    worker = runtime.spawn("px4.telemetry:udp", parent, owner="tests")
    await worker.task
    assert cancelled == ["sibling"] and worker.state is WorkerState.FAILED
    assert "stream lost" in worker.last_error


async def test_track_registers_the_current_task():
    runtime, _ = await runtime_with_fake_time()
    async with runtime.track("ui.ws_client", owner="ui.hub") as worker:
        assert worker.task is asyncio.current_task()
        assert runtime.worker("ui.ws_client") is worker
        assert runtime.unmanaged_tasks() == []
    assert runtime.workers() == []


async def test_busy_budget_is_measured():
    runtime, fake = await runtime_with_fake_time()

    def heavy():
        fake.t += 0.08  # 80 % of a 100 ms period

    worker = runtime.spawn_periodic(
        "vision.process", heavy, period_s=0.1, owner="tests",
        budget=ResourceBudget(max_busy_fraction=0.5),
    )
    while worker.iterations < 20:
        await asyncio.sleep(0)
    assert worker.busy_fraction(fake.t) == pytest.approx(0.8, abs=0.02)
    resource = worker_health(worker, fake.t).dimension(HealthDimension.RESOURCE)
    assert resource.state is HealthState.DEGRADED and "busy 8" in resource.detail
    await runtime.shutdown()


def test_no_service_launches_an_unregistered_background_task():
    """Spec rule: long-running work goes through VantaRuntime, nowhere else."""
    root = pathlib.Path(__file__).resolve().parents[1] / "vantaflight"
    pattern = re.compile(
        r"\b(create_task|ensure_future|run_coroutine_threadsafe|start_new_thread)\s*\(|threading\.Thread\s*\("
    )
    allowed = {root / "core" / "runtime.py"}
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path in allowed:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                offenders.append(f"{path.relative_to(root.parent)}:{lineno}: {line.strip()}")
    assert offenders == [], "unregistered background tasks:\n" + "\n".join(offenders)
