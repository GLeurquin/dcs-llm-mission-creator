"""Unit tests for `core/dtc.py`.

A `Mission(Caucasus())` is cheap and needs neither a DCS install nor the map
overlay, so these run in the default (fast) selection. They assert on the
*mechanism* — the cartridge lands in the package, the threat tab is marked for
upload, only player-flown Vipers carry it — not on any mission's threat
composition.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from dcs import planes
from dcs.drawing.drawings import StandardLayer
from dcs.drawing.polygon import Circle
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core import dtc
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_kit import mark_clients


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


def _flight(m: Mission, name: str, plane_type, size: int = 2):
    return m.flight_group_from_airport(
        m.country("USA"),
        name,
        plane_type,
        m.terrain.airports["Batumi"],
        group_size=size,
    )


def _viper(m: Mission, name: str = "Uzi", size: int = 2):
    flight = _flight(m, name, planes.F_16C_50, size)
    mark_clients(flight)
    return flight


def _points(count: int = 1) -> list[dtc.ThreatPoint]:
    return [
        dtc.ThreatPoint(Point(100_000.0 + 1_000.0 * i, 400_000.0, Caucasus()), dtc.SA_6)
        for i in range(count)
    ]


def _cartridge(m: Mission, tmp_path: Path, name: str = "THREATS") -> dict:
    """Save `m`, let the base class's writer run, and read the cartridge back."""
    miz = tmp_path / "test.miz"
    m.save(str(miz))
    dtc.write_cartridges(m, miz)
    with zipfile.ZipFile(miz) as zipf:
        return json.loads(zipf.read(f"DTC/{name}.dtc"))


def test_cartridge_lands_in_the_miz(mission: Mission, tmp_path: Path):
    _viper(mission)
    assert dtc.arm_hsd_threats(mission, _points()) == 1
    cartridge = _cartridge(mission, tmp_path)
    assert cartridge["type"] == planes.F_16C_50.id
    assert cartridge["data"]["terrain"] == "Caucasus"
    assert len(cartridge["data"]["MPD"]["THREAT_PTS"]) == 1


def test_threat_tab_is_marked_for_upload(mission: Mission, tmp_path: Path):
    """`mirror_*` is the editor's "do not upload tab data", and defaults to on.

    Left at its default the jet would take the cartridge and then decline to
    read the one tab this helper fills, which looks exactly like the feature
    not working.
    """
    _viper(mission)
    dtc.arm_hsd_threats(mission, _points())
    mpd = _cartridge(mission, tmp_path)["data"]["MPD"]
    assert mpd["mirror_THREAT_PTS"] is False
    assert mpd["mirror_NAV_PTS"] is True


def test_threat_row_carries_position_range_and_code(mission: Mission, tmp_path: Path):
    _viper(mission)
    position = Point(120_000.0, 420_000.0, Caucasus())
    dtc.arm_hsd_threats(
        mission, [dtc.ThreatPoint(position, dtc.SA_6, radius_m=12_000.0)]
    )
    row = _cartridge(mission, tmp_path)["data"]["MPD"]["THREAT_PTS"][0]
    assert (row["x"], row["y"]) == (position.x, position.y)
    assert row["radius"] == 12_000.0  # the briefed ring, not the published 25 km
    assert row["alt"] == dtc.SA_6.ceiling_m
    assert (row["def_num"], row["text"]) == (dtc.SA_6.def_num, "6")
    assert row["ring"] is True


def test_points_occupy_the_jets_threat_steerpoints(mission: Mission, tmp_path: Path):
    _viper(mission)
    dtc.arm_hsd_threats(mission, _points(3))
    rows = _cartridge(mission, tmp_path)["data"]["MPD"]["THREAT_PTS"]
    assert [row["id"] for row in rows] == [
        "THREAT_PTS56",
        "THREAT_PTS57",
        "THREAT_PTS58",
    ]
    assert [row["number"] for row in rows] == [1, 2, 3]


def test_only_player_flown_vipers_carry_the_cartridge(mission: Mission):
    player = _viper(mission)
    ai_viper = _flight(mission, "Weasel", planes.F_16C_50)
    hornet = _flight(mission, "Chevy", planes.FA_18C_hornet)
    mark_clients(hornet)
    dtc.arm_hsd_threats(mission, _points())
    assert all(unit.dtc["Cartridges"][0]["default"] for unit in player.units)
    assert all(unit.dtc["AutoLoad"] for unit in player.units)
    assert not any(hasattr(unit, "dtc") for unit in (*ai_viper.units, *hornet.units))


def test_unit_table_gets_a_dtc_key(mission: Mission):
    """pydcs has no `DTC` field, so the key only appears via `unit_extras`."""
    player = _viper(mission)
    dtc.arm_hsd_threats(mission, _points())
    assert player.units[0].dict()["DTC"]["Cartridges"][0]["name"] == "THREATS"


def test_no_threats_writes_no_cartridge(mission: Mission, tmp_path: Path):
    """How a veteran/ace mission comes out: `PlanOverlay` withholds every ring."""
    _viper(mission)
    assert dtc.arm_hsd_threats(mission, []) == 0
    miz = tmp_path / "test.miz"
    mission.save(str(miz))
    assert dtc.write_cartridges(mission, miz) == 0
    with zipfile.ZipFile(miz) as zipf:
        assert not [n for n in zipf.namelist() if n.startswith("DTC/")]


def test_oversubscribed_threat_slots_raise(mission: Mission):
    _viper(mission)
    with pytest.raises(ValueError, match="pre-planned threats"):
        dtc.arm_hsd_threats(mission, _points(dtc.MAX_POINTS + 1))


def test_no_viper_slot_raises(mission: Mission):
    """A mission whose player flies something else gets told, not ignored."""
    hog = _flight(mission, "Hawg", planes.A_10C_2)
    mark_clients(hog)
    with pytest.raises(ValueError, match="client slot"):
        dtc.arm_hsd_threats(mission, _points())


def test_cartridge_bytes_are_reproducible(mission: Mission, tmp_path: Path):
    """The whole `.miz` is meant to be byte-identical between builds."""
    _viper(mission)
    dtc.arm_hsd_threats(mission, _points(2))
    first, second = tmp_path / "a.miz", tmp_path / "b.miz"
    for path in (first, second):
        mission.save(str(path))
        dtc.write_cartridges(mission, path)
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        entry = "DTC/THREATS.dtc"
        assert a.read(entry) == b.read(entry)
        assert a.getinfo(entry).date_time == b.getinfo(entry).date_time


def test_briefed_follows_the_map_reveal(mission: Mission):
    """`briefed` is the join between the drawn plan and the cockpit.

    At `trained` the ring is coarsened and offset, and the cartridge has to
    carry *that* claim; at `ace` there is no ring, so there is nothing to load.
    """
    center = Point(100_000.0, 400_000.0, Caucasus())
    trained = PlanOverlay(mission, "trained").threat(
        center, radius=25_000.0, label="SA-6"
    )
    points = dtc.briefed(trained, dtc.SA_6)
    assert len(points) == 1
    assert points[0].position == trained[0] != center
    assert points[0].radius_m == trained[1] > 25_000.0

    ace = PlanOverlay(mission, "ace").threat(center, radius=25_000.0, label="SA-6")
    assert ace is None
    assert dtc.briefed(ace, dtc.SA_6) == []


def test_mobile_air_defense_draws_no_envelope(mission: Mission):
    """Air defence that drives gets a mark, not a ring — and no cartridge point.

    A ring at the column's spawn claims reach over ground its SHORAD has
    already left, and a pre-planned point would freeze that claim in the
    cockpit for the whole sortie. `mobile_threat` returns nothing, so there is
    nothing for `briefed` to load.
    """
    plan = PlanOverlay(mission, "trained")
    layer = mission.drawings.get_layer(StandardLayer.Blue)
    assert plan.mobile_threat(Point(1.0, 2.0, Caucasus()), "Convoy SHORAD") is None
    assert not [obj for obj in layer.objects if isinstance(obj, Circle)]

    plan.threat(Point(1.0, 2.0, Caucasus()), radius=8_000.0, label="SA-6")
    assert [obj for obj in layer.objects if isinstance(obj, Circle)]


def test_threat_code_is_validated(mission: Mission):
    point = dtc.ThreatPoint(Point(1.0, 2.0, Caucasus()), dtc.SA_6, code="SA-6")
    with pytest.raises(ValueError, match="alphanumeric"):
        point.label()
    assert dtc.ThreatPoint(Point(1.0, 2.0, Caucasus()), dtc.SA_6).label() == "6"


def test_system_table_matches_the_jets_own_rows():
    """`def_num` indexes the jet's table; a duplicate would mislabel a ring."""
    systems = [
        value for value in vars(dtc).values() if isinstance(value, dtc.ThreatSystem)
    ]
    assert len(systems) >= 20
    assert len({system.def_num for system in systems}) == len(systems)
    assert all(1 <= system.def_num <= 29 for system in systems)
