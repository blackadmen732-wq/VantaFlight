"""VantaBus channels: three delivery semantics, every drop accounted for.

=================  ==========================  ===========================================
Mode               Example                     Rule
=================  ==========================  ===========================================
LATEST             camera frames, telemetry    1–2 items; a reader gets the newest and
                                               the older ones are counted as stale
BOUNDED_STREAM     state history, diagnostics  bounded FIFO; a reader that falls behind
                                               loses the oldest items, counted per reader
RELIABLE_EVENT     safety events, commands,    kept until every *required* party has
                   mission transitions         persisted (sink) or acknowledged it; when
                                               the backlog is full ``publish`` raises
                                               ``BackpressureError`` — never a silent drop
=================  ==========================  ===========================================

A camera queue that holds 40 frames is a bug: the aircraft would react to
history. LATEST channels therefore refuse a capacity above 2.

Channels are thread-safe (a camera thread may publish while the event loop
reads). Readers are cursors over a bounded ring: lag is ``head - cursor`` and
an item evicted before a reader saw it is counted against that reader.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from ..core.metrics import RateMeter, RollingWindow, rounded, rounded_percentiles

logger = logging.getLogger(__name__)

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
#: Seconds during which a recent drop/reject still colours health.
RECENT_S = 10.0


class DeliveryMode(str, enum.Enum):
    LATEST = "LATEST"
    BOUNDED_STREAM = "BOUNDED_STREAM"
    RELIABLE_EVENT = "RELIABLE_EVENT"


class BusError(RuntimeError):
    pass


class BackpressureError(BusError):
    """A reliable channel is full of events nobody has acknowledged yet."""


class ChannelTypeError(BusError, TypeError):
    """An item of the wrong type was published on a typed channel."""


@dataclass(frozen=True)
class ChannelSpec:
    topic: str
    mode: DeliveryMode
    capacity: int
    #: Python type of items on a non-contract channel (frames, telemetry
    #: snapshots). Contract channels carry ``EventEnvelope``s checked by the bus.
    item_type: type | tuple[type, ...] | None = None
    #: LATEST: readers get nothing older than this (None = no limit).
    max_age_s: float | None = None
    #: Publication rate the producer promises; drives freshness health.
    expected_rate_hz: float | None = None
    safety_critical: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", DeliveryMode(self.mode))
        if not _TOPIC_RE.match(self.topic) or len(self.topic) > 96:
            raise ValueError(f"invalid topic {self.topic!r} (lowercase dotted, e.g. 'camera.frames.front')")
        if isinstance(self.capacity, bool) or not isinstance(self.capacity, int) or self.capacity < 1:
            raise ValueError(f"{self.topic}: capacity must be a positive integer")
        if self.mode is DeliveryMode.LATEST and self.capacity > 2:
            raise ValueError(
                f"{self.topic}: a LATEST channel keeps at most 2 items (got {self.capacity}); "
                "a deeper queue makes consumers react to history"
            )
        if self.capacity > 100_000:
            raise ValueError(f"{self.topic}: capacity {self.capacity} is not a bounded queue")
        if self.max_age_s is not None and not (self.max_age_s >= 0 and math.isfinite(self.max_age_s)):
            raise ValueError(f"{self.topic}: max_age_s must be a non-negative number")
        if self.expected_rate_hz is not None and not self.expected_rate_hz > 0:
            raise ValueError(f"{self.topic}: expected_rate_hz must be positive")


@dataclass(frozen=True, slots=True)
class _Slot:
    seq: int
    item: Any
    published_s: float
    origin_s: float  # when the data was true (capture time if known)


@dataclass(frozen=True)
class Delivery:
    """A reliable event handed to a reader; ``ack(seq)`` when done with it."""

    seq: int
    item: Any
    published_s: float


@dataclass(frozen=True)
class PublishReceipt:
    seq: int
    #: RELIABLE_EVENT only: every required sink persisted the event.
    persisted: bool = True
    #: RELIABLE_EVENT only: required parties that still hold it.
    pending: tuple[str, ...] = ()


def origin_seconds(item: Any, published_s: float) -> float:
    """When an item's data was true, in host-monotonic seconds, if known.

    Envelopes use ``captured_at`` (or ``received_at``) when it is on the host
    monotonic clock; frames use ``capture_timestamp``. Anything else is dated
    by its publication.
    """
    for attr in ("captured_at", "received_at"):
        stamp = getattr(item, attr, None)
        domain = getattr(stamp, "domain", None)
        if domain is not None and getattr(domain, "value", domain) == "HOST_MONOTONIC" and not stamp.clock_id:
            return stamp.seconds
    capture = getattr(item, "capture_timestamp", None)
    if isinstance(capture, (int, float)) and not isinstance(capture, bool) and math.isfinite(capture):
        return float(capture)
    return published_s


_USE_SPEC = object()


# -- readers -------------------------------------------------------------------------------------
class _Reader:
    """A cursor over a channel's ring buffer plus its own accounting."""

    def __init__(self, channel: "Channel", name: str, cursor: int) -> None:
        self.channel = channel
        self.name = name
        self._cursor = cursor
        self.delivered = 0
        self.dropped_superseded = 0  # evicted before this reader saw it (LATEST/STREAM overflow)
        self.dropped_stale = 0  # skipped as older than a newer item, or over max age
        self.last_drop_s: float | None = None
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = []
        self.closed = False

    # called with the channel lock held
    def _evicted(self, seq: int, now_s: float) -> None:
        if self._cursor <= seq:
            self.dropped_superseded += seq - self._cursor + 1
            self._cursor = seq + 1
            self.last_drop_s = now_s

    def _notify(self) -> None:
        waiters, self._waiters = self._waiters, []
        for loop, future in waiters:
            if loop.is_closed():
                continue
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                if not future.done():
                    future.set_result(None)
            else:
                loop.call_soon_threadsafe(_resolve, future)

    @property
    def lag(self) -> int:
        return max(0, self.channel._head - self._cursor)

    @property
    def depth(self) -> int:
        """Unread items still held for this reader."""
        with self.channel._lock:
            return self._depth_locked()

    def _depth_locked(self) -> int:
        return sum(1 for slot in self.channel._ring if slot.seq >= self._cursor)

    def unread_ages(self, now_s: float | None = None) -> tuple[float | None, float | None]:
        """(oldest, newest) age in seconds of the items this reader has not read."""
        now_s = self.channel._clock() if now_s is None else now_s
        with self.channel._lock:
            unread = [slot for slot in self.channel._ring if slot.seq >= self._cursor]
        if not unread:
            return None, None
        return max(0.0, now_s - unread[0].origin_s), max(0.0, now_s - unread[-1].origin_s)

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait until there is something unread (True) or the timeout passes (False)."""
        loop = asyncio.get_running_loop()
        with self.channel._lock:
            if self._cursor < self.channel._head or self._has_extra_locked():
                return True
            future = loop.create_future()
            self._waiters.append((loop, future))
        try:
            await asyncio.wait_for(future, timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _has_extra_locked(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "delivered": self.delivered,
            "dropped_superseded": self.dropped_superseded,
            "dropped_stale": self.dropped_stale,
            "depth": self.depth,
            "lag": self.lag,
        }


def _resolve(future: asyncio.Future) -> None:
    if not future.done():
        future.set_result(None)


class LatestReader(_Reader):
    def take(self, max_age_s: Any = _USE_SPEC, now: float | None = None) -> Any | None:
        """The newest unread item; older unread ones are counted as stale.

        Returns None when nothing new arrived or the newest item is older than
        ``max_age_s`` (that one is counted as stale too).
        """
        if max_age_s is _USE_SPEC:
            max_age_s = self.channel.spec.max_age_s
        if max_age_s is not None and max_age_s < 0:
            raise ValueError("max_age_s must be non-negative")
        channel = self.channel
        with channel._lock:
            if self._cursor >= channel._head or not channel._ring:
                return None
            newest = channel._ring[-1]
            skipped = newest.seq - self._cursor
            self._cursor = channel._head
            now_s = channel._clock() if now is None else now
            if skipped:
                self.dropped_stale += skipped
                self.last_drop_s = now_s
            age = now_s - newest.origin_s
            if max_age_s is not None and age > max_age_s:
                self.dropped_stale += 1
                self.last_drop_s = now_s
                return None
            self.delivered += 1
            channel._delivered_age.add(max(0.0, age))
            return newest.item

    def clear(self) -> None:
        channel = self.channel
        with channel._lock:
            unread = self._depth_locked()
            if unread:
                self.dropped_stale += unread
                self.last_drop_s = channel._clock()
            self._cursor = channel._head

    async def next(self, timeout: float | None = None, max_age_s: Any = _USE_SPEC) -> Any | None:
        if not await self.wait(timeout):
            return None
        return self.take(max_age_s)


class StreamReader(_Reader):
    def get_nowait(self) -> Any | None:
        channel = self.channel
        with channel._lock:
            if self._cursor >= channel._head:
                return None
            for slot in channel._ring:
                if slot.seq == self._cursor:
                    self._cursor += 1
                    self.delivered += 1
                    channel._delivered_age.add(max(0.0, channel._clock() - slot.origin_s))
                    return slot.item
            # Should not happen: evictions advance cursors.
            self._cursor = channel._head
            return None

    def drain(self, max_items: int | None = None) -> list[Any]:
        out: list[Any] = []
        while max_items is None or len(out) < max_items:
            item = self.get_nowait()
            if item is None:
                break
            out.append(item)
        return out

    async def get(self, timeout: float | None = None) -> Any | None:
        if not await self.wait(timeout):
            return None
        return self.get_nowait()


class ReliableReader(StreamReader):
    """Reader of a RELIABLE_EVENT channel.

    A *required* reader must ``ack`` what it received; until it does, the
    channel keeps the event (and refuses new ones once full). A best-effort
    reader reads the bounded history like a stream and may lose the oldest
    events if it falls behind (counted).
    """

    def __init__(self, channel: "ReliableChannel", name: str, cursor: int, required: bool) -> None:
        super().__init__(channel, name, cursor)
        self.required = required
        self.acked_seq = cursor - 1

    def _evicted(self, seq: int, now_s: float) -> None:
        # A required reader reads the retained backlog, not the history ring:
        # leaving the ring does not lose an event it has not acknowledged.
        if not self.required:
            super()._evicted(seq, now_s)

    def _has_extra_locked(self) -> bool:
        return self.required and any(
            seq > self._delivered_upto and self.name in entry.pending
            for seq, entry in self.channel._retained.items()
        )

    @property
    def _delivered_upto(self) -> int:
        return self._cursor - 1

    def receive_nowait(self) -> Delivery | None:
        channel: ReliableChannel = self.channel  # type: ignore[assignment]
        if not self.required:
            with channel._lock:
                cursor = self._cursor
            item = self.get_nowait()
            if item is None:
                return None
            return Delivery(cursor, item, channel._clock())
        with channel._lock:
            for seq, entry in channel._retained.items():
                if seq >= self._cursor and self.name in entry.pending:
                    self._cursor = seq + 1
                    self.delivered += 1
                    channel._delivered_age.add(max(0.0, channel._clock() - entry.slot.origin_s))
                    return Delivery(seq, entry.slot.item, entry.slot.published_s)
            self._cursor = channel._head
            return None

    async def receive(self, timeout: float | None = None) -> Delivery | None:
        if not await self.wait(timeout):
            return None
        return self.receive_nowait()

    def ack(self, seq: int) -> None:
        """Acknowledge every event up to and including ``seq``."""
        self.channel._ack(self, seq)  # type: ignore[attr-defined]


# -- channels ------------------------------------------------------------------------------------
class Channel:
    reader_class: type[_Reader] = _Reader

    def __init__(self, spec: ChannelSpec, clock: Callable[[], float] = time.monotonic) -> None:
        self.spec = spec
        self._clock = clock
        self._lock = threading.Lock()
        self._ring: deque[_Slot] = deque()
        self._head = 0  # seq of the next publication
        self._readers: dict[str, _Reader] = {}
        self.published = 0
        self.contended = 0  # try_publish could not take the lock
        self._rate = RateMeter()
        self._delivered_age = RollingWindow(512)
        self._last_publish_s: float | None = None
        self._type_check: Callable[[Any], None] | None = None

    # -- configuration --------------------------------------------------------------------
    @property
    def topic(self) -> str:
        return self.spec.topic

    @property
    def mode(self) -> DeliveryMode:
        return self.spec.mode

    @property
    def capacity(self) -> int:
        return self.spec.capacity

    def set_type_check(self, check: Callable[[Any], None] | None) -> None:
        self._type_check = check

    def _check(self, item: Any) -> None:
        if self._type_check is not None:
            self._type_check(item)
        elif self.spec.item_type is not None and not isinstance(item, self.spec.item_type):
            want = self.spec.item_type
            names = (
                " | ".join(t.__name__ for t in want) if isinstance(want, tuple) else want.__name__
            )
            raise ChannelTypeError(f"{self.topic} carries {names}, not {type(item).__name__}")

    # -- readers -------------------------------------------------------------------------------
    def _new_reader(self, name: str, **options) -> _Reader:
        raise NotImplementedError

    def subscribe(self, name: str, **options) -> _Reader:
        with self._lock:
            if name in self._readers:
                raise BusError(f"{self.topic} already has a reader named {name!r}")
            reader = self._new_reader(name, **options)
            self._readers[name] = reader
            return reader

    def unsubscribe(self, reader: _Reader | str) -> None:
        name = reader if isinstance(reader, str) else reader.name
        with self._lock:
            removed = self._readers.pop(name, None)
            if removed is not None:
                removed.closed = True
                self._released(removed)

    def _released(self, reader: _Reader) -> None:
        """Hook: a reader left (reliable channels drop its pending marks)."""

    @property
    def readers(self) -> list[_Reader]:
        with self._lock:
            return list(self._readers.values())

    # -- publishing ---------------------------------------------------------------------------
    def _append_locked(self, item: Any, now_s: float) -> _Slot:
        slot = _Slot(self._head, item, now_s, origin_seconds(item, now_s))
        if len(self._ring) >= self.capacity:
            evicted = self._ring.popleft()
            for reader in self._readers.values():
                reader._evicted(evicted.seq, now_s)
        self._ring.append(slot)
        self._head += 1
        self.published += 1
        self._rate.mark(now_s)
        self._last_publish_s = now_s
        return slot

    def publish(self, item: Any) -> PublishReceipt:
        self._check(item)
        with self._lock:
            slot = self._append_locked(item, self._clock())
            readers = list(self._readers.values())
        for reader in readers:
            reader._notify()
        return PublishReceipt(slot.seq)

    def try_publish(self, item: Any) -> bool:
        """Publish without waiting for the lock (preview paths); False = dropped."""
        self._check(item)
        if not self._lock.acquire(blocking=False):
            self.contended += 1
            return False
        try:
            self._append_locked(item, self._clock())
            readers = list(self._readers.values())
        finally:
            self._lock.release()
        for reader in readers:
            reader._notify()
        return True

    def latest(self, max_age_s: float | None = None) -> Any | None:
        """Peek at the newest item without consuming it (None if over age)."""
        with self._lock:
            if not self._ring:
                return None
            slot = self._ring[-1]
        if max_age_s is not None and self._clock() - slot.origin_s > max_age_s:
            return None
        return slot.item

    def __iter__(self) -> Iterator[Any]:  # snapshot of what the ring holds
        with self._lock:
            return iter([slot.item for slot in self._ring])

    # -- metrics -----------------------------------------------------------------------------------
    def dropped(self) -> dict[str, int]:
        with self._lock:
            readers = list(self._readers.values())
        return {
            "superseded": sum(r.dropped_superseded for r in readers),
            "stale": sum(r.dropped_stale for r in readers),
            "contended": self.contended,
        }

    def last_drop_s(self) -> float | None:
        stamps = [r.last_drop_s for r in self.readers if r.last_drop_s is not None]
        return max(stamps) if stamps else None

    def metrics(self, now_s: float | None = None) -> dict:
        now_s = self._clock() if now_s is None else now_s
        with self._lock:
            readers = list(self._readers.values())
            ring = list(self._ring)
            head = self._head
        dropped = self.dropped()
        total_dropped = sum(dropped.values())
        delivered = sum(r.delivered for r in readers)
        lags = [max(0, head - r._cursor) for r in readers]
        unread_oldest = None
        for r in readers:
            for slot in ring:
                if slot.seq >= r._cursor:
                    age = max(0.0, now_s - slot.origin_s)
                    unread_oldest = age if unread_oldest is None else max(unread_oldest, age)
                    break
        return {
            "topic": self.topic,
            "mode": self.mode.value,
            "capacity": self.capacity,
            "safety_critical": self.spec.safety_critical,
            "published": self.published,
            "delivered": delivered,
            "rate_hz": rounded(self._rate.rate_hz(now_s), 2),
            "expected_rate_hz": self.spec.expected_rate_hz,
            "last_publish_age_s": (
                None if self._last_publish_s is None else rounded(now_s - self._last_publish_s, 3)
            ),
            "age_ms": rounded_percentiles(self._delivered_age.percentiles(), 1000.0, 2),
            "depth": max(
                (sum(1 for slot in ring if slot.seq >= r._cursor) for r in readers), default=len(ring)
            ),
            "consumer_lag": max(lags, default=0),
            "consumer_lag_s": rounded(unread_oldest, 3),
            "dropped": dropped,
            "drop_fraction": rounded(total_dropped / (delivered + total_dropped), 4)
            if delivered + total_dropped
            else 0.0,
            "readers": [r.to_dict() for r in readers],
        }


class LatestChannel(Channel):
    reader_class = LatestReader

    def __init__(self, spec: ChannelSpec, clock: Callable[[], float] = time.monotonic) -> None:
        if spec.mode is not DeliveryMode.LATEST:
            raise ValueError(f"{spec.topic} is {spec.mode.value}, not LATEST")
        super().__init__(spec, clock)

    def _new_reader(self, name: str, *, from_latest: bool = True) -> LatestReader:
        # A late reader of a "current value" channel sees the current value.
        cursor = self._ring[-1].seq if (from_latest and self._ring) else self._head
        return LatestReader(self, name, cursor)

    def subscribe(self, name: str, *, from_latest: bool = True) -> LatestReader:  # type: ignore[override]
        return super().subscribe(name, from_latest=from_latest)  # type: ignore[return-value]


class StreamChannel(Channel):
    reader_class = StreamReader

    def __init__(self, spec: ChannelSpec, clock: Callable[[], float] = time.monotonic) -> None:
        if spec.mode is not DeliveryMode.BOUNDED_STREAM:
            raise ValueError(f"{spec.topic} is {spec.mode.value}, not BOUNDED_STREAM")
        super().__init__(spec, clock)

    def _new_reader(self, name: str, *, from_start: bool = False) -> StreamReader:
        cursor = self._ring[0].seq if (from_start and self._ring) else self._head
        return StreamReader(self, name, cursor)

    def subscribe(self, name: str, *, from_start: bool = False) -> StreamReader:  # type: ignore[override]
        return super().subscribe(name, from_start=from_start)  # type: ignore[return-value]


@dataclass
class _Retained:
    slot: _Slot
    pending: set[str]


@dataclass
class _Sink:
    name: str
    fn: Callable[[Any], None]
    calls: int = 0
    failures: int = 0
    last_error: str | None = None
    last_failure_s: float | None = None


class ReliableChannel(Channel):
    """Events retained until every required party persisted or acknowledged them.

    Required parties are *sinks* (synchronous persistence called during
    ``publish``, e.g. the flight database) and *required readers* (which must
    ``ack``). ``capacity`` bounds the backlog: when that many events are still
    pending, ``publish`` raises ``BackpressureError`` and nothing is accepted.
    A failing sink leaves the event pending and is retried on later publishes
    (and by ``retry_pending``), so a database hiccup delays persistence
    instead of losing the event. Best-effort readers (UI) read a bounded
    history and may lose the oldest entries, counted per reader.
    """

    reader_class = ReliableReader

    def __init__(self, spec: ChannelSpec, clock: Callable[[], float] = time.monotonic) -> None:
        if spec.mode is not DeliveryMode.RELIABLE_EVENT:
            raise ValueError(f"{spec.topic} is {spec.mode.value}, not RELIABLE_EVENT")
        super().__init__(spec, clock)
        self._retained: dict[int, _Retained] = {}
        self._sinks: dict[str, _Sink] = {}
        self.rejected = 0
        self.last_reject_s: float | None = None
        self.evicted_acked = 0
        self.released = 0

    def add_sink(self, name: str, fn: Callable[[Any], None]) -> None:
        with self._lock:
            if name in self._sinks or name in self._readers:
                raise BusError(f"{self.topic} already has a party named {name!r}")
            self._sinks[name] = _Sink(name, fn)

    def remove_sink(self, name: str) -> None:
        with self._lock:
            if self._sinks.pop(name, None) is not None:
                self._drop_party_locked(name)

    def _new_reader(self, name: str, *, required: bool = False) -> ReliableReader:
        if name in self._sinks:
            raise BusError(f"{self.topic} already has a party named {name!r}")
        return ReliableReader(self, name, self._head, required)

    def subscribe(self, name: str, *, required: bool = False) -> ReliableReader:  # type: ignore[override]
        return super().subscribe(name, required=required)  # type: ignore[return-value]

    def _released(self, reader: _Reader) -> None:
        self._drop_party_locked(reader.name)

    def _drop_party_locked(self, name: str) -> None:
        for seq in list(self._retained):
            entry = self._retained[seq]
            if name in entry.pending:
                entry.pending.discard(name)
                self.released += 1
                if not entry.pending:
                    del self._retained[seq]

    def _required_parties_locked(self) -> set[str]:
        parties = set(self._sinks)
        parties.update(name for name, r in self._readers.items() if getattr(r, "required", False))
        return parties

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._retained)

    def pending_by_party(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for entry in self._retained.values():
                for name in entry.pending:
                    counts[name] = counts.get(name, 0) + 1
            return counts

    def publish(self, item: Any) -> PublishReceipt:
        self._check(item)
        self.retry_pending()
        now_s = self._clock()
        with self._lock:
            if len(self._retained) >= self.capacity:
                self.rejected += 1
                self.last_reject_s = now_s
                waiting = sorted({n for e in self._retained.values() for n in e.pending})
                raise BackpressureError(
                    f"{self.topic}: {len(self._retained)} events still unacknowledged by "
                    f"{', '.join(waiting) or 'nobody'}; refusing to drop a reliable event"
                )
            slot = self._append_locked(item, now_s)
            parties = self._required_parties_locked()
            if parties:
                self._retained[slot.seq] = _Retained(slot, set(parties))
            sinks = [self._sinks[name] for name in parties if name in self._sinks]
            readers = list(self._readers.values())
        failed = self._run_sinks(slot.seq, item, sinks)
        for reader in readers:
            reader._notify()
        with self._lock:
            entry = self._retained.get(slot.seq)
            pending = tuple(sorted(entry.pending)) if entry is not None else ()
        return PublishReceipt(slot.seq, persisted=not failed, pending=pending)

    def _run_sinks(self, seq: int, item: Any, sinks: list[_Sink]) -> list[str]:
        failed: list[str] = []
        for sink in sinks:
            sink.calls += 1
            try:
                sink.fn(item)
            except Exception as exc:  # the event stays pending; never silent
                sink.failures += 1
                sink.last_error = f"{type(exc).__name__}: {exc}"
                sink.last_failure_s = self._clock()
                failed.append(sink.name)
                logger.error("%s: sink %s failed for event %d: %s", self.topic, sink.name, seq, exc)
                continue
            with self._lock:
                entry = self._retained.get(seq)
                if entry is not None:
                    entry.pending.discard(sink.name)
                    if not entry.pending:
                        del self._retained[seq]
                        self.evicted_acked += 1
        return failed

    def retry_pending(self) -> int:
        """Re-run sinks for events they failed on; returns how many succeeded."""
        with self._lock:
            work = [
                (seq, entry.slot.item, [self._sinks[n] for n in entry.pending if n in self._sinks])
                for seq, entry in self._retained.items()
            ]
        done = 0
        for seq, item, sinks in work:
            if sinks:
                done += len(sinks) - len(self._run_sinks(seq, item, sinks))
        return done

    def _ack(self, reader: ReliableReader, seq: int) -> None:
        with self._lock:
            reader.acked_seq = max(reader.acked_seq, seq)
            for s in [s for s in self._retained if s <= seq]:
                entry = self._retained[s]
                entry.pending.discard(reader.name)
                if not entry.pending:
                    del self._retained[s]
                    self.evicted_acked += 1

    def metrics(self, now_s: float | None = None) -> dict:
        out = super().metrics(now_s)
        with self._lock:
            sinks = [
                {
                    "name": s.name,
                    "calls": s.calls,
                    "failures": s.failures,
                    "last_error": s.last_error,
                }
                for s in self._sinks.values()
            ]
            for reader in out["readers"]:
                r = self._readers.get(reader["name"])
                reader["required"] = getattr(r, "required", False)
        out.update(
            {
                "pending": self.pending,
                "pending_by_party": self.pending_by_party(),
                "rejected": self.rejected,
                "released": self.released,
                "sinks": sinks,
            }
        )
        return out


def make_channel(spec: ChannelSpec, clock: Callable[[], float] = time.monotonic) -> Channel:
    return {
        DeliveryMode.LATEST: LatestChannel,
        DeliveryMode.BOUNDED_STREAM: StreamChannel,
        DeliveryMode.RELIABLE_EVENT: ReliableChannel,
    }[spec.mode](spec, clock)
