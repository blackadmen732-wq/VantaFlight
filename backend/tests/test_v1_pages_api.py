"""Backend support for the V1 Twin and Evolution pages."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vantaflight.digital_twin.live import LiveTwinPublisher
from vantaflight.digital_twin.primitives import TwinSnapshotBuilder
from vantaflight.evolution.training_bridge import run_packages, weakness_report
from vantaflight.main import create_app
from vantaflight.mission import MissionPlan, Waypoint
from vantaflight.models import Telemetry
from vantaflight.routing import Airspace, zone_from_spec
from vantaflight.training.models import RunConfig, RunResult

from .conftest import fly_until


# -- live twin ------------------------------------------------------------------
def _kinds(snapshot) -> list[str]:
    return [p["kind"] for p in snapshot.to_dict()["primitives"]]


def test_twin_publishes_aircraft_trail_zones_and_fence():
    builder = TwinSnapshotBuilder()
    pub = LiveTwinPublisher(builder)
    airspace = Airspace([zone_from_spec({"name": "Tower", "center": [20, 0], "radius": 4}, 0)])
    for i in range(3):
        pub.publish(Telemetry(connected=True, x=float(i), y=0.0, altitude=2.0, z=2.0), airspace=airspace)
        snap = builder.build()
    data = snap.to_dict()
    assert data["aircraft"]["ACTUAL"]["position"] == [2.0, 0.0, 2.0]
    kinds = _kinds(snap)
    assert "PATH" in kinds and "REGION" in kinds and "ENVELOPE" in kinds
    region = next(p for p in data["primitives"] if p["kind"] == "REGION")
    assert region["label"] == "Tower"


def test_twin_never_draws_an_unmeasured_position():
    builder = TwinSnapshotBuilder()
    pub = LiveTwinPublisher(builder)
    pub.publish(Telemetry(connected=True, x=0.0, y=0.0, position_available=False))
    assert builder.build().to_dict()["aircraft"] == {}


async def test_twin_shows_active_mission_route(controller, clock):
    builder = TwinSnapshotBuilder()
    pub = LiveTwinPublisher(builder)
    await controller.connect()
    assert (await controller.command("arm")).accepted
    plan = MissionPlan(name="Box", waypoints=[Waypoint(x=10, y=0, altitude=5), Waypoint(x=10, y=10, altitude=5)])
    assert (await controller.start_mission(plan)).accepted
    await fly_until(controller, clock, lambda t: t.altitude > 1.0)
    pub.publish(controller.get_telemetry(), controller.mission, controller.airspace)
    data = builder.build().to_dict()
    route = next(p for p in data["primitives"] if p["kind"] == "PATH" and p["layer"] == "DESIRED")
    assert route["label"] == "Box"
    assert len(route["points"]) == 3  # home + 2 waypoints


# -- evolution --------------------------------------------------------------------
def _run(i: int, mode: str, passed: int, total: int = 8, complete: bool | None = None, seed: int = 0) -> RunResult:
    done = passed == total if complete is None else complete
    r = RunResult(
        run_id=f"r{i}", campaign_id="c", config=RunConfig(course_mode=mode, seed=seed, gate_count=total),
        gates_passed=passed, total_gates=total, complete=done,
        failures=[] if done else ["missed gate"],
    )
    r.failure_categories = [] if done else ["GATE_MISS"]
    return r


def test_weakness_report_ranks_the_failing_condition_first():
    results = [_run(i, "RANDOM", 8) for i in range(4)] + [_run(10 + i, "SLALOM", 2) for i in range(4)]
    report = weakness_report(results)
    assert report[0]["condition"].startswith("SLALOM")
    assert report[0]["failure_rate"] == 1.0
    assert report[0]["top_causes"][0]["category"] == "GATE_MISS"
    assert report[-1]["failure_rate"] == 0.0


def test_run_packages_carry_utility():
    pkg = run_packages([_run(1, "RANDOM", 8)], "1.0.0")[0]
    assert pkg.metrics["utility"] == pytest.approx(1.5)
    assert pkg.success


def test_evolution_api(tmp_path):
    body = {"name": "Evo", "course_modes": ["RANDOM"], "seed_range": [0, 2], "gate_counts": [6], "difficulty_tiers": ["EASY"]}
    with TestClient(create_app(db_path=str(tmp_path / "evo.db"))) as client:
        a = client.post("/api/training/campaigns", json=body).json()["campaign_id"]
        b = client.post("/api/training/campaigns", json=body).json()["campaign_id"]
        for cid in (a, b):
            client.post(f"/api/training/campaigns/{cid}/start")
            while client.post(f"/api/training/campaigns/{cid}/run-next").json().get("status") != "complete":
                pass

        weak = client.get("/api/evolution/weaknesses").json()
        assert weak["run_count"] == 4
        assert weak["weaknesses"]

        cmp = client.post(
            "/api/evolution/compare", json={"champion_campaign_id": a, "challenger_campaign_id": b}
        ).json()
        # Same stack on the same scenarios: nothing to promote.
        assert cmp["promote"] is False
        assert cmp["champion"]["runs"] == 2

        assert client.get("/api/evolution/weaknesses", params={"campaign_id": "nope"}).status_code == 404
