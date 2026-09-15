# VantaFlight on Chromebook Linux

VantaFlight is designed to run locally inside ChromeOS Linux (Crostini) while opening its operator UI in Chrome. This keeps the flight runtime local and preserves browser features such as Web Bluetooth when an official Hopper browser transport is available.

## Install

From the repository root:

```bash
bash scripts/install_chromebook_linux.sh
```

The installer creates:

- `backend/.venv`
- Python dependencies
- frontend `node_modules`
- a Linux launcher named **VantaFlight** in the ChromeOS Linux apps menu

## Launch

Use the VantaFlight icon in the Linux apps launcher or run:

```bash
bash scripts/vantaflight-desktop.sh
```

The launcher starts the FastAPI backend on `127.0.0.1:8000`, the Vite frontend on `127.0.0.1:5173`, waits for the backend health endpoint, and opens the UI in Chrome/Chromium app mode when available.

## Hopper connection model

Hopper is intentionally split into independent links:

- **Camera/Wi-Fi**: observe-only camera path
- **Telemetry**: normalized only when the active official interface exposes real values
- **Program deployment**: remains `UNKNOWN` until a supported deployment transport exists
- **Live control**: remains disabled unless an official FTW transport explicitly exposes the command capability

VantaFlight never fabricates a battery, position, altitude, velocity, command acknowledgement, or successful deployment.

## Hardware validation ladder

The desktop UI/software may be used before real control is enabled. Report progress separately as:

1. IMPLEMENTED
2. UNIT_TESTED
3. INTEGRATION_TESTED
4. FAST_SIM_VERIFIED
5. SITL_VERIFIED
6. REAL_CAMERA_VERIFIED
7. HOPPER_OBSERVE_VERIFIED
8. HOPPER_PROGRAM_VERIFIED
9. HOPPER_LIVE_CONTROL_VERIFIED
10. HOPPER_CLOSED_LOOP_VERIFIED
11. COMPETITION_VERIFIED

Do not claim a higher level from a lower one.
