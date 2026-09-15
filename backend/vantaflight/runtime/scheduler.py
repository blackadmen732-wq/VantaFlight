"""FrameScheduler — latest-frame processing with stale-frame discard."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class SchedulerMetrics:
    frames_captured: int = 0
    frames_processed: int = 0
    frames_dropped_stale: int = 0
    frames_dropped_overflow: int = 0
    capture_fps: float = 0.0
    processing_fps: float = 0.0

    def to_dict(self) -> dict:
        return {
            "frames_captured": self.frames_captured,
            "frames_processed": self.frames_processed,
            "frames_dropped_stale": self.frames_dropped_stale,
            "frames_dropped_overflow": self.frames_dropped_overflow,
            "capture_fps": round(self.capture_fps, 1),
            "processing_fps": round(self.processing_fps, 1),
        }


class FrameScheduler:
    """Drop-when-behind scheduler for latest-frame processing.

    The camera can produce frames faster than vision processes them.
    This scheduler keeps only the latest frame, discards stale ones,
    and tracks capture/processing rates.
    """

    def __init__(self, max_frame_age_s: float = 0.15) -> None:
        self._max_age = max_frame_age_s
        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_timestamp: float = 0.0
        self._metrics = SchedulerMetrics()
        self._capture_start: float = 0.0
        self._process_start: float = 0.0

    @property
    def metrics(self) -> SchedulerMetrics:
        return self._metrics

    def submit_frame(self, frame, capture_time: float) -> None:
        """Submit a new camera frame. Previous unprocessed frame is discarded."""
        with self._lock:
            if self._latest_frame is not None:
                self._metrics.frames_dropped_stale += 1
            self._latest_frame = frame
            self._latest_timestamp = capture_time
            self._metrics.frames_captured += 1
            if self._metrics.frames_captured == 1:
                self._capture_start = time.monotonic()
            elapsed = time.monotonic() - self._capture_start
            if elapsed > 0:
                self._metrics.capture_fps = self._metrics.frames_captured / elapsed

    def take_frame(self):
        """Take the latest frame for processing. Returns None if stale or empty."""
        now = time.monotonic()
        with self._lock:
            if self._latest_frame is None:
                return None
            age = now - self._latest_timestamp
            if age > self._max_age:
                self._metrics.frames_dropped_stale += 1
                self._latest_frame = None
                return None
            frame = self._latest_frame
            self._latest_frame = None
            self._metrics.frames_processed += 1
            if self._metrics.frames_processed == 1:
                self._process_start = now
            elapsed = now - self._process_start
            if elapsed > 0:
                self._metrics.processing_fps = self._metrics.frames_processed / elapsed
            return frame

    def reset(self) -> None:
        with self._lock:
            self._latest_frame = None
            self._latest_timestamp = 0.0
            self._metrics = SchedulerMetrics()
