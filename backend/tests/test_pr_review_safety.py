from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vantaflight.adapters import MockDroneAdapter
from vantaflight.connection import ConnectionManager
from vantaflight.core import FlightController
from vantaflight.core.flight_controller import SessionState
from vantaflight.data import FlightDatabase
from vantaflight.main import ConnectionHub, create_app


class DelayedArmAdapter(MockDroneAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.active_arm_calls = 0
        self.max_active_arm_calls = 0

    async def arm(self) -> None:
        self.active_arm_calls += 1
        self.max_active_arm_calls = max(
            self.max_active_arm_calls,
            self.active_arm_calls,
        )
        try:
            await asyncio.sleep(0.01)
            await super().arm()
        finally:
            self.active_arm_calls -= 1


class LinkLossDuringArmAdapter(MockDroneAdapter):
    async def arm(self) -> None:
        await asyncio.sleep(0)
        self.force_connection_loss()


class BlockedSocket:
    async def send_json(self, _message: dict) -> None:
        await asyncio.Event().wait()


def controller_for(
    db: FlightDatabase,
    adapter: MockDroneAdapter,
) -> FlightController:
    manager = ConnectionManager(adapter_factory=lambda _drone: adapter)
    return FlightController(db, connection_manager=manager)


@pytest.mark.asyncio
async def test_disarm_is_rejected_immediately_after_takeoff() -> None:
    db = FlightDatabase(":memory:")
    try:
        controller = controller_for(db, MockDroneAdapter())
        assert (await controller.connect()).accepted
        assert (await controller.command("arm")).accepted
        assert (
            await controller.command("takeoff", target_altitude_m=5.0)
        ).accepted

        result = await controller.command("disarm")

        assert result.accepted is False
        assert result.message == "cannot disarm while airborne"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_concurrent_adapter_commands_are_serialized() -> None:
    db = FlightDatabase(":memory:")
    try:
        adapter = DelayedArmAdapter()
        controller = controller_for(db, adapter)
        assert (await controller.connect()).accepted

        results = await asyncio.gather(
            controller.command("arm"),
            controller.command("arm"),
        )

        assert sum(result.accepted for result in results) == 1
        assert adapter.max_active_arm_calls == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_command_is_not_acknowledged_after_link_loss() -> None:
    db = FlightDatabase(":memory:")
    try:
        controller = controller_for(db, LinkLossDuringArmAdapter())
        assert (await controller.connect()).accepted

        result = await controller.command("arm")

        assert result.accepted is False
        assert result.message == "connection lost while command was in progress"
        assert controller.session_state is SessionState.INTERRUPTED
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("altitude", [float("nan"), float("inf"), -1.0, 0.15, 121.0])
async def test_invalid_takeoff_altitudes_are_rejected(altitude: float) -> None:
    db = FlightDatabase(":memory:")
    try:
        controller = controller_for(db, MockDroneAdapter())
        assert (await controller.connect()).accepted
        assert (await controller.command("arm")).accepted

        result = await controller.command("takeoff", target_altitude_m=altitude)

        assert result.accepted is False
    finally:
        db.close()


@pytest.mark.asyncio
async def test_blocked_websocket_is_bounded_and_evicted() -> None:
    hub = ConnectionHub(send_timeout_s=0.01)
    socket = BlockedSocket()
    hub._clients.add(socket)  # type: ignore[arg-type]

    started = time.monotonic()
    await hub.broadcast({"type": "telemetry"})

    assert time.monotonic() - started < 0.2
    assert hub.count == 0


def test_cross_origin_flight_command_is_rejected() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.post(
            "/api/connect",
            headers={"origin": "https://evil.example"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "cross-origin command rejected"}


def test_trusted_origin_flight_command_is_allowed() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.post(
            "/api/connect",
            headers={"origin": "http://127.0.0.1:5173"},
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_cross_origin_websocket_is_rejected() -> None:
    with TestClient(create_app(":memory:")) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/ws/telemetry",
                headers={"origin": "https://evil.example"},
            ):
                pass

    assert rejected.value.code == 1008


def test_trusted_origin_websocket_receives_initial_telemetry() -> None:
    with TestClient(create_app(":memory:")) as client:
        with client.websocket_connect(
            "/ws/telemetry",
            headers={"origin": "http://localhost:5173"},
        ) as websocket:
            frame = websocket.receive_json()

    assert frame["type"] == "telemetry"
