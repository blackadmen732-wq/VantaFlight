from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from vantaflight.vision import (
    AircraftState,
    CameraManager,
    CameraProfile,
    FrameBuffer,
    FramePacket,
    LockConfig,
    LockState,
    PreviewBuffer,
    SyntheticCameraSource,
    SyntheticTargetSpec,
    TargetCandidate,
    TargetLock,
    TargetProfile,
    VisionPipeline,
    default_optical_to_frd,
    render_synthetic_scene,
    render_target_image,
)


def calibrated_camera() -> CameraProfile:
    return CameraProfile(
        camera_id="nose",
        resolution=(320, 240),
        fps=60,
        fx=500,
        fy=510,
        cx=160,
        cy=120,
        distortion=np.zeros(5),
        mount_transform=default_optical_to_frd(),
        capture_latency_s=0.008,
        calibration_version="lab-2026-09",
    )


def green_profile() -> TargetProfile:
    return TargetProfile.rectangle(
        "gate",
        .8,
        .4,
        (45, 100, 80),
        (85, 255, 255),
        profile_id="gate-v1",
        target_type="race-gate",
        min_area_px=100,
        aspect_ratio_range=(1, 3),
        detector_config={"epsilon": 0.02},
        pose_config={"ransac": True},
    )


def test_explicit_models_have_serializable_semantics_and_compatibility_aliases() -> None:
    camera = calibrated_camera()
    image = np.zeros((240, 320, 3), np.uint8)
    frame = FramePacket(
        frame_id="frame-42",
        capture_timestamp=10.0,
        receive_timestamp=10.01,
        image=image,
        camera_source="nose-usb",
        camera_profile=camera,
        session_id="race-7",
        sequence=42,
    )
    assert (frame.width, frame.height, frame.timestamp, frame.source_id) == (320, 240, 10, "nose-usb")
    serialized = frame.to_dict()
    assert "image" not in serialized
    assert serialized["frame_id"] == "frame-42"
    assert serialized["camera_profile"]["matrix"] == camera.matrix.tolist()
    assert camera.width == 320 and camera.fps == 60
    assert 30 < camera.horizontal_fov_deg < 40

    target = green_profile()
    corners = np.array([[10, 10], [50, 10], [50, 30], [10, 30]], dtype=float)
    candidate = TargetCandidate(
        target,
        ordered_corners=corners,
        frame_id=frame.frame_id,
        candidate_id="candidate-1",
        profile_id=target.profile_id,
        bounding_box=(10, 10, 40, 20),
        center=(30, 20),
        area_px=800,
        color_confidence=.9,
        edge_confidence=.8,
        shape_confidence=.85,
        geometry_confidence=.95,
        detector_confidence=.88,
    )
    assert candidate.corners is candidate.ordered_corners
    assert candidate.centroid == candidate.center and candidate.score == .88
    assert candidate.to_dict()["profile_id"] == "gate-v1"
    assert target.to_dict()["physical_width_m"] == .8


def test_frame_and_preview_buffer_metrics_are_bounded_and_age_aware() -> None:
    now = [5.0]
    buffer = FrameBuffer(2, clock=lambda: now[0])
    preview = PreviewBuffer(1)
    for sequence, timestamp in enumerate((3.0, 4.0, 4.5)):
        frame = FramePacket(np.zeros((2, 2), np.uint8), timestamp, sequence, "test")
        buffer.put(frame)
        assert preview.put_nowait(frame)
    metrics = buffer.metrics
    assert metrics.frames_captured == 3
    assert metrics.frames_dropped == 1
    assert metrics.depth == 2
    assert metrics.oldest_frame_age_s == 1
    assert metrics.current_frame_age_s == .5
    assert preview.metrics.depth == 1 and preview.metrics.dropped == 2


@pytest.mark.asyncio
async def test_camera_manager_has_one_capture_task_and_idempotent_restart() -> None:
    image = render_target_image((80, 60))
    source = SyntheticCameraSource([image], loop=True, clock=lambda: 1.0)
    manager = CameraManager(source, frame_buffer=FrameBuffer(2), preview_buffer=PreviewBuffer(1))
    await manager.start()
    first_task = manager.capture_task
    await manager.start()
    assert manager.capture_task is first_task
    await asyncio.sleep(.01)
    assert manager.frame_buffer.metrics.frames_captured > 0
    assert manager.preview_buffer.metrics.received > 0
    await manager.stop()
    await manager.stop()
    assert manager.capture_task is None and not manager.running
    await manager.restart()
    assert manager.capture_task is not None and manager.capture_task is not first_task
    await manager.stop()


def test_canonical_lock_states_and_evidence_transitions() -> None:
    assert [state.value for state in LockState] == [
        "SEARCHING", "CANDIDATE", "DETECTED", "CONFIRMED", "TRACKED",
        "POSE_LOCKED", "PREDICTIVE_LOCK", "RACE_LOCK", "DEGRADED", "LOST",
    ]
    lock = TargetLock(LockConfig(acquire_hits=3, coast_misses=1, lost_misses=2))
    assert lock.update(True, .9).state == LockState.CANDIDATE
    assert lock.update(True, .9).state == LockState.DETECTED
    assert lock.update(True, .9).state == LockState.CONFIRMED
    assert lock.update(True, .9, track_confirmed=True).state == LockState.TRACKED
    assert lock.update(True, .9, track_confirmed=True, pose_valid=True).state == LockState.POSE_LOCKED
    predictive = lock.update(
        True, .9, track_confirmed=True, pose_valid=True, predictive=True,
        evidence=("detector", "pose", "tracker"),
    )
    assert predictive.state == LockState.PREDICTIVE_LOCK
    assert predictive.evidence == ("detector", "pose", "tracker")
    assert lock.update(
        True, .9, track_confirmed=True, pose_valid=True, predictive=True, race_ready=True
    ).state == LockState.RACE_LOCK
    assert lock.update(False, 0).state == LockState.DEGRADED
    assert lock.update(False, 0).state == LockState.LOST


@pytest.mark.asyncio
async def test_pipeline_tracks_two_targets_and_uses_optical_flow_cadence() -> None:
    camera = calibrated_camera()
    target = green_profile()
    image = np.full((240, 320, 3), 16, np.uint8)
    image[60:120, 35:135] = (0, 255, 0)
    image[130:190, 185:285] = (0, 255, 0)
    shifted = np.roll(image, 2, axis=1)
    now = [0.0]
    pipeline = VisionPipeline(
        camera, [target], detector_interval_frames=3, clock=lambda: now[0]
    )
    first = await pipeline.process(FramePacket(image, 0, 0, "synthetic"))
    assert first.detector_ran and len(first.candidates) == 2 and len(first.tracks) == 2
    now[0] = .05
    second = await pipeline.process(FramePacket(shifted, .05, 1, "synthetic"))
    assert not second.detector_ran
    assert len(second.candidates) == 2 and len(second.tracks) == 2
    assert {candidate.source for candidate in second.candidates} == {"optical_flow"}
    assert set(pipeline.tracker_map.tracks) == set(second.tracks)
    now[0] = .10
    third = await pipeline.process(FramePacket(np.roll(image, 4, axis=1), .10, 2, "synthetic"))
    assert not third.detector_ran and len(third.tracks) == 2
    now[0] = .15
    periodic = await pipeline.process(
        FramePacket(np.roll(image, 6, axis=1), .15, 3, "synthetic")
    )
    assert periodic.detector_ran and len(periodic.candidates) == 2
    assert len(periodic.tracks) == 2


@pytest.mark.asyncio
async def test_pipeline_3d_fusion_timeline_and_process_latest_lifecycle() -> None:
    camera = calibrated_camera()
    target = green_profile()
    image = render_target_image(
        (320, 240), np.array([[60, 70], [260, 70], [260, 170], [60, 170]])
    )
    now = [2.0]
    pipeline = VisionPipeline(camera, [target], clock=lambda: now[0])
    body_to_world = np.eye(4)
    body_to_world[:3, 3] = [10, 20, 30]
    aircraft = AircraftState(2.0, body_to_world=body_to_world)
    buffer = FrameBuffer(clock=lambda: now[0])
    buffer.put(
        FramePacket(
            image, 2.0, 0, "synthetic", receive_timestamp=2.0, camera_profile=camera
        )
    )
    result = await pipeline.process_latest(buffer, aircraft_state=aircraft)
    assert result is not None
    assert result.fusion.observed_pose is not None
    assert result.fusion.predicted_pose is not None
    assert result.fusion.observed_pose.position_world_m[0] > 10
    assert set(result.timeline.stage_timestamps()) == set(result.timeline.STAGES)
    assert all(value is not None for value in result.timeline.stage_timestamps().values())

    source = SyntheticCameraSource([image], loop=True, clock=lambda: now[0])
    manager = CameraManager(source)
    seen: list[str] = []
    await pipeline.start(manager, lambda item: seen.append(item.frame.frame_id))
    await pipeline.start(manager)
    await asyncio.sleep(.02)
    await pipeline.stop()
    await pipeline.stop()
    assert seen and not pipeline.running and not manager.running


def test_vision_has_no_mavsdk_or_digital_twin_imports() -> None:
    vision_dir = Path(__file__).parents[1] / "vantaflight" / "vision"
    source = "\n".join(path.read_text() for path in vision_dir.glob("*.py"))
    assert "import mavsdk" not in source.lower()
    assert "vantaflight.digital_twin" not in source
    assert "from ..digital_twin" not in source


def test_synthetic_scene_supports_multiple_targets_artifacts_and_reproducibility() -> None:
    targets = (
        SyntheticTargetSpec(
            np.array([[20, 20], [100, 20], [100, 80], [20, 80]]),
            brightness=0.8,
            occlusion_fraction=0.1,
        ),
        SyntheticTargetSpec(
            np.array([[180, 110], [285, 125], [275, 205], [170, 190]]),
            bgr=(0, 220, 0),
        ),
    )
    first = render_synthetic_scene(
        (320, 240), targets, noise_std=2.0, motion_blur_px=3, seed=44
    )
    second = render_synthetic_scene(
        (320, 240), targets, noise_std=2.0, motion_blur_px=3, seed=44
    )
    different = render_synthetic_scene(
        (320, 240), targets, noise_std=2.0, motion_blur_px=3, seed=45
    )
    assert np.array_equal(first, second)
    assert not np.array_equal(first, different)
    assert first.shape == (240, 320, 3)
