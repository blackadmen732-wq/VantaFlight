# VantaFlight Project Status

## Current Version: 1.0.0

V1 brings together the V0.9 platform (Hopper adapter, training engine, replay,
Digital Twin 2.0, hardware mode and preflight) and the V0.4 autonomy line
(waypoint missions, routing engine, failsafes, link health), and adds the
Mission, Twin, VantaForge and Evolution workspaces and the desktop app.

### Verification levels

Each claim is reported at the level it was actually reached (see
[CHROMEBOOK_LINUX.md](CHROMEBOOK_LINUX.md#hardware-validation-ladder)).

| Area | Level reached |
|------|---------------|
| Flight Core, safety rules, session state machine | UNIT_TESTED, INTEGRATION_TESTED, SITL_VERIFIED |
| Waypoint missions (square/orbit/survey) | SITL_VERIFIED |
| Battery failsafe → return home → land | SITL_VERIFIED |
| Geofence and stale-link failsafes | INTEGRATION_TESTED (mock), not yet flown on SITL |
| Routing engine and no-fly zones | INTEGRATION_TESTED |
| Replay of a recorded flight | SITL_VERIFIED |
| VantaRace / vision autonomy loop | FAST_SIM_VERIFIED; not started from the app against PX4 |
| Training campaigns, weakness map, champion/challenger | INTEGRATION_TESTED |
| Hopper camera-only mode | INTEGRATION_TESTED; needs REAL_CAMERA / HOPPER_OBSERVE on hardware |
| Hopper live control | Disabled: FTW's control protocol is undocumented |
| Desktop app (AppImage, .deb) | Built, installed, launched, closed (backend stopped), reinstalled and removed on Ubuntu 24.04 under Xvfb; not yet on a Chromebook |

`SITL_VERIFIED` means `simulation/px4/e2e_flight.py` passed against PX4 v1.14.3
with Gazebo (15/15 stages; see [PX4.md](PX4.md#end-to-end-verification)).

### V1.0.0

**Integration**
- [x] V0.4 missions, routing and failsafes merged into V0.9 without replacing
      its typed transport results, connect lock, hardware-mode gating or
      telemetry availability flags
- [x] Both mission systems kept: V0.4 waypoint plans/runner (`mission/checks.py`,
      `runner.py`) and V0.9 typed goals/registry (`mission/models.py`, `registry.py`)
- [x] Failsafes and plan checks ignore battery/position a link does not report
- [x] Persisted missions restored at startup; interrupted ones marked ABORTED
- [x] Review fixes from PR #6: refused commands are never reported as sent,
      Hopper camera-only links are not shown as disconnected, training runs are
      persisted without a fake course id, request models at module level

**Workspaces**
- [x] Mission: planner, airspace, digital twin, saved typed missions
- [x] Twin: live Digital Twin 2.0 snapshots with layer toggles, 3D/top/side
- [x] VantaForge: CourseLab-style course generation, stats, projections, validation
- [x] Evolution: weakness map and champion/challenger evaluation

**Desktop app**
- [x] Tauri AppImage and `.deb` with the VantaFlight icon
- [x] Backend lifecycle: private venv on first launch, start/stop with the app,
      reuse of an already running backend

**Quality**
- [x] Backend: 628 tests on Python 3.10, 3.11 and 3.12
- [x] Frontend: 38 tests, typecheck and production build clean
- [x] `npm audit`: 0 vulnerabilities (Vite 8, Vitest 5)
- [x] PX4 SITL end-to-end flight (found and fixed the MAVSDK battery scaling bug)

### Not in V1

- Live Hopper control or program deployment (no official transport)
- Physical autonomous flight of any kind
- Starting the VantaRace racing loop from the app against PX4
- Cloud services, accounts, swarm, voice

### Known Limitations

- PX4 mode needs `pip install -r backend/requirements-px4.txt` (`mavsdk>=2,<4`)
- Mock adapter uses simplified physics (no wind, no inertia)
- Mission battery estimates use the simulator's drain rate
- The desktop app needs `python3` and `python3-venv` on the system; the first
  launch downloads the backend's Python packages

## Previous versions

- **0.9.0**: Hopper adapter, Chromebook launcher, training engine, replay,
  Digital Twin 2.0, mission architecture, database V4.
- **0.5.0**: VantaSight vision, VantaRace, CourseLab, async recorder
  (see [V0.5_BACKEND_INTELLIGENCE.md](V0.5_BACKEND_INTELLIGENCE.md)).
- **0.4.0**: Waypoint missions, routing engine, failsafes, link health
  (see [MISSIONS.md](MISSIONS.md), [ROUTING.md](ROUTING.md)).
- **0.3.0**: Hardened Flight Core, PX4 SITL via MAVSDK, Digital Twin, dashboard.
