"""Bounded target scene model with deterministic expiry and eviction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SceneObject:
    object_id: str
    timestamp: float
    confidence: float
    payload: Any


class VantaScene:
    def __init__(self, capacity: int = 32, max_age_s: float = 2.0) -> None:
        if capacity <= 0 or max_age_s < 0:
            raise ValueError("invalid scene bounds")
        self.capacity = capacity
        self.max_age_s = max_age_s
        self._objects: dict[str, SceneObject] = {}
        self.evictions = 0
        self.expirations = 0

    def update(self, item: SceneObject) -> None:
        self._objects[item.object_id] = item
        if len(self._objects) > self.capacity:
            oldest = min(self._objects.values(), key=lambda obj: (obj.timestamp, obj.object_id))
            del self._objects[oldest.object_id]
            self.evictions += 1

    def get(self, object_id: str, now: float | None = None) -> SceneObject | None:
        if now is not None:
            self.purge(now)
        return self._objects.get(object_id)

    def purge(self, now: float) -> int:
        stale = [
            object_id for object_id, item in self._objects.items()
            if now - item.timestamp > self.max_age_s
        ]
        for object_id in stale:
            del self._objects[object_id]
        self.expirations += len(stale)
        return len(stale)

    def snapshot(self, now: float | None = None) -> tuple[SceneObject, ...]:
        if now is not None:
            self.purge(now)
        return tuple(sorted(self._objects.values(), key=lambda item: (-item.timestamp, item.object_id)))

    def __len__(self) -> int:
        return len(self._objects)
