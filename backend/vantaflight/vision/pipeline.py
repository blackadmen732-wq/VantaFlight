"""End-to-end V0.5 vision pipeline wiring."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import cv2
import numpy as np

from .association import CandidateAssociator
from .capture import CameraManager, FrameBuffer
from .concepts import (
    AircraftState,
    CameraProfile,
    FramePacket,
    ObservedPose3D,
    PoseEstimate,
    PredictedPose3D,
    TargetCandidate,
    TargetProfile,
)
from .detection import ClassicalTargetDetector
from .fusion import FusionResult, VantaFusion, VisionEvidence
from .inference import AsyncDetector
from .latency import LatencyTimeline
from .lock import LockSnapshot, TargetLock
from .optical_flow import PyramidalLK
from .pose import PoseEstimator
from .preprocess import OpenCVPreprocessor, PreprocessResult
from .roi import ROISearchPolicy
from .scene import SceneObject, VantaScene
from .tracking import KalmanTargetTracker, MultiTargetTracker, TrackSnapshot, TrackStatus
from .transforms import camera_to_body, camera_to_world


@dataclass(frozen=True)
class VisionPipelineResult:
    frame: FramePacket
    preprocessed: PreprocessResult
    candidates: tuple[TargetCandidate, ...]
    selected: TargetCandidate | None
    pose: PoseEstimate | None
    track: TrackSnapshot
    fusion: FusionResult
    lock: LockSnapshot
    timeline: LatencyTimeline
    tracks: dict[str, TrackSnapshot]
    poses: dict[str, PoseEstimate]
    detector_ran: bool


class VisionPipeline:
    """Deterministic orchestration; I/O remains outside the processing path."""

    def __init__(
        self,
        camera: CameraProfile,
        profiles: list[TargetProfile],
        *,
        preprocessor: OpenCVPreprocessor | None = None,
        detector: ClassicalTargetDetector | None = None,
        pose_estimator: PoseEstimator | None = None,
        tracker: KalmanTargetTracker | None = None,
        associator: CandidateAssociator | None = None,
        roi_policy: ROISearchPolicy | None = None,
        fusion: VantaFusion | None = None,
        lock: TargetLock | None = None,
        scene: VantaScene | None = None,
        async_detector: AsyncDetector | None = None,
        async_max_age_s: float = 0.25,
        detector_interval_frames: int = 3,
        flow: PyramidalLK | None = None,
        flow_min_confidence: float = 0.45,
        max_tracks: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.camera = camera
        self.preprocessor = preprocessor or OpenCVPreprocessor()
        self.detector = detector or ClassicalTargetDetector(profiles)
        self.pose_estimator = pose_estimator or PoseEstimator(camera)
        self.tracker = tracker or KalmanTargetTracker()
        self.tracker_map = MultiTargetTracker(self.tracker.config, max_tracks)
        self.associator = associator or CandidateAssociator(self.tracker.config.mahalanobis_gate)
        self.roi_policy = roi_policy or ROISearchPolicy()
        self.fusion_engine = fusion or VantaFusion()
        self.lock_machine = lock or TargetLock()
        self.scene = scene or VantaScene()
        self.async_detector = async_detector
        self.async_max_age_s = async_max_age_s
        if detector_interval_frames <= 0:
            raise ValueError("detector_interval_frames must be positive")
        self.detector_interval_frames = detector_interval_frames
        self.flow = flow or PyramidalLK()
        self.flow_min_confidence = flow_min_confidence
        self._clock = clock
        self._last_area: float | None = None
        self._profile_name: str | None = None
        self._frame_count = 0
        self._previous_gray: np.ndarray | None = None
        self._previous_candidates: tuple[TargetCandidate, ...] = ()
        self._last_world_position: np.ndarray | None = None
        self._last_world_timestamp: float | None = None
        self._manager: CameraManager | None = None
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    def _flow_candidates(
        self, image: np.ndarray, frame: FramePacket
    ) -> list[TargetCandidate]:
        if self._previous_gray is None or not self._previous_candidates:
            return []
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        flowed: list[TargetCandidate] = []
        for candidate in self._previous_candidates:
            result = self.flow.track(self._previous_gray, gray, candidate.corners)
            if len(result.valid) != len(candidate.corners) or not result.valid.all():
                continue
            corners = result.current_points.astype(np.float64)
            old_lengths = np.linalg.norm(
                candidate.corners - np.roll(candidate.corners, 1, axis=0), axis=1
            )
            new_lengths = np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)
            scale_error = float(np.max(np.abs(new_lengths / np.maximum(old_lengths, 1e-6) - 1)))
            confidence = candidate.score * max(0.0, 1.0 - scale_error) * 0.96
            if confidence < self.flow_min_confidence:
                continue
            contour = np.rint(corners).astype(np.int32).reshape(-1, 1, 2)
            flowed.append(
                TargetCandidate(
                    candidate.profile, corners, contour, tuple(corners.mean(axis=0)),
                    float(abs(cv2.contourArea(contour))), confidence,
                    frame.capture_timestamp, "optical_flow", frame_id=frame.frame_id,
                    color_confidence=candidate.color_confidence * 0.96,
                    edge_confidence=confidence, shape_confidence=confidence,
                    geometry_confidence=max(0.0, 1.0 - scale_error),
                )
            )
        return flowed

    async def process(
        self, frame: FramePacket, aircraft_state: AircraftState | None = None
    ) -> VisionPipelineResult:
        timeline = LatencyTimeline(frame.capture_timestamp)
        timeline.mark("receive", frame.receive_timestamp)
        processed = self.preprocessor.process(
            frame.image, self.camera.camera_matrix, self.camera.distortion
        )
        timeline.mark("preprocess", max(frame.receive_timestamp, self._clock()))
        # A single ROI is only safe for a single target; preserve full-frame
        # search whenever the tracker map is maintaining simultaneous targets.
        roi = (
            None
            if len(self._previous_candidates) > 1
            else self.roi_policy.region(processed.image.shape)
        )
        due = self._frame_count % self.detector_interval_frames == 0
        force = (
            not self._previous_candidates
            or any(candidate.score < self.flow_min_confidence for candidate in self._previous_candidates)
            or self.tracker.status == TrackStatus.LOST
        )
        detector_ran = due or force
        candidates = (
            self.detector.detect(
                processed.image, frame.capture_timestamp, roi, frame.frame_id
            )
            if detector_ran else self._flow_candidates(processed.image, frame)
        )
        # Flow failure/geometry rejection immediately falls back to a full detector pass.
        if not detector_ran and not candidates:
            candidates = self.detector.detect(
                processed.image, frame.capture_timestamp, None, frame.frame_id
            )
            detector_ran = True
        neural_result = await self.async_detector.detect(frame) if self.async_detector else None
        timeline.mark("detect", max(max(timeline.marks.values()), self._clock()))
        self._frame_count += 1

        if self.tracker.initialized:
            self.tracker.predict(frame.capture_timestamp)
            observation = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
            innovation_covariance = (
                observation @ self.tracker.covariance @ observation.T
                + np.eye(2) * self.tracker.config.measurement_noise
            )
            association = self.associator.associate(
                candidates,
                self.tracker.state[:2],
                innovation_covariance,
                profile_name=self._profile_name,
                previous_area_px=self._last_area,
            )
            selected = association.candidate
        else:
            selected = candidates[0] if candidates else None

        if selected is not None:
            track = self.tracker.update(np.asarray(selected.centroid), frame.capture_timestamp)
            self.roi_policy.found(selected.centroid)
            self._last_area = selected.area_px
            self._profile_name = selected.profile.name
            pose = self.pose_estimator.estimate(selected).validated
        else:
            track = self.tracker.update(None, frame.capture_timestamp)
            self.roi_policy.missed()
            pose = None
        all_poses = {
            candidate.candidate_id: estimate.validated
            for candidate in candidates
            if (estimate := self.pose_estimator.estimate(candidate)).validated is not None
        }
        timeline.mark("pose", max(max(timeline.marks.values()), self._clock()))
        tracks = self.tracker_map.update(candidates, frame.capture_timestamp)
        timeline.mark("track", max(max(timeline.marks.values()), self._clock()))

        evidence: list[VisionEvidence] = []
        if selected is not None:
            evidence.append(
                VisionEvidence(selected.source, selected.score, frame.capture_timestamp, 0.9, "image")
            )
        if pose is not None:
            pose_confidence = float(np.exp(-pose.reprojection_error_px / 3.0))
            evidence.append(
                VisionEvidence("pose", pose_confidence, frame.capture_timestamp, 0.8, "geometry")
            )
        now = max(frame.capture_timestamp, self._clock())
        if neural_result is not None and neural_result.is_fresh(now, self.async_max_age_s):
            neural_confidence = max((item.score for item in neural_result.candidates), default=0.0)
            evidence.append(
                VisionEvidence(neural_result.backend, neural_confidence, neural_result.frame_timestamp, 0.85, "neural")
            )
        observed_pose = predicted_pose = None
        world_velocity = np.zeros(3)
        uncertainty = np.eye(3)
        if pose is not None:
            aircraft = aircraft_state or AircraftState(frame.capture_timestamp)
            camera_position = pose.translation_vector
            body_position = camera_to_body(camera_position, self.camera)
            world_position = camera_to_world(camera_position, self.camera, aircraft)
            uncertainty = np.eye(3) * max(1e-6, pose.reprojection_error_px + 1e-3)
            observed_pose = ObservedPose3D(
                frame.capture_timestamp, camera_position, body_position, world_position,
                pose.rotation_vector, uncertainty,
            )
            if self._last_world_position is not None and self._last_world_timestamp is not None:
                dt = frame.capture_timestamp - self._last_world_timestamp
                if dt > 0:
                    world_velocity = (world_position - self._last_world_position) / dt
            horizon = max(0.0, now - frame.capture_timestamp)
            predicted_pose = PredictedPose3D(
                now, horizon, world_position + world_velocity * horizon,
                world_velocity, uncertainty * (1 + horizon),
            )
            self._last_world_position = world_position
            self._last_world_timestamp = frame.capture_timestamp
        fused = self.fusion_engine.fuse(
            evidence, now, observed_pose=observed_pose, predicted_pose=predicted_pose,
            velocity=world_velocity, uncertainty=uncertainty, track_state=track.status.value,
        )
        timeline.mark("fusion", max(max(timeline.marks.values()), self._clock()))
        lock_evidence = tuple(item.source for item in evidence)
        lock = self.lock_machine.update(
            selected is not None, fused.confidence,
            track_confirmed=track.status == TrackStatus.TRACKING,
            pose_valid=pose is not None,
            predictive=predicted_pose is not None and predicted_pose.horizon_s > 0,
            evidence=lock_evidence,
        )
        if selected is not None:
            self.scene.update(
                SceneObject("primary-target", frame.capture_timestamp, fused.confidence, selected)
            )
        self.scene.purge(now)
        timeline.mark("planning", max(max(timeline.marks.values()), self._clock()))
        timeline.mark("command", max(max(timeline.marks.values()), self._clock()))
        gray = (
            processed.image if processed.image.ndim == 2
            else cv2.cvtColor(processed.image, cv2.COLOR_BGR2GRAY)
        )
        self._previous_gray = gray.copy()
        self._previous_candidates = tuple(candidates)
        return VisionPipelineResult(
            frame, processed, tuple(candidates), selected, pose, track, fused, lock, timeline,
            tracks, all_poses, detector_ran,
        )

    async def process_latest(
        self,
        buffer: FrameBuffer | CameraManager,
        max_age_s: float | None = None,
        now: float | None = None,
        aircraft_state: AircraftState | None = None,
    ) -> VisionPipelineResult | None:
        actual_buffer = buffer.frame_buffer if isinstance(buffer, CameraManager) else buffer
        frame = actual_buffer.latest(max_age_s, now)
        return None if frame is None else await self.process(frame, aircraft_state)

    async def start(
        self,
        manager: CameraManager,
        on_result: Callable[[VisionPipelineResult], Any] | None = None,
        *,
        max_age_s: float | None = None,
    ) -> None:
        if self._started:
            return
        self._manager = manager
        await manager.start()
        await manager.start_processing(self.process, on_result, max_age_s=max_age_s)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        assert self._manager is not None
        await self._manager.stop()
        self._manager = None
        self._started = False
