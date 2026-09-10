"""The flight core orchestrator."""
from __future__ import annotations

import asyncio
import enum
import time

from ..connection import ConnectionManager
from ..data import FlightDatabase
from ..digital_twin import TwinSession
from ..models import CommandResult, FlightEvent, Telemetry
from ..safety import SafetyValidator
from ..config import SOFTWARE_VERSION

_DISCONNECTED = Telemetry(connected=False)

_ADAPTER_COMMANDS = {"arm", "disarm", "takeoff", "hold", "land"}


class SessionState(str, enum.Enum):
    NO_SESSION = "NO_SESSION"
    CONNECTED = "CONNECTED"
    ACTIVE = "ACTIVE"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"


_TERMINAL = {SessionState.INTERRUPTED, SessionState.COMPLETED}


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
        self._session_state = SessionState.NO_SESSION
        self._event_queue: list[FlightEvent] = []
        self._connection_loss_logged = False
        self._twin = TwinSession()
        self._last_telemetry_time: float = 0.0
        self._metrics = _Metrics()
        self._operation_lock = asyncio.Lock()

    # -- lifecycle ----------------------------------------------------------
    async def connect(self, target=None) -> CommandResult:
        async with self._operation_lock:
            return await self._connect_inner(target)

    async def _connect_inner(self, target=None) -> CommandResult:
        if self._connections.adapter is not None and self._connections.adapter.connected:
            return CommandResult(command="connect", accepted=False, message="already connected")

        try:
            adapter = await self._connections.connect(target)
        except Exception as exc:
            return CommandResult(command="connect", accepted=False, message=str(exc))

        caps = adapter.get_capabilities()
        active = self._connections.active
        drone_id = active.drone_id if active else adapter.adapter_id
        adapter_type = caps.adapter_type
        is_simulated = caps.is_simulated
        transport = active.transport.value if active else "SIMULATED"

        self._flight_id = self._db.start_flight(
            drone_id, caps.name,
            adapter_type=adapter_type,
            is_simulated=is_simulated,
            connection_type=transport,
            software_version=SOFTWARE_VERSION,
        )
        self._session_state = SessionState.CONNECTED
        self._connection_loss_logged = False
        telemetry = adapter.get_telemetry()
        self._twin.start(battery=telemetry.battery_percentage)
        self._log_event("connected", f"connected to {caps.name}")
        return CommandResult(command="connect", accepted=True, message=f"connected to {caps.name}")

    async def disconnect(self) -> CommandResult:
        async with self._operation_lock:
            return await self._disconnect_inner()

    async def _disconnect_inner(self) -> CommandResult:
        if self._connections.adapter is None:
            return CommandResult(command="disconnect", accepted=False, message="not connected")

        if self._session_state in _TERMINAL:
            await self._connections.disconnect()
            self._flight_id = None
            self._session_state = SessionState.NO_SESSION
            return CommandResult(command="disconnect", accepted=True, message="disconnected (session already ended)")

        await self._connections.disconnect()
        self._log_event("disconnected", "disconnected from drone")
        self._end_flight("completed")
        return CommandResult(command="disconnect", accepted=True, message="disconnected")

    # -- commands -----------------------------------------------------------
    async def command(self, name: str, **kwargs) -> CommandResult:
        if name == "connect":
            return await self.connect()
        if name == "disconnect":
            return await self.disconnect()
        async with self._operation_lock:
            return await self._command_inner(name, **kwargs)

    async def _command_inner(self, name: str, **kwargs) -> CommandResult:
        if name not in _ADAPTER_COMMANDS:
            result = CommandResult(command=name, accepted=False, message=f"unknown command '{name}'")
            self._record_command(result)
            return result

        if self._session_state in _TERMINAL:
            return CommandResult(command=name, accepted=False, message="flight session has ended")

        t_start = time.monotonic()
        telemetry = self.get_telemetry()
        violation = self._safety.check(name, telemetry)
        if violation is not None:
            result = CommandResult(command=name, accepted=False, message=violation.reason)
            self._record_command(result)
            self._log_event("rejected", f"{name} rejected: {violation.reason}")
            return result

        adapter = self._connections.adapter
        assert adapter is not None
        try:
            method = getattr(adapter, name)
            await method(**kwargs)
        except Exception as exc:
            result = CommandResult(command=name, accepted=False, message=f"drone error: {exc}")
            self._record_command(result)
            self._log_event("error", f"{name} failed: {exc}")
            return result

        telemetry = self.get_telemetry()
        if self._session_state in _TERMINAL or not telemetry.connected:
            return CommandResult(
                command=name,
                accepted=False,
                message="connection lost while command was executing",
            )

        cmd_time = time.monotonic() - t_start
        self._metrics.record_command_rtt(cmd_time)

        if self._session_state == SessionState.CONNECTED and name in ("arm", "takeoff"):
            self._session_state = SessionState.ACTIVE

        self._twin.record_command()
        result = CommandResult(command=name, accepted=True, message="accepted")
        self._record_command(result)
        self._log_event(name, f"{name} accepted")
        return result

    # -- telemetry ----------------------------------------------------------
    def get_telemetry(self) -> Telemetry:
        adapter = self._connections.adapter
        if adapter is None:
            return _DISCONNECTED
        telemetry = adapter.get_telemetry()

        now = time.time()
        if self._last_telemetry_time > 0:
            interval = now - self._last_telemetry_time
            self._metrics.record_telemetry_interval(interval)
        self._last_telemetry_time = now

        if self._session_state not in _TERMINAL and not telemetry.connected:
            if not self._connection_loss_logged:
                self._connection_loss_logged = True
                self._twin.record_interruption()
                self._log_event("connection_lost", "connection to drone lost")
                self._end_flight("interrupted")
        return telemetry

    def sample(self) -> Telemetry:
        telemetry = self.get_telemetry()
        if (
            self._flight_id is not None
            and self._session_state not in _TERMINAL
            and telemetry.connected
        ):
            t_start = time.monotonic()
            self._db.record_telemetry(self._flight_id, telemetry)
            db_time = time.monotonic() - t_start
            self._metrics.record_db_write(db_time)

        if self._twin.active:
            self._twin.update(telemetry)
        return telemetry

    # -- helpers ------------------------------------------------------------
    @property
    def flight_id(self) -> int | None:
        return self._flight_id

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    @property
    def connected(self) -> bool:
        adapter = self._connections.adapter
        return adapter is not None and adapter.connected

    @property
    def twin(self) -> TwinSession:
        return self._twin

    @property
    def metrics(self) -> _Metrics:
        return self._metrics

    def drain_events(self) -> list[FlightEvent]:
        events = self._event_queue
        self._event_queue = []
        return events

    def _log_event(self, event_type: str, message: str) -> FlightEvent:
        event = FlightEvent(event_type=event_type, message=message)
        self._event_queue.append(event)
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            self._db.record_event(self._flight_id, event)
        return event

    def _record_command(self, result: CommandResult) -> None:
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            self._db.record_command(self._flight_id, result)

    def _end_flight(self, status: str) -> None:
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            self._db.end_flight(self._flight_id, status)
            self._twin.end(status)
            if status == "interrupted":
                self._session_state = SessionState.INTERRUPTED
            else:
                self._session_state = SessionState.COMPLETED
                self._flight_id = None


class _Metrics:
    """Simple local timing metrics for observability."""

    def __init__(self) -> None:
        self.telemetry_intervals: list[float] = []
        self.command_rtts: list[float] = []
        self.db_writes: list[float] = []
        self._max_samples = 100

    def record_telemetry_interval(self, seconds: float) -> None:
        self.telemetry_intervals.append(seconds)
        if len(self.telemetry_intervals) > self._max_samples:
            self.telemetry_intervals = self.telemetry_intervals[-self._max_samples:]

    def record_command_rtt(self, seconds: float) -> None:
        self.command_rtts.append(seconds)
        if len(self.command_rtts) > self._max_samples:
            self.command_rtts = self.command_rtts[-self._max_samples:]

    def record_db_write(self, seconds: float) -> None:
        self.db_writes.append(seconds)
        if len(self.db_writes) > self._max_samples:
            self.db_writes = self.db_writes[-self._max_samples:]

    @property
    def avg_telemetry_hz(self) -> float:
        if not self.telemetry_intervals:
            return 0.0
        avg_interval = sum(self.telemetry_intervals) / len(self.telemetry_intervals)
        return 1.0 / avg_interval if avg_interval > 0 else 0.0

    @property
    def avg_command_rtt_ms(self) -> float:
        if not self.command_rtts:
            return 0.0
        return (sum(self.command_rtts) / len(self.command_rtts)) * 1000

    @property
    def avg_db_write_ms(self) -> float:
        if not self.db_writes:
            return 0.0
        return (sum(self.db_writes) / len(self.db_writes)) * 1000

    def to_dict(self) -> dict:
        return {
            "telemetry_hz": round(self.avg_telemetry_hz, 1),
            "avg_command_rtt_ms": round(self.avg_command_rtt_ms, 2),
            "avg_db_write_ms": round(self.avg_db_write_ms, 2),
        }
