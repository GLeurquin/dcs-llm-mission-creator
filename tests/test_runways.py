"""`core/runways.py`: the keep-out, with or without the install's localizers."""

from __future__ import annotations

from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import runways, sanctuary as sanc

TERRAIN = Caucasus()
VAZIANI = TERRAIN.airports["Vaziani"]


def test_the_reference_point_is_on_the_runway() -> None:
    """The fact the whole module rests on, and the bug it was written for."""
    assert runways.fouled_strip(VAZIANI.position, [VAZIANI], TERRAIN) is not None


def test_a_point_well_abeam_is_clear() -> None:
    (strip,) = runways.strips(VAZIANI, TERRAIN)
    abeam = VAZIANI.position.point_from_heading(strip.heading_deg + 90.0, 1_000.0)
    assert runways.fouled_strip(abeam, [VAZIANI], TERRAIN) is None


def test_nearest_clear_leaves_the_strip_sideways() -> None:
    (strip,) = runways.strips(VAZIANI, TERRAIN)
    on = VAZIANI.position.point_from_heading(strip.heading_deg, 500.0)
    off = strip.nearest_clear(on)
    assert not strip.contains(off)
    along_on, _ = strip.offsets(on)
    along_off, _ = strip.offsets(off)
    assert abs(along_on - along_off) < 1.0


def test_push_clear_moves_only_the_unit_on_the_runway() -> None:
    m = Mission(TERRAIN)
    (strip,) = runways.strips(VAZIANI, TERRAIN)
    abeam = VAZIANI.position.point_from_heading(strip.heading_deg + 90.0, 1_000.0)
    group = m.vehicle_group(
        m.country("USA"), "Guns", AirDefence.Vulcan, VAZIANI.position, group_size=2
    )
    group.units[1].position = Point(abeam.x, abeam.y, TERRAIN)
    assert runways.push_clear(group, [strip]) == 1
    assert all(not strip.contains(u.position) for u in group.units)
    assert group.units[1].position.distance_to_point(abeam) < 1.0


def test_a_sanctuary_facing_down_the_runway_keeps_off_it() -> None:
    """Axis along the runway: the worst case for both the battery and the ring."""
    m = Mission(TERRAIN)
    (strip,) = runways.strips(VAZIANI, TERRAIN)
    facing = VAZIANI.position.point_from_heading(strip.heading_deg, 100_000.0)
    built = sanc.build_sanctuary(
        m, m.country("USA"), VAZIANI, callsign="SENTRY", facing=facing,
        battery=sanc.HAWK,
    )  # fmt: skip
    for group in built.groups:
        for unit in group.units:
            assert runways.fouled_strip(unit.position, [VAZIANI], TERRAIN) is None, (
                unit.name
            )
