"""Hopper camera connector.

Connects to Hopper's Wi-Fi access point and obtains the live camera stream.
FTW documents the camera as viewable at http://192.168.2.1 after joining
Hopper's own Wi-Fi network.  The specific stream protocol (MJPEG endpoint,
HLS, etc.) is not part of FTW's published external spec, so this connector
uses a pluggable stream backend.

VantaSight sees only FramePackets from this connector — it does not know
or care that the source is Hopper vs. any other camera.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from .config import HopperCameraConfig
from .models import FramePacket
from .timesync import HopperTimeSync

logger = logging.getLogger(__name__)


class HopperCameraConnector:
    """Acquires frames from Hopper's camera stream.

    Responsibilities:
    - detect Wi-Fi network reachability
    - connect to the configured stream endpoint
    - decode frames and create FramePackets
    - always expose the *latest* frame (backpressure: stale frames are discarded)
    - reconnect after loss
    - publish FPS, frame age and drop count metrics
    """

    def __init__(
        self,
        config: Optional[HopperCameraConfig] = None,
        timesync: Optional[HopperTimeSync] = None,
    ) -> None:
        self._config = config or HopperCameraConfig()
        self._ts = timesync or HopperTimeSync()
        self._connected = False
        self._latest_frame: Optional[FramePacket] = None
        self._frame_callbacks: list[Callable[[FramePacket], None]] = []
        self._fps: float = 0.0
        self._dropped: int = 0
        self._total_received: int = 0
        self._last_frame_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def camera_fps(self) -> float:
        return self._fps

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    @property
    def frame_age_s(self) -> Optional[float]:
        if self._latest_frame is None:
            return None
        return self._latest_frame.age_s

    @property
    def stream_url(self) -> str:
        return self._config.base_url + self._config.stream_path

    def register_frame_callback(self, cb: Callable[[FramePacket], None]) -> None:
        self._frame_callbacks.append(cb)

    async def connect(self) -> bool:
        """Attempt to reach the camera endpoint.

        Returns True on success.  Does not raise; the caller checks the
        return value and reflects it in HopperHealth.
        """
        if self._connected:
            return True
        try:
            reachable = await self._probe_endpoint()
            if reachable:
                self._connected = True
                logger.info("Hopper camera connected: %s", self.stream_url)
            return reachable
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hopper camera connect failed: %s", exc)
            return False

    async def disconnect(self) -> None:
        self._connected = False
        self._latest_frame = None
        logger.info("Hopper camera disconnected")

    async def read_frame(self) -> Optional[FramePacket]:
        """Return the most recent frame without blocking, or None."""
        return self._latest_frame

    def _ingest_raw_frame(self, data: bytes, width: int = 0, height: int = 0) -> None:
        """Called by the stream-reading loop when a raw frame arrives."""
        now = time.monotonic()
        stamp = self._ts.stamp()
        frame = FramePacket(
            data=data,
            capture_ts=None,
            receive_ts=stamp.receive_ts,
            monotonic_ts=stamp.monotonic_ts,
            width=width,
            height=height,
        )

        # Backpressure: if there is already a queued frame, count it as dropped.
        if self._latest_frame is not None and self._latest_frame.age_s < 0.001:
            self._dropped += 1

        self._latest_frame = frame
        self._total_received += 1

        # Rolling FPS estimate.
        if self._last_frame_at > 0:
            dt = now - self._last_frame_at
            if dt > 0:
                instant_fps = 1.0 / dt
                self._fps = 0.9 * self._fps + 0.1 * instant_fps
        self._last_frame_at = now

        for cb in self._frame_callbacks:
            try:
                cb(frame)
            except Exception:  # noqa: BLE001
                pass

    async def _probe_endpoint(self) -> bool:
        """Non-blocking reachability check for the camera base URL.

        Uses a raw socket connect rather than importing httpx/aiohttp so the
        adapter has no heavy HTTP dependency.  Returns True if the host is
        reachable on port 80.
        """
        import socket

        host = self._config.base_url.removeprefix("http://").removeprefix("https://").split("/")[0]
        port = 80
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: socket.create_connection((host, port), timeout=self._config.connect_timeout_s),
                ),
                timeout=self._config.connect_timeout_s + 0.5,
            )
            return True
        except (OSError, asyncio.TimeoutError):
            return False
