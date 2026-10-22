"""VantaRuntime — every long-running task is registered, supervised and stoppable.

Rules (docs/RUNTIME.md):

* A service never launches an unregistered background task. Long-running work
  goes through ``spawn`` / ``spawn_periodic``; short structured fan-out inside
  a worker goes through ``gather_children``. A source-level test enforces it.
* Every worker has a name, an owner, a criticality, a restart policy (never an
  infinite blind restart) and, if periodic, a period and a deadline. Misses
  and overruns are counted, not hidden.
* Lifecycle: STARTING → READY → ACTIVE (→ DEGRADED while a critical worker is
  failing) → STOPPING → STOPPED. Shutdown stops workers in a declared order
  and leaves zero registered workers.
* Time comes from an injected clock and sleep, so the runtime is
  deterministic under a fake clock.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import enum
import inspect
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from .health import ComponentHealth, DimensionStatus, HealthDimension, HealthState
from .metrics import RollingWindow, rounded, rounded_percentiles

logger = logging.getLogger(__name__)

RECENT_S = 10.0
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:#\-]*$")


class RuntimeState(str, enum.Enum):
    STARTING = "STARTING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class Criticality(str, enum.Enum):
    """How much the aircraft's safety depends on a worker. Stop order is reversed."""

    SAFETY = "SAFETY"  # failsafes, vehicle telemetry: last to stop
    CRITICAL = "CRITICAL"  # autonomy/execution, perception feeding it
    NORMAL = "NORMAL"  # recording, health monitoring
    BEST_EFFORT = "BEST_EFFORT"  # UI streaming: first to stop, never blocks the rest


STOP_ORDER = (Criticality.BEST_EFFORT, Criticality.NORMAL, Criticality.CRITICAL, Criticality.SAFETY)


class WorkerState(str, enum.Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    BACKOFF = "BACKOFF"  # crashed, waiting to restart
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RestartPolicy:
    """At most ``max_restarts`` restarts within ``window_s``, with growing backoff."""

    max_restarts: int = 0
    window_s: float = 60.0
    backoff_s: float = 0.5
    max_backoff_s: float = 10.0

    def __post_init__(self) -> None:
        if self.max_restarts < 0 or self.window_s <= 0 or self.backoff_s < 0 or self.max_backoff_s < 0:
            raise ValueError("restart policy values must be non-negative (window positive)")

    @classmethod
    def never(cls) -> "RestartPolicy":
        return cls(0)

    @classmethod
    def limited(cls, max_restarts: int, *, window_s: float = 60.0, backoff_s: float = 0.5) -> "RestartPolicy":
        return cls(max_restarts, window_s, backoff_s)

    def to_dict(self) -> dict:
        return {"max_restarts": self.max_restarts, "window_s": self.window_s, "backoff_s": self.backoff_s}


@dataclass(frozen=True)
class ResourceBudget:
    """Declared limits. ``max_busy_fraction`` is measured for periodic workers."""

    max_busy_fraction: float | None = None
    max_queue: int | None = None
    max_memory_mb: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class RuntimeStoppedError(RuntimeError):
    pass


_current_worker: contextvars.ContextVar["Worker | None"] = contextvars.ContextVar(
    "vantaflight_current_worker", default=None
)


class Worker:
    def __init__(
        self,
        runtime: "VantaRuntime",
        name: str,
        factory: Callable[[], Awaitable[Any]] | None,
        *,
        owner: str,
        criticality: Criticality,
        restart: RestartPolicy,
        budget: ResourceBudget | None,
        period_s: float | None = None,
        deadline_s: float | None = None,
        description: str = "",
    ) -> None:
        self.runtime = runtime
        self.name = name
        self.factory = factory
        self.owner = owner
        self.criticality = Criticality(criticality)
        self.restart = restart
        self.budget = budget or ResourceBudget()
        self.period_s = period_s
        self.deadline_s = deadline_s
        self.description = description
        self.task: asyncio.Task | None = None
        self.state = WorkerState.STARTING
        self.created_s = runtime.clock()
        self.started_s: float | None = None
        self.restart_times: deque[float] = deque(maxlen=64)
        self.restarts = 0
        self.last_error: str | None = None
        self.iterations = 0
        self.iteration_errors = 0
        self.last_iteration_error_s: float | None = None
        self.deadline_misses = 0
        self.skipped_periods = 0
        self._miss_times: deque[float] = deque(maxlen=256)
        self._iteration_times: deque[tuple[float, float]] = deque(maxlen=512)  # (end, duration)
        self.durations = RollingWindow(256)
        self.lateness = RollingWindow(256)
        self.children: set[asyncio.Task] = set()
        self.stop_requested = False
        self.tracked = factory is None  # a request-scoped task adopted by ``track``

    @property
    def alive(self) -> bool:
        return self.task is not None and not self.task.done()

    def recent_misses(self, now_s: float) -> int:
        return sum(1 for t in self._miss_times if t > now_s - RECENT_S)

    def recent_iterations(self, now_s: float) -> int:
        return sum(1 for end, _ in self._iteration_times if end > now_s - RECENT_S)

    def busy_fraction(self, now_s: float) -> float | None:
        if self.period_s is None:
            return None
        window = [(end, d) for end, d in self._iteration_times if end > now_s - RECENT_S]
        if not window:
            return 0.0
        span = max(now_s - (window[0][0] - window[0][1]), 1e-9)
        return min(1.0, sum(d for _, d in window) / span)

    def to_dict(self, now_s: float | None = None) -> dict:
        now_s = self.runtime.clock() if now_s is None else now_s
        out = {
            "name": self.name,
            "owner": self.owner,
            "criticality": self.criticality.value,
            "state": self.state.value,
            "uptime_s": rounded(now_s - self.started_s, 1) if self.started_s is not None else 0.0,
            "restarts": self.restarts,
            "restart_policy": self.restart.to_dict(),
            "last_error": self.last_error,
            "children": len(self.children),
            "budget": self.budget.to_dict(),
        }
        if self.description:
            out["description"] = self.description
        if self.period_s is not None:
            out.update(
                {
                    "period_ms": rounded(self.period_s * 1000, 2),
                    "deadline_ms": rounded((self.deadline_s or self.period_s) * 1000, 2),
                    "iterations": self.iterations,
                    "iteration_errors": self.iteration_errors,
                    "deadline_misses": self.deadline_misses,
                    "skipped_periods": self.skipped_periods,
                    "duration_ms": rounded_percentiles(self.durations.percentiles(), 1000.0, 3),
                    "lateness_ms": rounded_percentiles(self.lateness.percentiles(), 1000.0, 3),
                    "busy_fraction": rounded(self.busy_fraction(now_s), 4),
                }
            )
        return out


class VantaRuntime:
    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        *,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        name: str = "vantaflight",
        library_mode: bool = False,
    ) -> None:
        self.clock = clock or time.monotonic
        self._sleep = sleep
        self.name = name
        self._workers: dict[str, Worker] = {}
        self._finished: deque[dict] = deque(maxlen=50)
        self._baseline: set[asyncio.Task] = set()
        self._phase = RuntimeState.ACTIVE if library_mode else RuntimeState.STARTING
        self.library_mode = library_mode
        self.started_s: float | None = None

    # -- lifecycle ----------------------------------------------------------------------------------
    @property
    def state(self) -> RuntimeState:
        if self._phase is RuntimeState.ACTIVE and self._degraded():
            return RuntimeState.DEGRADED
        return self._phase

    def _degraded(self) -> bool:
        now_s = self.clock()
        for worker in self._workers.values():
            if worker.criticality in (Criticality.SAFETY, Criticality.CRITICAL):
                if worker.state in (WorkerState.FAILED, WorkerState.BACKOFF):
                    return True
                if worker.criticality is Criticality.SAFETY and worker.recent_misses(now_s):
                    return True
        return False

    async def start(self) -> None:
        """STARTING: remember the tasks that already exist (server, lifespan)."""
        if self._phase not in (RuntimeState.STARTING, RuntimeState.STOPPED):
            raise RuntimeError(f"cannot start a runtime that is {self._phase.value}")
        self._phase = RuntimeState.STARTING
        self.started_s = self.clock()
        with contextlib.suppress(RuntimeError):
            self._baseline = {t for t in asyncio.all_tasks() if not t.done()}

    def mark_ready(self) -> None:
        if self._phase is RuntimeState.STARTING:
            self._phase = RuntimeState.READY

    def activate(self) -> None:
        if self._phase in (RuntimeState.STARTING, RuntimeState.READY):
            self._phase = RuntimeState.ACTIVE

    def begin_shutdown(self) -> None:
        if self._phase is not RuntimeState.STOPPED:
            self._phase = RuntimeState.STOPPING

    # -- registration ----------------------------------------------------------------------------
    def _register(self, worker: Worker, singleton: bool) -> None:
        if not _NAME_RE.match(worker.name) or len(worker.name) > 96:
            raise ValueError(f"invalid worker name {worker.name!r}")
        if self._phase in (RuntimeState.STOPPING, RuntimeState.STOPPED) and not self.library_mode:
            raise RuntimeStoppedError(f"runtime is {self._phase.value}; refusing to start {worker.name}")
        existing = self._workers.get(worker.name)
        if existing is not None and existing.task is not None and _orphaned(existing.task):
            # Its event loop is gone (a closed test loop): it can never run again.
            del self._workers[worker.name]
            existing = None
        if existing is not None:
            if singleton:
                raise RuntimeError(f"worker {worker.name!r} is already registered ({existing.state.value})")
            n = 2
            while f"{worker.name}#{n}" in self._workers:
                n += 1
            worker.name = f"{worker.name}#{n}"
        self._workers[worker.name] = worker

    def spawn(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        owner: str,
        criticality: Criticality = Criticality.NORMAL,
        restart: RestartPolicy | None = None,
        budget: ResourceBudget | None = None,
        description: str = "",
        singleton: bool = True,
    ) -> Worker:
        """Run ``factory()`` (a coroutine function) as a supervised worker.

        The worker's task is the supervisor: restarts happen inside it, so a
        worker is always exactly one task.
        """
        if not callable(factory):
            raise TypeError("factory must be a callable returning a coroutine")
        worker = Worker(
            self, name, factory, owner=owner, criticality=criticality,
            restart=restart or RestartPolicy.never(), budget=budget, description=description,
        )
        self._register(worker, singleton)
        worker.task = asyncio.get_running_loop().create_task(
            self._supervise(worker), name=f"vanta:{worker.name}"
        )
        return worker

    def spawn_periodic(
        self,
        name: str,
        step: Callable[[], Any],
        *,
        period_s: float,
        owner: str,
        deadline_s: float | None = None,
        criticality: Criticality = Criticality.NORMAL,
        restart: RestartPolicy | None = None,
        budget: ResourceBudget | None = None,
        on_error: str = "continue",
        description: str = "",
        singleton: bool = True,
    ) -> Worker:
        """Call ``step()`` every ``period_s`` on the runtime clock.

        A step that raises is counted (``iteration_errors``) and the loop goes
        on (``on_error="continue"``), or the worker crashes and its restart
        policy applies (``on_error="raise"``). Completion later than
        ``deadline_s`` (default: the period) after the scheduled release is a
        deadline miss. Overruns skip missed periods instead of bursting.
        """
        if not period_s > 0:
            raise ValueError("period_s must be positive")
        if deadline_s is not None and not deadline_s > 0:
            raise ValueError("deadline_s must be positive")
        if on_error not in ("continue", "raise"):
            raise ValueError("on_error must be 'continue' or 'raise'")
        holder: dict[str, Worker] = {}

        async def factory() -> None:
            await self._periodic(holder["worker"], step, on_error)

        worker = Worker(
            self, name, factory, owner=owner, criticality=criticality,
            restart=restart or RestartPolicy.never(), budget=budget,
            period_s=period_s, deadline_s=deadline_s, description=description,
        )
        holder["worker"] = worker
        self._register(worker, singleton)
        worker.task = asyncio.get_running_loop().create_task(
            self._supervise(worker), name=f"vanta:{worker.name}"
        )
        return worker

    @contextlib.asynccontextmanager
    async def track(self, name: str, *, owner: str, criticality: Criticality = Criticality.BEST_EFFORT):
        """Register the *current* task (e.g. a WebSocket session) while inside the block."""
        worker = Worker(
            self, name, None, owner=owner, criticality=criticality,
            restart=RestartPolicy.never(), budget=None,
        )
        self._register(worker, singleton=False)
        worker.task = asyncio.current_task()
        worker.state = WorkerState.RUNNING
        worker.started_s = self.clock()
        try:
            yield worker
        finally:
            worker.state = WorkerState.STOPPED
            self._retire(worker)

    # -- supervision ---------------------------------------------------------------------------------
    async def _supervise(self, worker: Worker) -> None:
        token = _current_worker.set(worker)
        try:
            while True:
                worker.state = WorkerState.RUNNING
                worker.started_s = self.clock()
                try:
                    await worker.factory()
                except asyncio.CancelledError:
                    worker.state = WorkerState.STOPPED
                    raise
                except Exception as exc:
                    worker.last_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("worker %s crashed", worker.name)
                    delay = self._restart_delay(worker)
                    if delay is None:
                        worker.state = WorkerState.FAILED
                        logger.error("worker %s failed permanently: %s", worker.name, worker.last_error)
                        return
                    worker.state = WorkerState.BACKOFF
                    await self._sleep(delay)
                    worker.restarts += 1
                    worker.restart_times.append(self.clock())
                    continue
                worker.state = WorkerState.COMPLETED
                return
        finally:
            if worker.state in (WorkerState.STARTING, WorkerState.RUNNING, WorkerState.BACKOFF, WorkerState.STOPPING):
                worker.state = WorkerState.STOPPED  # cancelled (possibly while backing off)
            await self._cancel_children(worker)
            _current_worker.reset(token)
            if worker.state is not WorkerState.FAILED:
                self._retire(worker)

    def _restart_delay(self, worker: Worker) -> float | None:
        policy = worker.restart
        if worker.stop_requested or policy.max_restarts == 0:
            return None
        now_s = self.clock()
        recent = [t for t in worker.restart_times if t > now_s - policy.window_s]
        if len(recent) >= policy.max_restarts:
            return None
        return min(policy.max_backoff_s, policy.backoff_s * (2 ** len(recent)))

    async def _periodic(self, worker: Worker, step: Callable[[], Any], on_error: str) -> None:
        period = worker.period_s or 0.0
        deadline = worker.deadline_s or period
        next_due = self.clock()
        while True:
            started = self.clock()
            lateness = max(0.0, started - next_due)
            try:
                result = step()
                if inspect.isawaitable(result):
                    await result
                worker.iterations += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                worker.iteration_errors += 1
                worker.last_error = f"{type(exc).__name__}: {exc}"
                worker.last_iteration_error_s = self.clock()
                if on_error == "raise":
                    raise
                logger.exception("worker %s: step failed; continuing", worker.name)
            ended = self.clock()
            duration = max(0.0, ended - started)
            worker.durations.add(duration)
            worker.lateness.add(lateness)
            worker._iteration_times.append((ended, duration))
            if lateness + duration > deadline + 1e-9:
                worker.deadline_misses += 1
                worker._miss_times.append(ended)
            next_due += period
            if next_due <= ended:
                worker.skipped_periods += int((ended - next_due) // period) + 1 if period > 0 else 0
                next_due = ended + period if period > 0 else ended
            await self._sleep(max(0.0, next_due - self.clock()))

    async def _cancel_children(self, worker: Worker) -> None:
        children = [t for t in worker.children if not t.done()]
        for child in children:
            child.cancel()
        if children:
            await asyncio.gather(*children, return_exceptions=True)
        worker.children.clear()

    def _retire(self, worker: Worker) -> None:
        if self._workers.get(worker.name) is worker:
            del self._workers[worker.name]
            self._finished.append(
                {
                    "name": worker.name,
                    "owner": worker.owner,
                    "state": worker.state.value,
                    "restarts": worker.restarts,
                    "last_error": worker.last_error,
                    "ended_s": rounded(self.clock(), 3),
                }
            )

    # -- stopping ------------------------------------------------------------------------------------
    def worker(self, name: str) -> Worker | None:
        return self._workers.get(name)

    def workers(self) -> list[Worker]:
        return [self._workers[k] for k in sorted(self._workers)]

    async def stop_worker(self, name: str, timeout_s: float = 5.0) -> bool:
        """Cancel one worker and wait for it; False if it did not stop in time."""
        worker = self._workers.get(name)
        if worker is None:
            return True
        worker.stop_requested = True
        if worker.state is WorkerState.FAILED or worker.task is None or worker.task.done():
            worker.state = WorkerState.STOPPED if worker.state is not WorkerState.FAILED else worker.state
            self._workers.pop(name, None)
            return True
        worker.state = WorkerState.STOPPING
        task = worker.task
        if task is asyncio.current_task():
            raise RuntimeError(f"worker {name} cannot stop itself; return from it instead")
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
        if not done:
            logger.error("worker %s did not stop within %.1fs", name, timeout_s)
            return False
        self._workers.pop(name, None)
        return True

    async def stop_owner(self, owner: str, timeout_s: float = 5.0) -> list[str]:
        names = [w.name for w in self.workers() if w.owner == owner]
        for name in names:
            await self.stop_worker(name, timeout_s)
        return names

    def clear_failed(self, name: str) -> bool:
        worker = self._workers.get(name)
        if worker is not None and worker.state is WorkerState.FAILED:
            del self._workers[name]
            self._finished.append({"name": name, "owner": worker.owner, "state": "FAILED",
                                   "restarts": worker.restarts, "last_error": worker.last_error,
                                   "ended_s": rounded(self.clock(), 3)})
            return True
        return False

    async def shutdown(self, timeout_s: float = 10.0) -> dict:
        """Stop every worker, least critical first; leaves zero registered workers."""
        self.begin_shutdown()
        stopped: list[str] = []
        stuck: list[str] = []
        for criticality in STOP_ORDER:
            names = [w.name for w in self.workers() if w.criticality is criticality]
            results = await asyncio.gather(*(self.stop_worker(n, timeout_s) for n in names))
            for name, ok in zip(names, results):
                (stopped if ok else stuck).append(name)
        for worker in list(self._workers.values()):  # failed ones, or stragglers
            if worker.alive:
                stuck.append(worker.name)
            self._workers.pop(worker.name, None)
        self._phase = RuntimeState.STOPPED
        return {"stopped": stopped, "stuck": stuck, "remaining": [w.name for w in self.workers()]}

    # -- observation ---------------------------------------------------------------------------------
    def unmanaged_tasks(self) -> list[str]:
        """Tasks in this loop that nobody registered (call from inside the loop).

        Excludes the caller, tasks that existed before ``start`` (the server,
        the lifespan) and workers' structured children.
        """
        try:
            tasks = asyncio.all_tasks()
        except RuntimeError:
            return []
        current = asyncio.current_task()
        managed: set[asyncio.Task] = set(self._baseline)
        for worker in self._workers.values():
            if worker.task is not None:
                managed.add(worker.task)
            managed.update(worker.children)
        out = []
        for task in tasks:
            if task is current or task in managed or task.done():
                continue
            coro = task.get_coro()
            label = getattr(coro, "__qualname__", None) or repr(coro)
            out.append(f"{task.get_name()} ({label})")
        return sorted(out)

    def status(self, now_s: float | None = None, *, include_unmanaged: bool = True) -> dict:
        now_s = self.clock() if now_s is None else now_s
        workers = self.workers()
        return {
            "lifecycle": self.state.value,
            "uptime_s": rounded(now_s - self.started_s, 1) if self.started_s is not None else None,
            "workers": [w.to_dict(now_s) for w in workers],
            "worker_count": len(workers),
            "unmanaged_tasks": self.unmanaged_tasks() if include_unmanaged else [],
            "recently_finished": list(self._finished)[-20:],
        }

    def health(self, now_s: float | None = None) -> Iterable[ComponentHealth]:
        now_s = self.clock() if now_s is None else now_s
        lifecycle = self.state
        live = {
            RuntimeState.ACTIVE: HealthState.HEALTHY,
            RuntimeState.READY: HealthState.STARTING,
            RuntimeState.STARTING: HealthState.STARTING,
            RuntimeState.DEGRADED: HealthState.DEGRADED,
            RuntimeState.STOPPING: HealthState.STOPPED,
            RuntimeState.STOPPED: HealthState.STOPPED,
        }[lifecycle]
        unmanaged = self.unmanaged_tasks()
        yield ComponentHealth(
            "runtime", "runtime",
            {
                HealthDimension.LIVENESS: DimensionStatus(live, lifecycle.value),
                HealthDimension.CORRECTNESS: DimensionStatus(
                    HealthState.DEGRADED if unmanaged else HealthState.HEALTHY,
                    f"unmanaged tasks: {', '.join(unmanaged)}" if unmanaged else "every task registered",
                    len(unmanaged),
                ),
            },
        )
        for worker in self.workers():
            yield worker_health(worker, now_s)


def worker_health(worker: Worker, now_s: float) -> ComponentHealth:
    liveness = {
        WorkerState.STARTING: HealthState.STARTING,
        WorkerState.RUNNING: HealthState.HEALTHY,
        WorkerState.BACKOFF: HealthState.RECOVERING,
        WorkerState.STOPPING: HealthState.STOPPED,
        WorkerState.STOPPED: HealthState.STOPPED,
        WorkerState.COMPLETED: HealthState.STOPPED,
        WorkerState.FAILED: HealthState.FAILED,
    }[worker.state]
    dims = {
        HealthDimension.LIVENESS: DimensionStatus(
            liveness, worker.state.value + (f": {worker.last_error}" if worker.last_error else "")
        )
    }
    if worker.period_s is not None:
        misses = worker.recent_misses(now_s)
        iterations = worker.recent_iterations(now_s)
        busy = worker.busy_fraction(now_s) or 0.0
        budget = worker.budget.max_busy_fraction
        if iterations and misses > iterations / 2:
            resource = HealthState.FAILED
        elif misses:
            resource = HealthState.DEGRADED
        elif budget is not None and busy > budget:
            resource = HealthState.DEGRADED
        else:
            resource = HealthState.HEALTHY
        dims[HealthDimension.RESOURCE] = DimensionStatus(
            resource,
            f"{misses} deadline miss(es) in {iterations} iterations over {RECENT_S:g}s; busy {busy:.0%}",
            misses,
        )
        recent_error = (worker.last_iteration_error_s or -1e18) > now_s - RECENT_S
        dims[HealthDimension.CORRECTNESS] = DimensionStatus(
            HealthState.DEGRADED if recent_error else HealthState.HEALTHY,
            f"step errors: {worker.iteration_errors}" + (f" (last: {worker.last_error})" if recent_error else ""),
            worker.iteration_errors,
        )
    return ComponentHealth(f"runtime:{worker.name}", "runtime.worker", dims)


def _orphaned(task: asyncio.Task) -> bool:
    try:
        return task.get_loop().is_closed()
    except Exception:
        return False


async def gather_children(*aws: Awaitable[Any], return_exceptions: bool = False) -> list[Any]:
    """Structured fan-out inside a worker: children live and die with it.

    Like ``asyncio.gather`` but the child tasks are attached to the current
    worker (visible in runtime status, never "unmanaged") and are all
    cancelled if one fails or the worker is cancelled.
    """
    worker = _current_worker.get()
    loop = asyncio.get_running_loop()
    tasks = [loop.create_task(_as_coro(aw)) for aw in aws]
    if worker is not None:
        worker.children.update(tasks)
    try:
        return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        if worker is not None:
            worker.children.difference_update(tasks)


async def _as_coro(aw: Awaitable[Any]) -> Any:
    return await aw


def current_worker() -> Worker | None:
    return _current_worker.get()


_default_runtime: VantaRuntime | None = None


def default_runtime() -> VantaRuntime:
    """Process-wide runtime for components built without VantaSystem (tests, tools)."""
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = VantaRuntime(name="default", library_mode=True)
    return _default_runtime


def set_default_runtime(runtime: VantaRuntime | None) -> None:
    global _default_runtime
    _default_runtime = runtime
