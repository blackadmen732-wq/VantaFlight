"""In-process V0.5 state exposed to API clients without coupling control loops."""
from __future__ import annotations

from dataclasses import dataclass, field

from .api_models import (
    CameraProfileModel,
    ExperimentResultModel,
    RaceStateModel,
    RunMetricModel,
    SceneStateModel,
    SimulationStateModel,
    TargetEstimateModel,
    VisionStatusModel,
)


@dataclass
class IntelligenceRuntime:
    """Latest-state boundary for low-rate API/WebSocket publication.

    Perception and racing loops may atomically replace these small snapshots.
    Camera images deliberately do not cross this telemetry boundary.
    """

    vision_status: VisionStatusModel = field(default_factory=VisionStatusModel)
    camera_profiles: dict[str, CameraProfileModel] = field(default_factory=dict)
    tracks: dict[str, TargetEstimateModel] = field(default_factory=dict)
    scene: SceneStateModel = field(default_factory=SceneStateModel)
    race: RaceStateModel = field(default_factory=RaceStateModel)
    simulation: SimulationStateModel = field(default_factory=SimulationStateModel)
    run_metrics: list[RunMetricModel] = field(default_factory=list)
    experiments: list[ExperimentResultModel] = field(default_factory=list)

    def publish_metric(self, metric: RunMetricModel, limit: int = 1_000) -> None:
        self.run_metrics.append(metric)
        del self.run_metrics[:-limit]
