# VantaFlight Project Status

## Current Version: 0.5.0 — Backend Intelligence

V0.5 is under review on its feature branch. The V0.3 Simulation Control
Foundation remains intact beneath the new packages.

### V0.5 implementation

- [x] Camera/profile/frame abstractions and synthetic/file sources
- [x] Bounded newest-frame buffering with stale-frame drops
- [x] Configurable OpenCV preprocessing and classical target detection
- [x] solvePnP pose estimation with reprojection validation
- [x] Timestamp-driven target tracking, optical flow, ROI policy, association
- [x] Fusion, bounded scene memory, latency prediction, lock state machine
- [x] Optional asynchronous neural detector interface (no bundled model)
- [x] VantaRace state machine, smooth trajectory, lookahead, speed envelope
- [x] Continuous confidence-aware aggression scaling
- [x] Simulation-only autonomous execution guard
- [x] Seeded path-first CourseLab with all seven mathematical modes
- [x] Course difficulty, run analysis, and bounded parameter experiments
- [x] Digital Twin simulator-truth validation boundary
- [x] Additive SQLite v3 schema and bounded asynchronous recorder
- [x] Typed V0.5 REST/WebSocket contracts without video on telemetry transport
- [x] Synthetic/property/lifecycle test coverage and benchmark command

### V0.5 verification

- Local automated test results are recorded in the pull request.
- `TESTED IN CI`: backend and frontend VantaFlight CI jobs pass on the PR.
- `SIMULATION VERIFIED`: not claimed until a live PX4/Gazebo run succeeds.
- `HARDWARE VERIFIED`: no V0.5 autonomous behavior.

See [V0.5 Backend Intelligence](V0.5_BACKEND_INTELLIGENCE.md) for the complete
scope/status matrix and safety boundaries.

### V0.3 Foundation (preserved)

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

### What's Not In V0.3

- Physical drone control (hardware MAVLink)
- Autonomous racing logic
- Authentication / cloud services
- AI / voice / swarm features
- Camera integration
- Waypoint missions
- Real GPS navigation

### Known Limitations

- PX4 SITL requires external PX4-Autopilot installation
- Three.js bundle adds ~700KB to the frontend build
- Mock adapter uses simplified physics (no wind, no inertia)
- The `mavsdk` Python package must be installed separately for PX4 mode
