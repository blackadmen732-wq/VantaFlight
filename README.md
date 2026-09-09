# VantaFlight

VantaFlight is a **local-first** autonomous competition drone software platform.
It runs entirely on your machine — no internet, no cloud, no accounts required.

This repository currently contains the **foundation**: a drone-agnostic flight
core, a simulated (mock) drone, live telemetry streaming, safety-checked flight
commands, a simple desktop UI, and local SQLite recording of every flight.

> Scope note: this is the stable base. Real drone backends (PX4/ArduPilot),
> computer vision, digital twin, simulation, competition logic, and packaging
> are intentionally **not** part of this foundation and will come later.

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

You need Python 3.10+ and Node.js 18+.

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
reconnect, plus the HTTP/WebSocket API.

Frontend type checking:

```bash
cd frontend
npm run typecheck
```
