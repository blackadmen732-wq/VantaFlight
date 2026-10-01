# VantaFlight

VantaFlight is a **local-first** autonomous competition drone software platform.
It runs entirely on your machine — no internet, no cloud, no accounts required.

**Version 1.0.0.** This repository contains the drone-agnostic Flight Core,
mock, PX4 SITL and Hopper adapters, autonomous missions with failsafes, a
routing engine, the live Digital Twin, VantaForge course generation, training
and evolution, replay, and the desktop app (browser or Tauri AppImage/.deb).
See [docs/STATUS.md](docs/STATUS.md) for what is verified at which level.

> Safety: autonomous flight is simulation-only. VantaFlight emits normalized
> trajectory/setpoint models, never raw motor PWM. PX4 remains responsible for
> stabilization and motor control. Hopper is observe-only: live control stays
> disabled until FTW publishes a control interface.

## What works today

Open the app, and you can:

1. See **Searching for aircraft**.
2. **Connect** to a simulated drone.
3. Watch live telemetry: connected status, battery, altitude, speed, position,
   flight mode, and armed/disarmed state.
4. **Arm** the drone.
5. Press **Takeoff** and watch the altitude rise.
6. Press **Hold** to loiter.
7. Press **Land** and watch it descend and auto-disarm.
8. The entire run is saved locally to SQLite (`backend/vantaflight.db`).

And autonomously:

9. Pick a **Square**, **Orbit** or **Survey** pattern (or edit waypoints) in the
   **Mission** panel, see the distance, time and battery estimate, and press
   **Start mission**. The drone takes off, flies the plan, and comes home.
10. If the battery runs low, the aircraft leaves the geofence, or telemetry
    goes stale, a **failsafe** returns it home, lands it, or holds it, and
    the UI shows why. Rehearse each one from **Sim Lab → Failsafe Rehearsal**.

11. Mark **no-fly zones**, then press **Optimize route**: VantaFlight finds the
    shortest safe order and path through your stops, flies around the zones,
    and warns you if any stop is past the battery's point of no return.
    Export the result as a QGroundControl `.plan`, or import one.

See [docs/MISSIONS.md](docs/MISSIONS.md) and [docs/ROUTING.md](docs/ROUTING.md)
for the details.

And in the other workspaces:

12. **Mission**: plan and fly waypoint missions and save typed missions
    (search & rescue, delivery, inspection, race) that survive restarts.
13. **Twin**: the live Digital Twin, with the actual and planned path,
    zones and geofence, in 3D, top or side view.
14. **VantaForge**: generate a race course in a safe volume, inspect its
    stats, projections and difficulty, and train on that course type.
15. **Evolution**: see where the autonomy stack fails most across training
    runs, and check whether a challenger really beats the champion.
16. **Replay** any recorded flight frame by frame.

Safety rules reject invalid commands (e.g. takeoff while disconnected, takeoff
before arming, disarm while airborne) and the UI recovers cleanly from a
simulated connection loss.

## Architecture

```
vantaflight/
├── backend/                     Flight Core (Python, FastAPI, WebSockets)
│   └── vantaflight/
│       ├── models/telemetry.py  Normalized, drone-agnostic data models
│       ├── adapters/base.py     DroneAdapter — the universal drone API
│       ├── adapters/mock.py     MockDroneAdapter (simulated physics)
│       ├── connection/manager.py ConnectionManager (discovery + lifecycle)
│       ├── safety/validator.py  Command safety rules
│       ├── safety/failsafe.py   Automatic battery/geofence/link failsafes
│       ├── mission/             Waypoint plans, checks, patterns, runner, QGC files
│       ├── routing/             No-fly zones, safe paths, route optimizer
│       ├── data/database.py     SQLite (WAL) local persistence
│       ├── core/flight_controller.py  Orchestrator
│       ├── vision/              Camera, detection, pose, tracking, fusion
│       ├── racing/              Trajectory, speed envelope, race states
│       ├── course_lab/          Course generation, analysis, experiments
│       └── main.py              FastAPI HTTP + WebSocket server
├── frontend/                    Desktop UI (React, TypeScript, Vite)
│   ├── src/                     App, telemetry stream, controls, timeline
│   └── src-tauri/               Tauri desktop app (AppImage, .deb)
└── scripts/install.sh           One-shot local setup
```

The rest of VantaFlight only ever depends on the `DroneAdapter` abstraction, so
adding PX4, ArduPilot, USB/serial, radio, UDP, or TCP later means implementing
that one contract — nothing else changes.

## Running VantaFlight locally (Linux / Chromebook Linux)

You need Python 3.10+ and Node.js 20.19+ or 22.12+.

### 1. Install dependencies

```bash
bash scripts/install.sh
```

This creates the Python virtualenv in `backend/.venv` and installs the frontend
packages. (It will `apt-get install python3-venv` if your system lacks it.)

### 2. Start the Flight Core (terminal 1)

```bash
cd backend
source .venv/bin/activate
python -m uvicorn vantaflight.main:app --host 127.0.0.1 --port 8000
```

### 3. Start the Desktop UI (terminal 2)

```bash
cd frontend
npm run dev
```

### 4. Open the app

Open <http://127.0.0.1:5173> in your browser. The Vite dev server proxies API
and WebSocket traffic to the Flight Core automatically.

Then: **Connect → Arm → Takeoff → Hold → Land**. Every run is recorded to
`backend/vantaflight.db`.

### Desktop app

Build the AppImage and `.deb` (needs Rust and the Tauri system packages,
`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`):

```bash
cd frontend
npx tauri build
sudo apt install ./src-tauri/target/release/bundle/deb/VantaFlight_1.0.0_amd64.deb
```

The app starts and stops its own Flight Core; the first launch sets up a
Python venv in `~/.local/share/com.vantaflight.app` (it needs `python3-venv`).
Uninstall with `sudo apt remove vantaflight`; your flights stay in that folder.

### PX4 SITL

See [docs/PX4.md](docs/PX4.md). `pip install -r backend/requirements-px4.txt`,
start PX4 SITL, pick **PX4 SITL** in the adapter selector, and connect.
`simulation/px4/e2e_flight.py` flies the whole path end to end.

## Tests

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

Covers connect/disconnect, arm/disarm, takeoff/hold/land, invalid-command
rejection, telemetry, SQLite recording, simulated connection loss, and
reconnect, missions, failsafes, PX4 coordinate conversion, plus the
HTTP/WebSocket API.

Frontend type checking and tests:

```bash
cd frontend
npm run typecheck
npm test
```
