"""Mission execution: fly a `MissionPlan` waypoint by waypoint.

The runner is a small state machine that is *ticked* with each telemetry
sample (it owns no background task, so there is nothing to leak or race).
It never talks to an adapter directly: every action goes through the
``execute`` callback, which in the app is ``FlightController.command`` — so
mission commands get exactly the same safety checks and flight recording as
commands from the operator.

    IDLE --start--> RUNNING --(all waypoints)--> COMPLETED
                     |   ^
               pause |   | resume
                     v   |
                    PAUSED
    RUNNING/PAUSED --abort / failsafe / pilot override / link loss--> ABORTED
"""
from __future__ import annotations

import asyncio
import enum
import math
import time
from typing import Awaitable, Callable

from ..config import MISSION_ACCEPT_RADIUS_M
from ..models import CommandResult, Telemetry
from .plan import FinishAction, MissionPlan

ExecuteFn = Callable[..., Awaitable[CommandResult]]
EventFn = Callable[[str, str], None]

# A leg may take this many times its nominal duration (plus slack) before the
# runner gives up on it, e.g. because the aircraft is fighting wind or stuck.
LEG_TIMEOUT_FACTOR = 3.0
LEG_TIMEOUT_SLACK_S = 20.0
NOMINAL_CLIMB_RATE_M_S = 1.0


class MissionState(str, enum.Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class MissionPhase(str, enum.Enum):
    TAKEOFF = "TAKEOFF"
    TRANSIT = "TRANSIT"
    LOITER = "LOITER"
    DONE = "DONE"


_ACTIVE = {MissionState.RUNNING, MissionState.PAUSED}


class MissionRunner:
    def __init__(
        self,
        execute: ExecuteFn,
        on_event: EventFn | None = None,
        clock: Callable[[], float] = time.monotonic,
        accept_radius_m: float = MISSION_ACCEPT_RADIUS_M,
    ) -> None:
        self._execute = execute
        self._on_event = on_event or (lambda _type, _msg: None)
        self._clock = clock
        self._accept = accept_radius_m
        self._lock = asyncio.Lock()
        self._reset()

    def _reset(self) -> None:
        self.state = MissionState.IDLE
        self.plan: MissionPlan | None = None
        self.phase = MissionPhase.DONE
        self.index = 0
        self.reached = 0
        self.message = ""
        self._loiter_until = 0.0
        self._leg_deadline = 0.0
        self._started_at = 0.0
        self._finished_at = 0.0
        self._distance_to_target = 0.0

    def clear(self) -> None:
        """Forget a finished mission (e.g. at the start of a new session)."""
        if not self.active:
            self._reset()

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE

    # -- operator actions ---------------------------------------------------
    async def start(self, plan: MissionPlan, telemetry: Telemetry) -> CommandResult:
        async with self._lock:
            if self.active:
                return self._result("mission_start", False, "a mission is already in progress")
            if not plan.waypoints:
                return self._result("mission_start", False, "mission has no waypoints")
            if not (telemetry.connected and telemetry.armed):
                return self._result("mission_start", False, "connect and arm before starting a mission")

            self._reset()
            self.plan = plan
            self.state = MissionState.RUNNING
            self._started_at = self._clock()
            self._emit("mission_started", f"mission '{plan.name}' started ({len(plan.waypoints)} waypoints)")

            if telemetry.airborne:
                ok = await self._begin_leg(telemetry)
            else:
                self.phase = MissionPhase.TAKEOFF
                altitude = plan.waypoints[0].altitude
                self._leg_deadline = self._clock() + self._timeout_for(altitude / NOMINAL_CLIMB_RATE_M_S)
                res = await self._execute("takeoff", target_altitude_m=altitude)
                ok = res.accepted or self._fail(f"takeoff rejected: {res.message}")
            if not ok:
                return self._result("mission_start", False, self.message)
            return self._result("mission_start", True, f"mission '{plan.name}' started")

    async def pause(self) -> CommandResult:
        async with self._lock:
            if self.state != MissionState.RUNNING:
                return self._result("mission_pause", False, "no running mission to pause")
            res = await self._execute("hold")
            if not res.accepted:
                return self._result("mission_pause", False, res.message)
            self.state = MissionState.PAUSED
            self._emit("mission_paused", f"mission paused at waypoint {self.index + 1}")
            return self._result("mission_pause", True, "mission paused")

    async def resume(self, telemetry: Telemetry) -> CommandResult:
        async with self._lock:
            if self.state != MissionState.PAUSED:
                return self._result("mission_resume", False, "no paused mission to resume")
            self.state = MissionState.RUNNING
            if not await self._begin_leg(telemetry):
                return self._result("mission_resume", False, self.message)
            self._emit("mission_resumed", f"mission resumed toward waypoint {self.index + 1}")
            return self._result("mission_resume", True, "mission resumed")

    async def abort(self, reason: str) -> bool:
        """Stop the mission. Issues no command: the caller decides what the
        aircraft does next (hold, land, return). Returns True if one was active."""
        async with self._lock:
            return self._abort_locked(reason)

    # -- the tick -------------------------------------------------------------
    async def step(self, telemetry: Telemetry) -> None:
        async with self._lock:
            if self.state != MissionState.RUNNING or self.plan is None:
                return
            if not telemetry.connected:
                self._abort_locked("connection to the aircraft was lost")
                return
            if not telemetry.armed:
                self._abort_locked("aircraft disarmed during the mission")
                return

            now = self._clock()
            if self.phase == MissionPhase.TAKEOFF:
                target_alt = self.plan.waypoints[0].altitude
                self._distance_to_target = max(0.0, target_alt - telemetry.altitude)
                if self._distance_to_target <= self._accept:
                    await self._begin_leg(telemetry)
                elif now > self._leg_deadline:
                    await self._timeout_locked("takeoff")
                return

            wp = self.plan.waypoints[self.index]
            self._distance_to_target = math.dist(
                (telemetry.x, telemetry.y, telemetry.altitude), (wp.x, wp.y, wp.altitude)
            )

            if self.phase == MissionPhase.TRANSIT:
                if self._distance_to_target <= self._accept:
                    self.reached = self.index + 1
                    self._emit("waypoint_reached", f"reached waypoint {self.index + 1}/{len(self.plan.waypoints)}")
                    if wp.hold_s > 0:
                        self.phase = MissionPhase.LOITER
                        self._loiter_until = now + wp.hold_s
                    else:
                        await self._advance(telemetry)
                elif now > self._leg_deadline:
                    await self._timeout_locked(f"leg to waypoint {self.index + 1}")
            elif self.phase == MissionPhase.LOITER and now >= self._loiter_until:
                await self._advance(telemetry)

    # -- internals ----------------------------------------------------------
    async def _begin_leg(self, telemetry: Telemetry) -> bool:
        assert self.plan is not None
        wp = self.plan.waypoints[self.index]
        speed = self.plan.leg_speed(wp)
        distance = math.dist(
            (telemetry.x, telemetry.y, telemetry.altitude), (wp.x, wp.y, wp.altitude)
        )
        self._distance_to_target = distance
        self.phase = MissionPhase.TRANSIT
        self._leg_deadline = self._clock() + self._timeout_for(distance / speed)
        res = await self._execute("goto", x=wp.x, y=wp.y, altitude=wp.altitude, speed_m_s=speed)
        if not res.accepted:
            return self._fail(f"waypoint {self.index + 1} rejected: {res.message}")
        return True

    async def _advance(self, telemetry: Telemetry) -> None:
        assert self.plan is not None
        if self.index + 1 < len(self.plan.waypoints):
            self.index += 1
            await self._begin_leg(telemetry)
            return
        finish = self.plan.finish
        if finish != FinishAction.HOLD:
            res = await self._execute(finish.value)
            if not res.accepted:
                self._fail(f"finish action '{finish.value}' rejected: {res.message}")
                return
        self.phase = MissionPhase.DONE
        self.state = MissionState.COMPLETED
        self._finished_at = self._clock()
        self.message = f"completed; finishing with {finish.value.replace('_', ' ')}"
        self._emit("mission_completed", f"mission '{self.plan.name}' completed")

    async def _timeout_locked(self, what: str) -> None:
        self._fail(f"{what} timed out")
        await self._execute("hold")

    def _abort_locked(self, reason: str) -> bool:
        if not self.active:
            return False
        self.state = MissionState.ABORTED
        self.phase = MissionPhase.DONE
        self.message = reason
        self._finished_at = self._clock()
        self._emit("mission_aborted", f"mission aborted: {reason}")
        return True

    def _fail(self, reason: str) -> bool:
        self._abort_locked(reason)
        return False

    @staticmethod
    def _timeout_for(nominal_s: float) -> float:
        return nominal_s * LEG_TIMEOUT_FACTOR + LEG_TIMEOUT_SLACK_S

    def _emit(self, event_type: str, message: str) -> None:
        self._on_event(event_type, message)

    @staticmethod
    def _result(command: str, accepted: bool, message: str) -> CommandResult:
        return CommandResult(command=command, accepted=accepted, message=message)

    def status(self) -> dict:
        plan = self.plan
        total = len(plan.waypoints) if plan else 0
        end = self._finished_at if not self.active else self._clock()
        return {
            "state": self.state.value,
            "phase": self.phase.value,
            "name": plan.name if plan else None,
            "current_index": self.index,
            "waypoints_reached": self.reached,
            "total_waypoints": total,
            "progress": round(self.reached / total, 3) if total else 0.0,
            "distance_to_target_m": round(self._distance_to_target, 2),
            "elapsed_s": round(end - self._started_at, 1) if self._started_at else 0.0,
            "message": self.message,
            "plan": plan.model_dump(mode="json") if plan else None,
        }
