"""SimulationRunner — orchestrates generate → load → run → record → analyze."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np

from .models import SimSessionConfig, SimSessionState, SimulationWorld
from .truth import AircraftTruth, SimulationTruth
from .faults import FaultInjector
from .course_bridge import CourseBridge
from .kinematic import KinematicDriver


@dataclass
class SimSessionResult:
    session_id: str
    course_id: str
    state: SimSessionState
    gates_passed: int = 0
    total_gates: int = 0
    race_time_s: float = 0.0
    complete: bool = False
    faults_injected: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "course_id": self.course_id,
            "state": self.state.value,
            "gates_passed": self.gates_passed,
            "total_gates": self.total_gates,
            "race_time_s": round(self.race_time_s, 3),
            "complete": self.complete,
            "faults_injected": self.faults_injected,
            "error": self.error,
        }


class SimulationRunner:
    """Manages simulation session lifecycle.

    Does NOT launch an actual Gazebo process — that requires PX4 SITL
    which may not be available. Instead, it manages session state,
    world configuration, truth tracking, and fault injection so that
    higher-level code (the autonomous loop or training engine) can
    drive simulation runs against the synthetic or Gazebo camera.
    """

    def __init__(self, aircraft_speed: float = 5.0) -> None:
        self._session_counter = 0
        self._state = SimSessionState.IDLE
        self._config: SimSessionConfig | None = None
        self._world: SimulationWorld | None = None
        self._truth: SimulationTruth | None = None
        self._fault_injector: FaultInjector | None = None
        self._session_id: str | None = None
        self._start_time: float = 0.0
        self._results: list[SimSessionResult] = []
        self._bridge = CourseBridge()
        self._aircraft_speed = aircraft_speed
        self._kinematic = KinematicDriver(
            max_speed=aircraft_speed,
            max_accel=aircraft_speed * 0.6,
        )
        self._aircraft_pos: np.ndarray = np.zeros(3)
        self._aircraft_vel: np.ndarray = np.zeros(3)
        self._next_gate_idx: int = 0
        self._gates_ordered: list = []
        self._prev_sim_time: float = 0.0

    @property
    def state(self) -> SimSessionState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def world(self) -> SimulationWorld | None:
        return self._world

    @property
    def truth(self) -> SimulationTruth | None:
        return self._truth

    @property
    def fault_injector(self) -> FaultInjector | None:
        return self._fault_injector

    def prepare(self, config: SimSessionConfig, world: SimulationWorld) -> str:
        if self._state not in (SimSessionState.IDLE, SimSessionState.COMPLETE, SimSessionState.FAILED):
            raise RuntimeError(f"cannot prepare in state {self._state.value}")

        self._session_counter += 1
        self._session_id = f"sim_{self._session_counter:04d}"
        self._config = config
        self._world = world
        self._truth = SimulationTruth(world)
        self._fault_injector = FaultInjector(config.faults) if config.faults else FaultInjector()
        self._aircraft_pos = world.start_position.copy()
        self._aircraft_vel = np.zeros(3)
        self._next_gate_idx = 0
        self._gates_ordered = sorted(world.gates, key=lambda g: g.order)
        self._prev_sim_time = 0.0
        self._state = SimSessionState.PREPARING
        self._state = SimSessionState.READY
        return self._session_id

    def start(self) -> None:
        if self._state != SimSessionState.READY:
            raise RuntimeError(f"cannot start in state {self._state.value}")
        self._state = SimSessionState.RUNNING
        self._start_time = time.monotonic()

    def pause(self) -> None:
        if self._state != SimSessionState.RUNNING:
            raise RuntimeError(f"cannot pause in state {self._state.value}")
        self._state = SimSessionState.PAUSED

    def resume(self) -> None:
        if self._state != SimSessionState.PAUSED:
            raise RuntimeError(f"cannot resume in state {self._state.value}")
        self._state = SimSessionState.RUNNING

    def stop(self, error: str | None = None) -> SimSessionResult:
        if self._state in (SimSessionState.COMPLETE, SimSessionState.FAILED):
            return self._results[-1] if self._results else SimSessionResult(
                session_id=self._session_id or "unknown",
                course_id=self._world.course_id if self._world else "unknown",
                state=self._state,
            )
        final_state = SimSessionState.FAILED if error else SimSessionState.COMPLETE
        self._state = final_state

        summary = self._truth.race_summary() if self._truth else {}
        result = SimSessionResult(
            session_id=self._session_id or "unknown",
            course_id=self._world.course_id if self._world else "unknown",
            state=final_state,
            gates_passed=summary.get("gates_passed", 0),
            total_gates=summary.get("total_gates", 0),
            race_time_s=summary.get("race_time_s", 0.0),
            complete=summary.get("complete", False),
            faults_injected=len(self._fault_injector.history) if self._fault_injector else 0,
            error=error,
        )
        self._results.append(result)
        return result

    def reset(self) -> None:
        self._state = SimSessionState.IDLE
        self._config = None
        self._world = None
        self._truth = None
        self._fault_injector = None
        self._session_id = None
        self._gates_ordered = []

    def tick(self, sim_time: float) -> None:
        if self._state != SimSessionState.RUNNING:
            return

        dt = sim_time - self._prev_sim_time
        self._prev_sim_time = sim_time
        if dt <= 0:
            return

        if not self._world or not self._truth:
            return

        prev_pos = self._aircraft_pos.copy()
        prev_vel = self._aircraft_vel.copy()

        if self._next_gate_idx < len(self._gates_ordered):
            target = self._gates_ordered[self._next_gate_idx].position
            desired_vel = self._kinematic.navigate_toward(
                self._aircraft_pos, target, self._aircraft_speed,
            )
        else:
            desired_vel = np.zeros(3)

        self._aircraft_pos, self._aircraft_vel = self._kinematic.step(
            self._aircraft_pos, self._aircraft_vel, desired_vel, dt,
        )

        truth_state = self._kinematic.build_truth(
            sim_time, self._aircraft_pos, self._aircraft_vel, prev_vel, dt,
        )
        self._truth.update_aircraft(truth_state)

        crossed = self._truth.check_gate_crossing(self._aircraft_pos, prev_pos)
        if crossed is not None and self._next_gate_idx < len(self._gates_ordered):
            if crossed == self._gates_ordered[self._next_gate_idx].gate_id:
                self._next_gate_idx += 1
                if self._next_gate_idx >= len(self._gates_ordered):
                    self.stop()
                    return

        if self._fault_injector:
            self._fault_injector.tick(sim_time)

        if self._config and sim_time > self._config.max_time_s:
            self.stop(error="max_time_exceeded")

    @property
    def elapsed_s(self) -> float:
        if self._start_time <= 0:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def results(self) -> list[SimSessionResult]:
        return list(self._results)

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "session_id": self._session_id,
            "course_id": self._world.course_id if self._world else None,
            "elapsed_s": round(self.elapsed_s, 3),
            "truth": self._truth.truth.to_dict() if self._truth else None,
            "faults": self._fault_injector.to_dict() if self._fault_injector else None,
            "completed_sessions": len(self._results),
        }
