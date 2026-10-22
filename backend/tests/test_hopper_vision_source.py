from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from vantaflight.adapters.hopper.models import FramePacket as HopperFramePacket
from vantaflight.adapters.hopper.vision_source import HopperVisionCameraSource


class FakeConnector:
    def __init__(self, packet: HopperFramePacket | None) -> None:
        self.packet = packet
        self.connected = False

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def read_frame(self):
        packet = self.packet
        self.packet = None
        return packet


def jpeg_packet(age_s: float = 0.0) -> HopperFramePacket:
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:, :, 1] = 200
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    now = time.monotonic()
    return HopperFramePacket(
        data=encoded.tobytes(),
        capture_ts=None,
        receive_ts=time.time(),
        monotonic_ts=now - age_s,
        width=32,
        height=24,
        encoding="jpeg",
    )


@pytest.mark.asyncio
async def test_hopper_raw_frame_becomes_vantasight_frame():
    source = HopperVisionCameraSource(FakeConnector(jpeg_packet()))
    await source.start()
    frame = await source.read()
    assert frame is not None
    assert frame.image.shape[:2] == (24, 32)
    assert frame.camera_source == "hopper-camera"
    assert frame.metadata["vehicle"] == "hopper"
    await source.stop()


@pytest.mark.asyncio
async def test_stale_hopper_frame_is_dropped():
    source = HopperVisionCameraSource(
        FakeConnector(jpeg_packet(age_s=2.0)),
        max_frame_age_s=0.1,
    )
    await source.start()
    assert await source.read() is None
    await source.stop()


@pytest.mark.asyncio
async def test_no_documented_stream_does_not_fake_frames():
    source = HopperVisionCameraSource(FakeConnector(None))
    await source.start()
    assert await source.read() is None
    await source.stop()
