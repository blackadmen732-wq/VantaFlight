"""Turn training-campaign results into evolution evidence.

Training campaigns are the evidence source for the improvement loop:

* ``weakness_report`` folds every run into a `WeaknessMap`, one row per
  condition (course mode, difficulty tier and injected faults), ranked so the
  conditions that fail most often, most severely, with the most evidence come
  first.
* ``run_packages`` describes each run as a `RunPackage` so two campaigns can
  go through the `ChampionChallengerEvaluator` promotion gate. The gate only
  compares campaigns that flew the identical scenario/seed set.

Utility (the default promotion metric) is gate completion (0..1) plus 0.5 for
a clean finish, so finishing matters more than getting slightly further.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from ..training.models import RunResult
from .evaluation import WeaknessMap
from .models import AlgorithmGenome, RunPackage

MISSION = "RACE"
STAGE = "course"
SUBSYSTEM = "autonomy"


def condition_of(result: RunResult) -> str:
    c = result.config
    faults = "+".join(sorted(c.fault_profiles)) or "clean"
    return f"{c.course_mode} · {c.difficulty_tier} · {faults}"


def scenario_of(result: RunResult) -> str:
    c = result.config
    faults = "+".join(sorted(c.fault_profiles)) or "clean"
    return f"{c.course_mode}-{c.gate_count}g-{c.difficulty_tier}-{faults}"


def severity_of(result: RunResult) -> float:
    """How bad a run was: 0 for a clean finish, up to 1 for no gates at all."""
    if result.success:
        return 0.0
    return min(1.0, max(0.0, 1.0 - result.gate_completion_rate))


def utility_of(result: RunResult) -> float:
    return result.gate_completion_rate + (0.5 if result.success else 0.0)


def weakness_report(results: Iterable[RunResult]) -> list[dict]:
    weaknesses = WeaknessMap()
    causes: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        condition = condition_of(r)
        weaknesses.add(
            mission=MISSION,
            stage=STAGE,
            condition=condition,
            subsystem=SUBSYSTEM,
            success=r.success,
            # A failed run is always somewhat severe, even if it passed gates.
            severity=max(severity_of(r), 0.0 if r.success else 0.1),
        )
        causes[condition].update(r.failure_categories)
    return [
        {
            "mission": w.mission,
            "condition": w.condition,
            "failure_rate": round(w.failure_rate, 4),
            "severity": round(w.severity, 4),
            "sample_count": w.sample_count,
            "priority": round(w.priority, 4),
            "top_causes": [
                {"category": cat, "count": n} for cat, n in causes[w.condition].most_common(3)
            ],
        }
        for w in weaknesses.ranked()
    ]


def run_packages(results: Iterable[RunResult], software_version: str, git_sha: str = "local") -> list[RunPackage]:
    genome = AlgorithmGenome(
        sense="vantasight",
        state="vantastate",
        mission="race",
        motion="vantarace",
        payload="none",
        execution="simulation",
        runtime="local",
        vehicle="sim-quad",
    )
    return [
        RunPackage(
            run_id=r.run_id,
            mission_type=MISSION,
            scenario_id=scenario_of(r),
            seed=r.config.seed,
            git_sha=git_sha,
            software_version=software_version,
            genome=genome,
            vehicle_profile="sim-quad",
            world_profile=r.config.course_mode,
            metrics={
                "utility": utility_of(r),
                "gate_completion": r.gate_completion_rate,
                "race_time_s": r.race_time_s,
            },
            success=r.success,
        )
        for r in results
    ]
