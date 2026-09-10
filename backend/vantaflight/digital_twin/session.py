"""Twin session — ties the digital twin to a flight session lifecycle."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import Telemetry
from .state import DigitalTwinState


@dataclass
class RunSummary:
    duration: float = 0.0
    max_altitude: float = 0.0
    max_speed: float = 0.0
    battery_start: float = 100.0
    battery_end: float = 100.0
    command_count: int = 0
    connection_interruptions: int = 0
    final_status: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 1),
            "max_altitude": round(self.max_altitude, 2),
            "max_speed": round(self.max_speed, 2),
            "battery_start": round(self.battery_start, 1),
            "battery_end": round(self.battery_end, 1),
            "command_count": self.command_count,
            "connection_interruptions": self.connection_interruptions,
            "final_status": self.final_status,
        }


class TwinSession:
    """Tracks a single flight session's twin state and run summary."""

    def __init__(self) -> None:
        self.state = DigitalTwinState()
        self._summary = RunSummary()
        self._started_at: Optional[float] = None
        self._active = False

    def start(self, battery: float = 100.0) -> None:
        self.state.reset()
        self._summary = RunSummary(battery_start=battery)
        self._started_at = time.time()
        self._active = True

    def update(self, telemetry: Telemetry) -> None:
        if not self._active:
            return
        self.state.update(telemetry)
        self._summary.max_altitude = max(self._summary.max_altitude, telemetry.altitude)
        self._summary.max_speed = max(self._summary.max_speed, abs(telemetry.velocity))
        self._summary.battery_end = telemetry.battery_percentage

    def record_command(self) -> None:
        self._summary.command_count += 1

    def record_interruption(self) -> None:
        self._summary.connection_interruptions += 1

    def end(self, status: str = "completed") -> RunSummary:
        self._active = False
        if self._started_at is not None:
            self._summary.duration = time.time() - self._started_at
        self._summary.final_status = status
        return self._summary

    @property
    def active(self) -> bool:
        return self._active

    @property
    def summary(self) -> RunSummary:
        if self._active and self._started_at is not None:
            self._summary.duration = time.time() - self._started_at
        return self._summary
