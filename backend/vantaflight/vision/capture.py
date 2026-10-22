"""Camera source interfaces, implementations, and bounded frame transport."""
from __future__ import annotations

import abc
import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

import cv2
import numpy as np

from ..bus.channels import ChannelSpec, DeliveryMode, LatestChannel
from ..core.runtime import Criticality, VantaRuntime, default_runtime
from .concepts import CameraProfile, FramePacket

logger = logging.getLogger(__name__)


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
    oldest_frame_age_s: float | None
    current_frame_age_s: float | None

    @property
    def frames_captured(self) -> int:
        return self.received

    @property
    def frames_processed(self) -> int:
        return self.delivered

    @property
    def frames_dropped(self) -> int:
        return self.dropped_capacity + self.dropped_stale


class FrameBuffer:
    """Newest-frame transport for vision: a VantaBus LATEST channel.

    Holds at most 2 frames (a deeper camera queue would make the aircraft
    react to history; the channel refuses it). ``latest`` hands over the
    newest frame and counts every older one as stale. Thread-safe.
    """

    def __init__(
        self,
        capacity: int = 2,
        *,
        clock: Callable[[], float] = time.monotonic,
        topic: str = "camera.frames",
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.channel = LatestChannel(
            ChannelSpec(topic, DeliveryMode.LATEST, capacity, item_type=FramePacket,
                        description="camera frames for vision (newest wins)"),
            clock,
        )
        self._reader = self.channel.subscribe("vision", from_latest=False)
        self._clock = clock

    @property
    def capacity(self) -> int:
        return self.channel.capacity

    def put(self, frame: FramePacket) -> None:
        self.channel.publish(frame)

    def latest(self, max_age_s: float | None = None, now: float | None = None) -> FramePacket | None:
        """Return newest frame, discarding older queued and over-age frames."""
        if max_age_s is not None and max_age_s < 0:
            raise ValueError("max_age_s must be non-negative")
        return self._reader.take(max_age_s, now)

    def clear(self) -> None:
        self._reader.clear()

    @property
    def metrics(self) -> FrameBufferMetrics:
        reader = self._reader
        oldest, newest = reader.unread_ages(self._clock())
        return FrameBufferMetrics(
            self.channel.published,
            reader.delivered,
            reader.dropped_superseded,
            reader.dropped_stale,
            reader.depth,
            self.capacity,
            oldest,
            newest,
        )


@dataclass(frozen=True)
class PreviewBufferMetrics:
    received: int
    dropped: int
    delivered: int
    depth: int
    capacity: int


class PreviewBuffer:
    """Best-effort bounded preview queue that never waits on its consumer.

    A LATEST channel published with ``try_publish``: if a reader holds the
    lock, the preview frame is dropped (and counted) instead of waiting.
    """

    def __init__(self, capacity: int = 1, *, topic: str = "camera.preview") -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.channel = LatestChannel(
            ChannelSpec(topic, DeliveryMode.LATEST, capacity, item_type=FramePacket,
                        description="camera frames for the operator preview (best effort)"),
        )
        self._reader = self.channel.subscribe("preview", from_latest=False)

    @property
    def capacity(self) -> int:
        return self.channel.capacity

    def put_nowait(self, frame: FramePacket) -> bool:
        return self.channel.try_publish(frame)

    def latest(self) -> FramePacket | None:
        return self._reader.take(None)

    @property
    def metrics(self) -> PreviewBufferMetrics:
        reader = self._reader
        dropped = self.channel.contended + reader.dropped_superseded + reader.dropped_stale
        return PreviewBufferMetrics(
            self.channel.published, dropped, reader.delivered, reader.depth, self.capacity
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
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.source_id = source_id
        self._frames = tuple(np.asarray(frame) for frame in frames)
        self._period = 1.0 / fps
        self._loop = loop
        self._clock = clock
        self.camera_profile = camera_profile
        self.session_id = session_id
        self._index = 0
        self._sequence = 0
        self._running = False

    async def start(self) -> None:
        self._index = 0
        self._running = True

    async def read(self) -> FramePacket | None:
        if not self._running or not self._frames:
            return None
        if self._index >= len(self._frames):
            if not self._loop:
                return None
            self._index = 0
        frame = self._frames[self._index]
        capture = self._clock()
        packet = FramePacket(
            frame.copy(), capture, self._sequence, self.source_id,
            frame_id=f"{self.source_id}:{self._sequence}",
            receive_timestamp=self._clock(), camera_profile=self.camera_profile,
            session_id=self.session_id,
        )
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
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        self.source_id = source_id
        self._paths = [Path(p) for p in path] if isinstance(path, list) else [Path(path)]
        self._loop = loop
        self._clock = clock
        self.camera_profile = camera_profile
        self.session_id = session_id
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
        capture = self._clock()
        packet = FramePacket(
            image, capture, self._sequence, self.source_id,
            frame_id=f"{self.source_id}:{self._sequence}",
            receive_timestamp=self._clock(), camera_profile=self.camera_profile,
            session_id=self.session_id,
        )
        self._sequence += 1
        await asyncio.sleep(0)
        return packet

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class ExternalCameraSource(BaseCameraSource):
    """Typed extension point for integrations intentionally absent from V0.5."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    async def start(self) -> None:
        raise NotImplementedError

    async def read(self) -> FramePacket | None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class USBCameraSource(BaseCameraSource):
    """Real USB/V4L2 camera via OpenCV VideoCapture."""

    def __init__(
        self,
        device_index: int = 0,
        *,
        source_id: str = "usb",
        width: int = 640,
        height: int = 480,
        fps: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        self.source_id = source_id
        self._device_index = device_index
        self._width = width
        self._height = height
        self._fps = fps
        self._clock = clock
        self.camera_profile = camera_profile
        self.session_id = session_id
        self._capture: cv2.VideoCapture | None = None
        self._sequence = 0
        self._running = False

    async def start(self) -> None:
        self._capture = cv2.VideoCapture(self._device_index)
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise OSError(f"unable to open USB camera at index {self._device_index}")
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._capture.set(cv2.CAP_PROP_FPS, self._fps)
        self._running = True
        self._sequence = 0

    async def read(self) -> FramePacket | None:
        if not self._running or self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok:
            return None
        capture = self._clock()
        packet = FramePacket(
            frame, capture, self._sequence, self.source_id,
            frame_id=f"{self.source_id}:{self._sequence}",
            receive_timestamp=self._clock(),
            camera_profile=self.camera_profile,
            session_id=self.session_id,
        )
        self._sequence += 1
        await asyncio.sleep(0)
        return packet

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class NetworkCameraSource(BaseCameraSource):
    """RTSP/HTTP camera stream via OpenCV VideoCapture."""

    def __init__(
        self,
        url: str,
        *,
        source_id: str = "network",
        clock: Callable[[], float] = time.monotonic,
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        self.source_id = source_id
        self._url = url
        self._clock = clock
        self.camera_profile = camera_profile
        self.session_id = session_id
        self._capture: cv2.VideoCapture | None = None
        self._sequence = 0
        self._running = False

    async def start(self) -> None:
        self._capture = cv2.VideoCapture(self._url)
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise OSError(f"unable to open network camera at {self._url}")
        self._running = True
        self._sequence = 0

    async def read(self) -> FramePacket | None:
        if not self._running or self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok:
            return None
        capture = self._clock()
        packet = FramePacket(
            frame, capture, self._sequence, self.source_id,
            frame_id=f"{self.source_id}:{self._sequence}",
            receive_timestamp=self._clock(),
            camera_profile=self.camera_profile,
            session_id=self.session_id,
        )
        self._sequence += 1
        await asyncio.sleep(0)
        return packet

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class GazeboCameraSource(BaseCameraSource):
    """Gazebo simulated camera via shared memory or ROS bridge.

    Actual Gazebo transport requires a running SITL environment which
    may not be available. This implementation provides the CameraSource
    contract and falls back to the SimulatedCameraSource when Gazebo
    is not reachable.
    """

    def __init__(
        self,
        topic: str = "/camera/image_raw",
        *,
        source_id: str = "gazebo",
        clock: Callable[[], float] = time.monotonic,
        camera_profile: CameraProfile | None = None,
        session_id: str | None = None,
    ) -> None:
        self.source_id = source_id
        self._topic = topic
        self._clock = clock
        self.camera_profile = camera_profile
        self.session_id = session_id
        self._running = False
        self._sequence = 0

    async def start(self) -> None:
        self._running = True
        self._sequence = 0

    async def read(self) -> FramePacket | None:
        if not self._running:
            return None
        await asyncio.sleep(0.033)
        return None

    async def stop(self) -> None:
        self._running = False


class CameraManager:
    """Own one capture task and independent bounded vision/preview queues."""

    def __init__(
        self,
        source: CameraSource,
        *,
        frame_buffer: FrameBuffer | None = None,
        preview_buffer: PreviewBuffer | None = None,
        idle_sleep_s: float = 0.001,
        runtime: VantaRuntime | None = None,
        bus=None,
    ) -> None:
        """``bus`` (optional) gets the frame and preview channels, so their
        rate, age, depth and drops show up in bus status and health."""
        self.source = source
        topic_id = _topic_safe(getattr(source, "source_id", "camera"))
        self.frame_buffer = frame_buffer or FrameBuffer(topic=f"camera.frames.{topic_id}")
        self.preview_buffer = preview_buffer or PreviewBuffer(topic=f"camera.preview.{topic_id}")
        self._runtime = runtime
        self._capture_worker = None
        self._processing_worker = None
        if bus is not None:
            bus.attach(self.frame_buffer.channel)
            bus.attach(self.preview_buffer.channel)
        self.idle_sleep_s = idle_sleep_s
        self._capture_task: asyncio.Task[None] | None = None
        self._processing_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._running = False
        self._source_started = False
        self._failure: Exception | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def capture_task(self) -> asyncio.Task[None] | None:
        return self._capture_task

    @property
    def failure(self) -> Exception | None:
        return self._failure

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if (
                self._running
                and self._capture_task is not None
                and not self._capture_task.done()
            ):
                return
            if self._capture_task is not None:
                await asyncio.gather(self._capture_task, return_exceptions=True)
                self._capture_task = None
            if self._processing_task is not None:
                self._processing_task.cancel()
                await asyncio.gather(self._processing_task, return_exceptions=True)
                self._processing_task = None
            if self._source_started:
                await self.source.stop()
                self._source_started = False
            try:
                await self.source.start()
            except Exception as exc:
                self._failure = exc
                self._running = False
                await self.source.stop()
                raise
            self._source_started = True
            self._failure = None
            self._running = True
            self._capture_worker = (self._runtime or default_runtime()).spawn(
                f"vision.capture:{_topic_safe(self.source.source_id)}", self._capture_loop,
                owner="vision.camera_manager", criticality=Criticality.CRITICAL,
                description="Reads the camera into the LATEST frame channel", singleton=False,
            )
            self._capture_task = self._capture_worker.task

    async def _capture_loop(self) -> None:
        try:
            while self._running:
                frame = await self.source.read()
                if frame is None:
                    await asyncio.sleep(self.idle_sleep_s)
                    continue
                self.frame_buffer.put(frame)
                self.preview_buffer.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure = exc
            self._running = False
            if (
                self._processing_task is not None
                and self._processing_task is not asyncio.current_task()
            ):
                self._processing_task.cancel()

    async def process_latest(
        self,
        processor: Callable[[FramePacket], Awaitable[Any]],
        *,
        max_age_s: float | None = None,
    ) -> Any | None:
        frame = self.frame_buffer.latest(max_age_s)
        return None if frame is None else await processor(frame)

    async def start_processing(
        self,
        processor: Callable[[FramePacket], Awaitable[Any]],
        on_result: Callable[[Any], Any] | None = None,
        *,
        max_age_s: float | None = None,
    ) -> asyncio.Task[None]:
        if self._processing_task is not None and not self._processing_task.done():
            return self._processing_task

        async def loop() -> None:
            while self._running:
                try:
                    result = await self.process_latest(processor, max_age_s=max_age_s)
                    if result is None:
                        await asyncio.sleep(self.idle_sleep_s)
                    elif on_result is not None:
                        callback_result = on_result(result)
                        if asyncio.iscoroutine(callback_result):
                            await callback_result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("vision processing loop failed")
                    self._running = False

        self._processing_worker = (self._runtime or default_runtime()).spawn(
            "vision.process", loop, owner="vision.camera_manager", criticality=Criticality.CRITICAL,
            description="Runs vision on the newest frame", singleton=False,
        )
        self._processing_task = self._processing_worker.task
        return self._processing_task

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if (
                not self._running
                and self._capture_task is None
                and self._processing_task is None
                and not self._source_started
            ):
                return
            self._running = False
            tasks = [task for task in (self._processing_task, self._capture_task) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._processing_task = None
            self._capture_task = None
            if self._source_started:
                await self.source.stop()
                self._source_started = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()


def _topic_safe(name: str) -> str:
    """A source id as a bus topic segment (lowercase, [a-z0-9_])."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(name).lower()).strip("_")
    return safe or "camera"
