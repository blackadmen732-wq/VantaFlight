"""Gate perception for the race loop: VantaSight → VantaState → gate targets.

Two modes:

* ``vision`` (default): a synthetic forward camera is rendered from the
  aircraft's *real* pose (PX4's EKF in SITL), run through the full VantaSight
  pipeline (detection, PnP pose, tracking, fusion), associated with the
  course's expected gates, and filtered by VantaState's target tracker. Only
  gates the camera actually sees reach the planner.
* ``scene``: the course map is handed to the planner directly (a baseline to
  separate perception problems from planning/control problems).

Fault hooks let tests and the SITL harness rehearse vision blackouts,
low-confidence detections, and an occluded gate.
"""
from __future__ import annotations

import enum
import math
import time
from dataclasses import dataclass, field

import numpy as np

from ..frames import body_to_world_transform, enu_to_ned, enu_yaw_deg_to_ned, ned_to_enu
from ..models import LocalState, Telemetry
from ..racing.models import GateTarget
from ..simulation.camera import SimulatedCameraSource
from ..simulation.models import GazeboGate
from ..simulation.truth import AircraftTruth
from ..state import VantaStateEstimator
from ..state.estimator import TargetObservation
from ..vision.concepts import AircraftState as VisionAircraftState
from ..vision.concepts import CameraProfile, FramePacket, TargetProfile
from ..vision.pipeline import VisionPipeline
from ..vision.transforms import camera_to_world, default_optical_to_frd
from .courses import GATE_COLORS, RaceCourse


class PerceptionMode(str, enum.Enum):
    VISION = "vision"
    SCENE = "scene"


class PerceptionStatus(str, enum.Enum):
    TRACKING = "TRACKING"  # the current gate has a fresh observation
    DEGRADED = "DEGRADED"  # current gate known but its observation is ageing
    #: Too close to see the whole gate: flying through on the last estimate.
    COMMITTED = "COMMITTED"
    LOST = "LOST"  # current gate not seen recently
    BLACKOUT = "BLACKOUT"  # no frames are arriving (camera fault)


#: Planner horizon: CURRENT, NEXT, FUTURE.
HORIZON = 3


def race_camera_profile() -> CameraProfile:
    """640x480 forward camera, ~77° horizontal FOV, mounted along the nose."""
    return CameraProfile(
        fx=400.0, fy=400.0, cx=320.0, cy=240.0,
        resolution=(640, 480),
        camera_id="vantasight_race",
        fps=30.0,
        calibration_version="simulation",
        camera_to_body=default_optical_to_frd(),
    )


def gate_profiles(size_m: float) -> list[TargetProfile]:
    """One square-gate profile per course colour."""
    return [
        TargetProfile(
            f"race_gate_{name}",
            hsv_lower=lower,
            hsv_upper=upper,
            physical_width_m=size_m,
            physical_height_m=size_m,
            min_area_px=40.0,
        )
        for name, _bgr, lower, upper in GATE_COLORS
    ]


@dataclass
class _Faults:
    blackout_until: float = 0.0
    confidence_scale: float = 1.0
    confidence_until: float = 0.0
    hidden: dict[str, float] = field(default_factory=dict)

    def active(self, now: float) -> dict:
        return {
            "vision_blackout_s": round(max(0.0, self.blackout_until - now), 2),
            "confidence_scale": self.scale(now),
            "occluded_gates": sorted(g for g, until in self.hidden.items() if until > now),
        }

    def scale(self, now: float) -> float:
        return self.confidence_scale if now < self.confidence_until else 1.0

    def hidden_ids(self, now: float) -> frozenset[str]:
        return frozenset(g for g, until in self.hidden.items() if until > now)


@dataclass(frozen=True)
class GateObservationRecord:
    gate_id: str
    position_enu: tuple[float, float, float]
    confidence: float
    error_m: float  # vs the course map (the truth for virtual gates)
    distance_m: float

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "position_enu": [round(v, 3) for v in self.position_enu],
            "confidence": round(self.confidence, 3),
            "error_m": round(self.error_m, 3),
            "distance_m": round(self.distance_m, 2),
        }


@dataclass
class PerceptionResult:
    timestamp: float
    status: PerceptionStatus
    targets: list[GateTarget]
    frame_processed: bool = False
    detections: int = 0
    observations: list[GateObservationRecord] = field(default_factory=list)
    processing_ms: float = 0.0
    current_gate_age_s: float | None = None

    @property
    def current_confidence(self) -> float:
        return self.targets[0].confidence if self.targets else 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "status": self.status.value,
            "frame_processed": self.frame_processed,
            "detections": self.detections,
            "observations": [o.to_dict() for o in self.observations],
            "targets": [
                {
                    "gate_id": t.target_id,
                    "slot": t.slot.value,
                    "confidence": round(t.confidence, 3),
                    "uncertainty": round(t.uncertainty, 3),
                    "position_enu": [round(float(v), 3) for v in t.position],
                }
                for t in self.targets
            ],
            "current_confidence": round(self.current_confidence, 3),
            "current_gate_age_s": (
                None if self.current_gate_age_s is None else round(self.current_gate_age_s, 3)
            ),
            "processing_ms": round(self.processing_ms, 2),
        }


class GatePerception:
    def __init__(
        self,
        course: RaceCourse,
        mode: PerceptionMode = PerceptionMode.VISION,
        *,
        clock=time.monotonic,
        vision_hz: float = 15.0,
        target_timeout_s: float = 1.0,
        fresh_s: float = 0.35,
        association_radius_m: float = 4.0,
        commit_radius_m: float = 7.0,
        commit_max_s: float = 8.0,
        commit_confidence: float = 0.6,
        lookahead_min_confidence: float = 0.4,
        lookahead_max_uncertainty_m: float = 1.0,
    ) -> None:
        self.course = course
        self.mode = PerceptionMode(mode)
        self._clock = clock
        self._gates: dict[str, GazeboGate] = {g.gate_id: g for g in course.ordered_gates}
        self._vision_period = 1.0 / vision_hz
        self._fresh_s = fresh_s
        self._association_radius = association_radius_m
        self._estimator = VantaStateEstimator(alpha=0.55, beta=0.0, target_timeout_s=target_timeout_s)
        self._target_timeout = target_timeout_s
        self._faults = _Faults()
        self._last_frame_at = -math.inf
        self._sequence = 0
        self.frames_processed = 0
        self.observation_count = 0
        self._last_observed: dict[str, float] = {}
        # Last good estimate per gate, for the commit zone.
        self._memory: dict[str, tuple[np.ndarray, float, float, float]] = {}
        self._commit_radius = commit_radius_m
        self._commit_max_s = commit_max_s
        self._commit_confidence = commit_confidence
        self._lookahead_min_conf = lookahead_min_confidence
        self._lookahead_max_unc = lookahead_max_uncertainty_m
        self.profile = race_camera_profile()
        if self.mode == PerceptionMode.VISION:
            size = min(min(g.width, g.height) for g in course.gates)
            self._camera = SimulatedCameraSource(course.world(), profile=self.profile)
            self._pipeline = VisionPipeline(self.profile, gate_profiles(size), clock=clock)
        else:
            self._camera = None
            self._pipeline = None

    # -- faults ---------------------------------------------------------------
    def inject_blackout(self, duration_s: float) -> None:
        self._faults.blackout_until = self._clock() + duration_s

    def inject_low_confidence(self, scale: float, duration_s: float) -> None:
        if not 0.0 <= scale <= 1.0:
            raise ValueError("confidence scale must be between 0 and 1")
        self._faults.confidence_scale = scale
        self._faults.confidence_until = self._clock() + duration_s

    def inject_occlusion(self, gate_id: str, duration_s: float) -> None:
        if gate_id not in self._gates:
            raise ValueError(f"unknown gate '{gate_id}'")
        self._faults.hidden[gate_id] = self._clock() + duration_s

    def faults(self) -> dict:
        return self._faults.active(self._clock())

    # -- the update -------------------------------------------------------------
    async def update(self, local: LocalState, remaining_gate_ids: list[str]) -> PerceptionResult:
        now = self._clock()
        horizon = [gid for gid in remaining_gate_ids[:HORIZON] if gid in self._gates]
        blackout = now < self._faults.blackout_until
        scale = self._faults.scale(now)
        hidden = self._faults.hidden_ids(now)

        if self.mode == PerceptionMode.SCENE:
            return self._scene_result(now, horizon, blackout, scale, hidden)

        result = PerceptionResult(now, PerceptionStatus.LOST, [])
        observations: list[TargetObservation] = []
        if not blackout and now - self._last_frame_at >= self._vision_period:
            started = time.perf_counter()
            observations, records, detections = await self._see(local, now, horizon, hidden, scale)
            result.processing_ms = (time.perf_counter() - started) * 1000.0
            result.frame_processed = True
            result.detections = detections
            result.observations = records
            self._last_frame_at = now
            self.frames_processed += 1
            self.observation_count += len(records)

        states = {s.target_id: s for s in self._estimator.update(Telemetry(connected=True), observations, now=now).targets}
        for gid, state in states.items():
            if state.age_s <= self._fresh_s:
                self._memory[gid] = (
                    np.asarray(state.position_world, dtype=np.float64),
                    float(state.confidence), float(state.position_uncertainty_m), now,
                )
        targets: list[GateTarget] = []
        committed = False
        for slot_index, gid in enumerate(horizon):
            state = states.get(gid)
            gate = self._gates[gid]
            if state is not None and slot_index > 0 and (
                state.confidence < self._lookahead_min_conf
                or state.position_uncertainty_m > self._lookahead_max_unc
            ):
                # Look-ahead gates only shape the exit; a poorly estimated far
                # gate must not drag the whole plan's confidence down.
                break
            if state is not None:
                position, confidence, uncertainty = state.position_world, state.confidence, state.position_uncertainty_m
            elif slot_index == 0 and self._can_commit(gid, local, now):
                position, _conf, uncertainty, _seen = self._memory[gid]
                confidence = self._commit_confidence * self._faults.scale(now)
                committed = True
            else:
                break  # never hand the planner a gate the camera has not seen
            targets.append(
                GateTarget(
                    position=position,
                    normal=gate.normal,
                    clearance=min(gate.width, gate.height) / 2.0,
                    confidence=float(np.clip(confidence, 0.0, 1.0)),
                    uncertainty=float(uncertainty),
                    slot=("CURRENT", "NEXT", "FUTURE")[slot_index],
                    target_id=gid,
                )
            )
        result.targets = targets
        current_age = states[horizon[0]].age_s if horizon and horizon[0] in states else None
        result.current_gate_age_s = current_age
        if committed:
            result.status = PerceptionStatus.COMMITTED
        elif blackout:
            result.status = PerceptionStatus.BLACKOUT
        elif current_age is None:
            result.status = PerceptionStatus.LOST
        elif current_age <= self._fresh_s:
            result.status = PerceptionStatus.TRACKING
        else:
            result.status = PerceptionStatus.DEGRADED
        return result

    def _can_commit(self, gid: str, local: LocalState, now: float) -> bool:
        """Close enough, still in front, and seen recently enough to fly through."""
        memory = self._memory.get(gid)
        if memory is None:
            return False
        position, _conf, _unc, seen = memory
        if now - seen > self._commit_max_s:
            return False
        aircraft = np.asarray(local.position_enu, dtype=np.float64)
        if float(np.linalg.norm(position - aircraft)) > self._commit_radius:
            return False
        normal = self._gates[gid].normal
        return float(np.dot(aircraft - position, normal)) < 0.5

    def _scene_result(self, now, horizon, blackout, scale, hidden) -> PerceptionResult:
        targets: list[GateTarget] = []
        if not blackout:
            for slot_index, gid in enumerate(horizon):
                if gid in hidden:
                    break
                gate = self._gates[gid]
                targets.append(
                    GateTarget(
                        position=gate.position,
                        normal=gate.normal,
                        clearance=min(gate.width, gate.height) / 2.0,
                        confidence=scale,
                        uncertainty=0.0,
                        slot=("CURRENT", "NEXT", "FUTURE")[slot_index],
                        target_id=gid,
                    )
                )
        if blackout:
            status = PerceptionStatus.BLACKOUT
        elif targets:
            status = PerceptionStatus.TRACKING
        else:
            status = PerceptionStatus.LOST
        return PerceptionResult(now, status, targets, current_gate_age_s=0.0 if targets else None)

    async def _see(self, local, now, horizon, hidden, scale):
        assert self._camera is not None and self._pipeline is not None
        position = np.asarray(local.position_enu, dtype=np.float64)
        truth = AircraftTruth(
            timestamp=now,
            position=position,
            velocity=np.asarray(local.velocity_enu, dtype=np.float64),
            yaw_deg=local.yaw_enu_deg,
            # The renderer's pitch is positive nose-down; ours is nose-up.
            pitch_deg=-local.pitch_deg,
        )
        image = self._camera.render(truth, hidden)
        frame = FramePacket(
            image, now, self._sequence, "vantasight_race",
            frame_id=f"race:{self._sequence}",
            receive_timestamp=now,
            camera_profile=self.profile,
        )
        self._sequence += 1
        aircraft = VisionAircraftState(
            now,
            position_ned_m=enu_to_ned(position),
            velocity_ned_mps=enu_to_ned(local.velocity_enu),
            body_to_world=body_to_world_transform(enu_yaw_deg_to_ned(local.yaw_enu_deg), local.pitch_deg, 0.0),
        )
        vision = await self._pipeline.process(frame, aircraft)

        best: dict[str, tuple[float, np.ndarray, float, float]] = {}
        for candidate in vision.candidates:
            pose = vision.poses.get(candidate.candidate_id)
            if pose is None:
                continue
            world_enu = ned_to_enu(camera_to_world(pose.translation_vector, self.profile, aircraft))
            distance = float(np.linalg.norm(pose.translation_vector))
            confidence = float(np.clip(candidate.score * math.exp(-pose.reprojection_error_px / 3.0) * scale, 0.0, 1.0))
            gid, error = self._associate(world_enu, distance, horizon)
            if gid is None:
                continue
            if gid not in best or confidence > best[gid][0]:
                best[gid] = (confidence, world_enu, error, distance)

        observations: list[TargetObservation] = []
        records: list[GateObservationRecord] = []
        for gid, (confidence, world_enu, error, distance) in best.items():
            # PnP range error grows roughly with distance squared (measured on
            # the synthetic camera: ~0.2 m at 12 m, ~1 m at 23 m, ~2 m at 34 m).
            uncertainty = 0.05 + 0.0016 * distance * distance
            observations.append(
                TargetObservation(gid, tuple(float(v) for v in world_enu), now, confidence, uncertainty)
            )
            records.append(
                GateObservationRecord(gid, tuple(float(v) for v in world_enu), confidence, error, distance)
            )
            self._last_observed[gid] = now
        return observations, records, len(vision.candidates)

    def _associate(self, world_enu: np.ndarray, distance: float, horizon: list[str]) -> tuple[str | None, float]:
        """Nearest expected gate within a distance-scaled radius."""
        radius = max(self._association_radius, 0.15 * distance)
        best_id, best_err = None, math.inf
        for gid in horizon:
            err = float(np.linalg.norm(world_enu - self._gates[gid].position))
            if err < best_err:
                best_id, best_err = gid, err
        if best_id is None or best_err > radius:
            return None, best_err
        return best_id, best_err

    def status(self) -> dict:
        return {
            "mode": self.mode.value,
            "frames_processed": self.frames_processed,
            "observations": self.observation_count,
            "faults": self.faults(),
        }
