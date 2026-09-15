from __future__ import annotations

import numpy as np
import pytest

from vantaflight.racing import (
    CubicHermiteTrajectory,
    GateTarget,
    LookaheadConfig,
    TargetSlot,
    lookahead_gate_position,
    trajectory_through_gates,
)


def gate(position, slot=TargetSlot.CURRENT, confidence=1.0) -> GateTarget:
    return GateTarget(position, [1, 0, 0], 2.0, confidence=confidence, slot=slot)


def test_hermite_interpolates_knots_and_requested_derivatives() -> None:
    trajectory = CubicHermiteTrajectory(
        [2.0, 4.0],
        [[0, 0, 0], [4, 2, 0]],
        [[1, 0, 0], [0, 1, 0]],
        trajectory_id="known",
        planner_confidence=0.7,
    )
    start = trajectory.evaluate(2.0)
    end = trajectory.evaluate(4.0)
    np.testing.assert_allclose(start.position, [0, 0, 0])
    np.testing.assert_allclose(end.position, [4, 2, 0])
    np.testing.assert_allclose(start.velocity, [1, 0, 0])
    np.testing.assert_allclose(end.velocity, [0, 1, 0])
    assert start.trajectory_id == end.trajectory_id == "known"
    assert start.planner_confidence == 0.7


def test_hermite_is_position_and_velocity_continuous_at_internal_knot() -> None:
    trajectory = CubicHermiteTrajectory(
        [0, 1, 3],
        [[0, 0, 0], [2, 1, 0], [5, 1, 2]],
        trajectory_id="continuous",
    )
    left = trajectory.evaluate(1.0 - 1e-7)
    knot = trajectory.evaluate(1.0)
    right = trajectory.evaluate(1.0 + 1e-7)
    np.testing.assert_allclose(left.position, knot.position, atol=1e-6)
    np.testing.assert_allclose(right.position, knot.position, atol=1e-6)
    np.testing.assert_allclose(left.velocity, knot.velocity, atol=1e-6)
    np.testing.assert_allclose(right.velocity, knot.velocity, atol=1e-6)


def test_hermite_clamps_evaluation_to_trajectory_range() -> None:
    trajectory = CubicHermiteTrajectory([5, 6], [[0, 0, 0], [1, 0, 0]])
    before = trajectory.evaluate(-100)
    after = trajectory.evaluate(100)
    assert before.timestamp == 5
    assert after.timestamp == 6
    np.testing.assert_allclose(before.position, [0, 0, 0])
    np.testing.assert_allclose(after.position, [1, 0, 0])


def test_lookahead_bends_current_target_toward_next_and_future() -> None:
    current = gate([0, 0, 0])
    next_gate = gate([10, 4, 0], TargetSlot.NEXT)
    future = gate([20, 10, 2], TargetSlot.FUTURE)
    config = LookaheadConfig(next_weight=0.25, future_weight=0.1, exit_distance=2)
    target = lookahead_gate_position(current, next_gate, future, config)
    expected_exit = np.array([2.0, 0, 0])
    expected = expected_exit + 0.25 * (next_gate.position - expected_exit)
    expected += 0.1 * (future.position - expected_exit)
    np.testing.assert_allclose(target, expected)
    assert target[1] > 0 and target[2] > 0


def test_gate_trajectory_uses_lookahead_timing_and_minimum_confidence() -> None:
    gates = [gate([5, 0, 0], confidence=0.9), gate([10, 2, 0], confidence=0.6)]
    trajectory = trajectory_through_gates(
        [0, 0, 0],
        gates,
        speed=5,
        start_time=10,
        trajectory_id="gates",
    )
    assert trajectory.times[0] == 10
    assert np.all(np.diff(trajectory.times) > 0)
    assert trajectory.planner_confidence == 0.6
    assert trajectory.evaluate(trajectory.times[-1]).trajectory_id == "gates"


@pytest.mark.parametrize(
    "times,positions",
    [
        ([0], [[0, 0, 0]]),
        ([0, 0], [[0, 0, 0], [1, 0, 0]]),
        ([0, 1], [[0, 0], [1, 0]]),
    ],
)
def test_trajectory_rejects_invalid_geometry(times, positions) -> None:
    with pytest.raises(ValueError):
        CubicHermiteTrajectory(times, positions)
