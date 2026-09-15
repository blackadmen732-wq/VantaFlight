"""Typed visualization primitives for the Digital Twin 2.0 rendering contract.

These immutable data classes carry everything the renderer needs to draw
one frame of the engineering visualization. The flight-critical pipeline
publishes them; the Three.js renderer consumes them. The Twin never
controls the algorithms — data flows one way.

Direction:  algorithms/sensors/simulation → publish immutable typed state
            → TwinSnapshot/TwinFrame → WebSocket/API → Three.js renderer
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


class TwinLayer(str, enum.Enum):
    TRUTH = "TRUTH"
    ESTIMATED = "ESTIMATED"
    DESIRED = "DESIRED"
    ACTUAL = "ACTUAL"


class PrimitiveKind(str, enum.Enum):
    POINT = "POINT"
    POSE = "POSE"
    VECTOR = "VECTOR"
    PATH = "PATH"
    GATE = "GATE"
    ENVELOPE = "ENVELOPE"
    TEXT = "TEXT"
    REGION = "REGION"


@dataclass(frozen=True)
class TwinPoint:
    x: float
    y: float
    z: float
    layer: TwinLayer
    label: str = ""
    color: str = ""
    radius: float = 0.05

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.POINT.value,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "z": round(self.z, 4),
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
            "radius": self.radius,
        }


@dataclass(frozen=True)
class TwinPose:
    x: float
    y: float
    z: float
    yaw_deg: float
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    layer: TwinLayer = TwinLayer.ACTUAL
    label: str = ""
    color: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.POSE.value,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "z": round(self.z, 4),
            "yaw_deg": round(self.yaw_deg, 2),
            "pitch_deg": round(self.pitch_deg, 2),
            "roll_deg": round(self.roll_deg, 2),
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
        }


@dataclass(frozen=True)
class TwinVector:
    origin_x: float
    origin_y: float
    origin_z: float
    dx: float
    dy: float
    dz: float
    layer: TwinLayer = TwinLayer.ACTUAL
    label: str = ""
    color: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.VECTOR.value,
            "origin": [round(self.origin_x, 4), round(self.origin_y, 4), round(self.origin_z, 4)],
            "direction": [round(self.dx, 4), round(self.dy, 4), round(self.dz, 4)],
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
        }


@dataclass(frozen=True)
class TwinPath:
    points: tuple[tuple[float, float, float], ...]
    layer: TwinLayer = TwinLayer.ACTUAL
    label: str = ""
    color: str = ""
    closed: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.PATH.value,
            "points": [[round(p[0], 4), round(p[1], 4), round(p[2], 4)] for p in self.points],
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
            "closed": self.closed,
        }


@dataclass(frozen=True)
class TwinGate:
    gate_id: str
    x: float
    y: float
    z: float
    normal_x: float
    normal_y: float
    normal_z: float
    width: float
    height: float
    layer: TwinLayer = TwinLayer.TRUTH
    passed: bool = False
    color: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.GATE.value,
            "gate_id": self.gate_id,
            "position": [round(self.x, 4), round(self.y, 4), round(self.z, 4)],
            "normal": [round(self.normal_x, 4), round(self.normal_y, 4), round(self.normal_z, 4)],
            "width": round(self.width, 3),
            "height": round(self.height, 3),
            "layer": self.layer.value,
            "passed": self.passed,
            "color": self.color,
        }


@dataclass(frozen=True)
class TwinEnvelope:
    center_x: float
    center_y: float
    center_z: float
    radius_x: float
    radius_y: float
    radius_z: float
    layer: TwinLayer = TwinLayer.ESTIMATED
    label: str = ""
    color: str = ""
    opacity: float = 0.3

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.ENVELOPE.value,
            "center": [round(self.center_x, 4), round(self.center_y, 4), round(self.center_z, 4)],
            "radii": [round(self.radius_x, 4), round(self.radius_y, 4), round(self.radius_z, 4)],
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
            "opacity": self.opacity,
        }


@dataclass(frozen=True)
class TwinText:
    x: float
    y: float
    z: float
    text: str
    layer: TwinLayer = TwinLayer.TRUTH
    color: str = ""
    size: float = 0.3

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.TEXT.value,
            "position": [round(self.x, 4), round(self.y, 4), round(self.z, 4)],
            "text": self.text,
            "layer": self.layer.value,
            "color": self.color,
            "size": self.size,
        }


@dataclass(frozen=True)
class TwinRegion:
    vertices: tuple[tuple[float, float, float], ...]
    layer: TwinLayer = TwinLayer.TRUTH
    label: str = ""
    color: str = ""
    opacity: float = 0.15

    def to_dict(self) -> dict:
        return {
            "kind": PrimitiveKind.REGION.value,
            "vertices": [[round(v[0], 4), round(v[1], 4), round(v[2], 4)] for v in self.vertices],
            "layer": self.layer.value,
            "label": self.label,
            "color": self.color,
            "opacity": self.opacity,
        }


TwinPrimitive = TwinPoint | TwinPose | TwinVector | TwinPath | TwinGate | TwinEnvelope | TwinText | TwinRegion


@dataclass(frozen=True)
class TwinAircraftState:
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    yaw_deg: float
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    layer: TwinLayer = TwinLayer.ACTUAL

    def to_dict(self) -> dict:
        return {
            "position": [round(v, 4) for v in self.position],
            "velocity": [round(v, 4) for v in self.velocity],
            "yaw_deg": round(self.yaw_deg, 2),
            "pitch_deg": round(self.pitch_deg, 2),
            "roll_deg": round(self.roll_deg, 2),
            "layer": self.layer.value,
        }


@dataclass(frozen=True)
class TwinErrorMetric:
    name: str
    value: float
    unit: str = "m"

    def to_dict(self) -> dict:
        return {"name": self.name, "value": round(self.value, 4), "unit": self.unit}


@dataclass(frozen=True)
class TwinWorldSnapshot:
    """Immutable snapshot of the complete twin state for one frame.

    Published by the backend at the streaming rate. The Three.js renderer
    consumes this without any back-channel to the algorithms.
    """
    timestamp: float
    sequence: int
    aircraft: dict[str, TwinAircraftState] = field(default_factory=dict)
    primitives: tuple[TwinPrimitive, ...] = ()
    errors: tuple[TwinErrorMetric, ...] = ()
    autonomy_state: str = "IDLE"
    race_time_s: float = 0.0
    gates_passed: int = 0
    total_gates: int = 0

    def to_dict(self) -> dict:
        return {
            "timestamp": round(self.timestamp, 6),
            "sequence": self.sequence,
            "aircraft": {k: v.to_dict() for k, v in self.aircraft.items()},
            "primitives": [p.to_dict() for p in self.primitives],
            "errors": [e.to_dict() for e in self.errors],
            "autonomy_state": self.autonomy_state,
            "race_time_s": round(self.race_time_s, 3),
            "gates_passed": self.gates_passed,
            "total_gates": self.total_gates,
        }

    @property
    def serialized_size(self) -> int:
        import json
        return len(json.dumps(self.to_dict(), separators=(",", ":")))


MAX_SNAPSHOT_BYTES = 64_000


class TwinSnapshotBuilder:
    """Mutable builder that accumulates primitives and produces an immutable snapshot."""

    def __init__(self) -> None:
        self._sequence = 0
        self._aircraft: dict[str, TwinAircraftState] = {}
        self._primitives: list[TwinPrimitive] = []
        self._errors: list[TwinErrorMetric] = []
        self._autonomy_state = "IDLE"
        self._race_time_s = 0.0
        self._gates_passed = 0
        self._total_gates = 0

    def set_aircraft(self, layer: TwinLayer, state: TwinAircraftState) -> None:
        self._aircraft[layer.value] = state

    def add_primitive(self, primitive: TwinPrimitive) -> None:
        self._primitives.append(primitive)

    def add_error(self, metric: TwinErrorMetric) -> None:
        self._errors.append(metric)

    def set_autonomy(self, state: str, race_time_s: float, gates_passed: int, total_gates: int) -> None:
        self._autonomy_state = state
        self._race_time_s = race_time_s
        self._gates_passed = gates_passed
        self._total_gates = total_gates

    def build(self) -> TwinWorldSnapshot:
        self._sequence += 1
        snapshot = TwinWorldSnapshot(
            timestamp=time.monotonic(),
            sequence=self._sequence,
            aircraft=dict(self._aircraft),
            primitives=tuple(self._primitives),
            errors=tuple(self._errors),
            autonomy_state=self._autonomy_state,
            race_time_s=self._race_time_s,
            gates_passed=self._gates_passed,
            total_gates=self._total_gates,
        )
        self._primitives.clear()
        self._errors.clear()
        return snapshot

    def reset(self) -> None:
        self._aircraft.clear()
        self._primitives.clear()
        self._errors.clear()
        self._autonomy_state = "IDLE"
        self._race_time_s = 0.0
        self._gates_passed = 0
        self._total_gates = 0
