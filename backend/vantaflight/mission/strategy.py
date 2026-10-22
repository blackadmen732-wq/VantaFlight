"""Competition mission strategy and small-route optimization.

This layer decides *which objective to do next*. Motion planning remains in
VantaMotion and safety remains in VantaExecution.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class ObjectiveCandidate:
    objective_id: str
    position: tuple[float, float, float]
    reward: float = 1.0
    success_probability: float = 1.0
    service_time_s: float = 0.0
    energy_cost: float = 0.0
    uncertainty: float = 0.0

    def __post_init__(self) -> None:
        values = (*self.position, self.reward, self.success_probability, self.service_time_s, self.energy_cost, self.uncertainty)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("objective values must be finite")
        if not 0.0 <= self.success_probability <= 1.0:
            raise ValueError("success probability must be between 0 and 1")
        if self.service_time_s < 0 or self.energy_cost < 0 or self.uncertainty < 0:
            raise ValueError("objective costs cannot be negative")


@dataclass(frozen=True)
class StrategyDecision:
    ordered_objective_ids: tuple[str, ...]
    expected_reward: float
    expected_time_s: float
    expected_energy: float
    utility: float


class MissionStrategyEngine:
    """Deterministic objective selector and exact small-N route optimizer."""

    def __init__(
        self,
        *,
        cruise_speed_mps: float = 1.5,
        time_weight: float = 0.04,
        energy_weight: float = 0.5,
        uncertainty_weight: float = 0.3,
    ) -> None:
        if cruise_speed_mps <= 0:
            raise ValueError("cruise speed must be positive")
        self.cruise_speed_mps = cruise_speed_mps
        self.time_weight = time_weight
        self.energy_weight = energy_weight
        self.uncertainty_weight = uncertainty_weight

    def rank_next(
        self,
        current_position: Sequence[float],
        objectives: Iterable[ObjectiveCandidate],
    ) -> list[tuple[ObjectiveCandidate, float]]:
        current = np.asarray(current_position, dtype=float)
        if current.shape != (3,) or not np.all(np.isfinite(current)):
            raise ValueError("current position must be finite XYZ")
        scored: list[tuple[ObjectiveCandidate, float]] = []
        for objective in objectives:
            distance = float(np.linalg.norm(np.asarray(objective.position) - current))
            travel_time = distance / self.cruise_speed_mps
            expected_reward = objective.reward * objective.success_probability
            utility = (
                expected_reward
                - self.time_weight * (travel_time + objective.service_time_s)
                - self.energy_weight * objective.energy_cost
                - self.uncertainty_weight * objective.uncertainty
            )
            scored.append((objective, utility))
        return sorted(scored, key=lambda item: (-item[1], item[0].objective_id))

    def optimize_route(
        self,
        start: Sequence[float],
        objectives: Sequence[ObjectiveCandidate],
        *,
        return_to: Sequence[float] | None = None,
        max_objectives: int | None = None,
        max_time_s: float | None = None,
        energy_budget: float | None = None,
    ) -> StrategyDecision:
        """Find the highest-utility route exactly for small competition fields.

        For <= 10 candidates this enumerates subsets/permutations. Competition
        tasks typically have a small number of pads/targets, making a
        deterministic exact solver preferable to opaque RL. Larger sets fall
        back to a greedy utility ranking to prevent factorial explosion.
        """
        if max_objectives is not None and max_objectives < 0:
            raise ValueError("max_objectives cannot be negative")
        candidates = list(objectives)
        if not candidates:
            return StrategyDecision((), 0.0, 0.0, 0.0, 0.0)
        limit = len(candidates) if max_objectives is None else min(max_objectives, len(candidates))
        start_v = np.asarray(start, dtype=float)
        return_v = np.asarray(return_to, dtype=float) if return_to is not None else None

        if len(candidates) > 10:
            ranked = [o for o, _ in self.rank_next(start, candidates)][:limit]
            return self._evaluate_order(start_v, ranked, return_v, max_time_s, energy_budget)

        best = StrategyDecision((), 0.0, 0.0, 0.0, -math.inf)
        for count in range(1, limit + 1):
            for order in itertools.permutations(candidates, count):
                decision = self._evaluate_order(start_v, list(order), return_v, max_time_s, energy_budget)
                if decision.utility > best.utility:
                    best = decision
        if best.utility == -math.inf:
            return StrategyDecision((), 0.0, 0.0, 0.0, 0.0)
        return best

    def _evaluate_order(
        self,
        start: np.ndarray,
        order: list[ObjectiveCandidate],
        return_to: np.ndarray | None,
        max_time_s: float | None,
        energy_budget: float | None,
    ) -> StrategyDecision:
        position = start
        total_time = 0.0
        energy = 0.0
        reward = 0.0
        uncertainty_cost = 0.0
        ids: list[str] = []
        for objective in order:
            target = np.asarray(objective.position, dtype=float)
            travel_time = float(np.linalg.norm(target - position)) / self.cruise_speed_mps
            total_time += travel_time + objective.service_time_s
            energy += objective.energy_cost
            reward += objective.reward * objective.success_probability
            uncertainty_cost += objective.uncertainty
            ids.append(objective.objective_id)
            position = target
        if return_to is not None:
            total_time += float(np.linalg.norm(return_to - position)) / self.cruise_speed_mps
        if max_time_s is not None and total_time > max_time_s:
            return StrategyDecision(tuple(ids), reward, total_time, energy, -math.inf)
        if energy_budget is not None and energy > energy_budget:
            return StrategyDecision(tuple(ids), reward, total_time, energy, -math.inf)
        utility = reward - self.time_weight * total_time - self.energy_weight * energy - self.uncertainty_weight * uncertainty_cost
        return StrategyDecision(tuple(ids), reward, total_time, energy, utility)
