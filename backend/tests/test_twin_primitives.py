"""Tests for Digital Twin 2.0 typed primitives, snapshot builder, and serialization."""
from __future__ import annotations

import json

from vantaflight.digital_twin.primitives import (
    MAX_SNAPSHOT_BYTES,
    PrimitiveKind,
    TwinAircraftState,
    TwinEnvelope,
    TwinErrorMetric,
    TwinGate,
    TwinLayer,
    TwinPath,
    TwinPoint,
    TwinPose,
    TwinRegion,
    TwinSnapshotBuilder,
    TwinText,
    TwinVector,
    TwinWorldSnapshot,
)


class TestTwinPrimitives:

    def test_point_serialization(self):
        p = TwinPoint(1.0, 2.0, 3.0, TwinLayer.TRUTH, label="gate_center")
        d = p.to_dict()
        assert d["kind"] == "POINT"
        assert d["x"] == 1.0
        assert d["layer"] == "TRUTH"
        assert d["label"] == "gate_center"

    def test_pose_serialization(self):
        p = TwinPose(1.0, 2.0, 3.0, yaw_deg=45.0, layer=TwinLayer.ACTUAL)
        d = p.to_dict()
        assert d["kind"] == "POSE"
        assert d["yaw_deg"] == 45.0
        assert d["layer"] == "ACTUAL"

    def test_vector_serialization(self):
        v = TwinVector(0, 0, 0, 1, 0, 0, layer=TwinLayer.DESIRED, label="velocity")
        d = v.to_dict()
        assert d["kind"] == "VECTOR"
        assert d["origin"] == [0, 0, 0]
        assert d["direction"] == [1, 0, 0]

    def test_path_serialization(self):
        path = TwinPath(
            points=((0, 0, 0), (1, 1, 1), (2, 2, 0)),
            layer=TwinLayer.DESIRED,
            closed=False,
        )
        d = path.to_dict()
        assert d["kind"] == "PATH"
        assert len(d["points"]) == 3
        assert d["closed"] is False

    def test_gate_serialization(self):
        g = TwinGate("g1", 5, 0, 1, 1, 0, 0, width=2.0, height=1.0, passed=True)
        d = g.to_dict()
        assert d["kind"] == "GATE"
        assert d["gate_id"] == "g1"
        assert d["passed"] is True
        assert d["width"] == 2.0

    def test_envelope_serialization(self):
        e = TwinEnvelope(1, 2, 3, 0.5, 0.5, 0.5, layer=TwinLayer.ESTIMATED)
        d = e.to_dict()
        assert d["kind"] == "ENVELOPE"
        assert d["radii"] == [0.5, 0.5, 0.5]

    def test_text_serialization(self):
        t = TwinText(0, 0, 5, "Gate 3", layer=TwinLayer.TRUTH)
        d = t.to_dict()
        assert d["kind"] == "TEXT"
        assert d["text"] == "Gate 3"

    def test_region_serialization(self):
        r = TwinRegion(
            vertices=((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)),
            layer=TwinLayer.TRUTH,
            label="safe_volume",
        )
        d = r.to_dict()
        assert d["kind"] == "REGION"
        assert len(d["vertices"]) == 4

    def test_all_primitives_are_frozen(self):
        p = TwinPoint(1, 2, 3, TwinLayer.TRUTH)
        try:
            p.x = 99
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_aircraft_state_serialization(self):
        a = TwinAircraftState((1, 2, 3), (0.1, 0.2, 0.3), yaw_deg=90.0, layer=TwinLayer.TRUTH)
        d = a.to_dict()
        assert d["position"] == [1, 2, 3]
        assert d["layer"] == "TRUTH"

    def test_error_metric(self):
        e = TwinErrorMetric("position_error", 0.1234, "m")
        d = e.to_dict()
        assert d["name"] == "position_error"
        assert d["value"] == 0.1234

    def test_layer_enum_values(self):
        assert set(TwinLayer) == {
            TwinLayer.TRUTH, TwinLayer.ESTIMATED,
            TwinLayer.DESIRED, TwinLayer.ACTUAL,
        }

    def test_primitive_kind_covers_all_types(self):
        assert len(PrimitiveKind) == 8


class TestTwinWorldSnapshot:

    def test_empty_snapshot_serializes(self):
        snap = TwinWorldSnapshot(timestamp=1.0, sequence=1)
        d = snap.to_dict()
        assert d["timestamp"] == 1.0
        assert d["sequence"] == 1
        assert d["aircraft"] == {}
        assert d["primitives"] == []
        assert d["errors"] == []

    def test_snapshot_with_all_fields(self):
        aircraft = {
            "TRUTH": TwinAircraftState((1, 2, 3), (0, 0, 0), 0.0, layer=TwinLayer.TRUTH),
            "ESTIMATED": TwinAircraftState((1.1, 2.1, 3.1), (0, 0, 0), 5.0, layer=TwinLayer.ESTIMATED),
        }
        primitives = (
            TwinPoint(5, 0, 1, TwinLayer.TRUTH),
            TwinGate("g1", 5, 0, 1, 1, 0, 0, 2.0, 1.0),
        )
        errors = (TwinErrorMetric("pos_err", 0.14),)
        snap = TwinWorldSnapshot(
            timestamp=10.5,
            sequence=42,
            aircraft=aircraft,
            primitives=primitives,
            errors=errors,
            autonomy_state="RUNNING",
            race_time_s=5.2,
            gates_passed=2,
            total_gates=8,
        )
        d = snap.to_dict()
        assert len(d["aircraft"]) == 2
        assert len(d["primitives"]) == 2
        assert d["primitives"][0]["kind"] == "POINT"
        assert d["primitives"][1]["kind"] == "GATE"
        assert d["gates_passed"] == 2
        assert d["autonomy_state"] == "RUNNING"

    def test_snapshot_is_json_serializable(self):
        snap = TwinWorldSnapshot(
            timestamp=1.0,
            sequence=1,
            primitives=(TwinPoint(1, 2, 3, TwinLayer.TRUTH),),
        )
        raw = json.dumps(snap.to_dict())
        parsed = json.loads(raw)
        assert parsed["primitives"][0]["kind"] == "POINT"

    def test_snapshot_serialized_size(self):
        snap = TwinWorldSnapshot(timestamp=1.0, sequence=1)
        assert snap.serialized_size < MAX_SNAPSHOT_BYTES

    def test_snapshot_is_frozen(self):
        snap = TwinWorldSnapshot(timestamp=1.0, sequence=1)
        try:
            snap.sequence = 99
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestTwinSnapshotBuilder:

    def test_build_produces_incrementing_sequence(self):
        builder = TwinSnapshotBuilder()
        s1 = builder.build()
        s2 = builder.build()
        assert s2.sequence == s1.sequence + 1

    def test_primitives_cleared_after_build(self):
        builder = TwinSnapshotBuilder()
        builder.add_primitive(TwinPoint(0, 0, 0, TwinLayer.TRUTH))
        s1 = builder.build()
        assert len(s1.primitives) == 1
        s2 = builder.build()
        assert len(s2.primitives) == 0

    def test_aircraft_persists_across_builds(self):
        builder = TwinSnapshotBuilder()
        builder.set_aircraft(
            TwinLayer.TRUTH,
            TwinAircraftState((1, 2, 3), (0, 0, 0), 0.0, layer=TwinLayer.TRUTH),
        )
        s1 = builder.build()
        s2 = builder.build()
        assert "TRUTH" in s1.aircraft
        assert "TRUTH" in s2.aircraft

    def test_set_autonomy(self):
        builder = TwinSnapshotBuilder()
        builder.set_autonomy("RUNNING", 5.0, 2, 8)
        snap = builder.build()
        assert snap.autonomy_state == "RUNNING"
        assert snap.race_time_s == 5.0
        assert snap.gates_passed == 2

    def test_reset_clears_everything(self):
        builder = TwinSnapshotBuilder()
        builder.set_aircraft(
            TwinLayer.TRUTH,
            TwinAircraftState((1, 2, 3), (0, 0, 0), 0.0, layer=TwinLayer.TRUTH),
        )
        builder.add_primitive(TwinPoint(0, 0, 0, TwinLayer.TRUTH))
        builder.set_autonomy("RUNNING", 5.0, 2, 8)
        builder.reset()
        snap = builder.build()
        assert snap.aircraft == {}
        assert snap.primitives == ()
        assert snap.autonomy_state == "IDLE"

    def test_multiple_layers(self):
        builder = TwinSnapshotBuilder()
        builder.set_aircraft(
            TwinLayer.TRUTH,
            TwinAircraftState((1, 2, 3), (0, 0, 0), 0.0, layer=TwinLayer.TRUTH),
        )
        builder.set_aircraft(
            TwinLayer.ESTIMATED,
            TwinAircraftState((1.1, 2.1, 3.1), (0, 0, 0), 0.0, layer=TwinLayer.ESTIMATED),
        )
        builder.set_aircraft(
            TwinLayer.DESIRED,
            TwinAircraftState((2, 3, 4), (1, 0, 0), 45.0, layer=TwinLayer.DESIRED),
        )
        builder.set_aircraft(
            TwinLayer.ACTUAL,
            TwinAircraftState((1.05, 2.05, 3.05), (0, 0, 0), 1.0, layer=TwinLayer.ACTUAL),
        )
        snap = builder.build()
        assert len(snap.aircraft) == 4
        d = snap.to_dict()
        assert set(d["aircraft"].keys()) == {"TRUTH", "ESTIMATED", "DESIRED", "ACTUAL"}
