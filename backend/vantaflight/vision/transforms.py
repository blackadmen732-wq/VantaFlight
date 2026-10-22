"""Explicit camera-optical, aircraft-FRD, and world-NED transforms.

All transforms are 4x4 homogeneous matrices and map column vectors from the
named source frame to destination frame. No flight-SDK types are imported.
"""
from __future__ import annotations

import numpy as np

from .concepts import AircraftState, CameraProfile


def transform_point(transform: np.ndarray, point: np.ndarray) -> np.ndarray:
    matrix = np.asarray(transform, np.float64)
    vector = np.asarray(point, np.float64).reshape(3)
    if matrix.shape != (4, 4):
        raise ValueError("transform must be 4x4")
    homogeneous = matrix @ np.append(vector, 1.0)
    if abs(homogeneous[3]) < 1e-12:
        raise ValueError("point transforms to infinity")
    return homogeneous[:3] / homogeneous[3]


def invert_transform(transform: np.ndarray) -> np.ndarray:
    matrix = np.asarray(transform, np.float64)
    if matrix.shape != (4, 4):
        raise ValueError("transform must be 4x4")
    return np.linalg.inv(matrix)


def camera_to_body(point_camera_m: np.ndarray, camera: CameraProfile) -> np.ndarray:
    """Convert OpenCV optical (+right,+down,+forward) into configured body FRD."""
    return transform_point(camera.camera_to_body, point_camera_m)


def body_to_world(point_body_m: np.ndarray, aircraft: AircraftState) -> np.ndarray:
    """Convert aircraft-relative FRD into NED, including aircraft position."""
    return (
        transform_point(aircraft.body_to_world, point_body_m)
        + aircraft.position_ned_m
    )


def camera_to_world(
    point_camera_m: np.ndarray,
    camera: CameraProfile,
    aircraft: AircraftState,
) -> np.ndarray:
    return body_to_world(camera_to_body(point_camera_m, camera), aircraft)


def default_optical_to_frd() -> np.ndarray:
    """Standard forward camera mounting: optical z→body x, x→y, y→z."""
    return np.array(
        [[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]],
        dtype=np.float64,
    )
