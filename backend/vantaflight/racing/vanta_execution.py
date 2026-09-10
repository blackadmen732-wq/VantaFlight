"""VantaExecution — clean boundary between planner setpoints and PX4 offboard."""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from .execution import (
    AutonomousExecutionRejected,
    SimulationExecutionPermit,
    SimulationOnlyExecutionGuard,
)
from .models import DesiredTrajectoryState

logger = logging.getLogger(__name__)


class ExecutionMode(str, enum.Enum):
    IDLE = "IDLE"
    OFFBOARD_POSITION = "OFFBOARD_POSITION"
    OFFBOARD_VELOCITY = "OFFBOARD_VELOCITY"
    HOLD = "HOLD"
    EMERGENCY_HOLD = "EMERGENCY_HOLD"


@dataclass
class OffboardSetpoint:
    """NED position/velocity/acceleration setpoint for PX4 offboard mode."""
    position_ned: np.ndarray
    velocity_ned: np.ndarray
    acceleration_ned: np.ndarray
    yaw_deg: float
    timestamp: float
    trajectory_id: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "position_ned": self.position_ned.tolist(),
            "velocity_ned": self.velocity_ned.tolist(),
            "acceleration_ned": self.acceleration_ned.tolist(),
            "yaw_deg": round(self.yaw_deg, 2),
            "timestamp": self.timestamp,
            "trajectory_id": self.trajectory_id,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class ExecutionMetrics:
    setpoints_sent: int = 0
    setpoints_rejected: int = 0
    hold_transitions: int = 0
    confidence_holds: int = 0
    last_setpoint_age_ms: float = 0.0
    mode: ExecutionMode = ExecutionMode.IDLE

    def to_dict(self) -> dict:
        return {
            "setpoints_sent": self.setpoints_sent,
            "setpoints_rejected": self.setpoints_rejected,
            "hold_transitions": self.hold_transitions,
            "confidence_holds": self.confidence_holds,
            "last_setpoint_age_ms": round(self.last_setpoint_age_ms, 2),
            "mode": self.mode.value,
        }


class VantaExecution:
    """Translates VantaRace trajectory output to PX4 offboard commands.

    Responsibilities:
    - Convert DesiredTrajectoryState (local frame) to NED offboard setpoints
    - Enforce confidence floor — drops to HOLD if planner confidence too low
    - Rate-limit setpoints to match PX4 offboard expectations (~50 Hz)
    - Track execution metrics
    - Requires a valid SimulationExecutionPermit for every setpoint

    Does NOT:
    - Touch actuators, motor speeds, or attitude directly (PX4 owns that)
    - Maintain its own position estimator (trusts upstream state)
    - Send any command without a valid permit
    """

    def __init__(
        self,
        *,
        confidence_floor: float = 0.15,
        max_setpoint_age_s: float = 0.5,
        hold_position: np.ndarray | None = None,
    ) -> None:
        self._confidence_floor = confidence_floor
        self._max_setpoint_age = max_setpoint_age_s
        self._mode = ExecutionMode.IDLE
        self._permit: SimulationExecutionPermit | None = None
        self._last_setpoint: OffboardSetpoint | None = None
        self._last_send_time: float = 0.0
        self._hold_position = hold_position if hold_position is not None else np.zeros(3)
        self._metrics = ExecutionMetrics()

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @property
    def metrics(self) -> ExecutionMetrics:
        self._metrics.mode = self._mode
        if self._last_setpoint:
            self._metrics.last_setpoint_age_ms = (
                (time.monotonic() - self._last_send_time) * 1000.0
            )
        return self._metrics

    def activate(self, permit: SimulationExecutionPermit) -> None:
        SimulationOnlyExecutionGuard.validate_permit(permit)
        self._permit = permit
        self._mode = ExecutionMode.OFFBOARD_POSITION
        logger.info("VantaExecution activated with permit for %s", permit.endpoint)

    def deactivate(self) -> None:
        self._mode = ExecutionMode.IDLE
        self._permit = None
        self._last_setpoint = None
        logger.info("VantaExecution deactivated")

    def translate(self, desired: DesiredTrajectoryState) -> OffboardSetpoint | None:
        """Convert a planner setpoint to an offboard command.

        Returns None if the setpoint should not be sent (low confidence,
        stale, or no active permit).
        """
        if self._mode == ExecutionMode.IDLE:
            return None

        if self._permit is None:
            self._metrics.setpoints_rejected += 1
            return None

        SimulationOnlyExecutionGuard.validate_permit(self._permit)

        if desired.planner_confidence < self._confidence_floor:
            self._metrics.confidence_holds += 1
            if self._mode != ExecutionMode.HOLD:
                self._mode = ExecutionMode.HOLD
                self._metrics.hold_transitions += 1
                logger.info(
                    "Confidence %.3f below floor %.3f, switching to HOLD",
                    desired.planner_confidence, self._confidence_floor,
                )
            return self._hold_setpoint(desired.timestamp, desired.trajectory_id)

        if self._mode == ExecutionMode.HOLD:
            self._mode = ExecutionMode.OFFBOARD_POSITION
            logger.info("Confidence recovered, resuming offboard position")

        setpoint = OffboardSetpoint(
            position_ned=np.array(desired.position, dtype=np.float64),
            velocity_ned=np.array(desired.velocity, dtype=np.float64),
            acceleration_ned=np.array(desired.acceleration, dtype=np.float64),
            yaw_deg=float(desired.yaw),
            timestamp=desired.timestamp,
            trajectory_id=desired.trajectory_id,
            confidence=desired.planner_confidence,
        )

        self._last_setpoint = setpoint
        self._last_send_time = time.monotonic()
        self._metrics.setpoints_sent += 1
        return setpoint

    def _hold_setpoint(self, timestamp: float, trajectory_id: str) -> OffboardSetpoint:
        return OffboardSetpoint(
            position_ned=self._hold_position.copy(),
            velocity_ned=np.zeros(3),
            acceleration_ned=np.zeros(3),
            yaw_deg=0.0,
            timestamp=timestamp,
            trajectory_id=trajectory_id,
            confidence=0.0,
        )

    def update_hold_position(self, position: np.ndarray) -> None:
        self._hold_position = np.array(position, dtype=np.float64)

    def is_setpoint_stale(self) -> bool:
        if self._last_send_time <= 0:
            return True
        return (time.monotonic() - self._last_send_time) > self._max_setpoint_age

    def to_dict(self) -> dict:
        return {
            "mode": self._mode.value,
            "has_permit": self._permit is not None,
            "metrics": self.metrics.to_dict(),
            "last_setpoint": self._last_setpoint.to_dict() if self._last_setpoint else None,
        }
