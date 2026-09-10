"""Simulation truth source — ground-truth gate/aircraft state kept strictly separate from perception."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .models import GazeboGate, SimulationWorld


@dataclass(frozen=True)
class GateTruth:
    gate_id: str
    position: np.ndarray
    normal: np.ndarray
    width: float
    height: float
    passed: bool = False
    pass_time: float | None = None


@dataclass(frozen=True)
class AircraftTruth:
    timestamp: float
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


@dataclass
class TruthSource:
    """Provides ground-truth state from the simulator. Never exposed to perception."""
    gates: dict[str, GateTruth] = field(default_factory=dict)
    aircraft: AircraftTruth | None = None
    gates_passed: int = 0
    total_gates: int = 0
    race_time_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "aircraft": {
                "position": self.aircraft.position.tolist(),
                "velocity": self.aircraft.velocity.tolist(),
                "yaw_deg": self.aircraft.yaw_deg,
            } if self.aircraft else None,
            "gates_passed": self.gates_passed,
            "total_gates": self.total_gates,
            "race_time_s": round(self.race_time_s, 3),
        }


class SimulationTruth:
    """Maintains truth state and compares against perception estimates."""

    def __init__(self, world: SimulationWorld) -> None:
        self._world = world
        self._truth = TruthSource(total_gates=len(world.gates))
        for gate in world.gates:
            self._truth.gates[gate.gate_id] = GateTruth(
                gate_id=gate.gate_id,
                position=gate.position.copy(),
                normal=gate.normal.copy(),
                width=gate.width,
                height=gate.height,
            )
        self._start_time: float | None = None

    @property
    def truth(self) -> TruthSource:
        return self._truth

    def update_aircraft(self, state: AircraftTruth) -> None:
        self._truth.aircraft = state
        if self._start_time is None:
            self._start_time = state.timestamp
        self._truth.race_time_s = state.timestamp - self._start_time

    def check_gate_crossing(
        self,
        aircraft_pos: np.ndarray,
        aircraft_prev_pos: np.ndarray,
        aircraft_radius: float = 0.3,
    ) -> str | None:
        """Return gate_id if the aircraft crossed through a gate, else None."""
        for gid, gt in self._truth.gates.items():
            if gt.passed:
                continue
            d_curr = float(np.dot(aircraft_pos - gt.position, gt.normal))
            d_prev = float(np.dot(aircraft_prev_pos - gt.position, gt.normal))
            if d_prev <= 0 and d_curr > 0:
                cross_t = -d_prev / max(d_curr - d_prev, 1e-9)
                cross_point = aircraft_prev_pos + cross_t * (aircraft_pos - aircraft_prev_pos)
                offset = cross_point - gt.position
                lateral = float(np.linalg.norm(offset - np.dot(offset, gt.normal) * gt.normal))
                if lateral <= max(gt.width, gt.height) / 2.0 + aircraft_radius:
                    self._truth.gates[gid] = GateTruth(
                        gate_id=gid,
                        position=gt.position,
                        normal=gt.normal,
                        width=gt.width,
                        height=gt.height,
                        passed=True,
                        pass_time=self._truth.race_time_s,
                    )
                    self._truth.gates_passed += 1
                    return gid
        return None

    def compare_perception(
        self,
        estimated_positions: dict[str, np.ndarray],
    ) -> dict[str, dict]:
        """Compare vision-estimated gate positions against truth."""
        comparison: dict[str, dict] = {}
        for gid, est_pos in estimated_positions.items():
            gt = self._truth.gates.get(gid)
            if gt is None:
                continue
            error = float(np.linalg.norm(est_pos - gt.position))
            comparison[gid] = {
                "truth_position": gt.position.tolist(),
                "estimated_position": est_pos.tolist(),
                "error_m": round(error, 4),
                "passed": gt.passed,
            }
        return comparison

    def race_summary(self) -> dict:
        return {
            "gates_passed": self._truth.gates_passed,
            "total_gates": self._truth.total_gates,
            "race_time_s": round(self._truth.race_time_s, 3),
            "complete": self._truth.gates_passed == self._truth.total_gates,
            "gate_pass_times": {
                gid: gt.pass_time
                for gid, gt in self._truth.gates.items()
                if gt.passed and gt.pass_time is not None
            },
        }
