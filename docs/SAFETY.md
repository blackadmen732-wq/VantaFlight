# VantaFlight Safety Documentation

## Scope

VantaFlight V0.4 is intended exclusively for simulation. While the PX4
adapter connects through MAVSDK with a configurable endpoint URL, only
SITL (Software-In-The-Loop) use is supported and tested. Operators must
ensure the configured MAVLink endpoint points to a simulator, not a
physical vehicle. No safety interlocks for real hardware are implemented.

## Safety Boundaries

### What VantaFlight Controls
- Mock drone adapter (entirely in-process, no hardware)
- PX4 SITL connections (software simulation only)

### What VantaFlight Does NOT Control
- Physical drones or motors
- Real MAVLink hardware connections
- Any radio or telemetry hardware
- GPS receivers or other sensors

## Session Safety

The flight controller enforces a strict session state machine:

1. **NO_SESSION** — No active connection. Commands are rejected.
2. **CONNECTED** — Adapter connected. Arm is available.
3. **ACTIVE** — Armed or flying. Full command set available.
4. **INTERRUPTED** — Connection lost during flight. Commands rejected.
5. **COMPLETED** — Session ended cleanly. Commands rejected.

### State Transition Rules
- A completed or interrupted session cannot receive new commands
- Reconnecting after interruption creates a new session (new flight ID)
- Telemetry is not written to the database after session termination
- Duplicate connection loss events are suppressed
- Disconnect is idempotent (safe to call multiple times)

## Command Validation

The flight controller validates commands before forwarding to adapters:

- **arm**: Requires connected state, not already armed
- **takeoff**: Requires armed state, on the ground, target inside the geofence
- **hold**: Requires airborne state
- **land**: Requires airborne state
- **disarm**: Requires not airborne
- **goto**: Requires airborne state, altitude ≥ 1 m, target inside the geofence
- **return_home**: Requires airborne state
- **missions**: The whole plan is checked against the geofence and the live
  battery level (with the low-battery reserve) before launch

## Automatic Failsafes

While armed and airborne, every telemetry sample is checked by the
`FailsafeGuardian` (see [MISSIONS.md](MISSIONS.md#failsafes)): critical
battery lands, low battery and geofence breaches return home, and stale
telemetry holds position. Failsafes abort any running mission and are
recorded in the flight log.

## Data Integrity

- SQLite runs in WAL mode for crash safety
- Flight records are created atomically at connection time
- Telemetry samples are written with timestamps
- Run summaries are computed from recorded data
- Schema migrations are applied safely on startup

## Network Safety

- CORS is restricted to localhost origins by default
- No authentication is required (local-only operation)
- No data is sent to external services
- WebSocket connections are local only
- The backend binds to 127.0.0.1 by default

## Future Considerations

Before any physical drone support is added, the following must be
implemented:
- Hardware-in-the-loop safety layer
- Emergency stop / kill switch
- Pre-flight checklist enforcement
- Validation of the V0.4 geofence and battery failsafes against real
  airframes (battery estimates are tuned to the simulator)
- Operator authentication
- Regulatory compliance verification
