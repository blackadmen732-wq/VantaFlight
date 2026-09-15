"""Replay data models."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ReplayState(str, enum.Enum):
    IDLE = "IDLE"
    LOADING = "LOADING"
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"


@dataclass
class ReplayFrame:
    timestamp: float
    frame_type: str
    data: dict

    def to_dict(self) -> dict:
        return {
            "timestamp": round(self.timestamp, 6),
            "frame_type": self.frame_type,
            "data": self.data,
        }


@dataclass
class FlightSummary:
    flight_id: int
    started_at: float
    ended_at: float | None
    drone_id: str
    drone_name: str
    status: str
    duration_s: float
    sample_count: int
    event_count: int
    command_count: int

    def to_dict(self) -> dict:
        return {
            "flight_id": self.flight_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "drone_id": self.drone_id,
            "drone_name": self.drone_name,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "sample_count": self.sample_count,
            "event_count": self.event_count,
            "command_count": self.command_count,
        }


@dataclass
class ReplayTimeline:
    flight_id: int
    start_time: float
    end_time: float
    duration_s: float
    total_frames: int
    frames: list[ReplayFrame] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "flight_id": self.flight_id,
            "start_time": round(self.start_time, 6),
            "end_time": round(self.end_time, 6),
            "duration_s": round(self.duration_s, 3),
            "total_frames": self.total_frames,
        }

    def frame_at(self, time_offset: float) -> ReplayFrame | None:
        target = self.start_time + time_offset
        best: ReplayFrame | None = None
        for f in self.frames:
            if f.timestamp <= target:
                best = f
            else:
                break
        return best

    def frames_between(self, t0: float, t1: float) -> list[ReplayFrame]:
        start = self.start_time + t0
        end = self.start_time + t1
        return [f for f in self.frames if start <= f.timestamp <= end]
