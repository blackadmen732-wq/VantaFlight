"""VantaAuthority: hierarchy, leases, preemption, and the flight core enforcing them."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from vantaflight.autonomy.courses import preset_courses
from vantaflight.autonomy.race_session import RaceConfig, RaceState
from vantaflight.core.authority import (
    MAX_AUTOMATION_TTL_S,
    AuthorityDenied,
    AuthorityLevel,
    LeaseInvalid,
    VantaAuthority,
)
from vantaflight.core.clock import VantaClock
from vantaflight.frames import enu_to_ned
from vantaflight.racing.vanta_execution import OffboardSetpoint

from .test_race_session import Harness, started

L = AuthorityLevel
VEHICLE = "mock-01"


def authority(*, simulated: bool = True) -> tuple[VantaAuthority, VantaClock]:
    clock = VantaClock.fake()
    auth = VantaAuthority(clock)
    auth.register_vehicle(VEHICLE, simulated=simulated)
    return auth, clock


def automation(auth: VantaAuthority, owner: str = "race:1", ttl_s: float = 2.0):
    return auth.acquire(owner, L.APPROVED_MISSION_AUTOMATION, {"trajectory", "offboard"}, VEHICLE,
                        ttl_s=ttl_s, approved_by="operator")


def test_operator_preempts_automation_and_old_leases_stop_working():
    auth, _ = authority()
    lease = automation(auth)
    assert auth.validate(lease, "trajectory", vehicle_id=VEHICLE) == lease
    revoked = auth.preempt(L.OPERATOR, "operator", VEHICLE, reason="pilot override (hold)")
    assert [r.lease_id for r in revoked] == [lease.lease_id]
    with pytest.raises(LeaseInvalid, match="preempted by operator"):
        auth.validate(lease, "trajectory")
    actions = [(d.action, d.owner) for d in auth.decisions()]
    assert ("grant", "race:1") in actions and ("revoke", "race:1") in actions and ("preempt", "operator") in actions


def test_mission_automation_cannot_take_what_an_operator_or_failsafe_holds():
    auth, _ = authority()
    auth.acquire("operator", L.OPERATOR, {"trajectory"}, VEHICLE, ttl_s=None)
    with pytest.raises(AuthorityDenied, match="held by operator"):
        automation(auth)
    auth.release_owner("operator")
    lease = automation(auth)
    auth.preempt(L.FAILSAFE, "failsafe:battery_low", VEHICLE, reason="battery low", hold=True)
    assert auth.check(lease, "trajectory") is not None
    with pytest.raises(AuthorityDenied, match="failsafe:battery_low"):
        automation(auth, owner="race:2")
    auth.release_owner("failsafe:battery_low")
    assert automation(auth, owner="race:2").owner == "race:2"


def test_equal_level_cannot_steal_and_lower_levels_cannot_preempt():
    auth, _ = authority()
    automation(auth, owner="race:1")
    with pytest.raises(AuthorityDenied, match="held by race:1"):
        automation(auth, owner="mission:7")
    with pytest.raises(AuthorityDenied):
        auth.preempt(L.APPROVED_MISSION_AUTOMATION, "mission:7", VEHICLE, reason="mine now")


def test_agents_can_propose_but_never_hold_physical_execution_authority():
    physical, _ = authority(simulated=False)
    with pytest.raises(AuthorityDenied, match="physical vehicle"):
        physical.acquire("agent:planner", L.AGENT_PROPOSAL, {"trajectory"}, VEHICLE, ttl_s=5.0)
    with pytest.raises(AuthorityDenied, match="physical vehicle"):
        physical.acquire("agent:planner", L.AGENT_PROPOSAL, {"*"}, VEHICLE, ttl_s=5.0)
    proposal = physical.acquire("agent:planner", L.AGENT_PROPOSAL, {"proposal"}, VEHICLE, ttl_s=5.0)
    assert proposal.covers("proposal") and not proposal.covers("trajectory")
    # An unknown vehicle counts as physical.
    with pytest.raises(AuthorityDenied, match="physical vehicle"):
        physical.acquire("teacher", L.TRAINING_OBSERVATION, {"arm"}, "unknown-drone", ttl_s=5.0)
    sim, _ = authority(simulated=True)
    assert sim.acquire("agent:planner", L.AGENT_PROPOSAL, {"trajectory"}, VEHICLE, ttl_s=5.0)


def test_automation_needs_an_approver_and_a_short_renewable_lease():
    auth, clock = authority()
    with pytest.raises(AuthorityDenied, match="approved"):
        auth.acquire("race:1", L.APPROVED_MISSION_AUTOMATION, {"trajectory"}, VEHICLE, ttl_s=2.0)
    with pytest.raises(AuthorityDenied, match="must expire"):
        auth.acquire("race:1", L.APPROVED_MISSION_AUTOMATION, {"trajectory"}, VEHICLE, ttl_s=None,
                     approved_by="operator")
    with pytest.raises(AuthorityDenied, match="at most"):
        automation(auth, ttl_s=MAX_AUTOMATION_TTL_S + 1)
    with pytest.raises(AuthorityDenied, match="unknown scope"):
        auth.acquire("race:1", L.OPERATOR, {"motor.set_pwm"}, VEHICLE, ttl_s=None)
    lease = automation(auth, ttl_s=2.0)
    clock.advance(1.5)
    lease = auth.renew(lease, 2.0)
    clock.advance(1.5)
    auth.validate(lease, "offboard")  # still alive thanks to the renewal
    clock.advance(1.0)
    with pytest.raises(LeaseInvalid, match="expired"):
        auth.validate(lease, "offboard")
    with pytest.raises(LeaseInvalid):
        auth.renew(lease, 2.0)
    assert any(d.action == "expire" for d in auth.decisions())


def test_forged_or_mismatched_leases_are_refused():
    auth, _ = authority()
    lease = automation(auth)
    with pytest.raises(LeaseInvalid, match="forged"):
        auth.validate(replace(lease, revocation_token="0" * 32), "trajectory")
    with pytest.raises(LeaseInvalid, match="forged"):
        auth.validate(replace(lease, lease_id="not-a-lease"), "trajectory")
    with pytest.raises(LeaseInvalid, match="does not cover"):
        auth.validate(lease, "payload")
    with pytest.raises(LeaseInvalid, match="is for"):
        auth.validate(lease, "trajectory", vehicle_id="other-drone")
    with pytest.raises(LeaseInvalid, match="no authority lease"):
        auth.validate(None, "trajectory")


def test_disconnecting_the_vehicle_revokes_its_leases():
    auth, _ = authority()
    lease = automation(auth)
    auth.unregister_vehicle(VEHICLE)
    with pytest.raises(LeaseInvalid, match="vehicle disconnected"):
        auth.validate(lease, "trajectory")
    assert not auth.is_simulated(VEHICLE)


# -- the flight core enforces it ---------------------------------------------------------------
async def test_pilot_override_revokes_the_race_lease_before_any_setpoint_flies():
    h = await started("sitl_line_3")
    await h.fly(1.0)
    lease = h.session.lease
    assert lease is not None and lease.level is L.APPROVED_MISSION_AUTOMATION
    assert h.controller.authority.holder(h.controller.vehicle_id, "trajectory") == lease
    assert (await h.controller.command("hold")).accepted
    assert h.state() == RaceState.ABORTED and h.session.lease is None
    # A setpoint still in flight with the old lease is refused by the flight core.
    north, east, down = (float(v) for v in enu_to_ned(h.position()))
    setpoint = OffboardSetpoint(
        np.array([north, east, down]), np.zeros(3), np.zeros(3), 0.0, h.clock.t, "late", 1.0
    )
    result = await h.controller.offboard_setpoint(setpoint, now=h.clock.t, lease=lease)
    assert not result.accepted and "preempted by operator" in result.message
    assert h.controller.offboard_stats["unauthorized"] == 1


async def test_failsafe_locks_automation_out_until_the_aircraft_is_down():
    h = await started("sitl_line_3")
    await h.fly_until(lambda: h.session.status()["gates_passed"] >= 1)
    h.adapter.inject_fault("battery", 20.0)
    await h.fly_until(lambda: not h.session.active, timeout=2.0)
    assert "failsafe: battery_low" in h.session.message
    holder = h.controller.authority.holder(h.controller.vehicle_id, "trajectory")
    assert holder is not None and holder.level is L.FAILSAFE
    h.adapter.inject_fault("battery", 90.0)  # (a charged battery would not lift the lock-out)
    refused = await h.session.start(preset_courses()["sitl_line_3"], RaceConfig(perception="vision"))
    assert not refused["accepted"] and "failsafe:battery_low" in refused["message"]
    await h.fly_until(lambda: not h.controller.get_telemetry().armed, timeout=120)
    await h.fly(0.2)
    assert h.controller.authority.holder(h.controller.vehicle_id, "trajectory") is None
    again = await h.session.start(preset_courses()["sitl_line_3"], RaceConfig(perception="vision"))
    assert again["accepted"], again


async def test_race_refused_when_authority_is_held_elsewhere():
    h = Harness()
    await h.connect()
    h.controller.authority.acquire("operator", L.OPERATOR, {"*"}, h.controller.vehicle_id, ttl_s=None)
    res = await h.session.start(preset_courses()["sitl_line_3"], RaceConfig(perception="vision"))
    assert not res["accepted"] and "authority refused" in res["message"]
    assert h.state() == RaceState.IDLE
