"""Precision visual-servo and terminal maneuver controllers."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class PrecisionCommand:
    vx_mps: float
    vy_mps: float
    vz_mps: float
    yaw_rate_dps: float = 0.0
    confidence: float = 1.0
    complete: bool = False


class IBVSController:
    """Bounded image-based visual servo controller.

    Inputs are normalized image errors where +x means target appears right and
    +y means target appears below image center. It only commands descent when
    the target is centered and confidence is high enough.
    """

    def __init__(
        self,
        *,
        lateral_gain: float = 0.8,
        max_lateral_mps: float = 0.6,
        descent_mps: float = 0.25,
        center_tolerance: float = 0.05,
        min_confidence: float = 0.65,
        target_range_m: float = 0.12,
    ) -> None:
        if lateral_gain <= 0 or max_lateral_mps <= 0 or descent_mps <= 0:
            raise ValueError("precision gains/speeds must be positive")
        if not 0 < center_tolerance < 1 or not 0 <= min_confidence <= 1:
            raise ValueError("invalid precision thresholds")
        self.lateral_gain = lateral_gain
        self.max_lateral_mps = max_lateral_mps
        self.descent_mps = descent_mps
        self.center_tolerance = center_tolerance
        self.min_confidence = min_confidence
        self.target_range_m = target_range_m

    def command(
        self,
        error_x: float,
        error_y: float,
        *,
        confidence: float,
        range_m: float | None = None,
    ) -> PrecisionCommand:
        values = [error_x, error_y, confidence]
        if range_m is not None:
            values.append(range_m)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("IBVS inputs must be finite")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        if confidence < self.min_confidence:
            return PrecisionCommand(0.0, 0.0, 0.0, confidence=confidence)

        vx = float(np.clip(-self.lateral_gain * error_y, -self.max_lateral_mps, self.max_lateral_mps))
        vy = float(np.clip(self.lateral_gain * error_x, -self.max_lateral_mps, self.max_lateral_mps))
        centered = math.hypot(error_x, error_y) <= self.center_tolerance
        complete = bool(centered and range_m is not None and range_m <= self.target_range_m)
        vz = 0.0 if complete else (self.descent_mps if centered else 0.0)
        return PrecisionCommand(vx, vy, vz, confidence=confidence, complete=complete)


@dataclass(frozen=True)
class PortalGeometry:
    center: tuple[float, float, float]
    normal: tuple[float, float, float]
    width_m: float
    height_m: float
    confidence: float

    def __post_init__(self) -> None:
        n = np.asarray(self.normal, dtype=float)
        if n.shape != (3,) or not np.all(np.isfinite(n)) or np.linalg.norm(n) < 1e-9:
            raise ValueError("portal normal must be a finite nonzero 3-vector")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("portal dimensions must be positive")
        if not 0 <= self.confidence <= 1:
            raise ValueError("portal confidence out of range")


class PortalTraversalController:
    """Creates deterministic align/entry/exit geometry for a rectangular portal."""

    def __init__(self, *, vehicle_radius_m: float = 0.13, margin_m: float = 0.08) -> None:
        if vehicle_radius_m <= 0 or margin_m < 0:
            raise ValueError("invalid portal clearance configuration")
        self.vehicle_radius_m = vehicle_radius_m
        self.margin_m = margin_m

    def safe_points(
        self,
        portal: PortalGeometry,
        *,
        approach_distance_m: float = 0.7,
        exit_distance_m: float = 0.5,
        min_confidence: float = 0.7,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
        required = 2.0 * (self.vehicle_radius_m + self.margin_m)
        if portal.width_m <= required or portal.height_m <= required:
            raise ValueError("portal is too small for configured vehicle clearance")
        if portal.confidence < min_confidence:
            raise ValueError("portal confidence too low to commit")
        center = np.asarray(portal.center, dtype=float)
        normal = np.asarray(portal.normal, dtype=float)
        normal /= np.linalg.norm(normal)
        align = center - normal * approach_distance_m
        entry = center - normal * self.margin_m
        exit_point = center + normal * exit_distance_m
        return tuple(align), tuple(entry), tuple(exit_point)


class HookAlignmentController:
    """Converts a payload contact-point error into a bounded body translation.

    `hook_offset_body` describes the hook contact point relative to the drone
    center. The target point is controlled, not the center of the aircraft.
    """

    def __init__(self, hook_offset_body: tuple[float, float, float], *, gain: float = 1.0, max_speed_mps: float = 0.35) -> None:
        if gain <= 0 or max_speed_mps <= 0:
            raise ValueError("hook controller limits must be positive")
        self.hook_offset_body = np.asarray(hook_offset_body, dtype=float)
        if self.hook_offset_body.shape != (3,) or not np.all(np.isfinite(self.hook_offset_body)):
            raise ValueError("hook offset must be a finite XYZ vector")
        self.gain = gain
        self.max_speed_mps = max_speed_mps

    def command(
        self,
        aircraft_position_world: tuple[float, float, float],
        target_position_world: tuple[float, float, float],
        *,
        heading_deg: float = 0.0,
        tolerance_m: float = 0.03,
        confidence: float = 1.0,
    ) -> PrecisionCommand:
        if confidence < 0 or confidence > 1:
            raise ValueError("confidence out of range")
        theta = math.radians(heading_deg)
        c, s = math.cos(theta), math.sin(theta)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        contact = np.asarray(aircraft_position_world, dtype=float) + rotation @ self.hook_offset_body
        error = np.asarray(target_position_world, dtype=float) - contact
        distance = float(np.linalg.norm(error))
        if distance <= tolerance_m:
            return PrecisionCommand(0.0, 0.0, 0.0, confidence=confidence, complete=True)
        velocity = error * self.gain
        speed = float(np.linalg.norm(velocity))
        if speed > self.max_speed_mps:
            velocity *= self.max_speed_mps / speed
        return PrecisionCommand(float(velocity[0]), float(velocity[1]), float(velocity[2]), confidence=confidence)
