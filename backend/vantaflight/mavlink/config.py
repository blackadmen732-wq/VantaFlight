"""MAVSDK connection configuration."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import PX4_SITL_URL, PX4_CONNECTION_TIMEOUT


@dataclass(frozen=True)
class MAVLinkConfig:
    system_address: str = PX4_SITL_URL
    connection_timeout: float = PX4_CONNECTION_TIMEOUT
    health_timeout: float = 10.0
