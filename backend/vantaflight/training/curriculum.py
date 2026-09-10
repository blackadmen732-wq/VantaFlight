"""Progressive training curriculum — generates run sequences that scale difficulty."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from ..course_lab.models import CourseMode
from ..simulation.models import FaultConfig, FaultType
from .models import CampaignConfig, DifficultyTier, RunConfig


_TIER_GATE_RANGE: dict[DifficultyTier, tuple[int, int]] = {
    DifficultyTier.BEGINNER: (3, 6),
    DifficultyTier.EASY: (5, 10),
    DifficultyTier.MODERATE: (8, 16),
    DifficultyTier.HARD: (12, 24),
    DifficultyTier.EXPERT: (16, 36),
    DifficultyTier.ADVERSARIAL: (20, 50),
}

_TIER_MODES: dict[DifficultyTier, list[CourseMode]] = {
    DifficultyTier.BEGINNER: [CourseMode.SPEED_RUN, CourseMode.SLALOM],
    DifficultyTier.EASY: [CourseMode.RANDOM, CourseMode.SLALOM],
    DifficultyTier.MODERATE: [CourseMode.RANDOM, CourseMode.SLALOM, CourseMode.VERTICAL],
    DifficultyTier.HARD: [CourseMode.TECHNICAL, CourseMode.CHALLENGE, CourseMode.VERTICAL],
    DifficultyTier.EXPERT: [CourseMode.TECHNICAL, CourseMode.CHALLENGE, CourseMode.ADVERSARY],
    DifficultyTier.ADVERSARIAL: [CourseMode.ADVERSARY, CourseMode.CHALLENGE],
}

_TIER_MAX_TIME: dict[DifficultyTier, float] = {
    DifficultyTier.BEGINNER: 120.0,
    DifficultyTier.EASY: 180.0,
    DifficultyTier.MODERATE: 300.0,
    DifficultyTier.HARD: 300.0,
    DifficultyTier.EXPERT: 360.0,
    DifficultyTier.ADVERSARIAL: 420.0,
}


@dataclass
class FaultProfile:
    name: str
    faults: list[FaultConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "faults": [f.to_dict() for f in self.faults],
        }


FAULT_PROFILES: dict[str, FaultProfile] = {
    "clean": FaultProfile(name="clean"),
    "light_noise": FaultProfile(
        name="light_noise",
        faults=[FaultConfig(FaultType.NOISE, probability=0.3, duration_s=2.0, magnitude=15.0, seed=1)],
    ),
    "frame_drops": FaultProfile(
        name="frame_drops",
        faults=[FaultConfig(FaultType.FRAME_DROP, probability=0.1, duration_s=0.5, magnitude=1.0, seed=2)],
    ),
    "blur_and_noise": FaultProfile(
        name="blur_and_noise",
        faults=[
            FaultConfig(FaultType.BLUR, probability=0.2, duration_s=1.5, magnitude=7.0, seed=3),
            FaultConfig(FaultType.NOISE, probability=0.25, duration_s=2.0, magnitude=20.0, seed=4),
        ],
    ),
    "lighting_shifts": FaultProfile(
        name="lighting_shifts",
        faults=[FaultConfig(FaultType.LIGHTING_CHANGE, probability=0.15, duration_s=3.0, magnitude=1.6, seed=5)],
    ),
    "camera_delay": FaultProfile(
        name="camera_delay",
        faults=[FaultConfig(FaultType.CAMERA_DELAY, probability=0.2, duration_s=2.0, magnitude=80.0, seed=6)],
    ),
    "occlusion": FaultProfile(
        name="occlusion",
        faults=[FaultConfig(FaultType.OCCLUSION, probability=0.1, duration_s=1.0, magnitude=0.25, seed=7)],
    ),
    "adversarial_full": FaultProfile(
        name="adversarial_full",
        faults=[
            FaultConfig(FaultType.NOISE, probability=0.3, duration_s=2.0, magnitude=25.0, seed=10),
            FaultConfig(FaultType.BLUR, probability=0.15, duration_s=1.5, magnitude=9.0, seed=11),
            FaultConfig(FaultType.FRAME_DROP, probability=0.08, duration_s=0.3, magnitude=1.0, seed=12),
            FaultConfig(FaultType.LIGHTING_CHANGE, probability=0.1, duration_s=2.5, magnitude=1.8, seed=13),
            FaultConfig(FaultType.CAMERA_DELAY, probability=0.1, duration_s=1.5, magnitude=60.0, seed=14),
            FaultConfig(FaultType.OCCLUSION, probability=0.05, duration_s=0.8, magnitude=0.2, seed=15),
        ],
    ),
}

_TIER_FAULT_PROFILES: dict[DifficultyTier, list[str]] = {
    DifficultyTier.BEGINNER: ["clean"],
    DifficultyTier.EASY: ["clean", "light_noise"],
    DifficultyTier.MODERATE: ["clean", "light_noise", "frame_drops"],
    DifficultyTier.HARD: ["blur_and_noise", "lighting_shifts", "camera_delay"],
    DifficultyTier.EXPERT: ["blur_and_noise", "camera_delay", "occlusion"],
    DifficultyTier.ADVERSARIAL: ["adversarial_full"],
}


class CurriculumBuilder:
    """Builds a sequence of RunConfigs from a CampaignConfig, expanding the
    cross-product of modes x seeds x gate counts x difficulty x faults."""

    def expand(self, config: CampaignConfig) -> list[RunConfig]:
        runs: list[RunConfig] = []

        seeds = list(range(config.seed_range[0], config.seed_range[1]))
        if not seeds:
            seeds = [0]

        fault_sets = config.fault_profiles if config.fault_profiles else [[]]

        for mode, seed, gc, tier_str, faults in product(
            config.course_modes, seeds, config.gate_counts,
            config.difficulty_tiers, fault_sets,
        ):
            tier = DifficultyTier(tier_str) if tier_str in DifficultyTier.__members__ else DifficultyTier.MODERATE
            max_time = _TIER_MAX_TIME.get(tier, config.max_time_per_run_s)

            runs.append(RunConfig(
                course_mode=mode,
                seed=seed,
                gate_count=gc,
                fault_profiles=list(faults),
                max_time_s=min(max_time, config.max_time_per_run_s),
                difficulty_tier=tier_str,
            ))

            if 0 < config.max_runs <= len(runs):
                return runs

        return runs

    def auto_curriculum(
        self,
        tiers: list[DifficultyTier] | None = None,
        seeds_per_tier: int = 3,
        gate_counts_per_tier: int = 2,
    ) -> CampaignConfig:
        """Generate a campaign config that progressively scales difficulty."""
        tiers = tiers or list(DifficultyTier)

        all_modes: list[str] = []
        all_tiers: list[str] = []
        all_gate_counts: list[int] = []
        all_fault_profiles: list[list[str]] = []

        for tier in tiers:
            modes = _TIER_MODES.get(tier, [CourseMode.RANDOM])
            for mode in modes:
                if mode.value not in all_modes:
                    all_modes.append(mode.value)
            if tier.value not in all_tiers:
                all_tiers.append(tier.value)

            gate_lo, gate_hi = _TIER_GATE_RANGE.get(tier, (8, 16))
            step = max(1, (gate_hi - gate_lo) // max(1, gate_counts_per_tier - 1))
            for gc in range(gate_lo, gate_hi + 1, step):
                if gc not in all_gate_counts:
                    all_gate_counts.append(gc)

            for fp_name in _TIER_FAULT_PROFILES.get(tier, ["clean"]):
                profile_list = [fp_name]
                if profile_list not in all_fault_profiles:
                    all_fault_profiles.append(profile_list)

        return CampaignConfig(
            name="Auto Curriculum",
            description=f"Progressive curriculum across {len(tiers)} difficulty tiers",
            course_modes=all_modes,
            seed_range=(0, seeds_per_tier),
            gate_counts=sorted(set(all_gate_counts)),
            difficulty_tiers=all_tiers,
            fault_profiles=all_fault_profiles,
        )

    def get_fault_configs(self, profile_names: list[str]) -> list[FaultConfig]:
        configs: list[FaultConfig] = []
        for name in profile_names:
            profile = FAULT_PROFILES.get(name)
            if profile:
                configs.extend(profile.faults)
        return configs
