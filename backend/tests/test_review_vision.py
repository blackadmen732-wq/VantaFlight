from __future__ import annotations

import asyncio

import cv2
import numpy as np
import pytest

from vantaflight.vision import (
    AircraftState,
    CameraManager,
    CameraProfile,
    ClassicalTargetDetector,
    FrameBuffer,
    FramePacket,
    KalmanTargetTracker,
    MultiTargetTracker,
    ONNXDetector,
    OpenCVPreprocessor,
    PixelColorSpace,
    PoseConfig,
    PoseEstimator,
    PreprocessConfig,
    ROISearchPolicy,
    TargetCandidate,
    TargetProfile,
    TrackerConfig,
    TrackStatus,
    VisionPipeline,
    body_to_world,
    project_planar_target,
    render_target_image,
)


def _camera(
    resolution: tuple[int, int] = (640, 480),
    distortion: np.ndarray | None = None,
) -> CameraProfile:
    return CameraProfile(
        np.array([[600.0, 0, 320], [0, 610.0, 240], [0, 0, 1]]),
        np.zeros(5) if distortion is None else distortion,
        resolution,
    )


def _profile(
    name: str = "green",
    lower: tuple[int, int, int] = (45, 100, 80),
    upper: tuple[int, int, int] = (85, 255, 255),
    **kwargs: object,
) -> TargetProfile:
    return TargetProfile.rectangle(
        name,
        1.0,
        0.5,
        lower,
        upper,
        min_area_px=20,
        aspect_ratio_range=(1, 4),
        **kwargs,
    )


def _candidate(
    profile: TargetProfile,
    points: np.ndarray,
    timestamp: float = 0.0,
) -> TargetCandidate:
    contour = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
    return TargetCandidate(
        profile,
        points,
        contour,
        tuple(points.mean(axis=0)),
        float(abs(cv2.contourArea(contour))),
        0.9,
        timestamp,
    )


def test_preprocessing_exposes_color_and_effective_calibration_contracts() -> None:
    camera = _camera(distortion=np.array([0.1, -0.03, 0.001, 0.0, 0.0]))
    image = np.zeros((480, 640, 3), np.uint8)
    result = OpenCVPreprocessor(
        PreprocessConfig(resize=(320, 240), convert_hsv=True, undistort=True)
    ).process(image, camera.camera_matrix, camera.distortion)

    assert result.color_space == PixelColorSpace.HSV
    assert np.allclose(result.camera_matrix, np.diag([0.5, 0.5, 1]) @ camera.camera_matrix)
    assert np.array_equal(result.distortion, np.zeros(5))
    with pytest.raises(ValueError, match="supplied together"):
        OpenCVPreprocessor().process(image, camera.camera_matrix)


@pytest.mark.asyncio
async def test_hsv_pipeline_does_not_double_convert_pixels() -> None:
    camera = _camera((640, 480))
    profile = _profile()
    corners = project_planar_target(
        profile.object_points, camera.camera_matrix, np.zeros(3), np.array([0, 0, 3.0])
    )
    image = render_target_image((640, 480), corners)
    pipeline = VisionPipeline(
        camera,
        [profile],
        preprocessor=OpenCVPreprocessor(PreprocessConfig(convert_hsv=True)),
        detector_interval_frames=1,
        clock=lambda: 0.0,
    )

    result = await pipeline.process(FramePacket(image, 0.0))

    assert result.preprocessed.color_space == PixelColorSpace.HSV
    assert len(result.candidates) == 1
    assert result.pose is not None


@pytest.mark.asyncio
async def test_pipeline_pose_is_invariant_to_preprocessing_resize() -> None:
    camera = _camera((640, 480))
    profile = _profile()
    expected_t = np.array([0.1, -0.05, 3.0])
    corners = project_planar_target(
        profile.object_points,
        camera.camera_matrix,
        np.zeros(3),
        expected_t,
    )
    class ProjectedDetector:
        def detect(
            self,
            image: np.ndarray,
            timestamp: float,
            _roi: tuple[int, int, int, int] | None = None,
            _frame_id: str | None = None,
            *,
            color_space: PixelColorSpace = PixelColorSpace.BGR,
        ) -> list[TargetCandidate]:
            assert color_space == PixelColorSpace.BGR
            scale = np.array([image.shape[1] / 640, image.shape[0] / 480])
            return [_candidate(profile, corners * scale, timestamp)]

    image = np.zeros((480, 640, 3), np.uint8)
    original = VisionPipeline(
        camera,
        [profile],
        detector=ProjectedDetector(),
        detector_interval_frames=1,
        clock=lambda: 0.0,
    )
    resized = VisionPipeline(
        camera,
        [profile],
        preprocessor=OpenCVPreprocessor(PreprocessConfig(resize=(320, 240))),
        detector=ProjectedDetector(),
        detector_interval_frames=1,
        clock=lambda: 0.0,
    )

    original_result = await original.process(FramePacket(image, 0.0))
    resized_result = await resized.process(FramePacket(image, 0.0))

    assert original_result.pose is not None and resized_result.pose is not None
    assert np.allclose(
        resized_result.pose.translation_vector,
        original_result.pose.translation_vector,
        atol=0.02,
    )


def test_resized_and_undistorted_correspondences_preserve_pose() -> None:
    distortion = np.array([0.12, -0.05, 0.001, -0.002, 0.0])
    camera = _camera(distortion=distortion)
    profile = _profile()
    expected_r = np.array([0.08, -0.06, 0.02])
    expected_t = np.array([0.15, -0.08, 3.5])
    distorted, _ = cv2.projectPoints(
        profile.object_points,
        expected_r,
        expected_t,
        camera.camera_matrix,
        camera.distortion,
    )
    undistorted = cv2.undistortPoints(
        distorted, camera.camera_matrix, camera.distortion, P=camera.camera_matrix
    ).reshape(-1, 2)
    scaled = undistorted * np.array([0.5, 0.5])
    processed = OpenCVPreprocessor(
        PreprocessConfig(resize=(320, 240), undistort=True)
    ).process(
        np.zeros((480, 640, 3), np.uint8),
        camera.camera_matrix,
        camera.distortion,
    )

    estimate = PoseEstimator(camera).estimate(
        _candidate(profile, scaled),
        use_ransac=False,
        camera_matrix=processed.camera_matrix,
        distortion=processed.distortion,
    )

    assert estimate.validated is not None
    assert np.allclose(estimate.validated.translation_vector, expected_t, atol=1e-5)


def test_pose_rejects_solution_with_any_target_geometry_behind_camera(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = _camera()
    profile = _profile()
    points = np.array([[200, 180], [440, 180], [440, 300], [200, 300]], dtype=float)

    def unsafe_solution(*_args: object, **_kwargs: object) -> tuple[bool, np.ndarray, np.ndarray]:
        return True, np.array([0.0, np.pi / 2, 0.0]), np.array([0.0, 0.0, 0.1])

    monkeypatch.setattr(cv2, "solvePnP", unsafe_solution)
    result = PoseEstimator(camera, PoseConfig(use_ransac=False)).estimate(
        _candidate(profile, points)
    )

    assert result.validated is None
    assert "target geometry" in result.raw.reason


def test_detector_rejects_nonquad_without_canonical_correspondence() -> None:
    object_points = np.array(
        [[-1, 0, 0], [-.5, -.8, 0], [.5, -.8, 0], [1, 0, 0], [.5, .8, 0], [-.5, .8, 0]]
    )
    profile = TargetProfile(
        "hex",
        (45, 100, 80),
        (85, 255, 255),
        object_points,
        min_area_px=20,
        polygon_vertices=6,
    )
    image = np.zeros((200, 200, 3), np.uint8)
    polygon = np.array([[40, 100], [70, 50], [130, 50], [160, 100], [130, 150], [70, 150]])
    cv2.fillConvexPoly(image, polygon, (0, 255, 0))

    assert ClassicalTargetDetector([profile]).detect(image, 0.0) == []


def test_same_timestamp_prediction_is_exact_noop_and_covariance_is_deterministic() -> None:
    config = TrackerConfig(process_noise=2.0, measurement_noise=1.0, confirmation_hits=1)
    tracker = KalmanTargetTracker(config)
    tracker.update([10, 20], 1.0)
    state = tracker.state.copy()
    covariance = tracker.covariance.copy()

    tracker.predict(1.0)

    assert np.array_equal(tracker.state, state)
    assert np.array_equal(tracker.covariance, covariance)

    profile = _profile()
    first = _candidate(profile, np.array([[0, 0], [10, 0], [10, 10], [0, 10.]]))
    second = _candidate(profile, np.array([[1, 0], [11, 0], [11, 10], [1, 10.]]), 1.0)
    multi = MultiTargetTracker(config)
    multi.update([first], 0.0)
    multi_snapshot = next(iter(multi.update([second], 1.0).values()))
    reference = KalmanTargetTracker(config)
    reference.update(first.center, 0.0)
    reference_snapshot = reference.update(second.center, 1.0)
    assert np.allclose(multi_snapshot.covariance, reference_snapshot.covariance)


@pytest.mark.asyncio
async def test_onnx_sync_async_timeout_exception_stale_and_disabled() -> None:
    frame = FramePacket(np.zeros((8, 8, 3), np.uint8), 1.0, sequence=7)
    calls: list[str] = []

    def sync_infer(_image: np.ndarray, _timestamp: float) -> list[TargetCandidate]:
        calls.append("sync")
        return []

    async def async_infer(_image: np.ndarray, _timestamp: float) -> list[TargetCandidate]:
        calls.append("async")
        await asyncio.sleep(0)
        return []

    assert (await ONNXDetector(sync_infer).detect(frame)).sequence == 7
    assert (await ONNXDetector(async_infer).detect(frame)).sequence == 7

    async def slow(_image: np.ndarray, _timestamp: float) -> list[TargetCandidate]:
        await asyncio.sleep(0.05)
        return []

    with pytest.raises(asyncio.TimeoutError):
        await ONNXDetector(slow, timeout_s=0.001).detect(frame)

    def broken(_image: np.ndarray, _timestamp: float) -> list[TargetCandidate]:
        raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        await ONNXDetector(broken).detect(frame)

    disabled = await ONNXDetector(sync_infer, enabled=False, clock=lambda: 2.0).detect(frame)
    assert disabled.candidates == ()
    assert calls == ["sync", "async"]
    assert not disabled.is_fresh(2.0, 0.5)


class _FailOnceSource:
    source_id = "fail-once"

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.reads = 0

    async def start(self) -> None:
        self.starts += 1

    async def read(self) -> FramePacket | None:
        self.reads += 1
        if self.starts == 1:
            raise OSError("camera disconnected")
        return None

    async def stop(self) -> None:
        self.stops += 1


@pytest.mark.asyncio
async def test_camera_manager_failure_state_and_deterministic_restart() -> None:
    source = _FailOnceSource()
    manager = CameraManager(source)
    await manager.start()
    first_task = manager.capture_task
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not manager.running
    assert isinstance(manager.failure, OSError)
    await manager.restart()
    assert manager.running
    assert manager.failure is None
    assert manager.capture_task is not first_task
    await manager.stop()
    await manager.stop()
    assert (source.starts, source.stops) == (2, 2)


def test_invalid_frame_age_and_off_image_rois_do_not_mutate_or_shift() -> None:
    buffer = FrameBuffer()
    frame = FramePacket(np.zeros((4, 4), np.uint8), 1.0)
    buffer.put(frame)
    with pytest.raises(ValueError, match="non-negative"):
        buffer.latest(max_age_s=-1)
    assert buffer.metrics.depth == 1

    policy = ROISearchPolicy()
    policy.found((-100.0, 50.0))
    assert policy.region((100, 100, 3)) == (0, 0, 0, 100)

    image = np.zeros((40, 40, 3), np.uint8)
    image[:, :10] = (0, 255, 0)
    assert ClassicalTargetDetector([_profile()]).detect(image, 0.0, (-20, 0, 10, 40)) == []


def test_orientation_range_filters_rotated_targets_and_wraps() -> None:
    image = np.zeros((240, 320, 3), np.uint8)
    box = np.rint(cv2.boxPoints(((160, 120), (120, 50), 30))).astype(np.int32)
    cv2.fillConvexPoly(image, box, (0, 255, 0))

    accepted = _profile("accepted", orientation_range_deg=(20, 40))
    rejected = _profile("rejected", orientation_range_deg=(-10, 10))
    wrapped = _profile("wrapped", orientation_range_deg=(170, 40))

    assert len(ClassicalTargetDetector([accepted]).detect(image, 0.0)) == 1
    assert ClassicalTargetDetector([rejected]).detect(image, 0.0) == []
    assert len(ClassicalTargetDetector([wrapped]).detect(image, 0.0)) == 1


def test_world_transform_includes_aircraft_position_and_pose_uncertainty_is_metric() -> None:
    aircraft = AircraftState(0.0, position_ned_m=np.array([10.0, 20.0, 30.0]))
    assert np.allclose(body_to_world(np.array([1.0, 2.0, 3.0]), aircraft), [11, 22, 33])

    camera = _camera()
    profile = _profile()
    points = project_planar_target(
        profile.object_points, camera.camera_matrix, np.zeros(3), np.array([0, 0, 4.0])
    )
    pose = PoseEstimator(camera).estimate(_candidate(profile, points), use_ransac=False).validated
    assert pose is not None
    assert pose.reprojection_error_px < 1e-6
    assert pose.translation_covariance_m2 is not None
    assert np.all(np.linalg.eigvalsh(pose.translation_covariance_m2) >= -1e-12)
    assert np.max(np.diag(pose.translation_covariance_m2)) > 0
    assert not np.allclose(
        pose.translation_covariance_m2,
        np.eye(3) * (pose.reprojection_error_px + 1e-3),
    )


@pytest.mark.asyncio
async def test_pipeline_releases_profile_constraint_after_track_is_lost() -> None:
    camera = _camera()
    green = _profile("green")
    blue = _profile("blue", (100, 100, 80), (130, 255, 255))
    tracker = KalmanTargetTracker(
        TrackerConfig(confirmation_hits=1, max_missed=0, measurement_noise=1.0)
    )
    pipeline = VisionPipeline(
        camera,
        [green, blue],
        tracker=tracker,
        detector_interval_frames=1,
        clock=lambda: 0.0,
    )
    corners = np.array([[220, 180], [420, 180], [420, 300], [220, 300]])
    green_image = render_target_image((640, 480), corners, bgr=(0, 255, 0))
    blue_image = render_target_image((640, 480), corners, bgr=(255, 0, 0))

    first = await pipeline.process(FramePacket(green_image, 0.0))
    lost = await pipeline.process(FramePacket(np.zeros_like(green_image), 0.1))
    recovered = await pipeline.process(FramePacket(blue_image, 0.2))

    assert first.selected is not None and first.selected.profile.name == "green"
    assert lost.track.status == TrackStatus.LOST
    assert recovered.selected is not None and recovered.selected.profile.name == "blue"
