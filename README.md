# VantaFlight

VantaFlight is a **local-first** autonomous competition drone software platform.
It runs entirely on your machine — no internet, no cloud, no accounts required.

It contains a drone-agnostic flight core, a simulated drone and a PX4 SITL
adapter, live telemetry streaming, safety-checked flight commands,
**autonomous waypoint missions**, **automatic failsafes**, a 3D digital twin,
a desktop UI, and local SQLite recording of every flight.

> Scope note: VantaFlight is for simulation only. Physical aircraft, computer
> vision, competition logic, and packaging are not supported yet.

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

See [docs/MISSIONS.md](docs/MISSIONS.md) for the details.

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
│       ├── mission/             Waypoint plans, checks, patterns, runner
│       ├── data/database.py     SQLite (WAL) local persistence
│       ├── core/flight_controller.py  Orchestrator
│       └── main.py              FastAPI HTTP + WebSocket server
├── frontend/                    Desktop UI (React, TypeScript, Vite)
│   ├── src/                     App, telemetry stream, controls, timeline
│   └── src-tauri/               Tauri packaging scaffold (not yet built)
└── scripts/install.sh           One-shot local setup
```

The rest of VantaFlight only ever depends on the `DroneAdapter` abstraction, so
adding PX4, ArduPilot, USB/serial, radio, UDP, or TCP later means implementing
that one contract — nothing else changes.

## Running VantaFlight locally (Linux / Chromebook Linux)

You need Python 3.10+ and Node.js 20+ (Node 22 recommended for full test suite).

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
