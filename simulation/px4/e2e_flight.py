#!/usr/bin/env python3
"""End-to-end flight against a real PX4 SITL + Gazebo simulator.

Drives a running VantaFlight backend over its HTTP API, exactly as the UI
does, through the whole V1 flight path:

    connect -> preflight -> arm -> takeoff -> autonomous mission ->
    battery failsafe -> return to launch -> land -> disconnect -> replay

The low-battery failsafe is real: PX4's simulated battery is told to drain
fast (SIM_BAT_DRAIN) over a second MAVLink link, and VantaFlight's own
FailsafeGuardian has to notice and bring the aircraft home.

Prerequisites (see docs/PX4.md):
  * PX4 SITL + Gazebo running and sending MAVLink to UDP 14540 (API) and
    14550 (ground station), e.g. the headless container:
        docker run --rm --network host --log-driver none \\
            jonasvautherin/px4-gazebo-headless:1.14.3 127.0.0.1 127.0.0.1
  * The backend running:  uvicorn vantaflight.main:app --port 8000
  * pip install "mavsdk>=2,<4"   (used here only to set the battery drain)

Usage:  python simulation/px4/e2e_flight.py [--api http://127.0.0.1:8000]
Exit code 0 means every stage passed; the report is printed as JSON lines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

STAGES: list[dict] = []


def stage(name: str, ok: bool, **detail) -> None:
    STAGES.append({"stage": name, "ok": ok, **detail})
    print(json.dumps(STAGES[-1]), flush=True)
    if not ok:
        raise SystemExit(f"stage failed: {name}")


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def _call(self, method: str, path: str, body: object | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return {"http_error": exc.code, "detail": exc.read().decode(errors="replace")}

    def get(self, path: str) -> dict:
        return self._call("GET", path)

    def post(self, path: str, body: object | None = None) -> dict:
        return self._call("POST", path, body)


def wait_for(api: Api, predicate, timeout_s: float, what: str, poll_s: float = 0.5) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = api.get("/api/telemetry")
        if predicate(last):
            return last
        time.sleep(poll_s)
    raise SystemExit(f"timed out after {timeout_s:.0f}s waiting for {what}; last telemetry: {last}")


async def drain_battery(gcs_url: str, drain_s: float) -> None:
    """Make PX4's simulated battery drain fast, over the ground-station link."""
    from mavsdk import System

    drone = System(port=50052)  # separate mavsdk_server from the backend's
    await drone.connect(system_address=gcs_url)
    async for state in drone.core.connection_state():
        if state.is_connected:
            break
    await drone.param.set_param_float("SIM_BAT_DRAIN", drain_s)
    # Let the battery fall to 0% (default floor is 50%): we want our own
    # failsafe to act before PX4's.
    await drone.param.set_param_float("SIM_BAT_MIN_PCT", 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--gcs", default="udpin://0.0.0.0:14550", help="second MAVLink link for parameters")
    ap.add_argument("--altitude", type=float, default=10.0)
    args = ap.parse_args()
    api = Api(args.api)
    t0 = time.monotonic()

    health = api.get("/api/health")
    stage("backend", health.get("status") == "ok", version=health.get("version"))

    res = api.post("/api/connect", {"adapter_type": "px4_sitl"})
    stage("connect", bool(res.get("accepted")), message=res.get("message"))
    t = wait_for(api, lambda t: t.get("connected"), 30, "telemetry")
    caps = api.get("/api/capabilities")["capabilities"]
    stage(
        "telemetry",
        t["connected"] and t["position_available"],
        battery=t["battery_percentage"],
        position=[t["x"], t["y"], t["altitude"]],
        supports_goto=caps.get("supports_goto"),
    )

    pre = api.get("/api/preflight")
    stage(
        "preflight",
        pre.get("critical_failures", 1) == 0,
        warnings=[c["name"] for c in pre.get("checks", []) if not c["passed"]],
    )

    # Arm and take off on our own first, so takeoff is tested on its own.
    res = api.post("/api/arm")
    stage("arm", bool(res.get("accepted")), message=res.get("message"))
    wait_for(api, lambda t: t.get("armed"), 15, "armed")
    res = api.post("/api/takeoff", {"target_altitude_m": args.altitude})
    stage("takeoff", bool(res.get("accepted")), message=res.get("message"))
    t = wait_for(api, lambda t: t["altitude"] >= args.altitude * 0.85, 60, "takeoff altitude")
    stage("airborne", True, altitude=round(t["altitude"], 2))

    # Autonomous mission: a 30 m square, flown with goto commands.
    pat = api.post("/api/mission/pattern", {"kind": "square", "params": {"size_m": 30, "altitude_m": args.altitude}})
    plan = pat["plan"]
    plan["finish"] = "hold"
    check = api.post("/api/mission/validate", plan)
    stage("mission_check", bool(check.get("valid")), distance_m=check.get("distance_m"), errors=check.get("errors"))
    res = api.post("/api/mission/start", plan)
    stage("mission_start", bool(res.get("accepted")), message=res.get("message"))
    deadline = time.monotonic() + 240
    status: dict = {}
    while time.monotonic() < deadline:
        status = api.get("/api/mission")
        if status.get("state") not in ("RUNNING", "PAUSED"):
            break
        time.sleep(1)
    stage(
        "mission_complete",
        status.get("state") == "COMPLETED",
        waypoints=f'{status.get("waypoints_reached")}/{status.get("total_waypoints")}',
        elapsed_s=status.get("elapsed_s"),
        message=status.get("message"),
    )

    # Battery failsafe -> RTL -> land, triggered by real simulated drain.
    asyncio.run(asyncio.wait_for(drain_battery(args.gcs, 30.0), 60))
    t = wait_for(api, lambda t: t["battery_percentage"] <= 25.0, 120, "battery to fall below 25%", poll_s=1)
    deadline = time.monotonic() + 30
    failsafe: dict = {}
    while time.monotonic() < deadline:
        failsafe = api.get("/api/failsafe")
        if failsafe.get("last_trigger"):
            break
        time.sleep(0.5)
    trig = failsafe.get("last_trigger") or {}
    stage("failsafe", trig.get("reason") in ("battery_low", "battery_critical"), trigger=trig)
    t = wait_for(api, lambda t: t["flight_mode"] in ("RETURNING", "LANDING") or t["altitude"] < 1.0, 30, "return home")
    stage("return_home", True, flight_mode=t["flight_mode"])
    t = wait_for(api, lambda t: not t["armed"], 180, "landing and disarm", poll_s=1)
    stage(
        "landed",
        t["altitude"] < 1.0,
        distance_from_home_m=round((t["x"] ** 2 + t["y"] ** 2) ** 0.5, 2),
        battery=round(t["battery_percentage"], 1),
    )

    res = api.post("/api/disconnect")
    stage("disconnect", bool(res.get("accepted")), message=res.get("message"))

    flights = api.get("/api/replay/flights")["flights"]
    latest = max(flights, key=lambda f: f["flight_id"])
    fid = latest["flight_id"]
    loaded = api.post(f"/api/replay/load/{fid}")
    timeline = loaded.get("timeline", {})
    stage(
        "replay",
        loaded.get("status") == "loaded" and timeline.get("total_frames", 0) > 0,
        flight_id=fid,
        status=latest["status"],
        duration_s=timeline.get("duration_s"),
        frames=timeline.get("total_frames"),
        samples=latest["sample_count"],
        events=latest["event_count"],
        commands=latest["command_count"],
    )

    print(json.dumps({"result": "PASS", "stages": len(STAGES), "duration_s": round(time.monotonic() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
