from __future__ import annotations

from dataclasses import replace
import numpy as np

from .profiles import EnvironmentProfile, WindField


class DomainRandomizer:
    """Bounded deterministic sim-to-real environment randomization."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self._rng = np.random.default_rng(self.seed)

    def sample(
        self,
        base: EnvironmentProfile,
        *,
        intensity: float = 0.25,
    ) -> EnvironmentProfile:
        if not 0.0 <= intensity <= 1.0:
            raise ValueError("intensity must be between 0 and 1")

        def jitter(value: float, fraction: float) -> float:
            return float(value * (1.0 + self._rng.uniform(-fraction, fraction) * intensity))

        steady = tuple(
            float(v + self._rng.normal(0.0, 0.45 * intensity))
            for v in base.wind.steady_mps
        )
        wind = WindField(
            steady_mps=steady,
            gust_mps=max(0.0, jitter(max(base.wind.gust_mps, 0.5), 0.8)),
            turbulence=max(0.0, jitter(max(base.wind.turbulence, 0.15), 0.8)),
        )

        return replace(
            base,
            name=f"{base.name}_RND_{self.seed}",
            wind=wind,
            temperature_c=base.temperature_c + float(self._rng.normal(0.0, 4.0 * intensity)),
            visibility=float(np.clip(base.visibility + self._rng.normal(0.0, 0.15 * intensity), 0.05, 1.0)),
            light_level=float(np.clip(base.light_level + self._rng.normal(0.0, 0.18 * intensity), 0.05, 1.0)),
            glare=float(np.clip(base.glare + self._rng.normal(0.0, 0.18 * intensity), 0.0, 1.0)),
            sensor_noise_scale=max(0.0, jitter(base.sensor_noise_scale, 0.4)),
            command_latency_ms=max(0.0, base.command_latency_ms + float(self._rng.uniform(0.0, 80.0 * intensity))),
            camera_latency_ms=max(0.0, base.camera_latency_ms + float(self._rng.uniform(0.0, 120.0 * intensity))),
            payload_mass_g=max(0.0, base.payload_mass_g + float(self._rng.uniform(-2.0, 4.0) * intensity)),
            battery_scale=float(np.clip(base.battery_scale + self._rng.normal(0.0, 0.08 * intensity), 0.2, 1.2)),
        )
