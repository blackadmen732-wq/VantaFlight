"""Bridge Hopper camera frames into the VantaSight CameraSource contract.

The bridge supports two honest paths:
1. bytes delivered by HopperCameraConnector through an official/injected backend;
2. an explicitly configured OpenCV-compatible stream URL.

It never guesses an undocumented FTW MJPEG/HLS endpoint.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import cv2
import numpy as np

from ...vision.concepts import CameraProfile, FramePacket
from .camera import HopperCameraConnector
from .errors import HopperCameraUnavailable


class HopperVisionCameraSource:
    source_id = "hopper-camera"

    def __init__(
        self,
        connector: HopperCameraConnector,
        *,
        profile: Optional[CameraProfile] = None,
        stream_url: Optional[str] = None,
        max_frame_age_s: float = 0.5,
    ) -> None:
        self._connector = connector
        self._profile = profile
        self._stream_url = stream_url
        self._max_frame_age_s = max_frame_age_s
        self._capture: cv2.VideoCapture | None = None
        self._sequence = 0
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        if not await self._connector.connect():
            raise HopperCameraUnavailable("Hopper camera host is not reachable.")
        if self._stream_url:
            loop = asyncio.get_running_loop()
            capture = await loop.run_in_executor(None, lambda: cv2.VideoCapture(self._stream_url))
            if not capture.isOpened():
                capture.release()
                raise HopperCameraUnavailable(
                    f"Configured Hopper stream URL could not be opened: {self._stream_url}"
                )
            self._capture = capture
        self._running = True

    async def read(self) -> FramePacket | None:
        if not self._running:
            return None

        # Preferred path: bytes from a future official/injected Hopper backend.
        raw = await self._connector.read_frame()
        if raw is not None and raw.age_s <= self._max_frame_age_s:
            encoded = np.frombuffer(raw.data, dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if image is not None:
                self._sequence += 1
                now = time.monotonic()
                capture_ts = raw.monotonic_ts
                return FramePacket(
                    image=image,
                    capture_timestamp=capture_ts,
                    receive_timestamp=max(capture_ts, now),
                    sequence=self._sequence,
                    camera_source=self.source_id,
                    camera_profile=self._profile,
                    metadata={
                        "vehicle": "hopper",
                        "encoding": raw.encoding,
                        "source": "hopper_connector",
                    },
                )

        if self._capture is None:
            return None

        loop = asyncio.get_running_loop()
        ok, image = await loop.run_in_executor(None, self._capture.read)
        if not ok or image is None:
            return None
        now = time.monotonic()
        self._sequence += 1
        return FramePacket(
            image=image,
            capture_timestamp=now,
            receive_timestamp=now,
            sequence=self._sequence,
            camera_source=self.source_id,
            camera_profile=self._profile,
            metadata={"vehicle": "hopper", "source": "configured_stream"},
        )

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            capture = self._capture
            self._capture = None
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, capture.release)
        await self._connector.disconnect()
