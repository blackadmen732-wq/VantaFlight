"""The flight core orchestrator.

`FlightController` is the one place where connection, safety, commands,
missions, failsafes, recording, and the digital twin meet. The server calls
`tick()` once per telemetry period; everything time-driven (mission progress,
failsafe checks) happens inside that tick, so there are no hidden background
tasks.
"""
from __future__ import annotations

import asyncio
import collections
import enum
import time

from ..connection import ConnectionManager
from ..data import FlightDatabase
from ..digital_twin import TwinSession
from ..mission import MissionPlan, MissionRunner, check_plan
from ..models import CommandResult, FlightEvent, Telemetry
from ..safety import (
    FailsafeAction,
    FailsafeGuardian,
    FailsafeTrigger,
    Geofence,
    SafetyValidator,
)
from ..config import SOFTWARE_VERSION

_DISCONNECTED = Telemetry(connected=False)

_ADAPTER_COMMANDS = {"arm", "disarm", "takeoff", "hold", "land", "goto", "return_home"}

# Operator commands that take the aircraft away from an active mission.
_OVERRIDES_MISSION = {"disarm", "takeoff", "hold", "land", "goto", "return_home"}

# Who asked for a command. Recorded with it so a flight log shows whether the
# operator, the mission, or a failsafe moved the aircraft.
SOURCE_PILOT = "pilot"
SOURCE_MISSION = "mission"
SOURCE_FAILSAFE = "failsafe"


class SessionState(str, enum.Enum):
    NO_SESSION = "NO_SESSION"
    CONNECTED = "CONNECTED"
    ACTIVE = "ACTIVE"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"


_TERMINAL = {SessionState.INTERRUPTED, SessionState.COMPLETED}

# Events kept in memory so a client that (re)connects sees recent history.
RECENT_EVENTS = 50


class FlightController:
    """Coordinates connection, safety, command dispatch, and recording."""

    def __init__(
        self,
        database: FlightDatabase,
        connection_manager: ConnectionManager | None = None,
        validator: SafetyValidator | None = None,
        guardian: FailsafeGuardian | None = None,
        mission_clock=time.monotonic,
    ) -> None:
        self._db = database
        self._connections = connection_manager or ConnectionManager()
        self._guardian = guardian or FailsafeGuardian()
        self._geofence: Geofence = self._guardian.config.geofence
        self._safety = validator or SafetyValidator(self._geofence)
        self._mission = MissionRunner(
            execute=lambda name, **kw: self.command(name, source=SOURCE_MISSION, **kw),
            on_event=self._log_event,
            clock=mission_clock,
        )
        self._flight_id: int | None = None
        self._session_state = SessionState.NO_SESSION
        self._event_queue: list[FlightEvent] = []
        self._recent_events: collections.deque[FlightEvent] = collections.deque(maxlen=RECENT_EVENTS)
        self._connection_loss_logged = False
        self._twin = TwinSession()
        self._last_telemetry_time: float = 0.0
        self._metrics = _Metrics()
        self._connect_lock = asyncio.Lock()

    # -- lifecycle ----------------------------------------------------------
    async def connect(self, target=None) -> CommandResult:
        async with self._connect_lock:
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
        self._guardian.reset()
        self._mission.clear()
        telemetry = adapter.get_telemetry()
        self._twin.start(battery=telemetry.battery_percentage)
        self._log_event("connected", f"connected to {caps.name}")
        return CommandResult(command="connect", accepted=True, message=f"connected to {caps.name}")

    async def disconnect(self) -> CommandResult:
        if self._connections.adapter is None:
            return CommandResult(command="disconnect", accepted=False, message="not connected")

        await self._mission.abort("operator disconnected")

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
    async def command(self, name: str, *, source: str = SOURCE_PILOT, **kwargs) -> CommandResult:
        if name == "connect":
            return await self.connect()
        if name == "disconnect":
            return await self.disconnect()
        if name not in _ADAPTER_COMMANDS:
            result = CommandResult(command=name, accepted=False, message=f"unknown command '{name}'")
            self._record_command(result)
            return result

        if self._session_state in _TERMINAL:
            return CommandResult(command=name, accepted=False, message="flight session has ended")

        t_start = time.monotonic()
        telemetry = self.get_telemetry()
        violation = self._safety.check(name, telemetry, **kwargs)
        if violation is None:
            violation_msg = self._unsupported(name)
        else:
            violation_msg = violation.reason
        if violation_msg is not None:
            result = CommandResult(command=name, accepted=False, message=violation_msg)
            self._record_command(result)
            self._log_event("rejected", f"{name} rejected: {violation_msg}")
            return result

        if source == SOURCE_PILOT and name in _OVERRIDES_MISSION:
            await self._mission.abort(f"pilot override ({name})")

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

        cmd_time = time.monotonic() - t_start
        self._metrics.record_command_rtt(cmd_time)

        if self._session_state == SessionState.CONNECTED and name in ("arm", "takeoff"):
            self._session_state = SessionState.ACTIVE

        self._twin.record_command()
        suffix = "" if source == SOURCE_PILOT else f" ({source})"
        result = CommandResult(command=name, accepted=True, message=f"accepted{suffix}")
        self._record_command(result)
        self._log_event(name, f"{name} accepted{suffix}")
        return result

    def _unsupported(self, name: str) -> str | None:
        adapter = self._connections.adapter
        if adapter is None:
            return None
        caps = adapter.get_capabilities()
        if name == "goto" and not caps.supports_goto:
            return f"{caps.name} cannot fly to waypoints"
        if name == "return_home" and not caps.supports_return:
            return f"{caps.name} cannot return home on its own"
        return None

    # -- missions -----------------------------------------------------------
    def check_mission(self, plan: MissionPlan) -> dict:
        """Validate a plan against the geofence and the live battery level."""
        telemetry = self.get_telemetry()
        battery = telemetry.battery_percentage if telemetry.connected else None
        return check_plan(plan, self._geofence, battery_pct=battery).to_dict()

    async def start_mission(self, plan: MissionPlan) -> CommandResult:
        if self._session_state in _TERMINAL or self._connections.adapter is None:
            return self._mission_result(False, "connect to an aircraft first")
        unsupported = self._unsupported("goto")
        if unsupported:
            return self._mission_result(False, unsupported)
        report = self.check_mission(plan)
        if not report["valid"]:
            return self._mission_result(False, "; ".join(report["errors"]))
        result = await self._mission.start(plan, self.get_telemetry())
        if result.accepted and self._session_state == SessionState.CONNECTED:
            self._session_state = SessionState.ACTIVE
        self._record_command(result)
        return result

    async def pause_mission(self) -> CommandResult:
        return await self._mission.pause()

    async def resume_mission(self) -> CommandResult:
        return await self._mission.resume(self.get_telemetry())

    async def abort_mission(self) -> CommandResult:
        """Stop the mission and hold position (if airborne)."""
        if not await self._mission.abort("aborted by operator"):
            return CommandResult(command="mission_abort", accepted=False, message="no active mission")
        if self.get_telemetry().airborne:
            await self.command("hold", source=SOURCE_MISSION)
        return CommandResult(command="mission_abort", accepted=True, message="mission aborted; holding")

    def _mission_result(self, accepted: bool, message: str) -> CommandResult:
        result = CommandResult(command="mission_start", accepted=accepted, message=message)
        if not accepted:
            self._log_event("rejected", f"mission rejected: {message}")
        return result

    @property
    def mission(self) -> MissionRunner:
        return self._mission

    @property
    def guardian(self) -> FailsafeGuardian:
        return self._guardian

    # -- the periodic tick ----------------------------------------------------
    async def tick(self) -> Telemetry:
        """Sample telemetry, run failsafes, then advance any mission."""
        telemetry = self.sample()
        if self._session_state not in _TERMINAL and telemetry.connected:
            trigger = self._guardian.evaluate(telemetry)
            if trigger is not None:
                await self._run_failsafe(trigger)
        await self._mission.step(telemetry)
        return telemetry

    async def _run_failsafe(self, trigger: FailsafeTrigger) -> None:
        self._log_event("failsafe", trigger.message)
        await self._mission.abort(f"failsafe: {trigger.reason}")
        command = {
            FailsafeAction.HOLD: "hold",
            FailsafeAction.LAND: "land",
            FailsafeAction.RETURN: "return_home",
        }[trigger.action]
        if command == "return_home" and self._unsupported("return_home"):
            command = "land"  # no native return-home: landing in place is the safe fallback
        await self.command(command, source=SOURCE_FAILSAFE)

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

    def recent_events(self) -> list[FlightEvent]:
        """The latest events, oldest first (for clients that just connected)."""
        return list(self._recent_events)

    def _log_event(self, event_type: str, message: str) -> FlightEvent:
        event = FlightEvent(event_type=event_type, message=message)
        self._event_queue.append(event)
        self._recent_events.append(event)
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
