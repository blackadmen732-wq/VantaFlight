"""DJI/Ryze Tello and Tello EDU over the official Tello SDK (UDP text commands).

The Tello is the cheapest real drone VantaFlight can fly: the laptop joins
the drone's own Wi-Fi (``TELLO-XXXXXX``) and talks plain text over UDP:

* commands  → ``192.168.10.1:8889`` (``command``, ``takeoff``, ``land``, ``stop``,
  ``up 50`` …); the drone answers ``ok`` / ``error …`` to the sending port.
* state     ← the drone streams ``key:value;`` lines to our UDP port **8890**
  at about 10 Hz (height ``h`` in cm, battery ``bat`` in %, attitude, speeds).

Everything goes through the flight core like any other adapter, so the
safety validator, failsafes (battery → land, stale link → hold) and the
operator stay in charge. Limits, stated rather than faked:

* No position: the Tello has no GPS and reports no x/y, so missions, goto
  and return-home are unsupported (failsafes that want "return" land in
  place). Position is reported as unavailable, never as 0,0.
* No arming step: the Tello spins up on ``takeoff``. ``arm`` here only marks
  the aircraft ready in VantaFlight; ``disarm`` on the ground clears it.
* It is a physical aircraft: VantaRace/offboard autonomy stays refused.
* The SDK auto-lands after 15 s without a command; VantaFlight sends a
  harmless ``battery?`` keep-alive every few seconds while connected.

On a Chromebook the state stream arrives on UDP 8890, which ChromeOS
blocks from the Linux container until you add it under Settings →
Developers → Linux → Port forwarding (UDP 8890). See docs/CHROMEBOOK.md.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import (
    Capabilities,
    CommandResult,
    CommandStatus,
    ConnectionQuality,
    FlightMode,
    Telemetry,
)
from .base import DroneAdapter

logger = logging.getLogger(__name__)

#: Tello's own takeoff climbs to about this height (m) before answering "ok".
TAKEOFF_HEIGHT_M = 1.0


@dataclass
class TelloConfig:
    host: str = field(default_factory=lambda: os.environ.get("VANTAFLIGHT_TELLO_HOST", "192.168.10.1"))
    command_port: int = 8889
    #: Local port the drone streams state to (8890 in the SDK; 0 = any, for tests).
    state_port: int = field(default_factory=lambda: int(os.environ.get("VANTAFLIGHT_TELLO_STATE_PORT", "8890")))
    command_timeout_s: float = 7.0
    motion_timeout_s: float = 20.0  # takeoff / land / long climbs
    keepalive_s: float = 5.0
    #: State older than this means the link is stale.
    stale_s: float = 1.5
    max_altitude_m: float = 10.0


class TelloError(RuntimeError):
    pass


class _Command(asyncio.DatagramProtocol):
    def __init__(self, owner: "TelloAdapter") -> None:
        self.owner = owner

    def datagram_received(self, data: bytes, addr) -> None:
        self.owner._on_reply(data.decode("utf-8", errors="replace").strip())

    def error_received(self, exc: Exception) -> None:
        logger.warning("tello command socket error: %s", exc)


class _State(asyncio.DatagramProtocol):
    def __init__(self, owner: "TelloAdapter") -> None:
        self.owner = owner

    def datagram_received(self, data: bytes, addr) -> None:
        self.owner._on_state(data.decode("utf-8", errors="replace"))


def parse_state(line: str) -> dict[str, float]:
    """``"pitch:0;roll:0;h:30;bat:87;…;"`` → ``{"pitch": 0.0, "h": 30.0, "bat": 87.0, …}``."""
    out: dict[str, float] = {}
    for part in line.strip().split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        try:
            out[key.strip()] = float(value)
        except ValueError:
            continue
    return out


class TelloAdapter(DroneAdapter):
    def __init__(self, adapter_id: str = "tello-0", config: TelloConfig | None = None) -> None:
        self.adapter_id = adapter_id
        self.config = config or TelloConfig()
        self._cmd_transport: asyncio.DatagramTransport | None = None
        self._state_transport: asyncio.DatagramTransport | None = None
        self._pending: asyncio.Future | None = None
        self._lock: asyncio.Lock | None = None
        self._connected = False
        self._armed = False
        self._mode = FlightMode.IDLE
        self._state: dict[str, float] = {}
        self._state_at = 0.0  # time.monotonic() of the last state packet
        self._last_command_at = 0.0
        self.state_port: int | None = None

    # -- link ----------------------------------------------------------------
    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_running_loop()
        cfg = self.config
        self._lock = asyncio.Lock()
        try:
            self._cmd_transport, _ = await loop.create_datagram_endpoint(
                lambda: _Command(self), local_addr=("0.0.0.0", 0)
            )
            self._state_transport, _ = await loop.create_datagram_endpoint(
                lambda: _State(self), local_addr=("0.0.0.0", cfg.state_port)
            )
        except OSError as exc:
            await self.disconnect()
            raise TelloError(
                f"could not open the Tello UDP ports ({exc}); is another app using port {cfg.state_port}?"
            ) from exc
        self.state_port = self._state_transport.get_extra_info("sockname")[1]
        try:
            reply = await self._send("command", cfg.command_timeout_s)
        except TelloError:
            await self.disconnect()
            raise TelloError(
                f"no answer from a Tello at {cfg.host}. Join the drone's TELLO-XXXXXX Wi-Fi first."
            ) from None
        if reply != "ok":
            await self.disconnect()
            raise TelloError(f"Tello refused SDK mode: {reply!r}")
        self._connected = True
        logger.info("Tello connected at %s (state on UDP %s)", cfg.host, self.state_port)

    async def disconnect(self) -> None:
        for transport in (self._cmd_transport, self._state_transport):
            if transport is not None:
                transport.close()
        self._cmd_transport = self._state_transport = None
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None
        self._connected = False
        self._armed = False
        self._mode = FlightMode.IDLE

    @property
    def connected(self) -> bool:
        return self._connected

    # -- protocol ------------------------------------------------------------------
    def _on_reply(self, text: str) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(text)
        # otherwise: the answer to a keep-alive query; nothing waits for it

    def _on_state(self, text: str) -> None:
        values = parse_state(text)
        if values:
            self._state = values
            self._state_at = time.monotonic()

    def _raw_send(self, text: str) -> None:
        if self._cmd_transport is None:
            raise TelloError("not connected")
        self._cmd_transport.sendto(text.encode("ascii"), (self.config.host, self.config.command_port))
        self._last_command_at = time.monotonic()

    async def _send(self, text: str, timeout_s: float) -> str:
        assert self._lock is not None
        async with self._lock:
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            self._raw_send(text)
            try:
                return await asyncio.wait_for(self._pending, timeout_s)
            except asyncio.TimeoutError:
                raise TelloError(f"Tello did not answer '{text}' within {timeout_s:g}s") from None
            finally:
                self._pending = None

    async def _command(self, name: str, text: str, timeout_s: float | None = None) -> CommandResult:
        if not self._connected:
            return CommandResult.no_transport(name)
        try:
            reply = await self._send(text, timeout_s or self.config.command_timeout_s)
        except TelloError as exc:
            return CommandResult(command=name, accepted=False, status=CommandStatus.TIMED_OUT, message=str(exc))
        if reply.lower() != "ok":
            return CommandResult(command=name, accepted=False, status=CommandStatus.REJECTED,
                                 message=f"Tello answered: {reply}")
        return CommandResult.acknowledged(name, f"Tello: {text}")

    # -- commands ------------------------------------------------------------------
    async def arm(self) -> Optional[CommandResult]:
        if not self._connected:
            return CommandResult.no_transport("arm")
        self._armed = True  # the Tello spins its motors on takeoff, not before
        return CommandResult.acknowledged("arm", "ready (the Tello starts its motors on takeoff)")

    async def disarm(self) -> Optional[CommandResult]:
        if not self._connected:
            return CommandResult.no_transport("disarm")
        self._armed = False
        return CommandResult.acknowledged("disarm", "not ready")

    async def takeoff(self, target_altitude_m: float = 5.0) -> Optional[CommandResult]:
        target = min(float(target_altitude_m), self.config.max_altitude_m)
        self._mode = FlightMode.TAKEOFF
        result = await self._command("takeoff", "takeoff", self.config.motion_timeout_s)
        if not result.accepted:
            self._mode = FlightMode.IDLE if self._height_m() < 0.2 else FlightMode.HOLD
            return result
        self._armed = True
        climb_cm = int(round((target - TAKEOFF_HEIGHT_M) * 100))
        if climb_cm >= 20:  # the SDK moves 20–500 cm per step
            for step in _steps(climb_cm, 500):
                up = await self._command("takeoff", f"up {step}", self.config.motion_timeout_s)
                if not up.accepted:
                    self._mode = FlightMode.HOLD
                    return CommandResult(command="takeoff", accepted=True, status=CommandStatus.ACKNOWLEDGED,
                                         message=f"took off, but the climb to {target:g} m stopped: {up.message}")
        self._mode = FlightMode.HOLD
        return CommandResult.acknowledged("takeoff", f"airborne, holding at about {max(target, TAKEOFF_HEIGHT_M):.1f} m")

    async def hold(self) -> Optional[CommandResult]:
        result = await self._command("hold", "stop")
        if result.accepted:
            self._mode = FlightMode.HOLD
        return result

    async def land(self) -> Optional[CommandResult]:
        self._mode = FlightMode.LANDING
        result = await self._command("land", "land", self.config.motion_timeout_s)
        if result.accepted:
            self._mode = FlightMode.IDLE
            self._armed = False
        return result

    async def emergency_stop(self) -> CommandResult:
        """Cut the motors immediately (the aircraft falls). Operator use only."""
        if self._cmd_transport is None:
            return CommandResult.no_transport("emergency")
        self._raw_send("emergency")  # no waiting: this must go out even if a command is pending
        self._mode = FlightMode.IDLE
        self._armed = False
        return CommandResult(command="emergency", accepted=True, status=CommandStatus.SENT, message="motors cut")

    # -- state ------------------------------------------------------------------------
    def _height_m(self) -> float:
        return max(0.0, self._state.get("h", 0.0) / 100.0)

    def get_telemetry(self) -> Telemetry:
        now = time.monotonic()
        if self._connected and now - self._last_command_at > self.config.keepalive_s and (
            self._pending is None or self._pending.done()
        ):
            try:
                self._raw_send("battery?")  # keeps the SDK's 15 s auto-land from firing
            except TelloError:
                pass
        if not self._connected:
            return Telemetry(connected=False, battery_available=False, altitude_available=False,
                             velocity_available=False, position_available=False)
        have_state = self._state_at > 0
        age = (now - self._state_at) if have_state else float(self.config.stale_s * 10)
        s = self._state
        height = self._height_m()
        # SDK 2.0 reports vgx/vgy/vgz in dm/s.
        speed = math.sqrt(sum((s.get(k, 0.0) / 10.0) ** 2 for k in ("vgx", "vgy", "vgz")))
        ground = math.hypot(s.get("vgx", 0.0) / 10.0, s.get("vgy", 0.0) / 10.0)
        if age <= 0.5:
            quality = ConnectionQuality.EXCELLENT
        elif age <= self.config.stale_s:
            quality = ConnectionQuality.GOOD
        else:
            quality = ConnectionQuality.POOR
        mode = self._mode
        if mode is FlightMode.HOLD and have_state and height < 0.15 and age <= self.config.stale_s:
            mode = FlightMode.IDLE  # it landed on its own (e.g. low battery)
        return Telemetry(
            connected=True,
            armed=self._armed,
            flight_mode=mode,
            altitude=height,
            z=height,
            velocity=speed,
            ground_speed=ground,
            heading=s.get("yaw", 0.0) % 360.0,  # relative to the takeoff heading, not compass
            battery_percentage=s.get("bat", 0.0),
            battery_available=have_state and "bat" in s,
            altitude_available=have_state and "h" in s,
            velocity_available=have_state,
            position_available=False,
            connection_quality=quality,
            link_age_s=age,
        )

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            name="DJI/Ryze Tello (SDK)",
            adapter_type="tello",
            is_simulated=False,
            supports_position=False,
            supports_gps=False,
            supports_camera=True,
            supports_goto=False,
            supports_return=False,
            max_altitude_m=self.config.max_altitude_m,
            supported_capabilities=["arm", "takeoff", "land", "hold", "velocity", "heading", "battery", "camera"],
        )


def _steps(total_cm: int, max_step: int) -> list[int]:
    """Split a climb into SDK-legal steps (20–500 cm each)."""
    steps = []
    remaining = total_cm
    while remaining > 0:
        step = min(max_step, remaining)
        if remaining - step and remaining - step < 20:
            step = remaining - 20
        steps.append(step)
        remaining -= step
    return [s for s in steps if s >= 20]
