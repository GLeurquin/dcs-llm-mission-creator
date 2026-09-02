"""Unit tests for `core/mission_kit.py`'s player-flight split.

A `Mission(Caucasus())` is cheap and needs neither a DCS install nor the map
overlay, so these run in the default (fast) selection. What is asserted is the
mechanism — how many groups, how big, what they are called and that they are
recorded as one flight — never a mission's composition.
"""

from __future__ import annotations

import pytest
from dcs import planes
from dcs.mission import Mission, StartType
from dcs.task import CAP
from dcs.terrain import Caucasus
from dcs.unit import Skill

from dcs_mission_creator.core.loadout import Loadout
from dcs_mission_creator.core.mission_kit import (
    MAX_FLIGHT_SIZE,
    player_flight,
    section_names,
    section_sizes,
    sections_of,
    slot_names,
)

_AMRAAM = "AIM_120C_AMRAAM___Active_Radar_AAM"
_SIDEWINDER = "AIM_9X_Sidewinder_IR_AAM"

#: A two-fit table, which is what every mission here now declares: the slots of
#: one flight do not carry the same jet.
_FITS = (
    Loadout(role="AMRAAM", carries="one AIM-120C", stores=((1, _AMRAAM),)),
    Loadout(role="9X", carries="one AIM-9X", stores=((1, _SIDEWINDER),)),
)


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


@pytest.mark.parametrize(
    "total, expected",
    [
        (1, (1,)),
        (4, (4,)),
        (5, (3, 2)),  # never a four-ship trailed by a lone jet
        (6, (4, 2)),
        (9, (4, 3, 2)),
    ],
)
def test_section_sizes(total: int, expected: tuple[int, ...]):
    assert section_sizes(total) == expected


def test_section_sizes_never_exceeds_the_group_limit():
    assert all(n <= MAX_FLIGHT_SIZE for n in section_sizes(12))


def test_section_names_leaves_the_lead_alone():
    assert section_names("Dodge", 3) == ("Dodge", "Dodge 2", "Dodge 3")


def _build(m: Mission, slots: int):
    return player_flight(
        m,
        country=m.country("USA"),
        name="Dodge",
        aircraft_type=planes.F_16C_50,
        airport=m.terrain.airports["Batumi"],
        maintask=CAP,
        start_type=StartType.Warm,
        slots=slots,
        loadouts=_FITS,
    )


def test_four_slots_stay_one_group(mission: Mission):
    sections = _build(mission, 4)
    assert [g.name for g in sections] == ["Dodge"]
    assert len(sections[0].units) == 4


def test_six_slots_become_two_groups(mission: Mission):
    """pydcs clamps `group_size` to four in silence, so six has to be split."""
    sections = _build(mission, 6)
    assert [(g.name, len(g.units)) for g in sections] == [("Dodge", 4), ("Dodge 2", 2)]


def test_every_slot_is_a_client(mission: Mission):
    for group in _build(mission, 6):
        assert all(u.skill == Skill.Client for u in group.units)


def _clsids(group, index: int) -> tuple[str, ...]:
    return tuple(w["CLSID"] for _, w in sorted(group.units[index].pylons.items()))


def test_slots_alternate_the_declared_fits(mission: Mission):
    """The fit table cycles in order, slot 1 first — the briefing says so."""
    group = _build(mission, 4)[0]
    roles = [_clsids(group, i) for i in range(4)]
    assert roles[0] == roles[2] and roles[1] == roles[3]
    assert roles[0] != roles[1]


def test_the_cycle_runs_across_sections_not_per_section(mission: Mission):
    """Slot 5 continues the table; it does not restart at the first fit.

    Six slots are `(4, 2)`, so restarting per section would hand slot 5 the same
    fit as slot 1 and leave the flight two jets short of the second one.
    """
    lead, second = _build(mission, 6)
    assert _clsids(second, 0) == _clsids(lead, 0)  # slot 5 -> fit 1 (even cycle)
    assert _clsids(second, 1) == _clsids(lead, 1)
    assert _clsids(lead, 0) != _clsids(lead, 1)


def test_slot_names_match_the_dcs_slot_list(mission: Mission):
    """The briefing's slot column has to be the string the player clicks."""
    built = [u.name for g in _build(mission, 6) for u in g.units]
    assert slot_names("Dodge", 6) == tuple(built)


def test_sections_know_each_other(mission: Mission):
    sections = _build(mission, 6)
    for group in sections:
        assert sections_of(mission, group) == tuple(sections)


def test_a_flight_built_elsewhere_is_its_own_section(mission: Mission):
    other = mission.flight_group_from_airport(
        mission.country("USA"),
        "Hawg",
        planes.F_16C_50,
        mission.terrain.airports["Batumi"],
        group_size=2,
    )
    assert sections_of(mission, other) == (other,)
