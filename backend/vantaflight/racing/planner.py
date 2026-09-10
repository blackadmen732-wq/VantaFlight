"""Unified perception-to-trajectory racing planner."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .execution import SimulationExecutionPermit, SimulationOnlyExecutionGuard
from .models import AircraftState, DesiredTrajectoryState, GateTarget, RacingScene, TargetSlot
from .speed import SpeedEnvelopeConfig, speed_envelope
from .state_machine import RaceEvent, RaceState, RaceStateMachine
from .trajectory import CubicHermiteTrajectory, LookaheadConfig, trajectory_through_gates


@dataclass(frozen=True)
class VantaRaceConfig:
    aggression: float = 0.5
    nominal_speed: float = 10.0
    prediction_grace_s: float = 0.35
    command_lookahead_s: float = 0.1
    trajectory_samples: int = 33
    acquire_confidence: float = 0.35
    lock_confidence: float = 0.6
    lookahead: LookaheadConfig = LookaheadConfig()
    speed: SpeedEnvelopeConfig = SpeedEnvelopeConfig()

    def __post_init__(self) -> None:
        if not 0.0 <= self.aggression <= 1.0:
            raise ValueError("aggression must be between zero and one")
        if self.nominal_speed <= 0.0:
            raise ValueError("nominal_speed must be positive")
        if self.prediction_grace_s < 0.0 or self.command_lookahead_s < 0.0:
            raise ValueError("planner time horizons cannot be negative")
        if self.trajectory_samples < 5:
            raise ValueError("trajectory_samples must be at least five")
        if not 0.0 <= self.acquire_confidence <= self.lock_confidence <= 1.0:
            raise ValueError("confidence thresholds are invalid")


class VantaRace:
    """Deterministic gate planner with no transport or actuator access."""

    def __init__(self, config: VantaRaceConfig = VantaRaceConfig()) -> None:
        self.config = config
        self.state_machine = RaceStateMachine()
        self._aggression = config.aggression
        self._last_aggression_scale = config.aggression
        self._last_seen_at: float | None = None
        self._had_target = False
        self._trajectory: CubicHermiteTrajectory | None = None
        self._sequence = 0
        self.last_plan_executable = False
        self.last_speed_profile = np.empty(0, dtype=np.float64)
        self.last_sample_distances = np.empty(0, dtype=np.float64)

    @property
    def state(self) -> RaceState:
        return self.state_machine.state

    @property
    def aggression(self) -> float:
        return self._aggression

    @aggression.setter
    def aggression(self, value: float) -> None:
        value = float(value)
        if not np.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("aggression must be finite and between zero and one")
        self._aggression = value

    @property
    def aggression_scale(self) -> float:
        return self._last_aggression_scale

    def plan(
        self,
        aircraft: AircraftState,
        scene: RacingScene,
        *,
        executable: bool = False,
        permit: SimulationExecutionPermit | None = None,
        mark_executable: bool | None = None,
    ) -> DesiredTrajectoryState:
        """Plan one setpoint; executable plans require a guard-issued permit."""
        if mark_executable is not None:
            if executable and executable != mark_executable:
                raise ValueError("conflicting executable flags")
            executable = mark_executable
        self.last_plan_executable = False
        if executable:
            SimulationOnlyExecutionGuard.validate_permit(permit)
            self.last_plan_executable = True

        if self.state is RaceState.IDLE:
            self.state_machine.transition(RaceEvent.START)

        gates = self._scene_gates(scene)
        if gates:
            self._last_seen_at = aircraft.timestamp
            self._had_target = True
            self._advance_state(aircraft, gates[0])
            self._trajectory = self._build_retimed_trajectory(aircraft, gates)
            return self._trajectory.evaluate(
                min(
                    aircraft.timestamp + self.config.command_lookahead_s,
                    self._trajectory.times[-1],
                )
            )

        elapsed = (
            np.inf
            if self._last_seen_at is None
            else max(0.0, aircraft.timestamp - self._last_seen_at)
        )
        if self._trajectory is not None and elapsed <= self.config.prediction_grace_s:
            prediction_confidence = self._trajectory.planner_confidence * (
                1.0 - elapsed / max(self.config.prediction_grace_s, 1e-12)
            )
            predicted = self._trajectory.evaluate(
                min(
                    aircraft.timestamp + self.config.command_lookahead_s,
                    self._trajectory.times[-1],
                )
            )
            return replace(predicted, planner_confidence=max(0.0, prediction_confidence))

        if self._had_target and self.state not in {RaceState.RECOVER, RaceState.COMPLETE}:
            self.state_machine.transition(RaceEvent.TARGET_LOST)
        self._trajectory = self._build_recovery_trajectory(aircraft)
        return self._trajectory.evaluate(
            min(
                aircraft.timestamp + self.config.command_lookahead_s,
                self._trajectory.times[-1],
            )
        )

    def plan_executable(
        self,
        aircraft: AircraftState,
        scene: RacingScene,
        permit: SimulationExecutionPermit,
    ) -> DesiredTrajectoryState:
        return self.plan(aircraft, scene, executable=True, permit=permit)

    @staticmethod
    def _scene_value(scene: RacingScene, name: str):
        if hasattr(scene, name):
            return getattr(scene, name)
        return getattr(scene, name.lower(), None)

    def _scene_gates(self, scene: RacingScene) -> list[GateTarget]:
        gates: list[GateTarget] = []
        for slot in TargetSlot:
            target = self._scene_value(scene, slot.value)
            if target is not None:
                gates.append(
                    target
                    if isinstance(target, GateTarget) and target.slot is slot
                    else GateTarget.from_scene(target, slot)
                )
        return gates

    def _advance_state(self, aircraft: AircraftState, current: GateTarget) -> None:
        confidence = current.confidence
        event: RaceEvent | None = None
        if self.state is RaceState.RECOVER:
            event = RaceEvent.RECOVERED
        elif self.state is RaceState.SEARCH:
            event = RaceEvent.TARGET_SEEN
        elif self.state is RaceState.ACQUIRE and confidence >= self.config.acquire_confidence:
            event = RaceEvent.TARGET_ACQUIRED
        elif self.state is RaceState.LOCK and confidence >= self.config.lock_confidence:
            event = RaceEvent.TARGET_LOCKED
        elif self.state is RaceState.ALIGN:
            event = RaceEvent.ALIGNED
        elif self.state is RaceState.ACCELERATE:
            event = RaceEvent.AT_SPEED
        elif self.state is RaceState.PASS:
            signed_distance = float(np.dot(aircraft.position - current.position, current.normal))
            if signed_distance >= 0.0:
                event = RaceEvent.GATE_PASSED
        elif self.state is RaceState.NEXT:
            event = RaceEvent.ADVANCE
        if event is not None:
            self.state_machine.transition(event)

    def _next_trajectory_id(self) -> str:
        self._sequence += 1
        return f"vantarace-{self._sequence:06d}"

    def _planner_confidence(self, aircraft: AircraftState, gates: list[GateTarget]) -> float:
        vision = min(gate.confidence for gate in gates)
        uncertainty = aircraft.position_uncertainty + max(gate.uncertainty for gate in gates)
        return float(np.clip(vision * np.exp(-uncertainty), 0.0, 1.0))

    def _build_retimed_trajectory(
        self,
        aircraft: AircraftState,
        gates: list[GateTarget],
    ) -> CubicHermiteTrajectory:
        confidence = self._planner_confidence(aircraft, gates)
        effective_aggression = self._aggression * (0.25 + 0.75 * confidence)
        self._last_aggression_scale = effective_aggression
        geometric = trajectory_through_gates(
            aircraft.position,
            gates,
            speed=self.config.nominal_speed,
            start_time=aircraft.timestamp,
            lookahead=self.config.lookahead,
            trajectory_id=self._next_trajectory_id(),
        )
        sample_times = np.linspace(
            geometric.times[0],
            geometric.times[-1],
            self.config.trajectory_samples,
        )
        positions = np.vstack([geometric.evaluate(time).position for time in sample_times])
        segment_lengths = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        keep = np.concatenate(([True], segment_lengths > 1e-7))
        positions = positions[keep]
        if len(positions) < 2:
            positions = np.vstack((aircraft.position, aircraft.position + gates[0].normal * 1e-3))

        distances = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(positions, axis=0), axis=1))))
        derivatives = np.gradient(positions, distances, axis=0, edge_order=1)
        second = np.gradient(derivatives, distances, axis=0, edge_order=1)
        curvature = np.linalg.norm(np.cross(derivatives, second), axis=1) / np.maximum(
            np.linalg.norm(derivatives, axis=1) ** 3,
            1e-9,
        )
        vertical_gradient = np.gradient(positions[:, 2], distances, edge_order=1)
        horizon = np.linspace(0.0, 1.0, len(gates))
        sample_horizon = np.linspace(0.0, 1.0, len(positions))
        clearance = np.interp(sample_horizon, horizon, [gate.clearance for gate in gates])
        target_confidence = np.interp(
            sample_horizon,
            horizon,
            [gate.confidence for gate in gates],
        )
        uncertainty = np.interp(
            sample_horizon,
            horizon,
            [gate.uncertainty + aircraft.position_uncertainty for gate in gates],
        )
        envelope_config = replace(
            self.config.speed,
            aggression=effective_aggression,
        )
        speeds = speed_envelope(
            distances,
            curvature,
            vertical_gradient,
            clearance,
            target_confidence,
            uncertainty,
            initial_speed=float(np.linalg.norm(aircraft.velocity)),
            config=envelope_config,
        )
        average_speed = np.maximum((speeds[:-1] + speeds[1:]) * 0.5, 1e-3)
        durations = np.diff(distances) / average_speed
        times = aircraft.timestamp + np.concatenate(([0.0], np.cumsum(durations)))
        self.last_speed_profile = speeds.copy()
        self.last_sample_distances = distances.copy()
        return CubicHermiteTrajectory(
            times,
            positions,
            trajectory_id=geometric.trajectory_id,
            planner_confidence=confidence,
        )

    def _build_recovery_trajectory(self, aircraft: AircraftState) -> CubicHermiteTrajectory:
        duration = max(self.config.command_lookahead_s, 0.25)
        stop = aircraft.position + aircraft.velocity * duration * 0.5
        self.last_speed_profile = np.array([np.linalg.norm(aircraft.velocity), 0.0])
        self.last_sample_distances = np.array(
            [0.0, max(float(np.linalg.norm(stop - aircraft.position)), 1e-6)]
        )
        return CubicHermiteTrajectory(
            [aircraft.timestamp, aircraft.timestamp + duration],
            [aircraft.position, stop],
            [aircraft.velocity, np.zeros(3)],
            trajectory_id=self._next_trajectory_id(),
            planner_confidence=0.0,
        )
