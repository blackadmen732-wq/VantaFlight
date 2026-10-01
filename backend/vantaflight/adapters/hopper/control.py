"""Hopper control connector and command transport abstraction.

No undocumented FTW packet format is implemented here. A concrete transport
must be backed by an official, supported FTW interface.
"""
from __future__ import annotations

import abc
import logging
from typing import Optional

from ...models import CommandResult, CommandStatus
from .errors import (
    HopperCommandExpired,
    HopperControlUnavailable,
    HopperUnsupportedCapability,
)
from .models import HopperCommand

logger = logging.getLogger(__name__)


class HopperControlTransport(abc.ABC):
    @abc.abstractmethod
    async def connect(self) -> bool:
        ...

    @abc.abstractmethod
    async def disconnect(self) -> None:
        ...

    @abc.abstractmethod
    async def send_command(self, command: HopperCommand) -> CommandResult | bool:
        """Send a command and report whether it actually left the transport."""

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        ...

    @property
    @abc.abstractmethod
    def supported_commands(self) -> frozenset[str]:
        ...


class NoTransport(HopperControlTransport):
    @property
    def connected(self) -> bool:
        return False

    @property
    def supported_commands(self) -> frozenset[str]:
        return frozenset()

    async def connect(self) -> bool:
        logger.info(
            "Hopper live control unavailable: no official FTW control transport configured."
        )
        return False

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: HopperCommand) -> CommandResult:
        return CommandResult.unsupported(
            command.command_type,
            "No official FTW live-control transport is configured.",
        )


class HopperCommandMapper:
    """Semantic mapper only; no proprietary packet encoding lives here."""

    def map(self, command: HopperCommand) -> dict:
        mapper = getattr(self, f"_map_{command.command_type}", None)
        if mapper is None:
            raise HopperUnsupportedCapability(
                f"No supported command mapping for '{command.command_type}'."
            )
        return mapper(command)

    def _map_takeoff(self, cmd: HopperCommand) -> dict:
        return {"action": "takeoff", "altitude_m": cmd.params.get("altitude_m", 1.2)}

    def _map_land(self, cmd: HopperCommand) -> dict:
        return {"action": "land"}

    def _map_hold(self, cmd: HopperCommand) -> dict:
        return {"action": "hold"}

    def _map_arm(self, cmd: HopperCommand) -> dict:
        return {"action": "arm"}

    def _map_disarm(self, cmd: HopperCommand) -> dict:
        return {"action": "disarm"}

    def _map_velocity(self, cmd: HopperCommand) -> dict:
        return {"action": "velocity", **cmd.params}

    def _map_position(self, cmd: HopperCommand) -> dict:
        return {"action": "position", **cmd.params}

    def _map_relative_move(self, cmd: HopperCommand) -> dict:
        return {"action": "relative_move", **cmd.params}

    def _map_yaw(self, cmd: HopperCommand) -> dict:
        return {"action": "yaw", **cmd.params}


class HopperControlConnector:
    """Orchestrates one official control transport and preserves command truth."""

    def __init__(self, transport: Optional[HopperControlTransport] = None) -> None:
        self._transport = transport or NoTransport()
        self._mapper = HopperCommandMapper()

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def supported_commands(self) -> frozenset[str]:
        return self._transport.supported_commands

    async def connect(self) -> bool:
        return await self._transport.connect()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    async def send(self, command: HopperCommand) -> CommandResult:
        if not self._transport.connected:
            return CommandResult.no_transport(command.command_type)
        if command.expired:
            raise HopperCommandExpired(
                f"Command '{command.command_type}' expired before dispatch."
            )
        if command.command_type not in self._transport.supported_commands:
            return CommandResult.unsupported(
                command.command_type,
                f"Active Hopper transport does not support '{command.command_type}'.",
            )

        # Map first so unsupported semantics fail before touching hardware.
        self._mapper.map(command)
        outcome = await self._transport.send_command(command)
        if isinstance(outcome, CommandResult):
            return outcome
        if outcome is True:
            return CommandResult.ok(command.command_type)
        return CommandResult(
            command=command.command_type,
            accepted=False,
            status=CommandStatus.REJECTED,
            message=f"Hopper transport reported failure for '{command.command_type}'.",
        )

    def set_transport(self, transport: HopperControlTransport) -> None:
        self._transport = transport
