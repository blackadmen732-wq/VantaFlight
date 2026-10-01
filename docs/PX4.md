# PX4 Integration Guide

## Overview

VantaFlight V0.3 adds PX4 SITL (Software-In-The-Loop) support through the
MAVSDK Python library. The integration is simulation-only — no physical
drone connections are implemented.

## Architecture

```
VantaFlight ←→ MAVSDKClient ←→ MAVSDK library ←→ MAVLink ←→ PX4 SITL
```

The `MAVSDKClient` class (`vantaflight/mavlink/mavsdk_client.py`) is the
only module that imports `mavsdk`. It provides:

- `connect()` / `disconnect()` — lifecycle management
- `arm()` / `disarm()` — motor control
- `takeoff()` / `hold()` / `land()` — flight commands
- `PX4Telemetry` — raw telemetry data from PX4

The `PX4SITLAdapter` wraps the client and normalizes telemetry into
VantaFlight's standard `Telemetry` model.

## Setup

### 1. Install PX4-Autopilot

```bash
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
cd PX4-Autopilot
bash Tools/setup/ubuntu.sh  # Ubuntu/Debian
make px4_sitl gazebo-classic
```

Or, without building PX4, run the headless container (PX4 v1.14 + Gazebo,
the same one the V1 end-to-end flight was verified on):

```bash
docker run --rm --network host --log-driver none \
    jonasvautherin/px4-gazebo-headless:1.14.3 127.0.0.1 127.0.0.1
```

`--log-driver none` matters: the simulator logs several GB within minutes.

### 2. Install MAVSDK Python

```bash
pip install -r backend/requirements-px4.txt   # "mavsdk>=2,<4"
```

MAVSDK-Python 4 renamed the gRPC wrapper (`mavsdk-grpc`) and the `mavsdk`
name now points at a different native API, so VantaFlight pins `mavsdk<4`.

### 3. Configure VantaFlight

Set the environment variable for PX4 mode:
```bash
export VANTAFLIGHT_PX4_URL=udpin://0.0.0.0:14540
```

Or use the adapter selector in the dashboard.

## Supported Commands

| Command | PX4 Action |
|---------|-----------|
| connect | MAVSDK System.connect(), wait for health |
| arm | Action.arm() |
| disarm | Action.disarm() |
| takeoff | Action.set_takeoff_altitude() + Action.takeoff() |
| hold | Action.hold() |
| land | Action.land() |

## Telemetry Mapping

| VantaFlight Field | PX4 Source |
|-------------------|-----------|
| altitude | Telemetry.position().relative_altitude_m |
| heading | Telemetry.heading().heading_deg |
| velocity | sqrt(vn² + ve² + vd²) from velocity_ned |
| ground_speed | Telemetry.fixedwing_metrics().ground_speed |
| battery_percentage | Battery.remaining_percent |
| latitude / longitude | Telemetry.position() |
| armed | Telemetry.armed() |
| flight_mode | Telemetry.flight_mode() |

## Error Codes

| Code | Meaning |
|------|---------|
| PX4_NOT_FOUND | MAVSDK package not installed |
| PX4_CONNECTION_TIMEOUT | Could not connect within timeout |
| PX4_HEALTH_NOT_READY | Vehicle health checks not passing |
| PX4_COMMAND_REJECTED | PX4 rejected an action command |

## Testing

All PX4 tests use a `MockMAVSDKClient` that simulates MAVSDK behavior.
No PX4 instance is needed to run tests:

```bash
cd backend && pytest tests/test_px4_adapter.py -v
```


## End-to-end verification

`simulation/px4/e2e_flight.py` flies the whole V1 path against a live PX4 SITL
through the backend's HTTP API, exactly as the UI does:

connect → telemetry → preflight → arm → takeoff → 30 m square mission →
low-battery failsafe (PX4's simulated battery is drained with `SIM_BAT_DRAIN`)
→ return home → land → disconnect → replay.

```bash
python simulation/px4/e2e_flight.py --api http://127.0.0.1:8000
```

Result on V1.0.0 (PX4 v1.14.3, Gazebo headless, MAVSDK-Python 3.17):
**15/15 stages passed** in 143 s. The mission flew 4/4 waypoints in 39 s, the
`battery_low` failsafe fired at 25 % and returned home, and the aircraft
landed 0.15 m from home. The replay held 1,429 frames, 18 events and 9
commands. The run also caught a real bug: MAVSDK 2+ reports battery as
0..100, which VantaFlight was multiplying by 100 again.
