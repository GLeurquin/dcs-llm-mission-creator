"""Tests for `core.jtac` — needs a Mission, but no overlay and no DCS.

The failure this guards against is silent in a way the mission itself cannot
show: the readout is Lua generated from a table, so a target that never made it
into that table, or an airframe mapped to a format the script has no writer for,
still builds a perfectly valid `.miz` and simply answers nothing in-game.
"""

from __future__ import annotations

import pytest
from dcs import planes
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.triggers import TriggerStart
from dcs.unitgroup import VehicleGroup

from dcs_mission_creator.core import lua
from dcs_mission_creator.core.jtac import (
    COCKPIT_COORD_FORMAT,
    CoordFormat,
    CoordTarget,
    arm_jtac_coords,
)


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


@pytest.fixture
def convoy() -> VehicleGroup:
    return VehicleGroup(1, "Convoy")


def script_of(rule: TriggerStart) -> str:
    """The Lua the trigger carries, as DCS would compile it."""
    action = rule.actions[0]
    assert isinstance(action, lua.InlineDoScript)
    return action.script


def test_readout_carries_the_target_and_its_menu_entry(
    mission: Mission, convoy: VehicleGroup
) -> None:
    rule = arm_jtac_coords(
        mission,
        [CoordTarget(convoy, "Hammer 1-1", "the resupply column", laser_code=1688)],
        menu_title="Hammer 1-1",
    )
    script = script_of(rule)
    assert rule in mission.triggerrules.triggers
    assert '{group="Convoy", label="Hammer 1-1"' in script
    assert 'item="Target coordinates"' in script
    assert "code=1688" in script
    assert 'menuTitle = "Hammer 1-1"' in script


def test_a_target_without_a_laser_code_reads_nil(
    mission: Mission, convoy: VehicleGroup
) -> None:
    """An empty string would still be truthy in Lua and print "Laser code"."""
    script = script_of(
        arm_jtac_coords(mission, [CoordTarget(convoy, "Axeman 1-1", "the bunker")])
    )
    assert "code=nil" in script


def test_grid_cockpits_are_listed_and_the_rest_fall_through(
    mission: Mission, convoy: VehicleGroup
) -> None:
    """The A-10 gets a grid; the Viper is unlisted and takes the default."""
    script = script_of(
        arm_jtac_coords(mission, [CoordTarget(convoy, "Hammer 1-1", "the column")])
    )
    assert f'["{planes.A_10C_2.id}"] = "mgrs",' in script
    assert planes.F_16C_50.id not in script
    assert 'local default = "ddm"' in script


def test_formats_override_the_built_in_table(
    mission: Mission, convoy: VehicleGroup
) -> None:
    script = script_of(
        arm_jtac_coords(
            mission,
            [CoordTarget(convoy, "Hammer 1-1", "the column")],
            formats={
                planes.A_10C_2.id: CoordFormat.DDM,
                planes.FA_18C_hornet.id: CoordFormat.MGRS,
            },
            default_format=CoordFormat.DMS,
        )
    )
    assert f'["{planes.A_10C_2.id}"] = "ddm",' in script
    assert f'["{planes.FA_18C_hornet.id}"] = "mgrs",' in script
    assert 'local default = "dms"' in script


def test_every_format_has_a_writer_and_a_label_in_the_script() -> None:
    """A member the script has no writer for answers with a Lua error, not a position."""
    lines = lua.source("jtac_coords.lua").splitlines()
    writers = next(ln for ln in lines if ln.startswith("  local writers"))
    labels = next(ln for ln in lines if ln.startswith("  local labels"))
    for fmt in CoordFormat:
        assert f"{fmt.value} = " in writers
        assert f"{fmt.value} = " in labels


def test_the_built_in_table_maps_the_grid_cockpits() -> None:
    """Only cockpits that take a grid typed in belong in the table."""
    assert set(COCKPIT_COORD_FORMAT.values()) == {CoordFormat.MGRS}


def test_the_unprompted_readout_is_off_unless_asked_for(
    mission: Mission, convoy: VehicleGroup
) -> None:
    """`nil`, not 0.0 — a zero would push the position at mission start."""
    script = script_of(
        arm_jtac_coords(mission, [CoordTarget(convoy, "Hammer 1-1", "the column")])
    )
    assert "local pushAt = nil" in script


def test_push_at_reaches_the_script_as_a_mission_time(
    mission: Mission, convoy: VehicleGroup
) -> None:
    script = script_of(
        arm_jtac_coords(
            mission,
            [CoordTarget(convoy, "Hammer 1-1", "the column")],
            push_at_s=315.0,
        )
    )
    assert "local pushAt = 315.0" in script


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"coalition": "green"}, "blue/red"),
        ({"duration_s": 0.0}, "must be positive"),
        ({"scan_s": -1.0}, "must be positive"),
        ({"push_at_s": -5.0}, "mission time"),
    ],
)
def test_bad_arguments_raise(
    mission: Mission, convoy: VehicleGroup, kwargs: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        arm_jtac_coords(
            mission, [CoordTarget(convoy, "Hammer 1-1", "the column")], **kwargs
        )


def test_no_targets_raises(mission: Mission) -> None:
    with pytest.raises(ValueError, match="at least one target"):
        arm_jtac_coords(mission, [])
