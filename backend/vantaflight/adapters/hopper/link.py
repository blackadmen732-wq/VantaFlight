"""Hopper link supervisor.

Monitors the health of each individual link (camera, control/program) and
reports combined HopperHealth.  Camera and control links are independent —
camera loss does not imply control loss and vice versa.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .models import HopperConnectionState, HopperHealth, HopperHealthState

logger = logging.getLogger(__name__)


class HopperLinkSupervisor:
    """Tracks per-link health and computes the combined connection state."""

    def __init__(self, link_timeout_s: float = 5.0) -> None:
        self._timeout = link_timeout_s
        self._camera_alive: bool = False
        self._control_alive: bool = False
        self._telemetry_alive: bool = False
        self._last_camera_ok: float = 0.0
        self._last_control_ok: float = 0.0
        self._last_telemetry_ok: float = 0.0
        self._last_error: Optional[str] = None

    # -- link heartbeat updates ----------------------------------------------

    def camera_heartbeat(self) -> None:
        self._last_camera_ok = time.monotonic()
        if not self._camera_alive:
            logger.info("Hopper camera link UP")
        self._camera_alive = True

    def control_heartbeat(self) -> None:
        self._last_control_ok = time.monotonic()
        if not self._control_alive:
            logger.info("Hopper control link UP")
        self._control_alive = True

    def telemetry_heartbeat(self) -> None:
        self._last_telemetry_ok = time.monotonic()
        self._telemetry_alive = True

    def camera_lost(self) -> None:
        if self._camera_alive:
            logger.warning("Hopper camera link LOST")
        self._camera_alive = False

    def control_lost(self) -> None:
        if self._control_alive:
            logger.warning("Hopper control link LOST")
        self._control_alive = False

    def telemetry_lost(self) -> None:
        self._telemetry_alive = False

    def record_error(self, msg: str) -> None:
        self._last_error = msg
        logger.error("Hopper link error: %s", msg)

    # -- timeout check -------------------------------------------------------

    def tick(self) -> None:
        """Called periodically to detect timed-out links."""
        now = time.monotonic()
        if self._camera_alive and now - self._last_camera_ok > self._timeout:
            self.camera_lost()
        if self._control_alive and now - self._last_control_ok > self._timeout:
            self.control_lost()
        if self._telemetry_alive and now - self._last_telemetry_ok > self._timeout:
            self.telemetry_lost()

    # -- derived state -------------------------------------------------------

    @property
    def camera_link_alive(self) -> bool:
        return self._camera_alive

    @property
    def control_link_alive(self) -> bool:
        return self._control_alive

    @property
    def telemetry_link_alive(self) -> bool:
        return self._telemetry_alive

    def connection_state(
        self, operating_mode: Optional[str] = None
    ) -> HopperConnectionState:
        if not self._camera_alive and not self._control_alive:
            return HopperConnectionState.DISCONNECTED
        if self._camera_alive and not self._control_alive:
            return HopperConnectionState.CAMERA_ONLY
        if self._camera_alive and self._control_alive:
            if operating_mode == "LIVE_CONTROL":
                return HopperConnectionState.LIVE_CONTROL_READY
            if operating_mode == "PROGRAM_UPLOAD":
                return HopperConnectionState.PROGRAM_READY
            return HopperConnectionState.OBSERVE
        if self._control_alive and not self._camera_alive:
            return HopperConnectionState.DEGRADED
        return HopperConnectionState.DISCONNECTED

    def health(self) -> HopperHealth:
        h = HopperHealth(
            camera_link=HopperHealthState.OK if self._camera_alive else HopperHealthState.UNAVAILABLE,
            control_link=HopperHealthState.OK if self._control_alive else HopperHealthState.UNAVAILABLE,
            telemetry_link=HopperHealthState.OK if self._telemetry_alive else HopperHealthState.UNAVAILABLE,
            last_error=self._last_error,
        )
        h.overall = h.compute_overall()
        return h
