"""Typed, serializable concepts shared by the NumPy/OpenCV vision stack."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

import numpy as np


def _array(value: Any, shape: tuple[int, ...] | None = None) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if shape is not None and result.shape != shape:
        raise ValueError(f"expected array shape {shape}, got {result.shape}")
    if not np.all(np.isfinite(result)):
        raise ValueError("array must contain finite values")
    return result


class CoordinateFrame(str, Enum):
    CAMERA = "camera"  # OpenCV optical: +x right, +y down, +z forward
    BODY = "body"  # Aircraft FRD: +x forward, +y right, +z down
    WORLD = "world"  # Local NED: +x north, +y east, +z down


@dataclass(frozen=True, init=False)
class CameraProfile:
    camera_id: str
    resolution: tuple[int, int] | None
    fps: float
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: np.ndarray
    horizontal_fov_deg: float
    vertical_fov_deg: float
    mount_transform: np.ndarray
    capture_latency_s: float
    calibration_version: str

    def __init__(
        self,
        camera_matrix: np.ndarray | None = None,
        distortion: np.ndarray | None = None,
        resolution: tuple[int, int] | None = None,
        camera_to_body: np.ndarray | None = None,
        *,
        camera_id: str = "camera",
        fps: float = 30.0,
        fx: float | None = None,
        fy: float | None = None,
        cx: float | None = None,
        cy: float | None = None,
        matrix: np.ndarray | None = None,
        horizontal_fov_deg: float | None = None,
        vertical_fov_deg: float | None = None,
        mount_transform: np.ndarray | None = None,
        capture_latency_s: float = 0.0,
        calibration_version: str = "uncalibrated",
    ) -> None:
        supplied = matrix if matrix is not None else camera_matrix
        if supplied is None:
            if None in (fx, fy, cx, cy):
                raise ValueError("camera matrix or fx/fy/cx/cy must be provided")
            supplied = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
        intrinsic = _array(supplied)
        if intrinsic.shape != (3, 3):
            raise ValueError("camera_matrix must be a finite 3x3 matrix")
        if intrinsic[0, 0] <= 0 or intrinsic[1, 1] <= 0 or intrinsic[2, 2] == 0:
            raise ValueError("camera_matrix must have positive focal lengths")
        coefficients = _array(np.zeros(5) if distortion is None else distortion).reshape(-1)
        if coefficients.size not in (4, 5, 8, 12, 14):
            raise ValueError("distortion must be a finite OpenCV coefficient vector")
        transform = _array(
            mount_transform if mount_transform is not None
            else camera_to_body if camera_to_body is not None else np.eye(4),
            (4, 4),
        )
        if not np.allclose(transform[3], (0, 0, 0, 1)):
            raise ValueError("mount_transform must be homogeneous")
        if resolution is not None and (len(resolution) != 2 or min(resolution) <= 0):
            raise ValueError("resolution must contain positive width and height")
        if fps <= 0 or capture_latency_s < 0:
            raise ValueError("fps must be positive and capture latency non-negative")
        width, height = resolution or (int(2 * intrinsic[0, 2]), int(2 * intrinsic[1, 2]))
        hfov = horizontal_fov_deg if horizontal_fov_deg is not None else np.degrees(
            2 * np.arctan(width / (2 * intrinsic[0, 0]))
        )
        vfov = vertical_fov_deg if vertical_fov_deg is not None else np.degrees(
            2 * np.arctan(height / (2 * intrinsic[1, 1]))
        )
        for name, value in {
            "camera_id": camera_id, "resolution": resolution, "fps": float(fps),
            "fx": float(intrinsic[0, 0]), "fy": float(intrinsic[1, 1]),
            "cx": float(intrinsic[0, 2]), "cy": float(intrinsic[1, 2]),
            "distortion": coefficients, "horizontal_fov_deg": float(hfov),
            "vertical_fov_deg": float(vfov), "mount_transform": transform,
            "capture_latency_s": float(capture_latency_s),
            "calibration_version": calibration_version,
        }.items():
            object.__setattr__(self, name, value)

    @property
    def matrix(self) -> np.ndarray:
        return np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.]], dtype=np.float64)

    @property
    def camera_matrix(self) -> np.ndarray:
        return self.matrix

    @property
    def camera_to_body(self) -> np.ndarray:
        return self.mount_transform

    @property
    def width(self) -> int | None:
        return None if self.resolution is None else self.resolution[0]

    @property
    def height(self) -> int | None:
        return None if self.resolution is None else self.resolution[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id, "resolution": self.resolution, "fps": self.fps,
            "fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy,
            "matrix": self.matrix.tolist(), "distortion": self.distortion.tolist(),
            "horizontal_fov_deg": self.horizontal_fov_deg,
            "vertical_fov_deg": self.vertical_fov_deg,
            "mount_transform": self.mount_transform.tolist(),
            "capture_latency_s": self.capture_latency_s,
            "calibration_version": self.calibration_version,
        }


@dataclass(frozen=True, init=False)
class FramePacket:
    frame_id: str
    capture_timestamp: float
    receive_timestamp: float
    image: np.ndarray
    camera_source: str
    camera_profile: CameraProfile | None
    session_id: str | None
    sequence: int
    metadata: Mapping[str, Any]

    def __init__(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
        sequence: int = 0,
        source_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        *,
        frame_id: str | None = None,
        capture_timestamp: float | None = None,
        receive_timestamp: float | None = None,
        camera_source: str | None = None,
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
            raise ValueError("image must be a 2D or 3D numpy array")
        capture = capture_timestamp if capture_timestamp is not None else timestamp
        if capture is None or not np.isfinite(capture):
            raise ValueError("capture_timestamp must be finite")
        receive = capture if receive_timestamp is None else receive_timestamp
        if not np.isfinite(receive) or receive < capture:
            raise ValueError("receive_timestamp must be finite and not precede capture")
        if sequence < 0:
            raise ValueError("sequence must be non-negative")
        source = camera_source or source_id or "camera"
        values = {
            "frame_id": frame_id or f"{source}:{sequence}:{uuid4().hex}",
            "capture_timestamp": float(capture), "receive_timestamp": float(receive),
            "image": image, "camera_source": source, "camera_profile": camera_profile,
            "session_id": session_id, "sequence": sequence, "metadata": metadata or {},
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def timestamp(self) -> float:
        return self.capture_timestamp

    @property
    def source_id(self) -> str:
        return self.camera_source

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def to_dict(self, include_image: bool = False) -> dict[str, Any]:
        result = {
            "frame_id": self.frame_id, "capture_timestamp": self.capture_timestamp,
            "receive_timestamp": self.receive_timestamp, "width": self.width,
            "height": self.height, "camera_source": self.camera_source,
            "camera_profile": None if self.camera_profile is None else self.camera_profile.to_dict(),
            "session_id": self.session_id, "sequence": self.sequence,
            "metadata": dict(self.metadata),
        }
        if include_image:
            result["image"] = self.image.tolist()
        return result


@dataclass(frozen=True, init=False)
class TargetProfile:
    profile_id: str
    name: str
    target_type: str
    physical_width_m: float
    physical_height_m: float
    expected_shape: str
    expected_corners: int
    object_points: np.ndarray
    hsv_lower: tuple[int, int, int] | None
    hsv_upper: tuple[int, int, int] | None
    color_ranges: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...]
    min_area_px: float
    max_area_px: float
    min_solidity: float
    aspect_ratio_range: tuple[float, float]
    orientation_range_deg: tuple[float, float]
    tolerance: float
    detector_config: Mapping[str, Any]
    pose_config: Mapping[str, Any]

    def __init__(
        self,
        name: str,
        hsv_lower: tuple[int, int, int] | None = None,
        hsv_upper: tuple[int, int, int] | None = None,
        object_points: np.ndarray | None = None,
        min_area_px: float = 100.0,
        max_area_px: float = float("inf"),
        polygon_vertices: int = 4,
        min_solidity: float = 0.75,
        aspect_ratio_range: tuple[float, float] = (0.4, 2.5),
        *,
        profile_id: str | None = None,
        target_type: str = "planar",
        physical_width_m: float | None = None,
        physical_height_m: float | None = None,
        expected_shape: str = "rectangle",
        expected_corners: int | None = None,
        color_ranges: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] | None = None,
        orientation_range_deg: tuple[float, float] = (-180.0, 180.0),
        tolerance: float = 0.1,
        detector_config: Mapping[str, Any] | None = None,
        pose_config: Mapping[str, Any] | None = None,
    ) -> None:
        if object_points is None:
            if physical_width_m is None or physical_height_m is None:
                raise ValueError("object_points or physical dimensions are required")
            w, h = physical_width_m / 2, physical_height_m / 2
            object_points = np.array([[-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]])
        points = _array(object_points)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4:
            raise ValueError("object_points must be Nx3 with at least four points")
        width = physical_width_m or float(np.ptp(points[:, 0]))
        height = physical_height_m or float(np.ptp(points[:, 1]))
        if width <= 0 or height <= 0:
            raise ValueError("target dimensions must be positive")
        ranges = color_ranges or (() if hsv_lower is None or hsv_upper is None else ((hsv_lower, hsv_upper),))
        for lower, upper in ranges:
            if len(lower) != 3 or len(upper) != 3 or any(lo > hi for lo, hi in zip(lower, upper)):
                raise ValueError("invalid HSV color range")
        if min_area_px < 0 or max_area_px < min_area_px:
            raise ValueError("invalid area limits")
        values = {
            "profile_id": profile_id or name, "name": name, "target_type": target_type,
            "physical_width_m": float(width), "physical_height_m": float(height),
            "expected_shape": expected_shape,
            "expected_corners": expected_corners or polygon_vertices,
            "object_points": points, "hsv_lower": hsv_lower, "hsv_upper": hsv_upper,
            "color_ranges": tuple(ranges), "min_area_px": float(min_area_px),
            "max_area_px": float(max_area_px), "min_solidity": float(min_solidity),
            "aspect_ratio_range": aspect_ratio_range,
            "orientation_range_deg": orientation_range_deg, "tolerance": float(tolerance),
            "detector_config": detector_config or {}, "pose_config": pose_config or {},
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def polygon_vertices(self) -> int:
        return self.expected_corners

    @classmethod
    def rectangle(
        cls, name: str, width_m: float, height_m: float,
        hsv_lower: tuple[int, int, int] | None = None,
        hsv_upper: tuple[int, int, int] | None = None, **kwargs: Any,
    ) -> "TargetProfile":
        return cls(
            name, hsv_lower, hsv_upper, physical_width_m=width_m,
            physical_height_m=height_m, expected_shape="rectangle", **kwargs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id, "name": self.name, "target_type": self.target_type,
            "physical_width_m": self.physical_width_m,
            "physical_height_m": self.physical_height_m,
            "expected_shape": self.expected_shape, "expected_corners": self.expected_corners,
            "object_points": self.object_points.tolist(), "color_ranges": self.color_ranges,
            "min_area_px": self.min_area_px, "max_area_px": self.max_area_px,
            "aspect_ratio_range": self.aspect_ratio_range,
            "orientation_range_deg": self.orientation_range_deg, "tolerance": self.tolerance,
            "detector_config": dict(self.detector_config), "pose_config": dict(self.pose_config),
        }


@dataclass(frozen=True, init=False)
class TargetCandidate:
    candidate_id: str
    frame_id: str | None
    profile: TargetProfile
    profile_id: str
    bounding_box: tuple[float, float, float, float]
    ordered_corners: np.ndarray
    contour: np.ndarray
    center: tuple[float, float]
    area_px: float
    color_confidence: float
    edge_confidence: float
    shape_confidence: float
    geometry_confidence: float
    detector_confidence: float
    timestamp: float
    source: str

    def __init__(
        self,
        profile: TargetProfile,
        corners: np.ndarray | None = None,
        contour: np.ndarray | None = None,
        centroid: tuple[float, float] | None = None,
        area_px: float = 0.0,
        score: float | None = None,
        timestamp: float = 0.0,
        source: str = "classical",
        *,
        candidate_id: str | None = None,
        frame_id: str | None = None,
        profile_id: str | None = None,
        bounding_box: tuple[float, float, float, float] | None = None,
        ordered_corners: np.ndarray | None = None,
        center: tuple[float, float] | None = None,
        color_confidence: float | None = None,
        edge_confidence: float | None = None,
        shape_confidence: float | None = None,
        geometry_confidence: float | None = None,
        detector_confidence: float | None = None,
    ) -> None:
        points = _array(ordered_corners if ordered_corners is not None else corners)
        if points.shape != (len(profile.object_points), 2):
            raise ValueError("candidate corners must correspond to object_points")
        contour_array = np.asarray(
            contour if contour is not None else np.rint(points).astype(np.int32).reshape(-1, 1, 2)
        )
        candidate_center = center or centroid or tuple(np.mean(points, axis=0))
        x0, y0 = points.min(axis=0)
        x1, y1 = points.max(axis=0)
        box = bounding_box or (float(x0), float(y0), float(x1 - x0), float(y1 - y0))
        base = float(score if detector_confidence is None else detector_confidence)
        if score is None and detector_confidence is None:
            base = 0.0
        confidence_values = [
            base if value is None else float(value)
            for value in (color_confidence, edge_confidence, shape_confidence, geometry_confidence)
        ]
        if any(not 0 <= value <= 1 for value in confidence_values + [base]):
            raise ValueError("candidate confidence scores must be in [0, 1]")
        values = {
            "candidate_id": candidate_id or uuid4().hex, "frame_id": frame_id,
            "profile": profile, "profile_id": profile_id or profile.profile_id,
            "bounding_box": tuple(float(value) for value in box), "ordered_corners": points,
            "contour": contour_array, "center": tuple(float(value) for value in candidate_center),
            "area_px": float(area_px), "color_confidence": confidence_values[0],
            "edge_confidence": confidence_values[1], "shape_confidence": confidence_values[2],
            "geometry_confidence": confidence_values[3], "detector_confidence": base,
            "timestamp": float(timestamp), "source": source,
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def corners(self) -> np.ndarray:
        return self.ordered_corners

    @property
    def centroid(self) -> tuple[float, float]:
        return self.center

    @property
    def score(self) -> float:
        return self.detector_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "frame_id": self.frame_id,
            "profile_id": self.profile_id, "bounding_box": self.bounding_box,
            "ordered_corners": self.ordered_corners.tolist(), "center": self.center,
            "area_px": self.area_px, "color_confidence": self.color_confidence,
            "edge_confidence": self.edge_confidence, "shape_confidence": self.shape_confidence,
            "geometry_confidence": self.geometry_confidence,
            "detector_confidence": self.detector_confidence,
            "timestamp": self.timestamp, "source": self.source,
        }


@dataclass(frozen=True)
class PoseEstimate:
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
class ObservedPose3D:
    timestamp: float
    position_camera_m: np.ndarray
    position_body_m: np.ndarray
    position_world_m: np.ndarray
    rotation_vector: np.ndarray
    uncertainty: np.ndarray

    def __post_init__(self) -> None:
        for name in ("position_camera_m", "position_body_m", "position_world_m", "rotation_vector"):
            object.__setattr__(self, name, _array(getattr(self, name), (3,)))
        object.__setattr__(self, "uncertainty", _array(self.uncertainty, (3, 3)))


@dataclass(frozen=True)
class PredictedPose3D:
    timestamp: float
    horizon_s: float
    position_world_m: np.ndarray
    velocity_world_mps: np.ndarray
    uncertainty: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_world_m", _array(self.position_world_m, (3,)))
        object.__setattr__(self, "velocity_world_mps", _array(self.velocity_world_mps, (3,)))
        object.__setattr__(self, "uncertainty", _array(self.uncertainty, (3, 3)))


@dataclass(frozen=True)
class AircraftState:
    timestamp: float
    position_ned_m: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_ned_mps: np.ndarray = field(default_factory=lambda: np.zeros(3))
    body_to_world: np.ndarray = field(default_factory=lambda: np.eye(4))

    def __post_init__(self) -> None:
        position = _array(self.position_ned_m).reshape(-1)
        velocity = _array(self.velocity_ned_mps).reshape(-1)
        transform = _array(self.body_to_world, (4, 4))
        if position.shape != (3,) or velocity.shape != (3,):
            raise ValueError("position and velocity must be 3-vectors")
        if not np.allclose(transform[3], (0, 0, 0, 1)):
            raise ValueError("body_to_world must be homogeneous")
        object.__setattr__(self, "position_ned_m", position)
        object.__setattr__(self, "velocity_ned_mps", velocity)
        object.__setattr__(self, "body_to_world", transform)
