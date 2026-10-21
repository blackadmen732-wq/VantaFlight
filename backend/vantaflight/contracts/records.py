"""Stored contract records: encode for the database, decode (and migrate) for replay.

``autonomy_samples`` rows hold ``(timestamp, kind, data_json)``. Since
contracts, ``kind`` is the topic and ``data`` the encoded envelope. Rows
written before contracts (PR #8) used ``race_sample`` / ``race_event`` /
``race_summary`` kinds with bare dicts; they decode as schema version 0 and
migrate through the registry like any other old version.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping

from ..core.clock import ClockDomain, TimeStamp
from .envelope import EventEnvelope
from .payloads import LEGACY_RACE_KINDS
from .quality import Quality, QualityFlag
from .registry import ContractRegistry, default_registry

_LEGACY_NS = uuid.UUID("6c1f1e0a-7a1b-4b8e-9b0e-0f1f6b6a1e01")


def topic_for_kind(kind: str) -> str:
    """Map a legacy kind or a topic to the topic."""
    return LEGACY_RACE_KINDS.get(kind, kind)


def kinds_for_topic(topic: str) -> set[str]:
    """Every ``kind`` value a topic may be stored under (topic + legacy names)."""
    topic = topic_for_kind(topic)
    return {topic} | {k for k, t in LEGACY_RACE_KINDS.items() if t == topic}


def decode_record(
    kind: str,
    wall_s: float,
    data: Mapping[str, Any],
    *,
    index: int = 0,
    run_key: str = "",
    registry: ContractRegistry | None = None,
) -> EventEnvelope:
    registry = registry or default_registry()
    if kind in LEGACY_RACE_KINDS:
        stamp = TimeStamp.from_seconds(float(wall_s), ClockDomain.UTC_WALL)
        raw = {
            "schema_version": 0,
            "event_id": uuid.uuid5(_LEGACY_NS, f"{run_key}:{kind}:{wall_s!r}:{index}"),
            "run_id": uuid.uuid5(_LEGACY_NS, f"run:{run_key}"),
            "sequence": index,
            "source": "legacy.autonomy_samples",
            "topic": LEGACY_RACE_KINDS[kind],
            "captured_at": stamp,
            "received_at": stamp,
            "published_at": stamp,
            "quality": Quality(
                flags=frozenset({QualityFlag.LEGACY_UNVERSIONED}),
                provenance=("autonomy_samples:v0",),
            ),
            "payload": dict(data),
        }
        return registry.decode(raw)
    return registry.decode(data)


def record_view(envelope: EventEnvelope, wall_s: float) -> dict:
    """The JSON shape the API and replay show for one stored record."""
    return {
        "timestamp": wall_s,
        "topic": envelope.topic,
        "kind": envelope.topic,
        "schema_version": envelope.schema_version,
        "source": envelope.source,
        "sequence": envelope.sequence,
        "captured_at": envelope.captured_at.to_dict(),
        "published_at": envelope.published_at.to_dict(),
        "quality": envelope.quality.model_dump(mode="json"),
        "data": envelope.payload.model_dump(mode="json"),
    }
