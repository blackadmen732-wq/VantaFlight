"""Regression tests for V0.3 PR review findings.

Each test targets a specific issue raised by CodeAnt, Qodo, or Codex reviewers.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from vantaflight.adapters.px4_sitl import PX4SITLAdapter
from vantaflight.core import FlightController
from vantaflight.core.flight_controller import SessionState, _Metrics
from vantaflight.data import FlightDatabase
from vantaflight.digital_twin import DigitalTwinState
from vantaflight.main import create_app
from vantaflight.mavlink.mavsdk_client import MAVSDKError, PX4Telemetry
from vantaflight.models import FlightMode, Telemetry


# ---------------------------------------------------------------------------
# Diagnostics metrics key names match frontend contract
# ---------------------------------------------------------------------------

class TestDiagnosticsMetricsKeys:
    def test_metrics_keys_match_frontend(self):
        m = _Metrics()
        m.record_telemetry_interval(0.1)
        m.record_command_rtt(0.05)
        m.record_db_write(0.002)
        d = m.to_dict()
        assert "telemetry_hz" in d
        assert "avg_command_rtt_ms" in d
        assert "avg_db_write_ms" in d
        assert "command_rtt_ms" not in d
        assert "db_write_ms" not in d

    def test_diagnostics_endpoint_shape(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            r = client.get("/api/diagnostics").json()
            assert "metrics" in r
            metrics = r["metrics"]
            assert "telemetry_hz" in metrics
            assert "avg_command_rtt_ms" in metrics
            assert "avg_db_write_ms" in metrics


# ---------------------------------------------------------------------------
# Database run summary does not invent 100% battery when no samples exist
# ---------------------------------------------------------------------------

class TestRunSummaryNoBattery:
    def test_no_samples_battery_is_none(self):
        db = FlightDatabase(":memory:")
        fid = db.start_flight("mock-0", "Mock Drone")
        db.end_flight(fid, "completed")
        summary = db.get_run_summary(fid)
        assert summary["battery_start"] is None
        assert summary["battery_end"] is None
        db.close()

    def test_with_samples_battery_is_real(self):
        db = FlightDatabase(":memory:")
        fid = db.start_flight("mock-0", "Mock Drone")
        db.record_telemetry(fid, Telemetry(connected=True, battery_percentage=95.0))
        db.record_telemetry(fid, Telemetry(connected=True, battery_percentage=90.0))
        db.end_flight(fid, "completed")
        summary = db.get_run_summary(fid)
        assert summary["battery_start"] == pytest.approx(95.0, abs=0.1)
        assert summary["battery_end"] == pytest.approx(90.0, abs=0.1)
        db.close()


# ---------------------------------------------------------------------------
# Connect race condition — concurrent calls must not create duplicate sessions
# ---------------------------------------------------------------------------

class TestConnectRaceCondition:
    async def test_concurrent_connects_only_one_wins(self, controller, db):
        results = await asyncio.gather(
            controller.connect(),
            controller.connect(),
        )
        accepted = [r for r in results if r.accepted]
        rejected = [r for r in results if not r.accepted]
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert "already connected" in rejected[0].message


# ---------------------------------------------------------------------------
# Connect failures return structured CommandResult, not HTTP 500
# ---------------------------------------------------------------------------

class TestConnectFailureStructured:
    async def test_px4_connect_failure_returns_rejected(self):
        from tests.test_px4_adapter import MockMAVSDKClient
        client = MockMAVSDKClient(fail_connect=True)
        adapter = PX4SITLAdapter(mavsdk_client=client)

        from vantaflight.connection import ConnectionManager
        manager = ConnectionManager(adapter_factory=lambda d: adapter)
        db = FlightDatabase(":memory:")
        fc = FlightController(db, connection_manager=manager)

        result = await fc.connect()
        assert result.accepted is False
        assert "PX4_CONNECTION_TIMEOUT" in result.message
        db.close()


# ---------------------------------------------------------------------------
# Discover endpoint includes capabilities
# ---------------------------------------------------------------------------

class TestDiscoverCapabilities:
    def test_discover_includes_capabilities(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            drones = client.get("/api/discover").json()["drones"]
            for d in drones:
                assert "capabilities" in d
                assert isinstance(d["capabilities"], list)
                assert len(d["capabilities"]) > 0


# ---------------------------------------------------------------------------
# Capabilities endpoint returns proper envelope
# ---------------------------------------------------------------------------

class TestCapabilitiesEnvelope:
    def test_disconnected_capabilities(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            r = client.get("/api/capabilities").json()
            assert r["connected"] is False
            assert r["capabilities"] is None

    def test_connected_capabilities(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            client.post("/api/connect")
            r = client.get("/api/capabilities").json()
            assert r["connected"] is True
            caps = r["capabilities"]
            assert "supported_capabilities" in caps
            assert isinstance(caps["supported_capabilities"], list)


# ---------------------------------------------------------------------------
# Run summary endpoint returns {source, summary} envelope
# ---------------------------------------------------------------------------

class TestRunSummaryEnvelope:
    def test_run_summary_shape(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            r = client.get("/api/run-summary").json()
            assert "source" in r
            assert "summary" in r


# ---------------------------------------------------------------------------
# DigitalTwinState flight duration without double counting
# ---------------------------------------------------------------------------

class TestFlightDurationNoDuplicate:
    def test_arm_disarm_arm_accumulates(self):
        state = DigitalTwinState()
        t_armed = Telemetry(connected=True, armed=True, altitude=5.0)
        t_disarmed = Telemetry(connected=True, armed=False, altitude=0.0)

        state.update(t_armed)
        first_start = state._flight_start
        assert first_start is not None

        time.sleep(0.05)
        state.update(t_armed)
        d1 = state.flight_duration
        assert d1 > 0

        state.update(t_disarmed)
        d_after_disarm = state.flight_duration
        assert d_after_disarm >= d1
        assert state._flight_start is None

        state.update(t_armed)
        time.sleep(0.05)
        state.update(t_armed)
        d_second = state.flight_duration
        assert d_second > d_after_disarm

    def test_reset_clears_accumulated(self):
        state = DigitalTwinState()
        state.update(Telemetry(connected=True, armed=True, altitude=5.0))
        time.sleep(0.02)
        state.update(Telemetry(connected=True, armed=False))
        assert state.flight_duration > 0
        state.reset()
        assert state.flight_duration == 0.0
        assert state._accumulated_duration == 0.0


# ---------------------------------------------------------------------------
# PX4 flight mode normalization handles enum-qualified strings
# ---------------------------------------------------------------------------

class TestPX4FlightModeNormalization:
    def test_enum_qualified_takeoff(self):
        mode = PX4SITLAdapter._map_flight_mode("FlightMode.TAKEOFF", True)
        assert mode == FlightMode.TAKEOFF

    def test_plain_takeoff(self):
        mode = PX4SITLAdapter._map_flight_mode("TAKEOFF", True)
        assert mode == FlightMode.TAKEOFF

    def test_enum_qualified_land(self):
        mode = PX4SITLAdapter._map_flight_mode("FlightMode.LAND", False)
        assert mode == FlightMode.LANDING

    def test_enum_qualified_hold(self):
        mode = PX4SITLAdapter._map_flight_mode("FlightMode.HOLD", True)
        assert mode == FlightMode.HOLD

    def test_unknown_airborne_defaults_to_hold(self):
        mode = PX4SITLAdapter._map_flight_mode("FlightMode.OFFBOARD", True)
        assert mode == FlightMode.HOLD

    def test_unknown_grounded_defaults_to_idle(self):
        mode = PX4SITLAdapter._map_flight_mode("FlightMode.MANUAL", False)
        assert mode == FlightMode.IDLE


# ---------------------------------------------------------------------------
# TrajectoryPoint uses 'timestamp' key (not 'ts')
# ---------------------------------------------------------------------------

class TestTrajectoryPointKey:
    def test_to_dict_uses_timestamp(self):
        state = DigitalTwinState()
        state.update(Telemetry(connected=True, altitude=3.0))
        d = state.to_dict()
        assert len(d["trajectory"]) == 1
        point = d["trajectory"][0]
        assert "timestamp" in point
        assert "ts" not in point


# ---------------------------------------------------------------------------
# Connect endpoint validates adapter type
# ---------------------------------------------------------------------------

class TestConnectValidation:
    def test_unknown_adapter_rejected(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            r = client.post("/api/connect", json={"adapter_type": "nonexistent"}).json()
            assert r["accepted"] is False
            assert "unknown adapter" in r["message"]

    def test_double_connect_rejected(self):
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            r1 = client.post("/api/connect").json()
            assert r1["accepted"] is True
            r2 = client.post("/api/connect").json()
            assert r2["accepted"] is False
            assert "already connected" in r2["message"]
