from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from vantaflight.api_models import RunMetricModel
from vantaflight.config import PX4_SITL_URL
from vantaflight.intelligence import IntelligenceRuntime
from vantaflight.mavlink import MAVLinkConfig, PhysicalMAVLinkBlocked
from vantaflight.racing import (
    AircraftState,
    GateTarget,
    LookaheadConfig,
    RaceState,
    SimulationOnlyExecutionGuard,
    SpeedEnvelopeConfig,
    TargetSlot,
    VantaRace,
    VantaRaceConfig,
    speed_envelope,
)


@dataclass
class Scene:
    CURRENT: GateTarget | None
    NEXT: GateTarget | None = None
    FUTURE: GateTarget | None = None


def gate(position=(5.0, 0.0, 0.0)) -> GateTarget:
    return GateTarget(
        position,
        [1.0, 0.0, 0.0],
        2.0,
        slot=TargetSlot.CURRENT,
    )


def vision_result(
    candidate_id: str | None,
    profile_id: str = "gate",
    timestamp: float = 1.0,
) -> SimpleNamespace:
    selected = (
        None
        if candidate_id is None
        else SimpleNamespace(candidate_id=candidate_id, profile_id=profile_id)
    )
    return SimpleNamespace(
        frame=SimpleNamespace(camera_source="synthetic"),
        selected=selected,
        lock=SimpleNamespace(state=SimpleNamespace(value="TRACKED")),
        timeline=SimpleNamespace(end_to_end_s=0.01),
        fusion=SimpleNamespace(
            observed_pose=SimpleNamespace(position_world_m=np.array([1.0, 2.0, 3.0])),
            predicted_pose=None,
            velocity=np.zeros(3),
            confidence=0.8,
            uncertainty=np.eye(3) * 0.1,
            age_s=0.0,
            timestamp=timestamp,
            contributions={},
        ),
    )


@pytest.mark.parametrize("magnitude", [1e-308, 1e308])
def test_gate_normalization_handles_finite_float_extremes(magnitude: float) -> None:
    target = GateTarget([0, 0, 0], [magnitude, -magnitude, 0], 1)
    assert np.all(np.isfinite(target.normal))
    assert np.linalg.norm(target.normal) == pytest.approx(1.0)
    np.testing.assert_allclose(target.normal, [2**-0.5, -(2**-0.5), 0])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"next_weight": np.nan},
        {"future_weight": np.inf},
        {"exit_distance": np.nan},
    ],
)
def test_lookahead_rejects_non_finite_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="finite"):
        LookaheadConfig(**kwargs)
    with pytest.raises(ValueError, match="finite"):
        VantaRaceConfig(command_lookahead_s=np.nan)


def test_min_speed_never_overrides_harder_local_safety_cap() -> None:
    distances = np.arange(3, dtype=float)
    speeds = speed_envelope(
        distances,
        np.zeros(3),
        np.zeros(3),
        np.full(3, 0.001),
        np.ones(3),
        np.zeros(3),
        initial_speed=100.0,
        config=SpeedEnvelopeConfig(min_speed=2.0, aggression=1.0),
    )
    assert np.all(speeds < 2.0)


def test_planner_preserves_current_direction_and_caps_all_setpoint_speeds() -> None:
    planner = VantaRace(VantaRaceConfig(aggression=1.0, trajectory_samples=41))
    aircraft = AircraftState([0, 0, 0], [0, 4, 0])
    planner.plan(aircraft, Scene(gate()))
    trajectory = planner._trajectory
    assert trajectory is not None
    assert np.dot(trajectory.velocities[0], aircraft.velocity) > 0.0
    assert trajectory.velocities[0][0] == pytest.approx(0.0)
    np.testing.assert_array_less(
        np.linalg.norm(trajectory.velocities, axis=1),
        planner.last_speed_profile + 1e-10,
    )

    sample_times = np.linspace(trajectory.times[0], trajectory.times[-1], 1001)
    for timestamp in sample_times:
        desired = trajectory.evaluate(timestamp)
        limit = np.interp(
            timestamp,
            trajectory.times,
            planner.last_speed_profile,
        )
        assert np.linalg.norm(desired.velocity) <= limit + 1e-10


def test_single_gate_planner_emits_course_completion() -> None:
    planner = VantaRace()
    scene = Scene(gate())
    before = AircraftState([0, 0, 0], [1, 0, 0])
    for timestamp in range(5):
        planner.plan(
            AircraftState(before.position, before.velocity, timestamp=float(timestamp)),
            scene,
        )
    assert planner.state is RaceState.PASS
    planner.plan(AircraftState([6, 0, 0], [1, 0, 0], timestamp=5.0), scene)
    assert planner.state is RaceState.COMPLETE


def test_default_sitl_endpoint_is_authorized_but_config_flag_cannot_unlock_physical() -> None:
    config = MAVLinkConfig()
    assert config.system_address == PX4_SITL_URL
    capabilities = SimpleNamespace(
        is_simulated=True,
        adapter_type="px4_sitl",
        supported_capabilities=["simulation"],
    )
    permit = SimulationOnlyExecutionGuard.authorize(config.system_address, capabilities)
    assert permit.endpoint == PX4_SITL_URL

    with pytest.raises(PhysicalMAVLinkBlocked):
        MAVLinkConfig(
            system_address="serial:///dev/ttyUSB0",
            simulation_only=False,
        )
    with pytest.raises(PhysicalMAVLinkBlocked):
        MAVLinkConfig(
            system_address="udp://192.168.1.10:14540",
            simulation_only=False,
        )


def test_publish_metric_validates_limit_before_mutating_and_stays_bounded() -> None:
    runtime = IntelligenceRuntime()
    metric = RunMetricModel(run_id="run", metric_name="lap", metric_value=1.0)
    for invalid in (0, -1, 1.5, True, runtime.MAX_RUN_METRICS + 1):
        with pytest.raises(ValueError, match="limit"):
            runtime.publish_metric(metric, limit=invalid)  # type: ignore[arg-type]
    assert runtime.run_metrics == []

    runtime.publish_metric(metric, limit=1)
    runtime.publish_metric(
        RunMetricModel(run_id="run", metric_name="lap", metric_value=2.0),
        limit=1,
    )
    assert [item.metric_value for item in runtime.run_metrics] == [2.0]


def test_runtime_track_ids_are_stable_and_stale_scene_state_is_removed() -> None:
    runtime = IntelligenceRuntime()
    runtime.publish_vision_result(vision_result("frame-candidate-1"))
    first_id = runtime.scene.current.target_id
    runtime.publish_vision_result(vision_result("frame-candidate-2", timestamp=2.0))
    assert runtime.scene.current.target_id == first_id
    assert list(runtime.tracks) == [first_id]

    runtime.publish_vision_result(vision_result(None, timestamp=3.0))
    assert runtime.scene.current is None
    assert runtime.scene.next is None
    assert runtime.scene.future is None
    assert runtime.tracks == {}
    assert runtime._active_profile_id is None

    runtime.publish_vision_result(vision_result("frame-candidate-3", timestamp=4.0))
    assert runtime.scene.current.target_id != first_id
