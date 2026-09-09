# VantaFlight Simulation Guide

## Simulation Modes

### 1. Mock Adapter (Default)

The mock adapter runs entirely in-process. No external software needed.
It simulates:
- Connection/disconnection
- Arm/disarm
- Takeoff to a target altitude
- Hold position
- Landing
- Battery drain over time
- Basic telemetry (position, altitude, heading, velocity)

Select "Mock" in the adapter selector or set:
```
VANTAFLIGHT_DEFAULT_ADAPTER=mock
```

### 2. PX4 SITL

For higher-fidelity simulation, VantaFlight connects to a PX4 Software-In-The-Loop
instance via MAVLink/MAVSDK.

**Requirements:**
- PX4-Autopilot (cloned and built)
- Python `mavsdk` package: `pip install mavsdk`
- A simulator backend (Gazebo Classic, jMAVSim, or headless)

**Quick Start:**
```bash
# Terminal 1: Start PX4 SITL
cd PX4-Autopilot
make px4_sitl gazebo-classic

# Terminal 2: Start VantaFlight backend
cd backend
VANTAFLIGHT_DEFAULT_ADAPTER=px4_sitl uvicorn vantaflight.main:app --reload

# Terminal 3: Start VantaFlight frontend
cd frontend
npm run dev
```

Then select "PX4 SITL" in the adapter selector and click Connect.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VANTAFLIGHT_PX4_SITL_URL` | `udpin://0.0.0.0:14540` | MAVLink endpoint |
| `VANTAFLIGHT_PX4_CONNECTION_TIMEOUT` | `15.0` | Seconds before connection timeout |
| `VANTAFLIGHT_DEFAULT_ADAPTER` | `mock` | Default adapter on startup |
| `VANTAFLIGHT_STREAM_HZ` | `10` | Telemetry stream rate |

## Chromebook Notes

VantaFlight is designed to run on Chromebooks via Linux (Crostini):
- The mock adapter works with no additional setup
- PX4 SITL can run in the Linux container but Gazebo GUI needs X11 forwarding
- jMAVSim is lighter weight and may work better on constrained hardware
- The web dashboard runs in Chrome on the host OS at `http://localhost:5173`

## Troubleshooting

**PX4 won't connect:** Check that the SITL is running and the MAVLink port
(default 14540) is accessible. Verify with:
```bash
python3 -c "import mavsdk; print('mavsdk installed')"
```

**Telemetry not updating:** The PX4 SITL adapter starts streaming telemetry
after a successful connection. If the dashboard shows "CONNECTED" but
metrics don't update, check the backend logs for MAVSDK errors.

**High latency:** On constrained hardware, reduce `VANTAFLIGHT_STREAM_HZ`
to 5 or lower.
