"""Race courses for the VantaRace closed loop.

A course is an ordered list of gates in local ENU metres relative to home
(x east, y north, z up). In PX4 SITL the gates are *virtual*: they exist in
VantaFlight's course frame, are rendered by the synthetic VantaSight camera
from the aircraft's real (PX4-simulated) pose, and are checked for crossing
against PX4's own position estimate. They are not Gazebo models.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..simulation.models import GazeboGate, SimulationWorld

#: The synthetic camera's horizontal half field of view is ~38°. A leg that
#: turns more sharply than this would put the next gate out of view.
MAX_TURN_DEG = 55.0
MIN_GATE_SPACING_M = 6.0
MIN_GATE_ALTITUDE_M = 2.0
MIN_GATE_SIZE_M = 1.0

#: Gate colours cycle so neighbouring gates never merge into one blob in the
#: image (BGR, with the HSV hue band VantaSight uses to find each one).
GATE_COLORS: tuple[tuple[str, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]], ...] = (
    ("orange", (0, 128, 255), (5, 150, 150), (25, 255, 255)),
    ("magenta", (255, 0, 255), (140, 150, 150), (160, 255, 255)),
    ("green", (0, 200, 0), (50, 150, 120), (70, 255, 255)),
)


@dataclass
class RaceCourse:
    course_id: str
    name: str
    gates: list[GazeboGate]
    description: str = ""
    #: Altitude the aircraft climbs to before the loop starts.
    start_altitude_m: float = 5.0
    max_time_s: float = 180.0
    tags: list[str] = field(default_factory=list)

    @property
    def ordered_gates(self) -> list[GazeboGate]:
        return sorted(self.gates, key=lambda g: g.order)

    def world(self) -> SimulationWorld:
        return SimulationWorld(
            course_id=self.course_id,
            gates=self.ordered_gates,
            start_position=np.array([0.0, 0.0, self.start_altitude_m]),
        )

    def length_m(self) -> float:
        points = [np.array([0.0, 0.0, self.start_altitude_m])] + [g.position for g in self.ordered_gates]
        return float(sum(np.linalg.norm(b - a) for a, b in zip(points, points[1:])))

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "name": self.name,
            "description": self.description,
            "start_altitude_m": self.start_altitude_m,
            "max_time_s": self.max_time_s,
            "length_m": round(self.length_m(), 1),
            "gates": [g.to_dict() for g in self.ordered_gates],
            "tags": list(self.tags),
        }


def _gate(gate_id: str, order: int, position, toward, size: float = 3.0) -> GazeboGate:
    """A gate at ``position`` whose normal points along ``toward`` (horizontal)."""
    normal = np.asarray(toward, dtype=np.float64)
    normal = normal / np.linalg.norm(normal)
    return GazeboGate(
        color_bgr=GATE_COLORS[order % len(GATE_COLORS)][1],
        gate_id=gate_id,
        position=np.asarray(position, dtype=np.float64),
        normal=normal,
        width=size,
        height=size,
        yaw_deg=math.degrees(math.atan2(normal[1], normal[0])),
        order=order,
    )


def _through(points: list[tuple[float, float, float]], size: float, prefix: str = "gate") -> list[GazeboGate]:
    """Gates at ``points`` with normals bisecting the incoming/outgoing legs."""
    pts = [np.array([0.0, 0.0, points[0][2]])] + [np.asarray(p, dtype=np.float64) for p in points]
    gates: list[GazeboGate] = []
    for i in range(1, len(pts)):
        incoming = pts[i] - pts[i - 1]
        incoming[2] = 0.0
        incoming /= np.linalg.norm(incoming)
        if i + 1 < len(pts):
            outgoing = pts[i + 1] - pts[i]
            outgoing[2] = 0.0
            outgoing /= np.linalg.norm(outgoing)
            direction = incoming + outgoing
        else:
            direction = incoming
        gates.append(_gate(f"{prefix}_{i}", i - 1, pts[i], direction, size))
    return gates


def preset_courses() -> dict[str, RaceCourse]:
    courses = [
        RaceCourse(
            course_id="sitl_line_3",
            name="Line (3 gates)",
            description="Three large gates in a straight line east of home: the first SITL milestone.",
            gates=_through([(12.0, 0.0, 5.0), (24.0, 0.0, 5.0), (36.0, 0.0, 5.0)], size=3.0),
            start_altitude_m=5.0,
            max_time_s=120.0,
            tags=["beginner", "sitl"],
        ),
        RaceCourse(
            course_id="sitl_slalom_4",
            name="Gentle slalom (4 gates)",
            description="Four gates weaving 4 m either side of a line, with a climb to 7 m.",
            gates=_through(
                [(12.0, 3.0, 5.0), (24.0, -3.0, 6.0), (36.0, 3.0, 7.0), (48.0, -1.0, 6.0)],
                size=3.0,
            ),
            start_altitude_m=5.0,
            max_time_s=150.0,
            tags=["intermediate", "sitl"],
        ),
        RaceCourse(
            course_id="sitl_arc_5",
            name="Arc (5 gates)",
            description="Five gates on a 40 m-radius left-hand arc.",
            gates=_through(
                [
                    (40.0 * math.sin(a), 40.0 - 40.0 * math.cos(a), 5.0)
                    for a in np.radians([18.0, 36.0, 54.0, 72.0, 90.0])
                ],
                size=3.0,
            ),
            start_altitude_m=5.0,
            max_time_s=180.0,
            tags=["intermediate", "sitl"],
        ),
    ]
    return {c.course_id: c for c in courses}


def course_from_spec(spec: dict) -> RaceCourse:
    """Build a custom course from ``{"name", "gates": [{"x","y","z", ...}]}``.

    Gate normals default to bisecting the legs, as in the presets.
    """
    raw = spec.get("gates") or []
    if not raw:
        raise ValueError("a course needs at least one gate")
    size = float(spec.get("gate_size_m", 3.0))
    points = []
    for i, g in enumerate(raw):
        try:
            points.append((float(g["x"]), float(g["y"]), float(g.get("z", 5.0))))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"gate {i + 1} needs numeric x, y (and optional z)") from exc
    gates = _through(points, size=size)
    return RaceCourse(
        course_id=str(spec.get("course_id") or "custom"),
        name=str(spec.get("name") or "Custom course"),
        gates=gates,
        start_altitude_m=float(spec.get("start_altitude_m", points[0][2])),
        max_time_s=float(spec.get("max_time_s", 180.0)),
        tags=["custom"],
    )


@dataclass
class CourseCheck:
    valid: bool
    errors: list[str]
    warnings: list[str]
    length_m: float

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "length_m": round(self.length_m, 1),
        }


def check_course(course: RaceCourse, geofence=None, airspace=None) -> CourseCheck:
    """Static checks before a course may be flown."""
    errors: list[str] = []
    warnings: list[str] = []
    gates = course.ordered_gates
    if not gates:
        errors.append("course has no gates")
        return CourseCheck(False, errors, warnings, 0.0)
    if course.start_altitude_m < MIN_GATE_ALTITUDE_M:
        errors.append(f"start altitude must be at least {MIN_GATE_ALTITUDE_M:g} m")

    prev = np.array([0.0, 0.0, course.start_altitude_m])
    prev_dir: np.ndarray | None = None
    for gate in gates:
        label = gate.gate_id
        if gate.position[2] - gate.height / 2.0 < MIN_GATE_ALTITUDE_M - 1e-9:
            errors.append(f"{label}: bottom edge is below {MIN_GATE_ALTITUDE_M:g} m")
        if min(gate.width, gate.height) < MIN_GATE_SIZE_M:
            errors.append(f"{label}: gates must be at least {MIN_GATE_SIZE_M:g} m across")
        leg = gate.position - prev
        dist = float(np.linalg.norm(leg))
        if dist < MIN_GATE_SPACING_M:
            errors.append(f"{label}: only {dist:.1f} m from the previous gate (min {MIN_GATE_SPACING_M:g} m)")
        else:
            horiz = leg.copy()
            horiz[2] = 0.0
            if np.linalg.norm(horiz) > 1e-6:
                direction = horiz / np.linalg.norm(horiz)
                if prev_dir is not None:
                    turn = math.degrees(math.acos(float(np.clip(np.dot(prev_dir, direction), -1.0, 1.0))))
                    if turn > MAX_TURN_DEG:
                        errors.append(
                            f"{label}: {turn:.0f}° turn; the camera cannot see past {MAX_TURN_DEG:.0f}°"
                        )
                approach = float(np.dot(direction, gate.normal))
                if approach < math.cos(math.radians(MAX_TURN_DEG)):
                    warnings.append(f"{label}: approached at a steep angle to its face")
                prev_dir = direction
        if geofence is not None:
            breach = geofence.violation(float(gate.position[0]), float(gate.position[1]), float(gate.position[2]))
            if breach:
                errors.append(f"{label}: outside the geofence ({breach})")
        if airspace is not None and getattr(airspace, "zones", None):
            for zone in airspace.zones:
                if zone.contains((float(gate.position[0]), float(gate.position[1]))):
                    errors.append(f"{label}: inside no-fly zone '{zone.name}'")
        prev = gate.position
    return CourseCheck(not errors, errors, warnings, course.length_m())
