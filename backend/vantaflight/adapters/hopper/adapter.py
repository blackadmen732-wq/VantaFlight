"""HopperAdapter — facade over all Hopper sub-connectors.

This is the single class VantaFlight talks to for Hopper.  It implements
the same DroneAdapter contract as MockDroneAdapter and PX4SITLAdapter so the
rest of the application (FlightController, ConnectionManager, VantaExecution)
never knows which drone it is talking to.

Internal structure:
  HopperCameraConnector     — Wi-Fi camera stream
  HopperControlConnector    — live flight commands (transport-agnostic)
  HopperTelemetryConnector  — normalized telemetry
  HopperProgramConnector    — autonomous program compilation + upload
  HopperBatteryManager      — battery state machine + safety thresholds
  HopperLinkSupervisor      — per-link health and combined state
  HopperSafetySupervisor    — command arbiter gate

Design rules:
- VantaFlight never calls sub-connectors directly; only this facade.
- Unsupported capabilities raise HopperUnsupportedCapability.
- LIVE_CONTROL mode is architecture-complete but disabled until FTW
  publishes an official Python SDK.
- arm/disarm/takeoff/hold/land raise HopperUnsupportedCapability until
  a supported transport confirms live-control capability.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ...models import Capabilities, CapabilityStatus, CommandResult, CommandStatus, FlightMode, Telemetry
from ..base import DroneAdapter
from .battery import HopperBatteryManager
from .camera import HopperCameraConnector
from .capabilities import (
    HopperCapabilities,
    observe_mode_capabilities,
    program_mode_capabilities,
)
from .config import HopperConfig
from .control import HopperControlConnector
from .errors import (
    HopperNotFound,
    HopperUnsupportedCapability,
)
from .link import HopperLinkSupervisor
from .models import (
    BatteryState,
    HopperCommand,
    HopperConnectionState,
    HopperHealth,
    HopperOperatingMode,
)
from .programs import HopperProgramConnector, SimpleMissionPlan
from .safety import HopperSafetySupervisor
from .telemetry import HopperTelemetryConnector
from .timesync import HopperTimeSync

logger = logging.getLogger(__name__)


class HopperAdapter(DroneAdapter):
    """VantaFlight adapter for FTW Robotics Hopper.

    Connection states:
      DISCONNECTED        — nothing established
      DISCOVERING         — searching for Hopper
      CAMERA_ONLY         — Wi-Fi camera up; no control link
      OBSERVE             — camera + telemetry; no flight commands
      PROGRAM_READY       — autonomous programs can be deployed
      LIVE_CONTROL_READY  — live commands (requires official SDK)
      DEGRADED            — partial link loss
      ERROR               — unrecoverable error

    The adapter reports capabilities honestly.  Live flight commands are
    UNSUPPORTED until FTW publishes a supported interface.
    """

    def __init__(
        self,
        config: Optional[HopperConfig] = None,
        camera: Optional[HopperCameraConnector] = None,
        control: Optional[HopperControlConnector] = None,
        telemetry: Optional[HopperTelemetryConnector] = None,
        programs: Optional[HopperProgramConnector] = None,
        battery: Optional[HopperBatteryManager] = None,
        link: Optional[HopperLinkSupervisor] = None,
        safety: Optional[HopperSafetySupervisor] = None,
    ) -> None:
        self._config = config or HopperConfig()
        self.adapter_id = self._config.adapter_id

        self._ts = HopperTimeSync()
        self._camera = camera or HopperCameraConnector(
            config=self._config.camera, timesync=self._ts
        )
        self._control = control or HopperControlConnector()
        self._telemetry = telemetry or HopperTelemetryConnector(timesync=self._ts)
        self._programs = programs or HopperProgramConnector()
        self._battery = battery or HopperBatteryManager(config=self._config.battery)
        self._link = link or HopperLinkSupervisor(
            link_timeout_s=self._config.link_timeout_s
        )
        self._safety = safety or HopperSafetySupervisor(battery_manager=self._battery)

        self._mode = HopperOperatingMode.OBSERVE
        self._caps: HopperCapabilities = observe_mode_capabilities()
        self._connect_start: float = 0.0

    # -- DroneAdapter contract -----------------------------------------------

    @property
    def connected(self) -> bool:
        return self._link.connection_state(self._mode.value) not in (
            HopperConnectionState.DISCONNECTED,
            HopperConnectionState.DISCOVERING,
            HopperConnectionState.ERROR,
        )

    async def connect(self) -> None:
        """Establish all available links.

        Succeeds even if only the camera link comes up (CAMERA_ONLY mode).
        The caller checks `connection_state` to know what is actually available.
        """
        self._connect_start = time.monotonic()
        logger.info("HopperAdapter connecting (id=%s)…", self.adapter_id)

        camera_ok = await self._camera.connect()
        if camera_ok:
            self._link.camera_heartbeat()

        control_ok = await self._control.connect()
        if control_ok:
            self._link.control_heartbeat()
            self._telemetry.mark_connected()
            self._link.telemetry_heartbeat()
            self._programs.mark_connected()
            self._caps = program_mode_capabilities()
        else:
            self._caps = observe_mode_capabilities()

        state = self._link.connection_state(self._mode.value)
        logger.info("HopperAdapter connected: state=%s", state.value)

        if state == HopperConnectionState.DISCONNECTED:
            raise HopperNotFound(
                "Could not establish any connection to Hopper. "
                "Ensure the Hopper Wi-Fi network is joined and Bluetooth is available."
            )

    async def disconnect(self) -> None:
        await self._camera.disconnect()
        await self._control.disconnect()
        self._telemetry.mark_disconnected()
        self._programs.mark_disconnected()
        self._link.camera_lost()
        self._link.control_lost()
        self._link.telemetry_lost()
        logger.info("HopperAdapter disconnected")

    # -- flight commands (require live-control capability) -------------------

    async def arm(self) -> None:
        self._require_live_control("arm")
        cmd = HopperCommand.create("arm", ttl_s=self._config.command_ttl_s)
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        await self._control.send(cmd)

    async def disarm(self) -> None:
        self._require_live_control("disarm")
        cmd = HopperCommand.create("disarm", ttl_s=self._config.command_ttl_s)
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        await self._control.send(cmd)

    async def takeoff(self, target_altitude_m: float = 1.2) -> None:
        self._require_live_control("takeoff")
        cmd = HopperCommand.create(
            "takeoff",
            ttl_s=self._config.command_ttl_s,
            altitude_m=target_altitude_m,
        )
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        await self._control.send(cmd)

    async def hold(self) -> CommandResult:
        # hold is always accepted by safety — it is in _ALWAYS_ALLOWED.
        cmd = HopperCommand.create("hold", ttl_s=self._config.command_ttl_s)
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        if self._link.control_link_alive:
            await self._control.send(cmd)
            return CommandResult.ok("hold")
        return CommandResult.no_transport("hold")

    async def land(self) -> CommandResult:
        cmd = HopperCommand.create("land", ttl_s=self._config.command_ttl_s)
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        if self._link.control_link_alive:
            await self._control.send(cmd)
            return CommandResult.ok("land")
        return CommandResult.no_transport("land")

    # -- telemetry / capabilities --------------------------------------------

    def get_telemetry(self) -> Telemetry:
        tel = self._telemetry.get_telemetry()
        # Feed battery percentage to the state machine only when a real reading arrived.
        if tel.battery_available:
            self._battery.update(tel.battery_percentage)
        return tel

    def get_capabilities(self) -> Capabilities:
        cap_map = self._caps.as_map()
        return Capabilities(
            name="FTW Robotics Hopper",
            adapter_type="hopper",
            is_simulated=False,
            supports_gps=False,
            can_arm=self._caps.is_available("arm"),
            can_takeoff=self._caps.is_available("takeoff"),
            can_hold=self._caps.is_available("hold"),
            can_land=self._caps.is_available("land"),
            supports_position=self._caps.is_available("position"),
            supports_velocity=self._caps.is_available("velocity"),
            supports_heading=self._caps.is_available("yaw"),
            supports_battery=self._caps.is_available("battery"),
            supports_camera=self._caps.is_available("camera"),
            max_altitude_m=3.0,
            supported_capabilities=self._caps.supported_list(),
            capability_map={k: v for k, v in cap_map.items()},
        )

    # -- Hopper-specific API -------------------------------------------------

    @property
    def connection_state(self) -> HopperConnectionState:
        self._link.tick()
        return self._link.connection_state(self._mode.value)

    @property
    def health(self) -> HopperHealth:
        h = self._link.health()
        h.battery = self._battery_health()
        h.overall = h.compute_overall()
        return h

    @property
    def operating_mode(self) -> HopperOperatingMode:
        return self._mode

    def set_operating_mode(self, mode: HopperOperatingMode) -> None:
        logger.info(
            "HopperAdapter mode: %s → %s", self._mode.value, mode.value
        )
        self._mode = mode
        if mode == HopperOperatingMode.LIVE_CONTROL:
            logger.warning(
                "LIVE_CONTROL mode requested but is UNSUPPORTED until FTW "
                "publishes an official Python SDK.  No flight commands will succeed."
            )

    def authorize_hardware(self) -> None:
        """Operator grants hardware-mode authorization for live commands."""
        self._safety.authorize_hardware_mode()

    async def run_preflight(self) -> list[str]:
        """Run pre-flight checks.  Returns failures (empty = pass)."""
        return self._safety.run_preflight(
            camera_ok=self._link.camera_link_alive,
            battery_pct=self._battery.percentage,
            connection_state=self._connection_state,
        )

    # program deployment -------------------------------------------------------

    def compile_program(
        self, plan: SimpleMissionPlan, mission_id: str | None = None
    ):
        return self._programs.compile_and_validate(plan, mission_id=mission_id)

    async def deploy_program(self, program) -> bool:
        return await self._programs.deploy(program)

    # camera -------------------------------------------------------------------

    @property
    def camera(self) -> HopperCameraConnector:
        return self._camera

    # -- private helpers -----------------------------------------------------

    @property
    def _connection_state(self) -> HopperConnectionState:
        return self._link.connection_state(self._mode.value)

    def _require_live_control(self, capability: str) -> None:
        if not self._caps.is_available(capability):
            status = self._caps.as_map().get(capability, CapabilityStatus.UNKNOWN)
            raise HopperUnsupportedCapability(
                f"Capability '{capability}' is {status.value} on Hopper. "
                "Live flight commands require the official FTW Python SDK, "
                "which has not yet been published."
            )

    def _battery_health(self):
        from .models import HopperHealthState
        state = self._battery.state
        if state == BatteryState.CRITICAL:
            return HopperHealthState.CRITICAL
        if state in (BatteryState.RESERVE, BatteryState.RETURN_REQUIRED):
            return HopperHealthState.DEGRADED
        if state == BatteryState.UNKNOWN:
            return HopperHealthState.UNAVAILABLE
        return HopperHealthState.OK
