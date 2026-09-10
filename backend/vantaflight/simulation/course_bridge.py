"""Bridge from CourseLab courses to simulation-world gate geometry."""
from __future__ import annotations

import math

import numpy as np

from ..course_lab.models import Course, Gate as LabGate
from .models import GazeboGate, SimulationWorld


class CourseBridge:
    """Converts a CourseLab ``Course`` into a ``SimulationWorld``.

    The bridge maps the abstract gate geometry produced by the procedural
    generator into concrete simulation objects with physical dimensions,
    colours, and ordering that a simulator (or the synthetic camera)
    can consume.
    """

    def __init__(
        self,
        *,
        default_gate_color_bgr: tuple[int, int, int] = (0, 128, 255),
        start_offset_m: float = 3.0,
        start_altitude_m: float = 1.5,
    ) -> None:
        self._default_color = default_gate_color_bgr
        self._start_offset = start_offset_m
        self._start_altitude = start_altitude_m

    def course_to_world(self, course: Course, course_id: str | None = None) -> SimulationWorld:
        cid = course_id or f"{course.mode.value}_{course.seed}"
        gazebo_gates: list[GazeboGate] = []
        for lab_gate in course.gates:
            gazebo_gates.append(self._convert_gate(lab_gate))

        start_pos, start_yaw = self._compute_start(course)

        return SimulationWorld(
            course_id=cid,
            gates=gazebo_gates,
            start_position=start_pos,
            start_yaw_deg=start_yaw,
        )

    def _convert_gate(self, gate: LabGate) -> GazeboGate:
        position = np.array(gate.center, dtype=np.float64)
        normal = np.array(gate.normal, dtype=np.float64)
        norm_len = float(np.linalg.norm(normal))
        if norm_len > 1e-12:
            normal = normal / norm_len

        return GazeboGate(
            gate_id=f"gate_{gate.order:03d}",
            position=position,
            normal=normal,
            width=gate.width,
            height=gate.height,
            yaw_deg=math.degrees(gate.yaw),
            pitch_deg=math.degrees(gate.pitch),
            color_bgr=self._default_color,
            order=gate.order,
        )

    def _compute_start(self, course: Course) -> tuple[np.ndarray, float]:
        if not course.gates:
            return np.array([0.0, 0.0, self._start_altitude]), 0.0

        first = course.gates[0]
        normal = np.array(first.normal, dtype=np.float64)
        center = np.array(first.center, dtype=np.float64)

        start_pos = center - normal * self._start_offset
        start_pos[2] = max(start_pos[2], self._start_altitude)

        yaw_rad = math.atan2(float(normal[1]), float(normal[0]))
        return start_pos, math.degrees(yaw_rad)

    def world_gate_ids(self, world: SimulationWorld) -> list[str]:
        return [g.gate_id for g in sorted(world.gates, key=lambda g: g.order)]
