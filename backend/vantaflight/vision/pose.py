"""Planar target pose estimation and reprojection validation.

OpenCV returns target pose in the optical camera frame: +x image-right,
+y image-down, +z through the lens into the scene. ``rvec`` rotates points
from target coordinates into that camera frame; ``tvec`` is the target origin
in camera metres. Conversion to aircraft FRD or world NED is intentionally
performed by :mod:`vantaflight.vision.transforms`.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .concepts import CameraProfile, PoseEstimate, TargetCandidate


@dataclass(frozen=True)
class PoseConfig:
    use_ransac: bool = True
    ransac_reprojection_error_px: float = 4.0
    ransac_iterations: int = 100
    max_reprojection_error_px: float = 3.0
    min_depth_m: float = 0.02
    max_distance_m: float = 1000.0
    image_noise_floor_px: float = 0.5


@dataclass(frozen=True)
class PoseResult:
    """Both the numerical OpenCV result and the independently validated pose."""

    raw: PoseEstimate
    validated: PoseEstimate | None

    @property
    def valid(self) -> bool:
        return self.validated is not None


def _signed_area(points: np.ndarray) -> float:
    return float(np.dot(points[:, 0], np.roll(points[:, 1], -1)) -
                 np.dot(points[:, 1], np.roll(points[:, 0], -1))) / 2


class PoseEstimator:
    def __init__(self, camera: CameraProfile, config: PoseConfig | None = None) -> None:
        self.camera = camera
        self.config = config or PoseConfig()

    def estimate(
        self,
        candidate: TargetCandidate,
        *,
        use_ransac: bool | None = None,
        camera_matrix: np.ndarray | None = None,
        distortion: np.ndarray | None = None,
    ) -> PoseResult:
        object_points = candidate.profile.object_points.astype(np.float64)
        image_points = candidate.corners.astype(np.float64)
        intrinsic = np.asarray(
            self.camera.camera_matrix if camera_matrix is None else camera_matrix,
            dtype=np.float64,
        )
        coefficients = np.asarray(
            self.camera.distortion if distortion is None else distortion,
            dtype=np.float64,
        )
        zero = np.zeros(3, dtype=np.float64)
        invalid = lambda reason: PoseResult(
            PoseEstimate(zero, zero, float("inf"), None, False, False, reason), None
        )
        if len(object_points) < 4 or image_points.shape != (len(object_points), 2):
            return invalid("point-count mismatch")
        if not np.all(np.isfinite(image_points)):
            return invalid("non-finite image points")
        if len(image_points) == 4:
            if not cv2.isContourConvex(image_points.astype(np.float32)):
                return invalid("corners are non-convex or incorrectly ordered")
            object_area = _signed_area(object_points[:, :2])
            image_area = _signed_area(image_points)
            if abs(object_area) < 1e-12 or abs(image_area) < 1.0 or object_area * image_area <= 0:
                return invalid("corner winding/order does not match target profile")

        ransac = self.config.use_ransac if use_ransac is None else use_ransac
        try:
            if ransac:
                success, rvec, tvec, inliers = cv2.solvePnPRansac(
                    object_points,
                    image_points,
                    intrinsic,
                    coefficients,
                    flags=cv2.SOLVEPNP_ITERATIVE,
                    reprojectionError=self.config.ransac_reprojection_error_px,
                    iterationsCount=self.config.ransac_iterations,
                )
            else:
                success, rvec, tvec = cv2.solvePnP(
                    object_points,
                    image_points,
                    intrinsic,
                    coefficients,
                    flags=cv2.SOLVEPNP_IPPE if np.allclose(object_points[:, 2], 0) else cv2.SOLVEPNP_ITERATIVE,
                )
                inliers = np.arange(len(object_points), dtype=np.int32).reshape(-1, 1) if success else None
        except cv2.error as exc:
            return invalid(f"OpenCV solvePnP failed: {exc}")
        if not success:
            return invalid("solvePnP returned no solution")
        rvec = np.asarray(rvec, np.float64).reshape(3)
        tvec = np.asarray(tvec, np.float64).reshape(3)
        projected, jacobian = cv2.projectPoints(
            object_points, rvec, tvec, intrinsic, coefficients
        )
        residuals = np.linalg.norm(projected.reshape(-1, 2) - image_points, axis=1)
        rms = float(np.sqrt(np.mean(residuals**2)))
        translation_jacobian = np.asarray(jacobian, np.float64)[:, 3:6]
        pixel_variance = max(rms, self.config.image_noise_floor_px) ** 2
        translation_covariance = (
            np.linalg.pinv(translation_jacobian.T @ translation_jacobian) * pixel_variance
        )
        translation_covariance = (
            translation_covariance + translation_covariance.T
        ) / 2
        rotation, _ = cv2.Rodrigues(rvec)
        camera_points = (rotation @ object_points.T).T + tvec
        reason = ""
        if not np.all(np.isfinite(rvec)) or not np.all(np.isfinite(tvec)):
            reason = "non-finite pose"
        elif np.any(camera_points[:, 2] < self.config.min_depth_m):
            reason = "target geometry is behind or too close to camera"
        elif np.linalg.norm(tvec) > self.config.max_distance_m:
            reason = "target distance exceeds configured limit"
        elif rms > self.config.max_reprojection_error_px:
            reason = "reprojection error exceeds configured limit"
        elif ransac and (inliers is None or len(inliers) < 4):
            reason = "insufficient RANSAC inliers"
        raw = PoseEstimate(
            rvec,
            tvec,
            rms,
            inliers,
            True,
            not reason,
            reason,
            translation_covariance,
        )
        return PoseResult(raw, raw if not reason else None)

    def estimate_pose(self, candidate: TargetCandidate) -> PoseResult:
        """Compatibility alias with an explicit action name."""
        return self.estimate(candidate)
