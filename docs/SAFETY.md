# VantaFlight Safety Documentation

## Scope

VantaFlight V0.3 operates exclusively in simulation. There is no support
for physical drone control, and no path from this software to commanding
a real aircraft.

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

- **arm**: Requires connected state, not already airborne
- **takeoff**: Requires armed state
- **hold**: Requires airborne state
- **land**: Requires airborne state
- **disarm**: Requires not airborne

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
- Geofencing
- Battery failsafe automation
- Pre-flight checklist enforcement
- Operator authentication
- Regulatory compliance verification
