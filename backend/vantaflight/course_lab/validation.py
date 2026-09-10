"""Geometric validation and measurable course difficulty."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np

from .models import Course, Gate, SafeVolume


def _turn_angles(points: np.ndarray) -> np.ndarray:
    vectors = np.diff(points, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    usable = (lengths[:-1] > 1e-9) & (lengths[1:] > 1e-9)
    result = np.zeros(max(0, len(vectors) - 1))
    if np.any(usable):
        dots = np.sum(vectors[:-1] * vectors[1:], axis=1)
        cosine = dots[usable] / (lengths[:-1][usable] * lengths[1:][usable])
        result[usable] = np.arccos(np.clip(cosine, -1.0, 1.0))
    return result


def _segment_distance(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    """Shortest distance between two finite 3-D line segments."""
    u, v, w = b - a, d - c, a - c
    aa, bb, cc = float(u @ u), float(u @ v), float(v @ v)
    dd, ee = float(u @ w), float(v @ w)
    denominator = aa * cc - bb * bb
    s_n, s_d = denominator, denominator
    t_n, t_d = denominator, denominator
    if denominator < 1e-12:
        s_n, s_d, t_n, t_d = 0.0, 1.0, ee, cc
    else:
        s_n, t_n = bb * ee - cc * dd, aa * ee - bb * dd
        if s_n < 0:
            s_n, t_n, t_d = 0.0, ee, cc
        elif s_n > s_d:
            s_n, t_n, t_d = s_d, ee + bb, cc
    if t_n < 0:
        t_n = 0.0
        if -dd < 0:
            s_n = 0.0
        elif -dd > aa:
            s_n = s_d
        else:
            s_n, s_d = -dd, aa
    elif t_n > t_d:
        t_n = t_d
        if -dd + bb < 0:
            s_n = 0.0
        elif -dd + bb > aa:
            s_n = s_d
        else:
            s_n, s_d = -dd + bb, aa
    sc = 0.0 if abs(s_n) < 1e-12 else s_n / s_d
    tc = 0.0 if abs(t_n) < 1e-12 else t_n / t_d
    return float(np.linalg.norm(w + sc * u - tc * v))


def _gate_corners(gate: Gate) -> Iterable[np.ndarray]:
    _, right, up = gate.frame()
    center = np.asarray(gate.center)
    for horizontal in (-.5, .5):
        for vertical in (-.5, .5):
            yield center + horizontal * gate.width * right + vertical * gate.height * up


def _line_of_sight(a: Sequence[float], b: Sequence[float], volume: SafeVolume) -> bool:
    for alpha in np.linspace(0.0, 1.0, 17):
        point = np.asarray(a) * (1 - alpha) + np.asarray(b) * alpha
        if any(obstacle.contains(point) for obstacle in volume.obstacles):
            return False
    return True


def difficulty_metrics(
    path: Sequence[Sequence[float]],
    gates: Sequence[Gate],
    volume: SafeVolume,
    target_speed: float = 7.0,
) -> dict[str, float]:
    points = np.asarray(path, dtype=float)
    gate_centers = np.asarray([gate.center for gate in gates], dtype=float)
    spacing = np.linalg.norm(np.diff(gate_centers, axis=0), axis=1)
    turns = _turn_angles(gate_centers)
    vertical = np.abs(np.diff(gate_centers[:, 2]))
    orientation = np.asarray([
        abs(gate.pitch) + .45 * abs(gate.roll) for gate in gates
    ])
    gate_sizes = np.asarray([min(gate.size) for gate in gates])
    clearances = np.asarray([volume.clearance(point) for point in points])
    visible = [
        _line_of_sight(gates[i].center, gates[i + 1].center, volume)
        for i in range(len(gates) - 1)
    ]
    path_length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    curvature = float(np.mean(turns)) if len(turns) else 0.0
    braking = float(np.mean(
        np.maximum(0.0, target_speed * target_speed * np.sin(turns / 2) / np.maximum(spacing[1:], .1))
    )) if len(turns) else 0.0
    predicted_speed = target_speed / (
        1.0 + 1.5 * curvature + .18 * float(np.mean(vertical)) + .25 * float(np.mean(orientation))
    )
    return {
        "braking_demand": braking,
        "clearance": float(np.min(clearances)),
        "curvature": curvature,
        "density": float(len(gates) / max(path_length, 1e-9)),
        "gate_size": float(np.mean(gate_sizes)),
        "orientation": float(np.mean(orientation)),
        "predicted_speed": max(0.0, float(predicted_speed)),
        "spacing": float(np.mean(spacing)),
        "vertical": float(np.mean(vertical)),
        "visibility": float(np.mean(visible)) if visible else 1.0,
    }


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...]
    metrics: dict[str, float]

    def raise_for_errors(self) -> None:
        if not self.valid:
            raise ValueError("invalid course: " + "; ".join(self.errors))


class CourseValidator:
    def __init__(
        self,
        *,
        min_spacing: float = 1.0,
        max_spacing: float | None = None,
        drone_radius: float = .25,
        obstacle_clearance: float = .75,
        max_vertical_grade: float = 3.0,
        max_turn_angle: float = 2.45,
        self_intersection_clearance: float = .15,
    ) -> None:
        self.min_spacing = float(min_spacing)
        self.max_spacing = max_spacing
        self.drone_radius = float(drone_radius)
        self.obstacle_clearance = float(obstacle_clearance)
        self.max_vertical_grade = float(max_vertical_grade)
        self.max_turn_angle = float(max_turn_angle)
        self.self_intersection_clearance = float(self_intersection_clearance)

    def validate(self, course: Course) -> ValidationReport:
        errors: list[str] = []
        points = np.asarray(course.path, dtype=float)
        gates = course.gates
        required_clearance = self.drone_radius + self.obstacle_clearance

        if len(points) < 2 or not np.all(np.isfinite(points)):
            errors.append("path must contain finite points")
        if len(gates) < 2:
            errors.append("course must contain at least two gates")
        if errors:
            return ValidationReport(False, tuple(errors), {})

        for index, point in enumerate(points):
            if not course.volume.contains(point):
                errors.append(f"path point {index} violates boundary/floor/ceiling")
                break
            if any(obstacle.clearance(point) + 1e-7 < required_clearance for obstacle in course.volume.obstacles):
                errors.append(f"path point {index} lacks obstacle clearance")
                break

        segment_vectors = np.diff(points, axis=0)
        segment_lengths = np.linalg.norm(segment_vectors, axis=1)
        if np.any(segment_lengths < 1e-8):
            errors.append("path contains a discontinuous zero-length segment")
        horizontal = np.linalg.norm(segment_vectors[:, :2], axis=1)
        grade = np.abs(segment_vectors[:, 2]) / np.maximum(horizontal, 1e-9)
        if np.any(grade > self.max_vertical_grade):
            errors.append("path exceeds vertical grade")
        turns = _turn_angles(points)
        if len(turns) and float(np.max(turns)) > self.max_turn_angle:
            errors.append("path exceeds curvature limit")

        # Strict monotonicity on any axis proves non-adjacent segments cannot
        # cross. Fall back to the generic O(n²) check for custom looping paths.
        monotonic = any(
            np.all(np.diff(points[:, axis]) > 0) or np.all(np.diff(points[:, axis]) < 0)
            for axis in range(3)
        )
        if not monotonic:
            for i in range(len(points) - 1):
                for j in range(i + 3, len(points) - 1):
                    if _segment_distance(points[i], points[i + 1], points[j], points[j + 1]) < self.self_intersection_clearance:
                        errors.append(f"path self-intersects near segments {i} and {j}")
                        break
                if any(message.startswith("path self-intersects") for message in errors):
                    break

        if [gate.order for gate in gates] != list(range(len(gates))):
            errors.append("gate order must be contiguous and start at zero")
        centers = np.asarray([gate.center for gate in gates])
        spacings = np.linalg.norm(np.diff(centers, axis=0), axis=1)
        if np.any(spacings < self.min_spacing):
            errors.append("gate spacing is below minimum")
        if self.max_spacing is not None and np.any(spacings > self.max_spacing):
            errors.append("gate spacing exceeds maximum")

        for gate in gates:
            if not np.all(np.isfinite(np.asarray(gate.to_dict()["center"], dtype=float))):
                errors.append(f"gate {gate.order} is non-finite")
                continue
            corners = tuple(_gate_corners(gate))
            if not all(course.volume.contains(corner) for corner in corners):
                errors.append(f"gate {gate.order} opening violates volume boundary")
            if any(
                obstacle.clearance(corner) + 1e-7 < self.drone_radius
                for obstacle in course.volume.obstacles
                for corner in corners
            ):
                errors.append(f"gate {gate.order} opening lacks obstacle clearance")

            entry_plane, _, _ = gate.opening_coordinates(gate.entry)
            exit_plane, _, _ = gate.opening_coordinates(gate.exit)
            if entry_plane >= -1e-8 or exit_plane <= 1e-8:
                errors.append(f"gate {gate.order} entry/exit do not cross its plane")
            denominator = entry_plane - exit_plane
            if abs(denominator) > 1e-12:
                alpha = entry_plane / denominator
                crossing = np.asarray(gate.entry) + alpha * (
                    np.asarray(gate.exit) - np.asarray(gate.entry)
                )
                _, right, up = gate.opening_coordinates(crossing)
                if abs(right) > gate.width / 2 or abs(up) > gate.height / 2:
                    errors.append(f"gate {gate.order} path misses its opening")

        metrics = difficulty_metrics(points, gates, course.volume)
        return ValidationReport(not errors, tuple(dict.fromkeys(errors)), metrics)
