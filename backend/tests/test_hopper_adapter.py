"""Tests for HopperAdapter and its sub-systems.

Proves that:
1. HopperAdapter integrates through the same path as Mock/PX4.
2. Unsupported capabilities raise HopperUnsupportedCapability — not silently succeed.
3. Camera-only mode produces a valid CAMERA_ONLY connection state.
4. Capability negotiation reflects the active mode.
5. Battery safety state machine transitions correctly.
6. Program compiler + validator reject unsafe programs.
7. Safety supervisor blocks stale/unauthorized commands.
8. ConnectionManager builds a HopperAdapter for HOPPER transport.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vantaflight.adapters.hopper import (
    HopperAdapter,
    HopperConnectionState,
    HopperOperatingMode,
    HopperUnsupportedCapability,
    SimpleMissionPlan,
)
from vantaflight.adapters.hopper.battery import HopperBatteryManager
from vantaflight.adapters.hopper.camera import HopperCameraConnector
from vantaflight.adapters.hopper.capabilities import (
    observe_mode_capabilities,
    program_mode_capabilities,
)
from vantaflight.adapters.hopper.config import HopperBatteryConfig, HopperConfig
from vantaflight.adapters.hopper.control import HopperControlConnector, NoTransport
from vantaflight.adapters.hopper.errors import (
    HopperBatteryCritical,
    HopperCommandExpired,
    HopperNotFound,
    HopperPreflightFailed,
    HopperProgramValidationFailed,
)
from vantaflight.adapters.hopper.link import HopperLinkSupervisor
from vantaflight.adapters.hopper.models import (
    BatteryState,
    HopperCommand,
    HopperConnectionState,
)
from vantaflight.adapters.hopper.programs import (
    HopperProgramCompiler,
    ProgramSafetyValidator,
)
from vantaflight.adapters.hopper.safety import HopperSafetySupervisor
from vantaflight.adapters.hopper.telemetry import HopperTelemetryConnector
from vantaflight.connection.manager import (
    ConnectionManager,
    DiscoveredDrone,
    TransportType,
)
from vantaflight.models import AdapterType, CapabilityStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_disconnected_adapter() -> HopperAdapter:
    """Returns a HopperAdapter whose connectors never actually connect."""
    camera = HopperCameraConnector()
    control = HopperControlConnector()
    adapter = HopperAdapter(camera=camera, control=control)
    return adapter


def _make_camera_only_adapter() -> HopperAdapter:
    """Returns a HopperAdapter that reports camera-only connection."""
    camera = MagicMock(spec=HopperCameraConnector)
    camera.connect = AsyncMock(return_value=True)
    camera.disconnect = AsyncMock()
    camera.connected = True

    control = MagicMock(spec=HopperControlConnector)
    control.connect = AsyncMock(return_value=False)  # control link unavailable
    control.disconnect = AsyncMock()
    control.connected = False

    adapter = HopperAdapter(camera=camera, control=control)
    return adapter


async def _connect_camera_only(adapter: HopperAdapter) -> None:
    adapter._link.camera_heartbeat()
    # do not heartbeat control link


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_observe_mode_no_flight_commands(self):
        caps = observe_mode_capabilities()
        for cap in ("arm", "disarm", "takeoff", "land", "hold", "velocity", "position"):
            assert caps.as_map()[cap] == CapabilityStatus.UNSUPPORTED

    def test_observe_mode_has_camera_and_telemetry(self):
        caps = observe_mode_capabilities()
        assert caps.camera == CapabilityStatus.SUPPORTED
        assert caps.telemetry == CapabilityStatus.SUPPORTED
        assert caps.battery == CapabilityStatus.SUPPORTED

    def test_program_mode_program_caps_are_unknown_not_supported(self):
        """program_upload/run/stop must be UNKNOWN until FTW publishes the SDK."""
        caps = program_mode_capabilities()
        assert caps.program_upload == CapabilityStatus.UNKNOWN
        assert caps.program_run == CapabilityStatus.UNKNOWN
        assert caps.program_stop == CapabilityStatus.UNKNOWN

    def test_live_control_commands_remain_unknown(self):
        from vantaflight.adapters.hopper.capabilities import live_control_capabilities
        caps = live_control_capabilities()
        assert caps.velocity == CapabilityStatus.UNKNOWN
        assert caps.position == CapabilityStatus.UNKNOWN

    def test_supported_list_only_includes_supported(self):
        caps = program_mode_capabilities()
        supported = caps.supported_list()
        assert "camera" in supported
        # program_upload is UNKNOWN (no transport), not SUPPORTED
        assert "program_upload" not in supported
        assert "velocity" not in supported
        assert "arm" not in supported


# ---------------------------------------------------------------------------
# HopperAdapter connection state
# ---------------------------------------------------------------------------


class TestConnectionState:
    def test_disconnected_by_default(self):
        adapter = _make_disconnected_adapter()
        assert adapter.connection_state == HopperConnectionState.DISCONNECTED
        assert not adapter.connected

    def test_camera_only_state(self):
        adapter = _make_disconnected_adapter()
        adapter._link.camera_heartbeat()
        assert adapter.connection_state == HopperConnectionState.CAMERA_ONLY
        assert adapter.connected  # camera-only counts as connected

    def test_observe_state(self):
        adapter = _make_disconnected_adapter()
        adapter._link.camera_heartbeat()
        adapter._link.control_heartbeat()
        assert adapter.connection_state == HopperConnectionState.OBSERVE
        assert adapter.connected

    def test_program_ready_state(self):
        adapter = _make_disconnected_adapter()
        adapter.set_operating_mode(HopperOperatingMode.PROGRAM_UPLOAD)
        adapter._link.camera_heartbeat()
        adapter._link.control_heartbeat()
        assert adapter.connection_state == HopperConnectionState.PROGRAM_READY


# ---------------------------------------------------------------------------
# Unsupported capability enforcement
# ---------------------------------------------------------------------------


class TestUnsupportedCapabilities:
    @pytest.mark.asyncio
    async def test_arm_raises_unsupported(self):
        adapter = _make_disconnected_adapter()
        adapter._link.camera_heartbeat()
        with pytest.raises(HopperUnsupportedCapability):
            await adapter.arm()

    @pytest.mark.asyncio
    async def test_takeoff_raises_unsupported(self):
        adapter = _make_disconnected_adapter()
        with pytest.raises(HopperUnsupportedCapability):
            await adapter.takeoff()

    @pytest.mark.asyncio
    async def test_disarm_raises_unsupported(self):
        adapter = _make_disconnected_adapter()
        with pytest.raises(HopperUnsupportedCapability):
            await adapter.disarm()

    @pytest.mark.asyncio
    async def test_hold_does_not_raise_when_disconnected(self):
        """hold/land are safety commands; they never raise UNSUPPORTED."""
        adapter = _make_disconnected_adapter()
        # Safety supervisor allows hold even without control link.
        await adapter.hold()  # must not raise

    @pytest.mark.asyncio
    async def test_land_does_not_raise_when_disconnected(self):
        adapter = _make_disconnected_adapter()
        await adapter.land()  # must not raise

    def test_get_capabilities_reflects_observe_mode(self):
        adapter = _make_disconnected_adapter()
        adapter._link.camera_heartbeat()
        caps = adapter.get_capabilities()
        assert caps.adapter_type == "hopper"
        assert not caps.can_arm
        assert not caps.can_takeoff
        assert caps.supports_camera

    def test_capability_map_exposes_all_keys(self):
        adapter = _make_disconnected_adapter()
        caps = adapter.get_capabilities()
        assert "camera" in caps.capability_map
        assert "velocity" in caps.capability_map
        assert "program_upload" in caps.capability_map


# ---------------------------------------------------------------------------
# Battery safety
# ---------------------------------------------------------------------------


class TestBatterySafety:
    def test_normal_battery(self):
        mgr = HopperBatteryManager()
        state = mgr.update(80.0)
        assert state == BatteryState.NORMAL
        assert mgr.may_start_objective()
        assert not mgr.should_land_now()

    def test_reserve_threshold(self):
        mgr = HopperBatteryManager()
        state = mgr.update(29.0)
        assert state == BatteryState.RESERVE
        assert not mgr.may_start_objective()

    def test_critical_threshold(self):
        mgr = HopperBatteryManager()
        state = mgr.update(9.0)
        assert state == BatteryState.CRITICAL
        assert mgr.should_land_now()

    def test_mission_reserve_blocks_new_objective(self):
        cfg = HopperBatteryConfig(mission_reserve_pct=40.0)
        mgr = HopperBatteryManager(config=cfg)
        mgr.update(38.0)
        assert not mgr.may_start_objective()

    def test_battery_state_machine_transitions(self):
        mgr = HopperBatteryManager()
        assert mgr.update(100.0) == BatteryState.NORMAL
        assert mgr.update(34.0) == BatteryState.RETURN_REQUIRED
        assert mgr.update(29.0) == BatteryState.RESERVE
        assert mgr.update(9.0) == BatteryState.CRITICAL


# ---------------------------------------------------------------------------
# Program compiler + validator
# ---------------------------------------------------------------------------


class TestProgramCompiler:
    def _valid_plan(self) -> SimpleMissionPlan:
        return SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 1.2},
            {"opcode": "MOVE_FORWARD", "distance_m": 1.0, "speed_mps": 0.3},
            {"opcode": "HOVER", "duration_s": 2.0},
            {"opcode": "LAND"},
        ])

    def test_compile_valid_plan(self):
        compiler = HopperProgramCompiler()
        program = compiler.compile(self._valid_plan())
        assert len(program.instructions) == 4
        assert program.max_altitude_m == 1.2
        assert program.estimated_duration_s > 0

    def test_unsupported_opcode_raises(self):
        compiler = HopperProgramCompiler()
        plan = SimpleMissionPlan(steps=[{"opcode": "LOOP_FOREVER"}])
        with pytest.raises(HopperUnsupportedCapability, match="LOOP_FOREVER"):
            compiler.compile(plan)

    def test_validator_rejects_no_land(self):
        compiler = HopperProgramCompiler()
        validator = ProgramSafetyValidator()
        plan = SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 1.2},
            {"opcode": "HOVER", "duration_s": 2.0},
            # missing LAND
        ])
        program = compiler.compile(plan)
        with pytest.raises(HopperProgramValidationFailed, match="LAND"):
            validator.validate(program)

    def test_validator_rejects_excessive_altitude(self):
        compiler = HopperProgramCompiler()
        validator = ProgramSafetyValidator()
        plan = SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 10.0},
            {"opcode": "LAND"},
        ])
        program = compiler.compile(plan)
        with pytest.raises(HopperProgramValidationFailed, match="altitude"):
            validator.validate(program)

    def test_validator_rejects_excessive_move_distance(self):
        compiler = HopperProgramCompiler()
        validator = ProgramSafetyValidator()
        plan = SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 1.0},
            {"opcode": "MOVE_FORWARD", "distance_m": 50.0},
            {"opcode": "LAND"},
        ])
        program = compiler.compile(plan)
        with pytest.raises(HopperProgramValidationFailed, match="distance"):
            validator.validate(program)

    def test_compile_and_validate_via_connector(self):
        adapter = _make_disconnected_adapter()
        adapter._programs.mark_connected()
        program = adapter.compile_program(self._valid_plan(), mission_id="test-001")
        assert program.source_mission_id == "test-001"
        assert len(program.instructions) == 4


# ---------------------------------------------------------------------------
# Safety supervisor
# ---------------------------------------------------------------------------


class TestSafetySupervisor:
    def test_fresh_safety_command_passes(self):
        sup = HopperSafetySupervisor()
        cmd = HopperCommand.create("land", ttl_s=2.0)
        # Must not raise — land is always allowed.
        sup.check(cmd, HopperConnectionState.OBSERVE, control_link_alive=False)

    def test_expired_command_raises(self):
        sup = HopperSafetySupervisor()
        cmd = HopperCommand(
            command_id="x",
            command_type="takeoff",
            issued_at=time.monotonic() - 10,
            expires_at=time.monotonic() - 5,
        )
        with pytest.raises(HopperCommandExpired):
            sup.check(cmd, HopperConnectionState.OBSERVE, control_link_alive=True)

    def test_live_command_without_authorization_raises(self):
        sup = HopperSafetySupervisor()
        cmd = HopperCommand.create("takeoff", ttl_s=2.0)
        with pytest.raises(HopperUnsupportedCapability, match="authorization"):
            sup.check(cmd, HopperConnectionState.LIVE_CONTROL_READY, control_link_alive=True)

    def test_live_command_without_preflight_raises(self):
        sup = HopperSafetySupervisor()
        sup.authorize_hardware_mode()
        cmd = HopperCommand.create("takeoff", ttl_s=2.0)
        with pytest.raises(HopperPreflightFailed):
            sup.check(cmd, HopperConnectionState.LIVE_CONTROL_READY, control_link_alive=True)

    def test_critical_battery_blocks_non_safety_command(self):
        sup = HopperSafetySupervisor()
        sup.authorize_hardware_mode()
        sup.set_preflight_passed(True)
        sup._battery.update(5.0)  # below critical threshold
        cmd = HopperCommand.create("velocity", ttl_s=2.0)
        with pytest.raises(HopperBatteryCritical):
            sup.check(cmd, HopperConnectionState.LIVE_CONTROL_READY, control_link_alive=True)

    def test_critical_battery_does_not_block_land(self):
        sup = HopperSafetySupervisor()
        sup._battery.update(5.0)
        cmd = HopperCommand.create("land", ttl_s=2.0)
        sup.check(cmd, HopperConnectionState.LIVE_CONTROL_READY, control_link_alive=True)

    def test_preflight_passes_all_green(self):
        sup = HopperSafetySupervisor()
        failures = sup.run_preflight(
            camera_ok=True,
            battery_pct=80.0,
            connection_state=HopperConnectionState.OBSERVE,
        )
        assert failures == []
        assert sup._preflight_passed

    def test_preflight_fails_low_battery(self):
        sup = HopperSafetySupervisor()
        failures = sup.run_preflight(
            camera_ok=True,
            battery_pct=10.0,
            connection_state=HopperConnectionState.OBSERVE,
        )
        assert any("battery" in f.lower() for f in failures)
        assert not sup._preflight_passed


# ---------------------------------------------------------------------------
# Link supervisor
# ---------------------------------------------------------------------------


class TestLinkSupervisor:
    def test_disconnected_by_default(self):
        sup = HopperLinkSupervisor()
        assert sup.connection_state() == HopperConnectionState.DISCONNECTED

    def test_camera_heartbeat_gives_camera_only(self):
        sup = HopperLinkSupervisor()
        sup.camera_heartbeat()
        assert sup.connection_state() == HopperConnectionState.CAMERA_ONLY

    def test_both_links_give_observe(self):
        sup = HopperLinkSupervisor()
        sup.camera_heartbeat()
        sup.control_heartbeat()
        assert sup.connection_state() == HopperConnectionState.OBSERVE

    def test_timeout_drops_link(self):
        sup = HopperLinkSupervisor(link_timeout_s=0.0)
        sup.camera_heartbeat()
        sup.tick()  # instant timeout
        assert sup.connection_state() == HopperConnectionState.DISCONNECTED

    def test_health_ok_with_both_links(self):
        sup = HopperLinkSupervisor()
        sup.camera_heartbeat()
        sup.control_heartbeat()
        health = sup.health()
        from vantaflight.adapters.hopper.models import HopperHealthState
        assert health.camera_link == HopperHealthState.OK
        assert health.control_link == HopperHealthState.OK


# ---------------------------------------------------------------------------
# ConnectionManager integration
# ---------------------------------------------------------------------------


class TestConnectionManagerHopper:
    @pytest.mark.asyncio
    async def test_discover_includes_hopper(self):
        mgr = ConnectionManager()
        drones = await mgr.discover()
        hopper_drones = [d for d in drones if d.adapter_type == AdapterType.HOPPER]
        assert len(hopper_drones) == 1
        assert hopper_drones[0].drone_id == "hopper-0"

    def test_build_hopper_adapter_returns_hopper_type(self):
        mgr = ConnectionManager()
        drone = DiscoveredDrone(
            drone_id="hopper-test",
            name="Hopper Test",
            transport=TransportType.HOPPER_WIFI,
            address="http://192.168.2.1",
            adapter_type=AdapterType.HOPPER,
        )
        adapter = mgr._build_adapter(drone)
        assert isinstance(adapter, HopperAdapter)
        assert adapter.adapter_id == "hopper-test"

    @pytest.mark.asyncio
    async def test_mock_adapter_still_works(self):
        from vantaflight.adapters.mock import MockDroneAdapter
        mgr = ConnectionManager()
        drones = await mgr.discover()
        mock = next(d for d in drones if d.adapter_type == AdapterType.MOCK)
        adapter = mgr._build_adapter(mock)
        assert isinstance(adapter, MockDroneAdapter)

    @pytest.mark.asyncio
    async def test_px4_adapter_still_works(self):
        from vantaflight.adapters.px4_sitl import PX4SITLAdapter
        mgr = ConnectionManager()
        drones = await mgr.discover()
        px4 = next(d for d in drones if d.adapter_type == AdapterType.PX4_SITL)
        adapter = mgr._build_adapter(px4)
        assert isinstance(adapter, PX4SITLAdapter)


# ---------------------------------------------------------------------------
# Telemetry normalization
# ---------------------------------------------------------------------------


class TestTelemetryConnector:
    def test_empty_raw_marks_fields_unavailable(self):
        """Without raw data, availability flags must be False — not fake 100% battery."""
        conn = HopperTelemetryConnector()
        conn.mark_connected()
        tel = conn.get_telemetry()
        assert tel.connected
        assert not tel.battery_available
        assert not tel.altitude_available
        assert not tel.velocity_available
        assert not tel.position_available
        # placeholder values — callers must check *_available flags before trusting these
        assert tel.battery_percentage == 0.0
        assert tel.altitude == 0.0

    def test_ingest_raw_updates_fields_and_sets_available_flags(self):
        conn = HopperTelemetryConnector()
        conn.mark_connected()
        conn.ingest({
            "battery_pct": 72.0,
            "altitude_m": 1.5,
            "heading_deg": 45.0,
            "speed_mps": 0.5,
            "x": 0.1,
            "y": 0.2,
            "flight_mode": "HOVER",
        })
        tel = conn.get_telemetry()
        assert tel.battery_percentage == 72.0
        assert tel.battery_available
        assert tel.altitude == 1.5
        assert tel.altitude_available
        assert tel.velocity == 0.5
        assert tel.velocity_available
        assert tel.position_available
        assert tel.heading == 45.0

    def test_unknown_fields_are_none_not_fabricated(self):
        conn = HopperTelemetryConnector()
        conn.mark_connected()
        tel = conn.get_telemetry()
        # latitude/longitude must stay None, not 0.0
        assert tel.latitude is None
        assert tel.longitude is None

    def test_disconnected_telemetry_reports_not_connected(self):
        conn = HopperTelemetryConnector()
        tel = conn.get_telemetry()
        assert not tel.connected


# ---------------------------------------------------------------------------
# HopperAdapter implements DroneAdapter contract
# ---------------------------------------------------------------------------


class TestHopperAdapterContract:
    def test_implements_drone_adapter_interface(self):
        from vantaflight.adapters.base import DroneAdapter
        adapter = _make_disconnected_adapter()
        assert isinstance(adapter, DroneAdapter)

    def test_get_telemetry_returns_telemetry(self):
        from vantaflight.models import Telemetry
        adapter = _make_disconnected_adapter()
        tel = adapter.get_telemetry()
        assert isinstance(tel, Telemetry)

    def test_get_capabilities_returns_capabilities(self):
        from vantaflight.models import Capabilities
        adapter = _make_disconnected_adapter()
        caps = adapter.get_capabilities()
        assert isinstance(caps, Capabilities)
        assert caps.adapter_type == "hopper"
        assert not caps.is_simulated

    def test_adapter_id_set(self):
        cfg = HopperConfig(adapter_id="hopper-test-99")
        adapter = HopperAdapter(config=cfg)
        assert adapter.adapter_id == "hopper-test-99"


# ---------------------------------------------------------------------------
# deploy() must never fake success
# ---------------------------------------------------------------------------


class TestProgramDeployHonesty:
    @pytest.mark.asyncio
    async def test_deploy_raises_unsupported_not_true(self):
        """deploy() must raise HopperUnsupportedCapability — never return True."""
        adapter = _make_disconnected_adapter()
        adapter._programs.mark_connected()
        plan = SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 1.0},
            {"opcode": "LAND"},
        ])
        program = adapter.compile_program(plan, mission_id="test-deploy")
        with pytest.raises(HopperUnsupportedCapability, match="not yet available"):
            await adapter.deploy_program(program)

    @pytest.mark.asyncio
    async def test_deploy_directly_raises(self):
        """HopperProgramConnector.deploy() raises regardless of connected state."""
        from vantaflight.adapters.hopper.programs import HopperProgramCompiler, HopperProgramConnector
        connector = HopperProgramConnector()
        connector.mark_connected()
        compiler = HopperProgramCompiler()
        program = compiler.compile(SimpleMissionPlan(steps=[
            {"opcode": "TAKEOFF", "altitude_m": 1.0},
            {"opcode": "LAND"},
        ]))
        with pytest.raises(HopperUnsupportedCapability):
            await connector.deploy(program)


# ---------------------------------------------------------------------------
# hold() / land() return CommandResult, never void
# ---------------------------------------------------------------------------


class TestHoldLandCommandResult:
    @pytest.mark.asyncio
    async def test_hold_returns_no_transport_when_no_control_link(self):
        from vantaflight.models import CommandStatus
        adapter = _make_disconnected_adapter()
        result = await adapter.hold()
        assert result is not None
        assert result.command == "hold"
        assert result.status == CommandStatus.TRANSPORT_UNAVAILABLE
        assert not result.accepted

    @pytest.mark.asyncio
    async def test_land_returns_no_transport_when_no_control_link(self):
        from vantaflight.models import CommandStatus
        adapter = _make_disconnected_adapter()
        result = await adapter.land()
        assert result is not None
        assert result.command == "land"
        assert result.status == CommandStatus.TRANSPORT_UNAVAILABLE
        assert not result.accepted
