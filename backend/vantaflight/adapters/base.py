"""The universal drone API.

`DroneAdapter` is the single contract the rest of VantaFlight depends on. The
flight core, UI, safety layer, and persistence never import a concrete drone
implementation; they only ever see this abstraction. Adding PX4, Hopper, or
any future drone means implementing this interface rather than bypassing the
safety/execution boundary.
"""
from __future__ import annotations

import abc
from typing import Optional

from ..models import Capabilities, CommandResult, Telemetry


class DroneAdapter(abc.ABC):
    """Abstract, transport- and vendor-agnostic drone interface."""

    adapter_id: str = "unknown"

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish a link to the drone. Idempotent."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Tear down the link cleanly. Idempotent."""

    @abc.abstractmethod
    async def arm(self) -> Optional[CommandResult]:
        """Arm the drone. Return a typed outcome when the adapter can report one."""

    @abc.abstractmethod
    async def disarm(self) -> Optional[CommandResult]:
        """Disarm the drone. Return a typed outcome when available."""

    @abc.abstractmethod
    async def takeoff(self, target_altitude_m: float = 5.0) -> Optional[CommandResult]:
        """Begin automated takeoff to the requested altitude."""

    @abc.abstractmethod
    async def hold(self) -> Optional[CommandResult]:
        """Hold current position/altitude."""

    @abc.abstractmethod
    async def land(self) -> Optional[CommandResult]:
        """Begin automated landing."""

    @abc.abstractmethod
    def get_telemetry(self) -> Telemetry:
        """Return the latest normalized telemetry snapshot."""

    @abc.abstractmethod
    def get_capabilities(self) -> Capabilities:
        """Describe what this drone/adapter supports."""

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Whether at least one useful link to the adapter is currently up."""
