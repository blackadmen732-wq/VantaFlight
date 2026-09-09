"""Discovery and connection lifecycle for drone adapters.

For this foundation the manager only discovers the `MockDroneAdapter`, but the
design deliberately leaves room for USB/serial, telemetry radio, UDP, and TCP
transports: discovery yields transport-tagged descriptors, and a factory turns
a chosen descriptor into a concrete `DroneAdapter`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..adapters import DroneAdapter, MockDroneAdapter


class TransportType(str, Enum):
    """How we reach a drone. Only SIMULATED is implemented today."""

    SIMULATED = "SIMULATED"
    SERIAL = "SERIAL"  # USB / serial (future)
    RADIO = "RADIO"  # telemetry radio (future)
    UDP = "UDP"  # future
    TCP = "TCP"  # future


@dataclass(frozen=True)
class DiscoveredDrone:
    """A drone that discovery found and that can be connected to."""

    drone_id: str
    name: str
    transport: TransportType
    address: str  # e.g. "sim://mock-0", later "/dev/ttyUSB0" or "udp://:14550"


class ConnectionManager:
    """Discovers drones and hands back a connected adapter."""

    def __init__(
        self,
        adapter_factory: Callable[[DiscoveredDrone], DroneAdapter] | None = None,
    ) -> None:
        self._adapter: DroneAdapter | None = None
        self._active: DiscoveredDrone | None = None
        self._adapter_factory = adapter_factory

    async def discover(self) -> list[DiscoveredDrone]:
        """Return the drones currently reachable.

        Today this is always exactly one simulated drone. Future transports
        would append their own discovered descriptors here.
        """
        return [
            DiscoveredDrone(
                drone_id="mock-0",
                name="Mock Drone (Simulator)",
                transport=TransportType.SIMULATED,
                address="sim://mock-0",
            )
        ]

    def _build_adapter(self, drone: DiscoveredDrone) -> DroneAdapter:
        if self._adapter_factory is not None:
            return self._adapter_factory(drone)
        if drone.transport == TransportType.SIMULATED:
            return MockDroneAdapter(adapter_id=drone.drone_id)
        raise NotImplementedError(
            f"transport {drone.transport.value} is not supported yet"
        )

    async def connect(self, drone: DiscoveredDrone | None = None) -> DroneAdapter:
        """Connect to `drone` (or the first discovered one) and return its adapter."""
        if drone is None:
            found = await self.discover()
            if not found:
                raise RuntimeError("no drones discovered")
            drone = found[0]

        adapter = self._build_adapter(drone)
        await adapter.connect()
        self._adapter = adapter
        self._active = drone
        return adapter

    async def disconnect(self) -> None:
        if self._adapter is not None:
            await self._adapter.disconnect()
        self._adapter = None
        self._active = None

    @property
    def adapter(self) -> DroneAdapter | None:
        return self._adapter

    @property
    def active(self) -> DiscoveredDrone | None:
        return self._active
