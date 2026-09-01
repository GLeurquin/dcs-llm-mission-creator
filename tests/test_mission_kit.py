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

from dcs_mission_creator.core.mission_kit import (
    MAX_FLIGHT_SIZE,
    player_flight,
    section_names,
    section_sizes,
    sections_of,
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
        stores=[(1, "AIM_120C_AMRAAM___Active_Radar_AAM")],
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


def test_every_section_carries_the_same_loadout(mission: Mission):
    loadouts = {
        tuple(sorted((p, w["CLSID"]) for p, w in g.units[0].pylons.items()))
        for g in _build(mission, 6)
    }
    assert len(loadouts) == 1


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
