# Missions, Failsafes, and Link Health (V0.4)

V0.4 turns VantaFlight from "a remote control with a dashboard" into an
aircraft that can fly a plan on its own and protect itself when something goes
wrong. Everything here works the same on the Mock Drone and on PX4 SITL.

## Coordinates

Missions use **local metres relative to home** (where the session started):

| Field      | Meaning               |
|------------|-----------------------|
| `x`        | metres **east** of home  |
| `y`        | metres **north** of home |
| `altitude` | metres above home        |

The PX4 adapter converts these to and from latitude/longitude around PX4's home
position, so the same plan flies identically on either adapter. Telemetry `x`
and `y` are reported in the same frame (before V0.4, PX4 reported raw degrees
there, which put the digital twin in the wrong place).

## Flying a mission

1. **Connect** and **Arm**.
2. In the **Mission** panel, start from a pattern (Square, Orbit, Survey) or
   edit waypoints by hand. The plan is checked as you type: distance,
   estimated time, estimated battery use, and anything that would make it
   unflyable (outside the geofence, too low, not enough battery).
3. Press **Start mission**. If the aircraft is on the ground it takes off to the
   first waypoint's altitude, then flies each waypoint in order, waits out any
   `hold_s`, and finishes by returning home, landing, or hovering.
4. **Pause** holds position; **Resume** continues to the same waypoint;
   **Abort** stops the mission and holds.

Any manual flight command (Hold, Land, Return home, …) while a mission runs is
a **pilot override**: the mission is aborted and your command wins.

The runner also aborts on its own if the link is lost, the aircraft disarms,
or a leg takes far longer than it should (3× its nominal time + 20 s), and
then holds position.

### Plan format

```json
{
  "name": "Inspect the barn",
  "speed_m_s": 5,
  "finish": "return_home",          // or "land" | "hold"
  "waypoints": [
    {"x": 20, "y": 0,  "altitude": 12},
    {"x": 20, "y": 20, "altitude": 12, "hold_s": 10, "speed_m_s": 3}
  ]
}
```

### API

| Method | Path                     | Purpose                                   |
|--------|--------------------------|-------------------------------------------|
| POST   | `/api/mission/validate`  | Check a plan; returns errors and estimates |
| POST   | `/api/mission/pattern`   | `{kind, params}` → a generated plan        |
| POST   | `/api/mission/start`     | Fly a plan                                 |
| POST   | `/api/mission/pause`     | Hold and pause                             |
| POST   | `/api/mission/resume`    | Continue a paused mission                  |
| POST   | `/api/mission/abort`     | Stop and hold                              |
| GET    | `/api/mission`           | Current mission status                     |
| POST   | `/api/return-home`       | Fly home and land                          |

Mission progress is also pushed on the WebSocket as `{"type": "mission"}` frames.

Patterns: `square` (`size_m`, `altitude_m`), `orbit` (`radius_m`,
`altitude_m`, `points`), `survey` (`width_m`, `height_m`, `spacing_m`,
`altitude_m`); all accept `center_x`/`center_y`.

## Failsafes

The `FailsafeGuardian` checks every telemetry sample while the aircraft is
armed and airborne. Highest priority first:

| Condition                              | Action        | Default        |
|----------------------------------------|---------------|----------------|
| Battery ≤ critical                     | Land in place | 12%            |
| Battery ≤ low                          | Return home   | 25%            |
| Outside the geofence                   | Return home   | 150 m / 100 m  |
| No fresh telemetry for the stale time  | Hold          | 3 s            |

A failsafe aborts any running mission, is logged as a `failsafe` event (and
shown as a red banner in the UI), and fires once per flight. The link rule
re-arms once telemetry is fresh again. If an aircraft has no native return
home, "return home" falls back to landing in place.

The same geofence also rejects `takeoff` and `goto` commands and mission plans
that would leave it, before anything is sent to the aircraft.

## Link health

Telemetry now carries `link_age_s`: seconds since the last message actually
received from the aircraft. For PX4 it is measured from the MAVSDK streams and
grades `connection_quality` (≤0.5 s EXCELLENT, ≤1 s GOOD, ≤2 s FAIR, else POOR).
The UI shows the age next to the quality badge once it passes 1 s.

The server side of the connection was hardened too:

- One slow or dead browser tab is dropped after 1 s instead of stalling
  telemetry for every client.
- An error in one telemetry tick is logged and the loop carries on (before, it
  silently stopped telemetry, failsafes and missions for the whole session).
- A client that connects or reconnects is sent the last 50 events and the
  mission status, so a page reload doesn't lose the flight's history.

## Rehearsing failures (Sim Lab)

With the Mock Drone connected, the **Failsafe Rehearsal** card in Sim Lab can
drop the battery to 20% or 10% or stall the telemetry link for 5 s, so you can
watch each failsafe respond. The same is available as
`POST /api/sim/fault {"kind": "battery" | "link_stall", "value": n}`; it is
refused for anything but the simulator.

## Configuration

| Variable                              | Default |
|---------------------------------------|---------|
| `VANTAFLIGHT_GEOFENCE_RADIUS_M`       | 150     |
| `VANTAFLIGHT_GEOFENCE_MAX_ALT_M`      | 100     |
| `VANTAFLIGHT_BATTERY_LOW_PCT`         | 25      |
| `VANTAFLIGHT_BATTERY_CRITICAL_PCT`    | 12      |
| `VANTAFLIGHT_TELEMETRY_STALE_TIMEOUT` | 3.0     |
| `VANTAFLIGHT_RTL_ALT_M`               | 10      |
| `VANTAFLIGHT_MISSION_ACCEPT_RADIUS_M` | 1.0     |
| `VANTAFLIGHT_MISSION_SPEED_M_S`       | 5       |
| `VANTAFLIGHT_MISSION_MAX_SPEED_M_S`   | 15      |
| `VANTAFLIGHT_MISSION_MAX_WAYPOINTS`   | 100     |
