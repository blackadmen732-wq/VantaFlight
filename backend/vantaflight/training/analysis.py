"""Failure classification and training result analysis."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import FailureCategory, RunResult


def classify_failure(result: RunResult) -> list[FailureCategory]:
    """Derive failure categories from a run result's raw failure strings."""
    categories: list[FailureCategory] = []

    for failure in result.failures:
        lower = failure.lower()
        if "gate" in lower and ("miss" in lower or "skip" in lower):
            categories.append(FailureCategory.GATE_MISS)
        elif "collision" in lower or "crash" in lower:
            categories.append(FailureCategory.COLLISION)
        elif "timeout" in lower or "max_time" in lower:
            categories.append(FailureCategory.TIMEOUT)
        elif "track" in lower and "lost" in lower:
            categories.append(FailureCategory.TRACKING_LOST)
        elif "confidence" in lower:
            categories.append(FailureCategory.CONFIDENCE_TOO_LOW)
        elif "boundary" in lower or "volume" in lower:
            categories.append(FailureCategory.BOUNDARY_VIOLATION)
        elif "px4" in lower or "disconnect" in lower:
            categories.append(FailureCategory.PX4_DISCONNECT)
        elif "camera" in lower:
            categories.append(FailureCategory.CAMERA_FAILURE)
        elif "recover" in lower:
            categories.append(FailureCategory.RECOVERY_FAILURE)
        else:
            categories.append(FailureCategory.UNKNOWN)

    if not result.complete and not categories:
        if result.race_time_s >= result.config.max_time_s * 0.95:
            categories.append(FailureCategory.TIMEOUT)
        elif result.gates_passed == 0:
            categories.append(FailureCategory.TRACKING_LOST)
        else:
            categories.append(FailureCategory.UNKNOWN)

    return categories


@dataclass
class TierAnalysis:
    tier: str
    total_runs: int = 0
    successes: int = 0
    failures: int = 0
    avg_gate_completion: float = 0.0
    avg_race_time_s: float = 0.0
    failure_counts: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.successes / self.total_runs if self.total_runs > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "total_runs": self.total_runs,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 3),
            "avg_gate_completion": round(self.avg_gate_completion, 3),
            "avg_race_time_s": round(self.avg_race_time_s, 3),
            "failure_counts": dict(self.failure_counts),
        }


@dataclass
class ModeAnalysis:
    mode: str
    total_runs: int = 0
    successes: int = 0
    avg_gate_completion: float = 0.0
    avg_race_time_s: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.total_runs if self.total_runs > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_runs": self.total_runs,
            "successes": self.successes,
            "success_rate": round(self.success_rate, 3),
            "avg_gate_completion": round(self.avg_gate_completion, 3),
            "avg_race_time_s": round(self.avg_race_time_s, 3),
        }


@dataclass
class CampaignAnalysis:
    campaign_id: str
    total_runs: int = 0
    successes: int = 0
    failures: int = 0
    overall_success_rate: float = 0.0
    overall_gate_completion: float = 0.0
    overall_avg_time_s: float = 0.0
    tier_breakdown: list[TierAnalysis] = field(default_factory=list)
    mode_breakdown: list[ModeAnalysis] = field(default_factory=list)
    top_failures: list[tuple[str, int]] = field(default_factory=list)
    fault_impact: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "total_runs": self.total_runs,
            "successes": self.successes,
            "failures": self.failures,
            "overall_success_rate": round(self.overall_success_rate, 3),
            "overall_gate_completion": round(self.overall_gate_completion, 3),
            "overall_avg_time_s": round(self.overall_avg_time_s, 3),
            "tier_breakdown": [t.to_dict() for t in self.tier_breakdown],
            "mode_breakdown": [m.to_dict() for m in self.mode_breakdown],
            "top_failures": [{"category": cat, "count": count} for cat, count in self.top_failures],
            "fault_impact": {k: round(v, 3) for k, v in self.fault_impact.items()},
        }


def analyze_campaign(campaign_id: str, results: list[RunResult]) -> CampaignAnalysis:
    """Produce aggregate analysis from a list of completed run results."""
    if not results:
        return CampaignAnalysis(campaign_id=campaign_id)

    successes = sum(1 for r in results if r.success)
    gate_completions = [r.gate_completion_rate for r in results]
    race_times = [r.race_time_s for r in results if r.race_time_s > 0]

    all_failure_cats: list[str] = []
    for r in results:
        cats = classify_failure(r) if not r.failure_categories else [
            FailureCategory(c) for c in r.failure_categories
        ]
        all_failure_cats.extend(c.value for c in cats)

    failure_counter = Counter(all_failure_cats)

    # Per-tier breakdown
    tier_groups: dict[str, list[RunResult]] = {}
    for r in results:
        tier = r.config.difficulty_tier
        tier_groups.setdefault(tier, []).append(r)

    tier_analyses = []
    for tier, group in sorted(tier_groups.items()):
        t_success = sum(1 for r in group if r.success)
        t_gc = [r.gate_completion_rate for r in group]
        t_times = [r.race_time_s for r in group if r.race_time_s > 0]
        t_failures: list[str] = []
        for r in group:
            cats = classify_failure(r) if not r.failure_categories else [
                FailureCategory(c) for c in r.failure_categories
            ]
            t_failures.extend(c.value for c in cats)
        tier_analyses.append(TierAnalysis(
            tier=tier,
            total_runs=len(group),
            successes=t_success,
            failures=len(group) - t_success,
            avg_gate_completion=sum(t_gc) / len(t_gc) if t_gc else 0.0,
            avg_race_time_s=sum(t_times) / len(t_times) if t_times else 0.0,
            failure_counts=dict(Counter(t_failures)),
        ))

    # Per-mode breakdown
    mode_groups: dict[str, list[RunResult]] = {}
    for r in results:
        mode = r.config.course_mode
        mode_groups.setdefault(mode, []).append(r)

    mode_analyses = []
    for mode, group in sorted(mode_groups.items()):
        m_success = sum(1 for r in group if r.success)
        m_gc = [r.gate_completion_rate for r in group]
        m_times = [r.race_time_s for r in group if r.race_time_s > 0]
        mode_analyses.append(ModeAnalysis(
            mode=mode,
            total_runs=len(group),
            successes=m_success,
            avg_gate_completion=sum(m_gc) / len(m_gc) if m_gc else 0.0,
            avg_race_time_s=sum(m_times) / len(m_times) if m_times else 0.0,
        ))

    # Fault impact: compare success rate with faults vs without
    fault_impact: dict[str, float] = {}
    no_fault_results = [r for r in results if not r.config.fault_profiles]
    baseline_rate = (
        sum(1 for r in no_fault_results if r.success) / len(no_fault_results)
        if no_fault_results else 0.0
    )
    fault_groups: dict[str, list[RunResult]] = {}
    for r in results:
        for fp in r.config.fault_profiles:
            fault_groups.setdefault(fp, []).append(r)
    for fp_name, group in fault_groups.items():
        fp_rate = sum(1 for r in group if r.success) / len(group) if group else 0.0
        fault_impact[fp_name] = baseline_rate - fp_rate

    return CampaignAnalysis(
        campaign_id=campaign_id,
        total_runs=len(results),
        successes=successes,
        failures=len(results) - successes,
        overall_success_rate=successes / len(results),
        overall_gate_completion=sum(gate_completions) / len(gate_completions),
        overall_avg_time_s=sum(race_times) / len(race_times) if race_times else 0.0,
        tier_breakdown=tier_analyses,
        mode_breakdown=mode_analyses,
        top_failures=failure_counter.most_common(10),
        fault_impact=fault_impact,
    )
