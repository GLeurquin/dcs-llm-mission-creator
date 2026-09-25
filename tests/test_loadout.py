"""The air-to-air magazine count the force-balance arithmetic divides by two.

Store names are pydcs attribute names, exactly as a mission writes them; the
count comes off ED's display name behind each one.
"""

from __future__ import annotations

import pytest

from dcs_mission_creator.core.loadout import Loadout, air_to_air_shots


def _fit(*stores: str) -> Loadout:
    return Loadout(
        role="test",
        carries="test",
        stores=tuple(enumerate(stores, start=1)),
    )


@pytest.mark.parametrize(
    ("store", "rounds"),
    [
        ("AIM_120C_AMRAAM___Active_Radar_AAM", 1),
        ("AIM_9X_Sidewinder_IR_AAM", 1),
        # A rack name ends in `_AAM_`, which the old suffix rule missed.
        ("LAU_115_with_1_x_LAU_127_AIM_120C_AMRAAM___Active_Radar_AAM_", 1),
        ("LAU_115_2_LAU_127_AIM_120C", 2),
        # Heatblur's names carry no `AAM` at all.
        ("AIM_54A_Mk47", 1),
        ("AIM_7M", 1),
        ("LAU_138_AIM_9M", 1),
        ("APU_60_2M_with_2_x_R_60M__AA_8_Aphid_B____IR_AAM_", 2),
        ("MICA_IR", 1),
    ],
)
def test_each_missile_station_counts_its_rounds(store: str, rounds: int) -> None:
    assert air_to_air_shots(_fit(store)) == rounds


@pytest.mark.parametrize(
    "store",
    [
        "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_",
        "Fuel_tank_370_gal",
        "TER_9A_with_2_x_GBU_12___500lb_Laser_Guided_Bomb",
    ],
)
def test_anything_else_is_no_shot(store: str) -> None:
    assert air_to_air_shots(_fit(store)) == 0


def test_a_captive_round_is_no_shot() -> None:
    """A training seeker on a rail is carried and fired at nothing."""
    from dcs.weapons_data import Weapons

    captive = next(
        name
        for name, record in vars(Weapons).items()
        if isinstance(record, dict) and "Captive AIM-9M" in record.get("name", "")
    )
    assert air_to_air_shots(_fit(captive)) == 0


def test_a_dual_amraam_hornet_fit() -> None:
    """Four dual rails, two singles and two wingtip AIM-9X: twelve shots."""
    dual = "LAU_115_2_LAU_127_AIM_120C"
    single = "AIM_120C_AMRAAM___Active_Radar_AAM"
    tip = "AIM_9X_Sidewinder_IR_AAM"
    assert (
        air_to_air_shots(_fit(tip, dual, dual, single, single, dual, dual, tip)) == 12
    )
