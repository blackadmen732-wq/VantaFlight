"""MAVSDK connection configuration."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from ..config import PX4_SITL_URL, PX4_CONNECTION_TIMEOUT


class PhysicalMAVLinkBlocked(ValueError):
    """Raised when a simulation-only adapter is pointed at a physical link."""


@dataclass(frozen=True)
class MAVLinkConfig:
    system_address: str = PX4_SITL_URL
    connection_timeout: float = PX4_CONNECTION_TIMEOUT
    health_timeout: float = 10.0
    simulation_only: bool = True

    def __post_init__(self) -> None:
        if not self.simulation_only:
            raise PhysicalMAVLinkBlocked(
                "physical MAVLink connections are not supported"
            )
        validate_sitl_address(self.system_address)


def validate_sitl_address(address: str) -> None:
    """Allow only local UDP PX4 SITL endpoints.

    Serial devices, TCP endpoints, remote hosts, and non-SITL UDP port ranges
    are rejected before MAVSDK is imported or a connection is attempted.
    """
    parsed = urlsplit(address)
    if parsed.scheme not in {"udp", "udpin", "udpout"}:
        raise PhysicalMAVLinkBlocked(
            "V0.5 autonomous control is simulation-only; only UDP SITL is allowed"
        )
    host = parsed.hostname
    if host not in {None, "", "0.0.0.0", "127.0.0.1", "::", "::1", "localhost"}:
        raise PhysicalMAVLinkBlocked(
            "V0.5 autonomous control cannot connect to a non-local MAVLink host"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise PhysicalMAVLinkBlocked("invalid SITL MAVLink endpoint") from exc
    if port is None or not 14540 <= port <= 14580:
        raise PhysicalMAVLinkBlocked(
            "V0.5 autonomous control requires a PX4 SITL UDP port in 14540..14580"
        )
