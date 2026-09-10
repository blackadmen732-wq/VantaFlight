# VantaFlight Architecture (V0.3)

## Overview

VantaFlight is a local-first drone simulation and control platform. It runs
entirely on one machine with no cloud dependency. The system connects to
either a built-in mock adapter or a PX4 SITL instance via MAVLink.

## System Layers

```
┌──────────────────────────────────────┐
│           React Dashboard            │  TypeScript / Vite
│  (controls, twin view, sim lab)      │  Port 5173
├──────────────────────────────────────┤
│          FastAPI + WebSocket         │  Python / Uvicorn
│     (REST commands, telemetry WS)    │  Port 8000
├──────────────────────────────────────┤
│          Flight Controller           │
│  (session state, safety, sampling)   │
├──────────────────────────────────────┤
│       Connection Manager             │
│  (adapter discovery & selection)     │
├──────────┬───────────────────────────┤
│ MockDrone│    PX4SITLAdapter         │  DroneAdapter interface
│ Adapter  │    + MAVSDKClient         │
├──────────┴───────────────────────────┤
│          Digital Twin                │
│  (state, trajectory, session)        │
├──────────────────────────────────────┤
│       SQLite (WAL mode)              │
│  (flights, telemetry, events)        │
└──────────────────────────────────────┘
```

## Key Design Decisions

### Adapter Abstraction
All drone interaction goes through the `DroneAdapter` protocol. The mock
adapter and PX4 SITL adapter implement identical interfaces. The digital
twin and flight controller never know which adapter is active.

### Session State Machine
The flight controller tracks explicit session states:
`NO_SESSION → CONNECTED → ACTIVE → COMPLETED` (or `INTERRUPTED`).
This prevents state leaks between flights and ensures clean lifecycle
management.

### Digital Twin
The twin consumes normalized `Telemetry` objects, not raw MAVSDK data.
This keeps it adapter-agnostic and testable without PX4.

### MAVSDK Boundary
The `MAVSDKClient` class is the only code that imports `mavsdk`. It can be
injected (for testing) or created with a `MAVLinkConfig`. The lazy import
means the `mavsdk` package is only required when actually connecting to PX4.

### Central Configuration
All environment variable lookups live in `vantaflight/config.py`. No other
module reads `os.environ` directly.

## Directory Structure

```
backend/
  vantaflight/
    adapters/         # DroneAdapter implementations
    connection/       # ConnectionManager, discovery
    core/             # FlightController, safety
    data/             # SQLite database
    digital_twin/     # Twin state, trajectory, session
    mavlink/          # MAVSDKClient wrapper
    models/           # Telemetry, FlightMode, etc.
    config.py         # Central configuration
    main.py           # FastAPI app factory
  tests/              # pytest suite
frontend/
  src/
    components/       # AdapterSelector, DigitalTwin, etc.
    pages/            # SimulationLab
    App.tsx           # Main app with routing
    api.ts            # REST + WebSocket client
    types.ts          # TypeScript type definitions
simulation/           # SITL scripts and config
docs/                 # Project documentation
```

## Data Flow

1. User selects an adapter (Mock or PX4 SITL) in the dashboard
2. `/api/connect` creates the adapter via ConnectionManager
3. FlightController opens a session, starts the digital twin
4. Commands flow REST → FlightController → Adapter
5. Telemetry flows Adapter → FlightController → WebSocket → Dashboard
6. Twin state updates on each telemetry sample
7. SQLite records telemetry samples and flight events
8. On disconnect, a run summary is computed and available via REST
