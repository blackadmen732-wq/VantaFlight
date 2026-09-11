"""Tests for Fast Lab kinematic driver, deterministic simulation, and training success."""
from __future__ import annotations

import numpy as np
import pytest

from vantaflight.simulation.kinematic import KinematicDriver
from vantaflight.simulation.truth import AircraftTruth, SimulationTruth
from vantaflight.simulation.models import (
    FaultConfig,
    FaultType,
    GazeboGate,
    SimSessionConfig,
    SimSessionState,
    SimulationWorld,
)
from vantaflight.simulation.runner import SimulationRunner, SimSessionResult
from vantaflight.simulation.faults import FaultInjector
from vantaflight.training.engine import TrainingEngine
from vantaflight.training.models import CampaignConfig, CampaignState, RunConfig


def _straight_world(num_gates: int = 3, spacing: float = 5.0) -> SimulationWorld:
    """Build a simple straight-line course for testing."""
    gates = []
    for i in range(num_gates):
        gates.append(GazeboGate(
            gate_id=f"gate_{i:03d}",
            position=np.array([spacing * (i + 1), 0.0, 1.5]),
            normal=np.array([1.0, 0.0, 0.0]),
            width=2.0,
            height=2.0,
            order=i,
        ))
    return SimulationWorld(
        course_id="test_straight",
        gates=gates,
        start_position=np.array([0.0, 0.0, 1.5]),
        start_yaw_deg=0.0,
    )


class TestKinematicDriver:
    def test_step_zero_dt_returns_copy(self):
        kd = KinematicDriver()
        pos = np.array([1.0, 2.0, 3.0])
        vel = np.array([1.0, 0.0, 0.0])
        new_pos, new_vel = kd.step(pos, vel, vel, dt=0.0)
        np.testing.assert_array_equal(new_pos, pos)
        np.testing.assert_array_equal(new_vel, vel)
        assert new_pos is not pos

    def test_step_integrates_position(self):
        kd = KinematicDriver(max_speed=10.0, max_accel=100.0)
        pos = np.zeros(3)
        vel = np.array([2.0, 0.0, 0.0])
        new_pos, new_vel = kd.step(pos, vel, vel, dt=1.0)
        np.testing.assert_allclose(new_pos, [2.0, 0.0, 0.0])

    def test_step_respects_max_speed(self):
        kd = KinematicDriver(max_speed=3.0, max_accel=100.0)
        pos = np.zeros(3)
        vel = np.zeros(3)
        desired = np.array([10.0, 0.0, 0.0])
        _, new_vel = kd.step(pos, vel, desired, dt=1.0)
        assert float(np.linalg.norm(new_vel)) <= 3.0 + 1e-9

    def test_step_respects_max_accel(self):
        kd = KinematicDriver(max_speed=100.0, max_accel=2.0)
        pos = np.zeros(3)
        vel = np.zeros(3)
        desired = np.array([50.0, 0.0, 0.0])
        _, new_vel = kd.step(pos, vel, desired, dt=1.0)
        assert float(np.linalg.norm(new_vel)) <= 2.0 + 1e-9

    def test_navigate_toward_at_target(self):
        kd = KinematicDriver()
        pos = np.array([5.0, 0.0, 0.0])
        target = np.array([5.0, 0.0, 0.0])
        desired = kd.navigate_toward(pos, target, 5.0)
        assert float(np.linalg.norm(desired)) < 1e-6

    def test_navigate_toward_direction(self):
        kd = KinematicDriver()
        pos = np.zeros(3)
        target = np.array([10.0, 0.0, 0.0])
        desired = kd.navigate_toward(pos, target, 5.0)
        assert desired[0] > 0
        np.testing.assert_allclose(desired[1:], [0.0, 0.0], atol=1e-9)

    def test_navigate_decelerates_near_target(self):
        kd = KinematicDriver(max_speed=5.0, max_accel=3.0)
        far = kd.navigate_toward(np.zeros(3), np.array([50.0, 0.0, 0.0]), 5.0)
        near = kd.navigate_toward(np.zeros(3), np.array([0.5, 0.0, 0.0]), 5.0)
        assert float(np.linalg.norm(near)) < float(np.linalg.norm(far))

    def test_build_truth_snapshot(self):
        kd = KinematicDriver()
        pos = np.array([1.0, 2.0, 3.0])
        vel = np.array([1.0, 0.0, 0.0])
        prev_vel = np.array([0.5, 0.0, 0.0])
        truth = kd.build_truth(1.0, pos, vel, prev_vel, dt=0.5)
        assert isinstance(truth, AircraftTruth)
        assert truth.timestamp == 1.0
        np.testing.assert_array_equal(truth.position, pos)
        np.testing.assert_allclose(truth.acceleration, [1.0, 0.0, 0.0])

    def test_deterministic_step_sequence(self):
        kd = KinematicDriver(max_speed=5.0, max_accel=3.0)
        results = []
        for _ in range(2):
            pos = np.zeros(3)
            vel = np.zeros(3)
            target = np.array([20.0, 5.0, 2.0])
            for step in range(100):
                desired = kd.navigate_toward(pos, target, 5.0)
                pos, vel = kd.step(pos, vel, desired, dt=0.05)
            results.append(pos.copy())
        np.testing.assert_array_equal(results[0], results[1])


class TestSimulationRunnerCompletion:
    def test_runner_completes_straight_course(self):
        runner = SimulationRunner(aircraft_speed=5.0)
        world = _straight_world(num_gates=3, spacing=5.0)
        config = SimSessionConfig(course_id="test", max_time_s=60.0)

        runner.prepare(config, world)
        runner.start()

        sim_time = 0.0
        dt = 0.05
        while runner.state == SimSessionState.RUNNING and sim_time < 60.0:
            sim_time += dt
            runner.tick(sim_time)

        assert runner.state == SimSessionState.COMPLETE
        result = runner.results[-1]
        assert result.complete is True
        assert result.gates_passed == 3
        assert result.total_gates == 3
        assert result.error is None

    def test_runner_timeout_produces_failed(self):
        runner = SimulationRunner(aircraft_speed=0.1)
        world = _straight_world(num_gates=3, spacing=50.0)
        config = SimSessionConfig(course_id="test", max_time_s=2.0)

        runner.prepare(config, world)
        runner.start()

        sim_time = 0.0
        dt = 0.05
        while runner.state == SimSessionState.RUNNING:
            sim_time += dt
            runner.tick(sim_time)

        assert runner.state == SimSessionState.FAILED
        result = runner.results[-1]
        assert result.complete is False
        assert result.error == "max_time_exceeded"

    def test_timeout_never_overwrites_complete(self):
        runner = SimulationRunner(aircraft_speed=10.0)
        world = _straight_world(num_gates=2, spacing=3.0)
        config = SimSessionConfig(course_id="test", max_time_s=60.0)

        runner.prepare(config, world)
        runner.start()

        sim_time = 0.0
        dt = 0.05
        while runner.state == SimSessionState.RUNNING:
            sim_time += dt
            runner.tick(sim_time)

        assert runner.state == SimSessionState.COMPLETE
        second = runner.stop(error="max_time_exceeded")
        assert second.state == SimSessionState.COMPLETE
        assert runner.state == SimSessionState.COMPLETE

    def test_multi_gate_ordered_progression(self):
        runner = SimulationRunner(aircraft_speed=5.0)
        world = _straight_world(num_gates=5, spacing=4.0)
        config = SimSessionConfig(course_id="test", max_time_s=120.0)

        runner.prepare(config, world)
        runner.start()

        sim_time = 0.0
        dt = 0.05
        while runner.state == SimSessionState.RUNNING and sim_time < 120.0:
            sim_time += dt
            runner.tick(sim_time)

        assert runner.state == SimSessionState.COMPLETE
        result = runner.results[-1]
        assert result.gates_passed == 5
        assert result.complete is True
        summary = runner.truth.race_summary()
        times = summary["gate_pass_times"]
        sorted_times = sorted(times.values())
        assert sorted_times == list(times.values()), "gates should be passed in order"


class TestOutOfOrderGateCrossing:
    def test_later_gate_crossed_first_does_not_advance(self):
        gates = [
            GazeboGate(
                gate_id="gate_000",
                position=np.array([5.0, 0.0, 1.5]),
                normal=np.array([1.0, 0.0, 0.0]),
                width=2.0, height=2.0, order=0,
            ),
            GazeboGate(
                gate_id="gate_001",
                position=np.array([10.0, 0.0, 1.5]),
                normal=np.array([1.0, 0.0, 0.0]),
                width=2.0, height=2.0, order=1,
            ),
        ]
        world = SimulationWorld(
            course_id="test_ooo",
            gates=gates,
            start_position=np.array([0.0, 0.0, 1.5]),
        )
        runner = SimulationRunner(aircraft_speed=5.0)
        config = SimSessionConfig(course_id="test_ooo", max_time_s=60.0)
        runner.prepare(config, world)
        runner.start()

        truth = runner.truth
        prev_pos = np.array([9.5, 0.0, 1.5])
        curr_pos = np.array([10.5, 0.0, 1.5])
        crossed = truth.check_gate_crossing(curr_pos, prev_pos)
        assert crossed == "gate_001"

        assert runner._next_gate_idx == 0, \
            "out-of-order crossing must not advance ordered progression"


class TestDeterministicFaultTiming:
    def test_identical_seed_produces_identical_faults(self):
        faults = [
            FaultConfig(FaultType.NOISE, probability=0.5, duration_s=1.0, magnitude=10.0, seed=42),
            FaultConfig(FaultType.FRAME_DROP, probability=0.3, duration_s=0.5, magnitude=1.0, seed=99),
        ]
        histories = []
        for _ in range(2):
            injector = FaultInjector(faults)
            sim_time = 0.0
            for _ in range(200):
                sim_time += 0.05
                injector.tick(sim_time)
            histories.append(injector.history)

        assert len(histories[0]) == len(histories[1])
        for a, b in zip(histories[0], histories[1]):
            assert a["fault_type"] == b["fault_type"]
            assert abs(a["start"] - b["start"]) < 1e-9
            assert abs(a["duration"] - b["duration"]) < 1e-9

    def test_deterministic_full_run(self):
        results = []
        for _ in range(2):
            runner = SimulationRunner(aircraft_speed=5.0)
            world = _straight_world(num_gates=3, spacing=5.0)
            fault_cfg = [
                FaultConfig(FaultType.NOISE, probability=0.4, duration_s=0.5, magnitude=10.0, seed=7),
            ]
            config = SimSessionConfig(
                course_id="det_test", max_time_s=60.0, faults=fault_cfg,
            )
            runner.prepare(config, world)
            runner.start()

            sim_time = 0.0
            dt = 0.05
            while runner.state == SimSessionState.RUNNING and sim_time < 60.0:
                sim_time += dt
                runner.tick(sim_time)

            results.append(runner.results[-1])

        assert results[0].gates_passed == results[1].gates_passed
        assert results[0].complete == results[1].complete
        assert abs(results[0].race_time_s - results[1].race_time_s) < 1e-6
        assert results[0].faults_injected == results[1].faults_injected


class TestTrainingEngineSuccess:
    def test_training_produces_successful_run(self):
        runner = SimulationRunner(aircraft_speed=5.0)
        engine = TrainingEngine(sim_runner=runner)

        config = CampaignConfig(
            name="success_test",
            course_modes=["SPEED_RUN"],
            seed_range=(42, 43),
            gate_counts=[4],
            difficulty_tiers=["BEGINNER"],
            max_time_per_run_s=120.0,
        )
        campaign_id = engine.create_campaign(config)
        engine.start_campaign(campaign_id)

        result = engine.execute_next_run(campaign_id)
        assert result is not None
        assert result.complete is True, f"run should complete, failures: {result.failures}"
        assert result.success is True
        assert result.gates_passed == result.total_gates
        assert result.gates_passed > 0

    def test_training_campaign_completes(self):
        runner = SimulationRunner(aircraft_speed=5.0)
        engine = TrainingEngine(sim_runner=runner)

        config = CampaignConfig(
            name="campaign_test",
            course_modes=["SPEED_RUN"],
            seed_range=(0, 3),
            gate_counts=[4],
            difficulty_tiers=["BEGINNER"],
            max_time_per_run_s=120.0,
        )
        campaign_id = engine.create_campaign(config)
        engine.start_campaign(campaign_id)

        while True:
            result = engine.execute_next_run(campaign_id)
            if result is None:
                break

        summary = engine.get_summary(campaign_id)
        assert summary.state == CampaignState.COMPLETE
        assert summary.completed_runs == 3
        assert summary.successful_runs > 0

    def test_training_with_faults_deterministic(self):
        results_all = []
        for _ in range(2):
            runner = SimulationRunner(aircraft_speed=5.0)
            engine = TrainingEngine(sim_runner=runner)

            config = CampaignConfig(
                name="det_fault_test",
                course_modes=["SPEED_RUN"],
                seed_range=(10, 11),
                gate_counts=[4],
                difficulty_tiers=["BEGINNER"],
                fault_profiles=[["light_noise"]],
                max_time_per_run_s=120.0,
            )
            campaign_id = engine.create_campaign(config)
            engine.start_campaign(campaign_id)

            run_results = []
            while True:
                r = engine.execute_next_run(campaign_id)
                if r is None:
                    break
                run_results.append(r)
            results_all.append(run_results)

        for a, b in zip(results_all[0], results_all[1]):
            assert a.gates_passed == b.gates_passed
            assert a.complete == b.complete
            assert abs(a.race_time_s - b.race_time_s) < 1e-6
            assert a.faults_injected == b.faults_injected
