"""VantaBus: LATEST / BOUNDED_STREAM / RELIABLE_EVENT semantics, typing, metrics, health."""
from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

from vantaflight.bus import (
    BackpressureError,
    BusError,
    ChannelSpec,
    ChannelTypeError,
    DeliveryMode,
    LatestChannel,
    ReliableChannel,
    StreamChannel,
    VantaBus,
    channel_health,
)
from vantaflight.contracts import EnvelopeStamper
from vantaflight.contracts.payloads import FailsafeTriggerV1, RaceEventV1
from vantaflight.core.clock import SequentialIds, VantaClock
from vantaflight.core.health import HealthDimension, HealthState
from vantaflight.vision import FrameBuffer, FramePacket, PreviewBuffer


class Clock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def frame(i: int, t: float) -> FramePacket:
    return FramePacket(np.zeros((4, 4, 3), np.uint8), capture_timestamp=t, sequence=i, source_id="cam")


def latest(capacity: int = 2, clock=None, **kw) -> LatestChannel:
    return LatestChannel(ChannelSpec("test.latest", DeliveryMode.LATEST, capacity, item_type=int, **kw), clock or Clock())


# -- LATEST ----------------------------------------------------------------------------------
def test_latest_channel_refuses_a_deep_queue():
    with pytest.raises(ValueError, match="at most 2"):
        ChannelSpec("camera.frames", DeliveryMode.LATEST, 3, item_type=FramePacket)
    with pytest.raises(ValueError, match="at most 2"):
        FrameBuffer(capacity=40)


def test_camera_queue_cannot_grow_unbounded():
    clock = Clock()
    buffer = FrameBuffer(clock=clock)
    for i in range(1000):
        clock.t += 0.001
        buffer.put(frame(i, clock.t))
        assert buffer.metrics.depth <= 2
    m = buffer.metrics
    assert m.received == 1000 and m.depth == 2 and m.dropped_capacity == 998
    newest = buffer.latest()
    assert newest.sequence == 999, "the consumer acts on the newest frame, not on history"
    m = buffer.metrics
    assert m.depth == 0 and m.dropped_stale == 1 and m.delivered == 1
    assert m.dropped_capacity + m.dropped_stale + m.delivered == m.received


def test_latest_drops_frames_older_than_max_age():
    clock = Clock()
    buffer = FrameBuffer(clock=clock)
    buffer.put(frame(1, clock.t))
    clock.t += 0.5
    assert buffer.latest(max_age_s=0.2) is None
    assert buffer.metrics.dropped_stale == 1
    buffer.put(frame(2, clock.t))
    assert buffer.latest(max_age_s=0.2).sequence == 2
    with pytest.raises(ValueError):
        buffer.latest(max_age_s=-1)


def test_latest_reader_sees_the_current_value_and_readers_are_independent():
    channel = latest()
    channel.publish(1)
    channel.publish(2)
    late = channel.subscribe("late")  # joins after publication: gets the current value
    fresh = channel.subscribe("fresh", from_latest=False)
    assert late.take() == 2 and late.take() is None
    assert fresh.take() is None
    channel.publish(3)
    assert fresh.take() == 3 and late.take() == 3
    assert channel.metrics()["published"] == 3


def test_latest_channel_type_is_enforced():
    channel = latest()
    with pytest.raises(ChannelTypeError):
        channel.publish("not an int")


def test_preview_never_waits_for_its_reader():
    preview = PreviewBuffer()
    preview.channel._lock.acquire()  # a reader is busy with the buffer
    try:
        assert preview.put_nowait(frame(1, 0.0)) is False
    finally:
        preview.channel._lock.release()
    assert preview.put_nowait(frame(2, 0.0)) is True
    assert preview.put_nowait(frame(3, 0.0)) is True
    m = preview.metrics
    assert m.received == 2 and m.dropped == 2 and m.depth == 1
    assert preview.latest().sequence == 3


async def test_latest_next_wakes_on_publish_from_another_thread():
    channel = LatestChannel(ChannelSpec("test.latest", DeliveryMode.LATEST, 1, item_type=int))
    reader = channel.subscribe("r", from_latest=False)
    threading.Timer(0.05, channel.publish, args=(7,)).start()
    assert await reader.next(timeout=2.0) == 7
    assert await reader.next(timeout=0.05) is None


# -- BOUNDED_STREAM ---------------------------------------------------------------------------
def test_stream_slow_reader_loses_oldest_with_accounting_fast_reader_unaffected():
    channel = StreamChannel(ChannelSpec("test.stream", DeliveryMode.BOUNDED_STREAM, 4, item_type=int), Clock())
    slow = channel.subscribe("slow")
    fast = channel.subscribe("fast")
    got_fast = []
    for i in range(10):
        channel.publish(i)
        got_fast.extend(fast.drain())
    assert got_fast == list(range(10))
    assert slow.lag == 4 and slow.dropped_superseded == 6
    assert slow.drain() == [6, 7, 8, 9]
    m = channel.metrics()
    assert m["dropped"]["superseded"] == 6 and m["consumer_lag"] == 0 and m["depth"] == 0


def test_stream_reader_can_start_from_retained_history():
    channel = StreamChannel(ChannelSpec("test.stream", DeliveryMode.BOUNDED_STREAM, 3, item_type=int), Clock())
    for i in range(5):
        channel.publish(i)
    assert channel.subscribe("history", from_start=True).drain() == [2, 3, 4]
    assert channel.subscribe("live").drain() == []


# -- RELIABLE_EVENT ----------------------------------------------------------------------------
def reliable(capacity: int = 3, clock=None) -> ReliableChannel:
    return ReliableChannel(
        ChannelSpec("test.reliable", DeliveryMode.RELIABLE_EVENT, capacity, item_type=int), clock or Clock()
    )


def test_reliable_events_are_never_dropped_silently():
    channel = reliable(capacity=3)
    consumer = channel.subscribe("safety_consumer", required=True)
    for i in range(3):
        channel.publish(i)
    with pytest.raises(BackpressureError, match="safety_consumer"):
        channel.publish(3)
    assert channel.published == 3 and channel.rejected == 1, "the refused event was not half-accepted"
    first = consumer.receive_nowait()
    assert (first.seq, first.item) == (0, 0)
    consumer.ack(first.seq)
    channel.publish(3)  # room again
    received = [d.item for d in iter(consumer.receive_nowait, None)]
    assert received == [1, 2, 3]
    consumer.ack(3)  # cumulative
    assert channel.pending == 0


def test_reliable_sink_failure_keeps_the_event_until_it_is_persisted():
    clock = Clock()
    channel = reliable(capacity=4, clock=clock)
    stored, broken = [], [True]

    def sink(item):
        if broken[0]:
            raise OSError("disk full")
        stored.append(item)

    channel.add_sink("db", sink)
    receipt = channel.publish(1)
    assert not receipt.persisted and receipt.pending == ("db",)
    assert channel.pending == 1
    health = channel_health(channel, clock())
    assert health.dimension(HealthDimension.CORRECTNESS).state is HealthState.FAILED
    broken[0] = False
    assert channel.publish(2).persisted  # the retry runs first, so order is kept
    assert stored == [1, 2] and channel.pending == 0
    assert channel_health(channel, clock()).dimension(HealthDimension.CORRECTNESS).state is HealthState.HEALTHY


def test_reliable_backlog_full_of_unpersisted_events_refuses_new_ones():
    channel = reliable(capacity=2)
    channel.add_sink("db", lambda item: (_ for _ in ()).throw(OSError("down")))
    channel.publish(1)
    channel.publish(2)
    with pytest.raises(BackpressureError, match="db"):
        channel.publish(3)
    health = channel_health(channel, channel._clock())
    assert health.dimension(HealthDimension.RESOURCE).state is HealthState.FAILED


def test_best_effort_reader_of_a_reliable_channel_cannot_block_the_publisher():
    channel = reliable(capacity=2)
    channel.add_sink("db", lambda item: None)
    ui = channel.subscribe("ui")  # best effort
    for i in range(10):
        channel.publish(i)
    assert channel.pending == 0
    assert ui.dropped_superseded == 8
    assert [d.item for d in iter(ui.receive_nowait, None)] == [8, 9]


def test_unsubscribing_a_required_reader_releases_its_pending_events():
    channel = reliable(capacity=2)
    reader = channel.subscribe("r", required=True)
    channel.publish(1)
    channel.unsubscribe(reader)
    assert channel.pending == 0 and channel.released == 1


async def test_required_reader_can_wait_for_events():
    channel = reliable(capacity=4, clock=Clock())
    reader = channel.subscribe("r", required=True)
    asyncio.get_running_loop().call_later(0.02, channel.publish, 5)
    delivery = await reader.receive(timeout=2.0)
    assert delivery.item == 5
    reader.ack(delivery.seq)
    assert channel.pending == 0


# -- the bus ------------------------------------------------------------------------------------------
def stamper() -> EnvelopeStamper:
    return EnvelopeStamper("test.component", VantaClock.fake(), ids=SequentialIds(3))


def test_contract_channels_only_carry_their_registered_payload():
    bus = VantaBus(Clock())
    channel = bus.reliable("safety.failsafe", capacity=8)
    assert channel.spec.safety_critical, "flight-critical contracts make safety-critical channels"
    env = stamper().stamp(FailsafeTriggerV1(reason="battery_low", action="return", message="low"))
    bus.publish(env)
    wrong_topic = stamper().stamp(RaceEventV1(event_type="x", message="y"))
    with pytest.raises(BusError):
        bus.publish(wrong_topic)  # race.event has no channel
    with pytest.raises(ChannelTypeError):
        channel.publish(wrong_topic)  # and cannot ride on safety.failsafe
    with pytest.raises(ChannelTypeError):
        channel.publish({"reason": "battery_low"})  # never a loose dict
    with pytest.raises(BusError, match="contract topic"):
        bus.declare("safety.failsafe", DeliveryMode.RELIABLE_EVENT, 8, item_type=dict)


def test_non_contract_channels_must_declare_their_type_and_declarations_agree():
    bus = VantaBus(Clock())
    with pytest.raises(BusError, match="item_type"):
        bus.declare("camera.frames.front", DeliveryMode.LATEST, 2)
    first = bus.latest("camera.frames.front", capacity=2, item_type=FramePacket)
    assert bus.latest("camera.frames.front", capacity=2, item_type=FramePacket) is first
    with pytest.raises(BusError, match="already declared"):
        bus.latest("camera.frames.front", capacity=1, item_type=FramePacket)
    assert bus.ensure("camera.frames.front", DeliveryMode.LATEST, 1) is first
    with pytest.raises(BusError):
        bus.ensure("camera.frames.front", DeliveryMode.BOUNDED_STREAM, 4)


def test_channel_metrics_report_rate_age_depth_drops_and_lag():
    clock = Clock()
    bus = VantaBus(clock)
    channel = bus.stream("diag.samples", capacity=8, item_type=int, expected_rate_hz=10.0)
    reader = channel.subscribe("analyzer")
    for i in range(20):
        channel.publish(i)
        clock.t += 0.1
    for _ in range(4):
        reader.get_nowait()
    m = bus.status(clock())["channels"][0]
    assert m["rate_hz"] == pytest.approx(10.0, rel=0.01)
    assert m["published"] == 20 and m["delivered"] == 4
    assert m["dropped"]["superseded"] == 12 and m["depth"] == 4 and m["consumer_lag"] == 4
    # items 12..15 were read at t=102.0, 0.8 / 0.7 / 0.6 / 0.5 s after publication
    assert m["age_ms"]["p50"] == pytest.approx(650.0, abs=1.0)
    assert m["age_ms"]["max"] == pytest.approx(800.0, abs=1.0)
    assert m["consumer_lag_s"] == pytest.approx(0.4, abs=1e-6)  # item 16, unread for 0.4 s
    assert m["drop_fraction"] == pytest.approx(12 / 16)


def test_channel_freshness_follows_the_promised_rate():
    clock = Clock()
    bus = VantaBus(clock)
    channel = bus.latest("flight.snapshot", item_type=int, expected_rate_hz=10.0)
    assert channel_health(channel, clock()).dimension(HealthDimension.FRESHNESS).state is HealthState.UNKNOWN
    channel.publish(1)
    for age, state in ((0.1, HealthState.HEALTHY), (0.5, HealthState.DEGRADED), (2.0, HealthState.FAILED)):
        dim = channel_health(channel, clock() + age).dimension(HealthDimension.FRESHNESS)
        assert dim.state is state, (age, dim)


def test_concurrent_publishers_are_counted_exactly():
    channel = StreamChannel(ChannelSpec("test.stream", DeliveryMode.BOUNDED_STREAM, 100_000, item_type=int))
    reader = channel.subscribe("r")
    threads = [threading.Thread(target=lambda: [channel.publish(i) for i in range(2000)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert channel.published == 8000 and len(reader.drain()) == 8000
