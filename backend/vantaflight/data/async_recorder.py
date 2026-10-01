"""Bounded asynchronous persistence for non-critical intelligence records."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .database import FlightDatabase


class RecorderState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RecorderMetrics:
    accepted_records: int
    written_records: int
    failed_records: int
    dropped_records: int
    queue_depth: int
    running: bool
    state: RecorderState
    last_error: str | None


class AsyncRecorder:
    """Batch records off the perception/control path with bounded backpressure.

    Enqueue is non-blocking. When overloaded, the new analysis record is
    dropped rather than delaying a perception or control loop.
    """

    _STOP = object()

    def __init__(
        self,
        database: FlightDatabase,
        *,
        capacity: int = 512,
        batch_size: int = 32,
        flush_interval_s: float = 0.1,
    ) -> None:
        if capacity < 1 or batch_size < 1 or flush_interval_s <= 0:
            raise ValueError("recorder limits must be positive")
        self._database = database
        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(capacity)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._task: asyncio.Task[None] | None = None
        self._accepting = False
        self._state = RecorderState.STOPPED
        self._last_error: str | None = None
        self._accepted = 0
        self._written = 0
        self._failed = 0
        self._dropped = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._state is RecorderState.FAILED:
            raise RuntimeError("failed recorder cannot be restarted")
        self._accepting = True
        self._state = RecorderState.RUNNING
        self._task = asyncio.create_task(self._run(), name="vantaflight-recorder")

    def record(self, kind: str, payload: dict[str, Any]) -> bool:
        if not self._accepting:
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait({"kind": kind, "payload": payload})
        except asyncio.QueueFull:
            self._dropped += 1
            return False
        self._accepted += 1
        return True

    async def stop(self) -> None:
        task = self._task
        if task is None:
            self._accepting = False
            return
        self._accepting = False
        if not task.done():
            self._state = RecorderState.STOPPING
            await self._queue.put(self._STOP)
        await task
        self._task = None
        if self._state is not RecorderState.FAILED:
            self._state = RecorderState.STOPPED

    @property
    def metrics(self) -> RecorderMetrics:
        return RecorderMetrics(
            accepted_records=self._accepted,
            written_records=self._written,
            failed_records=self._failed,
            dropped_records=self._dropped,
            queue_depth=self._queue.qsize(),
            running=self._task is not None and not self._task.done(),
            state=self._state,
            last_error=self._last_error,
        )

    async def _write(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        await asyncio.to_thread(self._database.record_v05_batch, batch)
        self._written += len(batch)

    def _fail(self, exc: Exception, pending: int) -> None:
        self._accepting = False
        self._state = RecorderState.FAILED
        self._last_error = f"{type(exc).__name__}: {exc}"
        self._failed += pending
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not self._STOP:
                self._failed += 1
            self._queue.task_done()

    async def _run(self) -> None:
        batch: list[dict[str, Any]] = []
        stopping = False
        try:
            while not stopping:
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=self._flush_interval_s
                    )
                except asyncio.TimeoutError:
                    item = None

                if item is self._STOP:
                    stopping = True
                    self._queue.task_done()
                elif item is not None:
                    batch.append(item)
                    self._queue.task_done()

                while len(batch) < self._batch_size and not self._queue.empty():
                    item = self._queue.get_nowait()
                    if item is self._STOP:
                        stopping = True
                        self._queue.task_done()
                        break
                    batch.append(item)
                    self._queue.task_done()

                if batch and (
                    stopping or len(batch) >= self._batch_size or item is None
                ):
                    await self._write(batch)
                    batch = []

            if batch:
                await self._write(batch)
        except Exception as exc:
            self._fail(exc, len(batch))
