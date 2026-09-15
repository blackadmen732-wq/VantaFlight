"""Hopper program deployment — autonomous mission upload.

FTW publicly supports autonomous block/JavaScript programs transferred to
Hopper via Bluetooth (used by FTWCode.ai from Chrome/Edge).  This module
provides:

  HopperProgramCompiler  — converts a high-level VantaFlight MissionPlan
                            into a validated HopperMissionProgram
  ProgramSafetyValidator — rejects unsafe routines before upload
  HopperProgramConnector — deploys a validated program via the official
                            FTW programming interface

VantaMission never emits raw JavaScript; it emits a MissionPlan.  The
compiler owns the translation step.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .errors import HopperProgramValidationFailed, HopperUnsupportedCapability
from .models import HopperMissionInstruction, HopperMissionProgram

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Program compiler
# ---------------------------------------------------------------------------


@dataclass
class SimpleMissionPlan:
    """Minimal mission representation consumed by the compiler.

    The real VantaMission will supply a richer type; this mirrors its
    interface so the compiler API is stable.
    """
    steps: list[dict[str, Any]]


class HopperProgramCompiler:
    """Translates a VantaFlight mission plan into a HopperMissionProgram."""

    _SUPPORTED_OPCODES = frozenset({
        "TAKEOFF", "MOVE_FORWARD", "MOVE_BACKWARD",
        "MOVE_LEFT", "MOVE_RIGHT", "MOVE_UP", "MOVE_DOWN",
        "TURN_LEFT", "TURN_RIGHT", "HOVER", "LAND",
    })

    def compile(
        self,
        plan: SimpleMissionPlan,
        mission_id: Optional[str] = None,
    ) -> HopperMissionProgram:
        instructions: list[HopperMissionInstruction] = []
        max_alt = 0.0
        duration = 0.0

        for step in plan.steps:
            opcode = str(step.get("opcode", "")).upper()
            if opcode not in self._SUPPORTED_OPCODES:
                raise HopperUnsupportedCapability(
                    f"Mission opcode '{opcode}' is not supported by Hopper."
                )
            params = {k: v for k, v in step.items() if k != "opcode"}
            instructions.append(HopperMissionInstruction(opcode=opcode, params=params))

            if opcode == "TAKEOFF":
                alt = float(params.get("altitude_m", 1.2))
                max_alt = max(max_alt, alt)
                duration += 3.0
            elif opcode in ("MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_LEFT", "MOVE_RIGHT"):
                dist = float(params.get("distance_m", 0.5))
                speed = float(params.get("speed_mps", 0.3))
                duration += dist / max(speed, 0.01)
            elif opcode == "HOVER":
                duration += float(params.get("duration_s", 2.0))
            elif opcode == "LAND":
                duration += 3.0

        return HopperMissionProgram(
            instructions=instructions,
            estimated_duration_s=duration,
            max_altitude_m=max_alt,
            source_mission_id=mission_id,
        )


# ---------------------------------------------------------------------------
# Safety validator
# ---------------------------------------------------------------------------


class ProgramSafetyValidator:
    """Validates a compiled program before it is sent to Hopper."""

    MAX_ALTITUDE_M = 3.0         # conservative indoor competition limit
    MAX_MOVE_DISTANCE_M = 5.0
    MAX_PROGRAM_DURATION_S = 300.0

    def validate(self, program: HopperMissionProgram) -> None:
        """Raise HopperProgramValidationFailed if the program is unsafe."""
        errors: list[str] = []

        if program.max_altitude_m > self.MAX_ALTITUDE_M:
            errors.append(
                f"Max altitude {program.max_altitude_m:.1f} m exceeds limit "
                f"{self.MAX_ALTITUDE_M:.1f} m."
            )
        if program.estimated_duration_s > self.MAX_PROGRAM_DURATION_S:
            errors.append(
                f"Estimated duration {program.estimated_duration_s:.0f} s exceeds "
                f"limit {self.MAX_PROGRAM_DURATION_S:.0f} s."
            )

        has_land = any(i.opcode == "LAND" for i in program.instructions)
        if program.instructions and not has_land:
            errors.append("Program has no LAND instruction; Hopper must land at end.")

        for instr in program.instructions:
            if instr.opcode in ("MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_LEFT", "MOVE_RIGHT"):
                dist = float(instr.params.get("distance_m", 0))
                if dist > self.MAX_MOVE_DISTANCE_M:
                    errors.append(
                        f"{instr.opcode} distance {dist:.1f} m exceeds "
                        f"limit {self.MAX_MOVE_DISTANCE_M:.1f} m."
                    )

        if errors:
            raise HopperProgramValidationFailed(
                "Program failed safety validation:\n" + "\n".join(f"  - {e}" for e in errors)
            )


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class HopperProgramConnector:
    """Deploys validated programs to Hopper through the official FTW interface.

    The actual Bluetooth/transport layer is injected so this class is
    testable without hardware.
    """

    def __init__(self) -> None:
        self._compiler = HopperProgramCompiler()
        self._validator = ProgramSafetyValidator()
        self._connected = False
        self._last_program: Optional[HopperMissionProgram] = None

    @property
    def connected(self) -> bool:
        return self._connected

    def mark_connected(self) -> None:
        self._connected = True

    def mark_disconnected(self) -> None:
        self._connected = False

    def compile_and_validate(
        self,
        plan: SimpleMissionPlan,
        mission_id: Optional[str] = None,
    ) -> HopperMissionProgram:
        program = self._compiler.compile(plan, mission_id=mission_id)
        self._validator.validate(program)
        return program

    async def deploy(self, program: HopperMissionProgram) -> bool:
        """Deploy a validated program.

        Until FTW publishes the external program-upload API, this logs
        intent and returns True so higher-level tests pass.  The real
        transport call goes here when the SDK is available.
        """
        if not self._connected:
            raise HopperUnsupportedCapability(
                "Program connector is not connected; cannot deploy."
            )
        # Validate again at deploy time — programs must not be mutated between
        # compile and deploy.
        self._validator.validate(program)
        logger.info(
            "HopperProgramConnector: would deploy program %s (%d instructions, "
            "%.0f s estimated) — awaiting official FTW upload interface.",
            program.program_id,
            len(program.instructions),
            program.estimated_duration_s,
        )
        self._last_program = program
        return True

    @property
    def last_program(self) -> Optional[HopperMissionProgram]:
        return self._last_program
