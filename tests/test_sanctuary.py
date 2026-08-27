"""Tests for `core.sanctuary` — the invariants, on a bare `Mission`.

Four properties are pinned, and each of them is a way the feature would be
quietly wrong rather than broken:

- the `keep_clear` refusal, because an envelope that reaches the AO deletes the
  mission and its only symptom in the built `.miz` is a circle that is too big;
- the reveal asymmetry, because our own battery drawn as an estimate is useless
  to a pilot who is hit and an enemy battery drawn precisely is a leak;
- the marshal leg fitting inside its own envelope, because a hold drawn outside
  the cover is the one drawing whose whole purpose is that nothing can reach it;
- the primary/divert split in what reaches the cartridge, since it is the only
  thing standing between a scarce navigation tab and two useless duplicates.

Nothing here asserts on composition — see `tests/test_mission_smoke.py`'s note
on why freezing that would make every balance tweak look like a regression.
"""

from __future__ import annotations

import pytest
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core import dtc, sanctuary as sanc
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay

TERRAIN = Caucasus()


def _mission() -> Mission:
    return Mission(TERRAIN)


def _ao(m: Mission, *, km_from_batumi: float) -> Point:
    """A point due north of Batumi, so distances in a test read as themselves."""
    return TERRAIN.airports["Batumi"].position.point_from_heading(
        0.0, km_from_batumi * 1000.0
    )


def test_an_envelope_reaching_the_ao_is_refused() -> None:
    m = _mission()
    ao = _ao(m, km_from_batumi=100.0)
    # Hawk reaches 45 km and clears by ~50: fine.
    sanc.build_sanctuary(
        m,
        m.country("USA"),
        TERRAIN.airports["Batumi"],
        callsign="OK",
        facing=ao,
        battery=sanc.HAWK,
        keep_clear=[ao],
    )
    # Patriot reaches 100 km and covers it outright.
    with pytest.raises(ValueError, match="envelope comes within"):
        sanc.build_sanctuary(
            m,
            m.country("USA"),
            TERRAIN.airports["Batumi"],
            callsign="TOO BIG",
            facing=ao,
            battery=sanc.PATRIOT,
            keep_clear=[ao],
        )


def test_clearance_is_measured_from_the_battery_not_the_runway() -> None:
    """The battery is emplaced up the threat axis, which costs real margin.

    A Hawk whose ring clears the AO by 46 km from the runway clears it by only
    ~41.5 from where the launchers actually stand, and it is the launchers that
    shoot. Sited at 50 km of clearance the check passes; asking for 47 km of
    margin fails, which it would not if the arithmetic used the reference point.
    """
    m = _mission()
    ao = _ao(m, km_from_batumi=95.0)
    with pytest.raises(ValueError):
        sanc.build_sanctuary(
            m,
            m.country("USA"),
            TERRAIN.airports["Batumi"],
            callsign="TIGHT",
            facing=ao,
            battery=sanc.HAWK,
            keep_clear=[ao],
            clearance_m=47_000.0,
        )


def test_our_own_umbrella_is_precise_at_every_difficulty() -> None:
    """`ace` coarsens what we claim about *them*, never about our own battery."""
    for difficulty in Difficulty:
        m = _mission()
        plan = PlanOverlay(m, difficulty)
        home = sanc.build_sanctuary(
            m,
            m.country("USA"),
            TERRAIN.airports["Batumi"],
            callsign="BULLDOG",
            facing=_ao(m, km_from_batumi=100.0),
            battery=sanc.HAWK,
        )
        assert home.draw(plan) == [], "our own battery is not a pre-planned threat"
        ring = next(mk for mk in plan.marks() if mk.kind == "umbrella")
        assert ring.position == home.center, difficulty
        assert not ring.enemy
        assert f"{sanc.HAWK.radius_m / 1000:.0f} km" in ring.label


def test_an_enemy_field_battery_goes_through_the_reveal_policy() -> None:
    """Same call, red side: an estimate, and a cartridge point built off it."""
    m = _mission()
    plan = PlanOverlay(m, Difficulty.TRAINED)
    field = sanc.build_sanctuary(
        m,
        m.country("Russia"),
        TERRAIN.airports["Sukhumi-Babushara"],
        callsign="Sukhumi field",
        facing=_ao(m, km_from_batumi=100.0),
        battery=sanc.SA_3,
        enemy=True,
        label="SA-3 Sukhumi",
    )
    (point,) = field.draw(plan)
    mark = next(mk for mk in plan.marks() if mk.kind == "threat")
    assert mark.enemy and "(est.)" in mark.label
    # The drawn position is off truth, and the cartridge carries *that* one.
    assert mark.position != field.center
    assert point.position == mark.position
    assert point.hsd_code() == dtc.SA_3.code
    assert field.marshal is None, "we do not brief their holding pattern"
    assert field.remarks() == [], "the route card's threat block already has it"
    with pytest.raises(ValueError, match="enemy sanctuary"):
        sanc.checkin_text(field, controller="Magic")


def test_the_marshal_leg_stays_inside_its_own_envelope() -> None:
    """A hold drawn outside the cover is the one thing this may never do.

    NASAMS reaches 15 km, and the un-shrunk 14 km leg put both ends 18-19 km
    from the launchers — `eastern_shield` shipped exactly that at Gaziantep.
    """
    for battery in (sanc.NASAMS, sanc.HAWK, sanc.PATRIOT):
        m = _mission()
        home = sanc.build_sanctuary(
            m,
            m.country("USA"),
            TERRAIN.airports["Batumi"],
            callsign="HOME",
            facing=_ao(m, km_from_batumi=400.0),
            battery=battery,
        )
        assert home.marshal is not None
        for end in home.marshal:
            assert home.covers(end), battery.name
            assert home.center.distance_to_point(end) <= home.radius_m * 0.75


def test_a_divert_offers_its_position_and_a_primary_offers_a_hold() -> None:
    """The two cases want opposite things from a scarce navigation tab.

    A primary field is already the flight's own take-off and landing waypoint,
    so a mark on it restates the route; what it adds is the hold. A divert has no
    waypoint near it, so its position is the whole point — and nobody diverts in
    order to orbit.
    """
    m = _mission()
    plan = PlanOverlay(m, Difficulty.TRAINED)
    ao = _ao(m, km_from_batumi=400.0)
    primary = sanc.build_sanctuary(
        m,
        m.country("USA"),
        TERRAIN.airports["Batumi"],
        callsign="HOME",
        facing=ao,
        battery=sanc.NASAMS,
    )
    divert = sanc.build_sanctuary(
        m,
        m.country("USA"),
        TERRAIN.airports["Kobuleti"],
        callsign="ALT",
        facing=ao,
        battery=sanc.NASAMS,
        divert=True,
    )
    primary.draw(plan)
    divert.draw(plan)

    orbits = [ln for ln in plan.lines() if ln.kind == "orbit"]
    assert [ln.label for ln in orbits] == ["HOME MARSHAL"]
    assert divert.marshal is None

    labels = [mk.label for mk in plan.marks() if mk.kind == "waypoint"]
    assert labels == ["Kobuleti — divert (ALT)"]
    assert not any("Batumi" in text for text in labels)


def test_a_ring_never_takes_a_navigation_steerpoint() -> None:
    """The battery is 4.5 km off the runway — nobody needs a bearing to it.

    The marshal leg does reach the cartridge, via its orbit midpoint, and it has
    to come out ahead of anything drawn later: `core/dtc.plan_steerpoints`
    interleaves marks and lines by draw order for exactly this.
    """
    m = _mission()
    plan = PlanOverlay(m, Difficulty.TRAINED)
    home = sanc.build_sanctuary(
        m,
        m.country("USA"),
        TERRAIN.airports["Batumi"],
        callsign="HOME",
        facing=_ao(m, km_from_batumi=400.0),
        battery=sanc.HAWK,
    )
    home.draw(plan)
    plan.waypoint_label(_ao(m, km_from_batumi=50.0), "drawn later")

    notes = [point.note for point in dtc.plan_steerpoints(plan)]
    assert notes == ["HOME MARSHAL", "drawn later"]
    assert not any("umbrella" in (note or "") for note in notes)


def test_a_template_battery_refuses_a_country_it_cannot_build_for() -> None:
    """`VehicleTemplate.USA.hawk_site` hard-codes its own owner and ignores ours."""
    m = _mission()
    with pytest.raises(ValueError, match="hard-codes"):
        sanc.build_sanctuary(
            m,
            m.country("Russia"),
            TERRAIN.airports["Batumi"],
            callsign="WRONG",
            facing=_ao(m, km_from_batumi=400.0),
            battery=sanc.HAWK,
        )


def test_an_alternate_outside_the_envelope_is_not_claimed_as_covered() -> None:
    """Kutaisi is 97 km from Batumi: a runway, not cover, and the record says so."""
    m = _mission()
    home = sanc.build_sanctuary(
        m,
        m.country("USA"),
        TERRAIN.airports["Batumi"],
        callsign="HOME",
        facing=_ao(m, km_from_batumi=400.0),
        battery=sanc.HAWK,
        alternates=[TERRAIN.airports["Kobuleti"], TERRAIN.airports["Kutaisi"]],
    )
    assert [a.name for a in home.alternates] == ["Kobuleti"]
    assert any("Kobuleti" in line for line in home.remarks())
    assert not any("Kutaisi" in line for line in home.remarks())
