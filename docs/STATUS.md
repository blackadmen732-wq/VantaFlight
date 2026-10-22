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
| VantaRace / vision autonomy loop | INTEGRATION_TESTED: launchable from the Race page; full loop (VantaSight → VantaState → VantaRace → VantaExecution → offboard) flown closed-loop on the mock, all fault cases ([VANTARACE_SITL.md](VANTARACE_SITL.md)). PX4 SITL harness and CI job written; **not yet SITL_VERIFIED** (Actions not running) |
| Bus, runtime, health, authority ([RUNTIME.md](RUNTIME.md)) | UNIT_TESTED, INTEGRATION_TESTED on the mock; not SITL_VERIFIED |
| Time and contracts (VantaClock, typed versioned envelopes, legacy migration) | UNIT_TESTED, INTEGRATION_TESTED on the mock ([CONTRACTS.md](CONTRACTS.md)); not SITL_VERIFIED |
| Training campaigns, weakness map, champion/challenger | INTEGRATION_TESTED |
| Hopper camera-only mode | INTEGRATION_TESTED; needs REAL_CAMERA / HOPPER_OBSERVE on hardware |
| Hopper live control | Disabled: FTW's control protocol is undocumented |
| Desktop app (AppImage, .deb) | Built, installed, launched, closed (backend stopped), reinstalled and removed on Ubuntu 24.04 under Xvfb; not yet on a Chromebook |

`SITL_VERIFIED` means `simulation/px4/e2e_flight.py` passed against PX4 v1.14.3
with Gazebo (15/15 stages; see [PX4.md](PX4.md#end-to-end-verification)).

### VantaRace closed loop (on top of V1.0.0)

- [x] Race page: course, perception, speed, finish; start/pause/resume/abort;
      live loop panel; course map; fault rehearsal
- [x] `RaceSession`: 20 Hz loop, start-line positioning and gate alignment,
      commit zone, stale-state hold, setpoint delivery through the flight core
- [x] Flight-core setpoint gate (expiry, speed, geofence, no-fly); pilot
      overrides, failsafes and disconnects abort the race
- [x] Recording (schema v5 `autonomy_samples`) and replay frames
- [x] Fixed: VantaRace paths cut outside turning gates (waypoints were
      look-ahead blends, not gate centres); VantaExecution output ENU labelled
      as NED; PX4 offboard started with a setpoint at the ground (0,0,0);
      AutonomyLoop left RECOVERING while still holding; simulated camera had
      no mount transform and painted far gates over near ones
- [x] Fixed: a race ended outside the loop (operator abort, pilot override,
      failsafe) never recorded its final event or summary; a v0.3 test still
      treated PX4 OFFBOARD as an unknown mode. Both were caught only once the
      TestClient suites ran locally (see "How these were tested" below).
- [ ] SITL_VERIFIED: `simulation/px4/race_e2e.py` against PX4 + Gazebo
      (`.github/workflows/sitl.yml`) — pending a passing run
- Correction: the earlier FAST_SIM_VERIFIED label for this loop overstated
  it — the fast-sim runner never ran VantaRace (it flew straight at gates)
  and `AutonomyLoop` had no tests.

### Engineering spec, PR B: bus, runtime, health, authority (stacked on PR A)

- [x] VantaSystem composition root; VantaBus with LATEST / BOUNDED_STREAM /
      RELIABLE_EVENT channels and per-topic rate, age, depth, drops and lag
- [x] Camera frames on bounded LATEST channels (a queue deeper than 2 is refused)
- [x] Flight log, commands, failsafes and authority decisions on reliable
      channels persisted by the flight database; a full backlog is refused loudly
- [x] VantaRuntime: every long-running task registered (enforced by a source
      test), deadlines and restart policies, ordered shutdown to zero workers
- [x] Flight tick separated from the UI stream (a slow browser no longer delays failsafes)
- [x] VantaHealth: per-dimension health for bus, workers, vehicle link,
      battery/position availability, recorder (`/api/health/components`)
- [x] VantaAuthority: leases on every offboard setpoint; operator and failsafe
      preempt automation; agents never get physical execution authority
- Tested: 778 backend tests pass on the local stand-in runner (54 new). Not SITL_VERIFIED.

### Engineering spec, PR A: time and contracts (stacked on the VantaRace branch)

- [x] `VantaClock`: named clock domains; mixing clocks raises; host monotonic
      for every deadline and age; `ClockSync` maps vehicle and camera clocks
      onto host time with drift, outlier and reset handling and a reported
      uncertainty (`/api/clock`)
- [x] PX4 attitude timestamps feed a `VEHICLE_BOOT` sync; link age uses the
      monotonic clock instead of wall time
- [x] VantaContracts: `EventEnvelope` (ids, run, per-topic sequence, three
      timestamps, frame, calibration, model versions, quality), frame-tagged
      vectors, units in every float field, versioned registry with migrations
      (`/api/contracts`)
- [x] Race recordings are envelopes; PR #8 rows migrate from v0 with
      `LEGACY_UNVERSIONED`; undecodable rows are counted (`decode_errors`),
      not fatal
- [ ] Publishing telemetry, command and failsafe contracts on a bus (PR B)

How these were tested: GitHub Actions is locked by billing and this sandbox
has no package index, so the suite ran under a local stand-in for pytest,
pytest-asyncio, Hypothesis and FastAPI's TestClient. On that runner the full
backend suite passes (724 tests, 60 of them new in PR A). The earlier local
runner skipped test classes and every TestClient suite, and that gap hid the
two VantaRace bugs above. Rerun with real pytest once CI is unlocked.

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
