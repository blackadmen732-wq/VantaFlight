"""Tests for the safety validator rules."""
from __future__ import annotations

from vantaflight.models import FlightMode, Telemetry
from vantaflight.safety import SafetyValidator

validator = SafetyValidator()


def _airborne() -> Telemetry:
    return Telemetry(connected=True, armed=True, altitude=5.0, flight_mode=FlightMode.HOLD)


def test_no_arm_while_disconnected():
    v = validator.check("arm", Telemetry(connected=False))
    assert v is not None and "disconnected" in v.reason


def test_no_takeoff_while_disconnected():
    v = validator.check("takeoff", Telemetry(connected=False))
    assert v is not None and "disconnected" in v.reason


def test_no_takeoff_before_arming():
    v = validator.check("takeoff", Telemetry(connected=True, armed=False))
    assert v is not None and "arming" in v.reason


def test_takeoff_allowed_when_armed_and_grounded():
    assert validator.check("takeoff", Telemetry(connected=True, armed=True)) is None


def test_no_disarm_while_airborne():
    v = validator.check("disarm", _airborne())
    assert v is not None and "airborne" in v.reason


def test_no_hold_on_ground():
    v = validator.check("hold", Telemetry(connected=True, armed=True, altitude=0.0))
    assert v is not None


def test_no_land_on_ground():
    v = validator.check("land", Telemetry(connected=True, armed=True, altitude=0.0))
    assert v is not None


def test_land_allowed_when_airborne():
    assert validator.check("land", _airborne()) is None


def test_no_movement_command_while_disconnected():
    for cmd in ("takeoff", "hold", "land"):
        v = validator.check(cmd, Telemetry(connected=False))
        assert v is not None, cmd


def test_unknown_command_rejected():
    v = validator.check("barrel_roll", Telemetry(connected=True))
    assert v is not None
