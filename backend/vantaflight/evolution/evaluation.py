from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from typing import Iterable

from .models import RunPackage, WeaknessRecord


@dataclass(frozen=True)
class CandidateScore:
    name: str
    runs: int
    success_rate: float
    mean_score: float
    worst_score: float


class WeaknessMap:
    """Aggregate failure evidence by mission/stage/condition/subsystem."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str], list[tuple[bool, float]]] = defaultdict(list)

    def add(
        self,
        *,
        mission: str,
        stage: str,
        condition: str,
        subsystem: str,
        success: bool,
        severity: float,
    ) -> None:
        if not 0.0 <= severity <= 1.0:
            raise ValueError("severity must be between 0 and 1")
        self._records[(mission, stage, condition, subsystem)].append((success, severity))

    def ranked(self) -> list[WeaknessRecord]:
        result: list[WeaknessRecord] = []
        for (mission, stage, condition, subsystem), samples in self._records.items():
            failures = [severity for success, severity in samples if not success]
            failure_rate = len(failures) / len(samples)
            severity = sum(failures) / len(failures) if failures else 0.0
            result.append(
                WeaknessRecord(
                    mission=mission,
                    stage=stage,
                    condition=condition,
                    subsystem=subsystem,
                    failure_rate=failure_rate,
                    severity=severity,
                    sample_count=len(samples),
                )
            )
        return sorted(result, key=lambda r: (-r.priority, r.mission, r.stage, r.condition, r.subsystem))


class ChampionChallengerEvaluator:
    """Deterministic promotion gate using matched scenario/seed evidence.

    The evaluator never promotes a candidate merely because one aggregate
    metric improved. The challenger must use the same scenario/seed set,
    improve the configured score margin, and not regress success rate or the
    worst-case score beyond the supplied tolerance.
    """

    def __init__(
        self,
        *,
        score_metric: str = "utility",
        minimum_improvement: float = 0.01,
        max_worst_case_regression: float = 0.0,
    ) -> None:
        self.score_metric = score_metric
        self.minimum_improvement = minimum_improvement
        self.max_worst_case_regression = max_worst_case_regression

    def evaluate(
        self,
        champion_name: str,
        champion_runs: Iterable[RunPackage],
        challenger_name: str,
        challenger_runs: Iterable[RunPackage],
    ) -> tuple[bool, CandidateScore, CandidateScore, str]:
        champion = list(champion_runs)
        challenger = list(challenger_runs)
        champ_keys = {(r.scenario_id, r.seed) for r in champion}
        challenger_keys = {(r.scenario_id, r.seed) for r in challenger}
        if champ_keys != challenger_keys or not champ_keys:
            raise ValueError("champion and challenger must run identical non-empty scenario/seed sets")

        champion_score = self._score(champion_name, champion)
        challenger_score = self._score(challenger_name, challenger)

        if challenger_score.success_rate < champion_score.success_rate:
            return False, champion_score, challenger_score, "success-rate regression"
        if challenger_score.worst_score < champion_score.worst_score - self.max_worst_case_regression:
            return False, champion_score, challenger_score, "worst-case regression"
        improvement = challenger_score.mean_score - champion_score.mean_score
        if improvement < self.minimum_improvement:
            return False, champion_score, challenger_score, "insufficient mean improvement"
        return True, champion_score, challenger_score, "promotion candidate"

    def _score(self, name: str, runs: list[RunPackage]) -> CandidateScore:
        values = [float(r.metrics.get(self.score_metric, 0.0)) for r in runs]
        return CandidateScore(
            name=name,
            runs=len(runs),
            success_rate=sum(1 for r in runs if r.success) / len(runs),
            mean_score=sum(values) / len(values),
            worst_score=min(values),
        )
