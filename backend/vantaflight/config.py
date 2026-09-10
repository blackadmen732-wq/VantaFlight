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

SOFTWARE_VERSION: str = "0.3.0"
