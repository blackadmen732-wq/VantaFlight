"""VantaRace closed loop: vision → state → planner → execution → offboard.

Every test flies the real loop (RaceSession + GatePerception + AutonomyLoop +
VantaExecution + FlightController.offboard_setpoint) against the mock drone's
offboard physics on a fake clock. The same scenarios run against PX4 SITL in
``simulation/px4/race_e2e.py``.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from vantaflight.adapters import MockDroneAdapter
from vantaflight.autonomy.courses import (
    RaceCourse,
    check_course,
    course_from_spec,
    preset_courses,
)
from vantaflight.autonomy.perception import GatePerception, PerceptionStatus
from vantaflight.autonomy.race_session import (
    FinishAction,
    RaceConfig,
    RaceSession,
    RaceState,
    gate_crossing,
)
from vantaflight.connection import ConnectionManager
from vantaflight.core import FlightController
from vantaflight.data import FlightDatabase
from vantaflight.frames import (
    enu_to_ned,
    enu_yaw_deg_to_ned,
    frd_to_ned_rotation,
    ned_to_enu,
    ned_yaw_deg_to_enu,
)
from vantaflight.models import FlightMode, LocalState
from vantaflight.racing.execution import (
    AutonomousExecutionRejected,
    SimulationOnlyExecutionGuard,
)
from vantaflight.racing.models import DesiredTrajectoryState, GateTarget, TargetSlot
from vantaflight.racing.trajectory import trajectory_through_gates
from vantaflight.racing.vanta_execution import ExecutionMode, OffboardSetpoint, VantaExecution
from vantaflight.replay import ReplayLoader

DT = 0.05


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Harness:
    def __init__(self, adapter_cls=MockDroneAdapter) -> None:
        self.clock = Clock()
        self.db = FlightDatabase(":memory:")
        self.adapters: list = []

        def factory(d):
            adapter = adapter_cls(adapter_id=d.drone_id, time_source=self.clock)
            self.adapters.append(adapter)
            return adapter

        self.controller = FlightController(
            self.db,
            connection_manager=ConnectionManager(adapter_factory=factory),
            mission_clock=self.clock,
        )
        self.session = RaceSession(self.controller, clock=self.clock, run_task=False)
        self.controller.attach_autonomy(self.session)
        self.ticks = 0

    @property
    def adapter(self):
        return self.adapters[-1]

    async def connect(self) -> None:
        assert (await self.controller.connect()).accepted

    async def step(self) -> None:
        self.clock.t += DT
        await self.session.step()
        self.ticks += 1
        if self.ticks % 2 == 0:  # the app's 10 Hz telemetry/failsafe tick
            await self.controller.tick()

    async def fly(self, seconds: float) -> None:
        for _ in range(int(round(seconds / DT))):
            await self.step()

    async def fly_until(self, predicate, timeout: float = 120.0) -> float:
        start = self.clock.t
        while self.clock.t - start < timeout:
            await self.step()
            if predicate():
                return self.clock.t - start
        raise AssertionError(f"condition not reached in {timeout}s; status={self.session.status()['state']} "
                             f"message={self.session.message!r}")

    def position(self) -> np.ndarray:
        return np.asarray(self.adapter.local_state().position_enu)

    def events(self) -> list[str]:
        return [e.event_type for e in self.controller.recent_events()]

    def state(self) -> RaceState:
        return self.session.state


async def started(course_id: str = "sitl_line_3", **config) -> Harness:
    h = Harness()
    await h.connect()
    config.setdefault("perception", "vision")
    res = await h.session.start(preset_courses()[course_id], RaceConfig(**config))
    assert res["accepted"], res
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=30)
    return h


# -- end to end ------------------------------------------------------------------
@pytest.mark.parametrize("course_id", ["sitl_line_3", "sitl_slalom_4", "sitl_arc_5"])
async def test_vision_race_completes_every_course(course_id):
    h = await started(course_id)
    await h.fly_until(lambda: not h.session.active, timeout=150)
    status = h.session.status()
    assert h.state() == RaceState.COMPLETE, status["message"]
    assert status["gates_passed"] == status["total_gates"]
    assert all(offset < 0.5 for offset in status["stats"]["gate_pass_offsets_m"])
    assert status["stats"]["setpoints_rejected"] == 0
    assert h.session.loop.gate_tracker.is_complete


async def test_clean_landing_and_replay_after_completion():
    h = await started("sitl_line_3", finish="land")
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    await h.fly_until(lambda: not h.controller.get_telemetry().armed, timeout=60)
    t = h.controller.get_telemetry()
    assert t.altitude == 0.0 and t.flight_mode == FlightMode.IDLE
    assert not h.adapter.offboard_active

    flight_id = h.controller.flight_id
    kinds = {s["kind"] for s in h.db.get_autonomy_samples(flight_id)}
    assert {"race.sample", "race.event", "race.summary"} <= kinds
    envelope = h.db.get_autonomy_samples(flight_id, "race.sample")[-1]["data"]
    assert envelope["schema_version"] == 1 and envelope["topic"] == "race.sample"
    for key in ("vision", "state", "planner", "execution"):
        assert key in envelope["payload"]
    assert envelope["payload"]["state"]["position"]["frame"] == "world_enu"
    timeline = ReplayLoader(h.db).load_timeline(flight_id)
    autonomy = [f for f in timeline.frames if f.frame_type == "autonomy"]
    assert autonomy and all("decode_error" not in f.data for f in autonomy)
    assert any(f.data["topic"] == "race.summary" for f in autonomy)
    summary = next(f.data["data"] for f in autonomy if f.data["topic"] == "race.summary")
    assert summary["state"] == "COMPLETE" and summary["gates_passed"] == 3
    for event in ("race_started", "race_running", "race_gate_passed", "race_completed", "offboard_start", "offboard_stop"):
        assert event in h.events()


async def test_back_to_back_races_reposition_to_the_start_line():
    h = await started("sitl_line_3", finish="hold")
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    assert np.linalg.norm(h.position()[:2]) > 30, "first race ends far from home"
    res = await h.session.start(preset_courses()["sitl_slalom_4"], RaceConfig(perception="vision"))
    assert res["accepted"], res
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=60)
    assert np.linalg.norm(h.position()[:2]) < 1.5, "raced from the start line"
    assert "race_repositioning" in h.events() and "race_aligning" in h.events()
    await h.fly_until(lambda: not h.session.active, timeout=150)
    assert h.state() == RaceState.COMPLETE


async def test_race_turns_to_face_the_first_gate_before_starting():
    h = Harness()
    await h.connect()
    # Take off facing north; the first slalom gate is east-north-east.
    assert (await h.controller.command("arm")).accepted
    assert (await h.controller.command("takeoff", target_altitude_m=5.0)).accepted
    h.adapter._heading = 0.0
    await h.fly(6.0)
    res = await h.session.start(preset_courses()["sitl_slalom_4"], RaceConfig(perception="vision"))
    assert res["accepted"], res
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=30)
    gate = h.session.course.ordered_gates[0].position
    bearing = math.degrees(math.atan2(gate[1], gate[0]))
    assert abs(h.adapter.local_state().yaw_enu_deg - bearing) < 15
    await h.fly_until(lambda: not h.session.active, timeout=150)
    assert h.state() == RaceState.COMPLETE


async def test_map_perception_baseline_completes():
    h = await started("sitl_slalom_4", perception="scene")
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    assert max(h.session.stats.gate_pass_offsets_m) < 0.1


# -- operator control ---------------------------------------------------------------
async def test_pause_holds_position_and_resume_finishes():
    h = await started("sitl_line_3")
    await h.fly_until(lambda: h.session.status()["gates_passed"] >= 1)
    assert (await h.session.pause())["accepted"]
    assert h.state() == RaceState.PAUSED
    await h.fly(1.5)  # brake
    held = h.position()
    sent = h.adapter.offboard_setpoints
    await h.fly(3.0)
    assert np.linalg.norm(h.position() - held) < 0.3
    assert h.adapter.offboard_active, "pause keeps offboard alive with hold setpoints"
    assert h.adapter.offboard_setpoints > sent
    assert (await h.session.resume())["accepted"]
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    assert {"race_paused", "race_resumed"} <= set(h.events())


async def test_operator_abort_leaves_offboard_and_holds():
    h = await started("sitl_line_3")
    await h.fly(3.0)
    res = await h.session.abort()
    assert res["accepted"] and h.state() == RaceState.ABORTED
    assert not h.adapter.offboard_active
    assert h.controller.get_telemetry().flight_mode == FlightMode.HOLD
    sent = h.adapter.offboard_setpoints
    await h.fly(2.0)
    assert h.adapter.offboard_setpoints == sent, "no setpoints after abort"
    assert not (await h.session.abort())["accepted"]


async def test_pilot_command_overrides_race():
    h = await started("sitl_line_3")
    await h.fly(2.0)
    assert (await h.controller.command("hold")).accepted
    assert h.state() == RaceState.ABORTED
    assert "pilot override" in h.session.message


@pytest.mark.parametrize("ending", ["operator_abort", "pilot_override", "battery_failsafe"])
async def test_a_race_ended_outside_the_loop_records_its_final_event_and_summary(ending):
    """Abort, override and failsafe end the race between loop ticks; the
    closing event and summary must still reach the recording (and replay)."""
    h = await started("sitl_line_3")
    await h.fly(1.0)
    if ending == "operator_abort":
        assert (await h.session.abort())["accepted"]
    elif ending == "pilot_override":
        assert (await h.controller.command("hold")).accepted
    else:
        h.adapter.inject_fault("battery", 20.0)
        await h.fly_until(lambda: not h.session.active, timeout=2.0)
    assert h.state() == RaceState.ABORTED

    def race_rows():
        return [r for r in h.db.get_autonomy_samples(h.controller.flight_id) if r["kind"].startswith("race.")]

    rows = race_rows()
    assert rows[-1]["kind"] == "race.summary", [r["kind"] for r in rows[-3:]]
    assert rows[-1]["data"]["payload"]["state"] == "ABORTED"
    events = [r["data"]["payload"]["event_type"] for r in rows if r["kind"] == "race.event"]
    assert events[-1] == "race_aborted"
    await h.fly(1.0)  # nothing is written twice once the race is over
    assert len(race_rows()) == len(rows)


# -- perception faults --------------------------------------------------------------
async def test_stale_vision_holds_then_resumes():
    h = await started("sitl_line_3")
    await h.fly(0.5)
    assert np.linalg.norm(h.position() - h.session.course.ordered_gates[0].position) > 8.0
    h.session.inject_fault("vision_blackout", 3.0)
    await h.fly_until(lambda: h.state() == RaceState.RECOVERING, timeout=3.0)
    assert h.session.status()["perception_status"] == PerceptionStatus.BLACKOUT.value
    await h.fly(1.5)
    assert np.linalg.norm(h.adapter.local_state().velocity_enu) < 0.6, "holds while blind"
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=5.0)
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    assert h.session.stats.recoveries >= 1 and h.session.stats.reacquisitions >= 1


async def test_low_confidence_target_holds_then_resumes():
    h = await started("sitl_line_3")
    await h.fly(0.5)
    h.session.inject_fault("low_confidence", 2.5, value=0.05)
    await h.fly_until(lambda: h.state() == RaceState.RECOVERING, timeout=3.0)
    assert h.session.loop.execution.mode == ExecutionMode.HOLD
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=6.0)
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE


async def test_lost_gate_is_searched_for_and_reacquired():
    h = await started("sitl_slalom_4")
    await h.fly(0.5)
    h.session.inject_fault("occlude_gate", 4.0, gate_id="gate_1")
    await h.fly_until(lambda: h.session.status()["perception_status"] == PerceptionStatus.LOST.value, timeout=3.0)
    await h.fly_until(lambda: h.state() == RaceState.RECOVERING, timeout=3.0)
    await h.fly(3.0)
    # After search_after_s the hold yaw points at where gate_1 should be.
    gate = h.session.course.ordered_gates[0]
    delta = gate.position - h.position()
    expected = math.degrees(math.atan2(delta[1], delta[0]))
    yaw = h.adapter.local_state().yaw_enu_deg
    assert abs(((yaw - expected) + 180) % 360 - 180) < 15
    await h.fly_until(lambda: h.state() == RaceState.RUNNING, timeout=6.0)
    await h.fly_until(lambda: not h.session.active, timeout=150)
    assert h.state() == RaceState.COMPLETE
    assert "race_reacquired" in h.events()


# -- vehicle-state faults --------------------------------------------------------------
async def test_stale_telemetry_holds_last_point_then_continues():
    h = await started("sitl_line_3")
    await h.fly(1.0)
    h.adapter.inject_fault("link_stall", 1.2)
    await h.fly_until(lambda: h.state() == RaceState.HOLDING_STALE, timeout=1.0)
    await h.fly_until(lambda: h.state() in (RaceState.RUNNING, RaceState.RECOVERING), timeout=3.0)
    assert h.session.stats.stale_telemetry_events == 1
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE
    assert {"race_telemetry_stale", "race_telemetry_restored"} <= set(h.events())


async def test_frozen_vehicle_state_fault_holds_then_continues():
    h = await started("sitl_line_3")
    await h.fly(1.0)
    h.session.inject_fault("state_stall", 1.0)
    await h.fly_until(lambda: h.state() == RaceState.HOLDING_STALE, timeout=1.0)
    await h.fly_until(lambda: h.state() in (RaceState.RUNNING, RaceState.RECOVERING), timeout=2.0)
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE


async def test_long_telemetry_loss_triggers_failsafe_and_aborts_race():
    h = await started("sitl_line_3")
    await h.fly(1.0)
    h.adapter.inject_fault("link_stall", 5.0)
    await h.fly_until(lambda: not h.session.active, timeout=6.0)
    assert h.state() == RaceState.ABORTED
    assert "failsafe: link_degraded" in h.session.message


async def test_battery_failsafe_aborts_race_returns_home_and_lands():
    h = await started("sitl_line_3")
    await h.fly_until(lambda: h.session.status()["gates_passed"] >= 1)
    h.adapter.inject_fault("battery", 20.0)
    await h.fly_until(lambda: not h.session.active, timeout=2.0)
    assert h.state() == RaceState.ABORTED and "failsafe: battery_low" in h.session.message
    assert not h.adapter.offboard_active
    await h.fly_until(lambda: not h.controller.get_telemetry().armed, timeout=120)
    t = h.controller.get_telemetry()
    assert math.hypot(t.x, t.y) < 0.5 and t.altitude == 0.0


async def test_expired_setpoints_are_refused_not_flown():
    h = await started("sitl_line_3")
    await h.fly(1.0)
    before = h.adapter.offboard_setpoints
    h.session.inject_fault("setpoint_delay", 0.5, value=0.6)
    await h.fly(0.45)
    assert h.adapter.offboard_setpoints == before, "late setpoints never reach the vehicle"
    await h.fly(1.0)
    stats = h.session.stats
    assert stats.setpoints_expired > 0
    assert h.controller.offboard_stats["expired"] == stats.setpoints_expired
    assert "race_setpoint_expired" in h.events()
    assert h.adapter.offboard_active, "a short stall does not lose offboard"
    await h.fly_until(lambda: not h.session.active, timeout=120)
    assert h.state() == RaceState.COMPLETE


# -- refusals ------------------------------------------------------------------------
async def test_race_refused_without_connection_or_for_physical_aircraft():
    h = Harness()
    res = await h.session.start(preset_courses()["sitl_line_3"])
    assert not res["accepted"]

    class Physical(MockDroneAdapter):
        def get_capabilities(self):
            return super().get_capabilities().model_copy(update={"is_simulated": False})

    p = Harness(Physical)
    await p.connect()
    res = await p.session.start(preset_courses()["sitl_line_3"])
    assert not res["accepted"] and "autonomy refused" in res["message"]


async def test_race_refused_on_low_battery_and_bad_course():
    h = Harness()
    await h.connect()
    h.adapter.inject_fault("battery", 28.0)
    assert not (await h.session.start(preset_courses()["sitl_line_3"]))["accepted"]
    h.adapter.inject_fault("battery", 100.0)
    far = course_from_spec({"gates": [{"x": 500, "y": 0, "z": 5}]})
    res = await h.session.start(far)
    assert not res["accepted"] and "geofence" in res["message"]


async def test_flight_core_refuses_unsafe_setpoints():
    h = await started("sitl_line_3")
    now = h.clock.t

    def setpoint(position_ned, velocity_ned=(0.0, 0.0, 0.0), timestamp=None):
        return OffboardSetpoint(
            np.array(position_ned, float), np.array(velocity_ned, float), np.zeros(3), 0.0,
            now if timestamp is None else timestamp, "t", 1.0,
        )

    lease = h.session.lease
    assert (await h.controller.offboard_setpoint(setpoint((0, 5, -5)), now=now, lease=lease)).accepted
    outside = await h.controller.offboard_setpoint(setpoint((0, 400, -5)), now=now, lease=lease)
    assert not outside.accepted and "geofence" in outside.message
    fast = await h.controller.offboard_setpoint(setpoint((0, 5, -5), (0, 20, 0)), now=now, lease=lease)
    assert not fast.accepted and "speed" in fast.message
    late = await h.controller.offboard_setpoint(setpoint((0, 5, -5), timestamp=now - 1.0), now=now, lease=lease)
    assert not late.accepted and "expired" in late.message
    # No lease, or a lease the operator has since preempted: refused before anything else.
    bare = await h.controller.offboard_setpoint(setpoint((0, 5, -5)), now=now)
    assert not bare.accepted and "authority" in bare.message
    assert h.controller.offboard_stats["unauthorized"] == 1


# -- building blocks --------------------------------------------------------------------
def test_frames_round_trip_and_yaw_conventions():
    v = np.array([1.0, 2.0, 3.0])
    np.testing.assert_allclose(enu_to_ned(v), [2.0, 1.0, -3.0])
    np.testing.assert_allclose(ned_to_enu(enu_to_ned(v)), v)
    assert enu_yaw_deg_to_ned(0.0) == pytest.approx(90.0)  # east
    assert enu_yaw_deg_to_ned(90.0) == pytest.approx(0.0)  # north
    assert ned_yaw_deg_to_enu(enu_yaw_deg_to_ned(37.0)) == pytest.approx(37.0)
    # Facing east, the nose (FRD x) points along NED +y.
    np.testing.assert_allclose(frd_to_ned_rotation(90.0) @ [1, 0, 0], [0, 1, 0], atol=1e-12)
    # Nose-up pitch raises the nose: NED z goes negative.
    assert (frd_to_ned_rotation(0.0, 10.0) @ [1, 0, 0])[2] < 0


def _desired(position, velocity, yaw=0.0, timestamp=1000.0, confidence=0.9):
    return DesiredTrajectoryState(position, velocity, [0, 0, 0], yaw, timestamp, "t", confidence)


def test_execution_emits_true_ned_and_holds_yaw_when_slow():
    clock = Clock()
    permit = SimulationOnlyExecutionGuard.authorize(
        "udp://127.0.0.1:14540",
        SimpleNamespace(is_simulated=True, adapter_type="px4_sitl", supported_capabilities=["simulation"]),
    )
    execution = VantaExecution(clock=clock)
    execution.activate(permit)
    sp = execution.translate(_desired([10, 2, 5], [3, 0, 0], yaw=0.0, timestamp=clock.t))
    np.testing.assert_allclose(sp.position_ned, [2, 10, -5])
    np.testing.assert_allclose(sp.velocity_ned, [0, 3, 0])
    assert sp.yaw_deg == pytest.approx(90.0)
    execution.update_hold_position([0, 0, 5], yaw_enu_deg=90.0)
    slow = execution.translate(_desired([0, 0, 5], [0.1, 0, 0], yaw=0.0, timestamp=clock.t))
    assert slow.yaw_deg == pytest.approx(0.0), "keeps the held (north) heading instead of snapping east"
    hold = execution.translate(_desired([9, 9, 9], [3, 0, 0], timestamp=clock.t, confidence=0.01))
    assert execution.mode == ExecutionMode.HOLD
    np.testing.assert_allclose(hold.position_ned, [0, 0, -5])
    np.testing.assert_allclose(hold.velocity_ned, [0, 0, 0])


def test_trajectory_passes_through_gate_centre_on_turning_course():
    gates = [
        GateTarget([12, 3, 5], [0.99, -0.12, 0], 1.5, slot=TargetSlot.CURRENT),
        GateTarget([24, -3, 5], [1, 0, 0], 1.5, slot=TargetSlot.NEXT),
    ]
    trajectory = trajectory_through_gates([0, 0, 5], gates, speed=3.0)
    assert any(np.allclose(p, [12, 3, 5]) for p in trajectory.positions)


def test_guard_sim_scheme_is_only_for_the_in_process_mock():
    mock = SimpleNamespace(is_simulated=True, adapter_type="mock", supported_capabilities=["simulation"])
    assert SimulationOnlyExecutionGuard.authorize("sim://mock-0", mock).adapter_type == "mock"
    px4 = SimpleNamespace(is_simulated=True, adapter_type="px4_sitl", supported_capabilities=["simulation"])
    with pytest.raises(AutonomousExecutionRejected):
        SimulationOnlyExecutionGuard.authorize("sim://mock-0", px4)


async def test_mock_offboard_tracks_setpoints_and_times_out_to_hold():
    clock = Clock()
    drone = MockDroneAdapter(time_source=clock)
    await drone.connect()
    await drone.arm()
    await drone.takeoff(5.0)
    clock.t += 5.0
    drone.get_telemetry()
    with pytest.raises(Exception):
        await drone.set_position_velocity_ned((0, 0, -5), (0, 0, 0), 0.0)
    await drone.start_offboard(0.0, 0.0, -5.0, 90.0)
    assert drone.offboard_active
    for _ in range(100):
        clock.t += 0.05
        await drone.set_position_velocity_ned((0.0, 10.0, -5.0), (0.0, 0.0, 0.0), 90.0)
    state = drone.local_state()
    assert state.position_enu[0] == pytest.approx(10.0, abs=0.3)
    clock.t += 1.5  # setpoint stream stops
    drone.get_telemetry()
    assert not drone.offboard_active and drone.get_telemetry().flight_mode == FlightMode.HOLD


def test_gate_crossing_passed_and_missed():
    gate = preset_courses()["sitl_line_3"].ordered_gates[0]
    assert gate_crossing(gate, np.array([11.0, 0, 5]), np.array([12.5, 0, 5])) == "passed"
    assert gate_crossing(gate, np.array([11.0, 3, 5]), np.array([12.5, 3, 5])) == "missed"
    assert gate_crossing(gate, np.array([10.0, 0, 5]), np.array([11.0, 0, 5])) is None


def test_course_checks():
    for course in preset_courses().values():
        assert check_course(course).valid, course.course_id
    sharp = course_from_spec({"gates": [{"x": 12, "y": 0, "z": 5}, {"x": 12, "y": 12, "z": 5}]})
    assert any("turn" in e for e in check_course(sharp).errors)
    low = course_from_spec({"gates": [{"x": 12, "y": 0, "z": 1.5}]})
    assert any("below" in e for e in check_course(low).errors)
    close = course_from_spec({"gates": [{"x": 3, "y": 0, "z": 5}]})
    assert any("from the previous gate" in e for e in check_course(close).errors)
    with pytest.raises(ValueError):
        course_from_spec({"gates": []})


async def test_perception_sees_gates_with_small_error_and_handles_faults():
    clock = Clock()
    course = preset_courses()["sitl_slalom_4"]
    perception = GatePerception(course, clock=clock)
    ids = [g.gate_id for g in course.ordered_gates]
    state = LocalState(clock.t, (0.0, 0.0, 5.0), (0, 0, 0), 0.0, 0.0, 0.0, True)
    result = await perception.update(state, ids)
    assert result.status == PerceptionStatus.TRACKING
    gate_1 = next(o for o in result.observations if o.gate_id == "gate_1")
    assert gate_1.error_m < 0.5
    perception.inject_blackout(1.0)
    clock.t += 0.1
    assert (await perception.update(state, ids)).status == PerceptionStatus.BLACKOUT
    clock.t += 1.5
    perception.inject_occlusion("gate_1", 5.0)
    blocked = await perception.update(state, ids)
    assert all(t.target_id != "gate_1" for t in blocked.targets)
