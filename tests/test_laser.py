"""Tests for `core.laser` — the code the bombs are on, and who may set it.

The defect this guards is invisible in the built `.miz`: a mission that briefs
one laser code while the controller lases another produces a perfectly valid
mission, and from the cockpit a bomb that tracks nothing looks like a bomb that
failed. Needs neither an overlay nor a DCS install.
"""

from __future__ import annotations

import pytest
from dcs import planes, task
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, VehicleGroup

from dcs_mission_creator.core import laser
from dcs_mission_creator.core.jtac import CoordTarget, arm_jtac_coords
from dcs_mission_creator.core.mission_kit import arm


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


def flight(m: Mission, plane_type, name: str = "Dodge") -> FlyingGroup:
    return m.flight_group_from_airport(
        country=m.country("USA"),
        name=name,
        aircraft_type=plane_type,
        airport=m.terrain.airport_by_id(22),  # Batumi
        maintask=task.CAS,
        group_size=1,
    )


def test_the_default_is_the_code_dcs_comes_up_on() -> None:
    assert laser.DEFAULT_CODE == 1688
    assert laser.AI_JTAC_CODE == laser.DEFAULT_CODE


@pytest.mark.parametrize("code", [1688, 1511, 1788, 1511])
def test_legal_codes_pass_validation(code: int) -> None:
    assert laser.validate_code(code) == code


@pytest.mark.parametrize("code", [1234, 688, 1911, 16888, 1680])
def test_illegal_codes_are_refused(code: int) -> None:
    with pytest.raises(ValueError):
        laser.validate_code(code)


def test_a_viper_keeps_the_default_and_writes_nothing(mission: Mission) -> None:
    viper = flight(mission, planes.F_16C_50)
    assert laser.is_settable(viper) is False
    assert laser.set_code(viper, laser.DEFAULT_CODE) == laser.DEFAULT_CODE
    assert laser.code_for(viper) == laser.DEFAULT_CODE
    props = viper.units[0].addpropaircraft or {}
    assert not any("Laser" in key for key in props)


def test_a_viper_refuses_a_code_it_cannot_come_up_on(mission: Mission) -> None:
    viper = flight(mission, planes.F_16C_50)
    with pytest.raises(ValueError, match="not a mission-file field"):
        laser.set_code(viper, 1511)


def test_an_airframe_with_the_property_is_written(mission: Mission) -> None:
    harrier = flight(mission, planes.AV8BNA)
    assert laser.is_settable(harrier) is True
    laser.set_code(harrier, 1511)
    props = harrier.units[0].addpropaircraft
    assert props["LaserCode100"] == 5
    assert props["LaserCode10"] == 1
    assert props["LaserCode1"] == 1
    # The Harrier codes the seekers separately from the pod; both move together.
    assert props["GBULaserCode100"] == 5
    assert laser.code_for(harrier) == 1511


def test_the_strike_eagle_codes_every_lgb_station(mission: Mission) -> None:
    eagle = flight(mission, planes.F_15ESE)
    laser.set_code(eagle, 1571)
    props = eagle.units[0].addpropaircraft
    assert props["Sta2LaserCode"] == 571
    assert props["RCFTLaserCode"] == 571
    assert laser.code_for(eagle) == 1571


def test_laser_guided_stores_reads_the_loaded_pylons(mission: Mission) -> None:
    viper = flight(mission, planes.F_16C_50)
    arm(
        viper,
        planes.F_16C_50,
        [
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (3, "GBU_12___500lb_Laser_Guided_Bomb"),
            (4, "Fuel_tank_370_gal"),
        ],
    )
    assert laser.laser_guided_stores(viper) == ["GBU-12 - 500lb Laser Guided Bomb"]


def test_a_flight_with_nothing_on_a_laser_reports_none(mission: Mission) -> None:
    viper = flight(mission, planes.F_16C_50)
    arm(
        viper,
        planes.F_16C_50,
        [
            (3, "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"),
            (4, "Fuel_tank_370_gal"),
        ],
    )
    assert laser.laser_guided_stores(viper) == []


def test_a_controller_briefed_off_the_ai_code_is_refused(mission: Mission) -> None:
    convoy = VehicleGroup(1, "Convoy")
    with pytest.raises(ValueError, match="lases on 1688"):
        arm_jtac_coords(
            mission,
            [CoordTarget(convoy, "Ferret 1-1", "the shipment", laser_code=1511)],
        )


def test_the_ai_code_is_accepted(mission: Mission) -> None:
    convoy = VehicleGroup(1, "Convoy")
    rule = arm_jtac_coords(
        mission,
        [
            CoordTarget(
                convoy, "Ferret 1-1", "the shipment", laser_code=laser.AI_JTAC_CODE
            )
        ],
    )
    assert rule in mission.triggerrules.triggers


def test_skill_is_untouched_by_a_code_write(mission: Mission) -> None:
    harrier = flight(mission, planes.AV8BNA)
    harrier.units[0].skill = Skill.Client
    laser.set_code(harrier, laser.DEFAULT_CODE)
    assert harrier.units[0].skill == Skill.Client
