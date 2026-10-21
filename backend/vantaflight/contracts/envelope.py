"""EventEnvelope — the one shape every subsystem message travels in.

Subsystems exchange typed, versioned payloads wrapped in an envelope that
says who produced them, when (on which clock), in which frame, with which
calibration and model versions, and how trustworthy they are. Never a loose
dictionary of raw values.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Callable, ClassVar, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..core.clock import ClockDomain, TimeStamp, VantaClock
from .frames import FrameId
from .quality import Quality

ComponentId = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_:-]+)*$", max_length=96)
]
TopicName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=96)
]


class ContractPayload(BaseModel):
    """Base for every versioned message payload.

    Subclasses declare ``TOPIC`` and ``SCHEMA_VERSION``. Fields carry their
    unit in the name (``speed_m_s``) or are frame-tagged ``Vec3`` values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    TOPIC: ClassVar[str] = ""
    SCHEMA_VERSION: ClassVar[int] = 0


P = TypeVar("P", bound=BaseModel)


class EventEnvelope(BaseModel, Generic[P]):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(ge=0)
    event_id: UUID
    run_id: UUID
    sequence: int = Field(ge=0)

    source: ComponentId
    topic: TopicName

    captured_at: TimeStamp
    received_at: TimeStamp
    published_at: TimeStamp

    frame_id: FrameId | None = None
    calibration_id: str | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)

    quality: Quality = Field(default_factory=Quality)
    payload: P

    @model_validator(mode="after")
    def _causal_timestamps(self) -> "EventEnvelope":
        if not self.received_at.same_clock(self.published_at):
            raise ValueError(
                "received_at and published_at must be on the same clock "
                f"({self.received_at.describe()} vs {self.published_at.describe()})"
            )
        if self.published_at.value_ns < self.received_at.value_ns:
            raise ValueError("published_at cannot precede received_at")
        if self.captured_at.same_clock(self.received_at) and self.captured_at.value_ns > self.received_at.value_ns:
            raise ValueError("captured_at cannot be later than received_at")
        return self


class EnvelopeStamper:
    """Wraps payloads from one component: ids, per-topic sequences, timestamps.

    ``ids`` defaults to random UUIDs; pass ``SequentialIds(seed)`` for
    reproducible runs (same seed + same inputs = identical envelopes).
    """

    def __init__(
        self,
        source: str,
        clock: VantaClock,
        registry=None,
        *,
        run_id: UUID | None = None,
        ids: Callable[[], UUID] = uuid.uuid4,
    ) -> None:
        from .registry import default_registry

        self.source = source
        self.clock = clock
        self.registry = registry or default_registry()
        self._ids = ids
        self.run_id = run_id or ids()
        self._sequence: dict[str, int] = defaultdict(int)

    def new_run(self, run_id: UUID | None = None) -> UUID:
        self.run_id = run_id or self._ids()
        self._sequence.clear()
        return self.run_id

    def stamp(
        self,
        payload: ContractPayload,
        *,
        captured_at: TimeStamp | None = None,
        received_at: TimeStamp | None = None,
        quality: Quality | None = None,
        frame_id: FrameId | None = None,
        calibration_id: str | None = None,
        model_versions: dict[str, str] | None = None,
    ) -> EventEnvelope:
        entry = self.registry.entry(payload.TOPIC)
        if type(payload) is not entry.payload_type:
            raise TypeError(
                f"{type(payload).__name__} is not the registered payload for "
                f"{payload.TOPIC} ({entry.payload_type.__name__} v{entry.version})"
            )
        now = self.clock.now()
        captured = captured_at or now
        if received_at is None:
            received_at = captured if captured.domain is ClockDomain.HOST_MONOTONIC else now
        sequence = self._sequence[entry.topic]
        self._sequence[entry.topic] = sequence + 1
        return EventEnvelope[entry.payload_type](
            schema_version=entry.version,
            event_id=self._ids(),
            run_id=self.run_id,
            sequence=sequence,
            source=self.source,
            topic=entry.topic,
            captured_at=captured,
            received_at=received_at,
            published_at=now,
            frame_id=frame_id if frame_id is not None else entry.frame_id,
            calibration_id=calibration_id,
            model_versions=dict(model_versions or {}),
            quality=quality or Quality(),
            payload=payload,
        )
