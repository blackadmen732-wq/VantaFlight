from __future__ import annotations

import math

import pytest

from vantaflight.energy import EnergyModel
from vantaflight.evolution import (
    AlgorithmGenome,
    ChampionChallengerEvaluator,
    RunPackage,
    WeaknessMap,
)
from vantaflight.mission.strategy import MissionStrategyEngine, ObjectiveCandidate
from vantaflight.models import ConnectionQuality, FlightMode, Telemetry
from vantaflight.motion import (
    HookAlignmentController,
    IBVSController,
    PortalGeometry,
    PortalTraversalController,
    SafeCorridorGenerator,
)
from vantaflight.payload import PassiveHookProfile, PayloadManager
from vantaflight.state import TargetObservation, VantaStateEstimator
from vantaflight.world import DomainRandomizer, built_in_environment_profiles


def test_vantastate_never_treats_missing_position_as_precise_zero():
    tel = Telemetry(
        connected=True,
        flight_mode=FlightMode.HOLD,
        position_available=False,
        velocity_available=False,
        altitude_available=False,
        battery_available=False,
        connection_quality=ConnectionQuality.GOOD,
    )
    state = VantaStateEstimator().update(tel, now=100.0)
    assert state.position_available is False
    assert state.position_uncertainty_m >= 100.0
    assert state.confidence == 0.0


def test_vantastate_tracks_and_expires_target():
    estimator = VantaStateEstimator(target_timeout_s=1.0)
    tel = Telemetry(connected=True)
    estimator.update(
        tel,
        [TargetObservation("pad", (1.0, 0.0, 0.0), 10.0, 0.9, 0.1)],
        now=10.0,
    )
    state = estimator.update(
        tel,
        [TargetObservation("pad", (1.2, 0.0, 0.0), 10.2, 0.9, 0.1)],
        now=10.3,
    )
    assert len(state.targets) == 1
    assert state.targets[0].velocity_world[0] > 0
    expired = estimator.update(tel, now=12.0)
    assert expired.targets == ()


def test_strategy_engine_prefers_expected_utility_not_just_distance():
    engine = MissionStrategyEngine(cruise_speed_mps=1.0, time_weight=0.01)
    near_low = ObjectiveCandidate("near", (1, 0, 0), reward=1, success_probability=0.2)
    far_high = ObjectiveCandidate("far", (3, 0, 0), reward=5, success_probability=0.95)
    ranked = engine.rank_next((0, 0, 0), [near_low, far_high])
    assert ranked[0][0].objective_id == "far"


def test_route_optimizer_respects_time_and_return():
    engine = MissionStrategyEngine(cruise_speed_mps=1.0, time_weight=0.01)
    objectives = [
        ObjectiveCandidate("a", (1, 0, 0), reward=2),
        ObjectiveCandidate("b", (2, 0, 0), reward=2),
    ]
    decision = engine.optimize_route((0, 0, 0), objectives, return_to=(0, 0, 0), max_time_s=5.0)
    assert decision.ordered_objective_ids == ("a", "b")
    assert decision.expected_time_s == pytest.approx(4.0)


def test_corridor_requires_real_clearance_and_contains_route():
    corridor = SafeCorridorGenerator(vehicle_radius_m=0.1, safety_margin_m=0.05).generate(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
        available_clearance_m=0.5,
    )
    assert corridor.contains((0.5, 0.1, 0.0))
    assert not corridor.contains((0.5, 1.0, 0.0))
    with pytest.raises(ValueError):
        SafeCorridorGenerator(vehicle_radius_m=0.1, safety_margin_m=0.05).generate(
            [(0, 0, 0), (1, 0, 0)],
            available_clearance_m=0.14,
        )


def test_ibvs_descends_only_when_centered_and_confident():
    controller = IBVSController()
    off_center = controller.command(0.5, 0.0, confidence=0.9, range_m=0.5)
    assert off_center.vz_mps == 0.0
    centered = controller.command(0.01, 0.01, confidence=0.9, range_m=0.5)
    assert centered.vz_mps > 0.0
    lost = controller.command(0.01, 0.01, confidence=0.2, range_m=0.5)
    assert lost.vx_mps == lost.vy_mps == lost.vz_mps == 0.0


def test_portal_rejects_opening_that_vehicle_cannot_clear():
    controller = PortalTraversalController(vehicle_radius_m=0.2, margin_m=0.1)
    small = PortalGeometry((0, 0, 1), (1, 0, 0), 0.5, 0.5, 0.95)
    with pytest.raises(ValueError):
        controller.safe_points(small)
    portal = PortalGeometry((0, 0, 1), (1, 0, 0), 1.5, 1.5, 0.95)
    align, entry, exit_point = controller.safe_points(portal)
    assert align[0] < entry[0] < exit_point[0]


def test_hook_alignment_controls_contact_point_not_vehicle_center():
    controller = HookAlignmentController((0.2, 0.0, 0.0), max_speed_mps=0.4)
    done = controller.command((0, 0, 0), (0.2, 0, 0), tolerance_m=0.01)
    assert done.complete
    move = controller.command((0, 0, 0), (0.5, 0, 0), tolerance_m=0.01)
    assert 0 < move.vx_mps <= 0.4


def test_payload_manager_requires_verified_configuration():
    manager = PayloadManager(PassiveHookProfile.hopper_default())
    manager.confirm_pickup("rescue-1")
    assert manager.state.loaded and manager.state.verified
    manager.confirm_release()
    assert not manager.state.loaded


def test_energy_model_blocks_objective_when_safe_landing_reserve_is_insufficient():
    model = EnergyModel(base_pct_per_s=1.0, reserve_pct=10.0)
    estimate = model.can_start(
        available_pct=25.0,
        objective_duration_s=10.0,
        return_duration_s=10.0,
    )
    assert estimate.required_pct == pytest.approx(30.0)
    assert estimate.safe_to_start is False


def test_world_randomization_is_seeded_and_bounded():
    base = built_in_environment_profiles()["GUSTY_OUTDOOR"]
    a = DomainRandomizer(42).sample(base, intensity=0.5)
    b = DomainRandomizer(42).sample(base, intensity=0.5)
    assert a == b
    assert 0.0 <= a.visibility <= 1.0
    assert 0.0 <= a.glare <= 1.0


def _run(run_id: str, seed: int, score: float, success: bool) -> RunPackage:
    genome = AlgorithmGenome("s", "st", "m", "mo", "p", "e", "r", "v")
    return RunPackage(
        run_id=run_id,
        mission_type="TIME_TRIAL",
        scenario_id=f"scenario-{seed}",
        seed=seed,
        git_sha="abc123",
        software_version="1.0",
        genome=genome,
        vehicle_profile="hopper",
        world_profile="CALM_INDOOR",
        metrics={"utility": score},
        success=success,
    )


def test_champion_challenger_requires_matched_seeds_and_no_regression():
    evaluator = ChampionChallengerEvaluator(minimum_improvement=0.05)
    champion = [_run("c1", 1, 1.0, True), _run("c2", 2, 1.0, True)]
    challenger = [_run("n1", 1, 1.2, True), _run("n2", 2, 1.1, True)]
    promoted, _, _, reason = evaluator.evaluate("champ", champion, "new", challenger)
    assert promoted and reason == "promotion candidate"


def test_weakness_map_prioritizes_repeated_severe_failures():
    weakness = WeaknessMap()
    for _ in range(20):
        weakness.add(mission="DELIVERY", stage="LAND", condition="LOW_LIGHT", subsystem="SENSE", success=False, severity=0.9)
    for _ in range(20):
        weakness.add(mission="RACE", stage="TURN", condition="CALM", subsystem="MOTION", success=True, severity=0.0)
    ranked = weakness.ranked()
    assert ranked[0].mission == "DELIVERY"
    assert ranked[0].priority > 0.0
