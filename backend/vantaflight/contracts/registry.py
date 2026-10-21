"""ContractRegistry — topics, schema versions and migrations.

Every topic has exactly one current payload model and version. Older
versions decode through a chain of upgraders (v0 → v1 → … → current), each a
pure function on the payload dict. A message from a newer build than this one
is refused rather than guessed at.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .envelope import ContractPayload, EventEnvelope
from .frames import FrameId

Upgrader = Callable[[dict], dict]

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class ContractError(ValueError):
    pass


class UnknownTopicError(ContractError):
    pass


class SchemaVersionError(ContractError):
    pass


@dataclass
class TopicEntry:
    topic: str
    payload_type: type[ContractPayload]
    version: int
    upgraders: dict[int, Upgrader] = field(default_factory=dict)
    frame_id: FrameId | None = None
    flight_critical: bool = False
    description: str = ""

    def describe(self) -> dict:
        return {
            "topic": self.topic,
            "version": self.version,
            "payload": self.payload_type.__name__,
            "frame_id": self.frame_id.value if self.frame_id else None,
            "flight_critical": self.flight_critical,
            "description": self.description,
            "migrates_from": sorted(self.upgraders),
            "schema": self.payload_type.model_json_schema(),
        }


class ContractRegistry:
    def __init__(self) -> None:
        self._topics: dict[str, TopicEntry] = {}
        self._lock = threading.Lock()

    def register(
        self,
        payload_type: type[ContractPayload],
        *,
        upgraders: Mapping[int, Upgrader] | None = None,
        frame_id: FrameId | None = None,
        flight_critical: bool = False,
        description: str = "",
    ) -> TopicEntry:
        topic = payload_type.TOPIC
        version = payload_type.SCHEMA_VERSION
        if not topic or not _TOPIC_RE.match(topic):
            raise ContractError(f"{payload_type.__name__} has an invalid TOPIC {topic!r}")
        if version < 1:
            raise ContractError(f"{payload_type.__name__} must declare SCHEMA_VERSION >= 1")
        upgraders = dict(upgraders or {})
        bad = [v for v in upgraders if not 0 <= v < version]
        if bad:
            raise ContractError(f"{topic}: upgraders for versions {bad} are not below v{version}")
        if upgraders:
            lowest = min(upgraders)
            missing = [v for v in range(lowest, version) if v not in upgraders]
            if missing:
                raise ContractError(f"{topic}: migration chain has gaps at {missing}")
        entry = TopicEntry(topic, payload_type, version, upgraders, frame_id, flight_critical, description)
        with self._lock:
            existing = self._topics.get(topic)
            if existing is not None and existing.payload_type is not payload_type:
                raise ContractError(
                    f"{topic} is already registered to {existing.payload_type.__name__}"
                )
            self._topics[topic] = entry
        return entry

    # -- lookup ------------------------------------------------------------------
    def entry(self, topic: str) -> TopicEntry:
        try:
            return self._topics[topic]
        except KeyError:
            raise UnknownTopicError(f"unknown topic {topic!r}") from None

    def topics(self) -> list[str]:
        return sorted(self._topics)

    def flight_critical(self) -> list[TopicEntry]:
        return [e for _, e in sorted(self._topics.items()) if e.flight_critical]

    def describe(self) -> list[dict]:
        return [e.describe() for _, e in sorted(self._topics.items())]

    # -- versions ----------------------------------------------------------------------
    def upgrade_payload(self, topic: str, version: int, payload: dict) -> dict:
        entry = self.entry(topic)
        if version > entry.version:
            raise SchemaVersionError(
                f"{topic} v{version} is newer than this build understands (v{entry.version})"
            )
        current = dict(payload)
        while version < entry.version:
            upgrader = entry.upgraders.get(version)
            if upgrader is None:
                raise SchemaVersionError(f"no migration for {topic} from v{version}")
            current = upgrader(current)
            version += 1
        return current

    def decode_payload(self, topic: str, version: int, payload: dict) -> ContractPayload:
        entry = self.entry(topic)
        return entry.payload_type.model_validate(self.upgrade_payload(topic, version, payload))

    # -- envelopes ----------------------------------------------------------------------
    def encode(self, envelope: EventEnvelope) -> dict[str, Any]:
        self.entry(envelope.topic)
        return envelope.model_dump(mode="json")

    def decode(self, raw: Mapping[str, Any]) -> EventEnvelope:
        if not isinstance(raw, Mapping):
            raise ContractError("an envelope must be a mapping")
        topic = raw.get("topic")
        if not isinstance(topic, str):
            raise ContractError("envelope has no topic")
        entry = self.entry(topic)
        version = raw.get("schema_version", 0)
        if not isinstance(version, int) or isinstance(version, bool):
            raise SchemaVersionError("schema_version must be an integer")
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise ContractError("envelope payload must be a mapping")
        upgraded = self.upgrade_payload(topic, version, dict(payload))
        body = {**raw, "schema_version": entry.version, "payload": upgraded}
        return EventEnvelope[entry.payload_type].model_validate(body)


_default: ContractRegistry | None = None
_default_lock = threading.Lock()


def default_registry() -> ContractRegistry:
    """The registry with every VantaFlight contract (see payloads.py)."""
    global _default
    with _default_lock:
        if _default is None:
            from .payloads import register_all

            registry = ContractRegistry()
            register_all(registry)
            _default = registry
        return _default
