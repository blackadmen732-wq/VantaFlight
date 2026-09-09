"""VantaFlight Flight Core HTTP/WebSocket server.

Exposes the drone-agnostic flight core over a local API:
- REST endpoints issue commands (connect, arm, takeoff, hold, land, ...).
- A WebSocket streams normalized telemetry and timeline events to the UI.

Everything runs locally; there are no cloud or internet dependencies.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .core import FlightController
from .data import FlightDatabase

STREAM_HZ = float(os.environ.get("VANTAFLIGHT_STREAM_HZ", "10"))
DB_PATH = os.environ.get("VANTAFLIGHT_DB", str(Path.cwd() / "vantaflight.db"))


class TakeoffRequest(BaseModel):
    target_altitude_m: float = 5.0


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

    app = FastAPI(title="VantaFlight Flight Core", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    db = FlightDatabase(db_path or DB_PATH)
    controller = FlightController(db)
    hub = ConnectionHub()
    app.state.db = db
    app.state.controller = controller
    app.state.hub = hub

    async def stream_loop() -> None:
        period = 1.0 / STREAM_HZ
        while True:
            telemetry = controller.sample()
            await hub.broadcast({"type": "telemetry", "data": telemetry.model_dump(mode="json")})
            for event in controller.drain_events():
                await hub.broadcast({"type": "event", "data": event.model_dump(mode="json")})
            await asyncio.sleep(period)

    # -- REST ---------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "clients": hub.count}

    @app.get("/api/discover")
    async def discover() -> dict:
        drones = await controller._connections.discover()  # noqa: SLF001
        return {
            "drones": [
                {
                    "drone_id": d.drone_id,
                    "name": d.name,
                    "transport": d.transport.value,
                    "address": d.address,
                }
                for d in drones
            ]
        }

    @app.post("/api/connect")
    async def connect() -> dict:
        return (await controller.connect()).model_dump(mode="json")

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

    # -- WebSocket ----------------------------------------------------------
    @app.websocket("/ws/telemetry")
    async def telemetry_ws(ws: WebSocket) -> None:
        await hub.register(ws)
        try:
            await ws.send_json(
                {"type": "telemetry", "data": controller.get_telemetry().model_dump(mode="json")}
            )
            while True:
                # Keep the socket open; the stream loop pushes frames. We still
                # await client messages so disconnects are detected promptly.
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.unregister(ws)
        except Exception:
            hub.unregister(ws)

    return app


app = create_app()
