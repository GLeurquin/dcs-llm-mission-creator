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
from dcs.mission import Mission, StartType
from dcs.task import CAP
from dcs.terrain import Caucasus

from dcs_mission_creator.core import dtc, mission_kit, waypoints
from dcs_mission_creator.core.kneeboard.flightplan import flight_plan
from dcs_mission_creator.core.loadout import Loadout
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_kit import mark_clients

#: A clean two-fit table. These tests are about how the flight is *built*, not
#: what it carries, so the fits differ only enough to be two of them.
_BARE_FITS = (
    Loadout(role="clean", carries="nothing", stores=()),
    Loadout(role="clean 2", carries="nothing", stores=()),
)


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

    Every difficulty above `recruit` coarsens and offsets the ring, and the
    cartridge has to carry *that* claim rather than the site's true position —
    a pre-planned threat point is a steerpoint, and a steerpoint on the truth
    hands the player a fix the map deliberately refused them.
    """
    center = Point(100_000.0, 400_000.0, Caucasus())
    for difficulty in ("trained", "veteran", "ace"):
        drawn = PlanOverlay(mission, difficulty).threat(
            center, radius=25_000.0, label="SA-6"
        )
        points = dtc.briefed(drawn, dtc.SA_6)
        assert len(points) == 1, difficulty
        assert drawn is not None
        assert points[0].position == drawn[0] != center, difficulty
        assert points[0].radius_m == drawn[1] > 25_000.0, difficulty


def test_briefed_loads_nothing_for_a_site_the_mission_never_drew(mission: Mission):
    """The empty case is the one `briefed` returns a list for."""
    assert dtc.briefed(None, dtc.SA_6) == []


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


def test_arming_the_cartridge_records_the_briefed_picture(mission: Mission):
    """The cartridge is the Viper's copy of it; `core/kneeboard` prints the rest."""
    _viper(mission)
    assert dtc.briefed_threats(mission) == []
    points = _points(2)
    dtc.arm_hsd_threats(mission, points)
    assert dtc.briefed_threats(mission) == points


def test_a_recorded_picture_needs_no_viper(mission: Mission):
    """A package with no Viper still briefs rings, and still gets the card."""
    points = _points(1)
    dtc.record_briefed(mission, points)
    assert dtc.briefed_threats(mission) == points


def test_the_map_label_rides_along_but_never_reaches_the_jet(mission: Mission):
    """Three characters go to the HSD; the briefing's own name goes on paper."""
    estimate = (Point(1.0, 2.0, Caucasus()), 25_000.0)
    (point,) = dtc.briefed(estimate, dtc.SA_6, label="SA-6 belt")
    assert point.title() == "SA-6 BELT"
    assert point.hsd_code() == "6"
    assert dtc.briefed(None, dtc.SA_6, label="SA-6 belt") == []


def test_an_unlabelled_point_names_itself_off_the_system(mission: Mission):
    assert dtc.ThreatPoint(Point(1.0, 2.0, Caucasus()), dtc.SA_6).title() == (
        "SA-6 'GAINFUL'"
    )


def test_threat_code_is_validated(mission: Mission):
    point = dtc.ThreatPoint(Point(1.0, 2.0, Caucasus()), dtc.SA_6, code="SA-6")
    with pytest.raises(ValueError, match="alphanumeric"):
        point.hsd_code()
    assert dtc.ThreatPoint(Point(1.0, 2.0, Caucasus()), dtc.SA_6).hsd_code() == "6"


def test_system_table_matches_the_jets_own_rows():
    """`def_num` indexes the jet's table; a duplicate would mislabel a ring."""
    systems = [
        value for value in vars(dtc).values() if isinstance(value, dtc.ThreatSystem)
    ]
    assert len(systems) >= 20
    assert len({system.def_num for system in systems}) == len(systems)
    assert all(1 <= system.def_num <= 29 for system in systems)


# -- steerpoints and GEO lines ----------------------------------------------
#
# The other two navigation tabs. Same rule as above: these assert the mechanism
# — that the route survives being uploaded, that the reveal policy is not gone
# round, that a tab nobody armed stays mirrored — never a mission's composition.


class _Bounds:
    bottom, top, left, right = -1e9, 1e9, -1e9, 1e9


class _Manifest:
    bounds = _Bounds()


class _Overlay:
    """The two things `waypoints.ground_elevation_m` asks an overlay for."""

    theater = "Caucasus"
    manifest = _Manifest()

    @staticmethod
    def elevation_at(_position: Point) -> float:
        return 123.0


def _routed_viper(m: Mission, legs: int = 3):
    """A Viper with a full airfield-to-airfield route, as a mission builds one."""
    batumi = m.terrain.airports["Batumi"]
    flight = _viper(m)
    flight.add_runway_waypoint(batumi)
    for leg in range(legs):
        flight.add_waypoint(
            batumi.position.point_from_heading(45.0, 20_000.0 * (leg + 1)),
            altitude=6_000,
            speed=800,
            name=f"INGRESS-{leg + 1}",
        )
    flight.add_runway_waypoint(batumi)
    flight.land_at(batumi)
    return flight


def _nav_tab(m: Mission, tmp_path: Path) -> dict:
    return _cartridge(m, tmp_path)["data"]["MPD"]


def _drawn_plan(m: Mission) -> PlanOverlay:
    plan = PlanOverlay(m, "trained")
    here = m.terrain.airports["Batumi"].position
    plan.objective(here.point_from_heading(45.0, 90_000.0), "AO — convoy axis")
    plan.frontline(
        [here.point_from_heading(90.0, offset) for offset in (-40_000, 0, 40_000)],
        "FRONT LINE",
    )
    plan.waypoint_label(here.point_from_heading(90.0, 5_000.0), "SEAM")
    plan.orbit(
        here.point_from_heading(0.0, 30_000.0),
        here.point_from_heading(0.0, 60_000.0),
        "Magic AWACS",
    )
    return plan


def test_the_route_survives_being_uploaded(mission: Mission, tmp_path: Path):
    """Uploading a steerpoint tab replaces the flight plan, so it has to be in it.

    `mirror_NAV_PTS` defaults to on precisely so a half-filled cartridge cannot
    wipe the route DCS put in the cockpit; turning it off is only safe because
    the route is the first thing the tab contains.
    """
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    mpd = _nav_tab(mission, tmp_path)
    assert mpd["mirror_NAV_PTS"] is False
    route = mpd["NAV_PTS"][: len(flight.points)]
    assert [(row["x"], row["y"]) for row in route] == [
        (point.position.x, point.position.y) for point in flight.points
    ]
    assert [row["id"] for row in route[:2]] == ["STPT1", "STPT2"]


def test_the_route_is_flagged_into_navigation_route_1(mission: Mission, tmp_path: Path):
    """`R1` is what the HSD draws its route line from — without it there is none.

    Uploading a steerpoint tab with every route flag clear gives the pilot the
    points and no flight plan, which is a regression against the mirrored
    default rather than an addition to it. The plan's own marks stay off every
    route: a tanker station is a place to look at, not a leg, and flagging one
    would bend the drawn route out to it.
    """
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    rows = _nav_tab(mission, tmp_path)["NAV_PTS"]
    assert [row["R1"] for row in rows] == [True] * len(flight.points) + [False] * 3
    assert not any(row["R2"] or row["R3"] for row in rows)


def test_plan_marks_land_after_the_route(mission: Mission, tmp_path: Path):
    """The objective becomes a TGT; a text label and an orbit become steerpoints."""
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    extra = _nav_tab(mission, tmp_path)["NAV_PTS"][len(flight.points) :]
    assert [row["note"] for row in extra] == ["AO — convoy axis", "SEAM", "Magic AWACS"]
    assert [row["type"] for row in extra] == ["TGT", "STPT", "STPT"]


def test_steerpoints_carry_terrain_elevation(mission: Mission, tmp_path: Path):
    """`alt` is the ground under the point — the jet reads it as site elevation."""
    _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    rows = _nav_tab(mission, tmp_path)["NAV_PTS"]
    assert {row["alt"] for row in rows} == {123.0}


def test_the_route_is_re_read_after_the_finishing_steps(
    mission: Mission, tmp_path: Path
):
    """`build_miz` corrects take-off, landing and departure *after* `_assemble`.

    A tab frozen when the mission armed it would carry the sea-level take-off
    pydcs hard-codes and the 108 km/h departure speed, which are the two defects
    those finishing steps exist to remove.
    """
    _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    waypoints.snap_base_waypoints(mission, _Overlay())
    waypoints.set_departure_speeds(mission)
    rows = _nav_tab(mission, tmp_path)["NAV_PTS"]
    assert rows[0]["routeAltitude"] == 123.0  # take-off, snapped to the field
    assert rows[1]["speed"] == 800.0  # departure gate, no longer 108 kt
    assert all(row["speed"] > 0.0 for row in rows)


def test_route_steerpoints_carry_a_zulu_time_over_steerpoint(
    mission: Mission, tmp_path: Path
):
    """`TOS` is seconds past **zulu** midnight, not past the local one.

    `Mission.start_time` is local and the editor's own DTC manager subtracts the
    theatre offset before it computes anything, because the jet's clock runs on
    zulu. Caucasus is UTC+4, so an 09:30 local take-off is 05:30 in the
    cartridge; getting this wrong is a whole-hours error that still looks like a
    plausible time.
    """
    mission.start_time = mission.start_time.replace(hour=9, minute=30, second=0)
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    rows = _nav_tab(mission, tmp_path)["NAV_PTS"]
    route = rows[: len(flight.points)]
    assert route[0]["TOS"] == 5 * 3600 + 30 * 60
    assert all(row["isTOSEnabled"] for row in route)
    # A schedule only runs forwards, and nothing is pinned to a fixed time —
    # `FIX_Time` would make the editor derive the speeds back off these.
    assert [row["TOS"] for row in route] == sorted(row["TOS"] for row in route)
    assert not any(row["FIX_Time"] for row in rows)


def test_the_cartridge_and_the_kneeboard_tell_one_schedule(
    mission: Mission, tmp_path: Path
):
    """A steerpoint's `TOS` is the route card's own `ETA` for the same point."""
    mission.start_time = mission.start_time.replace(hour=9, minute=30, second=0)
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    route = _nav_tab(mission, tmp_path)["NAV_PTS"][: len(flight.points)]
    takeoff = dtc.takeoff_zulu_s(mission, flight)
    assert [row["TOS"] for row in route] == [
        round(takeoff + leg.elapsed_s) for leg in flight_plan(flight)
    ]


def test_plan_marks_are_left_unscheduled(mission: Mission, tmp_path: Path):
    """Nothing planned a time over a seam or a tanker station, so none is written.

    `-1` with the checkbox clear is the editor's own "this point has no time"
    state; a number there would be a schedule the mission never promised.
    """
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    extra = _nav_tab(mission, tmp_path)["NAV_PTS"][len(flight.points) :]
    assert extra and all(row["TOS"] == -1 for row in extra)
    assert not any(row["isTOSEnabled"] for row in extra)


def test_the_schedule_is_re_read_with_the_route(mission: Mission, tmp_path: Path):
    """The times ride on the same re-read the speeds do.

    A `TOS` worked out while the departure gate still carried pydcs's 108 kt
    would put the whole schedule minutes late, which is the same defect as the
    speed itself and invisible in a way the speed is not.
    """
    flight = _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    before = _nav_tab(mission, tmp_path)["NAV_PTS"][len(flight.points) - 1]["TOS"]
    waypoints.set_departure_speeds(mission)
    after = _nav_tab(mission, tmp_path)["NAV_PTS"][len(flight.points) - 1]["TOS"]
    assert after < before


def test_a_front_line_becomes_a_red_geo_line(mission: Mission, tmp_path: Path):
    """The one piece of enemy geometry with a shape, and nothing else carries it.

    It also goes first, ahead of the orbit `_drawn_plan` draws after it: the
    order the lines arrive in is what decides which survives an oversubscribed
    tab, and a front line is never the one to lose.
    """
    _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    mpd = _nav_tab(mission, tmp_path)
    assert mpd["mirror_GEO_LINES"] is False
    front = [row for row in mpd["GEO_LINES"] if row["L3"]]  # L3 is the red line
    assert len(front) == 3
    assert [row["id"] for row in front] == [
        "GEO_LINES31",
        "GEO_LINES32",
        "GEO_LINES33",
    ]
    assert [row["note"] for row in front] == ["FRONT LINE", "", ""]


def test_an_orbit_is_both_a_steerpoint_and_a_line(mission: Mission, tmp_path: Path):
    """The point carries range and bearing; the line carries the shape."""
    _routed_viper(mission)
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    mpd = _nav_tab(mission, tmp_path)
    track = [row for row in mpd["GEO_LINES"] if not row["L3"]]
    assert [row["note"] for row in track] == ["Magic AWACS", ""]
    assert all(row["L4"] for row in track)  # L4 is green — the friendly plan
    assert "Magic AWACS" in [row["note"] for row in mpd["NAV_PTS"]]


def test_the_flights_own_corridor_is_not_drawn_twice(mission: Mission, tmp_path: Path):
    """A `route` line over the flight's own waypoints is the steerpoints again."""
    flight = _routed_viper(mission)
    plan = PlanOverlay(mission, "trained")
    plan.route([point.position for point in flight.points[2:5]], "Uzi ingress")
    dtc.arm_plan(mission, plan, overlay=_Overlay())
    assert _nav_tab(mission, tmp_path)["GEO_LINES"] == []


def test_a_corridor_the_flight_does_not_fly_is_drawn(mission: Mission, tmp_path: Path):
    _routed_viper(mission)
    plan = PlanOverlay(mission, "trained")
    far = mission.terrain.airports["Kutaisi"].position
    plan.route([far.point_from_heading(0.0, m) for m in (0.0, 30_000.0)], "Ford lane")
    dtc.arm_plan(mission, plan, overlay=_Overlay())
    rows = _nav_tab(mission, tmp_path)["GEO_LINES"]
    assert len(rows) == 2
    assert all(row["L4"] for row in rows)  # L4 is green — the friendly plan


def test_geo_lines_are_thinned_to_the_jets_budget(mission: Mission, tmp_path: Path):
    """Twenty-five vertices across four lines, and both ends of each are kept."""
    _routed_viper(mission)
    plan = PlanOverlay(mission, "trained")
    here = mission.terrain.airports["Batumi"].position
    trace = [here.point_from_heading(90.0, 1_000.0 * step) for step in range(40)]
    plan.frontline(trace, "FRONT LINE")
    dtc.arm_plan(mission, plan, overlay=_Overlay())
    rows = _nav_tab(mission, tmp_path)["GEO_LINES"]
    assert len(rows) == dtc.MAX_GEO_POINTS
    assert (rows[0]["x"], rows[0]["y"]) == (trace[0].x, trace[0].y)
    assert (rows[-1]["x"], rows[-1]["y"]) == (trace[-1].x, trace[-1].y)


def test_briefed_threats_are_not_spent_on_steerpoints(mission: Mission, tmp_path: Path):
    """They are already the cartridge's pre-planned threats; a copy buys nothing."""
    flight = _routed_viper(mission)
    plan = PlanOverlay(mission, "trained")
    plan.threat(
        mission.terrain.airports["Batumi"].position.point_from_heading(45.0, 80_000.0),
        radius=25_000.0,
        label="SA-6 belt",
    )
    dtc.arm_plan(mission, plan, overlay=_Overlay())
    assert len(_nav_tab(mission, tmp_path)["NAV_PTS"]) == len(flight.points)


def test_the_plan_lands_in_the_same_cartridge_as_the_threats(
    mission: Mission, tmp_path: Path
):
    """One cartridge, filled a tab at a time — the jet loads one default."""
    _routed_viper(mission)
    dtc.arm_hsd_threats(mission, _points(), overlay=_Overlay())
    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    mpd = _nav_tab(mission, tmp_path)
    assert len(mpd["THREAT_PTS"]) == 1
    assert mpd["NAV_PTS"] and mpd["GEO_LINES"]
    assert mpd["mirror_DEST"] is True  # a tab nobody armed stays mirrored


def test_two_player_viper_flights_raise(mission: Mission):
    """One steerpoint tab, every Viper slot loads it — two routes will not fit."""
    _routed_viper(mission)
    second = _viper(mission, name="Dodge")
    second.add_runway_waypoint(mission.terrain.airports["Batumi"])
    with pytest.raises(ValueError, match="player Viper flights"):
        dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())


def test_two_sections_of_one_flight_do_not_raise(mission: Mission, tmp_path: Path):
    """Six coop slots are two DCS groups flying one route, not two flights.

    The tab that fits the lead section fits the second, so the guard above has
    to look at what the groups *are* rather than at how many there are.
    """
    batumi = mission.terrain.airports["Batumi"]
    sections = mission_kit.player_flight(
        mission,
        country=mission.country("USA"),
        name="Uzi",
        aircraft_type=planes.F_16C_50,
        airport=batumi,
        maintask=CAP,
        start_type=StartType.Warm,
        slots=6,
        loadouts=_BARE_FITS,
    )
    for flight in sections:
        flight.add_runway_waypoint(batumi)
        flight.add_waypoint(
            batumi.position.point_from_heading(45.0, 20_000.0),
            altitude=6_000,
            speed=800,
            name="INGRESS-1",
        )
        flight.add_runway_waypoint(batumi)
        flight.land_at(batumi)

    dtc.arm_plan(mission, _drawn_plan(mission), overlay=_Overlay())
    assert _nav_tab(mission, tmp_path)["NAV_PTS"]
