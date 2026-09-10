"""Seeded, path-first procedural 3-D course generation."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

import numpy as np

from .models import Course, CourseMode, Gate, SafeVolume, Vector, vector3


@dataclass(frozen=True)
class GenerationConfig:
    gate_count: int = 12
    path_samples_per_gate: int = 14
    min_gate_size: float = 1.4
    max_gate_size: float = 3.6
    min_spacing: float = 2.0
    drone_radius: float = 0.25
    obstacle_clearance: float = 0.75
    gate_plane_offset: float = 0.35

    def __post_init__(self) -> None:
        if self.gate_count < 3:
            raise ValueError("gate_count must be at least 3")
        if self.path_samples_per_gate < 4:
            raise ValueError("path_samples_per_gate must be at least 4")
        if not 0 < self.min_gate_size <= self.max_gate_size:
            raise ValueError("gate size bounds are invalid")
        if self.min_spacing <= 0 or self.drone_radius < 0 or self.obstacle_clearance < 0:
            raise ValueError("spacing and clearances must be nonnegative")

    def with_parameters(self, values: Mapping[str, float | int]) -> "GenerationConfig":
        unknown = set(values) - set(self.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown generation parameters: {sorted(unknown)}")
        return replace(self, **values)


@dataclass(frozen=True)
class _Profile:
    lateral: float
    vertical: float
    lateral_cycles: float
    vertical_cycles: float
    gate_scale: float
    roll: float
    target_speed: float
    path_fraction: float
    jitter: float


_PROFILES: dict[CourseMode, _Profile] = {
    CourseMode.RANDOM: _Profile(.24, .20, 1.7, 1.3, .72, .12, 7.0, .84, .18),
    CourseMode.SLALOM: _Profile(.36, .06, 3.2, .7, .76, .05, 7.5, .88, .08),
    CourseMode.VERTICAL: _Profile(.12, .40, 1.0, 2.4, .75, .18, 6.0, .86, .08),
    CourseMode.TECHNICAL: _Profile(.31, .27, 3.7, 3.0, .54, .42, 5.0, .80, .12),
    CourseMode.SPEED_RUN: _Profile(.055, .035, .7, .5, .98, .01, 13.0, .94, .025),
    CourseMode.CHALLENGE: _Profile(.30, .31, 2.5, 2.7, .62, .30, 7.0, .84, .13),
    CourseMode.ADVERSARY: _Profile(.39, .34, 4.2, 3.6, .45, .62, 5.5, .78, .17),
}


class CourseGenerationError(RuntimeError):
    pass


def _unit(value: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(value))
    return value / length if length > 1e-12 else np.array([0.0, 1.0, 0.0])


def _angles(normal: np.ndarray) -> tuple[float, float]:
    return (
        math.atan2(float(normal[1]), float(normal[0])),
        math.atan2(float(normal[2]), float(np.linalg.norm(normal[:2]))),
    )


def _moving_average(points: np.ndarray, rounds: int = 2) -> np.ndarray:
    result = points.copy()
    for _ in range(rounds):
        result[1:-1] = (
            result[:-2] * 0.2 + result[1:-1] * 0.6 + result[2:] * 0.2
        )
    return result


class CourseGenerator:
    """Generate repeatable courses without touching global random state."""

    def __init__(self, volume: SafeVolume | None = None, config: GenerationConfig | None = None):
        self.volume = volume or SafeVolume()
        self.config = config or GenerationConfig()

    def generate(
        self,
        mode: CourseMode | str = CourseMode.RANDOM,
        *,
        seed: int = 0,
        gate_count: int | None = None,
        config: GenerationConfig | None = None,
    ) -> Course:
        mode = CourseMode(mode)
        cfg = config or self.config
        if gate_count is not None:
            cfg = replace(cfg, gate_count=int(gate_count))
        rng = np.random.default_rng(int(seed))
        profile = _PROFILES[mode]
        path = self._make_path(mode, profile, cfg, rng)
        gates = self._sample_gates(path, mode, profile, cfg, rng)

        # Imported lazily to keep model/generator imports acyclic.
        from .validation import CourseValidator, difficulty_metrics

        difficulty = difficulty_metrics(path, gates, self.volume, profile.target_speed)
        course = Course(
            mode=mode,
            seed=int(seed),
            volume=self.volume,
            path=tuple(vector3(point) for point in path),
            gates=tuple(gates),
            difficulty=difficulty,
        )
        report = CourseValidator(
            min_spacing=min(cfg.min_spacing, self._expected_spacing(path, cfg) * 0.45),
            drone_radius=cfg.drone_radius,
            obstacle_clearance=cfg.obstacle_clearance,
        ).validate(course)
        if not report.valid:
            raise CourseGenerationError("; ".join(report.errors))
        return course

    @staticmethod
    def _expected_spacing(path: np.ndarray, cfg: GenerationConfig) -> float:
        return float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum() / (cfg.gate_count - 1))

    def _make_path(
        self,
        mode: CourseMode,
        profile: _Profile,
        cfg: GenerationConfig,
        rng: np.random.Generator,
    ) -> np.ndarray:
        count = max(cfg.gate_count * cfg.path_samples_per_gate, 48)
        t = np.linspace(0.0, 1.0, count)
        low, high = self.volume.usable_lower.copy(), self.volume.usable_upper.copy()
        span = high - low
        center = (low + high) / 2.0
        phase = rng.uniform(0.0, 2 * math.pi, 4)

        y_half = span[1] * profile.path_fraction / 2.0
        y = center[1] + (2.0 * t - 1.0) * y_half
        envelope = np.sin(math.pi * t) ** 0.7
        x = center[0] + span[0] * profile.lateral * envelope * (
            .74 * np.sin(2 * math.pi * profile.lateral_cycles * t + phase[0])
            + .26 * np.sin(2 * math.pi * (profile.lateral_cycles + .67) * t + phase[1])
        )
        z = center[2] + span[2] * profile.vertical * envelope * (
            .72 * np.sin(2 * math.pi * profile.vertical_cycles * t + phase[2])
            + .28 * np.sin(2 * math.pi * (profile.vertical_cycles + .5) * t + phase[3])
        )

        if mode is CourseMode.SLALOM:
            z = center[2] + span[2] * .025 * np.sin(2 * math.pi * t + phase[2])
        elif mode is CourseMode.SPEED_RUN:
            x = center[0] + (x - center[0]) * .45
            z = center[2] + (z - center[2]) * .35

        # Low-frequency seeded perturbations preserve smoothness and make
        # different seeds distinct without introducing discontinuities.
        harmonics = np.zeros((count, 2))
        for frequency in (1, 2, 3):
            amplitude = profile.jitter / frequency
            harmonics[:, 0] += amplitude * np.sin(
                2 * math.pi * frequency * t + rng.uniform(0, 2 * math.pi)
            )
            harmonics[:, 1] += amplitude * np.sin(
                2 * math.pi * frequency * t + rng.uniform(0, 2 * math.pi)
            )
        x += span[0] * .14 * envelope * harmonics[:, 0]
        z += span[2] * .12 * envelope * harmonics[:, 1]

        # Path-first obstacle avoidance adds a broad, smooth lateral detour.
        clearance = cfg.obstacle_clearance + cfg.drone_radius
        gate_clearance = max(
            clearance,
            cfg.max_gate_size / math.sqrt(2.0) + cfg.drone_radius + .2,
        )
        for index, obstacle in enumerate(self.volume.obstacles):
            obstacle_half = np.asarray(obstacle.size) / 2
            inner = obstacle_half[1] + gate_clearance
            outer = inner + max(4.0, self.volume.depth * .10)
            blend = np.clip((outer - np.abs(y - obstacle.center[1])) / (outer - inner), 0.0, 1.0)
            weight = blend * blend * (3.0 - 2.0 * blend)
            side = 1.0 if math.sin(phase[index % len(phase)] + index) >= 0 else -1.0
            target = obstacle.center[0] + side * (obstacle_half[0] + gate_clearance + .35)
            x = x * (1.0 - weight) + target * weight

        path = np.column_stack((x, y, z))
        path = _moving_average(path)
        path[:, 0] = np.clip(path[:, 0], low[0], high[0])
        path[:, 1] = np.clip(path[:, 1], low[1], high[1])
        path[:, 2] = np.clip(path[:, 2], low[2], high[2])

        # If smoothing left a sample in an obstacle, project it laterally and
        # smooth its neighbours with a compact five-point blend.
        for obstacle in self.volume.obstacles:
            half = np.asarray(obstacle.size) / 2
            for i, point in enumerate(path):
                if obstacle.contains(point, gate_clearance):
                    choices = (
                        obstacle.center[0] - half[0] - gate_clearance,
                        obstacle.center[0] + half[0] + gate_clearance,
                    )
                    target = min(choices, key=lambda value: abs(value - point[0]))
                    for j in range(max(0, i - 2), min(count, i + 3)):
                        blend = 1.0 - abs(j - i) / 3.0
                        path[j, 0] = path[j, 0] * (1 - blend) + target * blend
        path[:, 0] = np.clip(path[:, 0], low[0], high[0])
        return path

    def _sample_gates(
        self,
        path: np.ndarray,
        mode: CourseMode,
        profile: _Profile,
        cfg: GenerationConfig,
        rng: np.random.Generator,
    ) -> list[Gate]:
        segment_lengths = np.linalg.norm(np.diff(path, axis=0), axis=1)
        cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))
        targets = np.linspace(cumulative[-1] * .035, cumulative[-1] * .965, cfg.gate_count)
        indices = np.searchsorted(cumulative, targets).clip(2, len(path) - 3)
        gates: list[Gate] = []
        base_size = cfg.min_gate_size + (
            cfg.max_gate_size - cfg.min_gate_size
        ) * profile.gate_scale

        for order, index in enumerate(indices):
            center = path[index]
            entry = path[index - 2]
            exit = path[index + 2]
            normal = _unit(exit - entry)
            yaw, pitch = _angles(normal)
            oscillation = math.sin(order * 1.71 + rng.uniform(-.15, .15))
            roll = profile.roll * oscillation
            if mode is CourseMode.VERTICAL:
                roll += .12 * math.sin(order)
            size_jitter = 1.0 + rng.uniform(-.09, .09)
            width = float(np.clip(base_size * size_jitter, cfg.min_gate_size, cfg.max_gate_size))
            height = float(np.clip(width * rng.uniform(.90, 1.10), cfg.min_gate_size, cfg.max_gate_size))

            # Keep the complete opening inside the usable volume. The
            # conservative radial clamp works for any yaw/pitch/roll.
            boundary_clearance = float(np.min(np.minimum(
                center - self.volume.usable_lower,
                self.volume.usable_upper - center,
            )))
            max_size = max(cfg.min_gate_size, 2 * boundary_clearance * .86)
            width = min(width, max_size)
            height = min(height, max_size)
            gates.append(
                Gate(
                    center=vector3(center),
                    size=(width, height),
                    yaw=yaw,
                    pitch=pitch,
                    roll=roll,
                    normal=vector3(normal),
                    entry=vector3(entry),
                    exit=vector3(exit),
                    order=order,
                )
            )
        return gates
