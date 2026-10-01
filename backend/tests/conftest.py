"""Shared test fixtures.

Provides a controllable virtual clock so tests can advance the mock drone's
physics deterministically without sleeping.
"""
from __future__ import annotations

import pytest

from vantaflight.adapters import MockDroneAdapter
from vantaflight.connection import ConnectionManager
from vantaflight.core import FlightController
from vantaflight.data import FlightDatabase


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db() -> FlightDatabase:
    database = FlightDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def controller(db: FlightDatabase, clock: FakeClock) -> FlightController:
    manager = ConnectionManager(
        adapter_factory=lambda d: MockDroneAdapter(adapter_id=d.drone_id, time_source=clock)
    )
    return FlightController(db, connection_manager=manager, mission_clock=clock)


async def fly(controller: FlightController, clock: FakeClock, seconds: float, dt: float = 0.1):
    """Run the controller's periodic tick for ``seconds`` of simulated time."""
    telemetry = None
    for _ in range(int(round(seconds / dt))):
        clock.advance(dt)
        telemetry = await controller.tick()
    return telemetry


async def fly_until(controller, clock, predicate, timeout: float = 300.0, dt: float = 0.1):
    """Tick until ``predicate(telemetry)`` holds; fail the test on timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        clock.advance(dt)
        elapsed += dt
        telemetry = await controller.tick()
        if predicate(telemetry):
            return telemetry
    raise AssertionError(f"condition not reached within {timeout}s of simulated time")
