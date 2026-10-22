"""Mission-level battery/energy prediction.

This does not replace vehicle battery failsafes. It predicts whether a mission
objective can be started while preserving an explicit landing reserve.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class EnergyEstimate:
    objective_cost_pct: float
    return_cost_pct: float
    reserve_pct: float
    available_pct: float

    @property
    def required_pct(self) -> float:
        return self.objective_cost_pct + self.return_cost_pct + self.reserve_pct

    @property
    def safe_to_start(self) -> bool:
        return self.available_pct >= self.required_pct


class EnergyModel:
    """Online deterministic estimate of battery percentage consumed per task."""

    def __init__(
        self,
        *,
        base_pct_per_s: float = 0.08,
        climb_extra_pct_per_m: float = 0.15,
        payload_extra_per_g_s: float = 0.0008,
        reserve_pct: float = 15.0,
        smoothing: float = 0.2,
    ) -> None:
        values = (base_pct_per_s, climb_extra_pct_per_m, payload_extra_per_g_s, reserve_pct, smoothing)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("energy model values must be finite")
        if base_pct_per_s < 0 or climb_extra_pct_per_m < 0 or payload_extra_per_g_s < 0 or reserve_pct < 0:
            raise ValueError("energy costs cannot be negative")
        if not 0 < smoothing <= 1:
            raise ValueError("smoothing must be between 0 and 1")
        self.base_pct_per_s = base_pct_per_s
        self.climb_extra_pct_per_m = climb_extra_pct_per_m
        self.payload_extra_per_g_s = payload_extra_per_g_s
        self.reserve_pct = reserve_pct
        self.smoothing = smoothing

    def predict_cost(
        self,
        *,
        duration_s: float,
        climb_m: float = 0.0,
        payload_mass_g: float = 0.0,
        speed_factor: float = 1.0,
    ) -> float:
        if duration_s < 0 or payload_mass_g < 0 or speed_factor <= 0:
            raise ValueError("invalid energy prediction inputs")
        cruise = self.base_pct_per_s * duration_s * speed_factor
        climb = self.climb_extra_pct_per_m * max(0.0, climb_m)
        payload = self.payload_extra_per_g_s * payload_mass_g * duration_s
        return cruise + climb + payload

    def can_start(
        self,
        *,
        available_pct: float,
        objective_duration_s: float,
        return_duration_s: float,
        climb_m: float = 0.0,
        payload_mass_g: float = 0.0,
        speed_factor: float = 1.0,
    ) -> EnergyEstimate:
        if not 0.0 <= available_pct <= 100.0:
            raise ValueError("available battery must be a percentage")
        objective = self.predict_cost(
            duration_s=objective_duration_s,
            climb_m=climb_m,
            payload_mass_g=payload_mass_g,
            speed_factor=speed_factor,
        )
        returning = self.predict_cost(
            duration_s=return_duration_s,
            payload_mass_g=payload_mass_g,
            speed_factor=1.0,
        )
        return EnergyEstimate(objective, returning, self.reserve_pct, available_pct)

    def learn_from_flight(self, *, duration_s: float, consumed_pct: float) -> float:
        """Update base consumption from a measured clean-flight observation."""
        if duration_s <= 0 or consumed_pct < 0:
            raise ValueError("invalid measured energy observation")
        measured = consumed_pct / duration_s
        self.base_pct_per_s = (
            (1.0 - self.smoothing) * self.base_pct_per_s
            + self.smoothing * measured
        )
        return self.base_pct_per_s
