"""`core/route_plan` — planning a corridor the terrain actually allows.

The property that matters is the one the module exists to guarantee: the route
it hands back **clears the ground on every leg**, not just at every waypoint.
That is the half `daryal_run` shipped wrong, and it is the half a planner can
only get right by measuring the chord between two points rather than the points.

The overlay is a synthetic elevation array with the real transforms on it, so a
test states a landscape in a couple of lines and needs neither a DCS install nor
the built map overlay.
"""

from __future__ import annotations

import numpy as np
import pytest

from dcs_mission_creator.core import route_plan, waypoints
from tests.conftest import RasterOverlay, at

#: 100 m cells over a 20 km square, which is the scale a corridor is planned at.
CELL_M = 100
#: World coordinates of the synthetic raster's north-west corner.
TOP, LEFT = 10_000.0, 0.0


class _Overlay(RasterOverlay):
    """The shared raster stub, at this module's cell size.

    Nothing is added: `route_plan` and `waypoints` ask an overlay only for the
    transform, the point query, one bulk window and line of sight, and all four
    are `conftest`'s — with the same row/col transform the real overlay uses, so
    the bulk window and the point query cannot disagree about which cell a world
    coordinate is in. That is the one way this stub could make a broken planner
    look correct.
    """

    def __init__(self, heights: np.ndarray) -> None:
        super().__init__(heights, cell_m=CELL_M, top=TOP, left=LEFT)


def flat(height: float = 0.0) -> _Overlay:
    return _Overlay(np.full((200, 200), height, dtype=float))


def ridge_with_gap() -> _Overlay:
    """A north-south wall across the map with one low pass through it."""
    heights = np.zeros((200, 200), dtype=float)
    heights[:, 90:95] = 3_000.0  # the wall
    heights[150:160, 90:95] = 400.0  # the pass
    return _Overlay(heights)


def legs_clear(overlay: _Overlay, route: route_plan.PlannedRoute) -> float:
    """The deepest any leg of `route` goes into the ground; 0.0 when it clears."""
    return max(
        (
            waypoints.leg_violation(
                a, b, alt_a, alt_b, overlay, clearance_m=route_plan.CLEARANCE_M
            )[0]
            for a, b, alt_a, alt_b in zip(
                route.points,
                route.points[1:],
                route.altitude_m,
                route.altitude_m[1:],
            )
        ),
        default=0.0,
    )


# -- height bands ------------------------------------------------------------


def test_agl_bands_pick_the_band_for_the_ground_under_the_point():
    height = route_plan.agl_bands(((2_000.0, 900.0), (800.0, 600.0), (0.0, 300.0)))
    assert height(0.0) == 300.0
    assert height(799.0) == 300.0
    assert height(800.0) == 600.0
    assert height(2_500.0) == 900.0


def test_agl_bands_are_ordered_by_the_helper_not_the_caller():
    """Bands given lowest-first mean the same thing as highest-first."""
    ascending = route_plan.agl_bands(((0.0, 300.0), (800.0, 600.0)))
    descending = route_plan.agl_bands(((800.0, 600.0), (0.0, 300.0)))
    assert [ascending(g) for g in (0, 900)] == [descending(g) for g in (0, 900)]


# -- the valley search -------------------------------------------------------


def test_valley_path_goes_through_the_pass_rather_than_over_the_wall():
    overlay = ridge_with_gap()
    start, end = at(-5_500.0, 2_000.0), at(-5_500.0, 12_000.0)
    trace = route_plan.valley_path(overlay, start, end, cell_m=200.0, pad_m=6_000.0)
    crossing = [p for p in trace if 9_000.0 <= p.y <= 9_500.0]
    assert crossing, "the path never crossed the wall"
    assert max(overlay.elevation_at(p) for p in crossing) < 1_000.0


def test_valley_path_keeps_its_endpoints():
    overlay = flat()
    start, end = at(0.0, 1_000.0), at(-2_000.0, 5_000.0)
    trace = route_plan.valley_path(overlay, start, end, cell_m=200.0, pad_m=2_000.0)
    assert (trace[0].x, trace[0].y) == (start.x, start.y)
    assert (trace[-1].x, trace[-1].y) == (end.x, end.y)


# -- choosing waypoints ------------------------------------------------------


def test_flat_ground_needs_no_waypoints_beyond_the_anchors():
    overlay = flat()
    route = route_plan.plan_corridor(
        overlay, [at(0.0, 1_000.0), at(0.0, 8_000.0)], cell_m=200.0, pad_m=1_000.0
    )
    assert len(route.points) == 2
    assert route.worst_lift_m == pytest.approx(0.0)


def test_a_planned_corridor_clears_the_ground_on_every_leg():
    """The guarantee. A route that only clears at its waypoints is the bug."""
    overlay = ridge_with_gap()
    route = route_plan.plan_corridor(
        overlay,
        [at(-5_500.0, 2_000.0), at(-5_500.0, 12_000.0)],
        cell_m=200.0,
        pad_m=6_000.0,
        min_leg_m=200.0,
    )
    assert legs_clear(overlay, route) == pytest.approx(0.0)


def test_terrain_forces_more_waypoints_than_flat_ground_does():
    anchors = [at(-5_500.0, 2_000.0), at(-5_500.0, 12_000.0)]
    over_flat = route_plan.plan_corridor(
        flat(), anchors, cell_m=200.0, pad_m=6_000.0, min_leg_m=200.0
    )
    over_ridge = route_plan.plan_corridor(
        ridge_with_gap(), anchors, cell_m=200.0, pad_m=6_000.0, min_leg_m=200.0
    )
    assert len(over_ridge.points) > len(over_flat.points)


def test_raising_the_height_bands_buys_back_waypoints():
    """The trade the module exists to expose, as a property rather than a story."""
    overlay = ridge_with_gap()
    anchors = [at(-5_500.0, 2_000.0), at(-5_500.0, 12_000.0)]
    low = route_plan.plan_corridor(
        overlay,
        anchors,
        agl_for=route_plan.agl_bands(((0.0, 100.0),)),
        cell_m=200.0,
        pad_m=6_000.0,
        min_leg_m=200.0,
    )
    high = route_plan.plan_corridor(
        overlay,
        anchors,
        agl_for=route_plan.agl_bands(((0.0, 1_200.0),)),
        cell_m=200.0,
        pad_m=6_000.0,
        min_leg_m=200.0,
    )
    assert len(high.points) <= len(low.points)


def test_min_leg_stops_the_search_subdividing_forever():
    """A knife-edge saddle must terminate, even though it cannot be flown low."""
    heights = np.zeros((200, 200), dtype=float)
    heights[:, 95] = 4_000.0
    overlay = _Overlay(heights)
    route = route_plan.plan_corridor(
        overlay,
        [at(-5_500.0, 2_000.0), at(-5_500.0, 12_000.0)],
        cell_m=200.0,
        pad_m=4_000.0,
        min_leg_m=3_000.0,
        max_waypoints=6,
    )
    for a, b in zip(route.points, route.points[1:]):
        assert a.distance_to_point(b) >= 1.0


# -- reporting ---------------------------------------------------------------


def test_table_emits_one_row_per_waypoint_with_the_names_given():
    overlay = flat(500.0)
    route = route_plan.plan_corridor(
        overlay, [at(0.0, 1_000.0), at(0.0, 8_000.0)], cell_m=200.0, pad_m=1_000.0
    )
    rows = route.table(("PUSH", "TARGET"), speed_kph=680).splitlines()
    assert len(rows) == 2
    assert rows[0].strip().startswith('("PUSH",')
    assert "680" in rows[0]


def test_table_falls_back_to_positional_names():
    overlay = flat()
    route = route_plan.plan_corridor(
        overlay, [at(0.0, 1_000.0), at(0.0, 8_000.0)], cell_m=200.0, pad_m=1_000.0
    )
    assert '("WP00"' in route.table()


def test_lift_reports_what_clear_terrain_had_to_raise():
    overlay = flat()
    route = route_plan.plan_corridor(
        overlay, [at(0.0, 1_000.0), at(0.0, 8_000.0)], cell_m=200.0, pad_m=1_000.0
    )
    assert route.lift_m == tuple(
        a - r for a, r in zip(route.altitude_m, route.requested_m)
    )


# -- what can see the route --------------------------------------------------


def test_a_site_behind_the_wall_sees_nothing_and_one_beside_it_sees_everything():
    overlay = ridge_with_gap()
    route = [at(-5_500.0, y) for y in (2_000.0, 4_000.0, 6_000.0)]
    altitudes = [overlay.elevation_at(p) + 300.0 for p in route]
    masked, open_ground = route_plan.sighting(
        overlay,
        route,
        [(at(-5_500.0, 12_000.0), "behind"), (at(-5_500.0, 3_000.0), "beside")],
        altitudes_m=altitudes,
    )
    assert masked.first_seen is None
    assert "masked at every point" in masked.summary()
    assert open_ground.first_seen == 0


def test_sighting_reports_the_range_at_the_point_it_first_sees():
    overlay = flat()
    route = [at(0.0, y) for y in (1_000.0, 2_000.0, 3_000.0)]
    look = route_plan.sighting(overlay, route, [(at(0.0, 0.0), "site")])[0]
    assert look.first_seen == 0
    assert look.range_m[0] == pytest.approx(1_000.0)


# -- the cartridge budget ----------------------------------------------------


def test_nav_headroom_subtracts_the_route_and_its_overhead():
    from dcs_mission_creator.core import dtc

    assert route_plan.nav_headroom(0, overhead=0) == dtc.MAX_NAV_POINTS
    assert route_plan.nav_headroom(16) == dtc.MAX_NAV_POINTS - 20


def test_nav_headroom_goes_negative_when_the_route_alone_is_too_long():
    assert route_plan.nav_headroom(40) < 0
