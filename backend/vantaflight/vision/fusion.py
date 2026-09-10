"""Evidence-weighted confidence fusion without pretending correlated cues agree."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class VisionEvidence:
    source: str
    confidence: float
    timestamp: float
    reliability: float = 1.0
    independent_group: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1 or not 0 <= self.reliability <= 1:
            raise ValueError("confidence and reliability must be in [0, 1]")


@dataclass(frozen=True)
class FusionResult:
    confidence: float
    timestamp: float
    used_sources: tuple[str, ...]
    contributions: dict[str, float] = field(default_factory=dict)


class VantaFusion:
    """Fuse fresh evidence as weighted log-odds, once per correlation group."""

    def __init__(self, max_age_s: float = 0.25, prior: float = 0.1) -> None:
        if max_age_s < 0 or not 0 < prior < 1:
            raise ValueError("invalid fusion configuration")
        self.max_age_s = max_age_s
        self.prior = prior

    def fuse(self, evidence: list[VisionEvidence], timestamp: float) -> FusionResult:
        fresh = [item for item in evidence if 0 <= timestamp - item.timestamp <= self.max_age_s]
        # Keep strongest member of each declared correlated group.
        groups: dict[str, VisionEvidence] = {}
        for item in fresh:
            key = item.independent_group or f"source:{item.source}"
            current = groups.get(key)
            strength = abs(item.confidence - 0.5) * item.reliability
            if current is None or strength > abs(current.confidence - 0.5) * current.reliability:
                groups[key] = item
        prior_log_odds = float(np.log(self.prior / (1 - self.prior)))
        total = prior_log_odds
        contributions: dict[str, float] = {}
        for item in groups.values():
            probability = float(np.clip(item.confidence, 1e-6, 1 - 1e-6))
            contribution = item.reliability * float(np.log(probability / (1 - probability)))
            contributions[item.source] = contribution
            total += contribution
        confidence = float(1 / (1 + np.exp(-np.clip(total, -60, 60))))
        return FusionResult(confidence, timestamp, tuple(sorted(contributions)), contributions)
