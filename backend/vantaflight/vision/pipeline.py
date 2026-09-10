"""End-to-end V0.5 vision pipeline wiring."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .association import CandidateAssociator
from .capture import FrameBuffer
from .concepts import CameraProfile, FramePacket, PoseEstimate, TargetCandidate, TargetProfile
from .detection import ClassicalTargetDetector
from .fusion import FusionResult, VantaFusion, VisionEvidence
from .inference import AsyncDetector
from .latency import LatencyTimeline
from .lock import LockSnapshot, TargetLock
from .pose import PoseEstimator
from .preprocess import OpenCVPreprocessor, PreprocessResult
from .roi import ROISearchPolicy
from .scene import SceneObject, VantaScene
from .tracking import KalmanTargetTracker, TrackSnapshot


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.camera = camera
        self.preprocessor = preprocessor or OpenCVPreprocessor()
        self.detector = detector or ClassicalTargetDetector(profiles)
        self.pose_estimator = pose_estimator or PoseEstimator(camera)
        self.tracker = tracker or KalmanTargetTracker()
        self.associator = associator or CandidateAssociator(self.tracker.config.mahalanobis_gate)
        self.roi_policy = roi_policy or ROISearchPolicy()
        self.fusion_engine = fusion or VantaFusion()
        self.lock_machine = lock or TargetLock()
        self.scene = scene or VantaScene()
        self.async_detector = async_detector
        self.async_max_age_s = async_max_age_s
        self._clock = clock
        self._last_area: float | None = None
        self._profile_name: str | None = None

    async def process(self, frame: FramePacket) -> VisionPipelineResult:
        timeline = LatencyTimeline(frame.timestamp)
        processed = self.preprocessor.process(
            frame.image, self.camera.camera_matrix, self.camera.distortion
        )
        timeline.mark("preprocess", max(frame.timestamp, self._clock()))
        roi = self.roi_policy.region(processed.image.shape)
        candidates = self.detector.detect(processed.image, frame.timestamp, roi)
        neural_result = await self.async_detector.detect(frame) if self.async_detector else None
        timeline.mark("detect", max(max(timeline.marks.values()), self._clock()))

        if self.tracker.initialized:
            self.tracker.predict(frame.timestamp)
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
            track = self.tracker.update(np.asarray(selected.centroid), frame.timestamp)
            self.roi_policy.found(selected.centroid)
            self._last_area = selected.area_px
            self._profile_name = selected.profile.name
            pose_result = self.pose_estimator.estimate(selected)
            pose = pose_result.validated
        else:
            track = self.tracker.update(None, frame.timestamp)
            self.roi_policy.missed()
            pose = None
        timeline.mark("track_pose", max(max(timeline.marks.values()), self._clock()))

        evidence: list[VisionEvidence] = []
        if selected is not None:
            evidence.append(VisionEvidence("classical", selected.score, frame.timestamp, 0.9, "image"))
        if pose is not None:
            pose_confidence = float(np.exp(-pose.reprojection_error_px / 3.0))
            evidence.append(VisionEvidence("pose", pose_confidence, frame.timestamp, 0.8, "geometry"))
        now = max(frame.timestamp, self._clock())
        if neural_result is not None and neural_result.is_fresh(now, self.async_max_age_s):
            neural_confidence = max((item.score for item in neural_result.candidates), default=0.0)
            evidence.append(
                VisionEvidence(neural_result.backend, neural_confidence, neural_result.frame_timestamp, 0.85, "neural")
            )
        fused = self.fusion_engine.fuse(evidence, now)
        lock = self.lock_machine.update(selected is not None, fused.confidence)
        if selected is not None:
            self.scene.update(SceneObject("primary-target", frame.timestamp, fused.confidence, selected))
        self.scene.purge(now)
        timeline.mark("complete", max(max(timeline.marks.values()), self._clock()))
        return VisionPipelineResult(
            frame, processed, tuple(candidates), selected, pose, track, fused, lock, timeline
        )

    async def process_latest(
        self,
        buffer: FrameBuffer,
        max_age_s: float | None = None,
        now: float | None = None,
    ) -> VisionPipelineResult | None:
        frame = buffer.latest(max_age_s, now)
        return None if frame is None else await self.process(frame)
