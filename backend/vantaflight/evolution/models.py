from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Mapping


@dataclass(frozen=True)
class AlgorithmGenome:
    """Version fingerprint for the full autonomy stack used by a run."""

    sense: str
    state: str
    mission: str
    motion: str
    payload: str
    execution: str
    runtime: str
    vehicle: str
    parameters: Mapping[str, float | int | str | bool] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        payload = {
            "sense": self.sense,
            "state": self.state,
            "mission": self.mission,
            "motion": self.motion,
            "payload": self.payload,
            "execution": self.execution,
            "runtime": self.runtime,
            "vehicle": self.vehicle,
            "parameters": dict(sorted(self.parameters.items())),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class RunPackage:
    run_id: str
    mission_type: str
    scenario_id: str
    seed: int
    git_sha: str
    software_version: str
    genome: AlgorithmGenome
    vehicle_profile: str
    world_profile: str
    camera_profile: str | None = None
    payload_profile: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    success: bool = False

    def __post_init__(self) -> None:
        if not self.run_id or not self.mission_type or not self.git_sha:
            raise ValueError("run package identity fields are required")
        if any(not math.isfinite(float(v)) for v in self.metrics.values()):
            raise ValueError("run metrics must be finite")


@dataclass(frozen=True)
class WeaknessRecord:
    mission: str
    stage: str
    condition: str
    subsystem: str
    failure_rate: float
    severity: float
    sample_count: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.failure_rate <= 1.0 or not 0.0 <= self.severity <= 1.0:
            raise ValueError("failure rate/severity must be between 0 and 1")
        if self.sample_count < 0:
            raise ValueError("sample count cannot be negative")

    @property
    def priority(self) -> float:
        evidence = min(1.0, self.sample_count / 50.0)
        return self.failure_rate * self.severity * (0.35 + 0.65 * evidence)
