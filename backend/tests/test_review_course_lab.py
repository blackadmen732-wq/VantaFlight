"""Focused regressions for CourseLab review findings."""
from __future__ import annotations

from dataclasses import replace
import math

from hypothesis import given, settings, strategies as st
import pytest

from vantaflight.course_lab import (
    BoxObstacle,
    Course,
    CourseAnalyzer,
    CourseGenerator,
    CourseMode,
    CourseValidator,
    Gate,
    GenerationConfig,
    ParameterBounds,
    ParameterExperiment,
    RunSample,
    SafeVolume,
    ValidationSample,
)


def _gate(order: int, x: float, *, center=(0.0, 0.0, 3.0), size=(1.0, 1.0)) -> Gate:
    point = (x, center[1], center[2])
    return Gate(
        center=point,
        size=size,
        yaw=0.0,
        pitch=0.0,
        roll=0.0,
        normal=(1.0, 0.0, 0.0),
        entry=(x - 0.5, center[1], center[2]),
        exit=(x + 0.5, center[1], center[2]),
        order=order,
    )


def _course(
    path,
    *,
    volume: SafeVolume | None = None,
    gates: tuple[Gate, ...] | None = None,
) -> Course:
    return Course(
        mode=CourseMode.RANDOM,
        seed=1,
        volume=volume or SafeVolume(dimensions=(20, 20, 10)),
        path=tuple(path),
        gates=gates if gates is not None else (_gate(0, -3), _gate(1, 3)),
    )


@settings(max_examples=30, deadline=None)
@given(
    y=st.floats(-2.0, 2.0, allow_nan=False, allow_infinity=False),
    z=st.floats(2.0, 6.0, allow_nan=False, allow_infinity=False),
)
def test_validator_checks_obstacle_clearance_over_complete_segments(y, z):
    obstacle = BoxObstacle(center=(0, y, z), size=(0.2, 0.2, 0.2))
    volume = SafeVolume(dimensions=(20, 20, 10), obstacles=(obstacle,))
    course = _course(
        ((-3, y, z), (3, y, z)),
        volume=volume,
        gates=(_gate(0, -3, center=(-3, y, z)), _gate(1, 3, center=(3, y, z))),
    )

    report = CourseValidator(drone_radius=0.1, obstacle_clearance=0.2).validate(course)

    assert "path segment 0 lacks obstacle clearance" in report.errors
    assert not any("path point" in error and "obstacle" in error for error in report.errors)


def test_gate_plane_interior_is_checked_for_obstacles_and_drone_radius():
    obstacle = BoxObstacle(center=(0.2, 0, 3), size=(0.1, 0.2, 0.2))
    volume = SafeVolume(dimensions=(20, 20, 10), obstacles=(obstacle,))
    gates = (_gate(0, 0, size=(4, 4)), _gate(1, 5))
    course = _course(((-4, -5, 3), (6, -5, 3)), volume=volume, gates=gates)

    report = CourseValidator(drone_radius=0.25, obstacle_clearance=0).validate(course)

    assert "gate 0 opening lacks obstacle clearance" in report.errors


def test_gate_boundary_and_crossing_include_drone_radius():
    volume = SafeVolume(dimensions=(10, 10, 6), boundary_margin=1)
    edge_gate = _gate(0, 0, center=(0, -3.4, 3), size=(1, 1))
    missed_gate = replace(_gate(1, 3), entry=(2.5, 0.4, 3), exit=(3.5, 0.4, 3))
    course = _course(((-3, 3, 3), (4, 3, 3)), volume=volume, gates=(edge_gate, missed_gate))

    report = CourseValidator(drone_radius=0.25, obstacle_clearance=0).validate(course)

    assert "gate 0 opening violates volume boundary" in report.errors
    assert "gate 1 path misses its opening" in report.errors


def test_non_adjacent_segments_two_indices_apart_are_checked():
    course = _course(
        ((-2, -2, 3), (2, 2, 3), (-2, 2, 3), (2, -2, 3)),
        gates=(_gate(0, -2), _gate(1, 2)),
    )

    report = CourseValidator(max_turn_angle=math.pi).validate(course)

    assert any(error.startswith("path self-intersects near segments 0 and 2") for error in report.errors)


@pytest.mark.parametrize("seed", range(20))
def test_narrow_safe_volume_generation_fits_gate_clearance(seed):
    volume = SafeVolume(dimensions=(2.4, 30, 5), boundary_margin=0.5)
    config = GenerationConfig(
        gate_count=6,
        min_gate_size=1.4,
        max_gate_size=2.0,
        drone_radius=0.25,
        obstacle_clearance=0,
    )

    course = CourseGenerator(volume, config).generate(CourseMode.SLALOM, seed=seed)
    report = CourseValidator(
        min_spacing=min(config.min_spacing, 1.0),
        drone_radius=config.drone_radius,
        obstacle_clearance=config.obstacle_clearance,
    ).validate(course)

    assert report.valid, report.errors
    assert all(min(gate.size) >= 2 * config.drone_radius for gate in course.gates)


def test_analyzer_handles_a_course_with_no_gates():
    course = _course(((0, 0, 3), (1, 0, 3)), gates=())

    result = CourseAnalyzer().analyze(
        course,
        (RunSample(0, (0, 0, 3), 1), RunSample(1, (1, 0, 3), 1)),
    )

    assert result.run.gates_passed == 0
    assert result.run.completion_ratio == 0.0
    assert result.gates == ()
    assert result.segments == ()


def test_segment_metrics_interpolate_both_gate_crossing_boundaries():
    course = _course(
        ((0, 0, 3), (10, 0, 3)),
        gates=(_gate(0, 2), _gate(1, 8)),
    )
    samples = (
        RunSample(0, (0, 0, 3), 2),
        RunSample(5, (5, 0, 3), 4),
        RunSample(10, (10, 0, 3), 6),
    )

    result = CourseAnalyzer().analyze(course, samples)

    assert [gate.timestamp for gate in result.gates] == pytest.approx([2, 8])
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.duration == pytest.approx(6)
    assert segment.distance == pytest.approx(6)
    assert segment.average_speed == pytest.approx(1)
    assert segment.minimum_speed == pytest.approx(2.8)


def test_integer_bounds_reject_ranges_without_an_integer():
    with pytest.raises(ValueError, match="contain no integers"):
        ParameterBounds(0.1, 0.9, integer=True)


def test_invalid_experiment_candidate_is_recorded_instead_of_aborting():
    experiment = ParameterExperiment(
        CourseGenerator(),
        {"gate_count": ParameterBounds(5, 8, integer=True)},
    )

    records = experiment.grid_search({"gate_count": [9]})

    assert len(records) == 1
    assert records[0].candidate == {"gate_count": 9}
    assert records[0].score is None
    assert "outside" in (records[0].error or "")
    assert experiment.records == list(records)


def test_nonfinite_objective_mapping_value_is_recorded_as_an_error():
    experiment = ParameterExperiment(
        CourseGenerator(),
        {"gate_count": ParameterBounds(5, 8, integer=True)},
        objective=lambda _: {"other_metric": math.nan},
    )

    record = experiment.grid_search({"gate_count": [5]})[0]

    assert record.score is None
    assert record.result == {}
    assert "finite" in (record.error or "")


@pytest.mark.parametrize(
    "field",
    [
        "timestamp",
        "clearance",
        "vision_position_error",
        "vision_orientation_error",
        "prediction_error",
        "trajectory_following_error",
        "controller_lag_s",
        "perception_latency_s",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_validation_sample_rejects_nonfinite_numeric_fields(field, value):
    values = {"timestamp": 0.0, field: value}

    with pytest.raises(ValueError, match="finite"):
        ValidationSample(**values)

