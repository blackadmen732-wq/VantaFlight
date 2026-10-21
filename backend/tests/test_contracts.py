"""VantaContracts: typed, versioned, framed, timestamped messages."""
from __future__ import annotations

import math
import time
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from pydantic import BaseModel, ValidationError

from vantaflight.contracts import (
    ContractError,
    ContractPayload,
    ContractRegistry,
    EnvelopeStamper,
    EventEnvelope,
    FrameId,
    FrameMismatchError,
    Quality,
    QualityFlag,
    Quantity,
    SchemaVersionError,
    UnknownTopicError,
    Vec3,
    default_registry,
    enu_to_ned,
    ned_to_enu,
    position_enu,
    position_ned,
    velocity_enu,
    velocity_ned,
)
from vantaflight.contracts.frames import (
    PositionENU,
    acceleration_enu,
    acceleration_ned,
    yaw_enu_rad_to_ned,
    yaw_ned_rad_to_enu,
)
from vantaflight.contracts.payloads import (
    CommandRecordV1,
    DesiredStateV1,
    ExecutionSampleV1,
    FailsafeTriggerV1,
    GateObservationV1,
    GateTargetV1,
    OffboardSetpointV1,
    PerceptionFrameV1,
    PlannerSampleV1,
    RaceEventV1,
    RaceSampleV1,
    RaceSummaryV1,
    TelemetrySampleV1,
    VehicleStateV1,
)
from vantaflight.contracts.records import decode_record, kinds_for_topic, topic_for_kind
from vantaflight.contracts.units import unit_problems
from vantaflight.core.clock import ClockDomain, SequentialIds, TimeStamp, VantaClock

HOST = ClockDomain.HOST_MONOTONIC


# -- sample payloads (one per registered topic) ------------------------------------------
def sample_payloads() -> dict[str, ContractPayload]:
    state = VehicleStateV1(
        position=position_enu([1, 2, 3]), velocity=velocity_enu([0.5, 0, -0.1]), yaw_enu_rad=0.3
    )
    setpoint = OffboardSetpointV1(
        trajectory_id="vantarace-000001",
        position=position_ned([2, 1, -3]),
        velocity=velocity_ned([0, 0.5, 0]),
        acceleration=acceleration_ned([0, 0, 0]),
        yaw_ned_rad=1.2,
        planner_confidence=0.9,
    )
    desired = DesiredStateV1(
        trajectory_id="vantarace-000001",
        position=position_enu([1, 2, 3]),
        velocity=velocity_enu([0.5, 0, 0]),
        acceleration=acceleration_enu([0, 0, 0]),
        yaw_enu_rad=0.0,
        planner_confidence=0.9,
    )
    observation = GateObservationV1(
        gate_id="gate_1", position=position_enu([12, 0, 5]), confidence=0.9, distance_m=11.0, map_error_m=0.2
    )
    perception = PerceptionFrameV1(
        status="TRACKING",
        frame_processed=True,
        detections=2,
        observations=(observation,),
        targets=(
            GateTargetV1(
                gate_id="gate_1", slot="CURRENT", position=position_enu([12, 0, 5]),
                confidence=0.9, position_uncertainty_m=0.25,
            ),
        ),
        current_confidence=0.9,
        current_gate_age_s=0.05,
        processing_ms=8.0,
    )
    return {
        "vehicle.state": state,
        "vehicle.telemetry": TelemetrySampleV1(
            connected=True, armed=True, flight_mode="OFFBOARD", position=position_enu([1, 2, 3]),
            altitude_m=3.0, speed_m_s=0.5, ground_speed_m_s=0.5, heading_compass_deg=90.0,
            battery_pct=88.0, link_age_s=0.1, connection_quality="EXCELLENT",
        ),
        "planner.desired": desired,
        "execution.setpoint": setpoint,
        "execution.command": CommandRecordV1(command="hold", source="pilot", accepted=True, status="SENT"),
        "safety.failsafe": FailsafeTriggerV1(reason="battery_low", action="return", message="battery low"),
        "vision.gate_observation": observation,
        "vision.perception": perception,
        "race.sample": RaceSampleV1(
            race_state="RUNNING", race_time_s=3.2, remaining_gates=("gate_1", "gate_2"), state=state,
            state_age_s=0.02, vision=perception,
            planner=PlannerSampleV1(race_state="ACQUIRE", autonomy_state="RUNNING", desired=desired),
            execution=ExecutionSampleV1(
                mode="OFFBOARD_POSITION", has_permit=True, setpoints_sent=10, setpoints_rejected=0,
                hold_transitions=0, confidence_holds=0, last_setpoint_age_ms=12.0, last_setpoint=setpoint,
            ),
        ),
        "race.event": RaceEventV1(event_type="race_started", message="go"),
        "race.summary": RaceSummaryV1(
            state="COMPLETE", gates_passed=3, total_gates=3, race_time_s=20.0, elapsed_s=30.0
        ),
    }


def stamper(seed: int = 1, clock: VantaClock | None = None) -> EnvelopeStamper:
    return EnvelopeStamper("test.component", clock or VantaClock.fake(), ids=SequentialIds(seed))


# -- frames ------------------------------------------------------------------------------------
def test_vec3_validates_and_carries_its_frame():
    v = Vec3(1, 2, 3, "world_enu", "position")
    assert v.frame is FrameId.WORLD_ENU and v.quantity is Quantity.POSITION and v.unit.value == "m"
    with pytest.raises(ValueError):
        Vec3(float("nan"), 0, 0, FrameId.WORLD_ENU, Quantity.POSITION)
    with pytest.raises(TypeError):
        Vec3(True, 0, 0, FrameId.WORLD_ENU, Quantity.POSITION)


def test_vec3_refuses_to_mix_frames_or_quantities():
    a = position_enu([1, 2, 3])
    assert (a + position_enu([1, 1, 1])).as_list() == [2, 3, 4]
    with pytest.raises(FrameMismatchError):
        a + position_ned([1, 1, 1])
    with pytest.raises(FrameMismatchError):
        a - velocity_enu([1, 1, 1])
    with pytest.raises(FrameMismatchError):
        a.require(FrameId.LOCAL_NED)


def test_enu_ned_conversion_is_explicit_and_round_trips():
    ned = enu_to_ned(velocity_enu([1.0, 2.0, 3.0]))  # 1 m/s east, 2 north, 3 up
    assert ned.frame is FrameId.LOCAL_NED and ned.quantity is Quantity.VELOCITY
    assert ned.as_list() == [2.0, 1.0, -3.0]
    assert ned_to_enu(ned) == velocity_enu([1.0, 2.0, 3.0])
    with pytest.raises(FrameMismatchError):
        enu_to_ned(ned)  # already NED


@pytest.mark.parametrize("yaw_enu, yaw_ned", [(0.0, math.pi / 2), (math.pi / 2, 0.0), (math.pi, -math.pi / 2)])
def test_yaw_conventions(yaw_enu, yaw_ned):
    assert yaw_enu_rad_to_ned(yaw_enu) == pytest.approx(yaw_ned)
    assert yaw_ned_rad_to_enu(yaw_enu_rad_to_ned(yaw_enu)) == pytest.approx(
        math.atan2(math.sin(yaw_enu), math.cos(yaw_enu))
    )


def test_framed_fields_reject_the_wrong_frame():
    good = sample_payloads()["execution.setpoint"]
    with pytest.raises(ValidationError):
        good.model_copy(update={}).model_validate({**good.model_dump(), "position": position_enu([1, 2, 3])})


def _vec3_fields(model: type[BaseModel]) -> list[str]:
    from vantaflight.contracts.units import _leaf_types

    return [name for name, f in model.model_fields.items() if Vec3 in _leaf_types(f.annotation)]


def test_every_flight_critical_contract_is_typed_versioned_and_framed():
    registry = default_registry()
    critical = {e.topic for e in registry.flight_critical()}
    assert critical == {
        "vehicle.state", "vehicle.telemetry", "planner.desired", "execution.setpoint",
        "execution.command", "safety.failsafe", "vision.gate_observation", "vision.perception",
    }
    samples = sample_payloads()
    for topic in critical:
        entry = registry.entry(topic)
        assert issubclass(entry.payload_type, ContractPayload) and entry.version >= 1
        payload = samples[topic]
        for name in _vec3_fields(entry.payload_type):
            value = getattr(payload, name)
            if value is None:
                continue
            wrong = FrameId.LOCAL_NED if value.frame is FrameId.WORLD_ENU else FrameId.WORLD_ENU
            bad = {**payload.model_dump(), name: Vec3(value.x, value.y, value.z, wrong, value.quantity)}
            with pytest.raises(ValidationError):
                entry.payload_type.model_validate(bad)


def test_units_are_part_of_every_schema():
    registry = default_registry()
    problems = [p for topic in registry.topics() for p in unit_problems(registry.entry(topic).payload_type)]
    assert problems == []


def test_unit_lint_catches_a_bare_float_even_when_nested():
    class Inner(BaseModel):
        speed: float

    class Outer(BaseModel):
        altitude_m: float
        inner: Inner | None = None
        temps: list[float] = []

    problems = unit_problems(Outer)
    assert any("Inner.inner.speed" in p for p in problems)
    assert any("temps" in p for p in problems)
    assert not any("altitude_m" in p for p in problems)


# -- quality ------------------------------------------------------------------------------------
def test_quality_validates_covariance_and_confidence():
    Quality(covariance=((1.0, 0.1), (0.1, 2.0)))
    for bad in (((1.0, 0.2), (0.1, 2.0)), ((1.0, 0.0),), ((-1.0,),), ((float("inf"),),)):
        with pytest.raises(ValidationError):
            Quality(covariance=bad)
    with pytest.raises(ValidationError):
        Quality(confidence=1.5)
    with pytest.raises(ValidationError):
        Quality(stale_after_ms=0)


def test_quality_flags_and_staleness():
    q = Quality(stale_after_ms=500).with_flags(QualityFlag.SIMULATED, QualityFlag.ESTIMATED)
    assert {QualityFlag.SIMULATED, QualityFlag.ESTIMATED} <= q.flags
    assert q.is_stale(0.6) and not q.is_stale(0.4)
    assert not Quality().is_stale(1e9)


# -- envelopes ----------------------------------------------------------------------------------
def test_stamper_assigns_ids_sequences_and_timestamps():
    clock = VantaClock.fake()
    s = stamper(clock=clock)
    payload = sample_payloads()["race.event"]
    first = s.stamp(payload)
    clock.advance(0.1)
    second = s.stamp(payload)
    other_topic = s.stamp(sample_payloads()["vehicle.state"])
    assert (first.sequence, second.sequence, other_topic.sequence) == (0, 1, 0)
    assert first.run_id == second.run_id
    assert first.event_id != second.event_id
    assert (second.published_at - first.published_at).seconds == pytest.approx(0.1)
    assert other_topic.frame_id is FrameId.WORLD_ENU  # the topic's frame by default
    run = s.new_run()
    assert s.stamp(payload).sequence == 0 and s.stamp(payload).run_id == run


def test_stamping_is_reproducible_with_seeded_ids():
    def encoded(seed):
        clock = VantaClock.fake()
        s = stamper(seed, clock)
        out = []
        for payload in sample_payloads().values():
            clock.advance(0.01)
            out.append(default_registry().encode(s.stamp(payload)))
        return out

    assert encoded(3) == encoded(3)
    assert encoded(3) != encoded(4)


def test_envelope_rejects_acausal_or_mixed_clock_timestamps():
    clock = VantaClock.fake()
    env = stamper(clock=clock).stamp(sample_payloads()["race.event"])
    raw = default_registry().encode(env)
    later = env.published_at.plus_s(1.0).to_dict()
    with pytest.raises(ValidationError):
        default_registry().decode({**raw, "received_at": later})  # received after published
    with pytest.raises(ValidationError):
        default_registry().decode({**raw, "captured_at": later, "received_at": raw["published_at"]})
    utc = TimeStamp(env.published_at.value_ns, ClockDomain.UTC_WALL).to_dict()
    with pytest.raises(ValidationError):
        default_registry().decode({**raw, "published_at": utc})  # received/published on different clocks
    # A capture time on the sensor's own clock is fine: it is converted, not compared.
    camera = TimeStamp(5, ClockDomain.CAMERA_DEVICE, clock_id="front")
    assert stamper().stamp(sample_payloads()["race.event"], captured_at=camera).captured_at == camera


@pytest.mark.parametrize("field, value", [("topic", "NoDots"), ("source", "Bad Source"), ("sequence", -1)])
def test_envelope_rejects_bad_names_and_sequences(field, value):
    raw = default_registry().encode(stamper().stamp(sample_payloads()["race.event"]))
    with pytest.raises((ValidationError, ContractError)):
        default_registry().decode({**raw, field: value})


def test_envelope_rejects_unknown_fields():
    raw = default_registry().encode(stamper().stamp(sample_payloads()["race.event"]))
    with pytest.raises(ValidationError):
        default_registry().decode({**raw, "surprise": 1})
    with pytest.raises(ValidationError):
        default_registry().decode({**raw, "payload": {**raw["payload"], "surprise": 1}})


def test_stamper_refuses_a_payload_class_the_registry_does_not_own():
    class Impostor(ContractPayload):
        TOPIC: ClassVar[str] = "race.event"
        SCHEMA_VERSION: ClassVar[int] = 1
        event_type: str
        message: str

    with pytest.raises(TypeError):
        stamper().stamp(Impostor(event_type="x", message="y"))


# -- registry ------------------------------------------------------------------------------------
def test_every_registered_topic_round_trips():
    registry = default_registry()
    samples = sample_payloads()
    assert set(samples) == set(registry.topics())
    s = stamper()
    for topic, payload in samples.items():
        env = s.stamp(payload)
        raw = registry.encode(env)
        assert raw["schema_version"] == registry.entry(topic).version
        assert registry.decode(raw) == env


class _ProbeV3(ContractPayload):
    TOPIC: ClassVar[str] = "test.probe"
    SCHEMA_VERSION: ClassVar[int] = 3
    distance_m: float
    label: str


def _v1_to_v2(p: dict) -> dict:  # v1 had distance in centimetres
    return {"distance_m": p["distance_cm"] / 100.0, "name": p["name"]}


def _v2_to_v3(p: dict) -> dict:  # v2 → v3 renamed name → label
    return {"distance_m": p["distance_m"], "label": p["name"]}


def probe_registry() -> ContractRegistry:
    registry = ContractRegistry()
    registry.register(_ProbeV3, upgraders={1: _v1_to_v2, 2: _v2_to_v3})
    return registry


def _probe_raw(version: int, payload: dict) -> dict:
    clock = VantaClock.fake()
    now = clock.now().to_dict()
    return {
        "schema_version": version,
        "event_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "00000000-0000-0000-0000-000000000002",
        "sequence": 0,
        "source": "test.component",
        "topic": "test.probe",
        "captured_at": now,
        "received_at": now,
        "published_at": now,
        "payload": payload,
    }


def test_old_versions_migrate_step_by_step():
    registry = probe_registry()
    env = registry.decode(_probe_raw(1, {"distance_cm": 250, "name": "gate"}))
    assert env.schema_version == 3
    assert env.payload == _ProbeV3(distance_m=2.5, label="gate")
    assert registry.decode(_probe_raw(2, {"distance_m": 1.0, "name": "x"})).payload.label == "x"


def test_newer_or_unmigratable_versions_are_refused():
    registry = probe_registry()
    with pytest.raises(SchemaVersionError):
        registry.decode(_probe_raw(4, {"distance_m": 1.0, "label": "x"}))
    with pytest.raises(SchemaVersionError):
        registry.decode(_probe_raw(0, {}))  # no v0 → v1 migration
    with pytest.raises(SchemaVersionError):
        registry.decode({**_probe_raw(3, {"distance_m": 1.0, "label": "x"}), "schema_version": True})
    with pytest.raises(UnknownTopicError):
        registry.decode({**_probe_raw(3, {}), "topic": "test.unknown"})
    with pytest.raises(ContractError):
        registry.decode({**_probe_raw(3, {}), "payload": [1, 2]})


def test_registration_rules():
    registry = ContractRegistry()

    class NoVersion(ContractPayload):
        TOPIC: ClassVar[str] = "test.nover"

    class BadTopic(ContractPayload):
        TOPIC: ClassVar[str] = "Bad"
        SCHEMA_VERSION: ClassVar[int] = 1

    with pytest.raises(ContractError):
        registry.register(NoVersion)
    with pytest.raises(ContractError):
        registry.register(BadTopic)
    with pytest.raises(ContractError):
        registry.register(_ProbeV3, upgraders={1: _v1_to_v2})  # gap: no v2 → v3
    with pytest.raises(ContractError):
        registry.register(_ProbeV3, upgraders={3: _v2_to_v3})  # not below current
    registry.register(_ProbeV3, upgraders={2: _v2_to_v3})

    class Other(ContractPayload):
        TOPIC: ClassVar[str] = "test.probe"
        SCHEMA_VERSION: ClassVar[int] = 1

    with pytest.raises(ContractError):
        registry.register(Other)
    registry.register(_ProbeV3, upgraders={2: _v2_to_v3})  # re-registering the same class is fine


def test_describe_lists_schemas_for_tools_and_docs():
    described = {d["topic"]: d for d in default_registry().describe()}
    assert described["race.sample"]["migrates_from"] == [0]
    assert described["execution.setpoint"]["frame_id"] == "local_ned"
    assert "properties" in described["vehicle.state"]["schema"]


# -- legacy (PR #8) race records ------------------------------------------------------------------
def assert_close(a, b, tol=0.02, path="$"):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{path}: keys {set(a) ^ set(b)}"
        for k in a:
            assert_close(a[k], b[k], tol, f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), f"{path}: lengths differ"
        for i, (x, y) in enumerate(zip(a, b)):
            assert_close(x, y, tol, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, abs=tol), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


async def test_legacy_race_rows_decode_like_new_ones():
    """A v0 row written by PR #8 and a v1 row for the same tick decode the same."""
    from .test_race_session import started

    h = await started("sitl_line_3")
    await h.fly(4.0)
    session = h.session
    local = h.adapter.local_state()
    perception = session._last_perception
    loop = session.loop
    desired = loop.planner._trajectory.evaluate(h.clock.t) if loop.planner._trajectory else None
    now = h.clock.t
    legacy = {  # exactly what PR #8's RaceSession._record wrote
        "race_state": session.state.value,
        "race_time_s": round(now - session._race_started_at, 3),
        "remaining_gates": list(session._remaining),
        "state": local.to_dict(),
        "state_age_s": round(local.age_s(now), 3),
        "vision": perception.to_dict(),
        "planner": {
            "race_state": loop.planner.state.value,
            "autonomy_state": loop.state.value,
            "desired": desired.to_dict() if desired is not None else None,
        },
        "execution": loop.execution.to_dict(),
    }
    direct = RaceSampleV1(
        race_state=session.state.value,
        race_time_s=round(now - session._race_started_at, 3),
        remaining_gates=tuple(session._remaining),
        state=VehicleStateV1.from_local_state(local),
        state_age_s=round(local.age_s(now), 3),
        vision=PerceptionFrameV1.from_result(perception),
        planner=PlannerSampleV1(
            race_state=loop.planner.state.value,
            autonomy_state=loop.state.value,
            desired=DesiredStateV1.from_desired(desired) if desired is not None else None,
        ),
        execution=ExecutionSampleV1.from_execution(loop.execution),
    )
    migrated = decode_record("race_sample", 1_790_000_000.0, legacy)
    assert migrated.schema_version == 1 and migrated.topic == "race.sample"
    assert QualityFlag.LEGACY_UNVERSIONED in migrated.quality.flags
    assert migrated.captured_at.domain is ClockDomain.UTC_WALL
    assert_close(migrated.payload.model_dump(mode="json"), direct.model_dump(mode="json"))

    summary = decode_record("race_summary", 1_790_000_001.0, session.status())
    assert summary.payload.total_gates == 3


def test_legacy_kind_names_map_to_topics():
    assert topic_for_kind("race_sample") == "race.sample"
    assert topic_for_kind("race.sample") == "race.sample"
    assert kinds_for_topic("race.sample") == {"race.sample", "race_sample"}
    assert kinds_for_topic("race_event") == {"race.event", "race_event"}


async def test_replay_decodes_a_mix_of_legacy_and_contract_rows():
    from .test_race_session import Harness
    from vantaflight.replay import ReplayLoader

    h = Harness()
    await h.connect()
    fid = h.controller.flight_id
    env = stamper().stamp(RaceEventV1(event_type="race_started", message="new"))
    h.db.record_autonomy_samples(fid, [
        (1_790_000_000.0, "race_event", {"event_type": "race_started", "message": "old"}),
        (1_790_000_001.0, env.topic, default_registry().encode(env)),
        (1_790_000_002.0, "race.event", {"topic": "race.event", "garbage": True}),
    ])
    frames = [f for f in ReplayLoader(h.db).load_timeline(fid).frames if f.frame_type == "autonomy"]
    assert [f.data.get("topic") for f in frames] == ["race.event", "race.event", "race.event"]
    old, new, broken = frames
    assert old.data["data"]["message"] == "old" and "LEGACY_UNVERSIONED" in old.data["quality"]["flags"]
    assert new.data["data"]["message"] == "new" and new.data["schema_version"] == 1
    assert "decode_error" in broken.data  # one bad row never hides the rest


# -- determinism of a whole race recording ---------------------------------------------------------
_COMPUTE_TIMINGS = {"processing_ms", "avg_tick_ms", "max_tick_ms"}


def _strip_compute_timings(value):
    if isinstance(value, dict):
        return {k: _strip_compute_timings(v) for k, v in value.items() if k not in _COMPUTE_TIMINGS}
    if isinstance(value, list):
        return [_strip_compute_timings(v) for v in value]
    return value


async def _recorded_race(seed: int) -> list:
    from .test_race_session import Harness
    from vantaflight.autonomy.courses import preset_courses
    from vantaflight.autonomy.race_session import RaceConfig, RaceSession

    h = Harness()
    clock = VantaClock.fake(monotonic_s=1000.0)
    h.clock = clock  # the mock drone reads the same fake clock
    h.adapters.clear()
    await h.connect()
    h.adapter._now = clock
    h.adapter._last_tick = clock()
    session = RaceSession(h.controller, clock=clock, run_task=False, ids=SequentialIds(seed))
    h.controller.attach_autonomy(session)
    h.session = session
    res = await session.start(preset_courses()["sitl_line_3"], RaceConfig(perception="scene"))
    assert res["accepted"], res
    for _ in range(1200):  # up to 60 s of simulated flight
        clock.advance(0.05)
        await session.step()
        if not session.active:
            break
    assert session.state.value == "COMPLETE", session.message
    rows = h.db.get_autonomy_samples(h.controller.flight_id)
    return [(r["timestamp"], r["kind"], _strip_compute_timings(r["data"])) for r in rows]


async def test_same_clock_and_seed_record_identical_races():
    first = await _recorded_race(seed=11)
    assert first == await _recorded_race(seed=11)
    assert any(kind == "race.summary" for _, kind, _ in first)


# -- PX4 clock wiring ------------------------------------------------------------------------------
def test_px4_attitude_samples_sync_the_vehicle_boot_clock():
    from vantaflight.mavlink import MAVLinkConfig
    from vantaflight.mavlink.mavsdk_client import MAVSDKClient

    clock = VantaClock.fake()
    client = MAVSDKClient(MAVLinkConfig(), clock=clock)
    for i in range(10):
        clock.advance(0.02)
        att = SimpleNamespace(roll_deg=1.0, pitch_deg=-2.0, yaw_deg=90.0, timestamp_us=5_000_000 + i * 20_000)
        client.apply_attitude(att)
    assert client.telemetry.yaw_deg == 90.0 and client.telemetry.attitude_boot_us == 5_180_000
    syncs = {s["clock_id"]: s for s in clock.stats()["syncs"]}
    assert syncs[client.vehicle_clock_id]["synced"] is True
    boot = TimeStamp(5_180_000_000, ClockDomain.VEHICLE_BOOT, clock_id=client.vehicle_clock_id)
    assert clock.age(boot).seconds == pytest.approx(0.0, abs=1e-3)
    # Older MAVSDK without timestamps: attitude still applies, no sync is fed.
    client.apply_attitude(SimpleNamespace(roll_deg=0.0, pitch_deg=0.0, yaw_deg=10.0))
    assert client.telemetry.yaw_deg == 10.0


def test_px4_link_age_uses_the_monotonic_clock():
    from .test_px4_navigation import NavClient
    from vantaflight.adapters.px4_sitl import PX4SITLAdapter

    adapter = PX4SITLAdapter(mavsdk_client=NavClient())
    adapter._connected = True
    tel = adapter._client.telemetry
    tel.last_message_at = time.time()  # the wall clock says "fresh"...
    tel.last_message_mono = time.monotonic() - 2.0  # ...but 2 s really passed
    assert adapter.get_telemetry().link_age_s == pytest.approx(2.0, abs=0.1)


def test_local_state_age_prefers_capture_time():
    from vantaflight.models import LocalState

    received = LocalState(received_at=10.0, valid=True)
    assert not received.capture_known
    assert received.age_s(10.5) == pytest.approx(0.5)
    captured = LocalState(received_at=10.0, capture_s=9.9, valid=True)
    assert captured.capture_known and captured.age_s(10.5) == pytest.approx(0.6)
    assert captured.captured_at.domain is HOST
    with pytest.raises(ValueError):
        LocalState(received_at=10.0, capture_s=10.1)


def test_payload_converters_follow_unit_and_frame_rules():
    local = SimpleNamespace(
        position_enu=np.array([1.0, 2.0, 3.0]), velocity_enu=np.array([0.0, 1.0, 0.0]),
        yaw_enu_deg=90.0, pitch_deg=0.0, roll_deg=0.0, valid=True,
    )
    state = VehicleStateV1.from_local_state(local)
    assert state.yaw_enu_rad == pytest.approx(math.pi / 2)
    sp = SimpleNamespace(
        trajectory_id="t", position_ned=np.array([2.0, 1.0, -3.0]), velocity_ned=np.zeros(3),
        acceleration_ned=np.zeros(3), yaw_deg=0.0, confidence=1.0,
    )
    assert OffboardSetpointV1.from_setpoint(sp).position.frame is FrameId.LOCAL_NED
    t = SimpleNamespace(
        connected=True, armed=False, flight_mode=SimpleNamespace(value="IDLE"), x=1.0, y=2.0, z=0.0,
        altitude=0.0, velocity=-0.5, ground_speed=0.0, heading=270.0, battery_percentage=50.0,
        latitude=None, longitude=None, link_age_s=0.0, connection_quality=SimpleNamespace(value="GOOD"),
        battery_available=False,
    )
    sample = TelemetrySampleV1.from_telemetry(t)
    assert sample.speed_m_s == 0.5 and sample.battery_pct is None  # unknown, not 0 or 50
