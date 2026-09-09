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

### 2. Install MAVSDK Python

```bash
pip install mavsdk
```

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
