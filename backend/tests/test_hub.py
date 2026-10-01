"""WebSocket hub: a slow or broken client must not stall everyone else."""
from __future__ import annotations

import asyncio
import time

from vantaflight.main import ConnectionHub


class FakeWS:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay, self.fail, self.frames = delay, fail, []

    async def accept(self) -> None:
        pass

    async def send_json(self, message) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        await asyncio.sleep(self.delay)
        self.frames.append(message)


async def test_slow_and_dead_clients_are_dropped_without_blocking():
    hub = ConnectionHub(send_timeout_s=0.1)
    healthy, slow, dead = FakeWS(), FakeWS(delay=5), FakeWS(fail=True)
    for ws in (healthy, slow, dead):
        await hub.register(ws)

    t0 = time.monotonic()
    await hub.broadcast({"type": "telemetry"})
    assert time.monotonic() - t0 < 1.0
    assert healthy.frames == [{"type": "telemetry"}]
    assert hub.count == 1

    await hub.broadcast({"type": "event"})
    assert len(healthy.frames) == 2


async def test_broadcast_with_no_clients_is_a_noop():
    await ConnectionHub().broadcast({"type": "telemetry"})
