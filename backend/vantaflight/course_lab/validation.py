"""Geometric validation and measurable course difficulty."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np

from .models import BoxObstacle, Course, Gate, SafeVolume


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


def _segment_box_clearance(
    a: Sequence[float], b: Sequence[float], obstacle: BoxObstacle
) -> float:
    """Exact minimum Euclidean distance between a segment and an AABB."""
    start, end = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    direction = end - start
    breakpoints = {0.0, 1.0}
    for axis in range(3):
        if abs(direction[axis]) > 1e-15:
            for boundary in (obstacle.lower[axis], obstacle.upper[axis]):
                crossing = float((boundary - start[axis]) / direction[axis])
                if 0.0 < crossing < 1.0:
                    breakpoints.add(crossing)
    ordered = sorted(breakpoints)
    candidates = set(ordered)
    for left, right in zip(ordered, ordered[1:]):
        midpoint = (left + right) / 2.0
        point = start + midpoint * direction
        active: list[tuple[float, float]] = []
        for axis in range(3):
            if point[axis] < obstacle.lower[axis]:
                active.append((start[axis] - obstacle.lower[axis], direction[axis]))
            elif point[axis] > obstacle.upper[axis]:
                active.append((start[axis] - obstacle.upper[axis], direction[axis]))
        quadratic = sum(slope * slope for _, slope in active)
        if quadratic > 0:
            optimum = -sum(offset * slope for offset, slope in active) / quadratic
            candidates.add(min(right, max(left, float(optimum))))

    return min(
        float(np.linalg.norm(np.maximum(
            np.maximum(obstacle.lower - (start + alpha * direction),
                       (start + alpha * direction) - obstacle.upper),
            0.0,
        )))
        for alpha in candidates
    )


def _gate_corners(gate: Gate) -> Iterable[np.ndarray]:
    _, right, up = gate.frame()
    center = np.asarray(gate.center)
    for horizontal in (-.5, .5):
        for vertical in (-.5, .5):
            yield center + horizontal * gate.width * right + vertical * gate.height * up


def _box_corners(obstacle: BoxObstacle) -> tuple[np.ndarray, ...]:
    return tuple(
        np.asarray((x, y, z), dtype=float)
        for x in (obstacle.lower[0], obstacle.upper[0])
        for y in (obstacle.lower[1], obstacle.upper[1])
        for z in (obstacle.lower[2], obstacle.upper[2])
    )


def _box_edges(obstacle: BoxObstacle) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    corners = _box_corners(obstacle)
    for index, corner in enumerate(corners):
        for bit in (1, 2, 4):
            other = index ^ bit
            if index < other:
                yield corner, corners[other]


def _point_rectangle_distance(point: np.ndarray, gate: Gate) -> float:
    plane, horizontal, vertical = gate.opening_coordinates(point)
    horizontal = max(abs(horizontal) - gate.width / 2, 0.0)
    vertical = max(abs(vertical) - gate.height / 2, 0.0)
    return float(math.sqrt(plane * plane + horizontal * horizontal + vertical * vertical))


def _rectangle_intersects_box(gate: Gate, obstacle: BoxObstacle) -> bool:
    """Separating-axis test for a zero-thickness oriented rectangle and AABB."""
    normal, right, up = gate.frame()
    center_delta = np.asarray(gate.center) - np.asarray(obstacle.center)
    box_extent = np.asarray(obstacle.size) / 2.0
    axes = [
        np.eye(3)[axis] for axis in range(3)
    ] + [normal] + [
        np.cross(axis, edge)
        for axis in np.eye(3)
        for edge in (right, up)
    ]
    for axis in axes:
        length = float(np.linalg.norm(axis))
        if length < 1e-12:
            continue
        axis = axis / length
        separation = abs(float(center_delta @ axis))
        box_radius = float(box_extent @ np.abs(axis))
        rectangle_radius = (
            gate.width / 2 * abs(float(right @ axis))
            + gate.height / 2 * abs(float(up @ axis))
        )
        if separation > box_radius + rectangle_radius + 1e-12:
            return False
    return True


def _gate_obstacle_clearance(gate: Gate, obstacle: BoxObstacle) -> float:
    """Exact feature distance between the gate's opening plane and an AABB."""
    if _rectangle_intersects_box(gate, obstacle):
        return 0.0
    gate_corners = tuple(_gate_corners(gate))
    gate_edges = tuple(
        (gate_corners[a], gate_corners[b]) for a, b in ((0, 1), (0, 2), (1, 3), (2, 3))
    )
    box_edges = tuple(_box_edges(obstacle))
    distances = [
        *(_segment_box_clearance(a, b, obstacle) for a, b in gate_edges),
        *(_point_rectangle_distance(corner, gate) for corner in _box_corners(obstacle)),
        *(
            _segment_distance(a, b, c, d)
            for a, b in gate_edges
            for c, d in box_edges
        ),
    ]
    return min(distances)


def _line_of_sight(a: Sequence[float], b: Sequence[float], volume: SafeVolume) -> bool:
    return all(_segment_box_clearance(a, b, obstacle) > 0 for obstacle in volume.obstacles)


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
            if not course.volume.contains(point, margin=self.drone_radius):
                errors.append(f"path point {index} violates boundary/floor/ceiling")
                break
            if any(obstacle.clearance(point) + 1e-7 < required_clearance for obstacle in course.volume.obstacles):
                errors.append(f"path point {index} lacks obstacle clearance")
                break

        segment_vectors = np.diff(points, axis=0)
        segment_lengths = np.linalg.norm(segment_vectors, axis=1)
        for index, (start, end) in enumerate(zip(points, points[1:])):
            if any(
                _segment_box_clearance(start, end, obstacle) + 1e-7 < required_clearance
                for obstacle in course.volume.obstacles
            ):
                errors.append(f"path segment {index} lacks obstacle clearance")
                break
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
                for j in range(i + 2, len(points) - 1):
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
            if gate.width + 1e-7 < 2 * self.drone_radius or gate.height + 1e-7 < 2 * self.drone_radius:
                errors.append(f"gate {gate.order} opening is too small for drone")
            if not all(course.volume.contains(corner, margin=self.drone_radius) for corner in corners):
                errors.append(f"gate {gate.order} opening violates volume boundary")
            if any(
                _gate_obstacle_clearance(gate, obstacle) + 1e-7 < self.drone_radius
                for obstacle in course.volume.obstacles
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
                if (
                    abs(right) > gate.width / 2 - self.drone_radius
                    or abs(up) > gate.height / 2 - self.drone_radius
                ):
                    errors.append(f"gate {gate.order} path misses its opening")

        metrics = difficulty_metrics(points, gates, course.volume)
        return ValidationReport(not errors, tuple(dict.fromkeys(errors)), metrics)
