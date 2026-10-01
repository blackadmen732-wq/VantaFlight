# VantaFlight Project Status

## Current Version: 0.4.0 — Autonomy & Link Guardian

### V0.4

**Missions** (see [MISSIONS.md](MISSIONS.md))
- [x] Waypoint missions in local metres (same plan on mock and PX4)
- [x] Plan checks: geofence, altitude, speed, battery reserve, time estimate
- [x] Patterns: square, orbit, survey (lawnmower)
- [x] Start / pause / resume / abort, pilot override, leg timeouts
- [x] `goto` and `return_home` on both adapters (PX4 via MAVSDK goto_location / RTL)
- [x] Mission planner UI with live validation and 3D waypoint preview

**Routing engine** (see [ROUTING.md](ROUTING.md))
- [x] No-fly zones (circles and polygons, convex or not) with a safety margin
- [x] Shortest safe paths: visibility graph + A* (within 0.15% of the exact optimum)
- [x] Best visiting order: exact Held-Karp up to 12 stops, 2-opt/Or-opt with restarts beyond
- [x] Battery budget per stop and point-of-no-return warning
- [x] Zone-aware return home (button and failsafes)
- [x] Zone checks on goto commands and mission plans
- [x] QGroundControl `.plan` export/import (PX4 and ArduPilot compatible)
- [x] Route planner UI, zone editor, zones drawn in the digital twin

**Failsafes & safety envelope**
- [x] Geofence enforced on takeoff, goto and missions
- [x] Automatic land on critical battery, return home on low battery or geofence breach
- [x] Hold on stale telemetry; failsafe banner in the UI
- [x] Fault injection on the simulator (battery, link stall) with a Sim Lab panel

**Connection**
- [x] `link_age_s` on telemetry; PX4 link quality graded by message age
- [x] PX4 telemetry x/y reported in local metres (was raw lat/lon degrees)
- [x] Slow WebSocket clients dropped instead of stalling everyone
- [x] Telemetry loop survives errors instead of stopping silently
- [x] Recent events and mission status replayed to (re)connecting clients
- [x] UI commands never throw: network and validation errors become messages
- [x] Python 3.10 support fixed (`asyncio.timeout` was 3.11-only)

**Testing**
- [x] 213 backend tests (missions, routing, failsafes, navigation, PX4, QGC, API, hub)
- [x] 29 frontend tests (mission panel, routing, airspace, API error handling)

## Previous: 0.3.0 — Simulation Control Foundation

### What's Done

**Job 1: Hardened Flight Core**
- [x] Explicit session state machine (NO_SESSION → CONNECTED → ACTIVE → COMPLETED/INTERRUPTED)
- [x] Flight ID cleared after session end
- [x] No double termination
- [x] Interrupted flights stay interrupted on disconnect
- [x] Reconnect creates new flight ID
- [x] Commands rejected after session end
- [x] Telemetry not written after termination
- [x] Duplicate connection_loss events prevented
- [x] Idempotent disconnect
- [x] CORS restricted to localhost origins (configurable)
- [x] Central config.py for all env vars

**Job 2: PX4 SITL + MAVSDK Control**
- [x] PX4SITLAdapter implementing DroneAdapter
- [x] MAVSDKClient wrapper with lazy import
- [x] Configurable PX4 SITL endpoint
- [x] Telemetry normalization (PX4 → VantaFlight model)
- [x] Connect/disconnect/arm/disarm/takeoff/hold/land
- [x] ConnectionManager upgraded for adapter selection
- [x] Capabilities system strengthened

**Job 3: Digital Twin + Dashboard**
- [x] Digital twin package (state, trajectory buffer, session tracking)
- [x] Twin consumes normalized Telemetry only
- [x] Run summary with duration, max altitude/speed, battery delta
- [x] React dashboard with adapter selector
- [x] Three.js digital twin 3D visualization
- [x] Simulation Lab page
- [x] Run summary card
- [x] Diagnostics panel
- [x] React Router navigation
- [x] WebSocket reconnect with exponential backoff

**Testing**
- [x] 34+ backend tests (lifecycle, PX4 adapter, digital twin, config, API)
- [x] 8 frontend tests (types, components)
- [x] All tests pass without PX4 running

**Infrastructure**
- [x] Simulation scripts (launch_sitl.sh, check_environment.sh)
- [x] Documentation (ARCHITECTURE, STATUS, SIMULATION, PX4, SAFETY)
- [x] SQLite schema migration (v1 → v2)
- [x] Version bumped to 0.3.0 everywhere

### What's Not In V0.4

- Physical drone control (hardware MAVLink)
- Autonomous racing logic
- Authentication / cloud services
- AI / voice / swarm features
- Camera integration
- Saving mission plans on the server (use .plan export/import)
- Altitude-limited airspace and live obstacle avoidance

### Known Limitations

- PX4 SITL requires external PX4-Autopilot installation
- Three.js bundle adds ~700KB to the frontend build
- Mock adapter uses simplified physics (no wind, no inertia)
- Mission battery estimates use the simulator's drain rate
- The PX4 mission path is unit-tested against a mocked MAVSDK client; it has
  not been flown against a live PX4 SITL in CI
- The `mavsdk` Python package must be installed separately for PX4 mode
