"""HopperAdapter — VantaFlight facade for the FTW Robotics Hopper.

The adapter is capability-driven and refuses to invent undocumented FTW
control behavior. Camera/observe support can be useful before live control is
available; every hardware command preserves whether it was actually sent.
"""
from __future__ import annotations

import logging
from typing import Optional

from ...models import Capabilities, CapabilityStatus, CommandResult, Telemetry
from ..base import DroneAdapter
from .battery import HopperBatteryManager
from .camera import HopperCameraConnector
from .capabilities import HopperCapabilities, observe_mode_capabilities, program_mode_capabilities
from .config import HopperConfig
from .control import HopperControlConnector
from .errors import HopperNotFound, HopperUnsupportedCapability
from .link import HopperLinkSupervisor
from .models import BatteryState, HopperCommand, HopperConnectionState, HopperHealth, HopperOperatingMode
from .programs import HopperProgramConnector, SimpleMissionPlan
from .safety import HopperSafetySupervisor
from .telemetry import HopperTelemetryConnector
from .timesync import HopperTimeSync

logger = logging.getLogger(__name__)

_COMMAND_CAPS = {
    "arm": "arm",
    "disarm": "disarm",
    "takeoff": "takeoff",
    "land": "land",
    "hold": "hold",
    "velocity": "velocity",
    "position": "position",
    "relative_move": "relative_move",
    "yaw": "yaw",
}


class HopperAdapter(DroneAdapter):
    """Capability-negotiated Hopper adapter with no raw motor boundary."""

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
        self._camera = camera or HopperCameraConnector(self._config.camera, self._ts)
        self._control = control or HopperControlConnector()
        self._telemetry = telemetry or HopperTelemetryConnector(self._ts)
        self._programs = programs or HopperProgramConnector()
        self._battery = battery or HopperBatteryManager(self._config.battery)
        self._link = link or HopperLinkSupervisor(link_timeout_s=self._config.link_timeout_s)
        self._safety = safety or HopperSafetySupervisor(self._battery)
        self._mode = HopperOperatingMode.OBSERVE
        self._caps: HopperCapabilities = observe_mode_capabilities()

    @property
    def connected(self) -> bool:
        return self.connection_state not in {
            HopperConnectionState.DISCONNECTED,
            HopperConnectionState.DISCOVERING,
            HopperConnectionState.ERROR,
        }

    async def connect(self) -> None:
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

        self._refresh_capabilities()
        if not camera_ok and not control_ok:
            raise HopperNotFound(
                "Could not establish a Hopper camera or control link. Join the Hopper Wi-Fi "
                "network and configure an official FTW control transport when available."
            )
        logger.info("Hopper connected in state %s", self.connection_state.value)

    async def disconnect(self) -> None:
        await self._camera.disconnect()
        await self._control.disconnect()
        self._telemetry.mark_disconnected()
        self._programs.mark_disconnected()
        self._link.camera_lost()
        self._link.control_lost()
        self._link.telemetry_lost()
        self._safety.revoke_hardware_mode()

    async def arm(self) -> CommandResult:
        return await self._dispatch("arm")

    async def disarm(self) -> CommandResult:
        return await self._dispatch("disarm")

    async def takeoff(self, target_altitude_m: float = 1.2) -> CommandResult:
        return await self._dispatch("takeoff", altitude_m=target_altitude_m)

    async def hold(self) -> CommandResult:
        return await self._dispatch("hold", safety_override=True)

    async def land(self) -> CommandResult:
        return await self._dispatch("land", safety_override=True)

    async def _dispatch(self, command_type: str, safety_override: bool = False, **params) -> CommandResult:
        self._refresh_capabilities()
        if not safety_override:
            self._require_live_control(command_type)
        cmd = HopperCommand.create(command_type, ttl_s=self._config.command_ttl_s, **params)
        self._safety.check(cmd, self._connection_state, self._link.control_link_alive)
        result = await self._control.send(cmd)
        if not result.status.transmitted:
            logger.warning("Hopper command not transmitted: %s (%s)", command_type, result.status.value)
        return result

    def get_telemetry(self) -> Telemetry:
        tel = self._telemetry.get_telemetry()
        if tel.battery_available:
            self._battery.update(tel.battery_percentage)
        return tel

    def get_capabilities(self) -> Capabilities:
        self._refresh_capabilities()
        cap_map = self._effective_capability_map()
        # `supported_capabilities` describes adapter-level support and remains
        # useful during discovery. `capability_map` describes whether each
        # capability is available on the *current links right now*.
        supported = self._caps.supported_list()
        return Capabilities(
            name="FTW Robotics Hopper",
            adapter_type="hopper",
            is_simulated=False,
            supports_gps=False,
            can_arm=cap_map.get("arm") == CapabilityStatus.SUPPORTED,
            can_takeoff=cap_map.get("takeoff") == CapabilityStatus.SUPPORTED,
            can_hold=cap_map.get("hold") == CapabilityStatus.SUPPORTED,
            can_land=cap_map.get("land") == CapabilityStatus.SUPPORTED,
            supports_position=cap_map.get("position") == CapabilityStatus.SUPPORTED,
            supports_velocity=cap_map.get("velocity") == CapabilityStatus.SUPPORTED,
            supports_heading=cap_map.get("yaw") == CapabilityStatus.SUPPORTED,
            supports_battery=cap_map.get("battery") == CapabilityStatus.SUPPORTED,
            supports_camera=cap_map.get("camera") == CapabilityStatus.SUPPORTED,
            max_altitude_m=3.0,
            supported_capabilities=supported,
            capability_map=cap_map,
        )

    def _effective_capability_map(self) -> dict[str, CapabilityStatus]:
        cap_map = dict(self._caps.as_map())
        cap_map["camera"] = (
            CapabilityStatus.SUPPORTED if self._link.camera_link_alive else CapabilityStatus.UNAVAILABLE
        )
        cap_map["telemetry"] = (
            CapabilityStatus.SUPPORTED if self._link.telemetry_link_alive else CapabilityStatus.UNAVAILABLE
        )
        cap_map["battery"] = (
            CapabilityStatus.SUPPORTED if self._battery.state != BatteryState.UNKNOWN
            else CapabilityStatus.UNAVAILABLE
        )
        return cap_map

    def _refresh_capabilities(self) -> None:
        commands = getattr(self._control, "supported_commands", frozenset())
        if not isinstance(commands, (set, frozenset, list, tuple)):
            return
        for command in commands:
            attr = _COMMAND_CAPS.get(str(command))
            if attr and hasattr(self._caps, attr):
                setattr(self._caps, attr, CapabilityStatus.SUPPORTED)

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
        self._mode = mode
        commands = getattr(self._control, "supported_commands", frozenset())
        if mode == HopperOperatingMode.LIVE_CONTROL and not commands:
            logger.warning("LIVE_CONTROL requested but no official FTW control transport is configured")

    def authorize_hardware(self) -> None:
        self._safety.authorize_hardware_mode()

    def revoke_hardware(self) -> None:
        self._safety.revoke_hardware_mode()

    async def run_preflight(self) -> list[str]:
        return self._safety.run_preflight(
            camera_ok=self._link.camera_link_alive,
            battery_pct=self._battery.percentage,
            connection_state=self._connection_state,
            battery_known=self._battery.state != BatteryState.UNKNOWN,
        )

    def compile_program(self, plan: SimpleMissionPlan, mission_id: str | None = None):
        return self._programs.compile_and_validate(plan, mission_id=mission_id)

    async def deploy_program(self, program) -> bool:
        return await self._programs.deploy(program)

    @property
    def camera(self) -> HopperCameraConnector:
        return self._camera

    @property
    def _connection_state(self) -> HopperConnectionState:
        return self._link.connection_state(self._mode.value)

    def _require_live_control(self, capability: str) -> None:
        status = self._effective_capability_map().get(capability, CapabilityStatus.UNKNOWN)
        if status != CapabilityStatus.SUPPORTED:
            raise HopperUnsupportedCapability(
                f"Capability '{capability}' is {status.value}. Live control remains disabled "
                "until an active official FTW transport explicitly exposes it."
            )

    def _battery_health(self):
        from .models import HopperHealthState
        if self._battery.state == BatteryState.CRITICAL:
            return HopperHealthState.CRITICAL
        if self._battery.state in (BatteryState.RESERVE, BatteryState.RETURN_REQUIRED):
            return HopperHealthState.DEGRADED
        if self._battery.state == BatteryState.UNKNOWN:
            return HopperHealthState.UNAVAILABLE
        return HopperHealthState.OK
