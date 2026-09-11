"""Tests for SimulationTruth gate crossing with rectangular bounds."""
from __future__ import annotations

import numpy as np
import pytest

from vantaflight.simulation.truth import GateTruth, SimulationTruth, AircraftTruth
from vantaflight.simulation.models import GazeboGate, SimulationWorld


def _make_world(gates: list[GazeboGate]) -> SimulationWorld:
    return SimulationWorld(course_id="test", gates=gates)


def _gate(
    gate_id: str,
    position: tuple[float, float, float],
    normal: tuple[float, float, float],
    width: float = 2.0,
    height: float = 1.0,
) -> GazeboGate:
    return GazeboGate(
        gate_id=gate_id,
        position=np.array(position, dtype=float),
        normal=np.array(normal, dtype=float) / np.linalg.norm(normal),
        width=width,
        height=height,
    )


class TestRectangularGateCrossing:
    """Verify gate crossing uses rectangular (not circular) bounds and
    reduces usable opening by aircraft_radius rather than enlarging it."""

    def test_center_crossing_passes(self):
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0), width=2.0, height=1.0)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 0.0, 1.0])
        curr = np.array([6.0, 0.0, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.2)
        assert result == "g1"

    def test_wide_gate_narrow_height_rejects_vertical_miss(self):
        """Aircraft crosses through the plane but outside the short dimension."""
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0), width=3.0, height=0.8)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 0.0, 2.0])
        curr = np.array([6.0, 0.0, 2.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.2)
        assert result is None, "Should reject crossing outside gate height"

    def test_narrow_width_rejects_horizontal_miss(self):
        """Aircraft crosses outside the narrow width."""
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0), width=0.8, height=3.0)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 2.0, 1.0])
        curr = np.array([6.0, 2.0, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.2)
        assert result is None, "Should reject crossing outside gate width"

    def test_aircraft_radius_reduces_usable_opening(self):
        """Crossing near edge: passes without radius, fails with body clearance."""
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0), width=2.0, height=2.0)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        offset_y = 0.85
        prev = np.array([4.0, offset_y, 1.0])
        curr = np.array([6.0, offset_y, 1.0])
        small_radius = truth.check_gate_crossing(curr, prev, aircraft_radius=0.1)
        assert small_radius == "g1", "Small drone should pass near edge"

        world2 = _make_world([_gate("g2", (5, 0, 1), (1, 0, 0), width=2.0, height=2.0)])
        truth2 = SimulationTruth(world2)
        truth2.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        large_radius = truth2.check_gate_crossing(curr, prev, aircraft_radius=0.3)
        assert large_radius is None, "Large drone body should clip the frame"

    def test_aircraft_radius_does_not_enlarge_opening(self):
        """Verify that aircraft_radius narrows the usable opening, not widens it.
        An aircraft clearly outside the gate should never pass regardless of radius."""
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0), width=2.0, height=1.0)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        far_outside_y = 3.0
        prev = np.array([4.0, far_outside_y, 1.0])
        curr = np.array([6.0, far_outside_y, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.3)
        assert result is None, "Aircraft far outside gate must not pass"
        result_big = truth.check_gate_crossing(curr, prev, aircraft_radius=5.0)
        assert result_big is None, "Large radius must never enlarge the opening"

    def test_backward_crossing_does_not_count(self):
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0))])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([6, 0, 1.0]), np.zeros(3)))
        prev = np.array([6.0, 0.0, 1.0])
        curr = np.array([4.0, 0.0, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.2)
        assert result is None

    def test_already_passed_gate_skipped(self):
        world = _make_world([_gate("g1", (5, 0, 1), (1, 0, 0))])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 0.0, 1.0])
        curr = np.array([6.0, 0.0, 1.0])
        assert truth.check_gate_crossing(curr, prev) == "g1"
        assert truth.check_gate_crossing(curr, prev) is None

    def test_angled_gate_rectangular_check(self):
        """Gate at 45 degrees — rectangular bounds should still apply per-axis."""
        normal = np.array([1.0, 1.0, 0.0])
        normal = normal / np.linalg.norm(normal)
        world = _make_world([GazeboGate(
            gate_id="angled",
            position=np.array([5.0, 5.0, 1.0]),
            normal=normal,
            width=2.0,
            height=1.0,
        )])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 4, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 4.0, 1.0])
        curr = np.array([6.0, 6.0, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.2)
        assert result == "angled"

    def test_gate_too_small_for_aircraft(self):
        """Gate opening smaller than 2*aircraft_radius should never pass."""
        world = _make_world([_gate("tiny", (5, 0, 1), (1, 0, 0), width=0.3, height=0.3)])
        truth = SimulationTruth(world)
        truth.update_aircraft(AircraftTruth(0.0, np.array([4, 0, 1.0]), np.zeros(3)))
        prev = np.array([4.0, 0.0, 1.0])
        curr = np.array([6.0, 0.0, 1.0])
        result = truth.check_gate_crossing(curr, prev, aircraft_radius=0.25)
        assert result is None, "Gate too small for aircraft body"
