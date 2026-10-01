"""Hard boundary preventing racing autonomy from controlling real aircraft."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse


class AutonomousExecutionRejected(RuntimeError):
    pass


@runtime_checkable
class ExecutionCapabilities(Protocol):
    is_simulated: bool
    adapter_type: str
    supported_capabilities: Sequence[str]


@dataclass(frozen=True)
class SimulationExecutionPermit:
    endpoint: str
    adapter_type: str
    _proof: object = field(repr=False, compare=False)


_PERMIT_PROOF = object()


class SimulationOnlyExecutionGuard:
    """Validates both advertised capability and transport destination."""

    _SIMULATOR_ADAPTERS = frozenset({"px4_sitl", "sitl", "simulation", "simulator", "mock"})
    _SIMULATION_CAPABILITIES = frozenset({"simulation", "simulator", "sitl"})
    _LOCAL_HOSTS = frozenset(
        {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}
    )
    _SITL_SCHEMES = frozenset({"udp", "udpin", "udpout"})
    _MIN_SITL_PORT = 14540
    _MAX_SITL_PORT = 14580

    @classmethod
    def authorize(
        cls,
        endpoint: str,
        capabilities: ExecutionCapabilities,
    ) -> SimulationExecutionPermit:
        """Return a permit only for an explicitly simulated local endpoint."""
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise AutonomousExecutionRejected("an explicit SITL endpoint is required")
        if not bool(getattr(capabilities, "is_simulated", False)):
            raise AutonomousExecutionRejected("physical aircraft capabilities are forbidden")

        adapter_type = str(getattr(capabilities, "adapter_type", "")).lower()
        advertised = {
            str(item).lower()
            for item in getattr(capabilities, "supported_capabilities", ())
        }
        if adapter_type not in cls._SIMULATOR_ADAPTERS:
            raise AutonomousExecutionRejected(f"adapter {adapter_type!r} is not an approved simulator")
        if not advertised.intersection(cls._SIMULATION_CAPABILITIES):
            raise AutonomousExecutionRejected("adapter does not advertise a simulation capability")

        parsed = urlparse(endpoint)
        if parsed.scheme.lower() not in cls._SITL_SCHEMES:
            raise AutonomousExecutionRejected("only UDP endpoints are approved for SITL")
        if parsed.hostname not in cls._LOCAL_HOSTS:
            raise AutonomousExecutionRejected("autonomous racing is restricted to local SITL endpoints")
        try:
            port = parsed.port
        except ValueError as error:
            raise AutonomousExecutionRejected("SITL endpoint port is invalid") from error
        if port is None or not cls._MIN_SITL_PORT <= port <= cls._MAX_SITL_PORT:
            raise AutonomousExecutionRejected("SITL UDP port must be in the range 14540..14580")
        return SimulationExecutionPermit(
            endpoint=endpoint,
            adapter_type=adapter_type,
            _proof=_PERMIT_PROOF,
        )

    @staticmethod
    def validate_permit(permit: SimulationExecutionPermit | None) -> SimulationExecutionPermit:
        """Reject missing or manually constructed execution claims."""
        if not isinstance(permit, SimulationExecutionPermit) or permit._proof is not _PERMIT_PROOF:
            raise AutonomousExecutionRejected(
                "an execution permit produced by SimulationOnlyExecutionGuard is required"
            )
        return permit
