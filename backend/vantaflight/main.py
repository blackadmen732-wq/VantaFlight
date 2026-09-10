"""VantaFlight Flight Core HTTP/WebSocket server."""
from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .api_models import (
    CameraProfileModel,
    CourseDetailModel,
    CourseGenerationRequest,
    CourseValidationModel,
    ExperimentResultModel,
    RaceStateModel,
    RunMetricModel,
    SceneStateModel,
    SimulationStateModel,
    TargetEstimateModel,
    VisionStatusModel,
)
from .config import CORS_ORIGINS, DB_PATH, DEFAULT_ADAPTER, STREAM_HZ, SOFTWARE_VERSION
from .connection import ConnectionManager, DiscoveredDrone
from .core import FlightController
from .course_lab import CourseGenerator, CourseValidator, SafeVolume
from .data import AsyncRecorder, FlightDatabase
from .intelligence import IntelligenceRuntime
from .models import AdapterType


class TakeoffRequest(BaseModel):
    target_altitude_m: float = 5.0


class ConnectRequest(BaseModel):
    adapter_type: str = DEFAULT_ADAPTER


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
        await recorder.start()
        app.state.stream_task = asyncio.create_task(stream_loop())
        try:
            yield
        finally:
            task = getattr(app.state, "stream_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await recorder.stop()
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
    recorder = AsyncRecorder(db)
    connection_manager = ConnectionManager()
    controller = FlightController(db, connection_manager=connection_manager)
    hub = ConnectionHub()
    intelligence = IntelligenceRuntime()
    app.state.db = db
    app.state.controller = controller
    app.state.hub = hub
    app.state.recorder = recorder
    app.state.intelligence = intelligence

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
            await hub.broadcast(
                {
                    "type": "vision_state",
                    "data": intelligence.vision_status.model_dump(mode="json"),
                }
            )
            await hub.broadcast(
                {"type": "scene_state", "data": intelligence.scene.model_dump(mode="json")}
            )
            await hub.broadcast(
                {"type": "race_state", "data": intelligence.race.model_dump(mode="json")}
            )
            await hub.broadcast(
                {
                    "type": "simulation_state",
                    "data": intelligence.simulation.model_dump(mode="json"),
                }
            )
            elapsed = time.monotonic() - t_start
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
        results = []
        for d in drones:
            adapter = connection_manager._build_adapter(d)
            caps = adapter.get_capabilities()
            results.append({
                "drone_id": d.drone_id,
                "name": d.name,
                "transport": d.transport.value,
                "address": d.address,
                "adapter_type": d.adapter_type.value,
                "capabilities": caps.supported_capabilities,
            })
        return {"drones": results}

    @app.post("/api/connect")
    async def connect(req: ConnectRequest | None = None) -> dict:
        from .models import CommandResult as CR

        adapter_type = req.adapter_type if req else DEFAULT_ADAPTER
        drones = await connection_manager.discover()

        target = None
        for d in drones:
            if d.adapter_type.value == adapter_type:
                target = d
                break

        if target is None:
            available = [d.adapter_type.value for d in drones]
            return CR(
                command="connect", accepted=False,
                message=f"unknown adapter '{adapter_type}'; available: {available}",
            ).model_dump(mode="json")

        return (await controller.connect(target)).model_dump(mode="json")

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

    # -- V0.5 backend-intelligence contracts -------------------------------
    @app.get("/api/vision/status", response_model=VisionStatusModel)
    async def vision_status() -> VisionStatusModel:
        return intelligence.vision_status

    @app.get("/api/vision/camera-profiles", response_model=list[CameraProfileModel])
    async def camera_profiles() -> list[CameraProfileModel]:
        return list(intelligence.camera_profiles.values())

    @app.get("/api/vision/tracks", response_model=list[TargetEstimateModel])
    async def vision_tracks() -> list[TargetEstimateModel]:
        return list(intelligence.tracks.values())

    @app.get("/api/scene", response_model=SceneStateModel)
    async def scene_state() -> SceneStateModel:
        return intelligence.scene

    @app.get("/api/race", response_model=RaceStateModel)
    async def race_state() -> RaceStateModel:
        return intelligence.race

    @app.get("/api/simulation/status", response_model=SimulationStateModel)
    async def simulation_status() -> SimulationStateModel:
        return intelligence.simulation

    @app.get("/api/run-metrics", response_model=list[RunMetricModel])
    async def run_metrics() -> list[RunMetricModel]:
        return list(intelligence.run_metrics)

    @app.get("/api/experiments", response_model=list[ExperimentResultModel])
    async def experiment_results() -> list[ExperimentResultModel]:
        return list(intelligence.experiments)

    @app.post("/api/courses/generate", response_model=CourseDetailModel)
    async def generate_course(req: CourseGenerationRequest) -> CourseDetailModel:
        try:
            volume = SafeVolume(
                dimensions=(req.width, req.length, req.height),
                floor=req.floor,
                ceiling=req.ceiling,
                boundary_margin=req.boundary_margin,
            )
            course = CourseGenerator(volume).generate(
                req.mode, seed=req.seed, gate_count=req.gate_count
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        payload = course.to_dict()
        course_id = f"{course.mode.value.lower()}-{course.seed}-{len(course.gates)}"
        detail = CourseDetailModel(
            id=course_id,
            seed=course.seed,
            mode=course.mode.value,
            safe_volume=payload["volume"],
            path=payload["path"],
            gates=payload["gates"],
            difficulty=dict(course.difficulty),
        )
        intelligence.courses[course_id] = detail
        db.save_course(detail.model_dump(mode="json"))
        return detail

    @app.get("/api/courses/{course_id}", response_model=CourseDetailModel)
    async def course_details(course_id: str) -> CourseDetailModel:
        result = intelligence.courses.get(course_id)
        if result is None:
            stored = db.get_course(course_id)
            if stored is None:
                raise HTTPException(status_code=404, detail="course not found")
            result = CourseDetailModel.model_validate(stored)
            intelligence.courses[course_id] = result
        return result

    @app.get(
        "/api/courses/{course_id}/validation", response_model=CourseValidationModel
    )
    async def validate_course(course_id: str) -> CourseValidationModel:
        result = intelligence.courses.get(course_id)
        if result is None:
            stored = db.get_course(course_id)
            if stored is None:
                raise HTTPException(status_code=404, detail="course not found")
            result = CourseDetailModel.model_validate(stored)
            intelligence.courses[course_id] = result
        from .course_lab import Course

        payload = result.model_dump(mode="json")
        course = Course.from_dict(
            {
                "mode": payload["mode"],
                "seed": payload["seed"],
                "volume": payload["safe_volume"],
                "path": payload["path"],
                "gates": payload["gates"],
                "difficulty": payload["difficulty"],
            }
        )
        report = CourseValidator().validate(course)
        return CourseValidationModel(
            course_id=course_id,
            valid=report.valid,
            errors=list(report.errors),
        )

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
