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
    return FlightController(db, connection_manager=manager)
