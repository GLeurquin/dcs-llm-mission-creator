"""Tests for `snap_units_clear` — no overlay on disk, no DCS install.

The real `MapOverlay` needs a built raster stack that CI does not have, so the
two rules under test are exercised against a stand-in that answers the only two
questions the snap asks: is this cell clear, and where are the nearby clear ones.
Both rules exist because of a real failure: dispersing SAM sites across hundreds
of metres put several units of one group into the same treeline, they were all
handed the same clear cell, and the escalating fallback threw one of them a
kilometre downhill. The site came out 1.3 km wide with two vehicles inside each
other.
"""

from __future__ import annotations

import pytest
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.vehicles import AirDefence

from dcs_mission_creator.core.placement import (
    MIN_UNIT_SEPARATION_M,
    UNIT_SNAP_RADIUS_M,
    snap_units_clear,
)
from dcs_mission_creator.map_overlay.placement import Placement, Vegetation


class FakeOverlay:
    """Everything is forest except the points listed as clear.

    `offers` is what `find_placement` hands back, in order, whatever was asked
    for — which is the property that matters here: the real one samples cells
    rather than sorting them, so the first offer is not necessarily reachable or
    unclaimed.
    """

    def __init__(self, clear: list[Point], offers: list[Point]) -> None:
        self._clear = clear
        self._offers = offers

    def vegetation_at(self, point: Point) -> Vegetation:
        if any(point.distance_to_point(c) < 5.0 for c in self._clear):
            return Vegetation.NONE
        return Vegetation.DENSE_FOREST

    def find_placement(
        self, near: Point, radius_m: float, require: Placement, count: int = 1
    ) -> list[Point]:
        return self._offers[:count]


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


def _pair(mission: Mission, apart_m: float) -> tuple[Point, Point]:
    """Two positions `apart_m` apart, both in the fake's forest."""
    origin = mission.terrain.airports["Batumi"].position
    return origin, origin.point_from_heading(90.0, apart_m)


def _site(mission: Mission, a: Point, b: Point):
    vg = mission.vehicle_group(
        mission.country("Russia"), "pair", AirDefence.Osa_9A33_ln, a, 0
    )
    second = mission.vehicle("second", AirDefence.Osa_9A33_ln)
    second.position = b
    vg.add_unit(second)
    return vg


def test_two_units_are_never_snapped_onto_the_same_cell(mission: Mission) -> None:
    """The bug this fixes put two vehicles on one point."""
    a, b = _pair(mission, 30.0)
    only_clear = a.point_from_heading(0.0, 100.0)
    vg = _site(mission, a, b)

    snap_units_clear(
        FakeOverlay(clear=[only_clear], offers=[only_clear]),  # ty: ignore[invalid-argument-type]
        mission.terrain,
        vg,
    )

    moved, left = vg.units[0].position, vg.units[1].position
    assert moved.distance_to_point(only_clear) < 1.0, "the first unit took the spot"
    assert left.distance_to_point(b) < 1.0, "the second should stay where it was"
    assert moved.distance_to_point(left) >= MIN_UNIT_SEPARATION_M


def test_a_unit_is_not_thrown_beyond_the_search_radius(mission: Mission) -> None:
    """A unit left in the canopy beats a battery smeared across a valley."""
    a, b = _pair(mission, 400.0)
    far = a.point_from_heading(0.0, UNIT_SNAP_RADIUS_M * 4.0)
    vg = _site(mission, a, b)

    snap_units_clear(
        FakeOverlay(clear=[far], offers=[far]),  # ty: ignore[invalid-argument-type]
        mission.terrain,
        vg,
    )

    assert vg.units[0].position.distance_to_point(a) < 1.0
    assert vg.units[1].position.distance_to_point(b) < 1.0


def test_units_do_move_when_there_is_room_for_both(mission: Mission) -> None:
    """The rules bound the snap; they must not switch it off."""
    a, b = _pair(mission, 30.0)
    spots = [a.point_from_heading(0.0, 60.0), a.point_from_heading(180.0, 60.0)]
    vg = _site(mission, a, b)

    snap_units_clear(
        FakeOverlay(clear=spots, offers=spots),  # ty: ignore[invalid-argument-type]
        mission.terrain,
        vg,
    )

    placed = [u.position for u in vg.units]
    assert all(any(p.distance_to_point(s) < 1.0 for s in spots) for p in placed)
    assert placed[0].distance_to_point(placed[1]) >= MIN_UNIT_SEPARATION_M


def test_a_unit_already_in_the_clear_is_left_alone(mission: Mission) -> None:
    a, b = _pair(mission, 30.0)
    vg = _site(mission, a, b)
    elsewhere = a.point_from_heading(0.0, 90.0)

    snap_units_clear(
        FakeOverlay(clear=[a, b], offers=[elsewhere]),  # ty: ignore[invalid-argument-type]
        mission.terrain,
        vg,
    )

    assert vg.units[0].position.distance_to_point(a) < 1.0
    assert vg.units[1].position.distance_to_point(b) < 1.0
