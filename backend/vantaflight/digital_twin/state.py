"""Digital twin state — operator-side representation of the aircraft."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import Telemetry
from .trajectory import TrajectoryBuffer


@dataclass
class DigitalTwinState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    altitude: float = 0.0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    heading: float = 0.0
    velocity: float = 0.0
    ground_speed: float = 0.0
    armed: bool = False
    connected: bool = False
    flight_mode: str = "IDLE"
    battery_percentage: float = 100.0
    flight_duration: float = 0.0
    trajectory: TrajectoryBuffer = field(default_factory=TrajectoryBuffer)

    _flight_start: Optional[float] = field(default=None, repr=False)
    _accumulated_duration: float = field(default=0.0, repr=False)
    _last_update: float = field(default_factory=time.time, repr=False)

    def update(self, telemetry: Telemetry) -> None:
        now = time.time()
        self.x = telemetry.x
        self.y = telemetry.y
        self.z = telemetry.z
        self.altitude = telemetry.altitude
        self.latitude = telemetry.latitude
        self.longitude = telemetry.longitude
        self.heading = telemetry.heading
        self.velocity = telemetry.velocity
        self.ground_speed = telemetry.ground_speed
        self.armed = telemetry.armed
        self.connected = telemetry.connected
        self.flight_mode = telemetry.flight_mode.value
        self.battery_percentage = telemetry.battery_percentage

        if telemetry.armed and self._flight_start is None:
            self._flight_start = now
        elif not telemetry.armed and self._flight_start is not None:
            self._accumulated_duration += now - self._flight_start
            self._flight_start = None

        if self._flight_start is not None:
            self.flight_duration = self._accumulated_duration + (now - self._flight_start)
        else:
            self.flight_duration = self._accumulated_duration

        self.trajectory.add(now, self.x, self.y, self.z, self.heading)
        self._last_update = now

    def reset(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.altitude = 0.0
        self.latitude = None
        self.longitude = None
        self.heading = 0.0
        self.velocity = 0.0
        self.ground_speed = 0.0
        self.armed = False
        self.connected = False
        self.flight_mode = "IDLE"
        self.battery_percentage = 100.0
        self.flight_duration = 0.0
        self._flight_start = None
        self._accumulated_duration = 0.0
        self.trajectory.clear()

    def to_dict(self) -> dict:
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
            "altitude": round(self.altitude, 3),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "heading": round(self.heading, 1),
            "velocity": round(self.velocity, 3),
            "ground_speed": round(self.ground_speed, 3),
            "armed": self.armed,
            "connected": self.connected,
            "flight_mode": self.flight_mode,
            "battery_percentage": round(self.battery_percentage, 2),
            "flight_duration": round(self.flight_duration, 1),
            "trajectory": [
                {"ts": p.timestamp, "x": p.x, "y": p.y, "z": p.z, "heading": p.heading}
                for p in self.trajectory.points[-100:]
            ],
        }
