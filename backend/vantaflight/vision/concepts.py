"""Core, dependency-light concepts shared by the vision stack."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

import numpy as np


class CoordinateFrame(str, Enum):
    """Named right-handed frames used by VantaFlight vision."""

    CAMERA = "camera"  # OpenCV: +x right, +y down, +z forward
    BODY = "body"  # Aircraft FRD: +x forward, +y right, +z down
    WORLD = "world"  # Local NED: +x north, +y east, +z down


@dataclass(frozen=True)
class CameraProfile:
    """Camera calibration and rigid camera-to-body transform."""

    camera_matrix: np.ndarray
    distortion: np.ndarray = field(default_factory=lambda: np.zeros(5))
    resolution: tuple[int, int] | None = None
    camera_to_body: np.ndarray = field(default_factory=lambda: np.eye(4))

    def __post_init__(self) -> None:
        matrix = np.asarray(self.camera_matrix, dtype=np.float64)
        distortion = np.asarray(self.distortion, dtype=np.float64).reshape(-1)
        transform = np.asarray(self.camera_to_body, dtype=np.float64)
        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            raise ValueError("camera_matrix must be a finite 3x3 matrix")
        if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or matrix[2, 2] == 0:
            raise ValueError("camera_matrix must have positive focal lengths")
        if distortion.size not in (4, 5, 8, 12, 14) or not np.all(np.isfinite(distortion)):
            raise ValueError("distortion must be a finite OpenCV coefficient vector")
        if transform.shape != (4, 4) or not np.all(np.isfinite(transform)):
            raise ValueError("camera_to_body must be a finite 4x4 transform")
        if not np.allclose(transform[3], (0, 0, 0, 1), atol=1e-8):
            raise ValueError("camera_to_body must be homogeneous")
        if self.resolution is not None and min(self.resolution) <= 0:
            raise ValueError("resolution must contain positive width and height")
        object.__setattr__(self, "camera_matrix", matrix)
        object.__setattr__(self, "distortion", distortion)
        object.__setattr__(self, "camera_to_body", transform)


@dataclass(frozen=True)
class FramePacket:
    """An immutable capture envelope; image storage itself is not copied."""

    image: np.ndarray
    timestamp: float
    sequence: int
    source_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.image, np.ndarray) or self.image.ndim not in (2, 3):
            raise ValueError("image must be a 2D or 3D numpy array")
        if not np.isfinite(self.timestamp):
            raise ValueError("timestamp must be finite")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")


@dataclass(frozen=True)
class TargetProfile:
    """Reusable physical and appearance model for a planar target.

    ``object_points`` order must match detector corners. The conventional
    order is top-left, top-right, bottom-right, bottom-left as viewed.
    """

    name: str
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    object_points: np.ndarray
    min_area_px: float = 100.0
    max_area_px: float = float("inf")
    polygon_vertices: int = 4
    min_solidity: float = 0.75
    aspect_ratio_range: tuple[float, float] = (0.4, 2.5)

    def __post_init__(self) -> None:
        points = np.asarray(self.object_points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4:
            raise ValueError("object_points must be Nx3 with at least four points")
        if len(self.hsv_lower) != 3 or len(self.hsv_upper) != 3:
            raise ValueError("HSV bounds must have three components")
        if any(lo > hi for lo, hi in zip(self.hsv_lower, self.hsv_upper)):
            raise ValueError("each HSV lower bound must not exceed its upper bound")
        if self.min_area_px < 0 or self.max_area_px < self.min_area_px:
            raise ValueError("invalid area limits")
        object.__setattr__(self, "object_points", points)

    @classmethod
    def rectangle(
        cls,
        name: str,
        width_m: float,
        height_m: float,
        hsv_lower: tuple[int, int, int],
        hsv_upper: tuple[int, int, int],
        **kwargs: Any,
    ) -> "TargetProfile":
        if width_m <= 0 or height_m <= 0:
            raise ValueError("target dimensions must be positive")
        w, h = width_m / 2, height_m / 2
        # Clockwise TL, TR, BR, BL in the target's z=0 plane.
        points = np.array([[-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]])
        return cls(name, hsv_lower, hsv_upper, points, **kwargs)


@dataclass(frozen=True)
class TargetCandidate:
    profile: TargetProfile
    corners: np.ndarray
    contour: np.ndarray
    centroid: tuple[float, float]
    area_px: float
    score: float
    timestamp: float
    source: str = "classical"

    def __post_init__(self) -> None:
        corners = np.asarray(self.corners, dtype=np.float64)
        if corners.shape != (len(self.profile.object_points), 2):
            raise ValueError("candidate corners must correspond to object_points")
        if not np.all(np.isfinite(corners)):
            raise ValueError("candidate corners must be finite")
        object.__setattr__(self, "corners", corners)
        object.__setattr__(self, "contour", np.asarray(self.contour))


@dataclass(frozen=True)
class PoseEstimate:
    """Target pose expressed in OpenCV camera coordinates."""

    rotation_vector: np.ndarray
    translation_vector: np.ndarray
    reprojection_error_px: float
    inliers: np.ndarray | None
    raw_success: bool
    valid: bool
    reason: str = ""

    @property
    def distance_m(self) -> float:
        return float(np.linalg.norm(self.translation_vector))


@dataclass(frozen=True)
class AircraftState:
    """Normalized aircraft state in local NED/FRD coordinates (SI units)."""

    timestamp: float
    position_ned_m: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_ned_mps: np.ndarray = field(default_factory=lambda: np.zeros(3))
    body_to_world: np.ndarray = field(default_factory=lambda: np.eye(4))

    def __post_init__(self) -> None:
        position = np.asarray(self.position_ned_m, dtype=np.float64).reshape(-1)
        velocity = np.asarray(self.velocity_ned_mps, dtype=np.float64).reshape(-1)
        transform = np.asarray(self.body_to_world, dtype=np.float64)
        if position.shape != (3,) or velocity.shape != (3,):
            raise ValueError("position and velocity must be 3-vectors")
        if transform.shape != (4, 4) or not np.allclose(transform[3], (0, 0, 0, 1)):
            raise ValueError("body_to_world must be a homogeneous 4x4 transform")
        object.__setattr__(self, "position_ned_m", position)
        object.__setattr__(self, "velocity_ned_mps", velocity)
        object.__setattr__(self, "body_to_world", transform)
