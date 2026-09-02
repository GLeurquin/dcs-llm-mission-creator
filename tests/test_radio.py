"""Unit tests for `core/radio.py`.

The property under test is the one the missions shipped wrong: a `frequency=`
argument to `awacs_flight` / `refuel_flight` reaches the group's own `frequency`
field, which is what a player's radio has to match, and not only the
`SetFrequency` waypoint task pydcs puts it in. A `Mission(Caucasus())` is cheap
and needs neither a DCS install nor the map overlay, so these run in the default
selection.
"""

from __future__ import annotations

import pytest
from dcs import planes, task
from dcs.mission import Mission, StartType
from dcs.terrain import Caucasus

from dcs_mission_creator.core import radio
from dcs_mission_creator.core.mission_kit import mark_clients


@pytest.fixture
def mission() -> Mission:
    m = Mission(Caucasus())
    m.terrain.airports["Batumi"].set_blue()
    return m


def _batumi(m: Mission):
    return m.terrain.airports["Batumi"]


def _tanker(m: Mission, *, frequency: int = 253):
    batumi = _batumi(m)
    return m.refuel_flight(
        m.country("USA"),
        "Texaco",
        planes.KC_135,
        airport=batumi,
        position=batumi.position.point_from_heading(180, 60_000),
        frequency=frequency,
        tacanchannel="12X",
    )


def _viper(m: Mission, name: str = "Dodge"):
    batumi = _batumi(m)
    flight = m.flight_group_from_airport(
        m.country("USA"),
        name,
        planes.F_16C_50,
        batumi,
        maintask=task.CAP,
        start_type=StartType.Warm,
        group_size=2,
    )
    mark_clients(flight)
    return flight


def test_pydcs_leaves_the_tanker_on_the_default_until_the_sweep(
    mission: Mission,
) -> None:
    """The defect itself: `refuel_flight` never touches the group field."""
    tanker = _tanker(mission, frequency=253)
    assert tanker.frequency == 251
    assert tanker.radio_set is False

    radio.tune_working_frequencies(mission)

    assert tanker.frequency == 253
    # ED's own working tanker carries `radioSet = true`; that is the flag saying
    # the field rather than a preset table is the group's radio.
    assert tanker.radio_set is True


def test_an_integral_frequency_is_written_as_an_integer(mission: Mission) -> None:
    tanker = _tanker(mission, frequency=253)
    radio.tune_working_frequencies(mission)
    assert isinstance(tanker.frequency, int)


def test_a_fractional_frequency_survives(mission: Mission) -> None:
    tanker = _tanker(mission)
    tanker.points[1].tasks.clear()
    tanker.points[1].tasks.append(task.SetFrequencyCommand(251.5, task.Modulation.AM))
    radio.tune_working_frequencies(mission)
    assert tanker.frequency == pytest.approx(251.5)


def test_a_fac_task_beats_a_set_frequency_task(mission: Mission) -> None:
    """A controller talks on its FAC frequency, whatever the route says."""
    fac = mission.flight_group_from_airport(
        mission.country("USA"),
        "Hammer",
        planes.MQ_9_Reaper,
        _batumi(mission),
        maintask=task.AFAC,
        start_type=StartType.Warm,
    )
    fac.points[0].tasks.append(
        task.FACEngageGroup(1, frequency=133, modulation=task.Modulation.AM)
    )
    fac.points[0].tasks.append(task.SetFrequencyCommand(270, task.Modulation.AM))

    radio.tune_working_frequencies(mission)

    assert fac.frequency == 133
    assert fac.modulation == task.Modulation.AM.value


def test_the_modulation_comes_with_the_frequency(mission: Mission) -> None:
    fac = mission.flight_group_from_airport(
        mission.country("USA"),
        "Hammer",
        planes.MQ_9_Reaper,
        _batumi(mission),
        maintask=task.AFAC,
        start_type=StartType.Warm,
    )
    fac.points[0].tasks.append(
        task.FACEngageGroup(1, frequency=133, modulation=task.Modulation.FM)
    )
    radio.tune_working_frequencies(mission)
    assert fac.modulation == task.Modulation.FM.value


def test_a_client_group_keeps_its_cockpit_presets(mission: Mission) -> None:
    """`radioSet` on a player group would override the airframe's preset table,
    which the comms card's `R1 CH18` annotations are computed from."""
    flight = _viper(mission)
    flight.points[0].tasks.append(task.SetFrequencyCommand(377, task.Modulation.AM))

    radio.tune_working_frequencies(mission)

    assert flight.frequency == 251
    assert flight.radio_set is False


def test_a_flight_that_states_nothing_is_left_alone(mission: Mission) -> None:
    batumi = _batumi(mission)
    flight = mission.flight_group_from_airport(
        mission.country("USA"),
        "Hawg",
        planes.A_10C,
        batumi,
        maintask=task.CAS,
        start_type=StartType.Warm,
    )
    radio.tune_working_frequencies(mission)
    assert flight.frequency == 251
    assert flight.radio_set is False


def test_the_sweep_reaches_both_coalitions(mission: Mission) -> None:
    """Red launching on its own clock is policy; red being unreachable is not."""
    mission.terrain.airports["Sukhumi-Babushara"].set_red()
    awacs = mission.awacs_flight(
        mission.country("Russia"),
        "Ivan",
        plane_type=planes.A_50,
        airport=mission.terrain.airports["Sukhumi-Babushara"],
        position=mission.terrain.airports[
            "Sukhumi-Babushara"
        ].position.point_from_heading(0, 60_000),
        frequency=124,
    )
    radio.tune_working_frequencies(mission)
    assert awacs.frequency == 124


def test_working_frequency_reports_nothing_when_nothing_was_stated(
    mission: Mission,
) -> None:
    flight = _viper(mission)
    assert radio.working_frequency(flight) is None
