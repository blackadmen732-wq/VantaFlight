"""Frame- and unit-tagged vectors.

A bare ``[x, y, z]`` says nothing about which way x points. PR #8 found a
real bug of exactly that kind: VantaExecution emitted ENU coordinates in a
structure labelled NED. ``Vec3`` carries its frame and physical quantity, so
adding a NED velocity to an ENU position fails loudly, and every contract
field declares the frame it requires.

Frames (see docs/CONTRACTS.md):

* ``WORLD_ENU`` — VantaFlight's world: local metres from home, x east, y north, z up.
* ``LOCAL_NED`` — PX4 local frame: x north, y east, z down. Assumed to share
  WORLD_ENU's origin (true for SITL; PR D's TransformTree makes it explicit).
* ``BODY_FLU`` / ``BODY_FRD`` — aircraft body axes (forward-left-up / forward-right-down).
* ``CAMERA_*_OPTICAL`` — OpenCV optical axes: +x right, +y down, +z forward.

Units follow from the quantity: positions in metres, velocities in m/s,
accelerations in m/s², angular velocities in rad/s, directions unitless.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Annotated, Iterable

import numpy as np
from pydantic import AfterValidator


class FrameId(str, enum.Enum):
    GEODETIC = "geodetic"
    WORLD_ENU = "world_enu"
    LOCAL_NED = "local_ned"
    BODY_FLU = "body_flu"
    BODY_FRD = "body_frd"
    CAMERA_FRONT_OPTICAL = "camera_front_optical"
    CAMERA_DOWN_OPTICAL = "camera_down_optical"
    IMU = "imu"
    PAYLOAD_HOOK = "payload_hook"
    TARGET = "target"


class Quantity(str, enum.Enum):
    POSITION = "position"
    VELOCITY = "velocity"
    ACCELERATION = "acceleration"
    ANGULAR_VELOCITY = "angular_velocity"
    DIRECTION = "direction"


class Unit(str, enum.Enum):
    METRE = "m"
    METRE_PER_S = "m/s"
    METRE_PER_S2 = "m/s^2"
    RADIAN_PER_S = "rad/s"
    UNITLESS = "1"


QUANTITY_UNIT: dict[Quantity, Unit] = {
    Quantity.POSITION: Unit.METRE,
    Quantity.VELOCITY: Unit.METRE_PER_S,
    Quantity.ACCELERATION: Unit.METRE_PER_S2,
    Quantity.ANGULAR_VELOCITY: Unit.RADIAN_PER_S,
    Quantity.DIRECTION: Unit.UNITLESS,
}


class FrameMismatchError(TypeError):
    """Vectors in different frames (or of different quantities) were combined."""


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float
    y: float
    z: float
    frame: FrameId
    quantity: Quantity

    def __post_init__(self) -> None:
        for name in ("x", "y", "z"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise TypeError(f"{name} must be a number")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "frame", FrameId(self.frame))
        object.__setattr__(self, "quantity", Quantity(self.quantity))

    # -- construction -----------------------------------------------------------
    @classmethod
    def of(cls, values: Iterable[float], frame: FrameId, quantity: Quantity) -> "Vec3":
        x, y, z = (float(v) for v in values)
        return cls(x, y, z, frame, quantity)

    # -- views ----------------------------------------------------------------------
    @property
    def unit(self) -> Unit:
        return QUANTITY_UNIT[self.quantity]

    def as_array(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=np.float64)

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]

    def norm(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    # -- checks & arithmetic ---------------------------------------------------------
    def require(self, frame: FrameId, quantity: Quantity | None = None) -> "Vec3":
        if self.frame is not FrameId(frame) or (quantity is not None and self.quantity is not Quantity(quantity)):
            want = f"{FrameId(frame).value} {Quantity(quantity).value if quantity else ''}".strip()
            raise FrameMismatchError(f"expected {want}, got {self.frame.value} {self.quantity.value}")
        return self

    def _compatible(self, other: object) -> "Vec3":
        if not isinstance(other, Vec3):
            raise TypeError(f"expected a Vec3, got {type(other).__name__}")
        if other.frame is not self.frame or other.quantity is not self.quantity:
            raise FrameMismatchError(
                f"cannot combine {self.frame.value} {self.quantity.value} with "
                f"{other.frame.value} {other.quantity.value}"
            )
        return other

    def __add__(self, other: "Vec3") -> "Vec3":
        other = self._compatible(other)
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z, self.frame, self.quantity)

    def __sub__(self, other: "Vec3") -> "Vec3":
        other = self._compatible(other)
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z, self.frame, self.quantity)

    def scaled(self, k: float) -> "Vec3":
        return Vec3(self.x * k, self.y * k, self.z * k, self.frame, self.quantity)

    def to_dict(self) -> dict:
        return {
            "x": self.x, "y": self.y, "z": self.z,
            "frame": self.frame.value, "quantity": self.quantity.value,
        }


# -- the one conversion that needs no transform tree ------------------------------------
def enu_to_ned(v: Vec3) -> Vec3:
    """WORLD_ENU → LOCAL_NED: swap x/y and negate z (same origin, any quantity)."""
    v.require(FrameId.WORLD_ENU)
    return Vec3(v.y, v.x, -v.z, FrameId.LOCAL_NED, v.quantity)


def ned_to_enu(v: Vec3) -> Vec3:
    v.require(FrameId.LOCAL_NED)
    return Vec3(v.y, v.x, -v.z, FrameId.WORLD_ENU, v.quantity)


def yaw_enu_rad_to_ned(yaw_enu_rad: float) -> float:
    """CCW-from-east radians → clockwise-from-north radians, wrapped to (-π, π]."""
    return _wrap_rad(math.pi / 2.0 - yaw_enu_rad)


def yaw_ned_rad_to_enu(yaw_ned_rad: float) -> float:
    return _wrap_rad(math.pi / 2.0 - yaw_ned_rad)


def _wrap_rad(angle: float) -> float:
    wrapped = math.fmod(angle + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


# -- contract field types -------------------------------------------------------------------
def framed(frame: FrameId, quantity: Quantity):
    """Annotated Vec3 that only validates in ``frame`` as ``quantity``."""

    def check(v: Vec3) -> Vec3:
        try:
            return v.require(frame, quantity)
        except FrameMismatchError as exc:
            # pydantic only collects ValueErrors into a ValidationError.
            raise ValueError(str(exc)) from None

    return Annotated[Vec3, AfterValidator(check)]


PositionENU = framed(FrameId.WORLD_ENU, Quantity.POSITION)
VelocityENU = framed(FrameId.WORLD_ENU, Quantity.VELOCITY)
AccelerationENU = framed(FrameId.WORLD_ENU, Quantity.ACCELERATION)
PositionNED = framed(FrameId.LOCAL_NED, Quantity.POSITION)
VelocityNED = framed(FrameId.LOCAL_NED, Quantity.VELOCITY)
AccelerationNED = framed(FrameId.LOCAL_NED, Quantity.ACCELERATION)


def position_enu(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.WORLD_ENU, Quantity.POSITION)


def velocity_enu(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.WORLD_ENU, Quantity.VELOCITY)


def acceleration_enu(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.WORLD_ENU, Quantity.ACCELERATION)


def position_ned(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.LOCAL_NED, Quantity.POSITION)


def velocity_ned(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.LOCAL_NED, Quantity.VELOCITY)


def acceleration_ned(values: Iterable[float]) -> Vec3:
    return Vec3.of(values, FrameId.LOCAL_NED, Quantity.ACCELERATION)
