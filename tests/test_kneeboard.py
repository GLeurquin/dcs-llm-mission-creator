"""Unit tests for `core/kneeboard/`.

A `Mission(Caucasus())` is cheap and needs neither a DCS install nor the map
overlay, so these run in the default (fast) selection. They assert on the
*mechanism* — the legs come off the route, the frequency the AI actually uses
wins over pydcs's default, the archive entry has the path DCS expects, the pages
are byte-identical between builds — never on a mission's composition.

The beacon reader is tested against a hand-written `Beacons.lua` fragment rather
than the installed game, since CI has no DCS: the parser and the axis swap are
the parts that can break, and both are visible in four records.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from dcs import planes, task
from dcs.mission import Mission, StartType
from dcs.terrain import Caucasus

from dcs_mission_creator.core import kneeboard as kb
from dcs_mission_creator.core.kneeboard import (
    airfields,
    beacons,
    charts,
    comms,
    flightplan,
)
from dcs_mission_creator.core.kneeboard.page import Column, Page
from dcs_mission_creator.core.mission_kit import mark_clients


@pytest.fixture
def mission() -> Mission:
    m = Mission(Caucasus())
    m.terrain.airports["Batumi"].set_blue()
    m.terrain.airports["Kutaisi"].set_blue()
    return m


def _viper(m: Mission, name: str = "Dodge", size: int = 2):
    batumi = m.terrain.airports["Batumi"]
    flight = m.flight_group_from_airport(
        m.country("USA"),
        name,
        planes.F_16C_50,
        batumi,
        maintask=task.CAP,
        start_type=StartType.Warm,
        group_size=size,
    )
    mark_clients(flight)
    flight.add_runway_waypoint(batumi)
    flight.add_waypoint(
        batumi.position.point_from_heading(0, 100_000),
        altitude=6_000,
        speed=800,
        name="STATION",
    )
    flight.add_runway_waypoint(batumi)
    flight.land_at(m.terrain.airports["Kutaisi"])
    return flight


# -- the route ---------------------------------------------------------------


def test_legs_measure_the_route(mission: Mission) -> None:
    legs = flightplan.flight_plan(_viper(mission))

    assert legs[0].track_true is None and legs[0].ete_s is None
    station = next(leg for leg in legs if leg.name == "STATION")
    assert station.tas_kph == pytest.approx(800.0)
    # 800 km/h over the leg it flew, in seconds.
    assert station.ete_s == pytest.approx(station.leg_m / (800.0 / 3.6))
    assert legs[-1].total_m > legs[0].total_m
    assert [leg.number for leg in legs] == list(range(1, len(legs) + 1))


def test_the_unnamed_gates_are_named(mission: Mission) -> None:
    names = [leg.name for leg in flightplan.flight_plan(_viper(mission))]
    assert names[1] == "DEP GATE"
    assert "APCH GATE" in names
    assert not any(name.startswith("WP ") for name in names)


def test_a_radio_altitude_is_flagged_not_converted(mission: Mission) -> None:
    gate = flightplan.flight_plan(_viper(mission))[1]
    assert gate.agl is True


def test_magnetic_needs_a_known_theater() -> None:
    assert flightplan.variation_deg("Caucasus") is not None
    assert flightplan.variation_deg("Atlantis") is None
    assert flightplan.magnetic(10.0, 6.0) == pytest.approx(4.0)
    assert flightplan.magnetic(2.0, 6.0) == pytest.approx(356.0)
    assert flightplan.magnetic(10.0, None) is None


def test_time_and_coordinate_formats(mission: Mission) -> None:
    assert flightplan.hms(None) == "--"
    assert flightplan.hms(65) == "01:05"
    assert flightplan.hms(3_725) == "1:02:05"
    text = flightplan.ddm(mission.terrain.airports["Batumi"].position)
    assert text.startswith("N 41 ")
    assert " E 041 " in text


# -- comms -------------------------------------------------------------------


def test_the_player_flight_is_marked_and_listed_first(mission: Mission) -> None:
    _viper(mission)
    mission.awacs_flight(
        mission.country("USA"),
        "Magic",
        plane_type=planes.E_3A,
        airport=mission.terrain.airports["Batumi"],
        position=mission.terrain.airports["Batumi"].position.point_from_heading(
            270, 60_000
        ),
        frequency=251,
    )
    stations = comms.stations(mission)
    assert stations[0].player is True
    assert stations[0].callsign == "Dodge"
    assert [s.player for s in stations].count(True) == 1


def test_a_set_frequency_task_beats_the_pydcs_default(mission: Mission) -> None:
    flight = _viper(mission)
    flight.frequency = 251
    flight.points[0].tasks.append(task.SetFrequencyCommand(377, task.Modulation.AM))
    station = next(s for s in comms.stations(mission) if s.callsign == "Dodge")
    assert station.frequency_mhz == pytest.approx(377.0)


def test_a_tanker_reports_its_tacan(mission: Mission) -> None:
    _viper(mission)
    batumi = mission.terrain.airports["Batumi"]
    mission.refuel_flight(
        mission.country("USA"),
        "Texaco",
        planes.KC_135,
        airport=batumi,
        position=batumi.position.point_from_heading(180, 60_000),
        frequency=270,
        tacanchannel="10X",
    )
    station = next(s for s in comms.stations(mission) if s.callsign == "Texaco")
    assert station.tacan is not None and station.tacan.startswith("10X")
    assert station.frequency_mhz == pytest.approx(270.0)


def test_a_fac_is_a_controller_not_a_flight(mission: Mission) -> None:
    _viper(mission)
    batumi = mission.terrain.airports["Batumi"]
    fac = mission.flight_group_from_airport(
        mission.country("USA"),
        "Hammer",
        planes.MQ_9_Reaper,
        batumi,
        maintask=task.AFAC,
        start_type=StartType.Warm,
    )
    fac.points[0].tasks.append(
        task.FACEngageGroup(1, frequency=133, modulation=task.Modulation.AM)
    )
    station = next(s for s in comms.stations(mission) if s.callsign == "Hammer")
    assert station.controller is True
    assert station.role.startswith("FAC(A)")
    assert station.frequency_mhz == pytest.approx(133.0)


def test_a_mission_frequency_is_matched_against_the_preset_table(
    mission: Mission,
) -> None:
    """251 AM is channel 18 on the Viper's own UHF preset table."""
    flight = _viper(mission)
    flight.frequency = 251
    station = next(s for s in comms.stations(mission) if s.callsign == "Dodge")
    assert station.preset == "R1 CH18"


def test_atc_channels_come_from_pydcs(mission: Mission) -> None:
    batumi = mission.terrain.airports["Batumi"]
    channel = comms.atc_channels(mission, [batumi])[0]
    assert channel.airfield == "Batumi"
    assert channel.uhf_mhz == pytest.approx(batumi.atc_radio.uhf_hz / 1e6)


# -- airfields ---------------------------------------------------------------


def test_relevant_airfields_are_the_ones_flown_from_and_to(mission: Mission) -> None:
    _viper(mission)
    names = [a.name for a in airfields.relevant_airfields(mission)]
    assert names == ["Batumi", "Kutaisi"]


def test_a_card_knows_where_the_flight_parks(mission: Mission) -> None:
    flight = _viper(mission)
    card = next(
        c for c in airfields.airfield_cards(mission) if c.airport.name == "Batumi"
    )
    spawn = card.spawn_of("Dodge")
    assert spawn is not None
    assert spawn.player is True
    assert spawn.start == "HOT"
    assert spawn.slots == tuple(u.parking_id for u in flight.units)
    assert card.landings == ()


def test_the_recovery_field_is_the_one_landed_at(mission: Mission) -> None:
    _viper(mission)
    card = next(
        c for c in airfields.airfield_cards(mission) if c.airport.name == "Kutaisi"
    )
    assert card.landings == ("Dodge",)
    assert card.spawns == ()


# -- the beacon reader -------------------------------------------------------

_BEACONS_LUA = """
beaconsTableFormat = 2
beacons = {
    {
        display_name = _('Batumi');
        beaconId = 'airfield22_0';
        type = BEACON_TYPE_ILS_LOCALIZER;
        callsign = 'ILU';
        frequency = 110300000.000000;
        position = { -356584.812500, 10.030140, 618472.437500 };
        direction = -54.415131;
        positionGeo = { latitude = 41.601731, longitude = 41.612203 };
        sceneObjects = {'t:43163771'};
    };
    {
        display_name = _('Batumi');
        beaconId = 'airfield22_2';
        type = BEACON_TYPE_TACAN;
        callsign = 'BTM';
        frequency = 977000000.000000;
        channel = 16;
        position = { -355664.406250, 10.044037, 617386.812500 };
        direction = 0.000000;
    };
    {
        display_name = _('Kobuleti');
        beaconId = 'airfield18_0';
        type = BEACON_TYPE_HOMER;
        callsign = 'T';
        frequency = 995000.000000;
        position = { -318000.0, 10.0, 636000.0 };
    };
    {
        display_name = _('Somewhere');
        beaconId = 'enroute_vor_1';
        type = BEACON_TYPE_VOR;
        callsign = 'KT';
        frequency = 113600000.000000;
        position = { -355800.0, 10.0, 617400.0 };
    };
}
"""


@pytest.fixture
def beacons_file(tmp_path: Path) -> Path:
    path = tmp_path / "Beacons.lua"
    path.write_text(_BEACONS_LUA)
    return path


def test_the_reader_takes_type_frequency_and_position(beacons_file: Path) -> None:
    parsed = {b.beacon_id: b for b in beacons._parse(beacons_file)}
    assert set(parsed) == {
        "airfield22_0",
        "airfield22_2",
        "airfield18_0",
        "enroute_vor_1",
    }
    ils = parsed["airfield22_0"]
    assert ils.kind == "ILS_LOCALIZER"
    assert ils.kind_label == "ILS LOC"
    assert ils.label == "110.30 MHZ"
    assert ils.airfield_id == 22
    assert ils.display_name == "Batumi"
    # `position = {x, altitude, z}` — north, up, east.
    assert (ils.x, ils.y) == pytest.approx((-356584.8125, 618472.4375))


def test_a_tacan_channel_yields_its_mode(beacons_file: Path) -> None:
    tacan = next(
        b for b in beacons._parse(beacons_file) if b.beacon_id == "airfield22_2"
    )
    assert tacan.channel == 16
    assert tacan.tacan_mode == "X"
    assert tacan.label == "CH 16X"


def test_a_homer_is_tuned_in_kilohertz(beacons_file: Path) -> None:
    homer = next(
        b for b in beacons._parse(beacons_file) if b.beacon_id == "airfield18_0"
    )
    assert homer.label == "995 KHZ"
    assert homer.airfield_id == 18


def test_an_enroute_beacon_has_no_airfield(beacons_file: Path) -> None:
    vor = next(
        b for b in beacons._parse(beacons_file) if b.beacon_id == "enroute_vor_1"
    )
    assert vor.airfield_id is None


# -- which fields the theatre already charts ---------------------------------

#: What ED actually ships, in miniature: Caucasus names every field in the file
#: name, Syria names three of thirty, and Beirut's file drops the city name.
_CHARTS = {
    "Caucasus": ["07_GND_UGSB_Batumi_18.png", "14_GND_URKL_Krasnodar_Center_25.png"],
    "Syria": ["Akrotiri_p1.png", "Incirlik_AB_p1.png", "Rafic_Hariri_Intl_p1.png"],
}


@pytest.fixture
def fake_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for theater, names in _CHARTS.items():
        folder = tmp_path / "Mods" / "terrains" / theater / "Kneeboard"
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).touch()
    monkeypatch.setenv("DCS_INSTALL_DIR", str(tmp_path))
    charts._chart_names.cache_clear()
    yield tmp_path
    charts._chart_names.cache_clear()


def test_a_charted_field_gets_no_card(fake_install: Path) -> None:
    terrain = Caucasus()
    assert charts.has_chart(terrain, terrain.airports["Batumi"]) is True


def test_an_uncharted_field_does(fake_install: Path) -> None:
    from dcs.terrain import Syria

    terrain = Syria()
    assert charts.has_chart(terrain, terrain.airports["Hatay"]) is False
    assert charts.has_chart(terrain, terrain.airports["Incirlik"]) is True


def test_every_word_of_the_name_has_to_match(fake_install: Path) -> None:
    """Krasnodar-Pashkovsky is not covered by Krasnodar-Center's chart."""
    terrain = Caucasus()
    assert charts.has_chart(terrain, terrain.airports["Krasnodar-Center"]) is True
    assert charts.has_chart(terrain, terrain.airports["Krasnodar-Pashkovsky"]) is False


def test_two_long_words_are_enough(fake_install: Path) -> None:
    """`Beirut-Rafic Hariri` against a file called `Rafic_Hariri_Intl`."""
    from dcs.terrain import Syria

    terrain = Syria()
    assert charts.has_chart(terrain, terrain.airports["Beirut-Rafic Hariri"]) is True


def test_a_theater_with_no_charts_folder_is_uncharted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Mods" / "terrains" / "Caucasus").mkdir(parents=True)
    monkeypatch.setenv("DCS_INSTALL_DIR", str(tmp_path))
    charts._chart_names.cache_clear()
    terrain = Caucasus()
    assert charts.has_chart(terrain, terrain.airports["Batumi"]) is False
    charts._chart_names.cache_clear()


def test_without_an_install_the_card_is_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unknown case resolves to "no chart" — see `charts.py` on why."""
    monkeypatch.delenv("DCS_INSTALL_DIR", raising=False)
    charts._chart_names.cache_clear()
    terrain = Caucasus()
    assert charts.has_chart(terrain, terrain.airports["Batumi"]) is False
    charts._chart_names.cache_clear()


# -- the page ----------------------------------------------------------------


def test_a_page_is_portrait_and_paginates_rather_than_clipping() -> None:
    page = Page(title="Test", label="flight plan")
    page.table(
        (Column("#", 4), Column("NAME", 20)), [(str(i), "x") for i in range(200)]
    )
    images = page.images()
    assert len(images) > 1
    assert all(image.size == (1536, 2048) for image in images)


def test_a_table_row_keeps_its_columns() -> None:
    page = Page(title="Test")
    page.table((Column("A", 5), Column("B", 5, ">")), [("x", "1")])
    header, row = (block.text for block in page.blocks)
    assert header == "A         B"
    assert row == "x         1"


# -- publishing --------------------------------------------------------------


def _saved(m: Mission, tmp_path: Path) -> Path:
    miz = tmp_path / "test_mission.miz"
    m.save(str(miz))
    return miz


@pytest.fixture
def charted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the chart-coverage answer, which otherwise depends on the host.

    Whether a field is charted is a fact about the *installed game*, so a test that
    read it would pass on a machine with DCS and fail in CI. Every publishing test
    states which branch it is exercising instead.
    """
    monkeypatch.setattr(charts, "has_chart", lambda terrain, airport: True)


@pytest.fixture
def uncharted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(charts, "has_chart", lambda terrain, airport: False)


def test_an_uncharted_field_earns_an_airfield_page(
    mission: Mission, tmp_path: Path, uncharted: None
) -> None:
    """What Hatay gets: Syria ships no chart for it, so the mission provides one."""
    _viper(mission)
    written = kb.publish(mission, _saved(mission, tmp_path), title="Test Mission")
    assert [p.name for p in written] == [
        "01-FLIGHT-PLAN.png",
        "02-COMMS.png",
        "03-AIRFIELD-BATUMI.png",
        "04-AIRFIELD-KUTAISI.png",
    ]


def test_pages_land_in_the_archive_under_the_player_airframe(
    mission: Mission, tmp_path: Path, charted: None
) -> None:
    _viper(mission)
    miz = _saved(mission, tmp_path)
    written = kb.publish(mission, miz, title="Test Mission")

    # Two cards: this field is one the theatre charts itself.
    assert [p.name for p in written] == ["01-FLIGHT-PLAN.png", "02-COMMS.png"]
    assert all(p.path.is_file() and p.path.stat().st_size > 0 for p in written)
    with zipfile.ZipFile(miz) as zipf:
        entries = [n for n in zipf.namelist() if n.startswith("KNEEBOARD/")]
    assert entries == [f"KNEEBOARD/F-16C_50/IMAGES/{p.name}" for p in written]
    # pydcs's own helper writes `IMAGES//name.png`; DCS is not asked to resolve it.
    assert not any("//" in name for name in entries)


def test_no_client_slot_writes_no_kneeboard(
    mission: Mission, tmp_path: Path, charted: None
) -> None:
    mission.flight_group_from_airport(
        mission.country("USA"),
        "AI only",
        planes.F_16C_50,
        mission.terrain.airports["Batumi"],
        maintask=task.CAP,
    )
    assert kb.publish(mission, _saved(mission, tmp_path)) == []


def test_a_remark_reaches_the_comms_card(
    mission: Mission, tmp_path: Path, charted: None
) -> None:
    _viper(mission)
    kb.remark(mission, "Hammer lases on 1688.")
    written = kb.publish(mission, _saved(mission, tmp_path))
    comms_page = next(p for p in written if "COMMS" in p.name)
    assert comms_page.path.stat().st_size > 0


def test_the_pages_are_reproducible(tmp_path: Path, uncharted: None) -> None:
    """Two builds of the same mission produce the same pixels and the same entry.

    The pages are files inside the `.miz`, so a timestamp anywhere in them would
    make the package differ build to build — which is why the zip entries carry a
    fixed date and the PNGs are written without a `tIME` chunk. Run over the
    uncharted branch, so the drawn plan view is covered too.
    """
    digests = []
    for run in ("a", "b"):
        m = Mission(Caucasus())
        m.terrain.airports["Batumi"].set_blue()
        m.terrain.airports["Kutaisi"].set_blue()
        _viper(m)
        out = tmp_path / run
        out.mkdir()
        written = kb.publish(m, _saved(m, out))
        digests.append([(p.name, p.path.read_bytes()) for p in written])
    assert digests[0] == digests[1]
