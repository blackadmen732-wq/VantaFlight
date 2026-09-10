"""Deterministic synthetic planar-target imagery for tests and simulation."""
from __future__ import annotations

import cv2
import numpy as np


def render_target_image(
    size: tuple[int, int] = (640, 480),
    corners: np.ndarray | None = None,
    *,
    bgr: tuple[int, int, int] = (0, 255, 0),
    background_bgr: tuple[int, int, int] = (16, 16, 16),
    noise_std: float = 0.0,
    seed: int = 0,
    border_px: int = 0,
) -> np.ndarray:
    """Render a filled quadrilateral with repeatable Gaussian sensor noise."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("size must be positive")
    if corners is None:
        corners = np.array(
            [[width * 0.35, height * 0.3], [width * 0.65, height * 0.3],
             [width * 0.65, height * 0.7], [width * 0.35, height * 0.7]],
            dtype=np.float32,
        )
    corners = np.asarray(corners, dtype=np.float32)
    if corners.shape != (4, 2):
        raise ValueError("corners must be 4x2")
    image = np.full((height, width, 3), background_bgr, dtype=np.uint8)
    polygon = np.rint(corners).astype(np.int32)
    cv2.fillConvexPoly(image, polygon, bgr)
    if border_px > 0:
        cv2.polylines(image, [polygon], True, (255, 255, 255), border_px)
    if noise_std > 0:
        noise = np.random.default_rng(seed).normal(0, noise_std, image.shape)
        image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return image


def project_planar_target(
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    rotation_vector: np.ndarray,
    translation_vector: np.ndarray,
    distortion: np.ndarray | None = None,
) -> np.ndarray:
    points, _ = cv2.projectPoints(
        np.asarray(object_points, np.float64),
        np.asarray(rotation_vector, np.float64),
        np.asarray(translation_vector, np.float64),
        np.asarray(camera_matrix, np.float64),
        np.zeros(5) if distortion is None else np.asarray(distortion, np.float64),
    )
    return points.reshape(-1, 2)
