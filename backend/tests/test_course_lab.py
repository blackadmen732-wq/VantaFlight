"""Property-oriented tests for deterministic procedural course generation."""
from __future__ import annotations

from dataclasses import asdict
import math

from hypothesis import given, settings, strategies as st
import numpy as np
import pytest

from vantaflight.course_lab import (
    BoxObstacle,
    Course,
    CourseAnalyzer,
    CourseGenerator,
    CourseMode,
    CourseValidator,
    GenerationConfig,
    ParameterBounds,
    ParameterExperiment,
    RunSample,
    SafeVolume,
)


MODES = tuple(CourseMode)


@settings(max_examples=45, deadline=None)
@given(seed=st.integers(0, 2**32 - 1), mode=st.sampled_from(MODES))
def test_many_seed_courses_are_finite_ordered_continuous_and_valid(seed, mode):
    course = CourseGenerator().generate(mode, seed=seed)
    report = CourseValidator().validate(course)
    path = np.asarray(course.path)

    assert report.valid, report.errors
    assert np.all(np.isfinite(path))
    assert np.all(np.linalg.norm(np.diff(path, axis=0), axis=1) > 0)
    assert [gate.order for gate in course.gates] == list(range(len(course.gates)))
    assert all(np.isfinite(list(gate.center) + list(gate.normal) + list(gate.size)).all()
               for gate in course.gates)
    assert all(np.linalg.norm(gate.normal) == pytest.approx(1.0) for gate in course.gates)


@settings(max_examples=35, deadline=None)
@given(seed=st.integers(0, 100_000), mode=st.sampled_from(MODES))
def test_bounds_clearance_spacing_and_gate_plane_crossings(seed, mode):
    volume = SafeVolume(
        dimensions=(34, 64, 18),
        floor=2,
        ceiling=20,
        boundary_margin=1.25,
        obstacles=(BoxObstacle(center=(0, 0, 11), size=(3, 4, 4)),),
    )
    config = GenerationConfig(obstacle_clearance=.6, drone_radius=.2)
    course = CourseGenerator(volume, config).generate(mode, seed=seed)
    centers = np.asarray([gate.center for gate in course.gates])
    spacing = np.linalg.norm(np.diff(centers, axis=0), axis=1)

    assert all(volume.contains(point) for point in course.path)
    assert min(volume.clearance(point) for point in course.path) >= .8 - 1e-6
    assert np.all(spacing > 1.0)
    assert np.all(spacing < volume.depth / 2)
    for gate in course.gates:
        entry_plane, _, _ = gate.opening_coordinates(gate.entry)
        exit_plane, _, _ = gate.opening_coordinates(gate.exit)
        assert entry_plane < 0 < exit_plane
        alpha = entry_plane / (entry_plane - exit_plane)
        crossing = np.asarray(gate.entry) + alpha * (
            np.asarray(gate.exit) - np.asarray(gate.entry)
        )
        plane, right, up = gate.opening_coordinates(crossing)
        assert abs(plane) < 1e-7
        assert abs(right) <= gate.width / 2
        assert abs(up) <= gate.height / 2


def test_mode_constraints_are_meaningfully_different():
    generated = {mode: CourseGenerator().generate(mode, seed=22) for mode in MODES}
    metrics = {mode: course.difficulty for mode, course in generated.items()}

    assert metrics[CourseMode.SPEED_RUN]["predicted_speed"] > metrics[CourseMode.TECHNICAL]["predicted_speed"]
    assert metrics[CourseMode.SPEED_RUN]["curvature"] < metrics[CourseMode.SLALOM]["curvature"]
    assert metrics[CourseMode.VERTICAL]["vertical"] > metrics[CourseMode.SLALOM]["vertical"]
    assert metrics[CourseMode.ADVERSARY]["gate_size"] < metrics[CourseMode.RANDOM]["gate_size"]
    assert metrics[CourseMode.TECHNICAL]["orientation"] > metrics[CourseMode.SPEED_RUN]["orientation"]


@settings(max_examples=25, deadline=None)
@given(seed=st.integers(0, 10_000), mode=st.sampled_from(MODES))
def test_difficulty_metrics_are_finite_and_physical(seed, mode):
    course = CourseGenerator().generate(mode, seed=seed)
    expected = {
        "gate_size", "spacing", "curvature", "vertical", "orientation",
        "braking_demand", "visibility", "density", "predicted_speed", "clearance",
    }
    assert set(course.difficulty) == expected
    assert all(math.isfinite(value) for value in course.difficulty.values())
    assert course.difficulty["gate_size"] > 0
    assert course.difficulty["spacing"] > 0
    assert course.difficulty["predicted_speed"] >= 0
    assert course.difficulty["braking_demand"] >= 0
    assert 0 <= course.difficulty["visibility"] <= 1


def test_serialization_is_deterministic_and_round_trips():
    course = CourseGenerator().generate(CourseMode.CHALLENGE, seed=734)
    encoded = course.serialize()
    restored = Course.deserialize(encoded)

    assert encoded == course.serialize()
    assert restored.serialize() == encoded
    assert restored == course
    assert "NaN" not in encoded and "Infinity" not in encoded


def test_analyzer_calculates_nonnegative_run_gate_and_segment_metrics():
    course = CourseGenerator().generate(CourseMode.SLALOM, seed=9, gate_count=8)
    samples = [
        RunSample(float(index) * .1, point, 8.0 - (2.0 if 35 <= index < 42 else 0.0))
        for index, point in enumerate(course.path)
    ]
    result = CourseAnalyzer(speed_loss_threshold=.5).analyze(course, samples)

    assert result.run.gates_passed == len(course.gates)
    assert result.run.completion_ratio == 1
    assert result.run.distance > 0
    assert result.run.average_speed >= 0
    assert result.run.minimum_speed >= 0
    assert len(result.segments) == len(course.gates) - 1
    assert all(metric.speed_loss >= 0 and metric.crossing_speed >= 0 for metric in result.gates)
    assert all(metric.average_speed >= 0 and metric.minimum_speed >= 0 for metric in result.segments)


def test_analyzer_rejects_negative_speed_assumptions():
    with pytest.raises(ValueError, match="nonnegative"):
        RunSample(0, (0, 0, 1), -1)


def test_bounded_grid_and_random_experiments_record_without_mutating_defaults():
    config = GenerationConfig()
    generator = CourseGenerator(config=config)
    experiment = ParameterExperiment(
        generator,
        {
            "gate_count": ParameterBounds(7, 10, integer=True),
            "max_gate_size": (2.5, 4.0),
        },
        objective=lambda course: {"score": course.difficulty["predicted_speed"]},
    )
    grid = experiment.grid_search(
        {"gate_count": [7, 9], "max_gate_size": [2.8, 3.4]},
        mode=CourseMode.RANDOM,
        generation_seed=4,
    )
    random_one = experiment.random_search(6, seed=88, mode=CourseMode.SLALOM)
    random_two = ParameterExperiment(
        generator,
        experiment.bounds,
        objective=experiment.objective,
    ).random_search(6, seed=88, mode=CourseMode.SLALOM)

    assert len(grid) == 4
    assert len(experiment.records) == 10
    assert random_one == random_two
    assert all(record.error is None and record.score is not None for record in experiment.records)
    assert generator.config == config
    assert asdict(generator.config) == asdict(GenerationConfig())


def test_parameter_experiment_enforces_bounds():
    experiment = ParameterExperiment(
        CourseGenerator(),
        {"gate_count": ParameterBounds(5, 8, integer=True)},
    )
    with pytest.raises(ValueError, match="outside"):
        experiment.grid_search({"gate_count": [9]})
