"""VantaFlight Flight Core HTTP/WebSocket server."""
from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import CORS_ORIGINS, DB_PATH, STREAM_HZ, SOFTWARE_VERSION
from .connection import ConnectionManager, DiscoveredDrone
from .core import FlightController
from .data import FlightDatabase
from .models import AdapterType


class TakeoffRequest(BaseModel):
    target_altitude_m: float = 5.0


class ConnectRequest(BaseModel):
    adapter_type: str = "mock"


class ConnectionHub:
    """Tracks connected WebSocket clients and broadcasts JSON frames."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    @property
    def count(self) -> int:
        return len(self._clients)


def create_app(db_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.stream_task = asyncio.create_task(stream_loop())
        try:
            yield
        finally:
            task = getattr(app.state, "stream_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            db.close()

    app = FastAPI(
        title="VantaFlight Flight Core",
        version=SOFTWARE_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    db = FlightDatabase(db_path or DB_PATH)
    connection_manager = ConnectionManager()
    controller = FlightController(db, connection_manager=connection_manager)
    hub = ConnectionHub()
    app.state.db = db
    app.state.controller = controller
    app.state.hub = hub

    async def stream_loop() -> None:
        period = 1.0 / STREAM_HZ
        while True:
            t_start = time.monotonic()
            telemetry = controller.sample()
            await hub.broadcast({"type": "telemetry", "data": telemetry.model_dump(mode="json")})
            for event in controller.drain_events():
                await hub.broadcast({"type": "event", "data": event.model_dump(mode="json")})
            if controller.twin.active:
                await hub.broadcast({"type": "twin", "data": controller.twin.state.to_dict()})
            elapsed = time.monotonic() - t_start
            ws_delay = elapsed * 1000
            controller.metrics.record_telemetry_interval(elapsed)
            await asyncio.sleep(max(0, period - elapsed))

    # -- REST ---------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "version": SOFTWARE_VERSION,
            "clients": hub.count,
            "session_state": controller.session_state.value,
        }

    @app.get("/api/discover")
    async def discover() -> dict:
        drones = await connection_manager.discover()
        return {
            "drones": [
                {
                    "drone_id": d.drone_id,
                    "name": d.name,
                    "transport": d.transport.value,
                    "address": d.address,
                    "adapter_type": d.adapter_type.value,
                }
                for d in drones
            ]
        }

    @app.post("/api/connect")
    async def connect(req: ConnectRequest | None = None) -> dict:
        adapter_type = req.adapter_type if req else "mock"
        drones = await connection_manager.discover()

        target = None
        for d in drones:
            if d.adapter_type.value == adapter_type:
                target = d
                break

        if target is None:
            target = drones[0] if drones else None

        if target is None:
            return {"command": "connect", "accepted": False, "message": "no drones available"}

        old_connect = controller.connect

        async def connect_to_target():
            if connection_manager.adapter is not None and connection_manager.adapter.connected:
                from .models import CommandResult
                return CommandResult(command="connect", accepted=False, message="already connected")

            adapter = await connection_manager.connect(target)
            caps = adapter.get_capabilities()
            drone_id = target.drone_id
            controller._flight_id = db.start_flight(
                drone_id, caps.name,
                adapter_type=caps.adapter_type,
                is_simulated=caps.is_simulated,
                connection_type=target.transport.value,
                software_version=SOFTWARE_VERSION,
            )
            from .core.flight_controller import SessionState
            controller._session_state = SessionState.CONNECTED
            controller._connection_loss_logged = False
            telemetry = adapter.get_telemetry()
            controller._twin.start(battery=telemetry.battery_percentage)
            controller._log_event("connected", f"connected to {caps.name}")
            from .models import CommandResult
            return CommandResult(command="connect", accepted=True, message=f"connected to {caps.name}")

        result = await connect_to_target()
        return result.model_dump(mode="json")

    @app.post("/api/disconnect")
    async def disconnect() -> dict:
        return (await controller.disconnect()).model_dump(mode="json")

    @app.post("/api/arm")
    async def arm() -> dict:
        return (await controller.command("arm")).model_dump(mode="json")

    @app.post("/api/disarm")
    async def disarm() -> dict:
        return (await controller.command("disarm")).model_dump(mode="json")

    @app.post("/api/takeoff")
    async def takeoff(req: TakeoffRequest | None = None) -> dict:
        target = req.target_altitude_m if req else 5.0
        return (await controller.command("takeoff", target_altitude_m=target)).model_dump(mode="json")

    @app.post("/api/hold")
    async def hold() -> dict:
        return (await controller.command("hold")).model_dump(mode="json")

    @app.post("/api/land")
    async def land() -> dict:
        return (await controller.command("land")).model_dump(mode="json")

    @app.get("/api/telemetry")
    async def telemetry() -> dict:
        return controller.get_telemetry().model_dump(mode="json")

    @app.get("/api/capabilities")
    async def capabilities() -> dict:
        adapter = connection_manager.adapter
        if adapter is None:
            return {"connected": False, "capabilities": None}
        return {
            "connected": True,
            "capabilities": adapter.get_capabilities().model_dump(mode="json"),
        }

    @app.get("/api/twin")
    async def twin_state() -> dict:
        return controller.twin.state.to_dict()

    @app.get("/api/run-summary")
    async def run_summary() -> dict:
        fid = controller.flight_id
        if fid is None:
            twin_summary = controller.twin.summary.to_dict()
            return {"source": "twin", "summary": twin_summary}
        db_summary = db.get_run_summary(fid)
        return {"source": "database", "summary": db_summary}

    @app.get("/api/diagnostics")
    async def diagnostics() -> dict:
        return {
            "version": SOFTWARE_VERSION,
            "session_state": controller.session_state.value,
            "flight_id": controller.flight_id,
            "ws_clients": hub.count,
            "adapter": connection_manager.active.name if connection_manager.active else None,
            "metrics": controller.metrics.to_dict(),
            "twin_active": controller.twin.active,
        }

    # -- WebSocket ----------------------------------------------------------
    @app.websocket("/ws/telemetry")
    async def telemetry_ws(ws: WebSocket) -> None:
        await hub.register(ws)
        try:
            await ws.send_json(
                {"type": "telemetry", "data": controller.get_telemetry().model_dump(mode="json")}
            )
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.unregister(ws)
        except Exception:
            hub.unregister(ws)

    return app


app = create_app()
