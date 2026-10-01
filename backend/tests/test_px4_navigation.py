"""PX4 adapter: local<->global conversion, goto/RTL, and link grading."""
from __future__ import annotations

import asyncio
import time

import pytest

from vantaflight.adapters.px4_sitl import PX4SITLAdapter, global_to_local, local_to_global
from vantaflight.mavlink.mavsdk_client import MAVSDKError, PX4Telemetry, _first_matching
from vantaflight.models import ConnectionQuality, FlightMode

HOME = (47.397742, 8.545594)  # PX4 SITL default home (Zurich)


class NavClient:
    def __init__(self) -> None:
        self.connected = True
        self.telemetry = PX4Telemetry(
            connected=True, health_all_ok=True,
            home_latitude_deg=HOME[0], home_longitude_deg=HOME[1],
            home_absolute_altitude_m=488.0,
        )
        self.calls: list[tuple] = []

    async def goto_location(self, lat, lon, alt, yaw=float("nan")):
        self.calls.append(("goto", lat, lon, alt))

    async def set_speed(self, speed):
        self.calls.append(("speed", speed))

    async def return_to_launch(self):
        self.calls.append(("rtl",))


@pytest.fixture
async def adapter():
    a = PX4SITLAdapter(mavsdk_client=NavClient())
    a._connected = True
    return a


@pytest.mark.parametrize("x, y", [(0, 0), (100, 0), (0, -100), (-75.5, 42.25)])
def test_local_global_round_trip(x, y):
    lat, lon = local_to_global(x, y, *HOME)
    assert global_to_local(lat, lon, *HOME) == pytest.approx((x, y), abs=1e-6)


def test_one_degree_of_latitude_is_about_111_km():
    _, y = global_to_local(HOME[0] + 1, HOME[1], *HOME)
    assert y == pytest.approx(111_320, rel=0.01)


async def test_goto_converts_to_global_and_sets_speed(adapter):
    await adapter.goto(10.0, 20.0, 15.0, speed_m_s=7.0)
    calls = adapter._client.calls
    assert calls[0] == ("speed", 7.0)
    _, lat, lon, alt = calls[1]
    assert global_to_local(lat, lon, *HOME) == pytest.approx((10.0, 20.0), abs=1e-6)
    assert alt == pytest.approx(503.0)


async def test_goto_without_home_is_refused(adapter):
    adapter._client.telemetry.home_latitude_deg = None
    with pytest.raises(MAVSDKError, match="home position"):
        await adapter.goto(1, 1, 5)


async def test_return_home(adapter):
    await adapter.return_home()
    assert adapter._client.calls == [("rtl",)]


async def test_telemetry_positions_are_local_metres(adapter):
    lat, lon = local_to_global(30.0, -12.0, *HOME)
    adapter._client.telemetry.latitude_deg = lat
    adapter._client.telemetry.longitude_deg = lon
    t = adapter.get_telemetry()
    assert (t.x, t.y) == pytest.approx((30.0, -12.0), abs=0.01)
    assert (t.latitude, t.longitude) == (lat, lon)


async def test_link_age_degrades_quality(adapter):
    tel = adapter._client.telemetry
    tel.last_message_at = time.time()
    assert adapter.get_telemetry().connection_quality == ConnectionQuality.EXCELLENT
    tel.last_message_at = time.time() - 1.5
    t = adapter.get_telemetry()
    assert t.connection_quality == ConnectionQuality.FAIR
    assert t.link_age_s == pytest.approx(1.5, abs=0.1)
    tel.last_message_at = time.time() - 10
    assert adapter.get_telemetry().connection_quality == ConnectionQuality.POOR
    tel.health_all_ok = False
    tel.last_message_at = time.time()
    assert adapter.get_telemetry().connection_quality == ConnectionQuality.GOOD


def test_mode_mapping_for_missions_and_rtl():
    assert PX4SITLAdapter._map_flight_mode("FlightMode.RETURN_TO_LAUNCH", True) == FlightMode.RETURNING
    assert PX4SITLAdapter._map_flight_mode("FlightMode.MISSION", True) == FlightMode.MISSION


def test_capabilities_advertise_navigation():
    caps = PX4SITLAdapter(mavsdk_client=NavClient()).get_capabilities()
    assert caps.supports_goto and caps.supports_return


async def _items(*values, delay=0.0):
    for v in values:
        await asyncio.sleep(delay)
        yield v


async def test_first_matching_returns_first_hit():
    assert await _first_matching(_items(1, 2, 3, 4), lambda v: v > 2, timeout=1) == 3


async def test_first_matching_times_out():
    with pytest.raises(asyncio.TimeoutError):
        await _first_matching(_items(1, 2, delay=0.5), lambda v: v > 5, timeout=0.1)


async def test_first_matching_stream_end_is_an_error():
    with pytest.raises(ConnectionError):
        await _first_matching(_items(1), lambda v: False, timeout=1)
