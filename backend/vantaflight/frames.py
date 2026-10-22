"""Frame conventions for the autonomy loop, in one place.

VantaFlight's mission/racing world is **local ENU** metres relative to home:
``x`` east, ``y`` north, ``z`` up, with yaw measured counter-clockwise from
east (radians in planner types, degrees in ``AircraftTruth``).

PX4 offboard and VantaSight's world frame are **local NED**: ``x`` north,
``y`` east, ``z`` down, with yaw (heading) measured clockwise from north.

ENU → NED swaps x/y and negates z; it is a proper rotation, so the same
function converts positions, velocities, and accelerations both ways.
"""
from __future__ import annotations

import math

import numpy as np


def enu_to_ned(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float64).reshape(3)
    return np.array([v[1], v[0], -v[2]], dtype=np.float64)


def ned_to_enu(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float64).reshape(3)
    return np.array([v[1], v[0], -v[2]], dtype=np.float64)


def wrap_deg(angle: float) -> float:
    """Wrap to (-180, 180]."""
    wrapped = math.fmod(float(angle) + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def enu_yaw_deg_to_ned(yaw_enu_deg: float) -> float:
    """CCW-from-east → clockwise-from-north (compass heading), wrapped."""
    return wrap_deg(90.0 - float(yaw_enu_deg))


def ned_yaw_deg_to_enu(yaw_ned_deg: float) -> float:
    return wrap_deg(90.0 - float(yaw_ned_deg))


def frd_to_ned_rotation(yaw_ned_deg: float, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> np.ndarray:
    """3x3 rotation from aircraft FRD body axes to world NED (Z-Y-X Euler).

    Uses the aerospace convention: positive pitch is nose *up*.
    """
    psi, theta, phi = (math.radians(a) for a in (yaw_ned_deg, pitch_deg, roll_deg))
    cps, sps = math.cos(psi), math.sin(psi)
    cth, sth = math.cos(theta), math.sin(theta)
    cph, sph = math.cos(phi), math.sin(phi)
    return np.array(
        [
            [cth * cps, sph * sth * cps - cph * sps, cph * sth * cps + sph * sps],
            [cth * sps, sph * sth * sps + cph * cps, cph * sth * sps - sph * cps],
            [-sth, sph * cth, cph * cth],
        ],
        dtype=np.float64,
    )


def body_to_world_transform(yaw_ned_deg: float, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> np.ndarray:
    """4x4 homogeneous FRD→NED rotation for ``vision.concepts.AircraftState``."""
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = frd_to_ned_rotation(yaw_ned_deg, pitch_deg, roll_deg)
    return transform
