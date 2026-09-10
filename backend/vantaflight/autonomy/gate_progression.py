"""Gate crossing detection and course progression tracking."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..simulation.truth import SimulationTruth


@dataclass(frozen=True)
class GatePassEvent:
    gate_id: str
    pass_time: float
    gate_order: int
    total_gates: int
    race_time_s: float


class GateProgressionTracker:
    """Tracks gate crossings and maintains course progression state.

    Wraps SimulationTruth.check_gate_crossing() and keeps an ordered
    history of gate pass events for the current race.
    """

    def __init__(self, total_gates: int = 0) -> None:
        self._total_gates = total_gates
        self._passed: list[GatePassEvent] = []
        self._current_gate_index = 0
        self._race_start_time: float | None = None

    @property
    def gates_passed(self) -> int:
        return len(self._passed)

    @property
    def total_gates(self) -> int:
        return self._total_gates

    @property
    def is_complete(self) -> bool:
        return self._total_gates > 0 and self.gates_passed >= self._total_gates

    @property
    def current_gate_index(self) -> int:
        return self._current_gate_index

    @property
    def history(self) -> list[GatePassEvent]:
        return list(self._passed)

    @property
    def race_time_s(self) -> float:
        if self._race_start_time is None:
            return 0.0
        return time.monotonic() - self._race_start_time

    def start_race(self, total_gates: int) -> None:
        self._total_gates = total_gates
        self._passed.clear()
        self._current_gate_index = 0
        self._race_start_time = time.monotonic()

    def check_crossing(
        self,
        truth: SimulationTruth,
        aircraft_pos: np.ndarray,
        aircraft_prev_pos: np.ndarray,
        aircraft_radius: float = 0.3,
    ) -> GatePassEvent | None:
        gate_id = truth.check_gate_crossing(
            aircraft_pos, aircraft_prev_pos, aircraft_radius,
        )
        if gate_id is None:
            return None

        event = GatePassEvent(
            gate_id=gate_id,
            pass_time=time.monotonic(),
            gate_order=self._current_gate_index,
            total_gates=self._total_gates,
            race_time_s=self.race_time_s,
        )
        self._passed.append(event)
        self._current_gate_index += 1
        return event

    def reset(self) -> None:
        self._passed.clear()
        self._current_gate_index = 0
        self._race_start_time = None

    def to_dict(self) -> dict:
        return {
            "gates_passed": self.gates_passed,
            "total_gates": self._total_gates,
            "current_gate_index": self._current_gate_index,
            "is_complete": self.is_complete,
            "race_time_s": round(self.race_time_s, 3),
            "history": [
                {
                    "gate_id": e.gate_id,
                    "gate_order": e.gate_order,
                    "race_time_s": round(e.race_time_s, 3),
                }
                for e in self._passed
            ],
        }
