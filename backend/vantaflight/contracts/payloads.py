"""VantaFlight's message contracts.

Each payload is typed, versioned (``SCHEMA_VERSION``), framed (``Vec3``
fields declare their frame) and unit-explicit (scalar names end in their
unit). Converters build them from the runtime objects; they take duck-typed
inputs so this module never imports the subsystems it describes.

Angles: ``*_enu_rad`` yaw is counter-clockwise from east; ``*_ned_rad`` yaw is
clockwise from north (PX4); ``heading_compass_deg`` is clockwise from north.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .envelope import ContractPayload
from .frames import (
    AccelerationENU,
    AccelerationNED,
    FrameId,
    PositionENU,
    PositionNED,
    VelocityENU,
    VelocityNED,
    acceleration_enu,
    acceleration_ned,
    position_enu,
    position_ned,
    velocity_enu,
    velocity_ned,
)


class _Nested(BaseModel):
    """A typed part of a payload that is not a topic of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# -- vehicle --------------------------------------------------------------------------------
class VehicleStateV1(ContractPayload):
    """Local vehicle state, world ENU (from ``LocalState``)."""

    TOPIC = "vehicle.state"
    SCHEMA_VERSION = 1

    position: PositionENU
    velocity: VelocityENU
    yaw_enu_rad: float
    pitch_rad: float = 0.0
    roll_rad: float = 0.0
    valid: bool = True

    @classmethod
    def from_local_state(cls, local: Any) -> "VehicleStateV1":
        return cls(
            position=position_enu(local.position_enu),
            velocity=velocity_enu(local.velocity_enu),
            yaw_enu_rad=math.radians(float(local.yaw_enu_deg)),
            pitch_rad=math.radians(float(local.pitch_deg)),
            roll_rad=math.radians(float(local.roll_deg)),
            valid=bool(local.valid),
        )


class TelemetrySampleV1(ContractPayload):
    """Operator-facing telemetry (from ``Telemetry``). Unknown values are None."""

    TOPIC = "vehicle.telemetry"
    SCHEMA_VERSION = 1

    connected: bool
    armed: bool
    flight_mode: str
    position: PositionENU | None
    altitude_m: float | None
    speed_m_s: float | None
    ground_speed_m_s: float | None
    heading_compass_deg: float
    battery_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    link_age_s: float = Field(ge=0.0)
    connection_quality: str

    @classmethod
    def from_telemetry(cls, t: Any) -> "TelemetrySampleV1":
        def flag(name: str) -> bool:
            return bool(getattr(t, name, True))

        return cls(
            connected=bool(t.connected),
            armed=bool(t.armed),
            flight_mode=_enum_value(t.flight_mode),
            position=position_enu((t.x, t.y, t.z)) if flag("position_available") else None,
            altitude_m=float(t.altitude) if flag("altitude_available") else None,
            speed_m_s=abs(float(t.velocity)) if flag("velocity_available") else None,
            ground_speed_m_s=abs(float(t.ground_speed)) if flag("velocity_available") else None,
            heading_compass_deg=float(t.heading),
            battery_pct=float(t.battery_percentage) if flag("battery_available") else None,
            latitude_deg=t.latitude,
            longitude_deg=t.longitude,
            link_age_s=max(0.0, float(getattr(t, "link_age_s", 0.0))),
            connection_quality=_enum_value(t.connection_quality),
        )


# -- planner & execution ---------------------------------------------------------------------
class DesiredStateV1(ContractPayload):
    """VantaRace's desired setpoint, world ENU (from ``DesiredTrajectoryState``)."""

    TOPIC = "planner.desired"
    SCHEMA_VERSION = 1

    trajectory_id: str
    position: PositionENU
    velocity: VelocityENU
    acceleration: AccelerationENU
    yaw_enu_rad: float
    planner_confidence: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_desired(cls, d: Any) -> "DesiredStateV1":
        return cls(
            trajectory_id=str(d.trajectory_id),
            position=position_enu(d.position),
            velocity=velocity_enu(d.velocity),
            acceleration=acceleration_enu(d.acceleration),
            yaw_enu_rad=float(d.yaw),
            planner_confidence=float(d.planner_confidence),
        )


class OffboardSetpointV1(ContractPayload):
    """A setpoint as sent to PX4 offboard, local NED (from ``OffboardSetpoint``)."""

    TOPIC = "execution.setpoint"
    SCHEMA_VERSION = 1

    trajectory_id: str
    position: PositionNED
    velocity: VelocityNED
    acceleration: AccelerationNED
    yaw_ned_rad: float
    planner_confidence: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_setpoint(cls, sp: Any) -> "OffboardSetpointV1":
        return cls(
            trajectory_id=str(sp.trajectory_id),
            position=position_ned(sp.position_ned),
            velocity=velocity_ned(sp.velocity_ned),
            acceleration=acceleration_ned(sp.acceleration_ned),
            yaw_ned_rad=math.radians(float(sp.yaw_deg)),
            planner_confidence=float(sp.confidence),
        )


class CommandRecordV1(ContractPayload):
    """A command decision and what happened to it (from ``CommandResult``)."""

    TOPIC = "execution.command"
    SCHEMA_VERSION = 1

    command: str
    source: str
    accepted: bool
    status: str
    message: str = ""

    @classmethod
    def from_result(cls, result: Any, source: str) -> "CommandRecordV1":
        return cls(
            command=str(result.command),
            source=source,
            accepted=bool(result.accepted),
            status=_enum_value(result.status),
            message=str(result.message),
        )


class FailsafeTriggerV1(ContractPayload):
    TOPIC = "safety.failsafe"
    SCHEMA_VERSION = 1

    reason: str
    action: Literal["hold", "return", "land"]
    message: str

    @classmethod
    def from_trigger(cls, trigger: Any) -> "FailsafeTriggerV1":
        return cls(reason=str(trigger.reason), action=_enum_value(trigger.action), message=str(trigger.message))


# -- perception ------------------------------------------------------------------------------
class GateObservationV1(ContractPayload):
    """One gate seen by VantaSight, world ENU."""

    TOPIC = "vision.gate_observation"
    SCHEMA_VERSION = 1

    gate_id: str
    position: PositionENU
    confidence: float = Field(ge=0.0, le=1.0)
    distance_m: float = Field(ge=0.0)
    #: Distance from the course map's position (the truth for virtual gates).
    map_error_m: float | None = Field(default=None, ge=0.0)
    position_uncertainty_m: float | None = Field(default=None, ge=0.0)

    @classmethod
    def from_record(cls, record: Any) -> "GateObservationV1":
        return cls(
            gate_id=str(record.gate_id),
            position=position_enu(record.position_enu),
            confidence=float(record.confidence),
            distance_m=float(record.distance_m),
            map_error_m=float(record.error_m),
        )


class GateTargetV1(_Nested):
    gate_id: str
    slot: Literal["CURRENT", "NEXT", "FUTURE"]
    position: PositionENU
    confidence: float = Field(ge=0.0, le=1.0)
    position_uncertainty_m: float = Field(ge=0.0)


PerceptionStatusName = Literal["TRACKING", "DEGRADED", "COMMITTED", "LOST", "BLACKOUT"]


class PerceptionFrameV1(ContractPayload):
    """One VantaSight → VantaState update for the race loop."""

    TOPIC = "vision.perception"
    SCHEMA_VERSION = 1

    status: PerceptionStatusName
    frame_processed: bool
    detections: int = Field(ge=0)
    observations: tuple[GateObservationV1, ...] = ()
    targets: tuple[GateTargetV1, ...] = ()
    current_confidence: float = Field(ge=0.0, le=1.0)
    current_gate_age_s: float | None = Field(default=None, ge=0.0)
    processing_ms: float = Field(ge=0.0)

    @classmethod
    def from_result(cls, result: Any) -> "PerceptionFrameV1":
        return cls(
            status=_enum_value(result.status),
            frame_processed=bool(result.frame_processed),
            detections=int(result.detections),
            observations=tuple(GateObservationV1.from_record(o) for o in result.observations),
            targets=tuple(
                GateTargetV1(
                    gate_id=str(t.target_id),
                    slot=_enum_value(t.slot),
                    position=position_enu(t.position),
                    confidence=float(t.confidence),
                    position_uncertainty_m=float(t.uncertainty),
                )
                for t in result.targets
            ),
            current_confidence=float(result.current_confidence),
            current_gate_age_s=result.current_gate_age_s,
            processing_ms=float(result.processing_ms),
        )


# -- race recording ------------------------------------------------------------------------------
class PlannerSampleV1(_Nested):
    race_state: str | None = None
    autonomy_state: str | None = None
    desired: DesiredStateV1 | None = None


class ExecutionSampleV1(_Nested):
    mode: str
    has_permit: bool
    setpoints_sent: int = Field(ge=0)
    setpoints_rejected: int = Field(ge=0)
    hold_transitions: int = Field(ge=0)
    confidence_holds: int = Field(ge=0)
    last_setpoint_age_ms: float = Field(ge=0.0)
    last_setpoint: OffboardSetpointV1 | None = None

    @classmethod
    def from_execution(cls, execution: Any) -> "ExecutionSampleV1":
        m = execution.metrics
        last = getattr(execution, "_last_setpoint", None)
        return cls(
            mode=_enum_value(execution.mode),
            has_permit=getattr(execution, "_permit", None) is not None,
            setpoints_sent=int(m.setpoints_sent),
            setpoints_rejected=int(m.setpoints_rejected),
            hold_transitions=int(m.hold_transitions),
            confidence_holds=int(m.confidence_holds),
            last_setpoint_age_ms=max(0.0, float(m.last_setpoint_age_ms)),
            last_setpoint=OffboardSetpointV1.from_setpoint(last) if last is not None else None,
        )


class RaceSampleV1(ContractPayload):
    """The race loop at one instant: vision, state, planner, execution."""

    TOPIC = "race.sample"
    SCHEMA_VERSION = 1

    race_state: str
    race_time_s: float = Field(ge=0.0)
    remaining_gates: tuple[str, ...] = ()
    state: VehicleStateV1 | None = None
    state_age_s: float | None = Field(default=None, ge=0.0)
    vision: PerceptionFrameV1 | None = None
    planner: PlannerSampleV1 = Field(default_factory=PlannerSampleV1)
    execution: ExecutionSampleV1 | None = None


class RaceEventV1(ContractPayload):
    TOPIC = "race.event"
    SCHEMA_VERSION = 1

    event_type: str
    message: str


class GatePassV1(_Nested):
    gate_id: str
    time_s: float = Field(ge=0.0)
    offset_m: float = Field(ge=0.0)


class RaceStatsV1(_Nested):
    ticks: int = Field(default=0, ge=0)
    setpoints_sent: int = Field(default=0, ge=0)
    setpoints_rejected: int = Field(default=0, ge=0)
    setpoints_expired: int = Field(default=0, ge=0)
    hold_setpoints: int = Field(default=0, ge=0)
    recoveries: int = Field(default=0, ge=0)
    reacquisitions: int = Field(default=0, ge=0)
    stale_telemetry_events: int = Field(default=0, ge=0)
    gates_missed: int = Field(default=0, ge=0)
    avg_tick_ms: float = Field(default=0.0, ge=0.0)
    max_tick_ms: float = Field(default=0.0, ge=0.0)
    max_tracking_error_m: float = Field(default=0.0, ge=0.0)
    gate_pass_offsets_m: tuple[float, ...] = ()
    record_errors: int = Field(default=0, ge=0)


class OffboardStatsV1(_Nested):
    sent: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    expired: int = Field(default=0, ge=0)


class RaceSummaryV1(ContractPayload):
    TOPIC = "race.summary"
    SCHEMA_VERSION = 1

    state: str
    message: str = ""
    course_id: str | None = None
    course_name: str | None = None
    perception: str | None = None
    speed_limit_m_s: float | None = Field(default=None, gt=0.0)
    gates_passed: int = Field(ge=0)
    total_gates: int = Field(ge=0)
    passes: tuple[GatePassV1, ...] = ()
    race_time_s: float = Field(ge=0.0)
    elapsed_s: float = Field(ge=0.0)
    stats: RaceStatsV1 = Field(default_factory=RaceStatsV1)
    offboard: OffboardStatsV1 = Field(default_factory=OffboardStatsV1)

    @classmethod
    def from_status(cls, status: dict) -> "RaceSummaryV1":
        return cls.model_validate(_race_summary_v0_to_v1(status))


# -- legacy (pre-contract) race records ---------------------------------------------------------
def _xyz(values: Any, frame: FrameId, quantity: str) -> dict:
    if isinstance(values, dict):
        x, y, z = values["x"], values["y"], values["z"]
    else:
        x, y, z = values
    return {"x": float(x), "y": float(y), "z": float(z), "frame": frame.value, "quantity": quantity}


def _state_v0(state: dict | None) -> dict | None:
    if state is None:
        return None
    return {
        "position": _xyz(state["position_enu"], FrameId.WORLD_ENU, "position"),
        "velocity": _xyz(state["velocity_enu"], FrameId.WORLD_ENU, "velocity"),
        "yaw_enu_rad": math.radians(float(state.get("yaw_enu_deg", 0.0))),
        "pitch_rad": math.radians(float(state.get("pitch_deg", 0.0))),
        "roll_rad": math.radians(float(state.get("roll_deg", 0.0))),
        "valid": bool(state.get("valid", True)),
    }


def _vision_v0(vision: dict | None) -> dict | None:
    if vision is None:
        return None
    return {
        "status": vision["status"],
        "frame_processed": bool(vision.get("frame_processed", False)),
        "detections": int(vision.get("detections", 0)),
        "observations": [
            {
                "gate_id": o["gate_id"],
                "position": _xyz(o["position_enu"], FrameId.WORLD_ENU, "position"),
                "confidence": float(o["confidence"]),
                "distance_m": float(o["distance_m"]),
                "map_error_m": float(o["error_m"]) if o.get("error_m") is not None else None,
            }
            for o in vision.get("observations", [])
        ],
        "targets": [
            {
                "gate_id": t["gate_id"],
                "slot": t["slot"],
                "position": _xyz(t["position_enu"], FrameId.WORLD_ENU, "position"),
                "confidence": float(t["confidence"]),
                "position_uncertainty_m": float(t["uncertainty"]),
            }
            for t in vision.get("targets", [])
        ],
        "current_confidence": float(vision.get("current_confidence", 0.0)),
        "current_gate_age_s": vision.get("current_gate_age_s"),
        "processing_ms": float(vision.get("processing_ms", 0.0)),
    }


def _desired_v0(desired: dict | None) -> dict | None:
    if desired is None:
        return None
    return {
        "trajectory_id": desired["trajectory_id"],
        "position": _xyz(desired["desired_position"], FrameId.WORLD_ENU, "position"),
        "velocity": _xyz(desired["desired_velocity"], FrameId.WORLD_ENU, "velocity"),
        "acceleration": _xyz(desired["desired_acceleration"], FrameId.WORLD_ENU, "acceleration"),
        "yaw_enu_rad": float(desired["desired_yaw"]),
        "planner_confidence": float(desired["planner_confidence"]),
    }


def _setpoint_v0(sp: dict | None) -> dict | None:
    if sp is None:
        return None
    return {
        "trajectory_id": sp["trajectory_id"],
        "position": _xyz(sp["position_ned"], FrameId.LOCAL_NED, "position"),
        "velocity": _xyz(sp["velocity_ned"], FrameId.LOCAL_NED, "velocity"),
        "acceleration": _xyz(sp["acceleration_ned"], FrameId.LOCAL_NED, "acceleration"),
        "yaw_ned_rad": math.radians(float(sp["yaw_deg"])),
        "planner_confidence": float(sp["confidence"]),
    }


def _execution_v0(execution: dict | None) -> dict | None:
    if execution is None:
        return None
    m = execution.get("metrics", {})
    return {
        "mode": execution["mode"],
        "has_permit": bool(execution.get("has_permit", False)),
        "setpoints_sent": int(m.get("setpoints_sent", 0)),
        "setpoints_rejected": int(m.get("setpoints_rejected", 0)),
        "hold_transitions": int(m.get("hold_transitions", 0)),
        "confidence_holds": int(m.get("confidence_holds", 0)),
        "last_setpoint_age_ms": max(0.0, float(m.get("last_setpoint_age_ms", 0.0))),
        "last_setpoint": _setpoint_v0(execution.get("last_setpoint")),
    }


def _race_sample_v0_to_v1(old: dict) -> dict:
    """PR #8 recorded race samples as plain dicts; v1 makes frames and units explicit."""
    planner = old.get("planner") or {}
    return {
        "race_state": old["race_state"],
        "race_time_s": float(old.get("race_time_s", 0.0)),
        "remaining_gates": list(old.get("remaining_gates", [])),
        "state": _state_v0(old.get("state")),
        "state_age_s": old.get("state_age_s"),
        "vision": _vision_v0(old.get("vision")),
        "planner": {
            "race_state": planner.get("race_state"),
            "autonomy_state": planner.get("autonomy_state"),
            "desired": _desired_v0(planner.get("desired")),
        },
        "execution": _execution_v0(old.get("execution")),
    }


def _race_event_v0_to_v1(old: dict) -> dict:
    return {"event_type": str(old["event_type"]), "message": str(old.get("message", ""))}


def _race_summary_v0_to_v1(old: dict) -> dict:
    course = old.get("course") or {}
    config = old.get("config") or {}
    stats = dict(old.get("stats") or {})
    return {
        "state": old["state"],
        "message": old.get("message", ""),
        "course_id": course.get("course_id"),
        "course_name": course.get("name"),
        "perception": config.get("perception"),
        "speed_limit_m_s": config.get("speed_m_s"),
        "gates_passed": int(old.get("gates_passed", 0)),
        "total_gates": int(old.get("total_gates", 0)),
        "passes": [
            {"gate_id": p["gate_id"], "time_s": float(p["time_s"]), "offset_m": float(p["offset_m"])}
            for p in old.get("passes", [])
        ],
        "race_time_s": float(old.get("race_time_s", 0.0)),
        "elapsed_s": float(old.get("elapsed_s", 0.0)),
        "stats": {k: v for k, v in stats.items() if k in RaceStatsV1.model_fields},
        "offboard": {k: v for k, v in (old.get("offboard") or {}).items() if k in OffboardStatsV1.model_fields},
    }


#: Legacy ``autonomy_samples.kind`` values → topics (rows written before contracts).
LEGACY_RACE_KINDS = {
    "race_sample": RaceSampleV1.TOPIC,
    "race_event": RaceEventV1.TOPIC,
    "race_summary": RaceSummaryV1.TOPIC,
}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def register_all(registry) -> None:
    registry.register(VehicleStateV1, frame_id=FrameId.WORLD_ENU, flight_critical=True,
                      description="Local vehicle state used for closed-loop control.")
    registry.register(TelemetrySampleV1, frame_id=FrameId.WORLD_ENU, flight_critical=True,
                      description="Operator-facing telemetry snapshot.")
    registry.register(DesiredStateV1, frame_id=FrameId.WORLD_ENU, flight_critical=True,
                      description="VantaRace desired setpoint before execution.")
    registry.register(OffboardSetpointV1, frame_id=FrameId.LOCAL_NED, flight_critical=True,
                      description="Setpoint sent to PX4 offboard.")
    registry.register(CommandRecordV1, flight_critical=True,
                      description="A vehicle command and its transport outcome.")
    registry.register(FailsafeTriggerV1, flight_critical=True,
                      description="An automatic failsafe decision.")
    registry.register(GateObservationV1, frame_id=FrameId.WORLD_ENU, flight_critical=True,
                      description="A gate observed by VantaSight.")
    registry.register(PerceptionFrameV1, flight_critical=True,
                      description="VantaSight/VantaState output for one race tick.")
    registry.register(RaceSampleV1, upgraders={0: _race_sample_v0_to_v1},
                      description="Race loop snapshot for replay.")
    registry.register(RaceEventV1, upgraders={0: _race_event_v0_to_v1},
                      description="Race lifecycle event.")
    registry.register(RaceSummaryV1, upgraders={0: _race_summary_v0_to_v1},
                      description="Race result summary.")
