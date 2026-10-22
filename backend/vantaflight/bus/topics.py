"""The channels the flight core, authority and health publish on.

One place declares them so every component (and VantaSystem) agrees on mode
and capacity. See docs/RUNTIME.md for each channel's contract.
"""
from __future__ import annotations

from .bus import VantaBus
from .channels import DeliveryMode

FLIGHT_EVENT = "flight.event"
EXECUTION_COMMAND = "execution.command"
SAFETY_FAILSAFE = "safety.failsafe"
AUTHORITY_DECISION = "authority.decision"
SYSTEM_HEALTH = "system.health"
FLIGHT_TELEMETRY = "flight.telemetry"

#: Reliable channels and their backlog limits (events awaiting persistence/ack).
RELIABLE_CAPACITY = {
    FLIGHT_EVENT: 1024,
    EXECUTION_COMMAND: 1024,
    SAFETY_FAILSAFE: 256,
    AUTHORITY_DECISION: 1024,
}


def declare_core_channels(bus: VantaBus, *, telemetry_rate_hz: float | None = None) -> None:
    from ..models import Telemetry  # the operator-facing snapshot the UI renders

    for topic, capacity in RELIABLE_CAPACITY.items():
        bus.ensure(topic, DeliveryMode.RELIABLE_EVENT, capacity)
    bus.ensure(SYSTEM_HEALTH, DeliveryMode.BOUNDED_STREAM, 256)
    bus.ensure(
        FLIGHT_TELEMETRY, DeliveryMode.LATEST, 1, item_type=Telemetry,
        expected_rate_hz=telemetry_rate_hz, safety_critical=True,
        description="Latest telemetry sampled by the flight tick (what the UI and health read).",
    )
