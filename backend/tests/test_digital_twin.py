"""Tests for the digital twin state, session, and trajectory."""
from __future__ import annotations

from vantaflight.digital_twin import DigitalTwinState, TwinSession, TrajectoryBuffer
from vantaflight.models import FlightMode, Telemetry


def test_trajectory_buffer_add_and_limit():
    buf = TrajectoryBuffer(max_points=5)
    for i in range(10):
        buf.add(float(i), float(i), 0.0, float(i), 0.0)
    assert len(buf) == 5
    assert buf.points[0].timestamp == 5.0


def test_trajectory_buffer_clear():
    buf = TrajectoryBuffer()
    buf.add(0.0, 1.0, 2.0, 3.0, 90.0)
    buf.clear()
    assert len(buf) == 0


def test_twin_state_update():
    state = DigitalTwinState()
    t = Telemetry(
        connected=True,
        armed=True,
        altitude=5.0,
        x=1.0, y=2.0, z=5.0,
        heading=180.0,
        velocity=3.5,
        flight_mode=FlightMode.HOLD,
        battery_percentage=85.0,
    )
    state.update(t)
    assert state.altitude == 5.0
    assert state.armed is True
    assert state.connected is True
    assert state.heading == 180.0
    assert state.flight_mode == "HOLD"
    assert len(state.trajectory) == 1


def test_twin_state_reset():
    state = DigitalTwinState()
    t = Telemetry(connected=True, armed=True, altitude=10.0)
    state.update(t)
    state.reset()
    assert state.altitude == 0.0
    assert state.armed is False
    assert len(state.trajectory) == 0


def test_twin_state_to_dict():
    state = DigitalTwinState()
    t = Telemetry(connected=True, altitude=3.0, heading=90.0)
    state.update(t)
    d = state.to_dict()
    assert d["altitude"] == 3.0
    assert d["heading"] == 90.0
    assert isinstance(d["trajectory"], list)


def test_twin_session_lifecycle():
    session = TwinSession()
    assert session.active is False

    session.start(battery=100.0)
    assert session.active is True

    t = Telemetry(connected=True, armed=True, altitude=5.0, velocity=2.0, battery_percentage=90.0)
    session.update(t)
    session.record_command()
    session.record_command()

    summary = session.end("completed")
    assert session.active is False
    assert summary.max_altitude == 5.0
    assert summary.max_speed == 2.0
    assert summary.battery_start == 100.0
    assert summary.battery_end == 90.0
    assert summary.command_count == 2
    assert summary.final_status == "completed"


def test_twin_session_interruption():
    session = TwinSession()
    session.start()
    session.record_interruption()
    summary = session.end("interrupted")
    assert summary.connection_interruptions == 1
    assert summary.final_status == "interrupted"


def test_twin_session_no_update_when_inactive():
    session = TwinSession()
    t = Telemetry(connected=True, altitude=10.0)
    session.update(t)
    assert session.state.altitude == 0.0


def test_run_summary_to_dict():
    session = TwinSession()
    session.start()
    t = Telemetry(connected=True, altitude=8.0, velocity=4.0, battery_percentage=75.0)
    session.update(t)
    d = session.summary.to_dict()
    assert "max_altitude" in d
    assert "max_speed" in d
    assert "duration" in d
