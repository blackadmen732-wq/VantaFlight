"""VantaClock: explicit clock domains, fake-clock determinism, cross-clock sync."""
from __future__ import annotations

import random

import pytest

from vantaflight.core.clock import (
    NS_PER_S,
    ClockConversionError,
    ClockDomain,
    ClockSync,
    ClockSyncConfig,
    MixedClockDomainError,
    SequentialIds,
    SyncOutcome,
    TimeStamp,
    VantaClock,
)

HOST = ClockDomain.HOST_MONOTONIC


# -- TimeStamp ---------------------------------------------------------------------
def test_timestamp_validates_its_fields():
    with pytest.raises(TypeError):
        TimeStamp(1.5, HOST)  # nanoseconds are integers
    with pytest.raises(TypeError):
        TimeStamp(True, HOST)
    with pytest.raises(ValueError):
        TimeStamp(1, HOST, uncertainty_ns=-1)
    with pytest.raises(ValueError):
        TimeStamp(1, "NOT_A_DOMAIN")
    assert TimeStamp(5, "VEHICLE_BOOT").domain is ClockDomain.VEHICLE_BOOT


def test_same_clock_arithmetic_and_uncertainty():
    a = TimeStamp.from_seconds(10.0, HOST, uncertainty_s=0.003)
    b = TimeStamp.from_seconds(10.5, HOST, uncertainty_s=0.004)
    elapsed = b - a
    assert elapsed.seconds == pytest.approx(0.5)
    assert elapsed.uncertainty_s == pytest.approx(0.005, abs=1e-9)  # root-sum-square
    assert a < b and b >= a
    assert a.plus_s(0.5).value_ns == b.value_ns


@pytest.mark.parametrize(
    "other",
    [
        TimeStamp(10, ClockDomain.UTC_WALL),
        TimeStamp(10, ClockDomain.VEHICLE_BOOT),
        TimeStamp(10, ClockDomain.CAMERA_DEVICE, clock_id="front"),
    ],
)
def test_mixed_clock_arithmetic_is_refused(other):
    host = TimeStamp(10, HOST)
    with pytest.raises(MixedClockDomainError):
        host - other
    with pytest.raises(MixedClockDomainError):
        _ = host < other


def test_two_cameras_are_different_clocks():
    front = TimeStamp(10, ClockDomain.CAMERA_DEVICE, clock_id="front")
    down = TimeStamp(10, ClockDomain.CAMERA_DEVICE, clock_id="down")
    with pytest.raises(MixedClockDomainError):
        front - down


def test_timestamp_round_trips_through_dict():
    ts = TimeStamp(123, ClockDomain.CAMERA_DEVICE, 7, "front")
    assert TimeStamp.from_dict(ts.to_dict()) == ts
    assert TimeStamp.from_dict(TimeStamp(5, HOST).to_dict()) == TimeStamp(5, HOST)


# -- fake clock --------------------------------------------------------------------------
def test_fake_clock_is_deterministic():
    def run():
        clock = VantaClock.fake(monotonic_s=50.0, wall_s=1_800_000_000.0)
        out = []
        for step in (0.1, 0.25, 1.0):
            clock.advance(step)
            out.append((clock.now().value_ns, clock.now(ClockDomain.UTC_WALL).value_ns))
        return out

    assert run() == run()
    clock = VantaClock.fake(monotonic_s=50.0)
    assert clock() == 50.0  # callable like a legacy float clock
    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_only_fake_clocks_can_be_advanced():
    with pytest.raises(RuntimeError):
        VantaClock().advance(1.0)


def test_from_seconds_wraps_a_legacy_float_clock():
    t = [100.0]
    clock = VantaClock.from_seconds(lambda: t[0])
    start = clock.now()
    wall0 = clock.wall_s()
    t[0] += 2.5
    assert clock.age(start).seconds == pytest.approx(2.5)
    assert clock.wall_s() - wall0 == pytest.approx(2.5, abs=1e-6)  # wall follows the wrapped clock
    assert VantaClock.from_seconds(clock) is clock


def test_utc_converts_to_host_and_back():
    clock = VantaClock.fake()
    wall = clock.now(ClockDomain.UTC_WALL)
    host = clock.to_host(wall)
    assert host.domain is HOST
    assert host.value_ns == clock.now().value_ns
    assert clock.convert(host, ClockDomain.UTC_WALL).value_ns == wall.value_ns


def test_ntp_step_does_not_change_ages():
    clock = VantaClock.fake()
    stamp = clock.now()
    clock.fake_source.step_wall(-30.0)  # NTP jumps the wall clock back
    clock.advance(1.0)
    assert clock.age(stamp).seconds == pytest.approx(1.0)


def test_unknown_age_counts_as_stale():
    clock = VantaClock.fake()
    foreign = TimeStamp(5, ClockDomain.VEHICLE_BOOT, clock_id="never-synced")
    assert clock.is_stale(foreign, max_age_s=100.0)
    assert clock.is_stale(None, max_age_s=100.0)
    with pytest.raises(ClockConversionError):
        clock.to_host(foreign)


# -- clock sync ----------------------------------------------------------------------------
def simulate(*, drift_ppm, offset_s, lat_min_ms, jitter_ms, hz, duration_s, nominal_ms, seed=7):
    """Feed a sync with a drifting, jittery source; return (errors_ns, uncertainties_ns, sync)."""
    rng = random.Random(seed)
    clock = VantaClock.fake()
    sync = clock.sync(
        ClockDomain.CAMERA_DEVICE, "cam", nominal_latency_ns=int(nominal_ms * 1e6)
    )
    t0 = clock.now().value_ns
    errors, uncertainties = [], []
    for i in range(int(duration_s * hz)):
        true_host = t0 + int(i / hz * NS_PER_S)
        src = int((true_host - t0) * (1 + drift_ppm * 1e-6)) + int(offset_s * NS_PER_S)
        received = true_host + int((lat_min_ms + rng.random() * jitter_ms) * 1e6)
        source = TimeStamp(src, ClockDomain.CAMERA_DEVICE, clock_id="cam")
        sync.observe(source, TimeStamp(received, HOST))
        if sync.synced:
            converted = sync.to_host(source)
            errors.append(abs(converted.value_ns - true_host))
            uncertainties.append(converted.uncertainty_ns)
    return errors, uncertainties, sync


def p99(values):
    ordered = sorted(values)
    return ordered[int(0.99 * (len(ordered) - 1))]


@pytest.mark.parametrize(
    "scenario",
    [
        dict(drift_ppm=40, offset_s=5.0, lat_min_ms=2.0, jitter_ms=3.0, hz=30, duration_s=20, nominal_ms=2.0),
        dict(drift_ppm=-120, offset_s=123.0, lat_min_ms=1.0, jitter_ms=10.0, hz=15, duration_s=30, nominal_ms=1.0),
        dict(drift_ppm=0, offset_s=0.5, lat_min_ms=0.5, jitter_ms=0.5, hz=100, duration_s=5, nominal_ms=0.5),
    ],
)
def test_conversion_error_stays_inside_reported_uncertainty(scenario):
    errors, uncertainties, sync = simulate(**scenario)
    covered = sum(e <= u for e, u in zip(errors, uncertainties)) / len(errors)
    assert covered >= 0.99
    assert p99(errors) < 3e6  # better than 3 ms even with 10 ms of jitter
    stats = sync.stats()
    assert stats["synced"] and stats["latency_compensated"]
    for key in ("residual_p50_ms", "residual_p95_ms", "residual_p99_ms", "conversion_uncertainty_ms"):
        assert stats[key] >= 0.0


def test_unknown_transport_latency_is_reported_not_hidden():
    errors, _, sync = simulate(
        drift_ppm=40, offset_s=5.0, lat_min_ms=2.0, jitter_ms=3.0, hz=30, duration_s=20, nominal_ms=0.0
    )
    # One-way timing cannot see the 2 ms minimum latency: conversions are late by it...
    assert 1.5e6 < sorted(errors)[len(errors) // 2] < 2.6e6
    # ...and the sync says so.
    assert sync.stats()["latency_compensated"] is False


def test_drift_is_estimated_with_the_right_sign():
    _, _, sync = simulate(
        drift_ppm=300, offset_s=1.0, lat_min_ms=0.5, jitter_ms=0.2, hz=50, duration_s=5, nominal_ms=0.5
    )
    assert sync.stats()["source_drift_ppm"] == pytest.approx(300, abs=25)


def test_single_spike_is_rejected_without_moving_the_fit():
    clock = VantaClock.fake()
    sync = clock.sync(ClockDomain.VEHICLE_BOOT, "px4")
    for i in range(20):
        src = TimeStamp(i * 50_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4")
        sync.observe(src, TimeStamp(10 * NS_PER_S + i * 50_000_000 + 1_000_000, HOST))
    probe = TimeStamp(500_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4")
    before = sync.to_host(probe).value_ns
    spike = sync.observe(
        TimeStamp(20 * 50_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4"),
        TimeStamp(10 * NS_PER_S + 20 * 50_000_000 + 900_000_000, HOST),  # 0.9 s late
    )
    assert spike is SyncOutcome.OUTLIER
    assert sync.to_host(probe).value_ns == before
    assert sync.stats()["outliers"] == 1


def test_vehicle_reboot_is_detected_as_a_jump():
    clock = VantaClock.fake()
    sync = clock.sync(ClockDomain.VEHICLE_BOOT, "px4")
    host0 = 10 * NS_PER_S
    for i in range(20):
        sync.observe(
            TimeStamp(500 * NS_PER_S + i * 50_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4"),
            TimeStamp(host0 + i * 50_000_000, HOST),
        )
    # Reboot: the boot clock restarts near zero while host time keeps going.
    outcomes = [
        sync.observe(
            TimeStamp(i * 50_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4"),
            TimeStamp(host0 + (20 + i) * 50_000_000, HOST),
        )
        for i in range(3)
    ]
    assert outcomes == [SyncOutcome.OUTLIER, SyncOutcome.OUTLIER, SyncOutcome.JUMP]
    assert sync.stats()["jumps"] == 1
    after = sync.to_host(TimeStamp(100_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4"))
    assert after.value_ns == pytest.approx(host0 + 22 * 50_000_000, abs=2_000_000)


def test_inconsistent_spikes_are_not_a_jump():
    clock = VantaClock.fake()
    sync = clock.sync(ClockDomain.VEHICLE_BOOT, "px4")
    for i in range(10):
        sync.observe(TimeStamp(i * NS_PER_S, ClockDomain.VEHICLE_BOOT, clock_id="px4"), TimeStamp(i * NS_PER_S + 7, HOST))
    for i, late_s in enumerate((0.5, 3.0, 9.0)):
        src = (10 + i) * NS_PER_S
        outcome = sync.observe(
            TimeStamp(src, ClockDomain.VEHICLE_BOOT, clock_id="px4"),
            TimeStamp(src + int(late_s * NS_PER_S), HOST),
        )
        assert outcome is SyncOutcome.OUTLIER
    assert sync.stats()["jumps"] == 0


def test_conversion_is_refused_until_enough_samples():
    sync = ClockSync(ClockDomain.CAMERA_DEVICE, "cam", config=ClockSyncConfig(min_samples_to_sync=3))
    src = TimeStamp(0, ClockDomain.CAMERA_DEVICE, clock_id="cam")
    for i in range(2):
        sync.observe(TimeStamp(i, ClockDomain.CAMERA_DEVICE, clock_id="cam"), TimeStamp(1000 + i, HOST))
        with pytest.raises(ClockConversionError):
            sync.to_host(src)
    sync.observe(TimeStamp(2, ClockDomain.CAMERA_DEVICE, clock_id="cam"), TimeStamp(1002, HOST))
    assert sync.to_host(src).domain is HOST


def test_from_host_inverts_to_host():
    _, _, sync = simulate(
        drift_ppm=80, offset_s=42.0, lat_min_ms=1.0, jitter_ms=1.0, hz=30, duration_s=10, nominal_ms=1.0
    )
    src = TimeStamp(50 * NS_PER_S, ClockDomain.CAMERA_DEVICE, clock_id="cam")
    back = sync.from_host(sync.to_host(src))
    assert abs(back.value_ns - src.value_ns) < 1000


def test_sync_refuses_the_wrong_clocks():
    sync = ClockSync(ClockDomain.CAMERA_DEVICE, "front")
    with pytest.raises(MixedClockDomainError):
        sync.observe(TimeStamp(1, ClockDomain.CAMERA_DEVICE, clock_id="down"), TimeStamp(1, HOST))
    with pytest.raises(MixedClockDomainError):
        sync.observe(TimeStamp(1, ClockDomain.CAMERA_DEVICE, clock_id="front"), TimeStamp(1, ClockDomain.UTC_WALL))
    with pytest.raises(ValueError):
        ClockSync(HOST)


def test_vantaclock_now_and_age_in_a_foreign_domain():
    clock = VantaClock.fake()
    for i in range(10):
        host = clock.now()
        clock.observe(TimeStamp(host.value_ns - 5 * NS_PER_S, ClockDomain.VEHICLE_BOOT, clock_id="px4"), host)
        clock.advance(0.05)
    vehicle_now = clock.now(ClockDomain.VEHICLE_BOOT, "px4")
    assert vehicle_now.domain is ClockDomain.VEHICLE_BOOT
    sample = TimeStamp(clock.now().value_ns - 5 * NS_PER_S - 200_000_000, ClockDomain.VEHICLE_BOOT, clock_id="px4")
    age = clock.age(sample)
    assert age.seconds == pytest.approx(0.2, abs=0.002)
    assert age.uncertainty_ns is not None
    assert [s["clock_id"] for s in clock.stats()["syncs"]] == ["px4"]


def test_native_clocks_do_not_take_a_sync():
    with pytest.raises(ValueError):
        VantaClock.fake().sync(ClockDomain.UTC_WALL)


def test_sequential_ids_are_reproducible():
    a, b = SequentialIds(5), SequentialIds(5)
    assert [a() for _ in range(3)] == [b() for _ in range(3)]
    assert SequentialIds(6)() != SequentialIds(5)()
