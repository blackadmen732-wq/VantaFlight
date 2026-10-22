"""Tello adapter against a fake Tello speaking the SDK's UDP text protocol."""
from __future__ import annotations

import asyncio

import pytest

from vantaflight.adapters.tello import TelloAdapter, TelloConfig, _steps, parse_state
from vantaflight.connection import ConnectionManager
from vantaflight.core import FlightController
from vantaflight.data import FlightDatabase
from vantaflight.models import FlightMode


class FakeTello(asyncio.DatagramProtocol):
    """Answers SDK commands like the real drone and streams state on request."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.transport = None
        self.height_cm = 0
        self.battery = 87
        self.refuse: set[str] = set()
        self.silent = False

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data, addr) -> None:
        text = data.decode()
        self.received.append(text)
        if self.silent:
            return
        if text.endswith("?"):
            self.transport.sendto(str(self.battery).encode(), addr)
            return
        word = text.split()[0]
        if word in self.refuse:
            self.transport.sendto(b"error Not joystick", addr)
            return
        if word == "takeoff":
            self.height_cm = 100
        elif word == "up":
            self.height_cm += int(text.split()[1])
        elif word == "land":
            self.height_cm = 0
        self.transport.sendto(b"ok", addr)

    def state_line(self) -> bytes:
        return (f"pitch:0;roll:0;yaw:-90;vgx:3;vgy:4;vgz:0;templ:60;temph:62;tof:{self.height_cm + 10};"
                f"h:{self.height_cm};bat:{self.battery};baro:12.3;time:5;agx:0.0;agy:0.0;agz:-1000.0;\r\n").encode()


async def fake_tello():
    loop = asyncio.get_running_loop()
    fake = FakeTello()
    transport, _ = await loop.create_datagram_endpoint(lambda: fake, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    config = TelloConfig(host="127.0.0.1", command_port=port, state_port=0,
                         command_timeout_s=0.5, motion_timeout_s=0.5, keepalive_s=0.2)
    return fake, transport, config


async def push_state(fake: FakeTello, adapter: TelloAdapter) -> None:
    fake.transport.sendto(fake.state_line(), ("127.0.0.1", adapter.state_port))
    await asyncio.sleep(0.05)


def test_state_parsing_and_climb_steps():
    s = parse_state("pitch:1;roll:-2;h:130;bat:55;baro:-7.25;\r\n")
    assert s == {"pitch": 1.0, "roll": -2.0, "h": 130.0, "bat": 55.0, "baro": -7.25}
    assert parse_state("garbage") == {}
    assert _steps(400, 500) == [400]
    assert _steps(1010, 500) == [500, 490, 20]
    assert all(20 <= s <= 500 for s in _steps(1210, 500)) and sum(_steps(1210, 500)) == 1210


async def test_connect_fly_and_land_through_the_sdk():
    fake, transport, config = await fake_tello()
    adapter = TelloAdapter(config=config)
    try:
        await adapter.connect()
        assert fake.received == ["command"] and adapter.connected
        t = adapter.get_telemetry()
        assert t.connected and not t.battery_available and not t.position_available
        await push_state(fake, adapter)
        t = adapter.get_telemetry()
        assert t.battery_percentage == 87 and t.battery_available and t.altitude == 0.0
        assert t.velocity == pytest.approx(0.5) and t.heading == 270.0

        assert (await adapter.arm()).accepted and fake.received == ["command"], "arming sends nothing"
        result = await adapter.takeoff(target_altitude_m=3.0)
        assert result.accepted
        assert fake.received[1:] == ["takeoff", "up 200"]
        await push_state(fake, adapter)
        t = adapter.get_telemetry()
        assert t.altitude == pytest.approx(3.0) and t.airborne and t.flight_mode == FlightMode.HOLD

        assert (await adapter.hold()).accepted and fake.received[-1] == "stop"
        assert (await adapter.land()).accepted
        await push_state(fake, adapter)
        t = adapter.get_telemetry()
        assert t.altitude == 0.0 and not t.armed and t.flight_mode == FlightMode.IDLE
    finally:
        await adapter.disconnect()
        transport.close()


async def test_refusals_and_silence_are_reported_not_hidden():
    fake, transport, config = await fake_tello()
    adapter = TelloAdapter(config=config)
    try:
        await adapter.connect()
        fake.refuse.add("takeoff")
        result = await adapter.takeoff()
        assert not result.accepted and "Not joystick" in result.message
        fake.silent = True
        result = await adapter.land()
        assert not result.accepted and result.status.value == "TIMED_OUT"
    finally:
        await adapter.disconnect()
        transport.close()


async def test_no_tello_on_the_network_gives_a_clear_error():
    fake, transport, config = await fake_tello()
    fake.silent = True
    adapter = TelloAdapter(config=config)
    with pytest.raises(Exception, match="TELLO-XXXXXX"):
        await adapter.connect()
    assert not adapter.connected
    transport.close()


async def test_keepalive_stops_the_sdk_auto_landing():
    fake, transport, config = await fake_tello()
    adapter = TelloAdapter(config=config)
    try:
        await adapter.connect()
        await asyncio.sleep(0.3)
        adapter.get_telemetry()
        await asyncio.sleep(0.05)
        assert "battery?" in fake.received
    finally:
        await adapter.disconnect()
        transport.close()


async def test_tello_flies_through_the_flight_core_and_autonomy_stays_refused():
    fake, transport, config = await fake_tello()
    db = FlightDatabase(":memory:")
    manager = ConnectionManager(adapter_factory=lambda d: TelloAdapter(adapter_id=d.drone_id, config=config))
    controller = FlightController(db, connection_manager=manager)
    try:
        drones = await manager.discover()
        tello = next(d for d in drones if d.adapter_type.value == "tello")
        assert (await controller.connect(tello)).accepted
        adapter = controller.adapter
        await push_state(fake, adapter)
        assert (await controller.command("arm")).accepted
        assert (await controller.command("takeoff", target_altitude_m=2.0)).accepted
        await push_state(fake, adapter)
        goto = await controller.command("goto", x=5.0, y=0.0, altitude=2.0)
        assert not goto.accepted, "no position: missions and goto are refused"
        caps = adapter.get_capabilities()
        assert not caps.is_simulated and "offboard" not in caps.supported_capabilities
        # Low battery: the failsafe lands it (there is no return-home without position).
        fake.battery = 15
        await push_state(fake, adapter)
        await controller.tick()
        assert fake.received[-1] == "land"
    finally:
        await controller.disconnect()
        transport.close()
        db.close()


async def test_network_check_explains_what_to_do():
    from vantaflight.connection.network import network_check

    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        report = await network_check(hopper_host="127.0.0.1", hopper_port=port)
    finally:
        server.close()
        await server.wait_closed()
    assert report["drones"]["hopper_camera_reachable"] is True
    assert any("Hopper's camera answers" in a for a in report["advice"])
    tello_state = next(p for p in report["ports"] if p["port"] == 8890)
    assert tello_state["protocol"] == "UDP" and tello_state["direction"] == "in"
    closed = await network_check(hopper_host="127.0.0.1", hopper_port=1)
    assert closed["drones"]["hopper_camera_reachable"] is False
