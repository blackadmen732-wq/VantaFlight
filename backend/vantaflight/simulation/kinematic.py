"""Fast Lab kinematic driver — deterministic aircraft motion integration.

Lightweight kinematic model for training and testing. Accepts desired
velocity setpoints (what PX4 would achieve), integrates position/velocity
with dt, and returns new state. No motor mixing, attitude control, or
actuator output — those remain PX4's domain. This models the *result* of
PX4 executing velocity commands, not the control loop itself.
"""
from __future__ import annotations

import numpy as np

from .truth import AircraftTruth


class KinematicDriver:
    """Deterministic first-order kinematic aircraft model.

    Given a desired velocity vector, applies acceleration limits to
    smoothly track it and integrates position. Fully deterministic:
    identical inputs produce identical outputs regardless of wall clock.
    """

    def __init__(
        self,
        max_speed: float = 5.0,
        max_accel: float = 3.0,
    ) -> None:
        self._max_speed = max_speed
        self._max_accel = max_accel

    @property
    def max_speed(self) -> float:
        return self._max_speed

    def step(
        self,
        pos: np.ndarray,
        vel: np.ndarray,
        desired_vel: np.ndarray,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Integrate one kinematic step with acceleration limiting.

        Returns (new_pos, new_vel).
        """
        if dt <= 0:
            return pos.copy(), vel.copy()

        delta_v = desired_vel - vel
        delta_v_mag = float(np.linalg.norm(delta_v))
        max_dv = self._max_accel * dt
        if delta_v_mag > max_dv:
            delta_v = delta_v * (max_dv / delta_v_mag)

        new_vel = vel + delta_v
        speed = float(np.linalg.norm(new_vel))
        if speed > self._max_speed:
            new_vel = new_vel * (self._max_speed / speed)

        new_pos = pos + new_vel * dt
        return new_pos, new_vel

    def navigate_toward(
        self,
        pos: np.ndarray,
        target: np.ndarray,
        cruise_speed: float,
    ) -> np.ndarray:
        """Compute desired velocity vector toward a target position.

        Uses proportional approach: full cruise speed when far,
        decelerating smoothly when close to avoid overshoot.
        """
        diff = target - pos
        dist = float(np.linalg.norm(diff))
        if dist < 1e-9:
            return np.zeros(3)
        direction = diff / dist
        decel_dist = cruise_speed ** 2 / (2.0 * self._max_accel)
        if dist < decel_dist:
            speed = cruise_speed * (dist / decel_dist)
            speed = max(speed, 0.3)
        else:
            speed = cruise_speed
        return direction * speed

    def build_truth(
        self,
        timestamp: float,
        pos: np.ndarray,
        vel: np.ndarray,
        prev_vel: np.ndarray,
        dt: float,
    ) -> AircraftTruth:
        """Build an AircraftTruth snapshot from current kinematic state."""
        accel = (vel - prev_vel) / dt if dt > 0 else np.zeros(3)
        speed = float(np.linalg.norm(vel))
        if speed > 1e-9:
            yaw_deg = float(np.degrees(np.arctan2(vel[1], vel[0])))
            pitch_deg = float(np.degrees(np.arcsin(
                np.clip(vel[2] / speed, -1.0, 1.0)
            )))
        else:
            yaw_deg = 0.0
            pitch_deg = 0.0
        return AircraftTruth(
            timestamp=timestamp,
            position=pos.copy(),
            velocity=vel.copy(),
            acceleration=accel,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
        )
