"""The flight core orchestrator.

`FlightController` ties the pieces together while staying completely
drone-agnostic: it discovers/connects through the `ConnectionManager`, gates
every command through the `SafetyValidator`, drives the active `DroneAdapter`,
and records everything to the local SQLite database. Nothing here knows or
cares whether the drone is a simulator, PX4, or ArduPilot.
"""
from __future__ import annotations

from ..connection import ConnectionManager
from ..data import FlightDatabase
from ..models import CommandResult, FlightEvent, Telemetry
from ..safety import SafetyValidator

_DISCONNECTED = Telemetry(connected=False)

# Commands routed through the safety layer and dispatched to the adapter.
_ADAPTER_COMMANDS = {"arm", "disarm", "takeoff", "hold", "land"}


class FlightController:
    """Coordinates connection, safety, command dispatch, and recording."""

    def __init__(
        self,
        database: FlightDatabase,
        connection_manager: ConnectionManager | None = None,
        validator: SafetyValidator | None = None,
    ) -> None:
        self._db = database
        self._connections = connection_manager or ConnectionManager()
        self._safety = validator or SafetyValidator()
        self._flight_id: int | None = None
        self._was_connected = False
        self._event_queue: list[FlightEvent] = []

    # -- lifecycle ----------------------------------------------------------
    async def connect(self) -> CommandResult:
        """Discover and connect to a drone, opening a new flight record."""
        if self._connections.adapter is not None and self._connections.adapter.connected:
            return CommandResult(command="connect", accepted=False, message="already connected")

        adapter = await self._connections.connect()
        caps = adapter.get_capabilities()
        active = self._connections.active
        drone_id = active.drone_id if active else adapter.adapter_id
        self._flight_id = self._db.start_flight(drone_id, caps.name)
        self._was_connected = True
        self._log_event("connected", f"connected to {caps.name}")
        return CommandResult(command="connect", accepted=True, message=f"connected to {caps.name}")

    async def disconnect(self) -> CommandResult:
        """Cleanly disconnect and close the active flight."""
        if self._connections.adapter is None:
            return CommandResult(command="disconnect", accepted=False, message="not connected")
        await self._connections.disconnect()
        self._log_event("disconnected", "disconnected from drone")
        self._end_flight("completed")
        self._was_connected = False
        return CommandResult(command="disconnect", accepted=True, message="disconnected")

    # -- commands -----------------------------------------------------------
    async def command(self, name: str, **kwargs) -> CommandResult:
        """Run a command through safety, dispatch it, and record the outcome."""
        if name == "connect":
            return await self.connect()
        if name == "disconnect":
            return await self.disconnect()
        if name not in _ADAPTER_COMMANDS:
            result = CommandResult(command=name, accepted=False, message=f"unknown command '{name}'")
            self._record_command(result)
            return result

        telemetry = self.get_telemetry()
        violation = self._safety.check(name, telemetry)
        if violation is not None:
            result = CommandResult(command=name, accepted=False, message=violation.reason)
            self._record_command(result)
            self._log_event("rejected", f"{name} rejected: {violation.reason}")
            return result

        adapter = self._connections.adapter
        assert adapter is not None  # safety guarantees connected
        try:
            method = getattr(adapter, name)
            await method(**kwargs)
        except Exception as exc:  # surface adapter errors as rejected commands
            result = CommandResult(command=name, accepted=False, message=f"drone error: {exc}")
            self._record_command(result)
            self._log_event("error", f"{name} failed: {exc}")
            return result

        result = CommandResult(command=name, accepted=True, message="accepted")
        self._record_command(result)
        self._log_event(name, f"{name} accepted")
        return result

    # -- telemetry ----------------------------------------------------------
    def get_telemetry(self) -> Telemetry:
        """Latest telemetry, with clean handling of unexpected link loss."""
        adapter = self._connections.adapter
        if adapter is None:
            return _DISCONNECTED
        telemetry = adapter.get_telemetry()
        if self._was_connected and not telemetry.connected:
            # Link dropped without a clean disconnect: record once and mark
            # the flight interrupted so state stays consistent.
            self._log_event("connection_lost", "connection to drone lost")
            self._end_flight("interrupted")
            self._was_connected = False
        return telemetry

    def sample(self) -> Telemetry:
        """Get telemetry and persist it as a sample (used by the stream loop)."""
        telemetry = self.get_telemetry()
        if self._flight_id is not None and telemetry.connected:
            self._db.record_telemetry(self._flight_id, telemetry)
        return telemetry

    # -- helpers ------------------------------------------------------------
    @property
    def flight_id(self) -> int | None:
        return self._flight_id

    @property
    def connected(self) -> bool:
        adapter = self._connections.adapter
        return adapter is not None and adapter.connected

    def drain_events(self) -> list[FlightEvent]:
        """Return and clear events accumulated since the last drain."""
        events = self._event_queue
        self._event_queue = []
        return events

    def _log_event(self, event_type: str, message: str) -> FlightEvent:
        event = FlightEvent(event_type=event_type, message=message)
        self._event_queue.append(event)
        if self._flight_id is not None:
            self._db.record_event(self._flight_id, event)
        return event

    def _record_command(self, result: CommandResult) -> None:
        if self._flight_id is not None:
            self._db.record_command(self._flight_id, result)

    def _end_flight(self, status: str) -> None:
        if self._flight_id is not None:
            self._db.end_flight(self._flight_id, status)
