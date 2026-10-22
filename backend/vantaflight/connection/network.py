"""Network check: "can this computer reach my drone?" in plain English.

On a Chromebook VantaFlight runs inside the Linux container, behind ChromeOS'
NAT. Outgoing traffic (commands to a drone) just works; *incoming* UDP
streams on fixed ports (Tello state on 8890, video on 11111, MAVLink on
14550/14540 from a real aircraft) only arrive after you add them under
Settings → Developers → Linux development environment → Port forwarding,
and those forwards switch off after every reboot.

This module finds out what it can without sending anything to the drone
that changes its state: which network the computer is on, whether the
Hopper camera answers, and which ports each drone needs.
"""
from __future__ import annotations

import asyncio
import os
import platform
import socket
from dataclasses import dataclass

HOPPER_HOST = "192.168.2.1"
TELLO_HOST = "192.168.10.1"


@dataclass(frozen=True)
class PortNeed:
    port: int
    protocol: str
    direction: str  # "in" = the drone sends to us (needs forwarding on ChromeOS)
    used_for: str
    drones: tuple[str, ...]

    def to_dict(self, chromeos: bool) -> dict:
        return {
            "port": self.port,
            "protocol": self.protocol,
            "direction": self.direction,
            "used_for": self.used_for,
            "drones": list(self.drones),
            "forward_on_chromeos": chromeos and self.direction == "in",
        }


PORTS = (
    PortNeed(8890, "UDP", "in", "Tello state (height, battery, attitude)", ("tello",)),
    PortNeed(11111, "UDP", "in", "Tello video stream", ("tello",)),
    PortNeed(8889, "UDP", "out", "Tello commands (replies come back on the same socket)", ("tello",)),
    PortNeed(14550, "UDP", "in", "MAVLink from a real PX4/ArduPilot aircraft or radio bridge", ("px4",)),
    PortNeed(14540, "UDP", "in", "MAVLink offboard link from PX4 SITL on another computer", ("px4_sitl",)),
    PortNeed(80, "TCP", "out", "Hopper camera page at http://192.168.2.1", ("hopper",)),
)


def in_chromeos_linux() -> bool:
    return (
        os.path.exists("/opt/google/cros-containers")
        or os.path.exists("/dev/.cros_milestone")
        or socket.gethostname() == "penguin"
    )


def route_source(host: str) -> str | None:
    """The local address the OS would use to reach ``host`` (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        return None


def same_subnet(local: str | None, host: str) -> bool:
    return bool(local) and local.rsplit(".", 1)[0] == host.rsplit(".", 1)[0]


async def tcp_reachable(host: str, port: int, timeout_s: float = 0.6) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def network_check(*, hopper_host: str = HOPPER_HOST, hopper_port: int = 80,
                        tello_host: str = TELLO_HOST) -> dict:
    chromeos = in_chromeos_linux()
    hopper_local = route_source(hopper_host)
    tello_local = route_source(tello_host)
    hopper_up = await tcp_reachable(hopper_host, hopper_port)
    on_tello_wifi = same_subnet(tello_local, tello_host)

    advice: list[str] = []
    if hopper_up:
        advice.append("Hopper's camera answers: open the Hopper page and press OPEN HOPPER CAMERA.")
    else:
        advice.append("Hopper camera not reachable: join the Hopper_#### Wi-Fi, then check again.")
    if on_tello_wifi:
        advice.append("You are on a Tello network. Pick the Tello source and press Connect.")
        if chromeos:
            advice.append("ChromeOS: add UDP 8890 (and 11111 for video) under Settings → Developers → "
                          "Linux → Port forwarding, or telemetry will not arrive. Re-enable after every reboot.")
    if chromeos:
        advice.append("ChromeOS Linux can't use Bluetooth directly; Bluetooth drones are flown from their own Chrome apps.")
    advice.append("The mock drone needs no network at all; PX4 SITL needs the simulator running (see docs/PX4.md).")

    return {
        "environment": {
            "chromeos_linux": chromeos,
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
        },
        "routes": {
            "to_hopper": hopper_local,
            "to_tello": tello_local,
        },
        "drones": {
            "hopper_camera_reachable": hopper_up,
            "on_tello_wifi": on_tello_wifi,
        },
        "ports": [p.to_dict(chromeos) for p in PORTS],
        "advice": advice,
    }
