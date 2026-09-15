"""ReplayPlayer — timeline scrubbing and playback control."""
from __future__ import annotations

import time

from .models import ReplayFrame, ReplayState, ReplayTimeline


class ReplayPlayer:
    """Controls playback of a loaded ReplayTimeline with seek and speed control."""

    def __init__(self) -> None:
        self._timeline: ReplayTimeline | None = None
        self._state = ReplayState.IDLE
        self._playback_speed: float = 1.0
        self._cursor: float = 0.0
        self._last_wall_time: float = 0.0
        self._frame_index: int = 0

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def cursor(self) -> float:
        return self._cursor

    @property
    def duration(self) -> float:
        return self._timeline.duration_s if self._timeline else 0.0

    @property
    def progress(self) -> float:
        if not self._timeline or self._timeline.duration_s <= 0:
            return 0.0
        return min(1.0, self._cursor / self._timeline.duration_s)

    @property
    def playback_speed(self) -> float:
        return self._playback_speed

    def load(self, timeline: ReplayTimeline) -> None:
        self._timeline = timeline
        self._state = ReplayState.READY
        self._cursor = 0.0
        self._frame_index = 0

    def play(self) -> None:
        if self._state not in (ReplayState.READY, ReplayState.PAUSED):
            return
        self._state = ReplayState.PLAYING
        self._last_wall_time = time.monotonic()

    def pause(self) -> None:
        if self._state != ReplayState.PLAYING:
            return
        self._advance_cursor()
        self._state = ReplayState.PAUSED

    def stop(self) -> None:
        self._state = ReplayState.READY
        self._cursor = 0.0
        self._frame_index = 0

    def seek(self, time_offset: float) -> None:
        if self._timeline is None:
            return
        self._cursor = max(0.0, min(time_offset, self._timeline.duration_s))
        self._frame_index = self._find_frame_index(self._cursor)
        if self._state == ReplayState.PLAYING:
            self._last_wall_time = time.monotonic()

    def set_speed(self, speed: float) -> None:
        if self._state == ReplayState.PLAYING:
            self._advance_cursor()
        self._playback_speed = max(0.1, min(speed, 16.0))
        if self._state == ReplayState.PLAYING:
            self._last_wall_time = time.monotonic()

    def tick(self) -> list[ReplayFrame]:
        """Advance playback and return new frames since last tick."""
        if self._timeline is None or self._state != ReplayState.PLAYING:
            return []

        prev_cursor = self._cursor
        self._advance_cursor()

        if self._cursor >= self._timeline.duration_s:
            self._cursor = self._timeline.duration_s
            self._state = ReplayState.COMPLETE
            return self._timeline.frames_between(prev_cursor, self._cursor)

        return self._timeline.frames_between(prev_cursor, self._cursor)

    def current_frame(self) -> ReplayFrame | None:
        if self._timeline is None:
            return None
        return self._timeline.frame_at(self._cursor)

    def _advance_cursor(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_wall_time
        self._cursor += elapsed * self._playback_speed
        self._last_wall_time = now

    def _find_frame_index(self, time_offset: float) -> int:
        if self._timeline is None:
            return 0
        target = self._timeline.start_time + time_offset
        for i, frame in enumerate(self._timeline.frames):
            if frame.timestamp > target:
                return max(0, i - 1)
        return max(0, len(self._timeline.frames) - 1)

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "flight_id": self._timeline.flight_id if self._timeline else None,
            "cursor_s": round(self._cursor, 3),
            "duration_s": round(self.duration, 3),
            "progress": round(self.progress, 4),
            "playback_speed": self._playback_speed,
            "total_frames": self._timeline.total_frames if self._timeline else 0,
        }
