"""Central VantaFlight configuration.

All environment-variable lookups live here. Every other module imports from
this single place so settings never scatter across the codebase.
"""
from __future__ import annotations

import os
from pathlib import Path


def _csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


STREAM_HZ: float = float(os.environ.get("VANTAFLIGHT_STREAM_HZ", "10"))

DB_PATH: str = os.environ.get(
    "VANTAFLIGHT_DB", str(Path.cwd() / "vantaflight.db")
)

CORS_ORIGINS: list[str] = _csv(
    os.environ.get(
        "VANTAFLIGHT_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
)

DEFAULT_ADAPTER: str = os.environ.get("VANTAFLIGHT_ADAPTER", "mock")

PX4_SITL_URL: str = os.environ.get(
    "VANTAFLIGHT_PX4_URL", "udpin://0.0.0.0:14540"
)

PX4_CONNECTION_TIMEOUT: float = float(
    os.environ.get("VANTAFLIGHT_PX4_TIMEOUT", "15")
)

TELEMETRY_STALE_TIMEOUT: float = float(
    os.environ.get("VANTAFLIGHT_TELEMETRY_STALE_TIMEOUT", "3.0")
)

# -- safety envelope & failsafes ---------------------------------------------
# Horizontal distance from home (metres) the aircraft may never exceed.
GEOFENCE_RADIUS_M: float = float(os.environ.get("VANTAFLIGHT_GEOFENCE_RADIUS_M", "150"))

# Altitude ceiling above home (metres).
GEOFENCE_MAX_ALTITUDE_M: float = float(
    os.environ.get("VANTAFLIGHT_GEOFENCE_MAX_ALT_M", "100")
)

# Battery level that triggers an automatic return-to-launch.
BATTERY_LOW_PCT: float = float(os.environ.get("VANTAFLIGHT_BATTERY_LOW_PCT", "25"))

# Battery level that forces an immediate landing wherever the aircraft is.
BATTERY_CRITICAL_PCT: float = float(
    os.environ.get("VANTAFLIGHT_BATTERY_CRITICAL_PCT", "12")
)

# Altitude used when flying home on return-to-launch.
RTL_ALTITUDE_M: float = float(os.environ.get("VANTAFLIGHT_RTL_ALT_M", "10"))

# -- missions -----------------------------------------------------------------
# A waypoint counts as reached inside this 3D radius (metres).
MISSION_ACCEPT_RADIUS_M: float = float(
    os.environ.get("VANTAFLIGHT_MISSION_ACCEPT_RADIUS_M", "1.0")
)

MISSION_DEFAULT_SPEED_M_S: float = float(
    os.environ.get("VANTAFLIGHT_MISSION_SPEED_M_S", "5")
)

MISSION_MAX_SPEED_M_S: float = float(
    os.environ.get("VANTAFLIGHT_MISSION_MAX_SPEED_M_S", "15")
)

MISSION_MAX_WAYPOINTS: int = int(os.environ.get("VANTAFLIGHT_MISSION_MAX_WAYPOINTS", "100"))

SOFTWARE_VERSION: str = "0.4.0"
