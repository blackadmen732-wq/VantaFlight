"""Camera source interfaces, implementations, and bounded frame transport."""
from __future__ import annotations

import abc
import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Protocol, runtime_checkable

import cv2
import numpy as np

from .concepts import FramePacket


@runtime_checkable
class CameraSource(Protocol):
    """Future camera integrations implement this asynchronous contract."""

    source_id: str

    async def start(self) -> None: ...
    async def read(self) -> FramePacket | None: ...
    async def stop(self) -> None: ...


class BaseCameraSource(abc.ABC):
    """Convenience base for hardware/network sources added after V0.5."""

    source_id: str

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def read(self) -> FramePacket | None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    async def frames(self) -> AsyncIterator[FramePacket]:
        await self.start()
        try:
            while (frame := await self.read()) is not None:
                yield frame
        finally:
            await self.stop()


@dataclass(frozen=True)
class FrameBufferMetrics:
    received: int
    delivered: int
    dropped_capacity: int
    dropped_stale: int
    depth: int
    capacity: int


class FrameBuffer:
    """Thread-safe bounded queue optimized for delivering the newest frame."""

    def __init__(self, capacity: int = 2) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._frames: deque[FramePacket] = deque()
        self._lock = threading.Lock()
        self._received = self._delivered = 0
        self._dropped_capacity = self._dropped_stale = 0

    def put(self, frame: FramePacket) -> None:
        with self._lock:
            self._received += 1
            if len(self._frames) == self.capacity:
                self._frames.popleft()
                self._dropped_capacity += 1
            self._frames.append(frame)

    def latest(self, max_age_s: float | None = None, now: float | None = None) -> FramePacket | None:
        """Return newest frame, discarding older queued and over-age frames."""
        with self._lock:
            if not self._frames:
                return None
            newest = self._frames.pop()
            self._dropped_stale += len(self._frames)
            self._frames.clear()
            if max_age_s is not None:
                if max_age_s < 0:
                    raise ValueError("max_age_s must be non-negative")
                age = (time.monotonic() if now is None else now) - newest.timestamp
                if age > max_age_s:
                    self._dropped_stale += 1
                    return None
            self._delivered += 1
            return newest

    def clear(self) -> None:
        with self._lock:
            self._dropped_stale += len(self._frames)
            self._frames.clear()

    @property
    def metrics(self) -> FrameBufferMetrics:
        with self._lock:
            return FrameBufferMetrics(
                self._received,
                self._delivered,
                self._dropped_capacity,
                self._dropped_stale,
                len(self._frames),
                self.capacity,
            )


class SyntheticCameraSource(BaseCameraSource):
    def __init__(
        self,
        frames: list[np.ndarray] | tuple[np.ndarray, ...],
        *,
        source_id: str = "synthetic",
        fps: float = 30.0,
        loop: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.source_id = source_id
        self._frames = tuple(np.asarray(frame) for frame in frames)
        self._period = 1.0 / fps
        self._loop = loop
        self._clock = clock
        self._index = 0
        self._sequence = 0
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def read(self) -> FramePacket | None:
        if not self._running or not self._frames:
            return None
        if self._index >= len(self._frames):
            if not self._loop:
                return None
            self._index = 0
        frame = self._frames[self._index]
        packet = FramePacket(frame.copy(), self._clock(), self._sequence, self.source_id)
        self._index += 1
        self._sequence += 1
        await asyncio.sleep(0)
        return packet

    async def stop(self) -> None:
        self._running = False


class FileCameraSource(BaseCameraSource):
    """OpenCV-backed still image, image sequence, or video source."""

    def __init__(
        self,
        path: str | Path | list[str | Path],
        *,
        source_id: str = "file",
        loop: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source_id = source_id
        self._paths = [Path(p) for p in path] if isinstance(path, list) else [Path(path)]
        self._loop = loop
        self._clock = clock
        self._capture: cv2.VideoCapture | None = None
        self._image_index = 0
        self._sequence = 0
        self._running = False
        self._images_mode = len(self._paths) > 1 or (
            self._paths and self._paths[0].suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        )

    async def start(self) -> None:
        if not all(path.exists() for path in self._paths):
            raise FileNotFoundError(next(str(path) for path in self._paths if not path.exists()))
        self._running = True
        self._image_index = 0
        if not self._images_mode:
            self._capture = cv2.VideoCapture(str(self._paths[0]))
            if not self._capture.isOpened():
                self._capture.release()
                self._capture = None
                raise OSError(f"unable to open video {self._paths[0]}")

    async def read(self) -> FramePacket | None:
        if not self._running:
            return None
        if self._images_mode:
            if self._image_index >= len(self._paths):
                if not self._loop:
                    return None
                self._image_index = 0
            image = cv2.imread(str(self._paths[self._image_index]), cv2.IMREAD_COLOR)
            self._image_index += 1
            if image is None:
                raise OSError("unable to decode image")
        else:
            assert self._capture is not None
            ok, image = self._capture.read()
            if not ok and self._loop:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, image = self._capture.read()
            if not ok:
                return None
        packet = FramePacket(image, self._clock(), self._sequence, self.source_id)
        self._sequence += 1
        await asyncio.sleep(0)
        return packet

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None
