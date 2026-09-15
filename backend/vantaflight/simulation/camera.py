"""Simulated camera source — renders synthetic gate views for SITL testing."""
from __future__ import annotations

import asyncio
import math
import time

import cv2
import numpy as np

from ..vision.capture import BaseCameraSource
from ..vision.concepts import CameraProfile, FramePacket
from .models import GazeboGate, SimulationWorld
from .truth import AircraftTruth
from .faults import FaultInjector


def _default_sim_profile() -> CameraProfile:
    fx, fy = 400.0, 400.0
    cx, cy = 320.0, 240.0
    return CameraProfile(
        fx=fx, fy=fy, cx=cx, cy=cy,
        resolution=(640, 480),
        camera_id="sim_camera",
        fps=30.0,
        calibration_version="simulation",
    )


class SimulatedCameraSource(BaseCameraSource):
    """Renders gate rectangles from simulated aircraft pose.

    Produces synthetic frames that exercise the full vision pipeline
    without requiring Gazebo rendering. Gates are drawn as coloured
    rectangles projected through a pinhole camera model from the
    current aircraft truth state.

    This is intentionally simple: no textures, no lighting model,
    no background clutter. Its purpose is pipeline integration testing,
    not photorealism — domain randomisation and adversarial difficulty
    belong in the training/fault-injection layers.
    """

    def __init__(
        self,
        world: SimulationWorld,
        *,
        profile: CameraProfile | None = None,
        source_id: str = "sim_camera",
        fps: float = 30.0,
        background_color: tuple[int, int, int] = (40, 42, 54),
        fault_injector: FaultInjector | None = None,
        clock: type[float] | None = None,
    ) -> None:
        self.source_id = source_id
        self._world = world
        self._profile = profile or _default_sim_profile()
        self._fps = fps
        self._period = 1.0 / fps
        self._bg_color = background_color
        self._fault_injector = fault_injector
        self._clock = time.monotonic
        self._aircraft: AircraftTruth | None = None
        self._running = False
        self._sequence = 0
        self._width = self._profile.resolution[0] if self._profile.resolution else 640
        self._height = self._profile.resolution[1] if self._profile.resolution else 480

    def set_aircraft_state(self, state: AircraftTruth) -> None:
        self._aircraft = state

    async def start(self) -> None:
        self._running = True
        self._sequence = 0

    async def read(self) -> FramePacket | None:
        if not self._running:
            return None

        if self._fault_injector and self._fault_injector.should_disconnect_camera():
            await asyncio.sleep(self._period)
            return None

        if self._fault_injector and self._fault_injector.should_drop_frame():
            await asyncio.sleep(self._period)
            return None

        delay = 0.0
        if self._fault_injector:
            delay = self._fault_injector.camera_delay_s()

        if delay > 0:
            await asyncio.sleep(delay)

        frame = self._render()

        if self._fault_injector:
            frame = self._fault_injector.apply_to_frame(frame)

        capture_ts = self._clock()
        packet = FramePacket(
            frame, capture_ts, self._sequence, self.source_id,
            frame_id=f"{self.source_id}:{self._sequence}",
            receive_timestamp=self._clock(),
            camera_profile=self._profile,
        )
        self._sequence += 1
        await asyncio.sleep(self._period)
        return packet

    async def stop(self) -> None:
        self._running = False

    def _render(self) -> np.ndarray:
        frame = np.full((self._height, self._width, 3), self._bg_color, dtype=np.uint8)

        if self._aircraft is None:
            return frame

        cam_pos = self._aircraft.position
        yaw = math.radians(self._aircraft.yaw_deg)
        pitch = math.radians(self._aircraft.pitch_deg)

        R_yaw = np.array([
            [math.cos(yaw), -math.sin(yaw), 0],
            [math.sin(yaw), math.cos(yaw), 0],
            [0, 0, 1],
        ])
        R_pitch = np.array([
            [math.cos(pitch), 0, math.sin(pitch)],
            [0, 1, 0],
            [-math.sin(pitch), 0, math.cos(pitch)],
        ])
        R_world_to_body = (R_yaw @ R_pitch).T

        R_body_to_cam = np.array([
            [0, -1, 0],
            [0, 0, -1],
            [1, 0, 0],
        ], dtype=np.float64)

        R = R_body_to_cam @ R_world_to_body
        K = self._profile.matrix

        for gate in self._world.gates:
            corners = self._gate_corners(gate)
            projected = self._project_points(corners, cam_pos, R, K)
            if projected is not None:
                self._draw_gate(frame, projected, gate.color_bgr)

        return frame

    @staticmethod
    def _gate_corners(gate: GazeboGate) -> np.ndarray:
        normal = gate.normal / max(float(np.linalg.norm(gate.normal)), 1e-12)
        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(normal, world_up)
        right_norm = float(np.linalg.norm(right))
        if right_norm < 1e-9:
            right = np.array([1.0, 0.0, 0.0])
        else:
            right = right / right_norm
        up = np.cross(right, normal)

        hw, hh = gate.width / 2.0, gate.height / 2.0
        return np.array([
            gate.position + right * hw + up * hh,
            gate.position - right * hw + up * hh,
            gate.position - right * hw - up * hh,
            gate.position + right * hw - up * hh,
        ])

    def _project_points(
        self, points_world: np.ndarray, cam_pos: np.ndarray,
        R: np.ndarray, K: np.ndarray,
    ) -> np.ndarray | None:
        relative = points_world - cam_pos
        cam_coords = (R @ relative.T).T

        if np.all(cam_coords[:, 2] <= 0):
            return None

        valid = cam_coords[:, 2] > 0.01
        if not np.any(valid):
            return None

        cam_coords[:, 2] = np.maximum(cam_coords[:, 2], 0.01)

        px = K[0, 0] * cam_coords[:, 0] / cam_coords[:, 2] + K[0, 2]
        py = K[1, 1] * cam_coords[:, 1] / cam_coords[:, 2] + K[1, 2]

        return np.column_stack((px, py)).astype(np.int32)

    @staticmethod
    def _draw_gate(
        frame: np.ndarray, corners: np.ndarray,
        color_bgr: tuple[int, int, int],
    ) -> None:
        h, w = frame.shape[:2]
        corners = np.clip(corners, [-w, -h], [2 * w, 2 * h])
        for i in range(4):
            pt1 = tuple(corners[i])
            pt2 = tuple(corners[(i + 1) % 4])
            cv2.line(frame, pt1, pt2, color_bgr, 2, cv2.LINE_AA)
