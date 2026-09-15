"""Hopper discovery service.

Discovers available Hopper units through officially documented interfaces.
Today: Wi-Fi camera reachability at the documented IP.  Bluetooth discovery
will be added via the official FTW SDK when it is published.

Never scans arbitrary BLE services for proprietary protocol details.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Optional

from .config import HopperCameraConfig

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredHopper:
    hopper_id: str
    nickname: str
    camera_url: Optional[str]
    control_transport: str          # "none", "ble_official", "sdk_official"
    firmware: Optional[str] = None
    serial: Optional[str] = None    # only if officially exposed by SDK


class HopperDiscovery:
    """Finds available Hopper units."""

    def __init__(self, camera_config: Optional[HopperCameraConfig] = None) -> None:
        self._camera_config = camera_config or HopperCameraConfig()

    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredHopper]:
        """Probe all known discovery channels and return found units."""
        results: list[DiscoveredHopper] = []

        camera_reachable = await self._probe_camera(timeout_s)
        if camera_reachable:
            results.append(
                DiscoveredHopper(
                    hopper_id="hopper-wifi-0",
                    nickname="Hopper",
                    camera_url=self._camera_config.base_url,
                    control_transport="none",
                )
            )
            logger.info("Hopper Wi-Fi camera reachable at %s", self._camera_config.base_url)
        else:
            logger.debug(
                "Hopper Wi-Fi camera not reachable at %s",
                self._camera_config.base_url,
            )

        return results

    async def _probe_camera(self, timeout_s: float) -> bool:
        host = (
            self._camera_config.base_url
            .removeprefix("http://")
            .removeprefix("https://")
            .split("/")[0]
        )
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: socket.create_connection((host, 80), timeout=timeout_s),
                ),
                timeout=timeout_s + 0.5,
            )
            return True
        except (OSError, asyncio.TimeoutError):
            return False
