# VantaFlight Simulation Tooling

This directory contains scripts and configuration for running VantaFlight
with simulated drone backends.

## Quick Start (Mock Adapter)

No external simulator needed. VantaFlight ships with a built-in mock
adapter that simulates basic flight behavior:

```bash
cd backend && uvicorn vantaflight.main:app --reload
cd frontend && npm run dev
```

## PX4 SITL

For higher-fidelity simulation with PX4:

1. Clone and build PX4-Autopilot (see `docs/PX4.md`)
2. `./px4/launch_sitl.sh`
3. Select "PX4 SITL" in the VantaFlight adapter selector
4. Connect and fly

## Environment Check

Run `./check_environment.sh` to verify your dev machine has the required
tools installed.

## Configuration

See `config/sitl_defaults.env` for available environment variables.
Copy it to the project root as `.env` and adjust values as needed.
