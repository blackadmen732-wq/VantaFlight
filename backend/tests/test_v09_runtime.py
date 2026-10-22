"""Tests for V0.9 runtime components: hardware mode, preflight, training, replay."""
from __future__ import annotations

import time

import pytest


class TestHardwareModeManager:
    def setup_method(self):
        from vantaflight.runtime.hardware_mode import HardwareModeManager, HardwareMode
        self.mgr = HardwareModeManager()
        self.HardwareMode = HardwareMode

    def test_initial_state(self):
        assert self.mgr.mode == self.HardwareMode.SIMULATION
        assert self.mgr.is_simulation is True
        assert self.mgr.can_observe is False
        assert self.mgr.can_command is False
        assert len(self.mgr.history) == 0

    def test_transition_to_observe(self):
        self.mgr.enter_observe("test")
        assert self.mgr.mode == self.HardwareMode.HARDWARE_OBSERVE
        assert self.mgr.is_simulation is False
        assert self.mgr.can_observe is True
        assert self.mgr.can_command is False
        assert len(self.mgr.history) == 1

    def test_transition_to_command_locked(self):
        self.mgr.enter_observe("step1")
        self.mgr.unlock_commands("step2")
        assert self.mgr.mode == self.HardwareMode.HARDWARE_COMMAND_LOCKED
        assert self.mgr.can_command is True
        assert len(self.mgr.history) == 2

    def test_invalid_transition_raises(self):
        with pytest.raises(RuntimeError, match="cannot transition"):
            self.mgr.unlock_commands("skip observe")

    def test_noop_same_mode(self):
        self.mgr.transition(self.HardwareMode.SIMULATION, "noop")
        assert len(self.mgr.history) == 0

    def test_return_to_simulation(self):
        self.mgr.enter_observe()
        self.mgr.return_to_simulation("fallback")
        assert self.mgr.mode == self.HardwareMode.SIMULATION
        assert len(self.mgr.history) == 2

    def test_full_cycle(self):
        self.mgr.enter_observe()
        self.mgr.unlock_commands()
        self.mgr.return_to_simulation()
        assert self.mgr.is_simulation is True
        assert len(self.mgr.history) == 3

    def test_to_dict(self):
        d = self.mgr.to_dict()
        assert d["mode"] == "SIMULATION"
        assert d["is_simulation"] is True
        assert d["can_observe"] is False
        assert d["can_command"] is False
        assert d["transition_count"] == 0

    def test_history_records_transitions(self):
        self.mgr.enter_observe("reason_a")
        self.mgr.unlock_commands("reason_b")
        history = self.mgr.history
        assert history[0].from_mode == self.HardwareMode.SIMULATION
        assert history[0].to_mode == self.HardwareMode.HARDWARE_OBSERVE
        assert history[0].reason == "reason_a"
        assert history[1].from_mode == self.HardwareMode.HARDWARE_OBSERVE
        assert history[1].to_mode == self.HardwareMode.HARDWARE_COMMAND_LOCKED
        assert history[1].reason == "reason_b"


class TestPreflightChecker:
    def setup_method(self):
        from vantaflight.runtime.preflight import PreflightChecker, CheckSeverity
        self.checker = PreflightChecker()
        self.CheckSeverity = CheckSeverity

    def test_all_pass(self):
        report = self.checker.check(
            connected=True,
            armed=False,
            battery_pct=80.0,
            adapter_name="mock",
            is_simulated=True,
            camera_available=True,
            gps_fix=False,
        )
        assert report.all_passed is True
        assert report.critical_failures == 0
        assert report.warnings == 0

    def test_no_connection_critical(self):
        report = self.checker.check(connected=False)
        assert report.all_passed is False
        assert report.critical_failures >= 1
        conn = next(c for c in report.checks if c.name == "connection")
        assert conn.severity == self.CheckSeverity.CRITICAL
        assert conn.passed is False

    def test_low_battery_warning(self):
        report = self.checker.check(
            connected=True, battery_pct=15.0, adapter_name="mock"
        )
        batt = next(c for c in report.checks if c.name == "battery")
        assert batt.severity == self.CheckSeverity.WARNING
        assert batt.passed is False

    def test_critical_battery(self):
        report = self.checker.check(
            connected=True, battery_pct=5.0, adapter_name="mock"
        )
        batt = next(c for c in report.checks if c.name == "battery")
        assert batt.severity == self.CheckSeverity.CRITICAL

    def test_no_adapter_critical(self):
        report = self.checker.check(connected=True, adapter_name=None)
        adapter = next(c for c in report.checks if c.name == "adapter")
        assert adapter.severity == self.CheckSeverity.CRITICAL
        assert adapter.passed is False

    def test_gps_check_only_hardware(self):
        sim_report = self.checker.check(
            connected=True, is_simulated=True, adapter_name="mock"
        )
        gps_checks = [c for c in sim_report.checks if c.name == "gps"]
        assert len(gps_checks) == 0

        hw_report = self.checker.check(
            connected=True, is_simulated=False, gps_fix=False, adapter_name="mock"
        )
        gps_checks = [c for c in hw_report.checks if c.name == "gps"]
        assert len(gps_checks) == 1
        assert gps_checks[0].severity == self.CheckSeverity.WARNING

    def test_armed_warning(self):
        report = self.checker.check(
            connected=True, armed=True, adapter_name="mock", battery_pct=80.0
        )
        armed = next(c for c in report.checks if c.name == "disarmed")
        assert armed.severity == self.CheckSeverity.WARNING

    def test_to_dict(self):
        report = self.checker.check(connected=True, adapter_name="mock")
        d = report.to_dict()
        assert "timestamp" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)
        assert "all_passed" in d


class TestTrainingModels:
    def test_campaign_config_to_dict(self):
        from vantaflight.training.models import CampaignConfig
        config = CampaignConfig(name="test")
        d = config.to_dict()
        assert d["name"] == "test"
        assert "seed_range" in d

    def test_difficulty_tiers(self):
        from vantaflight.training.models import DifficultyTier
        assert DifficultyTier("BEGINNER") == DifficultyTier.BEGINNER
        assert DifficultyTier("EXPERT") == DifficultyTier.EXPERT
        assert len(DifficultyTier) == 6

    def test_failure_categories(self):
        from vantaflight.training.models import FailureCategory
        assert FailureCategory.TRACKING_LOST.value == "TRACKING_LOST"
        assert FailureCategory.COLLISION.value == "COLLISION"
        assert len(FailureCategory) == 10


class TestCurriculumBuilder:
    def test_auto_curriculum(self):
        from vantaflight.training.curriculum import CurriculumBuilder
        config = CurriculumBuilder().auto_curriculum(seeds_per_tier=2, gate_counts_per_tier=1)
        assert "curriculum" in config.name.lower()
        assert len(config.difficulty_tiers) > 0

    def test_fault_profiles_available(self):
        from vantaflight.training.curriculum import FAULT_PROFILES
        assert "clean" in FAULT_PROFILES
        assert "adversarial_full" in FAULT_PROFILES
        assert len(FAULT_PROFILES) == 8


class TestTrainingEngine:
    def test_create_campaign(self):
        from vantaflight.training.engine import TrainingEngine
        from vantaflight.training.models import CampaignConfig
        engine = TrainingEngine()
        cid = engine.create_campaign(CampaignConfig(name="test"))
        campaigns = engine.list_campaigns()
        assert len(campaigns) == 1
        assert campaigns[0]["name"] == "test"

    def test_start_and_pause(self):
        from vantaflight.training.engine import TrainingEngine
        from vantaflight.training.models import CampaignConfig
        engine = TrainingEngine()
        cid = engine.create_campaign(CampaignConfig(name="lifecycle"))
        engine.start_campaign(cid)
        engine.pause_campaign(cid)
        campaigns = engine.list_campaigns()
        assert campaigns[0]["state"] == "PAUSED"

    def test_cancel(self):
        from vantaflight.training.engine import TrainingEngine
        from vantaflight.training.models import CampaignConfig
        engine = TrainingEngine()
        cid = engine.create_campaign(CampaignConfig(name="cancel-test"))
        engine.start_campaign(cid)
        engine.cancel_campaign(cid)
        campaigns = engine.list_campaigns()
        assert campaigns[0]["state"] == "CANCELLED"


class TestTrainingAnalysis:
    def _make_result(self, failures, complete=False, gates_passed=0, race_time=10.0):
        from vantaflight.training.models import RunConfig, RunResult
        config = RunConfig(
            course_mode="RANDOM", seed=1, gate_count=8,
            difficulty_tier="MODERATE", fault_profiles=[], max_time_s=300.0,
        )
        return RunResult(
            run_id="test-run",
            campaign_id="test-campaign",
            config=config,
            complete=complete,
            gates_passed=gates_passed,
            total_gates=8,
            race_time_s=race_time,
            failures=failures,
        )

    def test_classify_failure_timeout(self):
        from vantaflight.training.analysis import classify_failure
        from vantaflight.training.models import FailureCategory
        result = self._make_result(["timeout exceeded"])
        cats = classify_failure(result)
        assert FailureCategory.TIMEOUT in cats

    def test_classify_failure_collision(self):
        from vantaflight.training.analysis import classify_failure
        from vantaflight.training.models import FailureCategory
        result = self._make_result(["collision detected"])
        cats = classify_failure(result)
        assert FailureCategory.COLLISION in cats

    def test_classify_failure_tracking_lost(self):
        from vantaflight.training.analysis import classify_failure
        from vantaflight.training.models import FailureCategory
        result = self._make_result(["track was lost"])
        cats = classify_failure(result)
        assert FailureCategory.TRACKING_LOST in cats

    def test_classify_failure_unknown(self):
        from vantaflight.training.analysis import classify_failure
        from vantaflight.training.models import FailureCategory
        result = self._make_result(["something random"])
        cats = classify_failure(result)
        assert FailureCategory.UNKNOWN in cats


class TestReplayModels:
    def test_replay_state(self):
        from vantaflight.replay.models import ReplayState
        assert ReplayState.IDLE.value == "IDLE"
        assert ReplayState.PLAYING.value == "PLAYING"

    def test_replay_frame(self):
        from vantaflight.replay.models import ReplayFrame
        frame = ReplayFrame(
            timestamp=1.5,
            frame_type="telemetry",
            data={"x": 0, "y": 0, "z": 1, "altitude": 1.0},
        )
        d = frame.to_dict()
        assert d["timestamp"] == pytest.approx(1.5)
        assert d["frame_type"] == "telemetry"

    def test_replay_timeline_frame_at(self):
        from vantaflight.replay.models import ReplayFrame, ReplayTimeline
        frames = [
            ReplayFrame(timestamp=100.0, frame_type="t", data={"alt": 0}),
            ReplayFrame(timestamp=101.0, frame_type="t", data={"alt": 1}),
            ReplayFrame(timestamp=102.0, frame_type="t", data={"alt": 2}),
        ]
        timeline = ReplayTimeline(
            flight_id=1,
            start_time=100.0,
            end_time=102.0,
            duration_s=2.0,
            total_frames=3,
            frames=frames,
        )
        f = timeline.frame_at(0.5)
        assert f is not None
        assert f.timestamp == 100.0

        f2 = timeline.frame_at(1.5)
        assert f2 is not None
        assert f2.timestamp == 101.0

    def test_replay_timeline_frames_between(self):
        from vantaflight.replay.models import ReplayFrame, ReplayTimeline
        frames = [
            ReplayFrame(timestamp=100.0 + i, frame_type="t", data={"i": i})
            for i in range(5)
        ]
        timeline = ReplayTimeline(
            flight_id=1, start_time=100.0, end_time=104.0,
            duration_s=4.0, total_frames=5, frames=frames,
        )
        between = timeline.frames_between(1.0, 3.0)
        assert len(between) == 3
        assert between[0].timestamp == 101.0
        assert between[-1].timestamp == 103.0


class TestReplayPlayer:
    def _make_timeline(self, n=5):
        from vantaflight.replay.models import ReplayFrame, ReplayTimeline
        frames = [
            ReplayFrame(timestamp=100.0 + float(i), frame_type="telemetry", data={"i": i})
            for i in range(n)
        ]
        return ReplayTimeline(
            flight_id=1, start_time=100.0, end_time=100.0 + n - 1,
            duration_s=float(n - 1), total_frames=n, frames=frames,
        )

    def test_player_lifecycle(self):
        from vantaflight.replay.models import ReplayState
        from vantaflight.replay.player import ReplayPlayer

        player = ReplayPlayer()
        assert player.state == ReplayState.IDLE

        player.load(self._make_timeline())
        assert player.state == ReplayState.READY

        player.play()
        assert player.state == ReplayState.PLAYING

        player.pause()
        assert player.state == ReplayState.PAUSED

        player.stop()
        assert player.state == ReplayState.READY

    def test_seek(self):
        from vantaflight.replay.player import ReplayPlayer

        player = ReplayPlayer()
        player.load(self._make_timeline(10))
        player.seek(5.0)
        d = player.to_dict()
        assert d["cursor_s"] == pytest.approx(5.0, abs=0.1)

    def test_speed_bounds(self):
        from vantaflight.replay.player import ReplayPlayer

        player = ReplayPlayer()
        player.load(self._make_timeline(1))
        player.set_speed(0.01)
        assert player._playback_speed >= 0.1
        player.set_speed(100.0)
        assert player._playback_speed <= 16.0
