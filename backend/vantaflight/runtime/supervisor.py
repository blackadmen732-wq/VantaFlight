"""RuntimeSupervisor — service lifecycle management for VantaFlight."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .models import ComputeNodeInfo, DeploymentMode, ServiceState


@dataclass
class ServiceInfo:
    name: str
    state: ServiceState = ServiceState.STOPPED
    started_at: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "uptime_s": round(time.monotonic() - self.started_at, 1) if self.started_at > 0 else 0,
            "error": self.error,
        }


class RuntimeSupervisor:
    """Manages service lifecycles and failure isolation.

    Tracks camera, vision, race, recorder, and UI services independently.
    Prevents duplicate starts and enables clean shutdown ordering.
    """

    SERVICE_NAMES = ("camera", "vision", "race", "recorder", "preview", "ui")

    def __init__(self, deployment_mode: DeploymentMode = DeploymentMode.LOCAL_GROUND) -> None:
        self._deployment_mode = deployment_mode
        self._services: dict[str, ServiceInfo] = {
            name: ServiceInfo(name=name) for name in self.SERVICE_NAMES
        }
        self._node = ComputeNodeInfo(deployment_mode=deployment_mode)
        self._start_time = time.monotonic()

    @property
    def deployment_mode(self) -> DeploymentMode:
        return self._deployment_mode

    @property
    def node(self) -> ComputeNodeInfo:
        self._node.uptime_s = time.monotonic() - self._start_time
        self._node.camera_ready = self._services["camera"].state in (ServiceState.READY, ServiceState.RUNNING)
        self._node.vision_ready = self._services["vision"].state in (ServiceState.READY, ServiceState.RUNNING)
        self._node.race_ready = self._services["race"].state in (ServiceState.READY, ServiceState.RUNNING)
        return self._node

    def service_state(self, name: str) -> ServiceState:
        if name not in self._services:
            raise ValueError(f"unknown service: {name}")
        return self._services[name].state

    def set_service_state(self, name: str, state: ServiceState, error: str | None = None) -> None:
        if name not in self._services:
            raise ValueError(f"unknown service: {name}")
        svc = self._services[name]
        svc.state = state
        svc.error = error
        if state == ServiceState.RUNNING and svc.started_at == 0:
            svc.started_at = time.monotonic()
        elif state == ServiceState.STOPPED:
            svc.started_at = 0.0

    def overall_state(self) -> ServiceState:
        """Derive overall runtime state from service states."""
        states = {s.state for s in self._services.values()}
        if ServiceState.FAILED in states:
            return ServiceState.DEGRADED
        if all(s == ServiceState.STOPPED for s in states):
            return ServiceState.STOPPED
        if ServiceState.RUNNING in states:
            if ServiceState.DEGRADED in states:
                return ServiceState.DEGRADED
            return ServiceState.RUNNING
        if ServiceState.STARTING in states:
            return ServiceState.STARTING
        return ServiceState.READY

    def can_start_race(self) -> tuple[bool, str]:
        """Check if we have prerequisites for autonomous racing."""
        camera = self._services["camera"].state
        vision = self._services["vision"].state
        if camera not in (ServiceState.READY, ServiceState.RUNNING):
            return False, "camera not ready"
        if vision not in (ServiceState.READY, ServiceState.RUNNING):
            return False, "vision not ready"
        return True, "ready"

    async def shutdown(self, timeout_s: float = 5.0) -> dict[str, str]:
        """Request ordered shutdown of all services."""
        results: dict[str, str] = {}
        shutdown_order = ["race", "recorder", "preview", "vision", "camera", "ui"]
        for name in shutdown_order:
            svc = self._services.get(name)
            if svc and svc.state != ServiceState.STOPPED:
                svc.state = ServiceState.STOPPING
                svc.state = ServiceState.STOPPED
                svc.started_at = 0.0
                results[name] = "stopped"
            else:
                results[name] = "already_stopped"
        return results

    def preflight_check(self) -> dict:
        """Pre-flight readiness check."""
        checks: dict[str, dict] = {}
        for name, svc in self._services.items():
            passed = svc.state in (ServiceState.READY, ServiceState.RUNNING, ServiceState.STOPPED)
            checks[name] = {
                "state": svc.state.value,
                "passed": passed,
                "error": svc.error,
            }

        overall = all(c["passed"] for c in checks.values())
        return {
            "passed": overall,
            "deployment_mode": self._deployment_mode.value,
            "services": checks,
        }

    def to_dict(self) -> dict:
        return {
            "overall": self.overall_state().value,
            "deployment_mode": self._deployment_mode.value,
            "services": {name: svc.to_dict() for name, svc in self._services.items()},
            "node": self.node.to_dict(),
        }
