"""In-process V0.5 state exposed to API clients without coupling control loops."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .api_models import (
    CameraProfileModel,
    CourseDetailModel,
    ExperimentResultModel,
    FrameMetricsModel,
    RaceStateModel,
    RunMetricModel,
    SceneStateModel,
    SimulationStateModel,
    TargetEstimateModel,
    VisionStatusModel,
    Vector3Model,
)


@dataclass
class IntelligenceRuntime:
    """Latest-state boundary for low-rate API/WebSocket publication.

    Perception and racing loops may atomically replace these small snapshots.
    Camera images deliberately do not cross this telemetry boundary.
    """

    vision_status: VisionStatusModel = field(default_factory=VisionStatusModel)
    camera_profiles: dict[str, CameraProfileModel] = field(default_factory=dict)
    courses: dict[str, CourseDetailModel] = field(default_factory=dict)
    tracks: dict[str, TargetEstimateModel] = field(default_factory=dict)
    scene: SceneStateModel = field(default_factory=SceneStateModel)
    race: RaceStateModel = field(default_factory=RaceStateModel)
    simulation: SimulationStateModel = field(default_factory=SimulationStateModel)
    run_metrics: list[RunMetricModel] = field(default_factory=list)
    experiments: list[ExperimentResultModel] = field(default_factory=list)

    def publish_metric(self, metric: RunMetricModel, limit: int = 1_000) -> None:
        self.run_metrics.append(metric)
        del self.run_metrics[:-limit]

    def publish_vision_result(
        self,
        result: Any,
        *,
        frame_metrics: Any | None = None,
    ) -> None:
        """Convert a VantaSight result into image-free latest-state contracts."""

        def vector(values: Any) -> Vector3Model:
            return Vector3Model(
                x=float(values[0]), y=float(values[1]), z=float(values[2])
            )

        fused = result.fusion
        observed = (
            None
            if fused.observed_pose is None
            else vector(fused.observed_pose.position_world_m)
        )
        predicted = (
            None
            if fused.predicted_pose is None
            else vector(fused.predicted_pose.position_world_m)
        )
        selected = result.selected
        if selected is not None:
            uncertainty = float(max(fused.uncertainty.diagonal()))
            estimate = TargetEstimateModel(
                target_id=selected.candidate_id,
                profile_id=selected.profile_id,
                observed_position=observed,
                predicted_position=predicted,
                velocity=vector(fused.velocity),
                confidence=fused.confidence,
                uncertainty=uncertainty,
                measurement_age_s=fused.age_s,
                track_state=result.lock.state.value,
                evidence={
                    name: float(value)
                    for name, value in fused.contributions.items()
                },
            )
            self.tracks[estimate.target_id] = estimate
            while len(self.tracks) > 64:
                self.tracks.pop(next(iter(self.tracks)))
            self.scene = SceneStateModel(
                timestamp=fused.timestamp,
                current=estimate,
                next=self.scene.next,
                future=self.scene.future,
            )

        metrics = FrameMetricsModel()
        if frame_metrics is not None:
            metrics = FrameMetricsModel(
                frames_captured=int(frame_metrics.frames_captured),
                frames_processed=int(frame_metrics.frames_processed),
                dropped_frames=int(frame_metrics.frames_dropped),
                queue_depth=int(frame_metrics.depth),
                oldest_frame_age_s=float(frame_metrics.oldest_frame_age_s or 0.0),
                current_frame_age_s=float(frame_metrics.current_frame_age_s or 0.0),
            )
        self.vision_status = VisionStatusModel(
            running=True,
            source=result.frame.camera_source,
            lock_state=result.lock.state.value,
            frame_metrics=metrics,
            pipeline_latency_ms=result.timeline.end_to_end_s * 1_000,
        )
