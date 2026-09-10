"""Typed V0.5 REST and WebSocket contracts."""
from __future__ import annotations

import math
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class WebSocketEventType(str, Enum):
    TELEMETRY = "telemetry"
    EVENT = "event"
    VISION_STATE = "vision_state"
    SCENE_STATE = "scene_state"
    RACE_STATE = "race_state"
    SIMULATION_STATE = "simulation_state"
    RUN_METRIC = "run_metric"


class Vector3Model(BaseModel):
    x: float
    y: float
    z: float


class FrameMetricsModel(BaseModel):
    frames_captured: int = 0
    frames_processed: int = 0
    dropped_frames: int = 0
    queue_depth: int = 0
    oldest_frame_age_s: float = 0.0
    current_frame_age_s: float = 0.0


class VisionStatusModel(BaseModel):
    running: bool = False
    source: str | None = None
    lock_state: str = "SEARCHING"
    frame_metrics: FrameMetricsModel = Field(default_factory=FrameMetricsModel)
    pipeline_latency_ms: float = 0.0


class CameraProfileModel(BaseModel):
    camera_id: str
    width: int
    height: int
    fps: float
    focal_length_x: float | None = None
    focal_length_y: float | None = None
    camera_matrix: list[list[float]]
    distortion_coefficients: list[float] = Field(default_factory=list)
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None
    mount_transform: list[list[float]]
    estimated_capture_latency_s: float = 0.0
    calibration_version: str


class TargetEstimateModel(BaseModel):
    target_id: str
    profile_id: str
    observed_position: Vector3Model | None = None
    predicted_position: Vector3Model | None = None
    velocity: Vector3Model = Field(
        default_factory=lambda: Vector3Model(x=0.0, y=0.0, z=0.0)
    )
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0)
    measurement_age_s: float = Field(ge=0.0)
    track_state: str
    evidence: dict[str, str | int | float | bool] = Field(default_factory=dict)


class SceneStateModel(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    current: TargetEstimateModel | None = None
    next: TargetEstimateModel | None = None
    future: TargetEstimateModel | None = None


class DesiredTrajectoryModel(BaseModel):
    desired_position: Vector3Model
    desired_velocity: Vector3Model
    desired_acceleration: Vector3Model
    desired_yaw: float
    timestamp: float
    trajectory_id: str
    planner_confidence: float = Field(ge=0.0, le=1.0)


class RaceStateModel(BaseModel):
    state: str = "IDLE"
    timestamp: float = Field(default_factory=time.time)
    trajectory: DesiredTrajectoryModel | None = None
    aggression_scale: float = Field(default=0.0, ge=0.0, le=1.0)


class SimulationStateModel(BaseModel):
    run_id: str | None = None
    course_id: str | None = None
    status: str = "idle"
    timestamp: float = Field(default_factory=time.time)


class RunMetricModel(BaseModel):
    run_id: str
    scope: str = "run"
    scope_id: str | None = None
    metric_name: str
    metric_value: float
    unit: str | None = None
    timestamp: float = Field(default_factory=time.time)


class ExperimentResultModel(BaseModel):
    experiment_id: str
    configuration_id: str | None = None
    candidate: dict[str, float | int | str | bool]
    score: float | None = None
    status: str


class CourseGenerationRequest(BaseModel):
    seed: int
    mode: str = "RANDOM"
    gate_count: int = Field(default=8, ge=3, le=64)
    width: float = Field(default=30.0, gt=0)
    length: float = Field(default=50.0, gt=0)
    height: float = Field(default=12.0, gt=0)
    floor: float = 0.0
    ceiling: float | None = None
    boundary_margin: float = Field(default=1.5, ge=0)

    @model_validator(mode="after")
    def validate_vertical_bounds(self) -> "CourseGenerationRequest":
        if self.ceiling is not None and not math.isclose(
            self.ceiling, self.floor + self.height
        ):
            raise ValueError("ceiling must equal floor + height")
        return self


class CourseDetailModel(BaseModel):
    id: str
    seed: int
    mode: str
    safe_volume: dict[str, Any]
    path: list[list[float]]
    gates: list[dict[str, Any]]
    difficulty: dict[str, float]


class CourseValidationModel(BaseModel):
    course_id: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrainingCampaignRequest(BaseModel):
    name: str = "Untitled Campaign"
    description: str = ""
    course_modes: list[str] = Field(default_factory=lambda: ["RANDOM"])
    seed_range: tuple[int, int] = (0, 10)
    gate_counts: list[int] = Field(default_factory=lambda: [8, 12, 16])
    difficulty_tiers: list[str] = Field(default_factory=lambda: ["MODERATE"])
    fault_profiles: list[list[str]] = Field(default_factory=list)
    max_time_per_run_s: float = 300.0
    max_runs: int = 0
    stop_on_failure: bool = False


class TrainingCampaignModel(BaseModel):
    campaign_id: str
    name: str
    state: str
    total_runs: int
    completed_runs: int


class TrainingSummaryModel(BaseModel):
    campaign_id: str
    state: str
    total_runs: int
    completed_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    avg_gate_completion: float
    avg_race_time_s: float
    failure_breakdown: dict[str, int]
    elapsed_s: float


class AutoCurriculumRequest(BaseModel):
    tiers: list[str] | None = None
    seeds_per_tier: int = Field(default=3, ge=1, le=100)
    gate_counts_per_tier: int = Field(default=2, ge=1, le=10)
