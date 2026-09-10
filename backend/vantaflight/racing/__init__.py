"""Simulation-only gate racing planning primitives."""

from .execution import (
    AutonomousExecutionRejected,
    ExecutionCapabilities,
    SimulationExecutionPermit,
    SimulationOnlyExecutionGuard,
)
from .models import (
    AircraftState,
    DesiredTrajectoryState,
    GateTarget,
    RacingScene,
    SceneTarget,
    TargetSlot,
)
from .speed import SpeedEnvelopeConfig, speed_envelope
from .state_machine import RaceEvent, RaceState, RaceStateMachine
from .trajectory import (
    CubicHermiteTrajectory,
    LookaheadConfig,
    lookahead_gate_position,
    trajectory_through_gates,
)

__all__ = [
    "AircraftState",
    "AutonomousExecutionRejected",
    "CubicHermiteTrajectory",
    "DesiredTrajectoryState",
    "ExecutionCapabilities",
    "GateTarget",
    "LookaheadConfig",
    "RaceEvent",
    "RaceState",
    "RaceStateMachine",
    "RacingScene",
    "SceneTarget",
    "SimulationExecutionPermit",
    "SimulationOnlyExecutionGuard",
    "SpeedEnvelopeConfig",
    "TargetSlot",
    "lookahead_gate_position",
    "speed_envelope",
    "trajectory_through_gates",
]
