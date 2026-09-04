"""`core/waypoints.clear_terrain` — the route is above the ground it flies over.

The bug this exists for shipped in `daryal_run` and survived several passes over
the file: pydcs waypoint altitudes are metres AMSL, so a valley route written as
"800 m through the gorge" is 800 m above the *sea*, and on the Caucasus that is
1.9 km underground. The second half is the one a per-waypoint fix misses — DCS
ramps linearly between waypoints, so two points that each clear their own valley
floor still draw a chord through the spur between them.

The overlay is stubbed with an elevation *function*, so a test states a terrain
profile in one line and needs neither a DCS install nor the built map overlay.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from dcs import statics
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.planes import F_16C_50
from dcs.terrain.caucasus.caucasus import Caucasus

from dcs_mission_creator.core import waypoints

TERRAIN = Caucasus()

#: `clear_terrain`'s own default, and the elevation raster's cell size.
_SAMPLE_M = 50.0

#: Both sides walk the same grid, so the only gap left is floating-point.
_EPS = 1e-6


class _Bounds:
    bottom, top, left, right = -1e9, 1e9, -1e9, 1e9


class _Manifest:
    bounds = _Bounds()


class _Overlay:
    """The two things `waypoints.ground_elevation_m` asks an overlay for."""

    theater = "Caucasus"
    manifest = _Manifest()

    def __init__(self, elevation: Callable[[float], float]) -> None:
        self._elevation = elevation

    def elevation_at(self, position: Point) -> float:
        return self._elevation(position.x)


def _at(x: float) -> Point:
    """A point `x` metres north of the origin; the stubs vary terrain with x."""
    return Point(x, 0.0, TERRAIN)


def _flat(height: float) -> _Overlay:
    return _Overlay(lambda _x: height)


def _ridge(
    *, peak_x: float, peak: float, half_width: float
) -> Callable[[float], float]:
    """A triangular spur on a 100 m floor.

    Continuous on purpose: a step function makes the test measure the sampling
    grid rather than the algorithm, since the checker and the helper would land
    on different sides of the same cliff.
    """

    def elevation(x: float) -> float:
        return 100.0 + max(0.0, peak - 100.0) * max(
            0.0, 1.0 - abs(x - peak_x) / half_width
        )

    return elevation


def test_waypoint_below_the_ground_is_lifted_to_the_clearance() -> None:
    """The AMSL trap on its own: a valley altitude that is under the valley."""
    overlay = _flat(2_000.0)
    got = waypoints.clear_terrain(
        [_at(0.0), _at(10_000.0)], [800.0, 700.0], overlay=overlay, clearance_m=150.0
    )
    assert got == [2_150.0, 2_150.0]


def test_altitudes_already_clear_are_left_exactly_alone() -> None:
    """A mission's own numbers are a floor: this never lowers and never nudges."""
    overlay = _flat(500.0)
    altitudes = [3_000.0, 2_500.0, 1_200.0]
    got = waypoints.clear_terrain(
        [_at(0.0), _at(10_000.0), _at(20_000.0)],
        altitudes,
        overlay=overlay,
        clearance_m=150.0,
    )
    assert got == altitudes


def test_a_spur_between_two_safe_waypoints_lifts_the_leg() -> None:
    """The half a per-waypoint fix misses: both ends clear, the chord does not."""
    overlay = _Overlay(_ridge(peak_x=5_000.0, peak=900.0, half_width=2_000.0))
    route = [_at(0.0), _at(10_000.0)]
    got = waypoints.clear_terrain(
        route, [300.0, 300.0], overlay=overlay, clearance_m=150.0
    )
    # Both ends were already 200 m over their own floor, so a per-point check
    # would have passed the leg straight through the spur.
    assert got != [300.0, 300.0]
    assert _min_clearance(route, got, overlay) >= 150.0 - _EPS


def test_a_descent_stays_a_descent() -> None:
    """Only the cheaper end moves, so the profile keeps its shape.

    A gorge route descends the whole way; lifting the *lower* end of every leg
    that clips rock would flatten it into a cruise at ridge height, and the
    masking that makes the mission work is in the descent.
    """
    overlay = _Overlay(_ridge(peak_x=15_000.0, peak=700.0, half_width=1_000.0))
    route = [_at(0.0), _at(10_000.0), _at(20_000.0), _at(30_000.0)]
    got = waypoints.clear_terrain(
        route, [2_000.0, 1_200.0, 400.0, 300.0], overlay=overlay, clearance_m=150.0
    )
    assert got == sorted(got, reverse=True)
    assert _min_clearance(route, got, overlay) >= 150.0 - _EPS


def test_every_leg_of_a_long_profile_clears() -> None:
    """The whole-route property, over a ridge line that bites several legs."""
    overlay = _Overlay(lambda x: 100.0 + 800.0 * abs(math.sin(x / 4_000.0)))
    route = [_at(5_000.0 * i) for i in range(12)]
    got = waypoints.clear_terrain(
        route, [400.0] * len(route), overlay=overlay, clearance_m=150.0
    )
    assert _min_clearance(route, got, overlay) >= 150.0 - _EPS


def test_a_mismatched_altitude_list_is_refused() -> None:
    """Silently zipping to the shorter list would drop the tail of the route."""
    with pytest.raises(ValueError, match="3 points but 2 altitudes"):
        waypoints.clear_terrain(
            [_at(0.0), _at(1_000.0), _at(2_000.0)],
            [500.0, 500.0],
            overlay=_flat(100.0),
        )


def _min_clearance(
    route: list[Point], altitudes: list[float], overlay: _Overlay
) -> float:
    """Lowest height above the ground anywhere on the flown ramp.

    Walks the same 50 m grid the helper does, because that grid is the data:
    against a real overlay the elevation raster has 50 m cells and there is no
    ground between two samples to hit. A stub with infinite resolution would
    otherwise fail this by centimetres wherever a smooth peak fell between two
    of the helper's samples, which measures the stub rather than the code.
    """
    worst = float("inf")
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        alt_a, alt_b = altitudes[i], altitudes[i + 1]
        steps = max(1, int(a.distance_to_point(b) / _SAMPLE_M))
        for step in range(steps + 1):
            f = step / steps
            here = a.new_in_same_map(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)
            worst = min(worst, alt_a * (1 - f) + alt_b * f - overlay.elevation_at(here))
    return worst


# -- add_target_waypoint -----------------------------------------------------


def test_a_target_waypoint_is_read_off_the_building_not_off_the_plan() -> None:
    """The signature is the enforcement: a `StaticGroup`, never a `Point`.

    Both missions that got this wrong derived the aimpoint from something the
    mission *asked for* — a `PlanOverlay` estimate in one, the plot-plan centre
    in the other — and neither is where the building ended up. Reading it back
    off the built unit is what makes "surveyed" true rather than briefed.
    """
    m = Mission(terrain=TERRAIN)
    hall = m.static_group(
        m.country("Russia"),
        "casting hall",
        statics.Fortification.Workshop_A,
        position=_at(20_000.0),
    )
    # The layout asked for one place; `_plot`-style nudging put it 180 m away.
    hall.units[0].position = _at(20_180.0)
    colt = m.flight_group_inflight(
        m.country("USA"), "Colt", F_16C_50, position=_at(0.0), altitude=5_000, speed=800
    )

    point = waypoints.add_target_waypoint(
        colt, hall, overlay=_flat(742.0), speed=640, name="HALL"
    )
    assert point.position == hall.units[0].position
    assert point.alt == 742.0  # the building's own ground, not the run-in altitude
    assert str(point.name) == "HALL"
