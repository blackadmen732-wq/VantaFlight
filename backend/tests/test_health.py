"""VantaHealth: per-dimension health, probes, change history, and the vehicle's link facts."""
from __future__ import annotations

from vantaflight.core.health import (
    ComponentHealth,
    DimensionStatus,
    HealthDimension,
    HealthState,
    VantaHealth,
    worst,
)
from vantaflight.models import Telemetry

from .test_race_session import Harness

S = HealthState
D = HealthDimension


def test_worst_dimension_wins_and_unknown_is_not_healthy():
    assert worst([S.HEALTHY, S.DEGRADED, S.HEALTHY]) is S.DEGRADED
    assert worst([S.HEALTHY, S.UNKNOWN]) is S.UNKNOWN
    assert worst([S.STOPPED, S.STOPPED]) is S.STOPPED
    assert worst([S.STOPPED, S.HEALTHY]) is S.HEALTHY
    assert worst([S.RECOVERING, S.FAILED, S.STARTING]) is S.FAILED
    assert worst([]) is S.UNKNOWN


def test_dimensions_stay_separate_facts():
    camera = ComponentHealth("camera", "sensor", {
        D.LIVENESS: DimensionStatus(S.HEALTHY, "transport up"),
        D.FRESHNESS: DimensionStatus(S.DEGRADED, "frames 400 ms old", 0.4),
    })
    assert camera.state is S.DEGRADED
    as_dict = camera.to_dict()
    assert as_dict["dimensions"]["LIVENESS"]["state"] == "HEALTHY"
    assert as_dict["dimensions"]["FRESHNESS"] == {"state": "DEGRADED", "detail": "frames 400 ms old", "value": 0.4}


def test_probes_and_reports_merge_and_a_broken_probe_is_itself_reported():
    t = [0.0]
    health = VantaHealth(lambda: t[0])
    health.register_probe("camera", lambda now: [
        ComponentHealth("camera", "sensor", {D.LIVENESS: DimensionStatus(S.HEALTHY)}),
    ])
    health.report("camera", D.CORRECTNESS, S.FAILED, "calibration missing")
    health.register_probe("broken", lambda now: 1 / 0)
    snapshot = {c.component: c for c in health.snapshot()}
    assert snapshot["camera"].state is S.FAILED
    assert snapshot["camera"].dimension(D.LIVENESS).state is S.HEALTHY
    assert snapshot["probe:broken"].dimension(D.CORRECTNESS).state is S.FAILED
    assert health.overall() is S.FAILED


def test_changes_are_recorded_once_and_announced():
    state = {"freshness": S.HEALTHY}
    health = VantaHealth(lambda: 0.0)
    health.register_probe("link", lambda now: [
        ComponentHealth("vehicle.link", "vehicle", {D.FRESHNESS: DimensionStatus(state["freshness"], "age")}),
    ])
    heard = []
    health.on_change(heard.append)
    first = health.evaluate()
    assert [(c.previous, c.state) for c in first] == [(S.UNKNOWN, S.HEALTHY)]
    assert health.evaluate() == [], "no change, no announcement"
    state["freshness"] = S.DEGRADED
    (change,) = health.evaluate()
    assert (change.component, change.previous, change.state) == ("vehicle.link", S.HEALTHY, S.DEGRADED)
    assert len(heard) == 2 and len(health.changes()) == 2


async def test_link_freshness_and_liveness_are_independent():
    h = Harness()
    idle = {c.component: c for c in h.controller.health()}
    assert idle["vehicle.link"].state is S.STOPPED
    await h.connect()
    await h.fly(0.5)
    link = {c.component: c for c in h.controller.health()}["vehicle.link"]
    assert link.dimension(D.LIVENESS).state is S.HEALTHY
    assert link.dimension(D.FRESHNESS).state is S.HEALTHY
    stale_s = h.controller.guardian.config.link_stale_s
    h.adapter.inject_fault("link_stall", stale_s + 1.0)  # transport up, data stops flowing
    await h.fly(0.8)
    link = {c.component: c for c in h.controller.health()}["vehicle.link"]
    assert link.dimension(D.LIVENESS).state is S.HEALTHY, "still connected"
    assert link.dimension(D.FRESHNESS).state is S.DEGRADED, "but the data is old"
    await h.fly(stale_s - 0.4)
    link = {c.component: c for c in h.controller.health()}["vehicle.link"]
    assert link.dimension(D.FRESHNESS).state is S.FAILED
    await h.fly(1.5)
    link = {c.component: c for c in h.controller.health()}["vehicle.link"]
    assert link.dimension(D.FRESHNESS).state is S.HEALTHY


async def test_missing_sensors_are_unknown_not_assumed():
    h = Harness()
    await h.connect()
    real = h.adapter.get_telemetry

    def camera_only() -> Telemetry:
        return real().model_copy(update={"battery_available": False, "position_available": False})

    h.adapter.get_telemetry = camera_only
    components = {c.component: c for c in h.controller.health()}
    assert components["vehicle.battery"].state is S.UNKNOWN
    assert components["vehicle.position"].state is S.UNKNOWN
    assert components["vehicle.link"].state is S.HEALTHY
