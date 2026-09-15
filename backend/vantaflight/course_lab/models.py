"""Immutable data models used by the procedural course laboratory."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


Vector = tuple[float, float, float]


def vector3(value: Sequence[float], name: str = "vector") -> Vector:
    if len(value) != 3:
        raise ValueError(f"{name} must contain three values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain only finite values")
    return result  # type: ignore[return-value]


class CourseMode(str, Enum):
    RANDOM = "RANDOM"
    SLALOM = "SLALOM"
    VERTICAL = "VERTICAL"
    TECHNICAL = "TECHNICAL"
    SPEED_RUN = "SPEED_RUN"
    CHALLENGE = "CHALLENGE"
    ADVERSARY = "ADVERSARY"


@dataclass(frozen=True)
class BoxObstacle:
    """A simple axis-aligned box obstacle."""

    center: Vector
    size: Vector
    name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "center", vector3(self.center, "obstacle center"))
        object.__setattr__(self, "size", vector3(self.size, "obstacle size"))
        if any(item <= 0 for item in self.size):
            raise ValueError("obstacle size must be positive")

    @property
    def lower(self) -> np.ndarray:
        return np.asarray(self.center) - np.asarray(self.size) / 2.0

    @property
    def upper(self) -> np.ndarray:
        return np.asarray(self.center) + np.asarray(self.size) / 2.0

    def clearance(self, point: Sequence[float]) -> float:
        """Signed Euclidean clearance; negative values are inside the box."""
        p = np.asarray(point, dtype=float)
        outside = np.maximum(np.maximum(self.lower - p, p - self.upper), 0.0)
        distance = float(np.linalg.norm(outside))
        if distance > 0:
            return distance
        return -float(np.min(np.minimum(p - self.lower, self.upper - p)))

    def contains(self, point: Sequence[float], margin: float = 0.0) -> bool:
        p = np.asarray(point, dtype=float)
        return bool(np.all(p >= self.lower - margin) and np.all(p <= self.upper + margin))

    def to_dict(self) -> dict[str, Any]:
        return {"center": list(self.center), "name": self.name, "size": list(self.size)}


class SafeVolume:
    """User-defined rectangular flight volume with optional box obstacles.

    ``dimensions`` are width (x), depth (y), and nominal height (z). Explicit
    floor and ceiling values define the actual vertical interval.
    """

    def __init__(
        self,
        dimensions: Sequence[float] = (30.0, 60.0, 15.0),
        *,
        width: float | None = None,
        depth: float | None = None,
        height: float | None = None,
        floor: float = 0.0,
        ceiling: float | None = None,
        boundary_margin: float = 1.0,
        obstacles: Iterable[BoxObstacle | Mapping[str, Any] | Sequence[Any]] = (),
    ) -> None:
        dims = list(vector3(dimensions, "dimensions"))
        if width is not None:
            dims[0] = float(width)
        if depth is not None:
            dims[1] = float(depth)
        if height is not None:
            dims[2] = float(height)
        if any(not math.isfinite(v) or v <= 0 for v in dims):
            raise ValueError("dimensions must be finite and positive")
        floor = float(floor)
        ceiling = floor + dims[2] if ceiling is None else float(ceiling)
        margin = float(boundary_margin)
        if not all(math.isfinite(v) for v in (floor, ceiling, margin)):
            raise ValueError("volume bounds must be finite")
        if ceiling <= floor or margin < 0:
            raise ValueError("ceiling must exceed floor and margin must be nonnegative")
        if dims[0] <= margin * 2 or dims[1] <= margin * 2 or ceiling - floor <= margin * 2:
            raise ValueError("boundary margin leaves no usable flight volume")

        parsed: list[BoxObstacle] = []
        for obstacle in obstacles:
            if isinstance(obstacle, BoxObstacle):
                parsed.append(obstacle)
            elif isinstance(obstacle, Mapping):
                parsed.append(
                    BoxObstacle(
                        center=obstacle["center"],
                        size=obstacle["size"],
                        name=str(obstacle.get("name", "")),
                    )
                )
            else:
                center, size = obstacle
                parsed.append(BoxObstacle(center=center, size=size))

        self.dimensions = tuple(dims)
        self.floor = floor
        self.ceiling = ceiling
        self.boundary_margin = margin
        self.obstacles = tuple(parsed)

    @property
    def width(self) -> float:
        return self.dimensions[0]

    @property
    def depth(self) -> float:
        return self.dimensions[1]

    @property
    def height(self) -> float:
        return self.ceiling - self.floor

    @property
    def lower(self) -> Vector:
        return (-self.width / 2, -self.depth / 2, self.floor)

    @property
    def upper(self) -> Vector:
        return (self.width / 2, self.depth / 2, self.ceiling)

    @property
    def usable_lower(self) -> np.ndarray:
        return np.asarray(self.lower) + self.boundary_margin

    @property
    def usable_upper(self) -> np.ndarray:
        return np.asarray(self.upper) - self.boundary_margin

    def contains(self, point: Sequence[float], margin: float = 0.0) -> bool:
        p = np.asarray(point, dtype=float)
        low = np.asarray(self.lower) + self.boundary_margin + margin
        high = np.asarray(self.upper) - self.boundary_margin - margin
        return bool(np.all(np.isfinite(p)) and np.all(p >= low) and np.all(p <= high))

    def clearance(self, point: Sequence[float]) -> float:
        p = np.asarray(point, dtype=float)
        boundary = float(np.min(np.minimum(p - self.usable_lower, self.usable_upper - p)))
        obstacle = min((item.clearance(p) for item in self.obstacles), default=math.inf)
        return min(boundary, obstacle)

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_margin": self.boundary_margin,
            "ceiling": self.ceiling,
            "dimensions": list(self.dimensions),
            "floor": self.floor,
            "obstacles": [item.to_dict() for item in self.obstacles],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SafeVolume":
        return cls(
            value["dimensions"],
            floor=value["floor"],
            ceiling=value["ceiling"],
            boundary_margin=value["boundary_margin"],
            obstacles=value.get("obstacles", ()),
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SafeVolume) and self.to_dict() == other.to_dict()


@dataclass(frozen=True)
class Gate:
    center: Vector
    size: tuple[float, float]
    yaw: float
    pitch: float
    roll: float
    normal: Vector
    entry: Vector
    exit: Vector
    order: int

    def __post_init__(self) -> None:
        for name in ("center", "normal", "entry", "exit"):
            object.__setattr__(self, name, vector3(getattr(self, name), name))
        size = tuple(float(item) for item in self.size)
        if len(size) != 2 or any(not math.isfinite(item) or item <= 0 for item in size):
            raise ValueError("gate size must contain two positive finite values")
        object.__setattr__(self, "size", size)
        normal = np.asarray(self.normal)
        length = float(np.linalg.norm(normal))
        if length < 1e-9:
            raise ValueError("gate normal cannot be zero")
        if not math.isclose(length, 1.0, rel_tol=0.0, abs_tol=1e-12):
            object.__setattr__(self, "normal", vector3(normal / length))
        if self.order < 0:
            raise ValueError("gate order must be nonnegative")
        if not all(math.isfinite(item) for item in (self.yaw, self.pitch, self.roll)):
            raise ValueError("gate orientation must be finite")

    @property
    def width(self) -> float:
        return self.size[0]

    @property
    def height(self) -> float:
        return self.size[1]

    def frame(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        normal = np.asarray(self.normal)
        right = np.array([-math.sin(self.yaw), math.cos(self.yaw), 0.0])
        if np.linalg.norm(right) < 1e-9:
            right = np.array([1.0, 0.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(normal, right)
        up /= np.linalg.norm(up)
        c, s = math.cos(self.roll), math.sin(self.roll)
        return normal, c * right + s * up, -s * right + c * up

    def opening_coordinates(self, point: Sequence[float]) -> tuple[float, float, float]:
        normal, right, up = self.frame()
        relative = np.asarray(point) - np.asarray(self.center)
        return (
            float(np.dot(relative, normal)),
            float(np.dot(relative, right)),
            float(np.dot(relative, up)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": list(self.center),
            "entry": list(self.entry),
            "exit": list(self.exit),
            "normal": list(self.normal),
            "order": self.order,
            "pitch": self.pitch,
            "roll": self.roll,
            "size": list(self.size),
            "yaw": self.yaw,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Gate":
        return cls(**{key: value[key] for key in (
            "center", "size", "yaw", "pitch", "roll", "normal", "entry", "exit", "order"
        )})


@dataclass(frozen=True)
class Course:
    mode: CourseMode
    seed: int
    volume: SafeVolume
    path: tuple[Vector, ...]
    gates: tuple[Gate, ...]
    difficulty: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", CourseMode(self.mode))
        object.__setattr__(self, "path", tuple(vector3(item, "path point") for item in self.path))
        object.__setattr__(
            self,
            "difficulty",
            {str(key): float(value) for key, value in sorted(self.difficulty.items())},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": dict(self.difficulty),
            "gates": [gate.to_dict() for gate in self.gates],
            "mode": self.mode.value,
            "path": [list(point) for point in self.path],
            "seed": self.seed,
            "volume": self.volume.to_dict(),
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True)

    to_json = serialize

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Course":
        return cls(
            mode=CourseMode(value["mode"]),
            seed=int(value["seed"]),
            volume=SafeVolume.from_dict(value["volume"]),
            path=tuple(value["path"]),
            gates=tuple(Gate.from_dict(item) for item in value["gates"]),
            difficulty=value.get("difficulty", {}),
        )

    @classmethod
    def deserialize(cls, value: str) -> "Course":
        return cls.from_dict(json.loads(value))

    from_json = deserialize
