"""RaceSession — the VantaRace closed loop, launchable from the app.

One tick (20 Hz by default):

    vehicle local state (PX4 EKF in SITL)
      → VantaSight (synthetic camera from that pose → detection → PnP → fusion)
      → VantaState (gate tracks with ageing confidence)
      → VantaRace (trajectory through the visible gates)
      → VantaExecution (confidence floor, hold logic, ENU → NED)
      → FlightController.offboard_setpoint (expiry, speed, geofence, no-fly)
      → adapter (PX4 offboard position+velocity setpoint)

Lifecycle::

    IDLE → PREPARING (arm, take off to the course altitude)
         → RUNNING ⇄ PAUSED            (operator)
           RUNNING ⇄ RECOVERING         (perception lost/low confidence: hold)
           RUNNING ⇄ HOLDING_STALE      (vehicle state stale: hold last point)
         → FINISHING (leave offboard, run the finish action) → COMPLETE
    any active state → ABORTED (operator, pilot override, failsafe, timeout,
                                link loss, offboard lost) / FAILED (planner)

Simulation only: the session requires a `SimulationExecutionPermit`, which the
guard grants only to simulated adapters on local SITL endpoints (or the
in-process mock).
"""
from __future__ import annotations

import asyncio
import collections
import enum
import logging
import math
import time
from dataclasses import dataclass, field

import numpy as np

from ..config import AUTONOMY_LOOP_HZ, AUTONOMY_MAX_SPEED_M_S
from ..contracts import EnvelopeStamper, Quality, QualityFlag
from ..contracts.payloads import (
    DesiredStateV1,
    ExecutionSampleV1,
    PerceptionFrameV1,
    PlannerSampleV1,
    RaceEventV1,
    RaceSampleV1,
    RaceSummaryV1,
    VehicleStateV1,
)
from ..core.authority import AuthorityDenied, AuthorityLevel, LeaseInvalid
from ..core.clock import VantaClock, default_clock
from ..core.runtime import Criticality, VantaRuntime, default_runtime
from ..frames import enu_to_ned, enu_yaw_deg_to_ned
from ..racing.execution import AutonomousExecutionRejected, SimulationOnlyExecutionGuard
from ..racing.planner import VantaRaceConfig
from ..racing.speed import SpeedEnvelopeConfig
from ..racing.vanta_execution import ExecutionMode, OffboardSetpoint
from ..simulation.models import GazeboGate
from ..simulation.truth import AircraftTruth
from .courses import RaceCourse, check_course
from .loop import AutonomyConfig, AutonomyLoop, AutonomyState
from .perception import GatePerception, PerceptionMode, PerceptionResult, PerceptionStatus

logger = logging.getLogger(__name__)

SOURCE = "autonomy"
#: The race renews its authority lease every tick; if the loop stalls this
#: long the lease lapses and the flight core refuses its setpoints.
LEASE_TTL_S = 2.0
LEASE_SCOPE = frozenset({"trajectory", "offboard"})


class RaceState(str, enum.Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    HOLDING_STALE = "HOLDING_STALE"
    PAUSED = "PAUSED"
    FINISHING = "FINISHING"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


ACTIVE_STATES = {
    RaceState.PREPARING,
    RaceState.RUNNING,
    RaceState.RECOVERING,
    RaceState.HOLDING_STALE,
    RaceState.PAUSED,
    RaceState.FINISHING,
}
_FLYING = {RaceState.RUNNING, RaceState.RECOVERING, RaceState.HOLDING_STALE, RaceState.PAUSED}


class FinishAction(str, enum.Enum):
    LAND = "land"
    RETURN_HOME = "return_home"
    HOLD = "hold"


@dataclass
class RaceConfig:
    perception: PerceptionMode = PerceptionMode.VISION
    speed_m_s: float = 3.0
    aggression: float = 0.35
    finish: FinishAction = FinishAction.LAND
    loop_hz: float = AUTONOMY_LOOP_HZ
    #: Vehicle state older than this: stop flying the plan and hold.
    telemetry_stale_s: float = 0.5
    #: Lost the current gate this long: yaw toward where it should be.
    search_after_s: float = 2.0
    #: Lost the current gate this long: give up and hold.
    max_lost_s: float = 20.0
    takeoff_timeout_s: float = 90.0
    #: Further than this from the start line when starting: fly back first.
    reposition_radius_m: float = 2.0
    #: Facing within this of the first gate before the loop takes over.
    align_tolerance_deg: float = 12.0
    #: Offboard reported inactive this long while flying: abort.
    offboard_grace_s: float = 1.0
    record_hz: float = 5.0
    vision_hz: float = 15.0

    def __post_init__(self) -> None:
        self.perception = PerceptionMode(self.perception)
        self.finish = FinishAction(self.finish)
        if not 0.5 <= self.speed_m_s <= AUTONOMY_MAX_SPEED_M_S:
            raise ValueError(f"speed must be between 0.5 and {AUTONOMY_MAX_SPEED_M_S:g} m/s")
        if not 0.0 <= self.aggression <= 1.0:
            raise ValueError("aggression must be between 0 and 1")
        if self.loop_hz < 5.0:
            raise ValueError("loop rate must be at least 5 Hz (PX4 offboard needs > 2 Hz)")

    def planner_config(self) -> VantaRaceConfig:
        # Conservative envelope for the first SITL milestone: the planner can
        # never ask for more than speed_m_s, and accelerates/brakes gently.
        return VantaRaceConfig(
            aggression=self.aggression,
            nominal_speed=self.speed_m_s,
            prediction_grace_s=0.5,
            command_lookahead_s=0.25,
            speed=SpeedEnvelopeConfig(
                min_speed=0.5,
                max_speed=self.speed_m_s,
                max_lateral_acceleration=3.0,
                max_forward_acceleration=2.0,
                max_braking_acceleration=3.0,
                max_vertical_speed=1.5,
                clearance_for_full_speed=1.0,
            ),
        )

    def to_dict(self) -> dict:
        return {
            "perception": self.perception.value,
            "speed_m_s": self.speed_m_s,
            "aggression": self.aggression,
            "finish": self.finish.value,
            "loop_hz": self.loop_hz,
            "telemetry_stale_s": self.telemetry_stale_s,
        }


def gate_crossing(gate: GazeboGate, prev: np.ndarray, cur: np.ndarray, margin: float = 0.3) -> str | None:
    """'passed' through the opening, 'missed' the plane outside it, or None."""
    normal = gate.normal / max(float(np.linalg.norm(gate.normal)), 1e-12)
    d_prev = float(np.dot(prev - gate.position, normal))
    d_cur = float(np.dot(cur - gate.position, normal))
    if not (d_prev <= 0.0 < d_cur):
        return None
    t = -d_prev / max(d_cur - d_prev, 1e-9)
    point = prev + t * (cur - prev)
    offset = point - gate.position
    in_plane = offset - float(np.dot(offset, normal)) * normal
    right = np.cross(normal, np.array([0.0, 0.0, 1.0]))
    right = right / np.linalg.norm(right) if np.linalg.norm(right) > 1e-9 else np.array([1.0, 0.0, 0.0])
    up = np.cross(right, normal)
    lateral = abs(float(np.dot(in_plane, right)))
    vertical = abs(float(np.dot(in_plane, up)))
    if lateral <= gate.width / 2.0 - margin and vertical <= gate.height / 2.0 - margin:
        return "passed"
    return "missed"


@dataclass
class _Faults:
    setpoint_delay_s: float = 0.0
    setpoint_delay_until: float = 0.0
    state_stall_until: float = 0.0
    frozen_state: object = None


@dataclass
class RaceStats:
    ticks: int = 0
    setpoints_sent: int = 0
    setpoints_rejected: int = 0
    setpoints_expired: int = 0
    hold_setpoints: int = 0
    recoveries: int = 0
    reacquisitions: int = 0
    stale_telemetry_events: int = 0
    gates_missed: int = 0
    max_tick_ms: float = 0.0
    tick_ms_total: float = 0.0
    max_tracking_error_m: float = 0.0
    gate_pass_offsets_m: list[float] = field(default_factory=list)
    #: Records that could not be built or validated (never stops the race).
    record_errors: int = 0

    def to_dict(self) -> dict:
        return {
            "ticks": self.ticks,
            "setpoints_sent": self.setpoints_sent,
            "setpoints_rejected": self.setpoints_rejected,
            "setpoints_expired": self.setpoints_expired,
            "hold_setpoints": self.hold_setpoints,
            "recoveries": self.recoveries,
            "reacquisitions": self.reacquisitions,
            "stale_telemetry_events": self.stale_telemetry_events,
            "gates_missed": self.gates_missed,
            "avg_tick_ms": round(self.tick_ms_total / self.ticks, 2) if self.ticks else 0.0,
            "max_tick_ms": round(self.max_tick_ms, 2),
            "max_tracking_error_m": round(self.max_tracking_error_m, 2),
            "gate_pass_offsets_m": [round(v, 2) for v in self.gate_pass_offsets_m],
            "record_errors": self.record_errors,
        }


class RaceSession:
    """Owns one race attempt at a time. Reusable across attempts."""

    SOURCE_ID = "autonomy.race_session"

    def __init__(
        self, controller, *, clock=None, run_task: bool = True, ids=None, runtime: VantaRuntime | None = None
    ) -> None:
        """``clock`` is a VantaClock or a legacy float-seconds callable.

        ``ids`` (e.g. ``SequentialIds(seed)``) makes recorded envelopes
        reproducible run to run; the default is random UUIDs.
        """
        self._controller = controller
        if clock is None or clock is time.monotonic:
            self._vclock = default_clock()
        elif isinstance(clock, VantaClock):
            self._vclock = clock
        else:
            self._vclock = VantaClock.from_seconds(clock)
        # Callable: returns host monotonic seconds, like the old float clocks.
        self._clock = self._vclock
        self._run_task = run_task
        self._runtime = runtime
        self._worker = None  # the race.loop runtime worker
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        stamper_kwargs = {"ids": ids} if ids is not None else {}
        self._stamper = EnvelopeStamper(self.SOURCE_ID, self._vclock, **stamper_kwargs)
        self._reset()

    def _reset(self) -> None:
        # Every race attempt is its own run in the recorded envelopes.
        self._stamper.new_run()
        self.state = RaceState.IDLE
        self.course: RaceCourse | None = None
        self.config = RaceConfig()
        self.message = ""
        self._perception: GatePerception | None = None
        self._loop: AutonomyLoop | None = None
        self._permit = None
        self._remaining: list[str] = []
        self._gates: dict[str, GazeboGate] = {}
        self._prev_pos: np.ndarray | None = None
        self._started_at = 0.0
        self._race_started_at = 0.0
        self._finished_at = 0.0
        self._prepare_deadline = 0.0
        self._prep_phase = "climb"
        self._lost_since: float | None = None
        self._offboard_lost_since: float | None = None
        self._last_record = -math.inf
        self._record_buffer: list[tuple[float, str, dict]] = []
        self._faults = _Faults()
        self._delayed: collections.deque[tuple[float, OffboardSetpoint]] = collections.deque()
        self._pending: OffboardSetpoint | None = None
        self._last_perception: PerceptionResult | None = None
        self._last_setpoint: OffboardSetpoint | None = None
        self._last_local = None
        self._was_recovering = False
        self._was_stale = False
        self._expiry_logged = False
        self._passes: list[dict] = []
        self.stats = RaceStats()
        self._lease = None

    # -- queries --------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def loop(self) -> AutonomyLoop | None:
        return self._loop

    @property
    def lease(self):
        """The authority lease the race holds while active (None otherwise)."""
        return self._lease

    # -- operator actions -----------------------------------------------------
    async def start(self, course: RaceCourse, config: RaceConfig | None = None) -> dict:
        async with self._lock:
            if self.active:
                return self._result(False, f"a race is already {self.state.value.lower()}")
            config = config or RaceConfig()
            controller = self._controller
            adapter = controller.adapter
            if adapter is None:
                return self._result(False, "connect to a simulator first")
            problem = controller._offboard_capable()
            if problem:
                return self._result(False, problem)
            caps = adapter.get_capabilities()
            target = controller.active_target
            endpoint = getattr(target, "address", "") or ""
            try:
                permit = SimulationOnlyExecutionGuard.authorize(endpoint, caps)
            except AutonomousExecutionRejected as exc:
                return self._result(False, f"autonomy refused: {exc}")
            report = check_course(course, controller.geofence, controller.airspace)
            if not report.valid:
                return self._result(False, "course check failed: " + "; ".join(report.errors))
            telemetry = controller.get_telemetry()
            if not telemetry.connected:
                return self._result(False, "aircraft is not connected")
            if controller.mission.active:
                return self._result(False, "a mission is running; finish or abort it first")
            low = controller.guardian.config.battery_low_pct
            if telemetry.battery_available and telemetry.battery_percentage <= low + 5.0:
                return self._result(
                    False, f"battery {telemetry.battery_percentage:.0f}% is too low to start a race"
                )
            vehicle_id = controller.vehicle_id
            if vehicle_id is None:
                return self._result(False, "aircraft is not connected")
            # The operator starting the race approves this automation; it
            # still ranks below the operator and every failsafe.
            try:
                lease = controller.authority.acquire(
                    f"vantarace:{course.course_id}", AuthorityLevel.APPROVED_MISSION_AUTOMATION,
                    LEASE_SCOPE, vehicle_id, ttl_s=LEASE_TTL_S, approved_by="operator",
                )
            except AuthorityDenied as exc:
                return self._result(False, f"authority refused: {exc.reason}")

            runtime = self._runtime or default_runtime()
            if self._worker is not None and self._worker.alive:
                await runtime.stop_worker(self._worker.name)  # a previous attempt's loop
            self._reset()
            self._lease = lease
            self.course = course
            self.config = config
            self._permit = permit
            self._gates = {g.gate_id: g for g in course.ordered_gates}
            self._remaining = [g.gate_id for g in course.ordered_gates]
            self._perception = GatePerception(
                course, config.perception, clock=self._clock, vision_hz=config.vision_hz,
            )
            self._loop = AutonomyLoop(
                AutonomyConfig(loop_rate_hz=config.loop_hz, race_config=config.planner_config()),
                clock=self._clock,
            )
            self._loop.on_setpoint(self._capture_setpoint)
            self._started_at = self._clock()
            self._event(
                "race_started",
                f"VantaRace started on '{course.name}' ({len(course.gates)} gates, "
                f"{config.perception.value} perception, {config.speed_m_s:g} m/s)",
            )

            # Get airborne at the course altitude first, through the normal path.
            self.state = RaceState.PREPARING
            self._prepare_deadline = self._clock() + config.takeoff_timeout_s
            if not telemetry.armed:
                res = await controller.command("arm", source=SOURCE)
                if not res.accepted:
                    return self._fail_start(f"arm rejected: {res.message}")
            if not telemetry.airborne or telemetry.altitude < course.start_altitude_m * 0.8:
                res = await controller.command("takeoff", source=SOURCE, target_altitude_m=course.start_altitude_m)
                if not res.accepted:
                    return self._fail_start(f"takeoff rejected: {res.message}")
            if self._run_task:
                self._worker = runtime.spawn(
                    "race.loop", self._run, owner=self.SOURCE_ID, criticality=Criticality.CRITICAL,
                    description=f"VantaRace closed loop at {config.loop_hz:g} Hz",
                )
                self._task = self._worker.task
            return self._result(True, f"race started on '{course.name}'")

    async def pause(self) -> dict:
        async with self._lock:
            if self.state not in (RaceState.RUNNING, RaceState.RECOVERING, RaceState.HOLDING_STALE):
                return self._result(False, "no running race to pause")
            self._latch_hold()
            self._loop.pause()
            self.state = RaceState.PAUSED
            self._event("race_paused", f"race paused ({self._progress_text()})")
            return self._result(True, "race paused; holding position")

    async def resume(self) -> dict:
        async with self._lock:
            if self.state != RaceState.PAUSED:
                return self._result(False, "no paused race to resume")
            self._loop.resume()
            self.state = RaceState.RUNNING
            self._event("race_resumed", f"race resumed ({self._progress_text()})")
            return self._result(True, "race resumed")

    async def abort(self, reason: str = "aborted by operator", *, command_vehicle: bool = True) -> dict:
        """Stop the race. With ``command_vehicle`` the aircraft is told to hold
        (leave offboard); without it, the caller (pilot override or failsafe)
        is about to command the vehicle itself."""
        async with self._lock:
            if not self.active:
                return self._result(False, "no active race")
            was_flying = self.state in _FLYING
            self._finish_state(RaceState.ABORTED, reason)
            if command_vehicle and was_flying:
                await self._controller.stop_offboard(source=SOURCE)
            return self._result(True, f"race aborted: {reason}")

    async def shutdown(self, reason: str = "backend shutting down") -> None:
        """Abort any race and stop the loop worker (first step of app shutdown)."""
        try:
            await self.abort(reason)
        except Exception:
            logger.exception("race abort during shutdown failed")
        if self._worker is not None and self._worker.alive:
            await (self._runtime or default_runtime()).stop_worker(self._worker.name)

    # -- faults (simulation rehearsal hooks) -----------------------------------
    def inject_fault(self, kind: str, duration_s: float, value: float | None = None, gate_id: str | None = None) -> str:
        if not self.active or self._perception is None:
            raise ValueError("start a race before injecting faults")
        if duration_s <= 0:
            raise ValueError("duration must be positive")
        now = self._clock()
        if kind == "vision_blackout":
            self._perception.inject_blackout(duration_s)
            msg = f"vision blackout for {duration_s:g}s"
        elif kind == "low_confidence":
            scale = 0.05 if value is None else float(value)
            self._perception.inject_low_confidence(scale, duration_s)
            msg = f"vision confidence scaled to {scale:g} for {duration_s:g}s"
        elif kind == "occlude_gate":
            gid = gate_id or (self._remaining[0] if self._remaining else None)
            if gid is None:
                raise ValueError("no gate left to occlude")
            self._perception.inject_occlusion(gid, duration_s)
            msg = f"{gid} occluded for {duration_s:g}s"
        elif kind == "state_stall":
            # Freeze the vehicle state the loop sees, as if the telemetry
            # link stopped delivering (works on any adapter, PX4 included).
            self._faults.frozen_state = self._controller.adapter.local_state()
            self._faults.state_stall_until = now + duration_s
            msg = f"vehicle state frozen for {duration_s:g}s"
        elif kind == "setpoint_delay":
            delay = 0.6 if value is None else float(value)
            self._faults.setpoint_delay_s = delay
            self._faults.setpoint_delay_until = now + duration_s
            msg = f"setpoints delayed by {delay * 1000:.0f} ms for {duration_s:g}s"
        else:
            raise ValueError(
                f"unknown fault '{kind}'; expected vision_blackout, low_confidence, occlude_gate, "
                "state_stall or setpoint_delay"
            )
        self._event("race_fault", f"fault injected: {msg}")
        return msg

    # -- the loop ---------------------------------------------------------------
    async def _run(self) -> None:
        period = 1.0 / self.config.loop_hz
        try:
            while self.active:
                started = time.monotonic()
                try:
                    await self.step()
                except Exception as exc:  # never let the loop die silently
                    logger.exception("race loop error")
                    async with self._lock:
                        if self.active:
                            self._finish_state(RaceState.FAILED, f"internal error: {exc}")
                    try:
                        await self._controller.stop_offboard(source=SOURCE)
                    except Exception:
                        pass
                    return
                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))
        except asyncio.CancelledError:
            pass

    async def step(self) -> None:
        """One closed-loop iteration (called by the task, or directly by tests)."""
        async with self._lock:
            if not self.active:
                return
            tick_started = time.perf_counter()
            try:
                if not self._renew_lease():
                    return
                await self._step_locked()
            finally:
                elapsed_ms = (time.perf_counter() - tick_started) * 1000.0
                self.stats.ticks += 1
                self.stats.tick_ms_total += elapsed_ms
                self.stats.max_tick_ms = max(self.stats.max_tick_ms, elapsed_ms)
                self._flush_records()

    def _renew_lease(self) -> bool:
        """Prove (and extend) authority every tick; losing it ends the race."""
        if self._lease is None:
            return True
        try:
            self._lease = self._controller.authority.renew(self._lease, LEASE_TTL_S)
            return True
        except LeaseInvalid as exc:
            self._lease = None
            self._finish_state(RaceState.ABORTED, f"authority lost: {exc.reason}")
            return False

    async def _step_locked(self) -> None:
        now = self._clock()
        controller = self._controller
        adapter = controller.adapter
        telemetry = controller.get_telemetry()
        if adapter is None or not telemetry.connected:
            self._finish_state(RaceState.ABORTED, "connection to the aircraft was lost")
            return

        if self.state == RaceState.PREPARING:
            await self._step_preparing(now, telemetry)
            return
        if self.state == RaceState.FINISHING:
            await self._step_finishing(now)
            return

        if not telemetry.armed:
            self._finish_state(RaceState.ABORTED, "aircraft disarmed during the race")
            return
        if now - self._race_started_at > self.course.max_time_s:
            self._finish_state(RaceState.ABORTED, f"course time limit ({self.course.max_time_s:g}s) reached")
            await controller.stop_offboard(source=SOURCE)
            return

        # Offboard must stay engaged; PX4 drops it if setpoints stop.
        if not getattr(adapter, "offboard_active", True):
            if self._offboard_lost_since is None:
                self._offboard_lost_since = now
            elif now - self._offboard_lost_since > self.config.offboard_grace_s:
                self._finish_state(RaceState.ABORTED, "the vehicle left offboard mode")
                return
        else:
            self._offboard_lost_since = None

        local = adapter.local_state()
        if now < self._faults.state_stall_until and self._faults.frozen_state is not None:
            local = self._faults.frozen_state
        age = local.age_s(now)
        if not local.valid or age > self.config.telemetry_stale_s:
            if not self._was_stale:
                self._was_stale = True
                self.stats.stale_telemetry_events += 1
                self._latch_hold()
                self._event("race_telemetry_stale", f"vehicle state is {age:.1f}s old; holding last known point")
            if self.state != RaceState.PAUSED:
                self.state = RaceState.HOLDING_STALE
            await self._send_hold(now)
            self._record(now, local, None)
            return
        if self._was_stale:
            self._was_stale = False
            self._event("race_telemetry_restored", "vehicle state is fresh again; resuming")
            if self.state == RaceState.HOLDING_STALE:
                self.state = RaceState.RUNNING
        self._last_local = local

        position = np.asarray(local.position_enu, dtype=np.float64)
        if self._prev_pos is not None and self._remaining:
            self._check_crossing(self._prev_pos, position, now)
            if not self._remaining:
                self._prev_pos = position
                await self._begin_finish(now)
                return
        self._prev_pos = position

        perception = await self._perception.update(local, self._remaining)
        self._last_perception = perception

        if self.state == RaceState.PAUSED:
            await self._send_hold(now)
            self._record(now, local, perception)
            return

        if await self._track_lost(now, perception):
            return

        truth = AircraftTruth(
            timestamp=now,
            position=position,
            velocity=np.asarray(local.velocity_enu, dtype=np.float64),
            yaw_deg=local.yaw_enu_deg,
            pitch_deg=-local.pitch_deg,
        )
        self._pending = None
        desired = self._loop.process_tick(
            truth, remaining_targets=perception.targets, position_uncertainty=0.2,
        )
        if self._loop.state == AutonomyState.FAILED:
            self._finish_state(RaceState.FAILED, "planner failed repeatedly")
            await controller.stop_offboard(source=SOURCE)
            return

        recovering = self._loop.state == AutonomyState.RECOVERING
        if recovering and not self._was_recovering:
            self.stats.recoveries += 1
            self._event(
                "race_recovering",
                f"holding: {perception.status.value.lower()} perception "
                f"(confidence {perception.current_confidence:.2f})",
            )
        elif not recovering and self._was_recovering:
            self.stats.reacquisitions += 1
            self._event("race_reacquired", f"target reacquired; resuming toward {self._remaining[0]}")
        self._was_recovering = recovering
        self.state = RaceState.RECOVERING if recovering else RaceState.RUNNING

        if desired is not None and not recovering:
            expected = np.asarray(desired.position, dtype=np.float64)
            self.stats.max_tracking_error_m = max(
                self.stats.max_tracking_error_m, float(np.linalg.norm(expected - position))
            )

        if self._pending is not None:
            await self._deliver(self._pending, now)
        self._record(now, local, perception, desired)

    async def _step_preparing(self, now: float, telemetry) -> None:
        """climb → (reposition to the start line) → align with gate 1 → race."""
        course = self.course
        controller = self._controller
        if now > self._prepare_deadline:
            self._finish_state(RaceState.FAILED, f"could not reach the start line in time ({self._prep_phase})")
            await controller.command("hold", source=SOURCE)
            return
        local = controller.adapter.local_state()
        if not local.valid:
            return  # wait for a local position
        position = np.asarray(local.position_enu, dtype=np.float64)
        start = np.array([0.0, 0.0, course.start_altitude_m])
        first = course.ordered_gates[0].position
        bearing = math.degrees(math.atan2(first[1] - position[1], first[0] - position[0]))

        if self._prep_phase == "climb":
            if not (telemetry.airborne and telemetry.altitude >= course.start_altitude_m * 0.85):
                return
            if float(np.linalg.norm(position[:2] - start[:2])) > self.config.reposition_radius_m:
                res = await controller.command(
                    "goto", source=SOURCE, x=0.0, y=0.0,
                    altitude=course.start_altitude_m, speed_m_s=min(3.0, self.config.speed_m_s),
                )
                if not res.accepted:
                    self._finish_state(RaceState.FAILED, f"could not fly to the start line: {res.message}")
                    return
                self._prep_phase = "reposition"
                self._event("race_repositioning", "flying to the start line before racing")
                return
            self._prep_phase = "engage"
        if self._prep_phase == "reposition":
            near = float(np.linalg.norm(position[:2] - start[:2])) <= 1.0 and abs(position[2] - start[2]) <= 0.8
            if not (near and float(np.linalg.norm(local.velocity_enu)) < 0.6):
                return
            self._prep_phase = "engage"
        if self._prep_phase == "engage":
            ned = enu_to_ned(position)
            res = await controller.start_offboard(
                *ned, enu_yaw_deg_to_ned(local.yaw_enu_deg), source=SOURCE, lease=self._lease
            )
            if not res.accepted:
                self._finish_state(RaceState.FAILED, f"could not enter offboard: {res.message}")
                await controller.command("hold", source=SOURCE)
                return
            self._loop.initialize(self._permit, total_gates=len(self._gates))
            self._loop.execution.update_hold_position(position, bearing)
            self._prep_phase = "align"
            self._event("race_aligning", f"offboard engaged at {position[2]:.1f} m; turning to face {course.ordered_gates[0].gate_id}")
        if self._prep_phase == "align":
            self._loop.execution.set_hold_yaw(bearing)
            await self._send_hold(now)
            error = abs(((local.yaw_enu_deg - bearing) + 180.0) % 360.0 - 180.0)
            if error > self.config.align_tolerance_deg:
                return
            self._prev_pos = position
            self._race_started_at = now
            self.state = RaceState.RUNNING
            self._event("race_running", f"aligned with {course.ordered_gates[0].gate_id}; racing")

    async def _begin_finish(self, now: float) -> None:
        self.state = RaceState.FINISHING
        self._event(
            "race_finish_line",
            f"all {len(self._gates)} gates passed in {now - self._race_started_at:.1f}s; "
            f"finishing with {self.config.finish.value.replace('_', ' ')}",
        )
        await self._step_finishing(now)

    async def _step_finishing(self, now: float) -> None:
        controller = self._controller
        await controller.stop_offboard(source=SOURCE)
        if self.config.finish != FinishAction.HOLD:
            command = "return_home" if self.config.finish == FinishAction.RETURN_HOME else "land"
            if command == "return_home":
                res = await controller.return_home(source=SOURCE)
            else:
                res = await controller.command(command, source=SOURCE)
            if not res.accepted:
                self._event("error", f"finish action {command} rejected: {res.message}; holding")
                await controller.command("hold", source=SOURCE)
        self._finish_state(RaceState.COMPLETE, f"course complete in {now - self._race_started_at:.1f}s")

    # -- helpers ----------------------------------------------------------------
    def _check_crossing(self, prev: np.ndarray, cur: np.ndarray, now: float) -> None:
        gid = self._remaining[0]
        gate = self._gates[gid]
        outcome = gate_crossing(gate, prev, cur)
        if outcome is None:
            return
        if outcome == "missed":
            self.stats.gates_missed += 1
            self._event("race_gate_missed", f"{gid}: crossed its plane outside the opening; going around")
            return
        self._loop.record_gate_pass(gid)
        self._remaining.pop(0)
        normal = gate.normal / np.linalg.norm(gate.normal)
        offset = cur - gate.position
        offset = offset - float(np.dot(offset, normal)) * normal
        self.stats.gate_pass_offsets_m.append(float(np.linalg.norm(offset)))
        split = now - self._race_started_at
        self._passes.append({"gate_id": gid, "time_s": round(split, 3), "offset_m": round(float(np.linalg.norm(offset)), 3)})
        self._event(
            "race_gate_passed",
            f"{gid} passed ({len(self._passes)}/{len(self._gates)}) at {split:.1f}s, "
            f"{np.linalg.norm(offset):.2f} m from centre",
        )
        self._lost_since = None

    async def _track_lost(self, now: float, perception: PerceptionResult) -> bool:
        """Track how long the current gate has been unseen. True = race aborted."""
        if perception.targets:
            self._lost_since = None
            return False
        if self._lost_since is None:
            self._lost_since = now
        lost_for = now - self._lost_since
        if lost_for > self.config.max_lost_s:
            self._finish_state(RaceState.ABORTED, f"no gate seen for {lost_for:.0f}s")
            await self._controller.stop_offboard(source=SOURCE)
            return True
        if lost_for > self.config.search_after_s and self._remaining and self._last_local is not None:
            # Search: hold position but turn to face where the gate should be.
            gate = self._gates[self._remaining[0]]
            delta = gate.position - np.asarray(self._last_local.position_enu)
            self._loop.execution.set_hold_yaw(math.degrees(math.atan2(delta[1], delta[0])))
        return False

    def _capture_setpoint(self, setpoint: OffboardSetpoint) -> None:
        self._pending = setpoint

    async def _deliver(self, setpoint: OffboardSetpoint, now: float) -> None:
        if self._loop.execution.mode == ExecutionMode.HOLD:
            self.stats.hold_setpoints += 1
        # Rehearse a stalled pipeline: each setpoint produced during the fault
        # reaches the flight core ``setpoint_delay_s`` late (and must be refused).
        while self._delayed and now >= self._delayed[0][0]:
            _, late = self._delayed.popleft()
            await self._send(late, now)
        if now < self._faults.setpoint_delay_until:
            self._delayed.append((now + self._faults.setpoint_delay_s, setpoint))
            return
        if not self._delayed:
            self._expiry_logged = False
        await self._send(setpoint, now)

    async def _send(self, setpoint: OffboardSetpoint, now: float) -> None:
        if not self.active:
            return
        result = await self._controller.offboard_setpoint(setpoint, now=now, lease=self._lease)
        if result.accepted:
            self.stats.setpoints_sent += 1
            self._last_setpoint = setpoint
            return
        if result.message.startswith("authority:"):
            # Whoever took authority (operator, failsafe) is commanding now.
            self.stats.setpoints_rejected += 1
            self._finish_state(RaceState.ABORTED, result.message)
            return
        if "expired" in result.message:
            self.stats.setpoints_expired += 1
            if not self._expiry_logged:
                self._expiry_logged = True
                self._event("race_setpoint_expired", f"setpoint refused: {result.message}")
            return
        self.stats.setpoints_rejected += 1
        if "geofence" in result.message or "no-fly" in result.message:
            self._finish_state(RaceState.ABORTED, result.message)
            await self._controller.stop_offboard(source=SOURCE)
            return
        self._event("rejected", f"setpoint refused: {result.message}")

    def _latch_hold(self) -> None:
        if self._loop is None:
            return
        local = self._last_local or (self._controller.adapter.local_state() if self._controller.adapter else None)
        if local is not None and local.valid:
            self._loop.execution.update_hold_position(local.position_enu, local.yaw_enu_deg)

    async def _send_hold(self, now: float) -> None:
        setpoint = self._loop.execution.hold_setpoint(now) if self._loop else None
        if setpoint is not None:
            self.stats.hold_setpoints += 1
            await self._send(setpoint, now)

    def _finish_state(self, state: RaceState, message: str) -> None:
        self.state = state
        self.message = message
        self._finished_at = self._clock()
        if self._loop is not None:
            self._loop.stop()
        if self._lease is not None:
            self._controller.authority.release(self._lease, f"race {state.value.lower()}")
            self._lease = None
        event = {
            RaceState.COMPLETE: "race_completed",
            RaceState.ABORTED: "race_aborted",
            RaceState.FAILED: "race_failed",
        }.get(state, "race_state")
        self._event(event, f"race {state.value.lower()}: {message}")
        self._buffer_summary()
        # Persist now: an abort from the API, a pilot override or a failsafe
        # ends the race outside the loop, and no tick follows to flush.
        self._flush_records()

    def _event(self, event_type: str, message: str) -> None:
        self._controller.log_event(event_type, message)
        self._buffer(lambda: RaceEventV1(event_type=event_type, message=message))

    def _wall(self) -> float:
        return self._vclock.wall_s()

    def _quality(self, local=None) -> Quality:
        flags = {QualityFlag.SIMULATED}
        if self.config.perception is PerceptionMode.VISION:
            flags.add(QualityFlag.SYNTHETIC_SENSOR)
        if local is not None and not local.capture_known:
            flags.add(QualityFlag.CAPTURE_TIME_IS_RECEIVE_TIME)
        adapter = self._controller.adapter
        provenance = (
            f"adapter:{getattr(adapter, 'adapter_id', 'none')}",
            f"perception:{self.config.perception.value}",
        )
        return Quality(
            valid=local.valid if local is not None else True,
            stale_after_ms=self.config.telemetry_stale_s * 1000.0,
            provenance=provenance,
            flags=frozenset(flags),
        )

    def _buffer(self, build_payload, *, captured_at=None, quality: Quality | None = None) -> None:
        """Stamp a payload into an envelope and queue it for the database.

        Recording must never stop the race: a payload that fails validation
        is logged and counted instead.
        """
        try:
            envelope = self._stamper.stamp(
                build_payload(), captured_at=captured_at, quality=quality or self._quality()
            )
            self._record_buffer.append(
                (self._wall(), envelope.topic, self._stamper.registry.encode(envelope))
            )
        except Exception:
            self.stats.record_errors += 1
            logger.exception("could not record race data")

    def _record(self, now: float, local, perception: PerceptionResult | None, desired=None) -> None:
        if now - self._last_record < 1.0 / self.config.record_hz:
            return
        self._last_record = now
        loop = self._loop

        def build() -> RaceSampleV1:
            return RaceSampleV1(
                race_state=self.state.value,
                race_time_s=round(max(0.0, now - self._race_started_at), 3) if self._race_started_at else 0.0,
                remaining_gates=tuple(self._remaining),
                state=VehicleStateV1.from_local_state(local) if local is not None else None,
                state_age_s=round(local.age_s(now), 3) if local is not None else None,
                vision=PerceptionFrameV1.from_result(perception) if perception is not None else None,
                planner=PlannerSampleV1(
                    race_state=loop.planner.state.value if loop else None,
                    autonomy_state=loop.state.value if loop else None,
                    desired=DesiredStateV1.from_desired(desired) if desired is not None else None,
                ),
                execution=ExecutionSampleV1.from_execution(loop.execution) if loop else None,
            )

        self._buffer(
            build,
            captured_at=local.captured_at if local is not None else None,
            quality=self._quality(local),
        )

    def _buffer_summary(self) -> None:
        self._buffer(lambda: RaceSummaryV1.from_status(self.status()))

    def _flush_records(self) -> None:
        if self._record_buffer:
            buffer, self._record_buffer = self._record_buffer, []
            try:
                self._controller.record_autonomy_samples(buffer)
            except Exception:
                logger.exception("failed to record autonomy samples")

    def _progress_text(self) -> str:
        return f"{len(self._passes)}/{len(self._gates)} gates"

    def _fail_start(self, message: str) -> dict:
        self._finish_state(RaceState.FAILED, message)
        return self._result(False, message)

    def _result(self, accepted: bool, message: str) -> dict:
        return {"accepted": accepted, "message": message, "state": self.state.value}

    def status(self) -> dict:
        now = self._clock()
        end = now if self.active else (self._finished_at or now)
        perception = self._last_perception
        return {
            "state": self.state.value,
            "message": self.message,
            "course": self.course.to_dict() if self.course else None,
            "config": self.config.to_dict(),
            "gates_passed": len(self._passes),
            "total_gates": len(self._gates),
            "next_gate": self._remaining[0] if self._remaining else None,
            "passes": list(self._passes),
            "race_time_s": round(end - self._race_started_at, 2) if self._race_started_at else 0.0,
            "elapsed_s": round(end - self._started_at, 2) if self._started_at else 0.0,
            "perception": perception.to_dict() if perception else None,
            "perception_status": perception.status.value if perception else None,
            "perception_faults": self._perception.faults() if self._perception else None,
            "autonomy": self._loop.to_dict() if self._loop else None,
            "stats": self.stats.to_dict(),
            "offboard": self._controller.offboard_stats,
            "last_setpoint": self._last_setpoint.to_dict() if self._last_setpoint else None,
            "authority": self._lease.to_dict(self._controller.authority.now()) if self._lease else None,
        }
