from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from vantaflight.racing import (
    AircraftState,
    AutonomousExecutionRejected,
    GateTarget,
    RaceState,
    SimulationExecutionPermit,
    SimulationOnlyExecutionGuard,
    SpeedEnvelopeConfig,
    TargetSlot,
    VantaRace,
    VantaRaceConfig,
)


@dataclass
class Scene:
    CURRENT: GateTarget | None
    NEXT: GateTarget | None = None
    FUTURE: GateTarget | None = None


def target(
    position,
    slot: TargetSlot,
    *,
    confidence: float = 1.0,
    clearance: float = 3.0,
    uncertainty: float = 0.0,
) -> GateTarget:
    return GateTarget(
        position,
        [1, 0, 0],
        clearance,
        confidence=confidence,
        uncertainty=uncertainty,
        slot=slot,
        target_id=slot.value,
    )


def aircraft(timestamp: float = 0.0) -> AircraftState:
    return AircraftState([0, 0, 0], [1, 0, 0], timestamp=timestamp)


def full_scene(confidence: float = 1.0, uncertainty: float = 0.0) -> Scene:
    return Scene(
        target([6, 0, 0], TargetSlot.CURRENT, confidence=confidence, uncertainty=uncertainty),
        target([12, 5, 0], TargetSlot.NEXT, confidence=confidence, uncertainty=uncertainty),
        target([18, 10, 2], TargetSlot.FUTURE, confidence=confidence, uncertainty=uncertainty),
    )


def test_unified_planner_combines_gate_lookahead_speed_envelope_and_retiming() -> None:
    config = VantaRaceConfig(
        aggression=0.8,
        trajectory_samples=41,
        speed=SpeedEnvelopeConfig(
            max_forward_acceleration=3.0,
            max_braking_acceleration=4.0,
        ),
    )
    planner = VantaRace(config)
    desired = planner.plan(aircraft(), full_scene())

    assert planner.state is RaceState.ACQUIRE
    assert desired.position[0] > 0
    assert desired.position[1] > 0
    assert planner._trajectory is not None
    assert planner._trajectory.positions[-1, 1] > 9.0
    assert len(planner.last_speed_profile) == config.trajectory_samples
    ds = np.diff(planner.last_sample_distances)
    dv2 = np.diff(planner.last_speed_profile**2)
    assert np.all(dv2 <= 2 * config.speed.max_forward_acceleration * ds + 1e-9)
    assert np.all(-dv2 <= 2 * config.speed.max_braking_acceleration * ds + 1e-9)
    assert not np.allclose(
        np.diff(planner._trajectory.times),
        np.diff(planner._trajectory.times)[0],
    )


def test_confidence_and_uncertainty_continuously_scale_speed_and_aggression() -> None:
    high = VantaRace(VantaRaceConfig(aggression=1.0))
    low = VantaRace(VantaRaceConfig(aggression=1.0))
    high_desired = high.plan(aircraft(), full_scene(confidence=1.0))
    low_desired = low.plan(aircraft(), full_scene(confidence=0.35, uncertainty=0.6))

    assert low_desired.planner_confidence < high_desired.planner_confidence
    assert low.aggression_scale < high.aggression_scale
    assert np.max(low.last_speed_profile) < np.max(high.last_speed_profile)


def test_brief_target_dropout_continues_prediction_without_recovery() -> None:
    planner = VantaRace(VantaRaceConfig(prediction_grace_s=0.5))
    visible = planner.plan(aircraft(10.0), full_scene())
    state_before = planner.state
    predicted = planner.plan(aircraft(10.2), Scene(None))

    assert predicted.trajectory_id == visible.trajectory_id
    assert planner.state is state_before
    assert 0 < predicted.planner_confidence < visible.planner_confidence


def test_truly_lost_target_transitions_to_recovery_and_brakes() -> None:
    planner = VantaRace(VantaRaceConfig(prediction_grace_s=0.2))
    planner.plan(aircraft(5.0), full_scene())
    recovery = planner.plan(aircraft(5.21), Scene(None))

    assert planner.state is RaceState.RECOVER
    assert recovery.planner_confidence == 0
    assert planner.last_speed_profile[-1] == 0


def test_executable_plan_requires_guard_issued_local_sitl_permit() -> None:
    planner = VantaRace()
    with pytest.raises(AutonomousExecutionRejected):
        planner.plan(aircraft(), full_scene(), executable=True)
    forged = SimulationExecutionPermit("udp://127.0.0.1:14540", "px4_sitl", object())
    with pytest.raises(AutonomousExecutionRejected):
        planner.plan(aircraft(), full_scene(), executable=True, permit=forged)

    capabilities = SimpleNamespace(
        is_simulated=True,
        adapter_type="px4_sitl",
        supported_capabilities=["position", "simulation"],
    )
    permit = SimulationOnlyExecutionGuard.authorize("udp://localhost:14580", capabilities)
    desired = planner.plan_executable(aircraft(), full_scene(), permit)
    assert planner.last_plan_executable is True
    assert desired.trajectory_id


def test_desired_state_aliases_serialize_frontend_contract_without_actuators() -> None:
    desired = VantaRace().plan(aircraft(), full_scene())
    payload = desired.to_dict()
    assert desired.desired_position is desired.position
    assert desired.desired_velocity is desired.velocity
    assert desired.desired_acceleration is desired.acceleration
    assert desired.desired_yaw == desired.yaw
    assert set(payload) == {
        "desired_position",
        "desired_velocity",
        "desired_acceleration",
        "desired_yaw",
        "timestamp",
        "trajectory_id",
        "planner_confidence",
    }
    forbidden = {"pwm", "motor", "motors", "throttle", "actuator", "actuators"}
    assert forbidden.isdisjoint({key.lower() for key in payload})
