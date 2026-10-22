"""VantaHealth — multidimensional health instead of one ``connected: bool``.

Every component is judged on up to five dimensions:

* LIVENESS     — is it running / connected at all?
* FRESHNESS    — is its data recent enough to act on?
* CORRECTNESS  — is what it produces valid (no errors, sinks persisting)?
* RESOURCE     — queues, deadlines, budgets within limits?
* DEPENDENCY   — are the things it needs healthy?

and each dimension is one of UNKNOWN, STARTING, HEALTHY, DEGRADED, FAILED,
RECOVERING or STOPPED. A component's overall state is its worst dimension, so
"camera transport HEALTHY, camera freshness DEGRADED" stays visible as two
facts rather than being averaged into one.

Health comes from *probes* (functions evaluated on demand: the bus, the
runtime, the vehicle link) and from *reports* pushed by components that learn
something about themselves (a recorder that failed). ``evaluate`` records
state changes in a bounded history and hands them to listeners (the bus).
"""
from __future__ import annotations

import enum
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

logger = logging.getLogger(__name__)


class HealthState(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"
    STOPPED = "STOPPED"


class HealthDimension(str, enum.Enum):
    LIVENESS = "LIVENESS"
    FRESHNESS = "FRESHNESS"
    CORRECTNESS = "CORRECTNESS"
    RESOURCE = "RESOURCE"
    DEPENDENCY = "DEPENDENCY"


#: Worst wins. Not knowing is worse than healthy; STOPPED is a deliberate state.
SEVERITY = {
    HealthState.HEALTHY: 0,
    HealthState.STOPPED: 0,
    HealthState.STARTING: 1,
    HealthState.UNKNOWN: 1,
    HealthState.RECOVERING: 2,
    HealthState.DEGRADED: 3,
    HealthState.FAILED: 4,
}


def worst(states: Iterable[HealthState]) -> HealthState:
    states = list(states)
    if not states:
        return HealthState.UNKNOWN
    if all(s is HealthState.STOPPED for s in states):
        return HealthState.STOPPED
    return max(states, key=lambda s: (SEVERITY[s], s is not HealthState.STOPPED))


@dataclass(frozen=True)
class DimensionStatus:
    state: HealthState
    detail: str = ""
    #: The measured quantity behind the verdict (an age, a depth, a count).
    value: float | None = None

    def to_dict(self) -> dict:
        out = {"state": self.state.value, "detail": self.detail}
        if self.value is not None:
            out["value"] = round(float(self.value), 4)
        return out


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    kind: str
    dimensions: Mapping[HealthDimension, DimensionStatus] = field(default_factory=dict)

    @property
    def state(self) -> HealthState:
        return worst(d.state for d in self.dimensions.values())

    def dimension(self, dim: HealthDimension) -> DimensionStatus | None:
        return self.dimensions.get(dim)

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "kind": self.kind,
            "state": self.state.value,
            "dimensions": {d.value: s.to_dict() for d, s in sorted(self.dimensions.items(), key=lambda kv: kv[0].value)},
        }


Probe = Callable[[float], Iterable[ComponentHealth]]


@dataclass(frozen=True)
class HealthChange:
    component: str
    kind: str
    previous: HealthState
    state: HealthState
    detail: str
    at_s: float

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "kind": self.kind,
            "previous": self.previous.value,
            "state": self.state.value,
            "detail": self.detail,
            "at_s": round(self.at_s, 3),
        }


class VantaHealth:
    def __init__(self, clock: Callable[[], float] | None = None, *, history: int = 200) -> None:
        self._clock = clock or time.monotonic
        self._probes: dict[str, Probe] = {}
        self._reports: dict[str, tuple[str, dict[HealthDimension, DimensionStatus]]] = {}
        self._last_states: dict[str, HealthState] = {}
        self._changes: deque[HealthChange] = deque(maxlen=history)
        self._listeners: list[Callable[[HealthChange], None]] = []
        self._lock = threading.Lock()

    # -- sources ---------------------------------------------------------------------------
    def register_probe(self, name: str, probe: Probe) -> None:
        with self._lock:
            self._probes[name] = probe

    def unregister_probe(self, name: str) -> None:
        with self._lock:
            self._probes.pop(name, None)

    def report(
        self,
        component: str,
        dimension: HealthDimension,
        state: HealthState,
        detail: str = "",
        *,
        kind: str = "service",
        value: float | None = None,
    ) -> None:
        with self._lock:
            entry = self._reports.setdefault(component, (kind, {}))
            entry[1][HealthDimension(dimension)] = DimensionStatus(HealthState(state), detail, value)

    def clear(self, component: str) -> None:
        with self._lock:
            self._reports.pop(component, None)

    def on_change(self, listener: Callable[[HealthChange], None]) -> None:
        self._listeners.append(listener)

    # -- views ---------------------------------------------------------------------------------
    def snapshot(self, now_s: float | None = None) -> list[ComponentHealth]:
        now_s = self._clock() if now_s is None else now_s
        with self._lock:
            probes = list(self._probes.items())
            reports = {k: (kind, dict(dims)) for k, (kind, dims) in self._reports.items()}
        merged: dict[str, ComponentHealth] = {}
        for name, probe in probes:
            try:
                components = list(probe(now_s))
            except Exception as exc:  # a broken probe is itself a health fact
                logger.exception("health probe %s failed", name)
                components = [
                    ComponentHealth(
                        f"probe:{name}", "probe",
                        {HealthDimension.CORRECTNESS: DimensionStatus(HealthState.FAILED, f"probe error: {exc}")},
                    )
                ]
            for comp in components:
                merged[comp.component] = comp
        for component, (kind, dims) in reports.items():
            existing = merged.get(component)
            if existing is not None:
                combined = dict(existing.dimensions)
                for dim, status in dims.items():
                    current = combined.get(dim)
                    if current is None or SEVERITY[status.state] >= SEVERITY[current.state]:
                        combined[dim] = status
                merged[component] = ComponentHealth(component, existing.kind, combined)
            else:
                merged[component] = ComponentHealth(component, kind, dims)
        return [merged[k] for k in sorted(merged)]

    def component(self, name: str, now_s: float | None = None) -> ComponentHealth | None:
        for comp in self.snapshot(now_s):
            if comp.component == name:
                return comp
        return None

    def overall(self, now_s: float | None = None) -> HealthState:
        return worst(c.state for c in self.snapshot(now_s))

    def evaluate(self, now_s: float | None = None) -> list[HealthChange]:
        """Record and announce components whose overall state changed."""
        now_s = self._clock() if now_s is None else now_s
        snapshot = self.snapshot(now_s)
        changes: list[HealthChange] = []
        seen = set()
        with self._lock:
            for comp in snapshot:
                seen.add(comp.component)
                state = comp.state
                previous = self._last_states.get(comp.component, HealthState.UNKNOWN)
                if state is not previous:
                    worst_dim = max(
                        comp.dimensions.values(), key=lambda d: SEVERITY[d.state], default=None
                    )
                    change = HealthChange(
                        comp.component, comp.kind, previous, state,
                        worst_dim.detail if worst_dim is not None else "", now_s,
                    )
                    changes.append(change)
                    self._changes.append(change)
                    self._last_states[comp.component] = state
            for gone in set(self._last_states) - seen:
                del self._last_states[gone]
        for change in changes:
            for listener in list(self._listeners):
                try:
                    listener(change)
                except Exception:
                    logger.exception("health listener failed")
        return changes

    def changes(self) -> list[HealthChange]:
        return list(self._changes)

    def to_dict(self, now_s: float | None = None) -> dict:
        snapshot = self.snapshot(now_s)
        return {
            "overall": worst(c.state for c in snapshot).value,
            "components": [c.to_dict() for c in snapshot],
            "recent_changes": [c.to_dict() for c in list(self._changes)[-50:]],
        }
