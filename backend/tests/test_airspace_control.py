"""No-fly zones in the live system: command gate, plan checks, safe return home."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vantaflight.main import create_app
from vantaflight.mission import FinishAction, MissionPlan, MissionState, Waypoint
from vantaflight.models import FlightMode

from .conftest import fly, fly_until

STADIUM = {"name": "Stadium", "center": [30, 0], "radius": 10}


async def _airborne_at(controller, clock, x, y, altitude=10):
    await controller.connect()
    await controller.command("arm")
    await controller.command("takeoff", target_altitude_m=altitude)
    await fly(controller, clock, 10)
    if (x, y) != (0, 0):
        assert (await controller.command("goto", x=x, y=y, altitude=altitude)).accepted
        await fly_until(controller, clock, lambda t: abs(t.x - x) < 0.2 and abs(t.y - y) < 0.2)


def test_set_airspace_validates(controller):
    controller.set_airspace([STADIUM], margin_m=3)
    assert controller.airspace.margin_m == 3
    assert len(controller.airspace.zones) == 1
    with pytest.raises(ValueError, match="covers home"):
        controller.set_airspace([{"name": "Home", "center": [0, 0], "radius": 5}])
    with pytest.raises(ValueError):
        controller.set_airspace([{"name": "Bad", "vertices": [[0, 0], [1, 1]]}])
    assert len(controller.airspace.zones) == 1  # unchanged after a bad update


async def test_goto_into_or_through_a_zone_is_refused(controller, clock):
    controller.set_airspace([STADIUM])
    await _airborne_at(controller, clock, 0, 0)
    inside = await controller.command("goto", x=30, y=0, altitude=10)
    assert not inside.accepted and "inside no-fly zone 'Stadium'" in inside.message
    through = await controller.command("goto", x=60, y=0, altitude=10)
    assert not through.accepted and "would enter no-fly zone 'Stadium'" in through.message
    around = await controller.command("goto", x=0, y=30, altitude=10)
    assert around.accepted


async def test_mission_through_a_zone_is_refused_but_optimized_route_flies(controller, clock):
    controller.set_airspace([STADIUM])
    await controller.connect()
    await controller.command("arm")
    straight = MissionPlan(waypoints=[Waypoint(x=60, y=0, altitude=10)], finish=FinishAction.LAND)
    res = await controller.start_mission(straight)
    assert not res.accepted and "Stadium" in res.message

    route = controller.plan_route([Waypoint(x=60, y=0, altitude=10)])
    assert route.detour_points >= 1
    assert (await controller.start_mission(route.plan)).accepted
    path = []

    def track(t):
        path.append((t.x, t.y))
        return controller.mission.state != MissionState.RUNNING

    await fly_until(controller, clock, track)
    assert controller.mission.state == MissionState.COMPLETED
    zone = controller.airspace.zones[0]
    assert not any(zone.contains(p) for p in path)  # never entered the stadium
    t = await fly_until(controller, clock, lambda t: not t.armed)
    assert (t.x, t.y) == pytest.approx((0, 0), abs=1.0)


async def test_return_home_routes_around_zone(controller, clock):
    await _airborne_at(controller, clock, 60, 0)
    controller.set_airspace([STADIUM])
    res = await controller.return_home()
    assert res.accepted and "safe route" in res.message
    assert controller.mission.status()["name"] == "Safe return home"
    path = []
    t = await fly_until(controller, clock, lambda t: path.append((t.x, t.y)) or not t.armed)
    assert (t.x, t.y) == pytest.approx((0, 0), abs=1.0)
    assert not any(controller.airspace.zones[0].contains(p) for p in path)


async def test_return_home_without_zones_uses_native_rtl(controller, clock):
    await _airborne_at(controller, clock, 20, 0)
    res = await controller.return_home()
    assert res.accepted and res.message == "accepted"
    assert controller.get_telemetry().flight_mode == FlightMode.RETURNING


async def test_low_battery_failsafe_returns_around_zone(controller, clock):
    await _airborne_at(controller, clock, 60, 0)
    controller.set_airspace([STADIUM])
    controller._connections.adapter.inject_fault("battery", 20)
    await fly(controller, clock, 0.2)
    assert controller.guardian.last_trigger.reason == "battery_low"
    assert controller.mission.status()["name"] == "Safe return home"
    path = []
    t = await fly_until(controller, clock, lambda t: path.append((t.x, t.y)) or not t.armed)
    assert (t.x, t.y) == pytest.approx((0, 0), abs=1.0)
    assert not any(controller.airspace.zones[0].contains(p) for p in path)


# -- HTTP ---------------------------------------------------------------------
@pytest.fixture
def client():
    with TestClient(create_app(db_path=":memory:")) as c:
        yield c


def test_airspace_api(client):
    assert client.get("/api/airspace").json()["zones"] == []
    r = client.post("/api/airspace", json={"zones": [STADIUM], "margin_m": 4}).json()
    assert r["ok"] and r["airspace"]["margin_m"] == 4 and len(r["airspace"]["zones"]) == 1
    bad = client.post("/api/airspace", json={"zones": [{"name": "Home", "center": [0, 0], "radius": 3}]}).json()
    assert not bad["ok"] and "covers home" in bad["error"]
    assert len(client.get("/api/airspace").json()["zones"]) == 1


def test_route_optimize_api(client):
    client.post("/api/airspace", json={"zones": [STADIUM]})
    stops = [{"x": 60, "y": 0, "altitude": 10}, {"x": -40, "y": 0, "altitude": 10}, {"x": 55, "y": 10, "altitude": 10}]
    r = client.post("/api/route/optimize", json={"stops": stops}).json()
    assert r["ok"], r
    route = r["route"]
    assert route["method"] == "exact" and route["saved_m"] > 0
    assert route["detour_points"] >= 1
    assert client.post("/api/mission/validate", json=route["plan"]).json()["valid"]

    bad = client.post("/api/route/optimize", json={"stops": [{"x": 30, "y": 0, "altitude": 10}]}).json()
    assert not bad["ok"] and "Stadium" in bad["error"]


def test_export_import_api(client):
    client.post("/api/airspace", json={"zones": [STADIUM]})
    plan = {"name": "x", "waypoints": [{"x": 10, "y": 20, "altitude": 8}], "finish": "land"}
    doc = client.post("/api/mission/export", json={"plan": plan}).json()
    assert doc["fileType"] == "Plan" and len(doc["geoFence"]["polygons"]) == 1
    back = client.post("/api/mission/import", json=doc).json()
    assert back["ok"]
    assert back["plan"]["waypoints"][0]["x"] == pytest.approx(10, abs=0.01)
    assert len(back["zones"]) == 1
    junk = client.post("/api/mission/import", json={"hello": "world"}).json()
    assert not junk["ok"] and "could not read plan" in junk["error"]
