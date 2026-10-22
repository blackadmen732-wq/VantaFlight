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
from .planner import VantaRace, VantaRaceConfig
from .speed import SpeedEnvelopeConfig, speed_envelope
from .state_machine import RaceEvent, RaceState, RaceStateMachine
from .trajectory import (
    CubicHermiteTrajectory,
    LookaheadConfig,
    lookahead_gate_position,
    trajectory_through_gates,
)
from .vanta_execution import (
    ExecutionMetrics,
    ExecutionMode,
    OffboardSetpoint,
    VantaExecution,
)

__all__ = [
    "AircraftState",
    "AutonomousExecutionRejected",
    "CubicHermiteTrajectory",
    "DesiredTrajectoryState",
    "ExecutionCapabilities",
    "ExecutionMetrics",
    "ExecutionMode",
    "GateTarget",
    "LookaheadConfig",
    "OffboardSetpoint",
    "RaceEvent",
    "RaceState",
    "RaceStateMachine",
    "RacingScene",
    "SceneTarget",
    "SimulationExecutionPermit",
    "SimulationOnlyExecutionGuard",
    "SpeedEnvelopeConfig",
    "TargetSlot",
    "VantaExecution",
    "VantaRace",
    "VantaRaceConfig",
    "lookahead_gate_position",
    "speed_envelope",
    "trajectory_through_gates",
]
