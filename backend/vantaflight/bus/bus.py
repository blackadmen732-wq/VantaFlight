"""VantaBus — the in-process typed data plane.

One drone does not need Kafka. The bus is a registry of typed channels
(see ``channels.py``) with per-topic metrics that VantaHealth reads:
rate f, delivered-age p50/p95/p99, queue depth, drops and consumer lag.

Typing: a channel whose topic is a registered contract (``vehicle.state``,
``safety.failsafe``, …) only carries ``EventEnvelope``s of that topic whose
payload is the registered payload class. Any other channel declares the
Python type it carries (camera ``FramePacket``s, telemetry snapshots), and
publishing anything else raises ``ChannelTypeError``.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from ..contracts.envelope import EventEnvelope
from ..contracts.registry import ContractRegistry, UnknownTopicError, default_registry
from ..core.health import ComponentHealth, DimensionStatus, HealthDimension, HealthState
from .channels import (
    RECENT_S,
    BusError,
    Channel,
    ChannelSpec,
    ChannelTypeError,
    DeliveryMode,
    LatestChannel,
    PublishReceipt,
    ReliableChannel,
    StreamChannel,
    make_channel,
)


class VantaBus:
    def __init__(self, clock: Callable[[], float] | None = None, registry: ContractRegistry | None = None) -> None:
        #: Seconds on the host monotonic clock (a VantaClock is callable too).
        self._clock = clock or time.monotonic
        self._registry = registry or default_registry()
        self._channels: dict[str, Channel] = {}
        self._lock = threading.Lock()

    @property
    def registry(self) -> ContractRegistry:
        return self._registry

    def clock(self) -> float:
        return self._clock()

    # -- channels -------------------------------------------------------------------------
    def _contract_entry(self, topic: str):
        try:
            return self._registry.entry(topic)
        except UnknownTopicError:
            return None

    def _envelope_check(self, topic: str, payload_type: type) -> Callable[[Any], None]:
        def check(item: Any) -> None:
            if not isinstance(item, EventEnvelope):
                raise ChannelTypeError(f"{topic} carries EventEnvelope[{payload_type.__name__}], not {type(item).__name__}")
            if item.topic != topic:
                raise ChannelTypeError(f"envelope for {item.topic} published on {topic}")
            if type(item.payload) is not payload_type:
                raise ChannelTypeError(
                    f"{topic} carries {payload_type.__name__}, not {type(item.payload).__name__}"
                )

        return check

    def declare(
        self,
        topic: str,
        mode: DeliveryMode | str,
        capacity: int,
        *,
        item_type: type | tuple[type, ...] | None = None,
        max_age_s: float | None = None,
        expected_rate_hz: float | None = None,
        safety_critical: bool | None = None,
        description: str = "",
    ) -> Channel:
        """Create a channel, or return the existing one if declared identically."""
        entry = self._contract_entry(topic)
        if entry is not None and item_type is not None:
            raise BusError(f"{topic} is a contract topic; it carries EventEnvelope[{entry.payload_type.__name__}]")
        if entry is None and item_type is None:
            raise BusError(f"{topic} is not a registered contract, so declare the item_type it carries")
        if safety_critical is None:
            safety_critical = bool(entry.flight_critical) if entry is not None else False
        spec = ChannelSpec(
            topic, DeliveryMode(mode), capacity, item_type=item_type, max_age_s=max_age_s,
            expected_rate_hz=expected_rate_hz, safety_critical=safety_critical,
            description=description or (entry.description if entry is not None else ""),
        )
        with self._lock:
            existing = self._channels.get(topic)
            if existing is not None:
                if existing.spec != spec:
                    raise BusError(f"{topic} is already declared as {existing.spec}")
                return existing
            channel = make_channel(spec, self._clock)
            if entry is not None:
                channel.set_type_check(self._envelope_check(topic, entry.payload_type))
            self._channels[topic] = channel
            return channel

    def ensure(self, topic: str, mode: DeliveryMode | str, capacity: int, **kw) -> Channel:
        """The channel for ``topic``, declaring it if nobody has yet.

        Components that can run on their own (tests, tools) declare the
        channels they need with ``ensure``; when VantaSystem declared them
        first, its tuning (capacity, expected rate) stands.
        """
        existing = self._channels.get(topic)
        if existing is not None:
            if existing.mode is not DeliveryMode(mode):
                raise BusError(f"{topic} is {existing.mode.value}, not {DeliveryMode(mode).value}")
            return existing
        try:
            return self.declare(topic, mode, capacity, **kw)
        except BusError:
            existing = self._channels.get(topic)  # declared concurrently
            if existing is None:
                raise
            return existing

    def latest(self, topic: str, *, capacity: int = 1, **kw) -> LatestChannel:
        return self.declare(topic, DeliveryMode.LATEST, capacity, **kw)  # type: ignore[return-value]

    def stream(self, topic: str, *, capacity: int, **kw) -> StreamChannel:
        return self.declare(topic, DeliveryMode.BOUNDED_STREAM, capacity, **kw)  # type: ignore[return-value]

    def reliable(self, topic: str, *, capacity: int, **kw) -> ReliableChannel:
        return self.declare(topic, DeliveryMode.RELIABLE_EVENT, capacity, **kw)  # type: ignore[return-value]

    def attach(self, channel: Channel) -> Channel:
        """Register a channel built elsewhere (e.g. a camera's frame buffer)."""
        with self._lock:
            existing = self._channels.get(channel.topic)
            if existing is not None and existing is not channel:
                raise BusError(f"{channel.topic} is already on the bus")
            self._channels[channel.topic] = channel
        return channel

    def detach(self, topic: str) -> None:
        with self._lock:
            self._channels.pop(topic, None)

    def channel(self, topic: str) -> Channel:
        try:
            return self._channels[topic]
        except KeyError:
            raise BusError(f"no channel {topic!r} on the bus") from None

    def has(self, topic: str) -> bool:
        return topic in self._channels

    def channels(self) -> list[Channel]:
        with self._lock:
            return [self._channels[t] for t in sorted(self._channels)]

    # -- traffic -----------------------------------------------------------------------------
    def publish(self, topic_or_envelope: str | EventEnvelope, item: Any = None) -> PublishReceipt:
        if isinstance(topic_or_envelope, EventEnvelope):
            return self.channel(topic_or_envelope.topic).publish(topic_or_envelope)
        return self.channel(topic_or_envelope).publish(item)

    def subscribe(self, topic: str, name: str, **options):
        return self.channel(topic).subscribe(name, **options)

    def maintain(self) -> int:
        """Retry persistence of reliable events whose sinks failed earlier."""
        retried = 0
        for channel in self.channels():
            if isinstance(channel, ReliableChannel):
                retried += channel.retry_pending()
        return retried

    # -- observability ---------------------------------------------------------------------------
    def status(self, now_s: float | None = None) -> dict:
        now_s = self._clock() if now_s is None else now_s
        channels = [c.metrics(now_s) for c in self.channels()]
        return {
            "channels": channels,
            "totals": {
                "channels": len(channels),
                "published": sum(c["published"] for c in channels),
                "pending_reliable": sum(c.get("pending", 0) for c in channels),
                "rejected_reliable": sum(c.get("rejected", 0) for c in channels),
            },
        }

    def health(self, now_s: float | None = None) -> Iterable[ComponentHealth]:
        """One health component per channel: freshness, resources, correctness."""
        now_s = self._clock() if now_s is None else now_s
        for channel in self.channels():
            yield channel_health(channel, now_s)


def channel_health(channel: Channel, now_s: float) -> ComponentHealth:
    spec = channel.spec
    dims: dict[HealthDimension, DimensionStatus] = {}

    # FRESHNESS: is the producer keeping its promised rate?
    last = channel._last_publish_s
    if last is None:
        dims[HealthDimension.FRESHNESS] = DimensionStatus(HealthState.UNKNOWN, "nothing published yet")
    elif spec.expected_rate_hz:
        age = now_s - last
        period = 1.0 / spec.expected_rate_hz
        if age > 10 * period:
            state = HealthState.FAILED
        elif age > 3 * period:
            state = HealthState.DEGRADED
        else:
            state = HealthState.HEALTHY
        dims[HealthDimension.FRESHNESS] = DimensionStatus(
            state, f"last publish {age * 1000:.0f} ms ago (expected every {period * 1000:.0f} ms)", age
        )
    else:
        dims[HealthDimension.FRESHNESS] = DimensionStatus(HealthState.HEALTHY, "event-driven", now_s - last)

    # RESOURCE: bounded queues behaving as designed?
    recent_drop = (channel.last_drop_s() or -1e18) > now_s - RECENT_S
    if isinstance(channel, ReliableChannel):
        pending = channel.pending
        rejected_recently = (channel.last_reject_s or -1e18) > now_s - RECENT_S
        if rejected_recently:
            dims[HealthDimension.RESOURCE] = DimensionStatus(
                HealthState.FAILED, f"refused {channel.rejected} event(s): backlog full", pending
            )
        elif pending >= 0.8 * channel.capacity:
            dims[HealthDimension.RESOURCE] = DimensionStatus(
                HealthState.DEGRADED, f"{pending}/{channel.capacity} events awaiting acknowledgement", pending
            )
        else:
            dims[HealthDimension.RESOURCE] = DimensionStatus(
                HealthState.HEALTHY, f"{pending}/{channel.capacity} pending", pending
            )
        failing = [s for s in channel._sinks.values() if s.last_failure_s is not None
                   and any(s.name in e.pending for e in channel._retained.values())]
        if failing:
            dims[HealthDimension.CORRECTNESS] = DimensionStatus(
                HealthState.FAILED,
                "; ".join(f"{s.name}: {s.last_error}" for s in failing),
            )
        else:
            dims[HealthDimension.CORRECTNESS] = DimensionStatus(HealthState.HEALTHY, "all sinks persisting")
    elif spec.mode is DeliveryMode.BOUNDED_STREAM and recent_drop:
        dims[HealthDimension.RESOURCE] = DimensionStatus(
            HealthState.DEGRADED, "a reader fell behind and lost history", sum(channel.dropped().values())
        )
    else:
        # LATEST channels drop stale items by design; that is health, not a fault.
        depth = channel.metrics(now_s)["depth"]
        dims[HealthDimension.RESOURCE] = DimensionStatus(
            HealthState.HEALTHY, f"depth {depth}/{channel.capacity}", depth
        )
    return ComponentHealth(f"bus:{channel.topic}", "bus.channel", dims)
