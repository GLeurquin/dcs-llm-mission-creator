"""Unit tests for `core/datalink.py`.

A `Mission(Caucasus())` is cheap and needs neither a DCS install nor the map
overlay, so these run in the default (fast) selection. They assert on the
*mechanism* — unique track numbers, distinct callsigns, a flight that lists
itself — not on any mission's composition.
"""

from __future__ import annotations

import pytest
from dcs import planes
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core.datalink import assign_datalink_identities


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


def _flight(m: Mission, name: str, plane_type, size: int):
    return m.flight_group_from_airport(
        m.country("USA"),
        name,
        plane_type,
        m.terrain.airports["Batumi"],
        group_size=size,
    )


def test_every_slot_gets_its_own_track_number(mission: Mission):
    viper = _flight(mission, "Viper", planes.F_16C_50, 4)
    assign_datalink_identities(mission)
    stns = [u.addpropaircraft["STN_L16"] for u in viper.units]
    assert stns == ["00101", "00102", "00103", "00104"]


def test_track_numbers_are_unique_across_flights(mission: Mission):
    a = _flight(mission, "Viper", planes.F_16C_50, 2)
    b = _flight(mission, "Hornet", planes.FA_18C_hornet, 2)
    assign_datalink_identities(mission)
    stns = [u.addpropaircraft["STN_L16"] for u in (*a.units, *b.units)]
    assert len(set(stns)) == len(stns)


def test_link16_and_sadl_number_independently(mission: Mission):
    """Different networks, so a Viper STN and a Hog TN may collide."""
    viper = _flight(mission, "Viper", planes.F_16C_50, 1)
    hog = _flight(mission, "Hog", planes.A_10C_2, 1)
    assign_datalink_identities(mission)
    assert viper.units[0].addpropaircraft["STN_L16"] == "00101"
    assert hog.units[0].addpropaircraft["SADL_TN"] == "0101"


def test_voice_callsigns_follow_the_editor_rule(mission: Mission):
    """`Springfield11` -> label `SD`, number `11`; one identity per slot."""
    viper = _flight(mission, "Viper", planes.F_16C_50, 2)
    assign_datalink_identities(mission)
    names = [u.callsign_as_str() for u in viper.units]
    props = [
        (
            u.addpropaircraft["VoiceCallsignLabel"],
            u.addpropaircraft["VoiceCallsignNumber"],
        )
        for u in viper.units
    ]
    expected_label = (names[0][0] + names[0].rstrip("0123456789")[-1]).upper()
    assert props == [(expected_label, "11"), (expected_label, "12")]
    assert len(set(props)) == 2


def test_flight_lists_itself_as_team_members(mission: Mission):
    viper = _flight(mission, "Viper", planes.F_16C_50, 3)
    assign_datalink_identities(mission)
    ids = [u.id for u in viper.units]
    for unit in viper.units:
        network = unit.datalinks["Link16"]["network"]
        assert [m["missionUnitId"] for m in network["teamMembers"]] == ids
        assert all(m["TDOA"] for m in network["teamMembers"])
        assert network["donors"] == []


def test_only_the_lead_is_flight_lead(mission: Mission):
    viper = _flight(mission, "Viper", planes.F_16C_50, 4)
    assign_datalink_identities(mission)
    leads = [u.datalinks["Link16"]["settings"]["flightLead"] for u in viper.units]
    assert leads == [True, False, False, False]


def test_hornet_team_members_carry_no_tdoa(mission: Mission):
    hornet = _flight(mission, "Hornet", planes.FA_18C_hornet, 2)
    assign_datalink_identities(mission)
    members = hornet.units[0].datalinks["Link16"]["network"]["teamMembers"]
    assert members == [{"missionUnitId": u.id} for u in hornet.units]


def test_module_without_a_datalink_dialog_is_left_alone(mission: Mission):
    """The AI Eagle gets an identity but no network table — as the ME writes it."""
    eagle = _flight(mission, "Eagle", planes.F_15C, 2)
    assign_datalink_identities(mission)
    assert eagle.units[0].addpropaircraft["STN_L16"] == "00101"
    assert getattr(eagle.units[0], "datalinks", None) is None


def test_datalinks_survive_serialization(mission: Mission):
    viper = _flight(mission, "Viper", planes.F_16C_50, 2)
    assign_datalink_identities(mission)
    unit = viper.units[0].dict()
    assert unit["datalinks"]["Link16"]["settings"]["transmitPower"] == 3
    assert unit["AddPropAircraft"]["STN_L16"] == "00101"


def test_units_without_datalinks_omit_the_key(mission: Mission):
    eagle = _flight(mission, "Eagle", planes.F_15C, 1)
    assign_datalink_identities(mission)
    assert "datalinks" not in eagle.units[0].dict()


def test_split_player_flight_is_one_team(mission: Mission):
    """Six coop slots are two DCS groups and one flight on the net.

    Teaming each group only with itself would put half the flight off the other
    half's scope, which is the blindness this module exists to fix.
    """
    from dcs.mission import StartType
    from dcs.task import CAP

    from dcs_mission_creator.core.loadout import Loadout
    from dcs_mission_creator.core.mission_kit import player_flight

    # Two fits, because every player flight in this project declares two;
    # this test is about the datalink team, so they differ in name only.
    _BARE_FITS = (
        Loadout(role="clean", carries="nothing", stores=()),
        Loadout(role="clean 2", carries="nothing", stores=()),
    )

    sections = player_flight(
        mission,
        country=mission.country("USA"),
        name="Dodge",
        aircraft_type=planes.F_16C_50,
        airport=mission.terrain.airports["Batumi"],
        maintask=CAP,
        start_type=StartType.Warm,
        slots=6,
        loadouts=_BARE_FITS,
    )
    assign_datalink_identities(mission)

    assert [g.name for g in sections] == ["Dodge", "Dodge 2"]
    everyone = [u.id for g in sections for u in g.units]
    for group in sections:
        for unit in group.units:
            members = unit.datalinks["Link16"]["network"]["teamMembers"]
            assert [m["missionUnitId"] for m in members] == everyone


def test_unsplit_flights_stay_their_own_team(mission: Mission):
    """Two separate flights of the same type are two teams, as before."""
    one = _flight(mission, "Viper", planes.F_16C_50, 2)
    two = _flight(mission, "Hawg", planes.F_16C_50, 2)
    assign_datalink_identities(mission)
    for group in (one, two):
        members = group.units[0].datalinks["Link16"]["network"]["teamMembers"]
        assert [m["missionUnitId"] for m in members] == [u.id for u in group.units]
