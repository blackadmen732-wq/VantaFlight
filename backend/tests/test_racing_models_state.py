from __future__ import annotations

from dataclasses import fields

import numpy as np
import pytest

from vantaflight.racing import (
    AircraftState,
    DesiredTrajectoryState,
    GateTarget,
    RaceEvent,
    RaceState,
    RaceStateMachine,
    RacingScene,
    SceneTarget,
    TargetSlot,
)


def test_aircraft_state_normalizes_and_owns_vectors() -> None:
    source = [1, 2, 3]
    state = AircraftState(source, (4, 5, 6), yaw=0.3, timestamp=12)
    source[0] = 99
    assert state.position.dtype == np.float64
    np.testing.assert_array_equal(state.position, [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(state.acceleration, np.zeros(3))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"position": [1, 2], "velocity": [0, 0, 0]}, "shape"),
        ({"position": [1, np.nan, 2], "velocity": [0, 0, 0]}, "finite"),
        ({"position": [0, 0, 0], "velocity": [0, 0, 0], "position_uncertainty": -1}, "negative"),
    ],
)
def test_aircraft_state_rejects_invalid_input(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AircraftState(**kwargs)


def test_gate_target_normalizes_normal_and_is_structural_scene_target() -> None:
    target = GateTarget([1, 2, 3], [0, 4, 0], 2.0, slot=TargetSlot.NEXT)
    np.testing.assert_allclose(target.normal, [0, 1, 0])
    assert isinstance(target, SceneTarget)


def test_scene_protocol_uses_current_next_future_slots() -> None:
    gate = GateTarget([0, 0, 0], [1, 0, 0], 1)

    class Scene:
        CURRENT = gate
        NEXT = None
        FUTURE = None

    assert isinstance(Scene(), RacingScene)


def test_desired_state_is_high_level_and_has_no_actuator_fields() -> None:
    desired = DesiredTrajectoryState(
        [1, 2, 3], [4, 5, 6], [0, 0, 1], 0.5, 10.0, "race-1", 0.8
    )
    names = {item.name.lower() for item in fields(desired)}
    assert names == {
        "position",
        "velocity",
        "acceleration",
        "yaw",
        "timestamp",
        "trajectory_id",
        "planner_confidence",
    }
    assert not names.intersection({"motor", "motors", "pwm", "throttle"})


def test_state_machine_follows_complete_deterministic_course() -> None:
    machine = RaceStateMachine()
    events = [
        RaceEvent.START,
        RaceEvent.TARGET_SEEN,
        RaceEvent.TARGET_ACQUIRED,
        RaceEvent.TARGET_LOCKED,
        RaceEvent.ALIGNED,
        RaceEvent.AT_SPEED,
        RaceEvent.GATE_PASSED,
        RaceEvent.COURSE_COMPLETE,
    ]
    expected = list(RaceState)[1:8] + [RaceState.COMPLETE]
    assert [machine.transition(event) for event in events] == expected


def test_state_machine_recovery_reset_and_invalid_transition() -> None:
    machine = RaceStateMachine()
    machine.transition(RaceEvent.START)
    machine.transition(RaceEvent.TARGET_SEEN)
    assert machine.transition(RaceEvent.TARGET_LOST) is RaceState.RECOVER
    assert machine.transition(RaceEvent.RECOVERED) is RaceState.SEARCH
    with pytest.raises(ValueError, match="invalid"):
        machine.transition(RaceEvent.AT_SPEED)
    assert machine.transition(RaceEvent.RESET) is RaceState.IDLE
