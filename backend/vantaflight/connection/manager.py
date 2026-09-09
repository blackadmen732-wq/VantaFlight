"""Discovery and connection lifecycle for drone adapters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..adapters import DroneAdapter, MockDroneAdapter, PX4SITLAdapter
from ..config import PX4_SITL_URL
from ..mavlink import MAVLinkConfig
from ..models import AdapterType


class TransportType(str, Enum):
    SIMULATED = "SIMULATED"
    PX4_SITL = "PX4_SITL"
    SERIAL = "SERIAL"
    RADIO = "RADIO"
    UDP = "UDP"
    TCP = "TCP"


@dataclass(frozen=True)
class DiscoveredDrone:
    drone_id: str
    name: str
    transport: TransportType
    address: str
    adapter_type: AdapterType = AdapterType.MOCK


_BUILTIN_SOURCES = [
    DiscoveredDrone(
        drone_id="mock-0",
        name="Mock Drone (Simulator)",
        transport=TransportType.SIMULATED,
        address="sim://mock-0",
        adapter_type=AdapterType.MOCK,
    ),
    DiscoveredDrone(
        drone_id="px4-sitl-0",
        name="PX4 SITL",
        transport=TransportType.PX4_SITL,
        address=PX4_SITL_URL,
        adapter_type=AdapterType.PX4_SITL,
    ),
]


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
        return list(_BUILTIN_SOURCES)

    def _build_adapter(self, drone: DiscoveredDrone) -> DroneAdapter:
        if self._adapter_factory is not None:
            return self._adapter_factory(drone)
        if drone.adapter_type == AdapterType.MOCK or drone.transport == TransportType.SIMULATED:
            return MockDroneAdapter(adapter_id=drone.drone_id)
        if drone.adapter_type == AdapterType.PX4_SITL or drone.transport == TransportType.PX4_SITL:
            cfg = MAVLinkConfig(system_address=drone.address)
            return PX4SITLAdapter(adapter_id=drone.drone_id, config=cfg)
        raise NotImplementedError(
            f"transport {drone.transport.value} is not supported yet"
        )

    async def connect(self, drone: DiscoveredDrone | None = None) -> DroneAdapter:
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
