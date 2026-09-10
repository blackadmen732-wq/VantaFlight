"""TrainingEngine — orchestrates campaign execution through SimulationRunner."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..course_lab import CourseGenerator, SafeVolume
from ..course_lab.models import CourseMode
from ..simulation.course_bridge import CourseBridge
from ..simulation.models import SimSessionConfig
from ..simulation.runner import SimulationRunner
from .analysis import analyze_campaign, classify_failure, CampaignAnalysis
from .curriculum import CurriculumBuilder
from .models import (
    CampaignConfig,
    CampaignState,
    CampaignSummary,
    RunConfig,
    RunResult,
)


@dataclass
class ActiveCampaign:
    campaign_id: str
    config: CampaignConfig
    run_queue: list[RunConfig]
    results: list[RunResult] = field(default_factory=list)
    state: CampaignState = CampaignState.PENDING
    current_run_index: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0


class TrainingEngine:
    """Manages training campaign lifecycle: expand curriculum, execute runs via
    SimulationRunner, collect results, and produce analysis."""

    def __init__(self, sim_runner: SimulationRunner | None = None) -> None:
        self._sim_runner = sim_runner or SimulationRunner()
        self._curriculum = CurriculumBuilder()
        self._bridge = CourseBridge()
        self._campaigns: dict[str, ActiveCampaign] = {}
        self._campaign_counter = 0
        self._on_run_complete: Callable[[RunResult], None] | None = None

    def set_run_callback(self, callback: Callable[[RunResult], None]) -> None:
        self._on_run_complete = callback

    @property
    def campaigns(self) -> dict[str, ActiveCampaign]:
        return dict(self._campaigns)

    def create_campaign(self, config: CampaignConfig) -> str:
        self._campaign_counter += 1
        campaign_id = f"campaign_{self._campaign_counter:04d}"
        runs = self._curriculum.expand(config)
        campaign = ActiveCampaign(
            campaign_id=campaign_id,
            config=config,
            run_queue=runs,
        )
        self._campaigns[campaign_id] = campaign
        return campaign_id

    def start_campaign(self, campaign_id: str) -> None:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        if campaign.state not in (CampaignState.PENDING, CampaignState.PAUSED):
            raise RuntimeError(f"cannot start campaign in state {campaign.state.value}")
        campaign.state = CampaignState.RUNNING
        if campaign.started_at == 0:
            campaign.started_at = time.monotonic()

    def pause_campaign(self, campaign_id: str) -> None:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        if campaign.state != CampaignState.RUNNING:
            raise RuntimeError(f"cannot pause campaign in state {campaign.state.value}")
        campaign.state = CampaignState.PAUSED

    def cancel_campaign(self, campaign_id: str) -> None:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        campaign.state = CampaignState.CANCELLED
        campaign.finished_at = time.monotonic()

    def execute_next_run(self, campaign_id: str) -> RunResult | None:
        """Execute the next pending run in the campaign synchronously.

        Returns the RunResult, or None if the campaign has no more runs.
        This is designed to be called in a loop by the orchestrator.
        """
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        if campaign.state != CampaignState.RUNNING:
            return None
        if campaign.current_run_index >= len(campaign.run_queue):
            campaign.state = CampaignState.COMPLETE
            campaign.finished_at = time.monotonic()
            return None

        run_config = campaign.run_queue[campaign.current_run_index]
        run_id = f"{campaign_id}_run_{campaign.current_run_index:04d}"

        result = self._execute_single_run(run_id, campaign_id, run_config)

        categories = classify_failure(result)
        result.failure_categories = [c.value for c in categories]

        campaign.results.append(result)
        campaign.current_run_index += 1

        if self._on_run_complete:
            self._on_run_complete(result)

        if not result.success and campaign.config.stop_on_failure:
            campaign.state = CampaignState.FAILED
            campaign.finished_at = time.monotonic()

        if campaign.current_run_index >= len(campaign.run_queue):
            if campaign.state == CampaignState.RUNNING:
                campaign.state = CampaignState.COMPLETE
                campaign.finished_at = time.monotonic()

        return result

    def _execute_single_run(
        self, run_id: str, campaign_id: str, config: RunConfig
    ) -> RunResult:
        """Run one simulation session and return the result."""
        result = RunResult(
            run_id=run_id,
            campaign_id=campaign_id,
            config=config,
            started_at=time.monotonic(),
        )

        try:
            course_mode = CourseMode(config.course_mode)
            volume = SafeVolume()
            course = CourseGenerator(volume).generate(
                course_mode, seed=config.seed, gate_count=config.gate_count
            )
            world = self._bridge.course_to_world(course, f"train_{run_id}")

            fault_configs = self._curriculum.get_fault_configs(config.fault_profiles)
            sim_config = SimSessionConfig(
                course_id=f"train_{run_id}",
                seed=config.seed,
                max_time_s=config.max_time_s,
                faults=fault_configs,
            )

            self._sim_runner.prepare(sim_config, world)
            self._sim_runner.start()

            sim_time = 0.0
            tick_dt = 0.05
            while self._sim_runner.state.value == "RUNNING":
                sim_time += tick_dt
                self._sim_runner.tick(sim_time)

            sim_result = self._sim_runner.stop()

            result.gates_passed = sim_result.gates_passed
            result.total_gates = sim_result.total_gates
            result.race_time_s = sim_result.race_time_s
            result.complete = sim_result.complete
            result.faults_injected = sim_result.faults_injected

            if sim_result.error:
                result.failures.append(sim_result.error)

        except Exception as exc:
            result.failures.append(str(exc))

        result.finished_at = time.monotonic()
        self._sim_runner.reset()
        return result

    def get_summary(self, campaign_id: str) -> CampaignSummary:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")

        completed = campaign.results
        successes = [r for r in completed if r.success]
        gate_rates = [r.gate_completion_rate for r in completed]
        race_times = [r.race_time_s for r in completed if r.race_time_s > 0]

        failure_breakdown: dict[str, int] = {}
        for r in completed:
            for cat in r.failure_categories:
                failure_breakdown[cat] = failure_breakdown.get(cat, 0) + 1

        return CampaignSummary(
            campaign_id=campaign_id,
            config=campaign.config,
            state=campaign.state,
            total_runs=len(campaign.run_queue),
            completed_runs=len(completed),
            successful_runs=len(successes),
            failed_runs=len(completed) - len(successes),
            avg_gate_completion=sum(gate_rates) / len(gate_rates) if gate_rates else 0.0,
            avg_race_time_s=sum(race_times) / len(race_times) if race_times else 0.0,
            failure_breakdown=failure_breakdown,
            started_at=campaign.started_at,
            finished_at=campaign.finished_at,
        )

    def get_analysis(self, campaign_id: str) -> CampaignAnalysis:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        return analyze_campaign(campaign_id, campaign.results)

    def list_campaigns(self) -> list[dict]:
        return [
            {
                "campaign_id": c.campaign_id,
                "name": c.config.name,
                "state": c.state.value,
                "total_runs": len(c.run_queue),
                "completed_runs": len(c.results),
            }
            for c in self._campaigns.values()
        ]
