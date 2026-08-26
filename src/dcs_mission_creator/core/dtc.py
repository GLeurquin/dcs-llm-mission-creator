"""Briefed SAM rings on the F-16C's HSD (project-owned helper).

The Viper draws a ring for a surface-to-air threat in two places — the HSD's
pre-planned symbols and the HAD — and neither has anything to do with the RWR.
They come off the **data cartridge**: up to fifteen pre-planned threat points,
kept in steerpoints 56-70, each with a position, a three-character code and the
range and ceiling of the system that sits there. That is what a real flight
loads on the way to the jet, and without it the player's only cockpit picture of
a briefed SAM belt is a memory of the F10 map.

DCS builds the cartridge from `DTC/<name>.dtc` inside the `.miz` (JSON, written
by the Mission Editor's DTC manager) plus a per-unit `DTC` key listing which
cartridges that slot carries. pydcs models neither: `Mission.save` writes a
fixed set of zip entries (`mission`, `options`, `warehouses`, the l10n pair and
the kneeboard images) and `Unit.dict` has no `DTC` field. So this helper writes
both halves:

    from dcs_mission_creator.core import dtc

    points = [dtc.ThreatPoint(sa6_estimate, dtc.SA_6, radius_m=25_000.0)]
    dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)

`MissionBuilder.build_miz` writes the cartridge file itself, after the save, for
the same reason it snaps base waypoints and assigns datalink identities: the
file goes *into* the finished package, so a mission cannot be the thing that
remembers to do it.

Two design rules the API is shaped around:

- **Feed it the briefed position, not the true one.** `PlanOverlay.threat`
  returns the estimate it actually drew — coarsened and offset, further out the
  harder the mission — so passing that estimate straight through makes the
  cockpit ring the same claim as the F10 ring, and the difficulty policy stays
  in one place instead of being reimplemented here. A pre-planned threat *is* a
  steerpoint, which is why this matters more here than on the map: a point on
  the site's true position is a set of coordinates the player can read out of
  the DED, and it would undo a reveal policy the F10 map had just applied. An
  empty list writes no cartridge, for a mission that briefs no ring at all.
  The same points are recorded on the mission (`record_briefed` /
  `briefed_threats`), because the cartridge is only the Viper's copy of the
  briefed picture: `core/kneeboard` prints the identical list, with the
  identical coordinates, for whoever is not flying a Viper.
- **Emplaced sites only, and only ones the briefing names.** A ring the player
  was never briefed on is intel the mission did not claim to have, and the
  fifteen slots are better spent on belts than on every MANPADS in the AO.
  Mobile air defence — the 2S6 riding with a convoy, a SHORAD vehicle on a road
  march — is excluded on principle: a pre-planned point is a static claim, so
  the ring is wrong the moment the column moves, and it is worse than no ring
  because everywhere it no longer covers reads as clear. Mark those with
  `PlanOverlay.mobile_threat`, which draws no envelope and hands back nothing
  for `briefed` to load.

The F/A-18C is deliberately absent: it has a data cartridge too, but its
threats live on the SA page as `MEZ_THRTS` with a different descriptor and
different section names — a second table, not a parameter. The AH-64D and A-10C
have no pre-planned threat ring at all.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

import structlog
from dcs import planes
from dcs.unit import Skill

from dcs_mission_creator.core import unit_extras, waypoints

if TYPE_CHECKING:
    from dcs.flyingunit import FlyingUnit
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.core.map_draw import PlanOverlay
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: The only aircraft type this writes cartridges for; also the `type` field DCS
#: matches a cartridge against a slot with.
AIRCRAFT = planes.F_16C_50

#: Pre-planned threats occupy steerpoints 56-70, so the jet holds fifteen.
MAX_POINTS = 15

#: The navigation steerpoints, 1-25 — the jet's own flight plan.
MAX_NAV_POINTS = 25
FIRST_NAV_STEERPOINT = 1

#: The GEO line vertices, steerpoints 31-55: twenty-five points shared between
#: **four** polylines. A point declares its membership with an `L1`-`L4` flag
#: and the jet joins consecutive members of each flag, so a line is one
#: polyline and there is no fifth one to spend a point on.
MAX_GEO_POINTS = 25
FIRST_GEO_STEERPOINT = 31
GEO_LINE_COUNT = 4

#: Which of the four lines a drawing would rather be, most-wanted first. The
#: colours are the editor's own (`GEO_LINES.lua`): L1 white, L2 black, L3 red,
#: L4 green — so enemy geometry asks for red and the friendly plan for green,
#: then both fall back on white. What each asks for *last* is the other side's
#: colour, behind even black: a friendly track drawn in red reads as a threat,
#: and a line nobody can pick out is a smaller loss than a line that lies about
#: which side put it there.
_LINE_PREFERENCE = {True: (3, 1, 2, 4), False: (4, 1, 2, 3)}

#: A `route` line whose every vertex is this close to a steerpoint already being
#: written is the flight's own route drawn twice, and is dropped. Missions draw
#: the corridor they then fly, so this is the usual case, and the HSD already
#: joins the steerpoints.
_TRACED_M = 1_000.0

#: The first of them. Public because the player reads it in the cockpit and the
#: kneeboard's threat block prints it beside each site, so a ring on the HSD and
#: a line on the card can be matched up by number.
FIRST_STEERPOINT = 56
_FIRST_STEERPOINT = FIRST_STEERPOINT

#: The editor's own VIP/VRP defaults (`MPD/VIPVRP.lua`), written whenever the
#: steerpoint tab is filled. Both offsets stay disabled — nothing in this
#: project plans a visual initial or reference point — but the block has to be
#: *present*: the editor's steerpoint table reads `VIP_Number` unguarded when a
#: row is deleted, and against the empty table pydcs would otherwise leave
#: there, that is a nil comparison rather than a number.
_VIPVRP: dict[str, Any] = {
    "VIP_Number": 1,
    "VRP_Number": 1,
    "VIP_Enabled": False,
    "VIPPUP_Enabled": False,
    "VRP_Enabled": False,
    "VRPPUP_Enabled": False,
    **{
        f"{leg}_{field}": value
        for leg in ("VIPTOTGT", "VIPTOPUP", "TGTTOVRP", "TGTTOPUP")
        for field, value in (
            ("id", leg),
            ("LineName", f"{leg}_Line"),
            ("X", 0.0),
            ("Y", 0.0),
            ("DeltaX", 0.0),
            ("DeltaY", 0.0),
            ("Bearing", 0.0),
            ("Range", 0.0),
            ("Elevation", 0),
        )
    },
}

#: A fixed zip timestamp, so appending the cartridge keeps two builds of the
#: same mission byte-identical.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: Where `arm_hsd_threats` parks cartridges until the `.miz` exists.
_STASH = "dtc_cartridges"

#: Where `arm_plan` parks the steerpoint tab's *route*, to be re-read at write
#: time. See `_PendingRoute`.
_PENDING = "dtc_pending_routes"

#: Where the briefed threat points themselves are kept, for the consumers that
#: are not the Viper's cartridge — today `core/kneeboard`'s threat block.
_BRIEFED = "briefed_threats"


@dataclass(frozen=True)
class ThreatSystem:
    """One row of the jet's own threat table (`DTC/MPD/THREAT_PTS_defs.lua`).

    `def_num` is that row's index and `name` its exact label: the cartridge
    stores both, and the editor reads the point back through them. `radius_m`
    and `ceiling_m` are DCS's published envelope for the system — the ring the
    HSD draws and the altitude block the HAD reads — and `code` the up-to-three
    characters printed inside the ring.
    """

    def_num: int
    name: str
    code: str
    radius_m: float
    ceiling_m: float


# The land rows of `THREAT_PTS_defs`, verbatim. Naval rows (indices 30-51) are
# left out: no mission here fields a ship threat, and a wrong `def_num` would
# label the ring as the wrong system.
CUSTOM = ThreatSystem(1, "Custom", "CST", 37_040.0, 9_144.0)
FIRE_CAN = ThreatSystem(2, "AAA SON-9 - Fire Can", "FC", 20_000.0, 14_000.0)
C_RAM = ThreatSystem(3, "LPWS C-RAM", "CR", 2_000.0, 6_000.0)
AVENGER = ThreatSystem(4, "SAM Avenger", "AV", 4_500.0, 5_200.0)
CHAPARRAL = ThreatSystem(5, "SAM Chaparrel", "CH", 8_500.0, 10_000.0)
HAWK = ThreatSystem(6, "SAM Hawk", "HK", 45_000.0, 20_000.0)
HQ_7 = ThreatSystem(7, "SAM HQ-7", "7", 15_000.0, 5_500.0)
IRIS_T_SLM = ThreatSystem(8, "SAM IRIS-T SLM", "IT", 40_000.0, 40_000.0)
NASAMS = ThreatSystem(9, "SAM NASAMS", "NS", 15_000.0, 17_000.0)
PATRIOT = ThreatSystem(10, "SAM Patriot", "P", 100_000.0, 160_000.0)
RAPIER = ThreatSystem(11, "SAM Rapier", "RP", 6_800.0, 4_000.0)
ROLAND = ThreatSystem(12, "SAM Roland", "RO", 8_000.0, 6_000.0)
SA_2 = ThreatSystem(13, "SAM SA-2 'Guideline'", "2", 43_000.0, 25_000.0)
SA_3 = ThreatSystem(14, "SAM SA-3 'Goa'", "3", 18_000.0, 20_000.0)
SA_5 = ThreatSystem(15, "SAM SA-5 'Gammon'", "5", 255_000.0, 40_000.0)
SA_6 = ThreatSystem(16, "SAM SA-6 'Gainful'", "6", 25_000.0, 14_000.0)
SA_8 = ThreatSystem(17, "SAM SA-8 'Gecko'", "8", 10_300.0, 5_000.0)
SA_9 = ThreatSystem(18, "SAM SA-9 'Gaskin'", "9", 4_200.0, 5_000.0)
SA_10 = ThreatSystem(19, "SAM SA-10 'Grumble'", "10", 120_000.0, 27_000.0)
SA_11 = ThreatSystem(20, "SAM SA-11 'Gadfly'", "11", 50_000.0, 22_000.0)
SA_13 = ThreatSystem(21, "SAM SA-13 'Gopher'", "13", 5_000.0, 3_500.0)
SA_15 = ThreatSystem(22, "SAM SA-15 'Gauntlet'", "15", 12_000.0, 8_000.0)
SA_15_M2 = ThreatSystem(23, "SAM SA-15 'Gauntlet' (M2)", "15", 16_000.0, 15_000.0)
SA_19 = ThreatSystem(24, "SAM SA-19 'Grison'", "19", 8_000.0, 3_500.0)
SA_22 = ThreatSystem(25, "SAM SA-22 'Greyhound'", "22", 20_000.0, 15_000.0)
GEPARD = ThreatSystem(26, "SPAAA Gepard", "A", 4_000.0, 3_000.0)
VULCAN = ThreatSystem(27, "SPAAA Vulcan", "A", 2_000.0, 5_000.0)
ZSU_23_4 = ThreatSystem(28, "SPAAA ZSU-23-4", "A", 2_500.0, 2_500.0)
ZSU_57_2 = ThreatSystem(29, "SPAAA ZSU-57-2", "A", 7_000.0, 7_000.0)


@dataclass(frozen=True)
class ThreatPoint:
    """One pre-planned threat: where the briefing puts it, and what it is.

    `position` is the **briefed** position — pass what `PlanOverlay.threat`
    returned, not the site's true `Point`, or the cockpit ring contradicts the
    map ring the player was given. `radius_m` overrides the system's published
    range with the radius the briefing claims (the two agree closely for most
    systems, and the briefed number is the one to show); `code` overrides the
    three-character label. `ring=False` keeps the point as a steerpoint without
    drawing its envelope.

    `label` is what the **F10 plan** calls this site, and it never reaches the
    cartridge — the jet has three characters and prints `system.code` in them.
    It is carried so the kneeboard's threat block can name a belt the way the
    map and the briefing name it ("SA-3 north shoulder", not "SAM SA-3 'Goa'"):
    a pilot cross-referencing the card against the map has to read one name.
    """

    position: "Point"
    system: ThreatSystem
    radius_m: Optional[float] = None
    ceiling_m: Optional[float] = None
    code: Optional[str] = None
    ring: bool = True
    label: Optional[str] = None

    def title(self) -> str:
        """The mission's own name for the site, or the system's own, tidied.

        The threat table's names are written for a dialog box (`SAM SA-6
        'Gainful'`), so the class prefix comes off: on a kneeboard the column
        heading already says these are threats.
        """
        if self.label:
            return self.label.upper()
        name = self.system.name
        for prefix in ("SAM ", "SPAAA ", "AAA "):
            if name.startswith(prefix):
                return name[len(prefix) :].upper()
        return name.upper()

    def hsd_code(self) -> str:
        """The three characters the HSD prints, validated the way the editor is."""
        code = self.system.code if self.code is None else self.code
        if not code.isalnum():
            raise ValueError(f"threat code must be alphanumeric, got {code!r}")
        return code.upper()[:3]


@dataclass(frozen=True)
class NavPoint:
    """One navigation steerpoint, STPT 1-25 — a point the jet can fly to.

    `note` is the editor's own free-text column, and is what makes a cartridge
    readable on the DTE page rather than a column of coordinates. `kind` is the
    editor's steerpoint type (`STPT` / `IP` / `TGT`).

    `route` is what makes a steerpoint part of the jet's **flight plan** rather
    than a loose point it can select. The HSD draws its route line from the
    points flagged into Navigation Route 1 (`R1`), and the editor's own table
    calls it a checkbox per point per route — so a tab uploaded with the flag
    clear gives the pilot every steerpoint and no route, which is exactly what a
    first version of this shipped. Only the flight's own waypoints carry it: a
    seam or a tanker station is a place to look at, not a leg to fly, and
    flagging one would bend the drawn route out to it.

    `route_altitude_m` and `speed_kph` are the *planned* numbers for the leg
    into this point, not the steerpoint's elevation: DCS keeps both in metric
    (metres, km/h) and converts for display, so a pydcs waypoint's own `alt` and
    `speed * 3.6` go straight in. `agl` picks the editor's altitude type, which
    matters because `add_runway_waypoint` writes an AGL gate into a route where
    everything else is MSL — see `core/kneeboard/flightplan.py`. The speed is
    written as **true** airspeed because that is the only thing pydcs stores
    (see the km/h rule in CLAUDE.md), rather than the editor's own ground-speed
    default, which would be a different number wearing the same digits.

    `tos_s` is the point's **time over steerpoint**, in seconds past zulu
    midnight — the editor's own encoding, `days * 86400 + h * 3600 + m * 60 + s`
    — or `None` for a point nothing scheduled. Only the flight's own route
    carries one: a tanker station or a seam is a place, and putting a time on it
    would invent a schedule.
    """

    position: "Point"
    note: str = ""
    kind: str = "STPT"
    route_altitude_m: float = 2_000.0
    speed_kph: float = 790.0
    agl: bool = False
    route: bool = False
    tos_s: float | None = None


@dataclass(frozen=True)
class GeoLine:
    """One polyline for the HSD, drawn between its own vertices in order.

    The jet holds four of these and twenty-five vertices between them, so a line
    that arrives longer than its share is thinned rather than cut short — losing
    the far end of a front line would move it, while losing a bend in the middle
    only makes it coarser. `note` is carried on the first vertex, which is the
    only per-point text the editor's table has room for.
    """

    points: tuple["Point", ...]
    note: str = ""
    enemy: bool = False


def takeoff_zulu_s(m: "Mission", group: "FlyingGroup") -> float:
    """When this flight rotates, in seconds past **zulu** midnight.

    The reference the cartridge's `TOS` is kept in, and it is not the one the
    mission file states. `Mission.start_time` is *local* — pydcs writes it out
    as seconds past local midnight — while the editor's own DTC manager builds
    every time it computes from ``start_time - SummerTimeDelta * 3600``, i.e.
    local wound back by the theatre's UTC offset, because the jet's clock and
    its steerpoint times run on zulu. pydcs carries that same offset as
    `Terrain.utc_offset` (Caucasus +4, Syria +3), so no table has to be invented
    here; getting it wrong would put every steerpoint time three or four hours
    out, which is the sort of error that looks deliberate.

    The theatre offset can wind a small-hours start past midnight, so the
    time-of-day part is taken modulo a day and the flight's own `start_time`
    delay is added on top — a route that then crosses midnight simply runs past
    86 400 s, which is exactly the `days` digit the editor's own encoding
    carries.
    """
    utc_offset = m.terrain.utc_offset.utcoffset(None)
    local_s = m.start_time.hour * 3600 + m.start_time.minute * 60 + m.start_time.second
    offset_s = utc_offset.total_seconds() if utc_offset is not None else 0.0
    return (local_s - offset_s) % 86_400.0 + float(group.start_time or 0)


def route_steerpoints(
    group: "FlyingGroup", *, takeoff_s: float | None = None
) -> list[NavPoint]:
    """The flight's own route, as the steerpoints the jet already flies.

    This exists because uploading a steerpoint tab **replaces** what the mission
    put in the cockpit — `mirror_NAV_PTS` is the editor's "do not upload tab
    data" and defaults to on precisely so a half-filled cartridge cannot wipe a
    route. So the first thing the tab has to contain is the route itself,
    reproduced point for point, before anything the F10 plan adds to it.

    Names come from `core/kneeboard/flightplan.py`, so the note beside STPT 2 is
    the same `DEP GATE` the kneeboard's route card prints — pydcs names that
    waypoint nothing, and a card and a cartridge disagreeing about a point's
    name is the sort of thing a pilot only notices at the worst moment. The
    import is function-local because `core/kneeboard` reads *this* module for
    its threat block; the dependency runs one way at import time and both ways
    at call time.

    **The times come from the same place as the names.** Given `takeoff_s` — the
    flight's rotation time in zulu seconds, from `takeoff_zulu_s` — each point
    gets the same instant the kneeboard's route card prints in its `ETA` column,
    because both are that number plus the elapsed time `flightplan.flight_plan`
    works out for the leg into it. One schedule, then, rather than two, and the
    card's zero-wind caveat covers the cockpit as well. What differs is the
    *clock*: the card is local, because that is the briefing's and the tower's,
    and the DED is zulu, so the card prints the take-off in both and labels its
    column `ETA L`. Without `takeoff_s` no point gets a time, which is the
    honest state for a route nobody clocked.
    """
    from dcs_mission_creator.core.kneeboard import flightplan

    names = flightplan.waypoint_names(group)
    elapsed = [leg.elapsed_s for leg in flightplan.flight_plan(group)]
    # Forward-fill, then back-fill the leading gap: pydcs writes no speed on the
    # first waypoint (the jet is parked), and a planned speed of zero on the DTE
    # page reads as a broken cartridge rather than as "not yet moving".
    speeds: list[float] = []
    for point in group.points:
        speeds.append(point.speed or (speeds[-1] if speeds else 0.0))
    first = next((speed for speed in speeds if speed), 0.0)
    speeds = [speed or first for speed in speeds]
    points: list[NavPoint] = []
    for index, point in enumerate(group.points):
        points.append(
            NavPoint(
                position=point.position,
                note=names[index],
                kind="STPT",
                route_altitude_m=float(point.alt),
                speed_kph=speeds[index] * 3.6,
                agl=point.alt_type == "RADIO",
                route=True,
                tos_s=None if takeoff_s is None else takeoff_s + elapsed[index],
            )
        )
    return points


def plan_steerpoints(plan: "PlanOverlay") -> list[NavPoint]:
    """The F10 plan's *points*, as steerpoints to append after the route.

    What qualifies is everything the map marks that the cockpit has nowhere else
    to put: the objective (as a `TGT`), the mission's own text labels — a seam to
    cross, an off-load point — the air defence that moves, and a vague enemy
    area. Emplaced threats are deliberately **not** here: they are already the
    cartridge's pre-planned threat points, and spending a navigation steerpoint
    on a second copy of the same ring buys nothing.

    An orbit is a *place*, so every race-track also yields one steerpoint at its
    midpoint — the thing a pilot actually wants from a tanker station is a range
    and a bearing to it, and a line on the HSD gives neither. `plan_geo_lines`
    may draw the track as well, when there is a line left after the geometry
    that has nowhere else to go.

    Every position here is the one `PlanOverlay` **drew**, so an estimated site
    stays estimated in the DED. That is the whole reason this reads back off the
    overlay rather than off the mission's own variables.

    Each keeps the editor's own defaults for planned altitude and speed. Nothing
    planned a leg to a seam or to a tanker station, and inheriting the cruise
    numbers off the route would put a figure on the DTE page that no part of the
    mission promised.
    """
    kinds = {"objective": "TGT", "waypoint": "STPT", "mobile": "STPT", "area": "STPT"}
    points = [
        NavPoint(mark.position, note=mark.label, kind=kinds[mark.kind])
        for mark in plan.marks()
        if mark.kind in kinds
    ]
    points += [
        NavPoint(line.points[0].midpoint(line.points[-1]), note=line.label or "ORBIT")
        for line in plan.lines()
        if line.kind == "orbit" and len(line.points) >= 2
    ]
    return points


def plan_geo_lines(
    plan: "PlanOverlay", *, traced_by: Sequence[NavPoint] = ()
) -> list[GeoLine]:
    """The F10 plan's *lines*, as the polylines the HSD can draw.

    Front lines come first, because a front line is the one piece of enemy
    geometry with a shape and nothing else in the cockpit carries it — the
    briefing's "cross at the seam" needs something on the HSD to point at, the
    same argument that makes `PlanOverlay.frontline` the one red drawing painted
    precisely at every difficulty.

    Then a corridor the flight does **not** fly. A `route` line is dropped when
    every one of its vertices is within `_TRACED_M` of a steerpoint in
    `traced_by`, which is the normal case — a mission draws the corridor it then
    flies, and the HSD already joins the steerpoints. What survives that test is
    a briefed lane for somebody else, which is worth a line.

    Orbits come last, and only take a line because in most missions there is one
    going spare: `plan_steerpoints` has already given every race-track the
    steerpoint that carries its range and bearing, so a line here adds the shape
    the point cannot — which way the pattern runs, and how long it is.
    """
    ranking = {"frontline": 0, "route": 1, "orbit": 2}
    candidates = [
        line for line in plan.lines() if line.kind in ranking and len(line.points) >= 2
    ]
    ordered = sorted(
        (
            line
            for line in candidates
            if line.kind != "route" or not _already_traced(line.points, traced_by)
        ),
        key=lambda line: ranking[line.kind],
    )
    if len(ordered) > GEO_LINE_COUNT:
        log.warning(
            "more plan lines than the jet's four GEO lines, dropping the last",
            dropped=[line.label for line in ordered[GEO_LINE_COUNT:]],
        )
    return [
        GeoLine(tuple(line.points), note=line.label or "", enemy=line.enemy)
        for line in ordered[:GEO_LINE_COUNT]
    ]


def briefed(
    estimate: Optional[tuple["Point", float]],
    system: ThreatSystem,
    *,
    label: Optional[str] = None,
) -> list[ThreatPoint]:
    """`PlanOverlay.threat`'s return value as zero or one threat point.

    A list because the empty case has to stay expressible: a mission passes
    `None` for a site it drew no ring for — one it deliberately left off the
    map, or air defence that moves — so a `_draw_plan` step can splat these
    together, `[*briefed(a, SA_6), *briefed(b, SA_8)]`, and keep the policy
    where it belongs, in `PlanOverlay`.

    Every difficulty now hands back an estimate, so a `veteran`/`ace` mission
    loads points too — deliberately imprecise ones. That is the honest cockpit
    for a thin picture: the ring is wide and it is in the wrong place by a few
    kilometres, which is exactly what the briefing claims. Loading nothing was
    worse than loading an approximation, because the mission then had no
    coarsened position to build its steerpoints from either and used the true
    one.

    Pass the **same `label` the `plan.threat` call above it was given**. It is
    not for the jet — the cartridge has three characters — it is so the
    kneeboard's threat block and the F10 map call the site the same thing.
    """
    if estimate is None:
        return []
    position, radius_m = estimate
    return [ThreatPoint(position, system, radius_m=radius_m, label=label)]


def record_briefed(m: "Mission", points: Sequence[ThreatPoint]) -> None:
    """Note `points` as this mission's briefed air-defence picture.

    The cartridge is the F-16C's copy of that picture; the kneeboard's threat
    block is everyone else's, and `core/kneeboard` reads it back through
    `briefed_threats`. `arm_hsd_threats` records for you, so a Viper mission
    calls nothing extra — this is public for the mission that briefs rings and
    has no Viper to load them into (a Hornet package: its cartridge keeps
    threats on the SA page under a different descriptor, so `arm_hsd_threats`
    would refuse it, but the pilot still gets the card).

    Recording is what keeps the two honest: both ends are the estimate
    `PlanOverlay.threat` drew, so the difficulty policy is applied once, in
    `map_draw.py`, and the card cannot be more precise than the map.
    """
    _briefed(m).extend(points)


def briefed_threats(m: "Mission") -> list[ThreatPoint]:
    """Every threat point this mission briefed, in the order it briefed them.

    The order is the cartridge's, so the nth entry is pre-planned threat point
    `56 + n - 1` on the HSD and the kneeboard can print the steerpoint number
    the player will actually see beside it.
    """
    return list(getattr(m, _BRIEFED, []))


def arm_hsd_threats(
    m: "Mission",
    points: Sequence[ThreatPoint],
    *,
    name: str = "THREATS",
    overlay: Optional["MapOverlay"] = None,
) -> int:
    """Load `points` as pre-planned threats on every F-16C client slot.

    Builds one cartridge named `name`, marks it the slot's default and sets it
    to load on spawn, so the rings are up before the player has touched the
    DTE page. Pass `overlay` to put each point's elevation on the terrain — the
    HAD reads it as the site's altitude — otherwise every point sits at sea
    level. Returns the number of threats written.

    An empty `points` writes nothing at all — a mission that briefs no ring at
    all, or one whose only air defence moves. Every difficulty produces an
    estimate, so a hard mission gets a cartridge like any other; what changes
    with difficulty is how far its rings sit from the launchers.
    Raises if there is no Viper slot to load, or if the fifteen pre-planned
    steerpoints are oversubscribed — both mean the mission asked for something
    it will not get, and a silent drop reads in-game as the feature not working.
    """
    if not points:
        log.debug("no briefed threats to load, writing no cartridge")
        return 0
    record_briefed(m, points)
    if len(points) > MAX_POINTS:
        raise ValueError(
            f"the F-16C holds {MAX_POINTS} pre-planned threats "
            f"(steerpoints {_FIRST_STEERPOINT}-{_FIRST_STEERPOINT + MAX_POINTS - 1}), "
            f"got {len(points)}"
        )
    slots = _client_slots(m)
    if not slots:
        raise ValueError(
            f"arm_hsd_threats found no {AIRCRAFT.id} client slot to load; "
            "only the Viper reads a pre-planned threat cartridge"
        )

    rows = [_row(index, point, overlay) for index, point in enumerate(points, start=1)]
    mpd = _ensure(m, name)["data"]["MPD"]
    mpd["THREAT_PTS"] = rows
    mpd["mirror_THREAT_PTS"] = False
    _attach(m, name, slots)
    log.debug(
        "armed HSD pre-planned threats",
        cartridge=name,
        threats=[row["threatName"] for row in rows],
        slots=len(slots),
    )
    return len(rows)


def arm_plan(
    m: "Mission",
    plan: "PlanOverlay",
    *,
    overlay: "MapOverlay",
    name: str = "THREATS",
) -> tuple[int, int]:
    """Load the F10 plan into the same cartridge as steerpoints and GEO lines.

    The map and the cockpit stop being two separate briefings. What the player
    was shown on F10 — the objective, the seam, the off-load point, the SHORAD
    that rides with the column, each supporting flight's station, the front line
    — arrives in the jet as steerpoints they can select and as lines the HSD
    draws, at the positions `PlanOverlay` drew them and no others. Returns
    `(steerpoints, GEO vertices)` written.

    The route comes first and is never truncated. Uploading a steerpoint tab
    *replaces* the flight plan DCS put in the cockpit (`mirror_NAV_PTS` is the
    editor's "do not upload tab data", and it is on by default for exactly that
    reason), so the tab is built as **the flight's own route plus what the plan
    adds**, and a plan that would push the route past the jet's twenty-five
    points loses its own marks rather than the pilot's navigation.

    `overlay` is required, not optional as it is for the threat tab. Every point
    written here carries an elevation the jet reads as terrain height under the
    steerpoint, and a cartridge that put the whole route at sea level would be
    worse than the mirrored default it replaces.

    The route is **re-read when the cartridge is written**, not frozen here.
    `MissionBuilder.build_miz` snaps take-off and landing altitudes and rewrites
    the departure speed after `_assemble` returns, so a tab built at this point
    would carry the zeroes and the 108 kt those two steps exist to correct. What
    is fixed here is the plan's own marks and which of them fit. The steerpoint
    times ride on that re-read for free: a `TOS` worked out from the 108 kt
    departure speed would have put the whole schedule minutes late.

    Raises if the mission has no player-flown Viper, or more than one Viper
    *flight*: there is one steerpoint tab and it can only hold one route, so two
    flights with two routes is a mission asking for something the jet will not
    give it.
    """
    groups = _client_groups(m)
    if not groups:
        raise ValueError(
            f"arm_plan found no {AIRCRAFT.id} client slot to load; "
            "only the Viper reads a steerpoint / GEO-line cartridge"
        )
    if len(groups) > 1:
        raise ValueError(
            "arm_plan writes one steerpoint tab and every Viper slot loads it, "
            f"but this mission has {len(groups)} player Viper flights "
            f"({', '.join(group.name for group in groups)}) with routes of "
            "their own; give each its own cartridge instead"
        )
    route = route_steerpoints(groups[0], takeoff_s=takeoff_zulu_s(m, groups[0]))
    extra = plan_steerpoints(plan)
    room = MAX_NAV_POINTS - len(route)
    if len(extra) > max(room, 0):
        log.warning(
            "plan steerpoints do not fit beside the route, dropping the last",
            route=len(route),
            dropped=[point.note for point in extra[max(room, 0) :]],
        )
    nav = route + extra[: max(room, 0)]
    lines = _fit_geo_lines(plan_geo_lines(plan, traced_by=nav))

    mpd = _ensure(m, name)["data"]["MPD"]
    pending = _PendingRoute(name, groups[0], tuple(extra[: max(room, 0)]), overlay)
    _pending(m).append(pending)
    pending.write(m)
    geo_rows = _geo_rows(lines, overlay)
    mpd["GEO_LINES"] = geo_rows
    mpd["mirror_GEO_LINES"] = not geo_rows
    _attach(m, name, _client_slots(m))
    log.debug(
        "armed cartridge navigation",
        cartridge=name,
        steerpoints=len(nav),
        geo_lines=[line.note for line in lines],
        geo_points=len(geo_rows),
    )
    return len(nav), len(geo_rows)


def write_cartridges(m: "Mission", miz_path: Path) -> int:
    """Append every armed cartridge to the saved `.miz` as `DTC/<name>.dtc`.

    Called by `MissionBuilder.build_miz` right after `Mission.save`, because
    pydcs writes a fixed set of zip entries and has no hook for another one.
    Appending re-writes the archive's central directory, which is what DCS
    reads; the entries carry a fixed timestamp so the package stays
    reproducible. Returns the number of cartridges written.
    """
    # The route is re-read here, not taken as it stood when the mission armed
    # it. `MissionBuilder.build_miz` snaps take-off and landing altitudes and
    # rewrites the departure speed *after* `_assemble` returns, so a cartridge
    # built during the mission's own `_load_cartridge` step would print the
    # zeroes and the 108 kt those two steps exist to correct — the same reason
    # the kneeboard is written last.
    for pending in getattr(m, _PENDING, ()):
        pending.write(m)
    cartridges = getattr(m, _STASH, None)
    if not cartridges:
        return 0
    with zipfile.ZipFile(miz_path, "a", compression=zipfile.ZIP_DEFLATED) as zipf:
        for name, cartridge in sorted(cartridges.items()):
            info = zipfile.ZipInfo(f"DTC/{name}.dtc", date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zipf.writestr(info, json.dumps(cartridge, indent=4))
    log.debug("wrote DTC cartridges", count=len(cartridges), miz=str(miz_path))
    return len(cartridges)


def _client_slots(m: "Mission") -> list["FlyingUnit"]:
    """Every player-flown Viper unit in the mission, in build order."""
    return [
        unit
        for coalition in m.coalition.values()
        for country in coalition.countries.values()
        for group in country.plane_group
        for unit in group.units
        if unit.unit_type.id == AIRCRAFT.id
        and unit.skill in (Skill.Client, Skill.Player)
    ]


def _client_groups(m: "Mission") -> list["FlyingGroup"]:
    """Every flight with a player-flown Viper in it, in build order."""
    return [
        group
        for coalition in m.coalition.values()
        for country in coalition.countries.values()
        for group in country.plane_group
        if any(
            unit.unit_type.id == AIRCRAFT.id
            and unit.skill in (Skill.Client, Skill.Player)
            for unit in group.units
        )
    ]


def _attach(m: "Mission", name: str, slots: Sequence["FlyingUnit"]) -> None:
    """Make `name` the cartridge every Viper slot carries, and loads on spawn.

    `default` picks it on the DTE page; `AutoLoad` uploads it at spawn instead
    of waiting for the player to press LOAD. There is no pydcs field to assign
    to — `unit_extras` is what turns the attribute into the mission file's `DTC`
    key — and both halves are idempotent, so the threat tab and the navigation
    tabs can be armed in either order or not at all.
    """
    unit_extras.emit_unit_key("dtc", "DTC")
    for unit in slots:
        unit.dtc = {  # ty: ignore[unresolved-attribute]
            "AutoLoad": True,
            "Cartridges": [{"name": name, "default": True}],
        }


def _already_traced(points: Sequence["Point"], traced_by: Sequence[NavPoint]) -> bool:
    """True when every vertex sits on a steerpoint that is already being written."""
    if not traced_by:
        return False
    return all(
        min(point.distance_to_point(nav.position) for nav in traced_by) <= _TRACED_M
        for point in points
    )


def _fit_geo_lines(lines: Sequence[GeoLine]) -> list[GeoLine]:
    """Thin `lines` until they fit the jet's twenty-five shared vertices.

    Fair-shared rather than first-come: a short line keeps every vertex it has
    and hands its unused share back, and only the lines still over their
    allocation are thinned. A line thinned below two vertices is not a line and
    is dropped, which can only happen with four lines and a budget this small if
    something upstream has gone wrong.
    """
    if not lines:
        return []
    sizes = {id(line): len(line.points) for line in lines}
    if sum(sizes.values()) <= MAX_GEO_POINTS:
        return list(lines)
    budget, remaining, allocation = MAX_GEO_POINTS, len(lines), {}
    for line in sorted(lines, key=lambda line: sizes[id(line)]):
        take = min(sizes[id(line)], budget // remaining)
        allocation[id(line)] = take
        budget -= take
        remaining -= 1
    fitted = [
        GeoLine(
            tuple(_thin(line.points, allocation[id(line)])),
            note=line.note,
            enemy=line.enemy,
        )
        for line in lines
        if allocation[id(line)] >= 2
    ]
    log.warning(
        "thinned GEO lines to the jet's vertex budget",
        budget=MAX_GEO_POINTS,
        before=[len(line.points) for line in lines],
        after=[len(line.points) for line in fitted],
    )
    return fitted


def _thin(points: Sequence["Point"], limit: int) -> list["Point"]:
    """`limit` vertices spread evenly along `points`, both ends kept.

    Evenly rather than by deviation (Douglas-Peucker): the lines this thins are
    already plans rather than surveys — a front line's trace is a handful of
    sector positions — so what matters is that the two ends stay put and the
    middle keeps its shape, not that any particular bend survives.
    """
    if len(points) <= limit:
        return list(points)
    if limit <= 2:
        return [points[0], points[-1]]
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def _geo_rows(lines: Sequence[GeoLine], overlay: "MapOverlay") -> list[dict[str, Any]]:
    """Every GEO vertex, flagged with the line it belongs to.

    The line index is a colour, so it is chosen by what the drawing *is* rather
    than by the order it arrived: enemy geometry asks for red first, the
    friendly plan for green, and a mission drawing more lines than there are
    colours takes what is left.
    """
    taken: set[int] = set()
    rows: list[dict[str, Any]] = []
    for line in lines:
        index = next(
            (i for i in _LINE_PREFERENCE[line.enemy] if i not in taken),
            None,
        )
        if index is None:
            break
        taken.add(index)
        for offset, point in enumerate(line.points):
            rows.append(
                _geo_row(
                    len(rows) + 1,
                    point,
                    index,
                    line.note if offset == 0 else "",
                    overlay,
                )
            )
    return rows


def _geo_row(
    number: int, point: "Point", line: int, note: str, overlay: "MapOverlay"
) -> dict[str, Any]:
    """One `GEO_LINES` vertex, in the shape the editor writes it."""
    row: dict[str, Any] = {
        "number": number,
        "id": f"GEO_LINES{FIRST_GEO_STEERPOINT + number - 1}",
        "x": point.x,
        "y": point.y,
        "alt": waypoints.ground_elevation_m(overlay, point),
        "note": note,
    }
    for index in range(1, GEO_LINE_COUNT + 1):
        row[f"L{index}"] = index == line
    return row


def _nav_row(number: int, point: NavPoint, overlay: "MapOverlay") -> dict[str, Any]:
    """One `NAV_PTS` record, in the shape the editor writes it.

    `alt` is the steerpoint's **elevation** — the terrain under it — while
    `routeAltitude` is the altitude planned for the leg into it; the editor
    keeps both in metres and converts to feet for display.

    `R1` is what the HSD draws its route line from, so the flight's own
    waypoints claim Navigation Route 1 and nothing else does, and they are also
    the only points that carry a `TOS` — the editor keeps it as seconds past
    zulu midnight, `-1` paired with `isTOSEnabled` off being its own "no time
    for this point" state. A route point's time is the kneeboard route card's
    own `ETA` for it, so the DED and the card agree by construction rather than
    by coincidence; the plan's marks keep `-1`, because nothing scheduled a
    tanker station or a seam.

    `FIX_Time` stays off for every point. It is the editor's "this time is the
    fixed one" switch, and it makes the *speed* a derived quantity — the DTC
    page recomputes each leg's speed from the gap between two times the moment
    it is set. The mission tuned those speeds per airframe (see the km/h rule in
    CLAUDE.md), so the time is what follows from them here, not the reverse.
    """
    return {
        "number": number,
        "id": f"STPT{number}",
        "idOA1": f"OA1{number}",
        "idOA2": f"OA2{number}",
        "idOA1_Line": f"OA1{number}Line",
        "idOA2_Line": f"OA2{number}Line",
        "x": point.position.x,
        "y": point.position.y,
        "alt": waypoints.ground_elevation_m(overlay, point.position),
        "routeAltitude": point.route_altitude_m,
        "speed": point.speed_kph,
        "note": point.note,
        "R1": point.route,
        "R2": False,
        "R3": False,
        "TOS": -1 if point.tos_s is None else int(round(point.tos_s)),
        "isTOSEnabled": point.tos_s is not None,
        "FIX_Time": False,
        # 1 = MSL, 2 = AGL; 4 = true airspeed, which is the only speed pydcs
        # stores (the editor's own default is ground speed).
        "altitudeType": 2 if point.agl else 1,
        "velocityType": 4,
        "type": point.kind,
        "isOAP_1": False,
        "isOAP_2": False,
        **{
            f"OAP_{oap}_{field}": 0.0
            for oap in (1, 2)
            for field in ("Range", "Bearing", "X", "Y", "Alt", "DeltaX", "DeltaY")
        },
    }


def _row(
    index: int, point: ThreatPoint, overlay: Optional["MapOverlay"]
) -> dict[str, Any]:
    """One `THREAT_PTS` record, in the shape the editor writes it."""
    system = point.system
    # Clamped at sea level, because the position is an *estimate* and can land
    # offshore — abkhaz_sweep's, 6 km off a coastal ridge, sampled -75 m of
    # Black Sea bathymetry, and the HAD would have read that back as the
    # battery's altitude. Nobody emplaces an S-125 below the waterline; a
    # briefed site sitting on the coast at 0 m is the smaller error.
    elevation = (
        0.0
        if overlay is None
        else max(0.0, waypoints.ground_elevation_m(overlay, point.position))
    )
    return {
        "number": index,
        "id": f"THREAT_PTS{_FIRST_STEERPOINT + index - 1}",
        "x": point.position.x,
        "y": point.position.y,
        "elev": elevation,
        "threatName": system.name,
        "def_num": system.def_num,
        "radius": system.radius_m if point.radius_m is None else point.radius_m,
        "alt": system.ceiling_m if point.ceiling_m is None else point.ceiling_m,
        "text": point.hsd_code(),
        "ring": point.ring,
    }


@dataclass(frozen=True)
class _PendingRoute:
    """A steerpoint tab whose route is re-read from the flight at write time."""

    name: str
    group: "FlyingGroup"
    extra: tuple[NavPoint, ...]
    overlay: "MapOverlay"

    def write(self, m: "Mission") -> None:
        """(Re)build the tab from the flight as it stands right now.

        A route longer than the jet's twenty-five steerpoints leaves the tab
        mirrored rather than uploading a truncated one: the points that would be
        cut are the recovery, and a cartridge that deletes a pilot's way home is
        worse than one that adds nothing. No mission here comes close.
        """
        route = route_steerpoints(self.group, takeoff_s=takeoff_zulu_s(m, self.group))
        if len(route) > MAX_NAV_POINTS:
            log.warning(
                "route is longer than the jet's steerpoint tab, leaving it mirrored",
                flight=self.group.name,
                waypoints=len(route),
                limit=MAX_NAV_POINTS,
            )
            return
        nav = route + list(self.extra[: max(MAX_NAV_POINTS - len(route), 0)])
        mpd = _ensure(m, self.name)["data"]["MPD"]
        mpd["NAV_PTS"] = [
            _nav_row(index, point, self.overlay)
            for index, point in enumerate(nav, start=1)
        ]
        mpd["VIPVRP"] = dict(_VIPVRP) if nav else []
        mpd["mirror_NAV_PTS"] = not nav


def _pending(m: "Mission") -> list[_PendingRoute]:
    """The mission's deferred steerpoint tabs, created on first use."""
    routes: Optional[list[_PendingRoute]] = getattr(m, _PENDING, None)
    if routes is None:
        routes = []
        setattr(m, _PENDING, routes)
    return routes


def _ensure(m: "Mission", name: str) -> dict[str, Any]:
    """This mission's cartridge `name`, created empty on first use.

    One cartridge, filled a tab at a time: the threat points, the steerpoints
    and the GEO lines are three separate calls a mission may make in any order
    or not at all, and each writes only its own tab and its own mirror flag.
    Building a second cartridge per tab would work in the file and not in the
    jet, which loads one default.
    """
    stash = _stash(m)
    if name not in stash:
        stash[name] = _cartridge(name, m.terrain.name)
    return stash[name]


def _cartridge(name: str, terrain: str) -> dict[str, Any]:
    """The whole `.dtc` document: the editor's `{name, type, data}` envelope.

    `terrain` is checked against the running map before any of the navigation
    tabs are read, so a cartridge built for the wrong theater loads as empty
    rather than putting threats in the sea.

    Every `mirror_*` flag is the editor's "Do not upload tab data" checkbox,
    which defaults to **on**, and every tab starts here in that state — so a tab
    nothing armed keeps whatever the mission itself put in the cockpit, and only
    the ones a caller filled are uploaded. Getting this backwards is silent in
    both directions: a mirrored tab means the jet takes the cartridge and then
    declines to read the one thing it was written for, and an un-mirrored empty
    one wipes the mission's own route or radio presets.
    """
    return {
        "name": name,
        "type": AIRCRAFT.id,
        "data": {
            "type": AIRCRAFT.id,
            "name": name,
            "terrain": terrain,
            "MPD": {
                "terrain": terrain,
                "mirror_NAV_PTS": True,
                "mirror_DEST": True,
                "mirror_GEO_LINES": True,
                "mirror_THREAT_PTS": True,
                "NAV_PTS": [],
                "VIPVRP": [],
                "DEST": [],
                "GEO_LINES": [],
                "THREAT_PTS": [],
                "CMDS": {},
            },
            "COMM": {
                "COMM1": {},
                "COMM2": {},
                "mirror_COMM1": True,
                "mirror_COMM2": True,
            },
            "ELINT": {"RWR": {}},
        },
    }


def _briefed(m: "Mission") -> list[ThreatPoint]:
    """The mission's briefed threat points, created on first use."""
    points: Optional[list[ThreatPoint]] = getattr(m, _BRIEFED, None)
    if points is None:
        points = []
        setattr(m, _BRIEFED, points)
    return points


def _stash(m: "Mission") -> dict[str, dict[str, Any]]:
    """The mission's pending cartridges, created on first use."""
    cartridges: Optional[dict[str, dict[str, Any]]] = getattr(m, _STASH, None)
    if cartridges is None:
        cartridges = {}
        setattr(m, _STASH, cartridges)
    return cartridges
