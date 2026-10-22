from __future__ import annotations

import cv2
import numpy as np
import pytest

from vantaflight.vision import (
    CameraProfile,
    CandidateAssociator,
    ClassicalTargetDetector,
    PoseConfig,
    PoseEstimator,
    TargetCandidate,
    TargetProfile,
    order_corners_clockwise,
    project_planar_target,
    render_target_image,
)


def camera() -> CameraProfile:
    return CameraProfile(
        np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]]),
        np.zeros(5),
        (640, 480),
    )


def profile(name: str = "green") -> TargetProfile:
    return TargetProfile.rectangle(
        name, 1.0, 0.5, (45, 120, 80), (85, 255, 255),
        min_area_px=500, aspect_ratio_range=(1, 3),
    )


def candidate(points: np.ndarray, target: TargetProfile | None = None, score: float = 0.9) -> TargetCandidate:
    target = target or profile()
    contour = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
    return TargetCandidate(
        target, points, contour, tuple(np.mean(points, axis=0)),
        float(abs(cv2.contourArea(contour))), score, 1.0,
    )


def test_classical_detector_finds_polygon_and_orders_corners() -> None:
    expected = np.array([[180, 140], [470, 160], [450, 330], [200, 310]], np.float32)
    image = render_target_image(corners=expected, noise_std=4, seed=17)
    found = ClassicalTargetDetector([profile()]).detect(image, 12.0)
    assert len(found) == 1
    detection = found[0]
    assert detection.profile.name == "green"
    assert detection.score > 0.9
    assert np.allclose(detection.corners, expected, atol=4)
    assert detection.timestamp == 12.0


def test_corner_order_is_stable_for_permutations() -> None:
    points = np.array([[50, 50], [10, 10], [50, 10], [10, 50]], dtype=float)
    assert np.array_equal(
        order_corners_clockwise(points),
        np.array([[10, 10], [50, 10], [50, 50], [10, 50]], dtype=float),
    )


def test_pose_recovers_projected_target_and_reprojection_is_validated() -> None:
    target = profile()
    expected_t = np.array([0.1, -0.05, 2.5])
    expected_r = np.array([0.08, -0.04, 0.02])
    image_points = project_planar_target(
        target.object_points, camera().camera_matrix, expected_r, expected_t
    )
    result = PoseEstimator(camera()).estimate(candidate(image_points, target))
    assert result.raw.raw_success and result.validated is not None
    assert result.validated.reprojection_error_px < 1e-4
    assert np.allclose(result.validated.translation_vector, expected_t, atol=1e-3)


def test_pose_rejects_bad_corner_order_before_solver() -> None:
    target = profile()
    points = project_planar_target(
        target.object_points, camera().camera_matrix, np.zeros(3), np.array([0, 0, 2.0])
    )
    points[[1, 2]] = points[[2, 1]]
    result = PoseEstimator(camera()).estimate(candidate(points, target))
    assert not result.raw.raw_success
    assert "non-convex" in result.raw.reason or "order" in result.raw.reason


def test_noisy_pose_preserves_raw_output_but_fails_reprojection_validation() -> None:
    points_3d = np.array([
        [-.5, -.25, 0], [0, -.25, 0], [.5, -.25, 0], [.5, .25, 0],
        [0, .25, 0], [-.5, .25, 0],
    ])
    target = TargetProfile("six", (0, 0, 0), (179, 255, 255), points_3d, polygon_vertices=6)
    projected = project_planar_target(
        points_3d, camera().camera_matrix, np.array([.05, 0, 0]), np.array([0, 0, 2.0])
    )
    noisy = projected + np.array([[3, -2], [-4, 3], [2, 4], [-3, -3], [4, -2], [-2, 4]])
    estimator = PoseEstimator(
        camera(), PoseConfig(use_ransac=False, max_reprojection_error_px=0.5)
    )
    result = estimator.estimate(candidate(noisy, target))
    assert result.raw.raw_success
    assert result.raw.reprojection_error_px > 0.5
    assert result.validated is None


def test_impossible_collapsed_pose_is_rejected() -> None:
    points = np.array([[320, 240], [320.1, 240], [320.1, 240.1], [320, 240.1]])
    estimator = PoseEstimator(camera(), PoseConfig(min_depth_m=0.5, max_distance_m=10))
    result = estimator.estimate(candidate(points))
    assert result.validated is None
    assert result.raw.reason


def test_association_uses_profile_geometry_and_mahalanobis_gate() -> None:
    expected = candidate(np.array([[40, 40], [60, 40], [60, 60], [40, 60.]]))
    wrong_profile = candidate(
        np.array([[49, 49], [51, 49], [51, 51], [49, 51.]]), profile("red")
    )
    far = candidate(np.array([[190, 190], [210, 190], [210, 210], [190, 210.]]))
    result = CandidateAssociator(mahalanobis_gate=9).associate(
        [wrong_profile, far, expected],
        np.array([50, 50]),
        np.eye(2) * 4,
        profile_name="green",
        previous_area_px=expected.area_px,
    )
    assert result.candidate is expected
    gated = CandidateAssociator(mahalanobis_gate=1).associate(
        [far], np.array([50, 50]), np.eye(2), profile_name="green"
    )
    assert gated.candidate is None and gated.reason == "mahalanobis gate"
