"""Tests for PX4SITLAdapter with mocked MAVSDK client."""
from __future__ import annotations

import pytest

from vantaflight.adapters.px4_sitl import PX4SITLAdapter
from vantaflight.mavlink.mavsdk_client import MAVSDKClient, MAVSDKError, PX4Telemetry
from vantaflight.models import FlightMode, ConnectionQuality


class MockMAVSDKClient:
    """Mock that simulates MAVSDK client behavior without PX4."""

    def __init__(self, fail_connect: bool = False, fail_arm: bool = False) -> None:
        self._connected = False
        self._telemetry = PX4Telemetry()
        self._fail_connect = fail_connect
        self._fail_arm = fail_arm

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def telemetry(self) -> PX4Telemetry:
        return self._telemetry

    async def connect(self) -> None:
        if self._fail_connect:
            raise MAVSDKError("PX4_CONNECTION_TIMEOUT", "simulated timeout")
        self._connected = True
        self._telemetry.connected = True
        self._telemetry.health_all_ok = True
        self._telemetry.battery_remaining_percent = 95.0

    async def disconnect(self) -> None:
        self._connected = False
        self._telemetry = PX4Telemetry()

    async def arm(self) -> None:
        if self._fail_arm:
            raise MAVSDKError("PX4_COMMAND_REJECTED", "arm rejected by PX4")
        self._telemetry.armed = True

    async def disarm(self) -> None:
        self._telemetry.armed = False

    async def takeoff(self, altitude_m: float = 5.0) -> None:
        self._telemetry.in_air = True
        self._telemetry.relative_altitude_m = altitude_m
        self._telemetry.flight_mode = "TAKEOFF"

    async def hold(self) -> None:
        self._telemetry.flight_mode = "HOLD"

    async def land(self) -> None:
        self._telemetry.flight_mode = "LAND"
        self._telemetry.in_air = False
        self._telemetry.relative_altitude_m = 0.0
        self._telemetry.armed = False


@pytest.fixture
def mock_client() -> MockMAVSDKClient:
    return MockMAVSDKClient()


@pytest.fixture
def px4_adapter(mock_client) -> PX4SITLAdapter:
    return PX4SITLAdapter(mavsdk_client=mock_client)


async def test_px4_connect(px4_adapter, mock_client):
    assert px4_adapter.connected is False
    await px4_adapter.connect()
    assert px4_adapter.connected is True


async def test_px4_connect_failure():
    client = MockMAVSDKClient(fail_connect=True)
    adapter = PX4SITLAdapter(mavsdk_client=client)
    with pytest.raises(MAVSDKError) as exc_info:
        await adapter.connect()
    assert exc_info.value.code == "PX4_CONNECTION_TIMEOUT"


async def test_px4_disconnect(px4_adapter):
    await px4_adapter.connect()
    await px4_adapter.disconnect()
    assert px4_adapter.connected is False


async def test_px4_arm(px4_adapter):
    await px4_adapter.connect()
    await px4_adapter.arm()
    t = px4_adapter.get_telemetry()
    assert t.armed is True


async def test_px4_arm_rejected():
    client = MockMAVSDKClient(fail_arm=True)
    adapter = PX4SITLAdapter(mavsdk_client=client)
    await adapter.connect()
    with pytest.raises(MAVSDKError) as exc_info:
        await adapter.arm()
    assert exc_info.value.code == "PX4_COMMAND_REJECTED"


async def test_px4_takeoff(px4_adapter):
    await px4_adapter.connect()
    await px4_adapter.arm()
    await px4_adapter.takeoff(target_altitude_m=10.0)
    t = px4_adapter.get_telemetry()
    assert t.altitude == pytest.approx(10.0)
    assert t.flight_mode == FlightMode.TAKEOFF


async def test_px4_hold(px4_adapter):
    await px4_adapter.connect()
    await px4_adapter.arm()
    await px4_adapter.takeoff(target_altitude_m=5.0)
    await px4_adapter.hold()
    t = px4_adapter.get_telemetry()
    assert t.flight_mode == FlightMode.HOLD


async def test_px4_land(px4_adapter):
    await px4_adapter.connect()
    await px4_adapter.arm()
    await px4_adapter.takeoff(target_altitude_m=5.0)
    await px4_adapter.land()
    t = px4_adapter.get_telemetry()
    assert t.altitude == pytest.approx(0.0)
    assert t.armed is False


async def test_px4_telemetry_normalization(px4_adapter, mock_client):
    await px4_adapter.connect()
    mock_client._telemetry.latitude_deg = 47.397742
    mock_client._telemetry.longitude_deg = 8.545594
    mock_client._telemetry.relative_altitude_m = 15.3
    mock_client._telemetry.heading_deg = 270.5
    mock_client._telemetry.ground_speed_m_s = 5.2
    mock_client._telemetry.velocity_north_m_s = 3.0
    mock_client._telemetry.velocity_east_m_s = 4.0
    mock_client._telemetry.velocity_down_m_s = -1.0
    mock_client._telemetry.battery_remaining_percent = 82.5

    t = px4_adapter.get_telemetry()
    assert t.connected is True
    assert t.latitude == pytest.approx(47.397742)
    assert t.longitude == pytest.approx(8.545594)
    assert t.altitude == pytest.approx(15.3)
    assert t.heading == pytest.approx(270.5, abs=0.1)
    assert t.ground_speed == pytest.approx(5.2)
    assert t.velocity > 0
    assert t.battery_percentage == pytest.approx(82.5)
    assert t.connection_quality == ConnectionQuality.EXCELLENT


async def test_px4_connection_loss(px4_adapter, mock_client):
    await px4_adapter.connect()
    mock_client._connected = False
    t = px4_adapter.get_telemetry()
    assert t.connected is False


async def test_px4_capabilities(px4_adapter):
    caps = px4_adapter.get_capabilities()
    assert caps.name == "PX4 SITL"
    assert caps.adapter_type == "px4_sitl"
    assert caps.is_simulated is True
    assert caps.supports_gps is True
    assert "gps" in caps.supported_capabilities
    assert "arm" in caps.supported_capabilities


async def test_px4_full_flight_flow(px4_adapter):
    await px4_adapter.connect()
    assert px4_adapter.connected is True

    await px4_adapter.arm()
    t = px4_adapter.get_telemetry()
    assert t.armed is True

    await px4_adapter.takeoff(target_altitude_m=5.0)
    t = px4_adapter.get_telemetry()
    assert t.altitude == pytest.approx(5.0)

    await px4_adapter.hold()
    t = px4_adapter.get_telemetry()
    assert t.flight_mode == FlightMode.HOLD

    await px4_adapter.land()
    t = px4_adapter.get_telemetry()
    assert t.altitude == pytest.approx(0.0)
    assert t.armed is False

    await px4_adapter.disconnect()
    assert px4_adapter.connected is False
