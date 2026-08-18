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
  returns the estimate it actually drew — coarsened and offset at `trained`,
  nothing at all at `veteran`/`ace` — so passing that estimate straight through
  makes the cockpit ring the same claim as the F10 ring, and the difficulty
  policy stays in one place instead of being reimplemented here. An empty list
  writes no cartridge, which is exactly what an ace mission wants.
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

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: The only aircraft type this writes cartridges for; also the `type` field DCS
#: matches a cartridge against a slot with.
AIRCRAFT = planes.F_16C_50

#: Pre-planned threats occupy steerpoints 56-70, so the jet holds fifteen.
MAX_POINTS = 15
_FIRST_STEERPOINT = 56

#: A fixed zip timestamp, so appending the cartridge keeps two builds of the
#: same mission byte-identical.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: Where `arm_hsd_threats` parks cartridges until the `.miz` exists.
_STASH = "dtc_cartridges"


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
    """

    position: "Point"
    system: ThreatSystem
    radius_m: Optional[float] = None
    ceiling_m: Optional[float] = None
    code: Optional[str] = None
    ring: bool = True

    def label(self) -> str:
        """The three characters the HSD prints, validated the way the editor is."""
        code = self.system.code if self.code is None else self.code
        if not code.isalnum():
            raise ValueError(f"threat code must be alphanumeric, got {code!r}")
        return code.upper()[:3]


def briefed(
    estimate: Optional[tuple["Point", float]], system: ThreatSystem
) -> list[ThreatPoint]:
    """`PlanOverlay.threat`'s return value as zero or one threat point.

    A list because the empty case is the interesting one: at `veteran`/`ace`
    the estimate is `None` and there is nothing to load, so a `_draw_plan` step
    can splat these together — `[*briefed(a, SA_6), *briefed(b, SA_8)]` — and
    keep the difficulty branch where it belongs, in `PlanOverlay`.
    """
    if estimate is None:
        return []
    position, radius_m = estimate
    return [ThreatPoint(position, system, radius_m=radius_m)]


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

    An empty `points` writes nothing at all: that is how `veteran` and `ace`
    missions come out, since `PlanOverlay.threat` withholds the estimate there.
    Raises if there is no Viper slot to load, or if the fifteen pre-planned
    steerpoints are oversubscribed — both mean the mission asked for something
    it will not get, and a silent drop reads in-game as the feature not working.
    """
    if not points:
        log.debug("no briefed threats to load, writing no cartridge")
        return 0
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

    unit_extras.emit_unit_key("dtc", "DTC")
    rows = [_row(index, point, overlay) for index, point in enumerate(points, start=1)]
    _stash(m)[name] = _cartridge(name, m.terrain.name, rows)
    for unit in slots:
        # `default` picks the cartridge on the DTE page; `AutoLoad` uploads it
        # at spawn instead of waiting for the player to press LOAD.
        # No pydcs field to assign to — `unit_extras` is what turns the
        # attribute into the mission file's `DTC` key.
        unit.dtc = {  # ty: ignore[unresolved-attribute]
            "AutoLoad": True,
            "Cartridges": [{"name": name, "default": True}],
        }
    log.debug(
        "armed HSD pre-planned threats",
        cartridge=name,
        threats=[row["threatName"] for row in rows],
        slots=len(slots),
    )
    return len(rows)


def write_cartridges(m: "Mission", miz_path: Path) -> int:
    """Append every armed cartridge to the saved `.miz` as `DTC/<name>.dtc`.

    Called by `MissionBuilder.build_miz` right after `Mission.save`, because
    pydcs writes a fixed set of zip entries and has no hook for another one.
    Appending re-writes the archive's central directory, which is what DCS
    reads; the entries carry a fixed timestamp so the package stays
    reproducible. Returns the number of cartridges written.
    """
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


def _row(
    index: int, point: ThreatPoint, overlay: Optional["MapOverlay"]
) -> dict[str, Any]:
    """One `THREAT_PTS` record, in the shape the editor writes it."""
    system = point.system
    elevation = (
        0.0
        if overlay is None
        else waypoints.ground_elevation_m(overlay, point.position)
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
        "text": point.label(),
        "ring": point.ring,
    }


def _cartridge(name: str, terrain: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole `.dtc` document: the editor's `{name, type, data}` envelope.

    `terrain` is checked against the running map before any of the navigation
    tabs are read, so a cartridge built for the wrong theater loads as empty
    rather than putting threats in the sea.

    Every `mirror_*` flag is the editor's "Do not upload tab data" checkbox,
    which defaults to **on** — so leaving `mirror_THREAT_PTS` alone would hand
    the jet a cartridge whose threat tab it then declines to upload. The other
    tabs stay mirrored on purpose: this helper only claims the threat points,
    and uploading an empty comms or steerpoint tab would wipe what the mission's
    own route and radio presets put in the cockpit.
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
                "mirror_THREAT_PTS": False,
                "NAV_PTS": [],
                "VIPVRP": [],
                "DEST": [],
                "GEO_LINES": [],
                "THREAT_PTS": rows,
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


def _stash(m: "Mission") -> dict[str, dict[str, Any]]:
    """The mission's pending cartridges, created on first use."""
    cartridges: Optional[dict[str, dict[str, Any]]] = getattr(m, _STASH, None)
    if cartridges is None:
        cartridges = {}
        setattr(m, _STASH, cartridges)
    return cartridges
