"""Timestamp normalization for Hopper data.

Camera frames and telemetry may arrive on separate paths.  This module
converts each measurement to a common monotonic reference so the rest of
VantaFlight can safely compare them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class TimedMeasurement:
    source_ts: Optional[float]   # timestamp from Hopper, if exposed
    receive_ts: float            # host wall-clock at receive
    monotonic_ts: float          # host monotonic at receive

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.monotonic_ts


class HopperTimeSync:
    """Attaches monotonic timestamps to incoming Hopper data.

    Hopper's external API does not currently document a timestamp field, so
    ``source_ts`` will be ``None`` until FTW exposes one.  The host receive
    times are always populated and are used throughout VantaFlight.
    """

    def stamp(self, source_ts: Optional[float] = None) -> TimedMeasurement:
        return TimedMeasurement(
            source_ts=source_ts,
            receive_ts=time.time(),
            monotonic_ts=time.monotonic(),
        )

    def predict_position(
        self,
        position: tuple[float, float, float],
        velocity: tuple[float, float, float],
        measurement_age_s: float,
    ) -> tuple[float, float, float]:
        """Dead-reckon a stale position forward by measurement_age_s."""
        x, y, z = position
        vx, vy, vz = velocity
        return (
            x + vx * measurement_age_s,
            y + vy * measurement_age_s,
            z + vz * measurement_age_s,
        )
