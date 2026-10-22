"""VantaSystem — the one composition root.

It builds the foundation services once and hands them to everything else:
the clock, the contract registry, the bus, the runtime, health and
authority. No subsystem constructs another major subsystem privately; tests
substitute a fake clock or sleep here, in one place.

Later spec PRs add the remaining members (profiles, vehicles, state,
physics, …) to this same object.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..bus import BackpressureError, VantaBus
from ..bus.topics import AUTHORITY_DECISION, SYSTEM_HEALTH, declare_core_channels
from ..contracts.envelope import EnvelopeStamper
from ..contracts.payloads import AuthorityDecisionV1, HealthChangeV1
from ..contracts.registry import ContractRegistry, default_registry
from .authority import AuthorityDecision, VantaAuthority
from .clock import VantaClock, default_clock
from .health import HealthChange, VantaHealth
from .runtime import Criticality, VantaRuntime, default_runtime, set_default_runtime

logger = logging.getLogger(__name__)

HEALTH_PERIOD_S = 0.5
BUS_MAINTENANCE_PERIOD_S = 1.0


@dataclass
class VantaSystem:
    clock: VantaClock
    contracts: ContractRegistry
    bus: VantaBus
    runtime: VantaRuntime
    health: VantaHealth
    authority: VantaAuthority
    _previous_default: VantaRuntime | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        clock: VantaClock | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        registry: ContractRegistry | None = None,
        telemetry_rate_hz: float | None = None,
    ) -> "VantaSystem":
        clock = clock or default_clock()
        contracts = registry or default_registry()
        system = cls(
            clock=clock,
            contracts=contracts,
            bus=VantaBus(clock, contracts),
            runtime=VantaRuntime(clock, sleep=sleep),
            health=VantaHealth(clock),
            authority=VantaAuthority(clock),
        )
        system._wire(telemetry_rate_hz)
        return system

    def _wire(self, telemetry_rate_hz: float | None) -> None:
        declare_core_channels(self.bus, telemetry_rate_hz=telemetry_rate_hz)
        authority_stamper = EnvelopeStamper("core.authority", self.clock, self.contracts)
        health_stamper = EnvelopeStamper("core.health", self.clock, self.contracts)

        def on_decision(decision: AuthorityDecision) -> None:
            self._publish(AUTHORITY_DECISION, authority_stamper.stamp(AuthorityDecisionV1.from_decision(decision)))

        def on_health_change(change: HealthChange) -> None:
            self._publish(SYSTEM_HEALTH, health_stamper.stamp(HealthChangeV1.from_change(change)))

        self.authority.on_decision(on_decision)
        self.health.on_change(on_health_change)
        self.health.register_probe("bus", self.bus.health)
        self.health.register_probe("runtime", self.runtime.health)

    def _publish(self, topic: str, envelope) -> None:
        # Recording a decision must never undo or block the decision itself.
        try:
            self.bus.publish(envelope)
        except BackpressureError:
            logger.critical("%s: backlog full; this record was refused (see bus status)", topic)

    # -- lifecycle ---------------------------------------------------------------------------
    async def start(self) -> None:
        """Runtime STARTING → READY, with the system's own housekeeping workers."""
        await self.runtime.start()
        self._previous_default = default_runtime()
        # Components built without explicit injection (adapters created on
        # connect) register their workers with this runtime.
        set_default_runtime(self.runtime)
        self.runtime.spawn_periodic(
            "health.monitor", self.health.evaluate, period_s=HEALTH_PERIOD_S,
            owner="core.health", criticality=Criticality.NORMAL,
            description="Evaluates every health probe and publishes state changes",
        )
        self.runtime.spawn_periodic(
            "bus.maintenance", self.bus.maintain, period_s=BUS_MAINTENANCE_PERIOD_S,
            owner="core.bus", criticality=Criticality.NORMAL,
            description="Retries reliable events whose persistence failed",
        )
        self.runtime.mark_ready()

    def activate(self) -> None:
        self.runtime.activate()

    async def stop(self, timeout_s: float = 10.0) -> dict:
        """Stop every remaining worker; afterwards the runtime holds zero workers."""
        report = await self.runtime.shutdown(timeout_s)
        if self._previous_default is not None and default_runtime() is self.runtime:
            set_default_runtime(self._previous_default)
        self._previous_default = None
        return report

    def status(self) -> dict:
        return {
            "runtime": self.runtime.status(),
            "health": self.health.to_dict(),
            "bus": self.bus.status(),
            "authority": self.authority.status(),
        }
