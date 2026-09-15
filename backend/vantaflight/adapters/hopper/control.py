"""Hopper control connector and command transport abstraction.

The transport layer is intentionally abstract.  Concrete implementations
will be added when FTW publishes an official Python SDK or browser bridge.
Today this file provides:

  HopperControlTransport  — abstract base all transports implement
  NoTransport             — placeholder when no live SDK is available
  HopperCommandMapper     — translates normalized VantaFlight commands
                            into whichever transport is active
  HopperControlConnector  — orchestrates transport + command mapper

VantaFlight must never call the transport directly; it only calls
HopperControlConnector.  If no transport supports a capability,
HopperUnsupportedCapability is raised explicitly.
"""
from __future__ import annotations

import abc
import logging
from typing import Any, Optional

from .errors import HopperControlUnavailable, HopperUnsupportedCapability
from .models import HopperCommand

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------


class HopperControlTransport(abc.ABC):
    """Contract every control transport must implement."""

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Establish the transport link.  Returns True on success."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Tear down the transport link cleanly."""

    @abc.abstractmethod
    async def send_command(self, command: HopperCommand) -> bool:
        """Send a command.  Returns True if acknowledged or best-effort sent."""

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Whether the transport link is currently alive."""

    @property
    @abc.abstractmethod
    def supported_commands(self) -> frozenset[str]:
        """Set of command_type strings this transport can carry."""


class NoTransport(HopperControlTransport):
    """Placeholder transport installed when no official SDK is available.

    Any command send raises HopperUnsupportedCapability so VantaFlight
    gets an explicit error rather than silent failure.
    """

    @property
    def connected(self) -> bool:
        return False

    @property
    def supported_commands(self) -> frozenset[str]:
        return frozenset()

    async def connect(self) -> bool:
        logger.info(
            "HopperControlTransport: no official FTW SDK available; "
            "live control is UNSUPPORTED until FTW publishes one."
        )
        return False

    async def disconnect(self) -> None:
        pass

    async def send_command(self, command: HopperCommand) -> bool:
        raise HopperUnsupportedCapability(
            f"Command '{command.command_type}' cannot be sent: "
            "no official FTW live-control interface is available."
        )


# ---------------------------------------------------------------------------
# Command mapper
# ---------------------------------------------------------------------------


class HopperCommandMapper:
    """Translates normalized VantaFlight commands into transport payloads.

    This is the only place in the Hopper adapter that knows the mapping
    between VantaFlight semantics and the transport's command vocabulary.
    It never invents undocumented packet formats.
    """

    def map(self, command: HopperCommand) -> dict[str, Any]:
        """Return a transport-ready payload dict, or raise if unmappable."""
        mapper = getattr(self, f"_map_{command.command_type}", None)
        if mapper is None:
            raise HopperUnsupportedCapability(
                f"No command mapping for '{command.command_type}'."
            )
        return mapper(command)

    # Individual mappers — populated when official SDK documents the interface.

    def _map_takeoff(self, cmd: HopperCommand) -> dict:
        return {"action": "takeoff", "altitude_m": cmd.params.get("altitude_m", 1.2)}

    def _map_land(self, cmd: HopperCommand) -> dict:
        return {"action": "land"}

    def _map_hold(self, cmd: HopperCommand) -> dict:
        return {"action": "hold"}


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class HopperControlConnector:
    """Orchestrates transport selection and command dispatch."""

    def __init__(self, transport: Optional[HopperControlTransport] = None) -> None:
        self._transport = transport or NoTransport()
        self._mapper = HopperCommandMapper()

    @property
    def connected(self) -> bool:
        return self._transport.connected

    async def connect(self) -> bool:
        return await self._transport.connect()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    async def send(self, command: HopperCommand) -> bool:
        if not self._transport.connected:
            raise HopperControlUnavailable(
                "Control transport is not connected; cannot send command."
            )
        if command.expired:
            from .errors import HopperCommandExpired
            raise HopperCommandExpired(
                f"Command '{command.command_type}' expired before dispatch."
            )
        if command.command_type not in self._transport.supported_commands:
            raise HopperUnsupportedCapability(
                f"Transport does not support '{command.command_type}'."
            )
        return await self._transport.send_command(command)

    def set_transport(self, transport: HopperControlTransport) -> None:
        """Swap the transport when a new official SDK becomes available."""
        self._transport = transport
