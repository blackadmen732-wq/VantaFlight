"""Hardware mode state machine for simulation→hardware transitions."""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass


class HardwareMode(str, enum.Enum):
    SIMULATION = "SIMULATION"
    HARDWARE_OBSERVE = "HARDWARE_OBSERVE"
    HARDWARE_COMMAND_LOCKED = "HARDWARE_COMMAND_LOCKED"


_TRANSITIONS: dict[HardwareMode, set[HardwareMode]] = {
    HardwareMode.SIMULATION: {HardwareMode.HARDWARE_OBSERVE},
    HardwareMode.HARDWARE_OBSERVE: {HardwareMode.SIMULATION, HardwareMode.HARDWARE_COMMAND_LOCKED},
    HardwareMode.HARDWARE_COMMAND_LOCKED: {HardwareMode.HARDWARE_OBSERVE, HardwareMode.SIMULATION},
}


@dataclass
class ModeTransition:
    from_mode: HardwareMode
    to_mode: HardwareMode
    timestamp: float
    reason: str


class HardwareModeManager:
    """Enforces a strict state machine for simulation/hardware transitions.

    SIMULATION → HARDWARE_OBSERVE → HARDWARE_COMMAND_LOCKED
    Each transition back is also allowed for safe fallback.
    """

    def __init__(self) -> None:
        self._mode = HardwareMode.SIMULATION
        self._history: list[ModeTransition] = []

    @property
    def mode(self) -> HardwareMode:
        return self._mode

    @property
    def is_simulation(self) -> bool:
        return self._mode == HardwareMode.SIMULATION

    @property
    def can_observe(self) -> bool:
        return self._mode in (HardwareMode.HARDWARE_OBSERVE, HardwareMode.HARDWARE_COMMAND_LOCKED)

    @property
    def can_command(self) -> bool:
        return self._mode == HardwareMode.HARDWARE_COMMAND_LOCKED

    @property
    def history(self) -> list[ModeTransition]:
        return list(self._history)

    def transition(self, target: HardwareMode, reason: str = "") -> None:
        if target == self._mode:
            return
        allowed = _TRANSITIONS.get(self._mode, set())
        if target not in allowed:
            raise RuntimeError(
                f"cannot transition from {self._mode.value} to {target.value}; "
                f"allowed: {sorted(m.value for m in allowed)}"
            )
        transition = ModeTransition(
            from_mode=self._mode,
            to_mode=target,
            timestamp=time.monotonic(),
            reason=reason,
        )
        self._history.append(transition)
        self._mode = target

    def enter_observe(self, reason: str = "manual") -> None:
        self.transition(HardwareMode.HARDWARE_OBSERVE, reason)

    def unlock_commands(self, reason: str = "manual") -> None:
        self.transition(HardwareMode.HARDWARE_COMMAND_LOCKED, reason)

    def return_to_simulation(self, reason: str = "manual") -> None:
        self.transition(HardwareMode.SIMULATION, reason)

    def to_dict(self) -> dict:
        return {
            "mode": self._mode.value,
            "is_simulation": self.is_simulation,
            "can_observe": self.can_observe,
            "can_command": self.can_command,
            "transition_count": len(self._history),
        }
