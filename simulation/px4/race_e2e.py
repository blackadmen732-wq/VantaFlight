#!/usr/bin/env python3
"""VantaRace closed loop against a real PX4 SITL + Gazebo, end to end.

Drives a running VantaFlight backend over its HTTP API — exactly what the
Race page does — through the full vision-driven loop:

    connect -> preflight -> race 1 (line course, VantaSight vision):
        offboard engaged -> pause/resume -> stale vision -> low confidence
        -> late (expired) setpoints -> frozen vehicle state -> complete
    race 2 (slalom, vision): lost/reacquired gate -> complete
    race 3 (line, vision): real PX4 battery drain -> battery failsafe ->
        race aborted -> return home -> land -> disarm
    disconnect -> replay holds vision/state/planner/execution samples

Every check is recorded; the script keeps going after a failed *check*
(so one report shows everything) but stops if a *prerequisite* fails.

Prerequisites (see docs/VANTARACE_SITL.md):
  * PX4 SITL + Gazebo, MAVLink on UDP 14540 (API) and 14550 (GCS), e.g.
        docker run --rm --network host jonasvautherin/px4-gazebo-headless:1.14.3
  * the backend:  uvicorn vantaflight.main:app --port 8000
  * pip install "mavsdk>=2,<4"  (only for the GCS link: params + battery drain)

Usage:  python simulation/px4/race_e2e.py [--api http://127.0.0.1:8000] [--json report.json]
Exit 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[dict] = []


class Prerequisite(SystemExit):
    pass


def record(name: str, ok: bool, *, required: bool = False, **detail) -> bool:
    entry = {"check": name, "ok": bool(ok), **detail}
    RESULTS.append(entry)
    print(json.dumps(entry), flush=True)
    if required and not ok:
        raise Prerequisite(f"prerequisite failed: {name}")
    return bool(ok)


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

    def race(self) -> dict:
        return self.get("/api/vantarace")

    def telemetry(self) -> dict:
        return self.get("/api/telemetry")


def wait_race(api: Api, predicate, timeout_s: float, poll_s: float = 0.2) -> tuple[bool, dict]:
    deadline = time.monotonic() + timeout_s
    status: dict = {}
    while time.monotonic() < deadline:
        status = api.race()
        if predicate(status):
            return True, status
        time.sleep(poll_s)
    return False, status


def wait_telemetry(api: Api, predicate, timeout_s: float, poll_s: float = 0.5) -> tuple[bool, dict]:
    deadline = time.monotonic() + timeout_s
    t: dict = {}
    while time.monotonic() < deadline:
        t = api.telemetry()
        if predicate(t):
            return True, t
        time.sleep(poll_s)
    return False, t


def speed(t: dict) -> float:
    return abs(float(t.get("velocity") or 0.0))


def summary(status: dict) -> dict:
    stats = status.get("stats") or {}
    return {
        "state": status.get("state"),
        "message": status.get("message"),
        "gates": f'{status.get("gates_passed")}/{status.get("total_gates")}',
        "race_time_s": status.get("race_time_s"),
        "passes": status.get("passes"),
        "setpoints_sent": stats.get("setpoints_sent"),
        "setpoints_rejected": stats.get("setpoints_rejected"),
        "setpoints_expired": stats.get("setpoints_expired"),
        "recoveries": stats.get("recoveries"),
        "avg_tick_ms": stats.get("avg_tick_ms"),
        "max_tracking_error_m": stats.get("max_tracking_error_m"),
    }


# -- GCS-side PX4 access (parameters, battery drain) ---------------------------
async def _gcs(gcs_url: str, params: dict[str, float], ints: dict[str, int]) -> None:
    from mavsdk import System

    drone = System(port=50052)  # its own mavsdk_server, separate from the backend's
    await drone.connect(system_address=gcs_url)
    async for state in drone.core.connection_state():
        if state.is_connected:
            break
    for name, value in ints.items():
        await drone.param.set_param_int(name, value)
    for name, value in params.items():
        await drone.param.set_param_float(name, value)


def gcs(gcs_url: str, params: dict[str, float] | None = None, ints: dict[str, int] | None = None) -> None:
    asyncio.run(asyncio.wait_for(_gcs(gcs_url, params or {}, ints or {}), 60))


# -- the scenario ------------------------------------------------------------------
def start_race(api: Api, course_id: str, finish: str, speed_m_s: float) -> dict:
    return api.post(
        "/api/vantarace/start",
        {"course_id": course_id, "perception": "vision", "speed_m_s": speed_m_s, "finish": finish},
    )


def race_one(api: Api, args) -> None:
    """Line course: offboard, pause/resume, stale vision, low confidence,
    expired setpoints, frozen state, completion."""
    res = start_race(api, "sitl_line_3", "hold", args.speed)
    record("race1_start", bool(res.get("accepted")), required=True, message=res.get("message"))
    ok, st = wait_race(api, lambda s: s.get("state") == "RUNNING", 90)
    record("race1_offboard_engaged", ok, required=True, state=st.get("state"), message=st.get("message"))
    t = api.telemetry()
    record("race1_px4_in_offboard", t.get("flight_mode") == "OFFBOARD", flight_mode=t.get("flight_mode"))

    ok, st = wait_race(api, lambda s: (s.get("gates_passed") or 0) >= 1 or s.get("state") not in ("RUNNING", "RECOVERING"), 90)
    record("race1_gate1_passed_by_vision", ok and st.get("gates_passed", 0) >= 1, **summary(st))

    # Pause holds position with offboard kept alive; resume continues.
    res = api.post("/api/vantarace/pause")
    record("pause_accepted", bool(res.get("accepted")), message=res.get("message"))
    time.sleep(3.0)
    t1 = api.telemetry()
    time.sleep(2.0)
    t2 = api.telemetry()
    drift = math.dist((t1["x"], t1["y"], t1["altitude"]), (t2["x"], t2["y"], t2["altitude"]))
    record("pause_holds_position", drift < 0.6 and speed(t2) < 0.6 and t2.get("flight_mode") == "OFFBOARD",
           drift_m=round(drift, 2), speed=speed(t2), flight_mode=t2.get("flight_mode"))
    res = api.post("/api/vantarace/resume")
    ok, st = wait_race(api, lambda s: s.get("state") in ("RUNNING", "RECOVERING"), 5)
    record("resume_continues", bool(res.get("accepted")) and ok, state=st.get("state"))

    # Stale vision: no frames for 3 s -> hold -> resume.
    time.sleep(1.0)
    before = api.race()
    res = api.post("/api/vantarace/fault", {"kind": "vision_blackout", "duration_s": 3.0})
    ok_hold, st = wait_race(api, lambda s: s.get("state") == "RECOVERING" or s.get("perception_status") == "COMMITTED", 3.5)
    ok_back, st2 = wait_race(api, lambda s: s.get("state") == "RUNNING" and s.get("perception_status") == "TRACKING", 8)
    record("stale_vision_holds_then_resumes", bool(res.get("accepted")) and ok_hold and ok_back,
           during=st.get("state"), perception=st.get("perception_status"), after=st2.get("state"),
           recoveries=(st2.get("stats") or {}).get("recoveries"),
           note="COMMITTED means the gate was inside the commit zone, so the loop flew through on its last estimate",
           gates_before=before.get("gates_passed"))

    # Low-confidence detections -> execution HOLD -> resume.
    res = api.post("/api/vantarace/fault", {"kind": "low_confidence", "duration_s": 3.0, "value": 0.05})
    ok_hold, st = wait_race(api, lambda s: s.get("state") == "RECOVERING", 3.5)
    ok_back, st2 = wait_race(api, lambda s: s.get("state") == "RUNNING", 8)
    record("low_confidence_holds_then_resumes", bool(res.get("accepted")) and ok_hold and ok_back,
           execution=((st.get("autonomy") or {}).get("execution") or {}).get("mode"), after=st2.get("state"))

    # Late setpoints must be refused by the flight core, never flown.
    expired_before = (api.race().get("stats") or {}).get("setpoints_expired", 0)
    res = api.post("/api/vantarace/fault", {"kind": "setpoint_delay", "duration_s": 0.5, "value": 0.6})
    time.sleep(2.0)
    st = api.race()
    expired = (st.get("stats") or {}).get("setpoints_expired", 0) - expired_before
    t = api.telemetry()
    record("expired_setpoints_refused", bool(res.get("accepted")) and expired > 0 and st.get("state") in ("RUNNING", "RECOVERING")
           and t.get("flight_mode") == "OFFBOARD", expired=expired, state=st.get("state"), flight_mode=t.get("flight_mode"))

    # Frozen vehicle state -> hold last point -> continue.
    res = api.post("/api/vantarace/fault", {"kind": "state_stall", "duration_s": 1.0})
    ok_hold, st = wait_race(api, lambda s: s.get("state") == "HOLDING_STALE", 1.5, poll_s=0.1)
    ok_back, st2 = wait_race(api, lambda s: s.get("state") in ("RUNNING", "RECOVERING"), 4)
    record("stale_state_holds_then_continues", bool(res.get("accepted")) and ok_hold and ok_back, after=st2.get("state"))

    ok, st = wait_race(api, lambda s: s.get("state") not in ("RUNNING", "RECOVERING", "PAUSED", "HOLDING_STALE", "PREPARING"), 150)
    record("race1_complete", st.get("state") == "COMPLETE" and st.get("gates_passed") == st.get("total_gates"), **summary(st))
    t = api.telemetry()
    record("race1_finish_leaves_offboard", t.get("flight_mode") == "HOLD", flight_mode=t.get("flight_mode"))


def race_two(api: Api, args) -> None:
    """Slalom: lost/reacquired gate, completion."""
    res = start_race(api, "sitl_slalom_4", "hold", args.speed)
    record("race2_start", bool(res.get("accepted")), required=True, message=res.get("message"))
    ok, st = wait_race(api, lambda s: s.get("state") == "RUNNING", 60)
    record("race2_running", ok, required=True, state=st.get("state"), message=st.get("message"))
    time.sleep(0.5)
    res = api.post("/api/vantarace/fault", {"kind": "occlude_gate", "duration_s": 4.0})
    ok_lost, st = wait_race(api, lambda s: s.get("perception_status") == "LOST", 3.0, poll_s=0.1)
    ok_rec, _ = wait_race(api, lambda s: s.get("state") == "RECOVERING", 3.0, poll_s=0.1)
    ok_back, st2 = wait_race(api, lambda s: s.get("state") == "RUNNING" and s.get("perception_status") in ("TRACKING", "COMMITTED"), 10)
    record("lost_gate_reacquired", bool(res.get("accepted")) and ok_lost and ok_rec and ok_back,
           lost=ok_lost, recovering=ok_rec, reacquired=ok_back,
           reacquisitions=(st2.get("stats") or {}).get("reacquisitions"))
    ok, st = wait_race(api, lambda s: s.get("state") not in ("RUNNING", "RECOVERING", "PAUSED", "HOLDING_STALE", "PREPARING"), 180)
    record("race2_complete", st.get("state") == "COMPLETE" and st.get("gates_passed") == st.get("total_gates"), **summary(st))


def race_three(api: Api, args) -> None:
    """Battery failsafe during a race, then return home and a clean landing."""
    res = start_race(api, "sitl_line_3", "land", args.speed)
    record("race3_start", bool(res.get("accepted")), required=True, message=res.get("message"))
    ok, st = wait_race(api, lambda s: s.get("state") == "RUNNING", 60)
    record("race3_running", ok, required=True, state=st.get("state"))
    if args.adapter == "px4_sitl":
        # Real PX4 battery drain over the GCS link: the FailsafeGuardian must notice.
        gcs(args.gcs, params={"SIM_BAT_DRAIN": 20.0, "SIM_BAT_MIN_PCT": 0.0})
    else:
        api.post("/api/sim/fault", {"kind": "battery", "value": 20.0})
    ok, st = wait_race(api, lambda s: s.get("state") not in ("RUNNING", "RECOVERING", "PAUSED", "HOLDING_STALE"), 120, poll_s=0.5)
    fs = api.get("/api/failsafe")
    trig = fs.get("last_trigger") or {}
    record("battery_failsafe_aborts_race",
           st.get("state") == "ABORTED" and "failsafe" in (st.get("message") or "")
           and trig.get("reason") in ("battery_low", "battery_critical"),
           state=st.get("state"), message=st.get("message"), trigger=trig)
    ok, t = wait_telemetry(api, lambda t: t.get("flight_mode") in ("RETURNING", "LANDING") or t.get("altitude", 9) < 1.0, 30)
    record("battery_failsafe_returns_home", ok, flight_mode=t.get("flight_mode"))
    ok, t = wait_telemetry(api, lambda t: not t.get("armed"), 180, poll_s=1.0)
    dist = math.hypot(t.get("x", 99), t.get("y", 99))
    record("clean_landing", ok and t.get("altitude", 9) < 1.0 and dist < 3.0,
           distance_from_home_m=round(dist, 2), altitude=t.get("altitude"), armed=t.get("armed"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--gcs", default="udpin://0.0.0.0:14550", help="second MAVLink link for parameters")
    ap.add_argument("--speed", type=float, default=3.0)
    ap.add_argument("--adapter", default="px4_sitl", choices=["px4_sitl", "mock"],
                    help="mock = dry run of this script against the built-in simulator")
    ap.add_argument("--json", default=None, help="write the full report here")
    args = ap.parse_args()
    api = Api(args.api)
    t0 = time.monotonic()
    try:
        health = api.get("/api/health")
        record("backend", health.get("status") == "ok", required=True, version=health.get("version"))
        if args.adapter == "px4_sitl":
            # No RC in SITL: let offboard run without a radio link (sim only).
            gcs(args.gcs, ints={"COM_RCL_EXCEPT": 4, "NAV_RCL_ACT": 0, "NAV_DLL_ACT": 0})
            record("px4_params", True, COM_RCL_EXCEPT=4)

        res = api.post("/api/connect", {"adapter_type": args.adapter})
        record("connect", bool(res.get("accepted")), required=True, message=res.get("message"))
        ok, t = wait_telemetry(api, lambda t: t.get("connected") and t.get("position_available"), 30)
        record("telemetry", ok, required=True, position=[t.get("x"), t.get("y"), t.get("altitude")])
        pre = api.get("/api/preflight")
        record("preflight", pre.get("critical_failures", 1) == 0,
               warnings=[c["name"] for c in pre.get("checks", []) if not c["passed"]])

        race_one(api, args)
        race_two(api, args)
        race_three(api, args)

        res = api.post("/api/disconnect")
        record("disconnect", bool(res.get("accepted")), message=res.get("message"))
        flights = api.get("/api/replay/flights")["flights"]
        fid = max(flights, key=lambda f: f["flight_id"])["flight_id"]
        loaded = api.post(f"/api/replay/load/{fid}")
        rec = api.get(f"/api/vantarace/recording/{fid}?kind=race.sample&limit=1")
        sample = (rec.get("samples") or [{}])[-1].get("data", {})
        timeline = loaded.get("timeline", {})
        record("replay_has_loop_data",
               loaded.get("status") == "loaded" and all(k in sample for k in ("vision", "state", "planner", "execution")),
               flight_id=fid, frames=timeline.get("total_frames"), race_samples=rec.get("count"))
    except Prerequisite as exc:
        print(json.dumps({"aborted": str(exc)}), flush=True)

    passed = sum(1 for r in RESULTS if r["ok"])
    report = {
        "result": "PASS" if passed == len(RESULTS) else "FAIL",
        "passed": passed,
        "checks": len(RESULTS),
        "duration_s": round(time.monotonic() - t0, 1),
        "failed": [r["check"] for r in RESULTS if not r["ok"]],
    }
    print(json.dumps(report), flush=True)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"summary": report, "checks": RESULTS}, fh, indent=2)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
