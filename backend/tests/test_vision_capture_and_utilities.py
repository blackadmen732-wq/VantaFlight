from __future__ import annotations

import asyncio

import cv2
import numpy as np
import pytest

from vantaflight.vision import (
    AircraftState,
    CameraProfile,
    FileCameraSource,
    FrameBuffer,
    FramePacket,
    LatencyTimeline,
    ONNXDetector,
    OpenCVPreprocessor,
    PreprocessConfig,
    PyramidalLK,
    SceneObject,
    SyntheticCameraSource,
    VantaScene,
    camera_to_world,
    default_optical_to_frd,
    predict_position_for_latency,
    render_target_image,
)


def packet(sequence: int, timestamp: float) -> FramePacket:
    return FramePacket(np.zeros((8, 8, 3), np.uint8), timestamp, sequence, "test")


def test_camera_profile_rejects_bad_calibration() -> None:
    with pytest.raises(ValueError, match="3x3"):
        CameraProfile(np.eye(2))
    bad = np.eye(3)
    bad[0, 0] = 0
    with pytest.raises(ValueError, match="focal"):
        CameraProfile(bad)
    with pytest.raises(ValueError, match="coefficient"):
        CameraProfile(np.eye(3), np.zeros(3))


def test_frame_buffer_drops_capacity_and_superseded_stale_frames() -> None:
    buffer = FrameBuffer(capacity=2)
    buffer.put(packet(0, 1.0))
    buffer.put(packet(1, 2.0))
    buffer.put(packet(2, 3.0))
    assert buffer.latest().sequence == 2
    metrics = buffer.metrics
    assert (metrics.received, metrics.delivered) == (3, 1)
    assert (metrics.dropped_capacity, metrics.dropped_stale, metrics.depth) == (1, 1, 0)


def test_frame_buffer_rejects_over_age_latest_frame() -> None:
    buffer = FrameBuffer()
    buffer.put(packet(0, 10.0))
    assert buffer.latest(max_age_s=0.5, now=10.6) is None
    assert buffer.metrics.dropped_stale == 1


@pytest.mark.asyncio
async def test_synthetic_and_file_sources_have_deterministic_sequences(tmp_path) -> None:
    image = render_target_image((80, 60), noise_std=2, seed=9)
    synthetic = SyntheticCameraSource([image, image], clock=lambda: 4.0)
    await synthetic.start()
    assert (await synthetic.read()).sequence == 0
    assert (await synthetic.read()).timestamp == 4.0
    assert await synthetic.read() is None
    await synthetic.stop()

    path = tmp_path / "target.png"
    assert cv2.imwrite(str(path), image)
    source = FileCameraSource(path, clock=lambda: 5.0)
    await source.start()
    frame = await source.read()
    assert frame is not None and frame.image.shape == image.shape and frame.timestamp == 5.0
    assert await source.read() is None
    await source.stop()


def test_preprocessing_configuration_and_timing() -> None:
    image = render_target_image((80, 60))
    preprocessor = OpenCVPreprocessor(
        PreprocessConfig(resize=(40, 30), blur_kernel=3, clahe_clip_limit=2, convert_hsv=True)
    )
    result = preprocessor.process(image)
    assert result.image.shape == (30, 40, 3)
    assert set(result.timings_ms) == {"resize", "clahe", "blur", "hsv"}
    assert result.total_ms >= 0


def test_pyramidal_lk_tracks_translation() -> None:
    first = np.zeros((100, 100), np.uint8)
    cv2.rectangle(first, (25, 25), (45, 45), 255, -1)
    transform = np.float32([[1, 0, 4], [0, 1, 3]])
    second = cv2.warpAffine(first, transform, (100, 100))
    points = np.array([[25, 25], [45, 25], [45, 45], [25, 45]], np.float32)
    result = PyramidalLK(max_error=50).track(first, second, points)
    assert result.valid.all()
    assert np.allclose(np.median(result.displacement, axis=0), [4, 3], atol=0.5)


def test_explicit_camera_body_world_transforms() -> None:
    camera = CameraProfile(np.eye(3), camera_to_body=default_optical_to_frd())
    body_to_world = np.eye(4)
    body_to_world[:3, 3] = [10, 20, 30]
    aircraft = AircraftState(0, body_to_world=body_to_world)
    # Optical [right=2, down=3, forward=5] -> FRD [5,2,3] -> NED offset.
    assert np.allclose(camera_to_world([2, 3, 5], camera, aircraft), [15, 22, 33])


def test_scene_is_bounded_and_expires() -> None:
    scene = VantaScene(capacity=2, max_age_s=1)
    scene.update(SceneObject("a", 0, 0.5, None))
    scene.update(SceneObject("b", 1, 0.6, None))
    scene.update(SceneObject("c", 2, 0.7, None))
    assert scene.get("a") is None and scene.evictions == 1
    assert scene.purge(2.1) == 1
    assert [item.object_id for item in scene.snapshot()] == ["c"]


def test_latency_timeline_and_prediction() -> None:
    timeline = LatencyTimeline(10)
    timeline.mark("detect", 10.02)
    timeline.mark("track", 10.03)
    assert timeline.stage_durations_ms() == pytest.approx({"detect": 20, "track": 10})
    predicted = predict_position_for_latency([1, 2], [10, -2], 10, 10.05, 0.05)
    assert np.allclose(predicted, [2, 1.8])


@pytest.mark.asyncio
async def test_optional_async_detector_reports_freshness() -> None:
    detector = ONNXDetector(lambda _image, _timestamp: [], clock=lambda: 1.05)
    result = await detector.detect(packet(7, 1.0))
    assert result.sequence == 7 and result.is_fresh(1.1, 0.2)
    assert not result.is_fresh(1.3, 0.2)
