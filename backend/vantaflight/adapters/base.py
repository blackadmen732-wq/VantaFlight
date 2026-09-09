"""The universal drone API.

`DroneAdapter` is the single contract the rest of VantaFlight depends on. The
flight core, UI, safety layer, and persistence never import a concrete drone
implementation; they only ever see this abstraction. Adding PX4, ArduPilot, or
any future drone later means implementing this interface, nothing else.
"""
from __future__ import annotations

import abc

from ..models import Capabilities, Telemetry


class DroneAdapter(abc.ABC):
    """Abstract, transport- and vendor-agnostic drone interface."""

    #: Stable identifier for this adapter instance (e.g. "mock-0").
    adapter_id: str = "unknown"

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish a link to the drone. Idempotent."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Tear down the link cleanly. Idempotent."""

    @abc.abstractmethod
    async def arm(self) -> None:
        """Arm the drone's motors."""

    @abc.abstractmethod
    async def disarm(self) -> None:
        """Disarm the drone's motors."""

    @abc.abstractmethod
    async def takeoff(self, target_altitude_m: float = 5.0) -> None:
        """Begin an automated takeoff to the requested altitude."""

    @abc.abstractmethod
    async def hold(self) -> None:
        """Hold current position/altitude (loiter)."""

    @abc.abstractmethod
    async def land(self) -> None:
        """Begin an automated landing."""

    @abc.abstractmethod
    def get_telemetry(self) -> Telemetry:
        """Return the latest normalized telemetry snapshot."""

    @abc.abstractmethod
    def get_capabilities(self) -> Capabilities:
        """Describe what this drone/adapter supports."""

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Whether the link is currently up."""
