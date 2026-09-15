from __future__ import annotations

import numpy as np
import pytest

from vantaflight.vision import (
    CameraProfile,
    FramePacket,
    KalmanTargetTracker,
    LockConfig,
    LockState,
    ROIPolicyConfig,
    ROISearchPolicy,
    ROIState,
    TargetLock,
    TargetProfile,
    TrackStatus,
    TrackerConfig,
    VantaFusion,
    VisionEvidence,
    VisionPipeline,
    project_planar_target,
    render_target_image,
)


def test_timestamp_driven_kalman_estimates_velocity() -> None:
    tracker = KalmanTargetTracker(
        TrackerConfig(process_noise=0.05, measurement_noise=0.1, confirmation_hits=2)
    )
    tracker.update([0, 0], 0)
    for step in range(1, 8):
        snapshot = tracker.update([2 * step, -step], float(step))
    assert snapshot.status == TrackStatus.TRACKING
    assert np.allclose(snapshot.velocity, [2, -1], atol=0.15)
    predicted = tracker.predict(8.0)
    assert np.allclose(predicted.position, [16, -8], atol=0.25)


def test_tracker_missed_lost_and_reacquisition_lifecycle() -> None:
    tracker = KalmanTargetTracker(
        TrackerConfig(
            confirmation_hits=1, max_missed=1, reacquisition_timeout_s=1,
            measurement_noise=1, mahalanobis_gate=9,
        )
    )
    assert tracker.update([10, 10], 0).status == TrackStatus.TRACKING
    assert tracker.update(None, 0.1).status == TrackStatus.COASTING
    assert tracker.update(None, 0.2).status == TrackStatus.LOST
    reacquired = tracker.update([10.2, 10.1], 0.3)
    assert reacquired.reacquired and reacquired.status == TrackStatus.TRACKING
    assert reacquired.missed == 0


def test_tracker_gates_unassociated_measurement() -> None:
    tracker = KalmanTargetTracker(
        TrackerConfig(confirmation_hits=1, measurement_noise=0.1, mahalanobis_gate=2)
    )
    tracker.update([0, 0], 0)
    snapshot = tracker.update([1000, 1000], 0.01)
    assert snapshot.status == TrackStatus.COASTING
    assert np.linalg.norm(snapshot.position) < 1


def test_roi_search_expands_then_returns_to_full_frame() -> None:
    policy = ROISearchPolicy(
        ROIPolicyConfig(tracked_size_px=40, expansion_factor=2, full_frame_after_misses=2)
    )
    policy.found((50, 50))
    assert policy.region((100, 100, 3)) == (30, 30, 40, 40)
    policy.missed()
    assert policy.state == ROIState.EXPANDING
    assert policy.region((100, 100, 3)) == (10, 10, 80, 80)
    policy.missed()
    assert policy.state == ROIState.FULL_FRAME
    assert policy.region((100, 100, 3)) == (0, 0, 100, 100)


def test_fusion_uses_fresh_independent_evidence_and_avoids_double_counting() -> None:
    fusion = VantaFusion(max_age_s=0.2, prior=0.1)
    evidence = [
        VisionEvidence("contour", 0.8, 1.0, 1, "image"),
        VisionEvidence("corners", 0.7, 1.0, 1, "image"),
        VisionEvidence("pose", 0.9, 1.0, 1, "geometry"),
        VisionEvidence("stale-neural", 0.99, 0.0, 1, "neural"),
    ]
    result = fusion.fuse(evidence, 1.1)
    assert set(result.used_sources) == {"contour", "pose"}
    assert result.confidence > 0.7
    assert "stale-neural" not in result.contributions


def test_lock_state_machine_is_deterministic_across_lifecycle() -> None:
    lock = TargetLock(LockConfig(acquire_hits=2, coast_misses=1, lost_misses=2))
    assert lock.update(True, 0.8).state == LockState.ACQUIRING
    assert lock.update(True, 0.8).state == LockState.LOCKED
    assert lock.update(False, 0).state == LockState.COASTING
    assert lock.update(False, 0).state == LockState.LOST
    assert lock.update(True, 0.8).state == LockState.ACQUIRING
    assert lock.update(True, 0.8).state == LockState.LOCKED


@pytest.mark.asyncio
async def test_pipeline_wires_detection_pose_tracking_confidence_and_lifecycle() -> None:
    intrinsics = np.array([[500.0, 0, 160], [0, 500.0, 120], [0, 0, 1]])
    camera = CameraProfile(intrinsics, np.zeros(5), (320, 240))
    target = TargetProfile.rectangle(
        "green", .8, .4, (45, 100, 80), (85, 255, 255),
        min_area_px=200, aspect_ratio_range=(1, 3),
    )
    corners = project_planar_target(
        target.object_points, intrinsics, np.zeros(3), np.array([0, 0, 2.0])
    )
    target_image = render_target_image((320, 240), corners)
    empty_image = np.zeros_like(target_image)
    now = [0.0]
    pipeline = VisionPipeline(
        camera,
        [target],
        clock=lambda: now[0],
        lock=TargetLock(LockConfig(acquire_hits=2, coast_misses=2, lost_misses=4)),
    )

    first = await pipeline.process(FramePacket(target_image, 0.0, 0, "synthetic"))
    assert len(first.candidates) == 1
    assert first.selected is not None and first.pose is not None
    assert first.track.status == TrackStatus.TENTATIVE
    assert first.fusion.confidence > 0.65
    assert first.lock.state == LockState.ACQUIRING
    assert len(pipeline.scene) == 1

    now[0] = 0.1
    second = await pipeline.process(FramePacket(target_image, 0.1, 1, "synthetic"))
    assert second.track.status == TrackStatus.TRACKING
    assert second.selected is not None

    result = second
    for sequence in range(2, 6):
        now[0] = sequence / 10
        result = await pipeline.process(
            FramePacket(empty_image, now[0], sequence, "synthetic")
        )
    assert result.track.status == TrackStatus.LOST
    assert result.lock.state == LockState.LOST

    now[0] = 0.6
    recovered = await pipeline.process(FramePacket(target_image, 0.6, 6, "synthetic"))
    assert recovered.selected is not None
    assert recovered.track.reacquired
    assert recovered.lock.state == LockState.ACQUIRING
