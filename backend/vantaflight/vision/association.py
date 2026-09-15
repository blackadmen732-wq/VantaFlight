"""Candidate-to-track association using identity, geometry, and uncertainty."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .concepts import TargetCandidate


@dataclass(frozen=True)
class AssociationResult:
    candidate: TargetCandidate | None
    cost: float
    mahalanobis_distance: float
    reason: str


class CandidateAssociator:
    def __init__(
        self,
        mahalanobis_gate: float = 9.21,
        min_area_ratio: float = 0.25,
        max_area_ratio: float = 4.0,
    ) -> None:
        self.mahalanobis_gate = mahalanobis_gate
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio

    def associate(
        self,
        candidates: list[TargetCandidate],
        predicted_position: np.ndarray,
        innovation_covariance: np.ndarray,
        *,
        profile_name: str | None = None,
        previous_area_px: float | None = None,
    ) -> AssociationResult:
        predicted = np.asarray(predicted_position, np.float64).reshape(2)
        covariance = np.asarray(innovation_covariance, np.float64).reshape(2, 2)
        try:
            inverse = np.linalg.inv(covariance)
        except np.linalg.LinAlgError:
            inverse = np.linalg.pinv(covariance)
        best: tuple[float, float, TargetCandidate] | None = None
        rejected_profile = rejected_geometry = rejected_gate = False
        for candidate in candidates:
            if profile_name is not None and candidate.profile.name != profile_name:
                rejected_profile = True
                continue
            geometry_penalty = 0.0
            if previous_area_px is not None and previous_area_px > 0:
                ratio = candidate.area_px / previous_area_px
                if not self.min_area_ratio <= ratio <= self.max_area_ratio:
                    rejected_geometry = True
                    continue
                geometry_penalty = abs(float(np.log(ratio)))
            delta = np.asarray(candidate.centroid) - predicted
            mahalanobis = float(delta @ inverse @ delta)
            if mahalanobis > self.mahalanobis_gate:
                rejected_gate = True
                continue
            cost = mahalanobis + geometry_penalty + (1.0 - candidate.score)
            if best is None or cost < best[0]:
                best = (cost, mahalanobis, candidate)
        if best is not None:
            return AssociationResult(best[2], best[0], best[1], "matched")
        reason = (
            "profile mismatch" if rejected_profile and not (rejected_geometry or rejected_gate)
            else "geometry mismatch" if rejected_geometry and not rejected_gate
            else "mahalanobis gate" if rejected_gate
            else "no candidates"
        )
        return AssociationResult(None, float("inf"), float("inf"), reason)
