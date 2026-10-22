"""Deterministic synthetic planar-target imagery for tests and simulation."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class SyntheticTargetSpec:
    corners: np.ndarray
    bgr: tuple[int, int, int] = (0, 255, 0)
    brightness: float = 1.0
    occlusion_fraction: float = 0.0


def render_synthetic_scene(
    size: tuple[int, int] = (640, 480),
    targets: tuple[SyntheticTargetSpec, ...] = (),
    *,
    background_bgr: tuple[int, int, int] = (16, 16, 16),
    background: np.ndarray | None = None,
    noise_std: float = 0.0,
    motion_blur_px: int = 0,
    seed: int = 0,
) -> np.ndarray:
    """Render deterministic multi-target scenes with common camera artifacts."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("size must be positive")
    if background is None:
        image = np.full((height, width, 3), background_bgr, dtype=np.uint8)
    else:
        image = np.asarray(background, dtype=np.uint8).copy()
        if image.shape != (height, width, 3):
            raise ValueError("background must match the requested scene size")
    for target in targets:
        corners = np.asarray(target.corners, dtype=np.float32)
        if corners.shape != (4, 2):
            raise ValueError("target corners must be 4x2")
        if target.brightness < 0 or not 0 <= target.occlusion_fraction < 1:
            raise ValueError("brightness must be nonnegative and occlusion in [0, 1)")
        polygon = np.rint(corners).astype(np.int32)
        color = tuple(
            int(value)
            for value in np.clip(np.asarray(target.bgr) * target.brightness, 0, 255)
        )
        cv2.fillConvexPoly(image, polygon, color)
        if target.occlusion_fraction:
            x, y, w, h = cv2.boundingRect(polygon)
            occlusion_width = max(1, int(round(w * target.occlusion_fraction)))
            image[y : y + h, x + w - occlusion_width : x + w] = background_bgr
    if motion_blur_px > 1:
        kernel = np.zeros((1, motion_blur_px), dtype=np.float32)
        kernel[0] = 1.0 / motion_blur_px
        image = cv2.filter2D(image, -1, kernel)
    if noise_std > 0:
        noise = np.random.default_rng(seed).normal(0, noise_std, image.shape)
        image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return image


def render_target_image(
    size: tuple[int, int] = (640, 480),
    corners: np.ndarray | None = None,
    *,
    bgr: tuple[int, int, int] = (0, 255, 0),
    background_bgr: tuple[int, int, int] = (16, 16, 16),
    noise_std: float = 0.0,
    seed: int = 0,
    border_px: int = 0,
    brightness: float = 1.0,
    occlusion_fraction: float = 0.0,
    motion_blur_px: int = 0,
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
    image = render_synthetic_scene(
        size,
        (
            SyntheticTargetSpec(
                corners,
                bgr=bgr,
                brightness=brightness,
                occlusion_fraction=occlusion_fraction,
            ),
        ),
        background_bgr=background_bgr,
        noise_std=noise_std,
        motion_blur_px=motion_blur_px,
        seed=seed,
    )
    polygon = np.rint(corners).astype(np.int32)
    if border_px > 0:
        cv2.polylines(image, [polygon], True, (255, 255, 255), border_px)
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
