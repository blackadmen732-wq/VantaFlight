"""Data quality and provenance attached to every contract message."""
from __future__ import annotations

import enum
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QualityFlag(str, enum.Enum):
    MEASURED = "MEASURED"  # read from a sensor
    ESTIMATED = "ESTIMATED"  # output of an estimator
    PREDICTED = "PREDICTED"  # propagated forward in time
    EXTRAPOLATED = "EXTRAPOLATED"  # beyond the data that supports it
    INTERPOLATED = "INTERPOLATED"
    SIMULATED = "SIMULATED"  # comes from a simulator, not an aircraft
    SYNTHETIC_SENSOR = "SYNTHETIC_SENSOR"  # rendered/generated sensor data
    STALE = "STALE"
    DEGRADED_SOURCE = "DEGRADED_SOURCE"
    OUTLIER_REJECTED = "OUTLIER_REJECTED"
    CLAMPED = "CLAMPED"
    UNCALIBRATED = "UNCALIBRATED"
    CLOCK_UNSYNCED = "CLOCK_UNSYNCED"  # captured_at could not be mapped to host time
    CAPTURE_TIME_IS_RECEIVE_TIME = "CAPTURE_TIME_IS_RECEIVE_TIME"  # source gave no capture time
    LATENCY_UNCOMPENSATED = "LATENCY_UNCOMPENSATED"  # converted time ignores transport latency
    LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"  # migrated from a pre-contract record


class Quality(BaseModel):
    """How far a message can be trusted, and where it came from.

    ``covariance`` is in the units of the payload it describes (e.g. m² for a
    position in metres).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool = True
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    covariance: tuple[tuple[float, ...], ...] | None = None
    stale_after_ms: float | None = Field(default=None, gt=0.0)
    provenance: tuple[str, ...] = ()
    flags: frozenset[QualityFlag] = frozenset()

    @field_validator("covariance")
    @classmethod
    def _covariance(cls, value):
        if value is None:
            return value
        n = len(value)
        if n == 0 or any(len(row) != n for row in value):
            raise ValueError("covariance must be a non-empty square matrix")
        for i in range(n):
            for j in range(n):
                if not math.isfinite(value[i][j]):
                    raise ValueError("covariance must be finite")
                if abs(value[i][j] - value[j][i]) > 1e-9 * max(1.0, abs(value[i][j])):
                    raise ValueError("covariance must be symmetric")
            if value[i][i] < 0.0:
                raise ValueError("covariance diagonal cannot be negative")
        return value

    def with_flags(self, *flags: QualityFlag) -> "Quality":
        return self.model_copy(update={"flags": self.flags | frozenset(flags)})

    def is_stale(self, age_s: float) -> bool:
        if self.stale_after_ms is None:
            return False
        return age_s * 1000.0 > self.stale_after_ms
