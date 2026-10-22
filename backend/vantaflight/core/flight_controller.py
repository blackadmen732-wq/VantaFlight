"""The flight core orchestrator.

`FlightController` is the one place where connection, safety, commands,
missions, failsafes, recording, and the digital twin meet. The runtime calls
`tick()` once per telemetry period (the ``flight.tick`` worker); everything
time-driven (mission progress, failsafe checks) happens inside that tick, so
there are no hidden background tasks.

The flight log, command records and failsafe triggers are published on
VantaBus reliable channels whose required sink is this controller's
database: an event is persisted (or retried) before it leaves the channel,
and a full backlog is refused loudly, never dropped silently. Execution of
autonomy setpoints requires a live VantaAuthority lease; operator overrides
and failsafes preempt automation leases.
"""
from __future__ import annotations

import asyncio
import collections
import enum
import logging
import time
import uuid

from ..connection import ConnectionManager
from ..data import FlightDatabase
from ..digital_twin import TwinSession
from ..mission import FinishAction, MissionPlan, MissionRunner, Waypoint, check_plan
from ..models import CommandResult, CommandStatus, FlightEvent, Telemetry
from ..safety import (
    FailsafeAction,
    FailsafeGuardian,
    FailsafeTrigger,
    Geofence,
    SafetyValidator,
)
from ..config import (
    AUTONOMY_MAX_SPEED_M_S,
    AUTONOMY_SETPOINT_MAX_AGE_S,
    REFERENCE_HOME_ALT_M,
    REFERENCE_HOME_LAT,
    REFERENCE_HOME_LON,
    RTL_ALTITUDE_M,
    SOFTWARE_VERSION,
)
from ..routing import Airspace, RoutePlan, RouteError, plan_route, zone_from_spec
from ..bus import BackpressureError, VantaBus
from ..bus.topics import (
    AUTHORITY_DECISION,
    EXECUTION_COMMAND,
    FLIGHT_EVENT,
    FLIGHT_TELEMETRY,
    SAFETY_FAILSAFE,
    declare_core_channels,
)
from ..contracts.envelope import EnvelopeStamper, EventEnvelope
from ..contracts.payloads import CommandRecordV1, FailsafeTriggerV1, FlightEventV1
from .authority import AuthorityDenied, AuthorityLease, AuthorityLevel, VantaAuthority
from .clock import ClockDomain, TimeStamp, VantaClock, default_clock
from .health import ComponentHealth, DimensionStatus, HealthDimension, HealthState

logger = logging.getLogger(__name__)

_DISCONNECTED = Telemetry(
    connected=False,
    battery_available=False,
    altitude_available=False,
    velocity_available=False,
    position_available=False,
)

_ADAPTER_COMMANDS = {"arm", "disarm", "takeoff", "hold", "land", "goto", "return_home"}

# Operator commands that take the aircraft away from an active mission.
_OVERRIDES_MISSION = {"disarm", "takeoff", "hold", "land", "goto", "return_home"}

# Who asked for a command. Recorded with it so a flight log shows whether the
# operator, the mission, or a failsafe moved the aircraft.
SOURCE_PILOT = "pilot"
SOURCE_MISSION = "mission"
SOURCE_FAILSAFE = "failsafe"
SOURCE_AUTONOMY = "autonomy"


class SessionState(str, enum.Enum):
    NO_SESSION = "NO_SESSION"
    CONNECTED = "CONNECTED"
    ACTIVE = "ACTIVE"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"


_TERMINAL = {SessionState.INTERRUPTED, SessionState.COMPLETED}

# Events kept in memory so a client that (re)connects sees recent history.
RECENT_EVENTS = 50
# Events kept for drain_events() callers (bounded: nobody may make it grow forever).
DRAIN_EVENTS = 1000
#: The flight database's name as a required party on the reliable channels.
DB_SINK = "flight_db"


class FlightController:
    """Coordinates connection, safety, command dispatch, and recording."""

    def __init__(
        self,
        database: FlightDatabase,
        connection_manager: ConnectionManager | None = None,
        validator: SafetyValidator | None = None,
        guardian: FailsafeGuardian | None = None,
        mission_clock=time.monotonic,
        *,
        bus: VantaBus | None = None,
        authority: VantaAuthority | None = None,
        clock: VantaClock | None = None,
    ) -> None:
        """``bus``, ``authority`` and ``clock`` come from VantaSystem in the app.

        Built on its own (tests, tools), the controller makes private ones
        on the mission clock, so it behaves the same either way.
        """
        if clock is None:
            clock = default_clock() if mission_clock is time.monotonic else VantaClock.from_seconds(mission_clock)
        self._vclock = clock
        self._bus = bus or VantaBus(clock)
        self._authority = authority or VantaAuthority(clock)
        declare_core_channels(self._bus)
        self._stamper = EnvelopeStamper("core.flight_controller", clock, self._bus.registry)
        # flight id each published record belongs to, until its sink stores it
        self._record_flight: dict[uuid.UUID, int | None] = {}
        self._bus_refusals = 0
        self._last_bus_refusal: str | None = None
        for topic, sink in (
            (FLIGHT_EVENT, self._persist_event),
            (EXECUTION_COMMAND, self._persist_command),
            (SAFETY_FAILSAFE, self._persist_envelope),
            (AUTHORITY_DECISION, self._persist_envelope),
        ):
            self._bus.channel(topic).add_sink(DB_SINK, sink)
        self._vehicle_id: str | None = None
        self._failsafe_owner: str | None = None
        self._db = database
        self._connections = connection_manager or ConnectionManager()
        self._guardian = guardian or FailsafeGuardian()
        self._geofence: Geofence = self._guardian.config.geofence
        self._safety = validator or SafetyValidator(self._geofence)
        self._airspace = Airspace(geofence=self._geofence)
        self._safety.airspace = self._airspace
        self._mission = MissionRunner(
            execute=lambda name, **kw: self.command(name, source=SOURCE_MISSION, **kw),
            on_event=self._log_event,
            clock=mission_clock,
        )
        self._flight_id: int | None = None
        self._session_state = SessionState.NO_SESSION
        self._event_queue: collections.deque[FlightEvent] = collections.deque(maxlen=DRAIN_EVENTS)
        self._recent_events: collections.deque[FlightEvent] = collections.deque(maxlen=RECENT_EVENTS)
        self._connection_loss_logged = False
        self._twin = TwinSession()
        self._last_telemetry_time: float = 0.0
        self._metrics = _Metrics()
        self._connect_lock = asyncio.Lock()
        #: The VantaRace session, attached by the app (see attach_autonomy).
        self._autonomy = None
        self._offboard_stats = {"sent": 0, "rejected": 0, "expired": 0, "unauthorized": 0}

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
            drone_id,
            caps.name,
            adapter_type=adapter_type,
            is_simulated=is_simulated,
            connection_type=transport,
            software_version=SOFTWARE_VERSION,
        )
        self._session_state = SessionState.CONNECTED
        self._connection_loss_logged = False
        self._guardian.reset()
        self._vehicle_id = drone_id
        self._failsafe_owner = None
        self._authority.register_vehicle(drone_id, simulated=bool(is_simulated))
        self._mission.clear()
        telemetry = adapter.get_telemetry()
        battery = telemetry.battery_percentage if telemetry.battery_available else 0.0
        self._twin.start(battery=battery)
        self._log_event("connected", f"connected to {caps.name}")
        return CommandResult(command="connect", accepted=True, message=f"connected to {caps.name}")

    async def disconnect(self) -> CommandResult:
        async with self._connect_lock:
            if self._connections.adapter is None:
                return CommandResult(command="disconnect", accepted=False, message="not connected")

            if self._session_state in _TERMINAL:
                await self._connections.disconnect()
                self._release_vehicle("vehicle disconnected")
                self._flight_id = None
                self._session_state = SessionState.NO_SESSION
                return CommandResult(command="disconnect", accepted=True, message="disconnected (session already ended)")

            await self._mission.abort("operator disconnected")
            await self._abort_autonomy("operator disconnected")
            await self._connections.disconnect()
            self._release_vehicle("vehicle disconnected")
            self._log_event("disconnected", "disconnected from drone")
            self._end_flight("completed")
            return CommandResult(command="disconnect", accepted=True, message="disconnected")

    async def shutdown(self, reason: str = "backend shutting down") -> None:
        """Stop execution first, then close the vehicle link (VantaRuntime shutdown order)."""
        async with self._connect_lock:
            await self._mission.abort(reason)
            await self._abort_autonomy(reason)
            adapter = self._connections.adapter
            if adapter is None:
                return
            telemetry = adapter.get_telemetry()
            if self._session_state not in _TERMINAL:
                self._log_event("shutdown", f"{reason}; closing the vehicle link")
            await self._connections.disconnect()
            self._release_vehicle(reason)
            self._end_flight("interrupted" if telemetry.armed else "completed")

    def _release_vehicle(self, reason: str) -> None:
        if self._vehicle_id is not None:
            self._authority.unregister_vehicle(self._vehicle_id, reason)
        self._vehicle_id = None
        self._failsafe_owner = None

    # -- commands -----------------------------------------------------------
    async def command(self, name: str, *, source: str = SOURCE_PILOT, **kwargs) -> CommandResult:
        if name == "connect":
            return await self.connect()
        if name == "disconnect":
            return await self.disconnect()
        if name not in _ADAPTER_COMMANDS:
            result = CommandResult(command=name, accepted=False, message=f"unknown command '{name}'")
            self._record_command(result, source)
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
            result = CommandResult.rejected(name, violation_msg)
            self._record_command(result, source)
            self._log_event("rejected", f"{name} rejected: {violation_msg}")
            return result

        if source == SOURCE_PILOT and name in _OVERRIDES_MISSION:
            # The operator outranks every automation lease, effective now:
            # a setpoint already in flight is refused by the lease check.
            if self._vehicle_id is not None:
                self._authority.preempt(
                    AuthorityLevel.OPERATOR, "operator", self._vehicle_id, reason=f"pilot override ({name})"
                )
            await self._mission.abort(f"pilot override ({name})")
            await self._abort_autonomy(f"pilot override ({name})")

        adapter = self._connections.adapter
        assert adapter is not None
        try:
            method = getattr(adapter, name)
            adapter_result = await method(**kwargs)
        except Exception as exc:
            # The adapter raised, so we cannot claim the command left this
            # process: never let it default to SENT in the audit trail.
            result = CommandResult(
                command=name,
                accepted=False,
                status=CommandStatus.INTERNAL_ERROR,
                message=f"drone error: {exc}",
            )
            self._record_command(result, source)
            self._log_event("error", f"{name} failed: {exc}")
            return result

        cmd_time = time.monotonic() - t_start
        self._metrics.record_command_rtt(cmd_time)

        # Newer physical adapters return a typed transport outcome. Preserve it
        # exactly so a command that never left this process can never be turned
        # into a generic "accepted" success by FlightController.
        if isinstance(adapter_result, CommandResult):
            result = adapter_result
            if not result.accepted or not result.status.transmitted:
                self._record_command(result, source)
                event = "rejected" if not result.accepted else "not_sent"
                self._log_event(event, f"{name}: {result.message or result.status.value}")
                return result
        else:
            # Legacy Mock/PX4 adapters complete the method only after dispatch.
            result = CommandResult.ok(name, "accepted")

        if self._session_state == SessionState.CONNECTED and name in ("arm", "takeoff"):
            self._session_state = SessionState.ACTIVE

        self._twin.record_command()
        suffix = "" if source == SOURCE_PILOT else f" ({source})"
        if suffix:
            result = result.model_copy(update={"message": f"{result.message}{suffix}"})
        self._record_command(result, source)
        self._log_event(name, f"{name} {result.status.value.lower()}{suffix}")
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

    # -- airspace & routing --------------------------------------------------
    @property
    def airspace(self) -> Airspace:
        return self._airspace

    def set_airspace(self, zone_specs: list[dict], margin_m: float | None = None) -> Airspace:
        """Replace the no-fly zones. Raises ValueError for an invalid zone."""
        zones = [zone_from_spec(spec, i) for i, spec in enumerate(zone_specs)]
        for zone in zones:
            if zone.contains((0.0, 0.0)):
                raise ValueError(f"zone '{zone.name}' covers home; the aircraft could not take off or land")
        margin = self._airspace.margin_m if margin_m is None else margin_m
        self._airspace = Airspace(zones, margin, self._geofence)
        self._safety.airspace = self._airspace
        self._log_event("airspace", f"airspace updated: {len(zones)} no-fly zone(s), {margin:g} m margin")
        return self._airspace

    def plan_route(
        self,
        stops: list[Waypoint],
        *,
        optimize_order: bool = True,
        finish: FinishAction = FinishAction.RETURN_HOME,
        speed_m_s: float | None = None,
        name: str = "Optimized route",
    ) -> RoutePlan:
        """Best safe route through ``stops`` with a battery budget (live level if connected)."""
        telemetry = self.get_telemetry()
        battery = (
            telemetry.battery_percentage
            if telemetry.connected and telemetry.battery_available
            else None
        )
        kwargs = {"speed_m_s": speed_m_s} if speed_m_s else {}
        return plan_route(
            stops, self._airspace, optimize_order=optimize_order, finish=finish,
            battery_pct=battery, name=name, **kwargs,
        )

    def home_reference(self) -> tuple[float, float, float]:
        """Home as (lat, lon, alt AMSL): the aircraft's own, else the configured one."""
        adapter = self._connections.adapter
        home_position = getattr(adapter, "home_position", None)
        home = home_position() if callable(home_position) else None
        return home or (REFERENCE_HOME_LAT, REFERENCE_HOME_LON, REFERENCE_HOME_ALT_M)

    async def return_home(self, source: str = SOURCE_PILOT) -> CommandResult:
        """Fly home and land, routing around no-fly zones when needed.

        A native return-to-launch flies a straight line. When that line would
        enter a no-fly zone, we fly the shortest safe path home as a short
        mission instead, and land at home.
        """
        t = self.get_telemetry()
        here = (t.x, t.y)
        if (
            not self._airspace.zones
            or not t.airborne
            or self._unsupported("goto")
            or self._airspace.blocking_zone(here, (0.0, 0.0), margin=0.0) is None
        ):
            return await self.command("return_home", source=source)

        try:
            try:
                path = self._airspace.shortest_path(here, (0.0, 0.0))
            except RouteError:
                # Already inside a zone's planning margin: keep out of the zone itself.
                path = self._airspace.with_margin(0.0).shortest_path(here, (0.0, 0.0))
        except RouteError as exc:
            self._log_event("error", f"no safe route home ({exc}); holding position")
            return await self.command("hold", source=source)

        altitude = max(t.altitude, RTL_ALTITUDE_M)
        plan = MissionPlan(
            name="Safe return home",
            waypoints=[
                *(Waypoint(x=x, y=y, altitude=altitude, kind="via") for x, y in path.detours),
                Waypoint(x=0.0, y=0.0, altitude=altitude, kind="via"),
            ],
            finish=FinishAction.LAND,
        )
        reason = "pilot override (return home)" if source == SOURCE_PILOT else f"{source}: return home"
        await self._mission.abort(reason)
        started = await self._mission.start(plan, t)
        if not started.accepted:
            return CommandResult(command="return_home", accepted=False, message=started.message)
        self._log_event(
            "return_home",
            f"returning home around no-fly zones via {len(path.detours)} detour point(s) ({source})",
        )
        return CommandResult(command="return_home", accepted=True, message="returning home on a safe route")

    # -- missions -----------------------------------------------------------
    def check_mission(self, plan: MissionPlan) -> dict:
        """Validate a plan against the geofence, airspace and live battery level."""
        telemetry = self.get_telemetry()
        battery = (
            telemetry.battery_percentage
            if telemetry.connected and telemetry.battery_available
            else None
        )
        return check_plan(plan, self._geofence, battery_pct=battery, airspace=self._airspace).to_dict()

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
        self._record_command(result, SOURCE_PILOT)
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
        if self._failsafe_owner is not None and telemetry.connected and not telemetry.armed:
            # Down and disarmed: the failsafe has done its job; automation may
            # be approved again.
            self._authority.release_owner(self._failsafe_owner, "failsafe complete: aircraft disarmed")
            self._failsafe_owner = None
        await self._mission.step(telemetry)
        try:
            self._bus.publish(FLIGHT_TELEMETRY, telemetry)
        except Exception:  # a UI-facing snapshot must never break the tick
            logger.exception("could not publish the telemetry snapshot")
        return telemetry

    async def _run_failsafe(self, trigger: FailsafeTrigger) -> None:
        self._log_event("failsafe", trigger.message)
        self._publish(FailsafeTriggerV1.from_trigger(trigger))
        if self._vehicle_id is not None:
            # Failsafe outranks everything and keeps a lock-out until the
            # aircraft is down, so automation cannot resume mid-recovery.
            owner = f"failsafe:{trigger.reason}"
            self._authority.preempt(
                AuthorityLevel.FAILSAFE, owner, self._vehicle_id, reason=trigger.message, hold=True
            )
            self._failsafe_owner = owner
        await self._mission.abort(f"failsafe: {trigger.reason}")
        await self._abort_autonomy(f"failsafe: {trigger.reason}")
        command = {
            FailsafeAction.HOLD: "hold",
            FailsafeAction.LAND: "land",
            FailsafeAction.RETURN: "return_home",
        }[trigger.action]
        if command == "return_home" and self._unsupported("return_home"):
            command = "land"  # no native return-home: landing in place is the safe fallback
        if command == "return_home":
            await self.return_home(source=SOURCE_FAILSAFE)
        else:
            await self.command(command, source=SOURCE_FAILSAFE)

    # -- autonomy (VantaRace offboard) -----------------------------------------
    def attach_autonomy(self, session) -> None:
        """Register the race session so overrides and failsafes can stop it."""
        self._autonomy = session

    @property
    def autonomy(self):
        return self._autonomy

    @property
    def authority(self) -> VantaAuthority:
        return self._authority

    @property
    def bus(self) -> VantaBus:
        return self._bus

    @property
    def vehicle_id(self) -> str | None:
        """The connected vehicle's id (what authority leases name)."""
        return self._vehicle_id

    def _authority_problem(self, lease: AuthorityLease | None, scope: str) -> str | None:
        if self._vehicle_id is None:
            return "no vehicle connected"
        reason = self._authority.check(lease, scope, vehicle_id=self._vehicle_id)
        return None if reason is None else f"authority: {reason}"

    async def _abort_autonomy(self, reason: str) -> None:
        session = self._autonomy
        if session is not None and getattr(session, "active", False):
            await session.abort(reason, command_vehicle=False)

    @property
    def adapter(self):
        return self._connections.adapter

    @property
    def active_target(self):
        return self._connections.active

    @property
    def geofence(self) -> Geofence:
        return self._geofence

    def log_event(self, event_type: str, message: str) -> FlightEvent:
        return self._log_event(event_type, message)

    def record_autonomy_samples(self, samples: list[tuple[float, str, dict]]) -> None:
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            self._db.record_autonomy_samples(self._flight_id, samples)

    def _offboard_capable(self) -> str | None:
        adapter = self._connections.adapter
        if adapter is None:
            return "not connected"
        caps = adapter.get_capabilities()
        if "offboard" not in caps.supported_capabilities or not all(
            callable(getattr(adapter, m, None))
            for m in ("start_offboard", "stop_offboard", "set_position_velocity_ned", "local_state")
        ):
            return f"{caps.name} does not support offboard control"
        return None

    async def start_offboard(self, north_m: float, east_m: float, down_m: float, yaw_deg: float,
                             *, source: str = SOURCE_AUTONOMY,
                             lease: AuthorityLease | None = None) -> CommandResult:
        """Enter offboard holding the given NED point (the current position).

        Needs a live authority lease covering ``offboard``.
        """
        name = "offboard_start"
        if self._session_state in _TERMINAL:
            return CommandResult.rejected(name, "flight session has ended")
        problem = self._offboard_capable()
        if problem is None:
            problem = self._authority_problem(lease, "offboard")
        t = self.get_telemetry()
        if problem is None and not (t.connected and t.armed and t.airborne):
            problem = "offboard needs a connected, armed, airborne aircraft"
        if problem is not None:
            result = CommandResult.rejected(name, problem)
            self._record_command(result, source)
            self._log_event("rejected", f"{name} rejected: {problem}")
            return result
        try:
            await self._connections.adapter.start_offboard(north_m, east_m, down_m, yaw_deg)
        except Exception as exc:
            result = CommandResult(
                command=name, accepted=False, status=CommandStatus.INTERNAL_ERROR,
                message=f"drone error: {exc}",
            )
            self._record_command(result, source)
            self._log_event("error", f"{name} failed: {exc}")
            return result
        result = CommandResult.ok(name, f"accepted ({source})")
        self._record_command(result, source)
        self._log_event(name, f"offboard control started ({source})")
        return result

    async def stop_offboard(self, *, source: str = SOURCE_AUTONOMY) -> CommandResult:
        name = "offboard_stop"
        adapter = self._connections.adapter
        if adapter is None or not callable(getattr(adapter, "stop_offboard", None)):
            return CommandResult.rejected(name, "not connected")
        try:
            await adapter.stop_offboard()
        except Exception as exc:
            result = CommandResult(
                command=name, accepted=False, status=CommandStatus.INTERNAL_ERROR,
                message=f"drone error: {exc}",
            )
            self._record_command(result, source)
            self._log_event("error", f"{name} failed: {exc}")
            return result
        result = CommandResult.ok(name, f"accepted ({source})")
        self._record_command(result, source)
        self._log_event(name, f"offboard control stopped ({source})")
        return result

    async def offboard_setpoint(self, setpoint, *, now: float, lease: AuthorityLease | None = None) -> CommandResult:
        """Send one VantaExecution setpoint, after the flight core's own checks.

        This is the only path from the autonomy stack to the vehicle. It
        refuses setpoints without a live ``trajectory`` authority lease, and
        setpoints that are expired, too fast, outside the geofence or inside
        a no-fly zone, or sent while the aircraft is not airborne. Accepted
        setpoints are counted, not logged one by one (they stream at 20 Hz);
        the race session records sampled copies for replay.
        """
        name = "offboard_setpoint"
        problem = None
        unauthorized = False
        if self._session_state in _TERMINAL:
            problem = "flight session has ended"
        else:
            problem = self._offboard_capable()
        if problem is None:
            problem = self._authority_problem(lease, "trajectory")
            unauthorized = problem is not None
        if problem is None:
            t = self.get_telemetry()
            if not (t.connected and t.armed and t.airborne):
                problem = "aircraft is not armed and airborne"
        expired = False
        if problem is None:
            age = float(now) - float(setpoint.timestamp)
            if age > AUTONOMY_SETPOINT_MAX_AGE_S:
                expired = True
                problem = f"setpoint expired ({age * 1000:.0f} ms old, limit {AUTONOMY_SETPOINT_MAX_AGE_S * 1000:.0f} ms)"
        if problem is None:
            speed = float((setpoint.velocity_ned[0] ** 2 + setpoint.velocity_ned[1] ** 2 + setpoint.velocity_ned[2] ** 2) ** 0.5)
            if speed > AUTONOMY_MAX_SPEED_M_S + 1e-6:
                problem = f"commanded speed {speed:.1f} m/s exceeds the {AUTONOMY_MAX_SPEED_M_S:g} m/s autonomy limit"
        if problem is None:
            north, east, down = (float(v) for v in setpoint.position_ned)
            breach = self._geofence.violation(east, north, -down)
            if breach:
                problem = f"setpoint outside geofence: {breach}"
            else:
                for zone in getattr(self._airspace, "zones", ()):
                    if zone.contains((east, north)):
                        problem = f"setpoint inside no-fly zone '{zone.name}'"
                        break
        if problem is not None:
            key = "unauthorized" if unauthorized else ("expired" if expired else "rejected")
            self._offboard_stats[key] += 1
            return CommandResult.rejected(name, problem)
        try:
            await self._connections.adapter.set_position_velocity_ned(
                tuple(float(v) for v in setpoint.position_ned),
                tuple(float(v) for v in setpoint.velocity_ned),
                float(setpoint.yaw_deg),
            )
        except Exception as exc:
            self._offboard_stats["rejected"] += 1
            return CommandResult(
                command=name, accepted=False, status=CommandStatus.INTERNAL_ERROR,
                message=f"drone error: {exc}",
            )
        self._offboard_stats["sent"] += 1
        return CommandResult.ok(name, "sent")

    @property
    def offboard_stats(self) -> dict:
        return dict(self._offboard_stats)

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
    def metrics(self) -> "_Metrics":
        return self._metrics

    def drain_events(self) -> list[FlightEvent]:
        events = list(self._event_queue)
        self._event_queue.clear()
        return events

    def recent_events(self) -> list[FlightEvent]:
        """The latest events, oldest first (for clients that just connected)."""
        return list(self._recent_events)

    def _log_event(self, event_type: str, message: str) -> FlightEvent:
        event = FlightEvent(event_type=event_type, message=message)
        self._event_queue.append(event)
        self._recent_events.append(event)
        self._publish(FlightEventV1.from_event(event), utc_s=event.timestamp)
        return event

    def _record_command(self, result: CommandResult, source: str = "flight_core") -> None:
        self._publish(CommandRecordV1.from_result(result, source), utc_s=result.timestamp)

    # -- reliable records ---------------------------------------------------------
    def _recording_flight(self) -> int | None:
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            return self._flight_id
        return None

    def _publish(self, payload, *, utc_s: float | None = None) -> EventEnvelope | None:
        """Stamp and publish a record; its flight id is fixed now, not at retry time."""
        captured = TimeStamp.from_seconds(utc_s, ClockDomain.UTC_WALL) if utc_s is not None else None
        envelope = self._stamper.stamp(payload, captured_at=captured)
        self._record_flight[envelope.event_id] = self._recording_flight()
        try:
            self._bus.publish(envelope)
        except BackpressureError as exc:
            # Refused, not dropped silently: counted, logged, visible in health.
            self._record_flight.pop(envelope.event_id, None)
            self._bus_refusals += 1
            self._last_bus_refusal = str(exc)
            logger.critical("flight record refused: %s", exc)
            return None
        return envelope

    def _record_utc(self, envelope: EventEnvelope) -> float:
        stamp = envelope.captured_at
        return stamp.seconds if stamp.domain is ClockDomain.UTC_WALL else self._vclock.wall_s()

    def _sink_flight(self, envelope: EventEnvelope) -> int | None:
        if envelope.event_id in self._record_flight:
            return self._record_flight[envelope.event_id]
        # Published by another component (authority): the flight in progress now.
        flight = self._recording_flight()
        self._record_flight[envelope.event_id] = flight
        return flight

    def _persist_event(self, envelope: EventEnvelope) -> None:
        flight = self._sink_flight(envelope)
        if flight is not None:
            p = envelope.payload
            self._db.record_event(
                flight, FlightEvent(timestamp=self._record_utc(envelope), event_type=p.event_type, message=p.message)
            )
        self._record_flight.pop(envelope.event_id, None)

    def _persist_command(self, envelope: EventEnvelope) -> None:
        flight = self._sink_flight(envelope)
        if flight is not None:
            p = envelope.payload
            self._db.record_command(
                flight,
                CommandResult(
                    command=p.command, accepted=p.accepted, status=CommandStatus(p.status),
                    message=p.message, timestamp=self._record_utc(envelope),
                ),
            )
        self._record_flight.pop(envelope.event_id, None)

    def _persist_envelope(self, envelope: EventEnvelope) -> None:
        """Typed records with no table of their own go to the contract record store."""
        flight = self._sink_flight(envelope)
        if flight is not None:
            self._db.record_autonomy_samples(
                flight, [(self._record_utc(envelope), envelope.topic, self._bus.registry.encode(envelope))]
            )
        self._record_flight.pop(envelope.event_id, None)

    # -- health ----------------------------------------------------------------------
    def health(self, now_s: float | None = None) -> list[ComponentHealth]:
        """Link liveness, data freshness and sensor availability, reported separately."""
        adapter = self._connections.adapter
        stale_s = self._guardian.config.link_stale_s
        if adapter is None:
            idle = DimensionStatus(HealthState.STOPPED, "no vehicle connected")
            return [ComponentHealth("vehicle.link", "vehicle", {HealthDimension.LIVENESS: idle})]
        t = adapter.get_telemetry()
        if t.connected:
            liveness = DimensionStatus(HealthState.HEALTHY, f"connected ({_value(t.connection_quality)})")
        elif self._session_state in _TERMINAL:
            liveness = DimensionStatus(HealthState.FAILED, "link lost; flight session interrupted")
        else:
            liveness = DimensionStatus(HealthState.FAILED, "not connected")
        age = float(t.link_age_s)
        if not t.connected:
            freshness = DimensionStatus(HealthState.UNKNOWN, "no link", age)
        elif age <= min(0.5, stale_s):
            freshness = DimensionStatus(HealthState.HEALTHY, f"telemetry {age * 1000:.0f} ms old", age)
        elif age <= stale_s:
            freshness = DimensionStatus(HealthState.DEGRADED, f"telemetry {age:.2f} s old", age)
        else:
            freshness = DimensionStatus(HealthState.FAILED, f"telemetry {age:.1f} s old (stale)", age)
        components = [
            ComponentHealth("vehicle.link", "vehicle", {
                HealthDimension.LIVENESS: liveness, HealthDimension.FRESHNESS: freshness,
            }),
        ]
        cfg = self._guardian.config
        if t.battery_available:
            pct = float(t.battery_percentage)
            state = (HealthState.FAILED if pct <= cfg.battery_critical_pct
                     else HealthState.DEGRADED if pct <= cfg.battery_low_pct else HealthState.HEALTHY)
            battery = DimensionStatus(state, f"{pct:.0f}%", pct)
        else:
            battery = DimensionStatus(HealthState.UNKNOWN, "this link does not report battery")
        components.append(ComponentHealth("vehicle.battery", "vehicle", {HealthDimension.CORRECTNESS: battery}))
        position = (
            DimensionStatus(HealthState.HEALTHY, "position reported")
            if t.position_available else DimensionStatus(HealthState.UNKNOWN, "this link does not report position")
        )
        components.append(ComponentHealth("vehicle.position", "vehicle", {HealthDimension.CORRECTNESS: position}))
        recording = (
            DimensionStatus(HealthState.FAILED, f"{self._bus_refusals} record(s) refused: {self._last_bus_refusal}")
            if self._bus_refusals else DimensionStatus(HealthState.HEALTHY, "flight log persisting")
        )
        components.append(ComponentHealth("flight.recording", "service", {HealthDimension.CORRECTNESS: recording}))
        return components

    def _end_flight(self, status: str) -> None:
        if self._flight_id is not None and self._session_state not in _TERMINAL:
            self._db.end_flight(self._flight_id, status)
            self._twin.end(status)
            if status == "interrupted":
                self._session_state = SessionState.INTERRUPTED
            else:
                self._session_state = SessionState.COMPLETED
                self._flight_id = None


def _value(v) -> str:
    return str(getattr(v, "value", v))


def event_from_envelope(envelope: EventEnvelope) -> FlightEvent:
    """The legacy ``FlightEvent`` shape (what the UI renders) of a flight.event envelope."""
    stamp = envelope.captured_at
    timestamp = stamp.seconds if stamp.domain is ClockDomain.UTC_WALL else time.time()
    p = envelope.payload
    return FlightEvent(timestamp=timestamp, event_type=p.event_type, message=p.message)


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
