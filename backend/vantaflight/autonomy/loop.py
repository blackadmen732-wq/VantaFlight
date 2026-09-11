"""AutonomyLoop — camera → VantaSight → scene → VantaRace → VantaExecution."""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from ..racing.execution import SimulationExecutionPermit, SimulationOnlyExecutionGuard
from ..racing.models import AircraftState, DesiredTrajectoryState, GateTarget, TargetSlot
from ..racing.planner import VantaRace, VantaRaceConfig
from ..racing.vanta_execution import VantaExecution
from ..runtime.latency import LatencySnapshot
from ..simulation.runner import SimulationRunner
from ..simulation.truth import AircraftTruth
from .gate_progression import GateProgressionTracker, GatePassEvent

logger = logging.getLogger(__name__)


class AutonomyState(str, enum.Enum):
    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class AutonomyConfig:
    loop_rate_hz: float = 50.0
    confidence_floor: float = 0.15
    max_setpoint_age_s: float = 0.5
    recovery_hold_s: float = 3.0
    max_consecutive_failures: int = 10
    race_config: VantaRaceConfig = field(default_factory=VantaRaceConfig)


@dataclass
class AutonomyMetrics:
    loop_iterations: int = 0
    vision_results: int = 0
    plans_generated: int = 0
    setpoints_sent: int = 0
    gate_passes: int = 0
    recovery_events: int = 0
    failures: int = 0
    avg_loop_ms: float = 0.0
    state: AutonomyState = AutonomyState.IDLE

    def to_dict(self) -> dict:
        return {
            "loop_iterations": self.loop_iterations,
            "vision_results": self.vision_results,
            "plans_generated": self.plans_generated,
            "setpoints_sent": self.setpoints_sent,
            "gate_passes": self.gate_passes,
            "recovery_events": self.recovery_events,
            "failures": self.failures,
            "avg_loop_ms": round(self.avg_loop_ms, 2),
            "state": self.state.value,
        }


class _SimpleScene:
    """Minimal RacingScene implementation from gate targets."""

    def __init__(
        self,
        current: GateTarget | None = None,
        next_gate: GateTarget | None = None,
        future: GateTarget | None = None,
    ) -> None:
        self.CURRENT = current
        self.NEXT = next_gate
        self.FUTURE = future


class AutonomyLoop:
    """Orchestrates the closed-loop autonomy pipeline.

    This is the integration point that wires together:
    1. Camera/SimulatedCamera → FramePacket
    2. VisionPipeline → gate detections
    3. Scene assembly → RacingScene with gate targets
    4. VantaRace → DesiredTrajectoryState
    5. VantaExecution → OffboardSetpoint
    6. PX4 adapter → offboard commands (SITL only)

    The loop runs at a configurable rate and handles recovery
    when vision or planning fails.
    """

    def __init__(
        self,
        config: AutonomyConfig | None = None,
        permit: SimulationExecutionPermit | None = None,
    ) -> None:
        self._config = config or AutonomyConfig()
        self._state = AutonomyState.IDLE
        self._planner = VantaRace(self._config.race_config)
        self._execution = VantaExecution(
            confidence_floor=self._config.confidence_floor,
            max_setpoint_age_s=self._config.max_setpoint_age_s,
        )
        self._gate_tracker = GateProgressionTracker()
        self._permit = permit
        self._metrics = AutonomyMetrics()
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._consecutive_failures = 0
        self._loop_times: list[float] = []
        self._last_aircraft_pos: np.ndarray | None = None
        self._on_gate_pass: list = []
        self._on_setpoint: list = []

    @property
    def state(self) -> AutonomyState:
        return self._state

    @property
    def metrics(self) -> AutonomyMetrics:
        self._metrics.state = self._state
        if self._loop_times:
            self._metrics.avg_loop_ms = (
                sum(self._loop_times[-100:]) / len(self._loop_times[-100:]) * 1000.0
            )
        return self._metrics

    @property
    def gate_tracker(self) -> GateProgressionTracker:
        return self._gate_tracker

    @property
    def execution(self) -> VantaExecution:
        return self._execution

    @property
    def planner(self) -> VantaRace:
        return self._planner

    def initialize(
        self,
        permit: SimulationExecutionPermit,
        total_gates: int,
    ) -> None:
        SimulationOnlyExecutionGuard.validate_permit(permit)
        self._permit = permit
        self._execution.activate(permit)
        self._gate_tracker.start_race(total_gates)
        self._state = AutonomyState.INITIALIZING
        self._consecutive_failures = 0
        logger.info("AutonomyLoop initialized with %d gates", total_gates)

    def process_tick(
        self,
        aircraft_truth: AircraftTruth,
        gate_targets: list[GateTarget] | None = None,
        sim_runner: SimulationRunner | None = None,
    ) -> DesiredTrajectoryState | None:
        """Process one tick of the autonomy loop synchronously.

        Returns the desired trajectory state, or None if planning failed.
        This method is designed to be called from an async context at the
        configured loop rate.
        """
        if self._state in (AutonomyState.IDLE, AutonomyState.PAUSED, AutonomyState.COMPLETE, AutonomyState.FAILED):
            return None

        tick_start = time.monotonic()

        if self._state == AutonomyState.INITIALIZING:
            self._state = AutonomyState.RUNNING

        aircraft = AircraftState(
            position=aircraft_truth.position,
            velocity=aircraft_truth.velocity,
            acceleration=aircraft_truth.acceleration,
            yaw=aircraft_truth.yaw_deg,
            timestamp=aircraft_truth.timestamp,
        )

        if self._last_aircraft_pos is not None and sim_runner and sim_runner.truth:
            event = self._gate_tracker.check_crossing(
                sim_runner.truth,
                aircraft_truth.position,
                self._last_aircraft_pos,
            )
            if event is not None:
                self._metrics.gate_passes += 1
                for callback in self._on_gate_pass:
                    callback(event)
                if self._gate_tracker.is_complete:
                    self._state = AutonomyState.COMPLETE
                    logger.info("Race complete! All gates passed.")
                    return None
        self._last_aircraft_pos = aircraft_truth.position.copy()

        scene = self._build_scene(gate_targets, self._gate_tracker.current_gate_index)

        try:
            desired = self._planner.plan(
                aircraft, scene,
                executable=True,
                permit=self._permit,
            )
            self._metrics.plans_generated += 1
            self._consecutive_failures = 0

            if self._state == AutonomyState.RECOVERING:
                self._state = AutonomyState.RUNNING
                self._metrics.recovery_events += 1

        except Exception as e:
            logger.warning("Planning failed: %s", e)
            self._consecutive_failures += 1
            self._metrics.failures += 1
            if self._consecutive_failures >= self._config.max_consecutive_failures:
                self._state = AutonomyState.FAILED
                logger.error("Too many consecutive failures, autonomy FAILED")
            elif self._state == AutonomyState.RUNNING:
                self._state = AutonomyState.RECOVERING
            return None

        setpoint = self._execution.translate(desired)
        if setpoint is not None:
            self._metrics.setpoints_sent += 1
            for callback in self._on_setpoint:
                callback(setpoint)
            self._execution.update_hold_position(aircraft_truth.position)

        self._metrics.loop_iterations += 1
        elapsed = time.monotonic() - tick_start
        self._loop_times.append(elapsed)
        if len(self._loop_times) > 200:
            self._loop_times = self._loop_times[-100:]

        return desired

    def stop(self) -> None:
        self._running = False
        self._execution.deactivate()
        if self._state not in (AutonomyState.COMPLETE, AutonomyState.FAILED):
            self._state = AutonomyState.IDLE
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()

    def pause(self) -> None:
        if self._state == AutonomyState.RUNNING:
            self._state = AutonomyState.PAUSED

    def resume(self) -> None:
        if self._state == AutonomyState.PAUSED:
            self._state = AutonomyState.RUNNING

    def on_gate_pass(self, callback) -> None:
        self._on_gate_pass.append(callback)

    def on_setpoint(self, callback) -> None:
        self._on_setpoint.append(callback)

    @staticmethod
    def _build_scene(gate_targets: list[GateTarget] | None, current_gate_index: int = 0) -> _SimpleScene:
        if not gate_targets:
            return _SimpleScene()
        remaining = gate_targets[current_gate_index:]
        current = remaining[0] if len(remaining) > 0 else None
        next_g = remaining[1] if len(remaining) > 1 else None
        future = remaining[2] if len(remaining) > 2 else None
        return _SimpleScene(current=current, next_gate=next_g, future=future)

    def reset(self) -> None:
        self._state = AutonomyState.IDLE
        self._metrics = AutonomyMetrics()
        self._loop_times.clear()
        self._last_aircraft_pos = None
        self._consecutive_failures = 0
        self._gate_tracker.reset()
        self._execution.deactivate()

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "metrics": self.metrics.to_dict(),
            "gate_progression": self._gate_tracker.to_dict(),
            "execution": self._execution.to_dict(),
            "planner_state": self._planner.state.value,
        }
