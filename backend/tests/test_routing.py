"""Routing engine: geometry, safe shortest paths, visiting order, battery budget."""
from __future__ import annotations

import itertools
import math
import random

import pytest

from vantaflight.mission import FinishAction, MissionPlan, Waypoint, check_plan
from vantaflight.routing import (
    Airspace,
    RouteError,
    Zone,
    circle_polygon,
    plan_route,
    solve,
    zone_from_spec,
)
from vantaflight.routing.geometry import point_in_polygon, segments_intersect
from vantaflight.routing.tour import held_karp, improve, nearest_neighbour, tour_length
from vantaflight.safety import Geofence

SQUARE = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]


def _square_airspace(margin=2.0, **kw) -> Airspace:
    return Airspace([Zone("z1", "Block", list(SQUARE))], margin_m=margin, **kw)


def _matrix(points):
    return [[math.dist(a, b) for b in points] for a in points]


def _min_clearance(path, zone: Zone) -> float:
    from vantaflight.routing.geometry import segment_segment_distance

    return min(
        segment_segment_distance(a, b, p, q)
        for a, b in zip(path, path[1:])
        for p, q in zone.edges
    )


# -- geometry -------------------------------------------------------------------
def test_segment_intersection_cases():
    assert segments_intersect((0, 0), (2, 2), (0, 2), (2, 0))  # cross
    assert not segments_intersect((0, 0), (1, 1), (2, 2), (3, 3))  # collinear, apart
    assert segments_intersect((0, 0), (2, 0), (1, 0), (3, 0))  # collinear overlap
    assert segments_intersect((0, 0), (1, 0), (1, 0), (1, 1))  # touching end


def test_point_in_polygon_including_concave():
    l_shape = [(0, 0), (20, 0), (20, 5), (5, 5), (5, 20), (0, 20)]
    assert point_in_polygon((2, 2), l_shape)
    assert point_in_polygon((2, 18), l_shape)
    assert not point_in_polygon((10, 10), l_shape)  # in the notch
    assert point_in_polygon((0, 10), l_shape)  # on the boundary counts as inside


def test_zone_validation():
    with pytest.raises(ValueError, match="at least 3"):
        Zone("z", "bad", [(0, 0), (1, 1)])
    with pytest.raises(ValueError, match="crosses itself"):
        Zone("z", "bowtie", [(0, 0), (10, 10), (10, 0), (0, 10)])
    from vantaflight.routing.geometry import signed_area

    clockwise = Zone("z", "cw", list(reversed(SQUARE)))
    assert signed_area(clockwise.vertices) > 0  # normalised to counter-clockwise


def test_circle_polygon_contains_the_circle():
    poly = circle_polygon(5, 5, 10, sides=12)
    for k in range(36):
        a = 2 * math.pi * k / 36
        assert point_in_polygon((5 + 10 * math.cos(a), 5 + 10 * math.sin(a)), poly)


def test_zone_specs():
    assert len(zone_from_spec({"center": [0, 0], "radius": 5}).vertices) == 12
    assert zone_from_spec({"name": "Tri", "vertices": [[0, 0], [5, 0], [0, 5]]}).name == "Tri"
    with pytest.raises(ValueError):
        zone_from_spec({"center": [0, 0], "radius": -1})


# -- shortest safe paths ----------------------------------------------------------
def test_clear_path_is_a_straight_line():
    path = _square_airspace().shortest_path((-30, 30), (30, 30))
    assert path.points == [(-30, 30), (30, 30)]
    assert path.length_m == pytest.approx(60)


def test_detour_around_square_is_near_the_exact_optimum():
    airspace = _square_airspace(margin=2.0)
    path = airspace.shortest_path((-30, 0), (30, 0))
    # Exact optimum with 2 m clearance: tangent to the 2 m circles around two
    # corners, plus the 20 m straight along the side, plus the arcs.
    r, corner, start = 2.0, (-10.0, -10.0), (-30.0, 0.0)
    d = math.dist(start, corner)
    tangent = math.sqrt(d * d - r * r)
    to_corner = math.atan2(corner[1] - start[1], corner[0] - start[0])
    tangent_dir = to_corner - math.asin(r / d)
    arc = (0.0 - tangent_dir) * r  # turn until heading east along the side
    exact = 2 * (tangent + arc) + 20.0
    assert exact <= path.length_m <= exact * 1.03
    zone = airspace.zones[0]
    assert _min_clearance(path.points, zone) >= 2.0 - 1e-6


def test_path_through_gap_between_zones():
    zones = [
        Zone("a", "West", [(-30, -5), (-2, -5), (-2, 5), (-30, 5)]),
        Zone("b", "East", [(2, -5), (30, -5), (30, 5), (2, 5)]),
    ]
    wide_gap = Airspace(zones, margin_m=1.0).shortest_path((0, -20), (0, 20))
    assert wide_gap.points == [(0, -20), (0, 20)]  # 4 m gap fits a 1 m margin
    narrow = Airspace(zones, margin_m=3.0).shortest_path((0, -20), (0, 20))
    assert narrow.detours  # 4 m gap does not fit 3 m on each side: go round
    assert narrow.length_m > 40


def test_concave_zone_is_navigated():
    u_shape = Zone("u", "U", [(-20, -20), (20, -20), (20, 20), (10, 20), (10, -10), (-10, -10), (-10, 20), (-20, 20)])
    airspace = Airspace([u_shape], margin_m=1.0)
    path = airspace.shortest_path((0, 0), (0, 40))  # start inside the U's mouth
    assert path.points[0] == (0, 0) and path.points[-1] == (0, 40)
    for a, b in zip(path.points, path.points[1:]):
        assert airspace.blocking_zone(a, b, margin=0.0) is None


def test_endpoint_inside_zone_is_an_error():
    with pytest.raises(RouteError, match="inside no-fly zone 'Block'"):
        _square_airspace().shortest_path((0, 0), (40, 0))


def test_unreachable_goal_is_an_error():
    ring = [
        Zone("n", "N", [(-30, 20), (30, 20), (30, 30), (-30, 30)]),
        Zone("s", "S", [(-30, -30), (30, -30), (30, -20), (-30, -20)]),
        Zone("w", "W", [(-30, -20), (-20, -20), (-20, 20), (-30, 20)]),
        Zone("e", "E", [(20, -20), (30, -20), (30, 20), (20, 20)]),
    ]
    with pytest.raises(RouteError, match="no safe route"):
        Airspace(ring, margin_m=1.0, geofence=Geofence(radius_m=500)).shortest_path((0, 0), (60, 0))


def test_paths_stay_inside_the_geofence():
    airspace = Airspace([Zone("z", "Wall", [(-5, -200), (5, -200), (5, 45), (-5, 45)])],
                        margin_m=1.0, geofence=Geofence(radius_m=60))
    path = airspace.shortest_path((-20, 0), (20, 0))
    assert all(math.hypot(*p) <= 60 for p in path.points)


# -- visiting order ---------------------------------------------------------------
@pytest.mark.parametrize("return_home", [True, False])
def test_held_karp_matches_brute_force(return_home):
    rng = random.Random(7)
    for _ in range(15):
        pts = [(0, 0)] + [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(7)]
        d = _matrix(pts)
        brute = min(tour_length(d, list(p), return_home) for p in itertools.permutations(range(1, 8)))
        assert tour_length(d, held_karp(d, return_home), return_home) == pytest.approx(brute)


def test_heuristic_is_close_to_optimal_and_beats_nearest_neighbour():
    import vantaflight.routing.tour as tour_module

    rng = random.Random(11)
    gaps, nn_gaps = [], []
    for _ in range(8):
        pts = [(0, 0)] + [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(11)]
        d = _matrix(pts)
        exact = tour_length(d, held_karp(d, True), True)
        original = tour_module.EXACT_LIMIT
        tour_module.EXACT_LIMIT = 0  # force the heuristic
        try:
            heuristic = solve(d, True)
        finally:
            tour_module.EXACT_LIMIT = original
        assert heuristic.method == "heuristic"
        gaps.append(heuristic.length_m / exact - 1)
        nn_gaps.append(tour_length(d, nearest_neighbour(d), True) / exact - 1)
    assert max(gaps) < 0.03
    assert sum(gaps) < sum(nn_gaps)


def test_solve_reports_method_and_is_deterministic():
    rng = random.Random(3)
    pts = [(0, 0)] + [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(20)]
    d = _matrix(pts)
    a, b = solve(d), solve(d)
    assert a.method == "heuristic" and a.order == b.order
    assert sorted(a.order) == list(range(1, 21))
    assert solve(_matrix(pts[:6])).method == "exact"


def test_improve_never_makes_a_tour_worse():
    rng = random.Random(5)
    pts = [(0, 0)] + [(rng.uniform(-50, 50), rng.uniform(-50, 50)) for _ in range(15)]
    d = _matrix(pts)
    start = list(range(1, 16))
    assert tour_length(d, improve(d, start, False), False) <= tour_length(d, start, False)


# -- route planner ----------------------------------------------------------------
def _stops(*points, altitude=10.0):
    return [Waypoint(x=x, y=y, altitude=altitude) for x, y in points]


def test_route_reorders_stops_and_reports_savings():
    stops = _stops((40, 0), (-40, 0), (45, 5), (-45, 5))  # zig-zag as given
    route = plan_route(stops, Airspace())
    assert route.method == "exact"
    assert route.distance_m < route.given_order_m
    assert route.saved_m == pytest.approx(route.given_order_m - route.distance_m)
    assert sorted(route.order) == [0, 1, 2, 3]
    assert {(w.x, w.y) for w in route.plan.waypoints} == {(s.x, s.y) for s in stops}


def test_route_keeps_order_when_asked():
    stops = _stops((40, 0), (-40, 0), (45, 5))
    route = plan_route(stops, Airspace(), optimize_order=False)
    assert route.order == [0, 1, 2] and route.method == "fixed"


def test_route_inserts_detours_and_passes_plan_checks():
    airspace = Airspace([zone_from_spec({"name": "Stadium", "center": [30, 0], "radius": 10})])
    stops = _stops((60, 0), (60, 30))
    route = plan_route(stops, airspace, optimize_order=False)
    assert route.detour_points >= 1
    assert all(w.kind == "via" for w in route.plan.waypoints if (w.x, w.y) not in {(60, 0), (60, 30)})
    report = check_plan(route.plan, airspace=airspace)
    assert report.valid, report.errors


def test_straight_plan_through_a_zone_is_rejected_with_a_hint():
    airspace = Airspace([zone_from_spec({"name": "Stadium", "center": [30, 0], "radius": 10})])
    plan = MissionPlan(waypoints=_stops((60, 0)), finish=FinishAction.LAND)
    report = check_plan(plan, airspace=airspace)
    assert not report.valid
    assert "crosses no-fly zone 'Stadium'" in report.errors[0]
    assert "Optimize route" in report.errors[0]


def test_route_home_around_zone_lands_at_home():
    airspace = Airspace([zone_from_spec({"name": "Stadium", "center": [30, 0], "radius": 10})])
    route = plan_route(_stops((60, 0)), airspace)
    last = route.plan.waypoints[-1]
    assert (last.x, last.y) == (0.0, 0.0)
    assert route.plan.finish == FinishAction.LAND
    assert check_plan(route.plan, airspace=airspace).valid


def test_point_of_no_return_is_found():
    stops = _stops((100, 0), (100, 100), (0, 100), altitude=20)
    healthy = plan_route(stops, Airspace(), battery_pct=100, speed_m_s=2)
    assert healthy.feasible
    tight = plan_route(stops, Airspace(), battery_pct=40, speed_m_s=2)
    assert not tight.feasible
    assert tight.point_of_no_return is not None
    assert "point of no return" in tight.warnings[0]
    margins = [b.margin_pct for b in tight.budgets]
    assert margins == sorted(margins, reverse=True)  # margin only shrinks along the route


def test_budget_assumes_full_battery_when_unknown():
    route = plan_route(_stops((10, 0)), Airspace())
    assert route.battery_start_pct == 100
    assert any("battery level unknown" in w for w in route.warnings)


def test_route_rejects_bad_input():
    with pytest.raises(ValueError, match="at least one stop"):
        plan_route([], Airspace())
    with pytest.raises(ValueError, match="geofence"):
        plan_route(_stops((1000, 0)), Airspace())
    with pytest.raises(RouteError):
        plan_route(_stops((0, 0)), _square_airspace())  # stop inside a zone


def test_many_stops_with_zones_is_fast():
    import time

    rng = random.Random(1)
    airspace = Airspace([
        zone_from_spec({"name": f"Z{i}", "center": [cx, cy], "radius": 8})
        for i, (cx, cy) in enumerate([(30, 30), (-30, 30), (30, -30), (-30, -30), (0, 60)])
    ])
    stops = []
    while len(stops) < 30:
        p = (rng.uniform(-90, 90), rng.uniform(-90, 90))
        if airspace.zone_at(p) is None and math.hypot(*p) < 140:
            stops.append(Waypoint(x=p[0], y=p[1], altitude=12))
    t0 = time.monotonic()
    route = plan_route(stops, airspace)
    assert time.monotonic() - t0 < 10
    assert check_plan(route.plan, airspace=airspace).valid


def test_zero_margin_still_blocks_a_crossing():
    zone = Zone("z", "Block", list(SQUARE))
    assert zone.blocks((-30, 0), (30, 0), margin=0.0)
    assert not zone.blocks((-30, 11), (30, 11), margin=0.0)
    path = Airspace([zone], margin_m=0.0).shortest_path((-30, 0), (30, 0))
    assert path.detours
