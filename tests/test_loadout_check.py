"""`core/loadout_check` — a store on a station the game itself does not use.

pydcs only checks that a station *accepts* a store, which is a low bar: it
allows an AMRAAM on an F-16C's station 2 and a Sidewinder on its wingtip, and
that pair was the wrong way round in every mission here until somebody read
ED's payload tables. These tests pin the parsing of those tables and the three
answers the check can give, against a fixture rather than an install — CI has
no DCS.
"""

from __future__ import annotations

import pytest

from dcs_mission_creator.core import dcs_install, loadout_check

#: One payload block in the shape the game writes them: the keys interleaved,
#: `["num"]` following its `["CLSID"]`, several loadouts per file.
PAYLOAD_LUA = """
local unitPayloads = {
    ["unitType"] = "Test-1",
    ["payloads"] = {
        [1] = {
            ["name"] = "AMRAAM*2, FUEL*2",
            ["pylons"] = {
                [1] = { ["CLSID"] = "{AMRAAM}", ["num"] = 1 },
                [2] = { ["CLSID"] = "{TANK}", ["num"] = 4 },
                [3] = { ["CLSID"] = "{TANK}", ["num"] = 6 },
                [4] = { ["CLSID"] = "{AMRAAM}", ["num"] = 9 },
            },
        },
        [2] = {
            ["name"] = "POD",
            ["pylons"] = {
                [1] = { ["CLSID"] = "{LITENING}", ["num"] = 11 },
            },
        },
    },
}
return unitPayloads
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    """`ed_stations` memoises per airframe; a test must not see another's install."""
    loadout_check.ed_stations.cache_clear()
    yield
    loadout_check.ed_stations.cache_clear()


@pytest.fixture
def table(tmp_path) -> dict[str, frozenset[int]]:
    path = tmp_path / "Test-1.lua"
    path.write_text(PAYLOAD_LUA)
    found: dict[str, set[int]] = {}
    loadout_check._read_payloads(path, found)
    return {clsid: frozenset(stations) for clsid, stations in found.items()}


# -- reading the game's tables ----------------------------------------------


def test_every_store_collects_the_stations_it_is_shipped_on(table):
    assert table["{AMRAAM}"] == frozenset({1, 9})
    assert table["{TANK}"] == frozenset({4, 6})
    assert table["{LITENING}"] == frozenset({11})


def test_payloads_accumulate_across_blocks_rather_than_overwriting(table):
    """Two loadouts in one file are two sources of evidence, not the later one."""
    assert 11 in table["{LITENING}"]
    assert 1 in table["{AMRAAM}"]


def test_no_install_means_no_opinion(monkeypatch):
    """Without the game there is nothing to read, and guessing would be worse."""
    monkeypatch.setattr(dcs_install, "install_dir", lambda: None)
    assert loadout_check.ed_stations("F-16C_50") == {}


# -- what the check says -----------------------------------------------------


def test_a_store_on_a_station_the_game_uses_for_it_is_silent(table):
    assert loadout_check._station_note(table, 1, "{AMRAAM}", "AMRAAM") is None


def test_a_store_on_the_wrong_station_names_the_right_ones(table):
    note = loadout_check._station_note(table, 4, "{AMRAAM}", "AMRAAM")
    assert note is not None
    assert "station(s) 1, 9" in note.message


def test_an_unshipped_store_names_what_the_game_puts_there_instead(table):
    """The finding that made this worth writing: a working alternative, not an error."""
    note = loadout_check._station_note(table, 11, "{SNIPER}", "Sniper ATP")
    assert note is not None
    assert "no shipped payload carries it" in note.message
    assert "{LITENING}" in note.message  # no weapon_ids entry, so the raw id


def test_an_unshipped_store_on_a_station_nothing_uses_says_only_that(table):
    note = loadout_check._station_note(table, 7, "{ROCKETS}", "rockets")
    assert note is not None
    assert note.message == "no shipped payload carries this store"


def test_note_renders_as_one_readable_line(table):
    note = loadout_check._station_note(table, 4, "{AMRAAM}", "AMRAAM")
    assert str(note).startswith("station 4: AMRAAM — ")


# -- joining pydcs to the tables --------------------------------------------


def test_clsid_for_resolves_a_pydcs_store_name():
    from dcs import planes

    clsid = loadout_check.clsid_for(
        planes.F_16C_50, 1, "AIM_120C_AMRAAM___Active_Radar_AAM"
    )
    assert clsid and clsid.startswith("{")


def test_clsid_for_returns_none_for_a_station_that_does_not_take_it():
    from dcs import planes

    assert (
        loadout_check.clsid_for(planes.F_16C_50, 5, "GBU_12___500lb_Laser_Guided_Bomb")
        is None
    )


def test_check_is_empty_without_an_install(monkeypatch):
    from dcs import planes

    monkeypatch.setattr(dcs_install, "install_dir", lambda: None)
    assert (
        loadout_check.check(
            planes.F_16C_50, [(2, "AIM_120C_AMRAAM___Active_Radar_AAM")]
        )
        == []
    )
