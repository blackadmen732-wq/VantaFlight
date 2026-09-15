"""Training engine — campaigns, curriculum, and analysis."""
from .analysis import CampaignAnalysis, analyze_campaign, classify_failure
from .curriculum import CurriculumBuilder, FAULT_PROFILES, FaultProfile
from .engine import TrainingEngine
from .models import (
    CampaignConfig,
    CampaignState,
    CampaignSummary,
    DifficultyTier,
    FailureCategory,
    RunConfig,
    RunResult,
)

__all__ = [
    "CampaignAnalysis",
    "CampaignConfig",
    "CampaignState",
    "CampaignSummary",
    "CurriculumBuilder",
    "DifficultyTier",
    "FAULT_PROFILES",
    "FailureCategory",
    "FaultProfile",
    "RunConfig",
    "RunResult",
    "TrainingEngine",
    "analyze_campaign",
    "classify_failure",
]
