"""Training engine data models — campaigns, runs, and results."""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


class CampaignState(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureCategory(str, enum.Enum):
    GATE_MISS = "GATE_MISS"
    COLLISION = "COLLISION"
    TIMEOUT = "TIMEOUT"
    TRACKING_LOST = "TRACKING_LOST"
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    BOUNDARY_VIOLATION = "BOUNDARY_VIOLATION"
    PX4_DISCONNECT = "PX4_DISCONNECT"
    CAMERA_FAILURE = "CAMERA_FAILURE"
    RECOVERY_FAILURE = "RECOVERY_FAILURE"
    UNKNOWN = "UNKNOWN"


class DifficultyTier(str, enum.Enum):
    BEGINNER = "BEGINNER"
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    EXPERT = "EXPERT"
    ADVERSARIAL = "ADVERSARIAL"


@dataclass
class RunConfig:
    course_mode: str = "RANDOM"
    seed: int = 0
    gate_count: int = 12
    fault_profiles: list[str] = field(default_factory=list)
    max_time_s: float = 300.0
    difficulty_tier: str = "MODERATE"

    def to_dict(self) -> dict:
        return {
            "course_mode": self.course_mode,
            "seed": self.seed,
            "gate_count": self.gate_count,
            "fault_profiles": list(self.fault_profiles),
            "max_time_s": self.max_time_s,
            "difficulty_tier": self.difficulty_tier,
        }


@dataclass
class RunResult:
    run_id: str
    campaign_id: str
    config: RunConfig
    gates_passed: int = 0
    total_gates: int = 0
    race_time_s: float = 0.0
    complete: bool = False
    failures: list[str] = field(default_factory=list)
    failure_categories: list[str] = field(default_factory=list)
    faults_injected: int = 0
    max_speed_ms: float = 0.0
    avg_speed_ms: float = 0.0
    min_clearance_m: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_s(self) -> float:
        if self.finished_at > 0 and self.started_at > 0:
            return self.finished_at - self.started_at
        return 0.0

    @property
    def success(self) -> bool:
        return self.complete and len(self.failures) == 0

    @property
    def gate_completion_rate(self) -> float:
        return self.gates_passed / self.total_gates if self.total_gates > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "campaign_id": self.campaign_id,
            "config": self.config.to_dict(),
            "gates_passed": self.gates_passed,
            "total_gates": self.total_gates,
            "race_time_s": round(self.race_time_s, 3),
            "complete": self.complete,
            "success": self.success,
            "failures": list(self.failures),
            "failure_categories": list(self.failure_categories),
            "faults_injected": self.faults_injected,
            "max_speed_ms": round(self.max_speed_ms, 2),
            "avg_speed_ms": round(self.avg_speed_ms, 2),
            "min_clearance_m": round(self.min_clearance_m, 3),
            "gate_completion_rate": round(self.gate_completion_rate, 3),
            "duration_s": round(self.duration_s, 3),
        }


@dataclass
class CampaignConfig:
    name: str = "Untitled Campaign"
    description: str = ""
    course_modes: list[str] = field(default_factory=lambda: ["RANDOM"])
    seed_range: tuple[int, int] = (0, 10)
    gate_counts: list[int] = field(default_factory=lambda: [8, 12, 16])
    difficulty_tiers: list[str] = field(default_factory=lambda: ["MODERATE"])
    fault_profiles: list[list[str]] = field(default_factory=list)
    max_time_per_run_s: float = 300.0
    max_runs: int = 0
    stop_on_failure: bool = False

    @property
    def planned_run_count(self) -> int:
        seed_count = max(1, self.seed_range[1] - self.seed_range[0])
        fault_count = max(1, len(self.fault_profiles))
        return (
            len(self.course_modes)
            * seed_count
            * len(self.gate_counts)
            * len(self.difficulty_tiers)
            * fault_count
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "course_modes": list(self.course_modes),
            "seed_range": list(self.seed_range),
            "gate_counts": list(self.gate_counts),
            "difficulty_tiers": list(self.difficulty_tiers),
            "fault_profiles": [list(fp) for fp in self.fault_profiles],
            "max_time_per_run_s": self.max_time_per_run_s,
            "max_runs": self.max_runs,
            "stop_on_failure": self.stop_on_failure,
            "planned_run_count": self.planned_run_count,
        }


@dataclass
class CampaignSummary:
    campaign_id: str
    config: CampaignConfig
    state: CampaignState = CampaignState.PENDING
    total_runs: int = 0
    completed_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    avg_gate_completion: float = 0.0
    avg_race_time_s: float = 0.0
    failure_breakdown: dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successful_runs / self.completed_runs if self.completed_runs > 0 else 0.0

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at > 0 else time.monotonic()
        return end - self.started_at if self.started_at > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "config": self.config.to_dict(),
            "state": self.state.value,
            "total_runs": self.total_runs,
            "completed_runs": self.completed_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "success_rate": round(self.success_rate, 3),
            "avg_gate_completion": round(self.avg_gate_completion, 3),
            "avg_race_time_s": round(self.avg_race_time_s, 3),
            "failure_breakdown": dict(self.failure_breakdown),
            "elapsed_s": round(self.elapsed_s, 3),
        }
