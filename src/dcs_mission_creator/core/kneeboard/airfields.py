"""The airfields this mission uses, and the part of them the game does not show.

"Relevant" is derived, not declared: an airfield is here because a blue flight
departs from it or lands at it, which is exactly the set a player has to know —
their own field, their divert, and the field the strike pair came out of.

What can honestly be said about a field, and what cannot, is fixed by the data:

- **Position, parking and runway designators are pydcs's**, straight out of the
  terrain module, so they are the game's own numbers.
- **Elevation is the overlay's**, sampled the way `core/waypoints.py` snaps a
  take-off point, so the card's field elevation is the altitude the jet spawns at.
- **Frequencies and navaids are the install's** (`kneeboard/beacons.py`).
- **Runway length is nobody's.** DCS keeps it in the terrain binary; pydcs has no
  field for it, and the F10 airdrome panel reads it through the game's own API.
  Where a runway carries a full ILS the two antennas bracket it and the measured
  centreline course *is* real data, so that is printed — and where they do not, the
  card says a direction and claims nothing about where the concrete starts.

Whether any of this is *worth* printing is `kneeboard/charts.py`'s question, not
this module's: on a theatre that ships an aerodrome chart for the field, ED's
surveyed drawing is better than anything derivable here and only the
mission-specific lines go on the route and comms cards. On Syria — three charts
for the whole map — a player starting at Hatay has no page about their own field,
and then this is the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from dcs_mission_creator.core import waypoints
from dcs_mission_creator.core.kneeboard import beacons as beacon_data
from dcs_mission_creator.core.kneeboard.beacons import Beacon
from dcs_mission_creator.core.kneeboard.comms import player_groups
from dcs_mission_creator.core.kneeboard.flightplan import FT_PER_M

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.terrain.terrain import Airport, Terrain

    from dcs_mission_creator.map_overlay.query import MapOverlay

#: How far off the field a beacon may sit and still be listed under it. Beacons
#: whose id carries the airport's own number are taken whatever the distance (an
#: outer homer is 4 km out); this bound only decides which *en-route* beacons — a
#: VOR or RSBN with no airfield in its id — count as the field's.
NEARBY_BEACON_M = 2_500.0

#: How far off the field a beacon may sit and still be drawn on the plan view. The
#: approach aids (an outer homer is 4 km out) stay in the table but would otherwise
#: set the drawing's scale and shrink the field to a smudge.
SKETCH_RADIUS_M = 2_500.0

#: A measured ILS course further than this from the runway designator means the two
#: antennas were not the pair assumed — print nothing rather than a wrong course.
_COURSE_TOLERANCE_DEG = 30.0


@dataclass(frozen=True)
class IlsGeometry:
    """The two antennas of one ILS, and the course they measure out.

    The glideslope sits a few hundred metres in from its threshold and the
    localizer beyond the far end, so the bearing from one to the other *is* the
    landing course and the segment between them brackets the strip. It is the only
    surveyed runway geometry available without the game running.
    """

    callsign: str
    course_true: float
    glideslope: Point
    localizer: Point


@dataclass(frozen=True)
class Approach:
    """One end of a runway."""

    designator: str
    heading_deg: int
    aids: tuple[Beacon, ...]
    ils: IlsGeometry | None = None

    @property
    def measured_true(self) -> float | None:
        return None if self.ils is None else self.ils.course_true


@dataclass(frozen=True)
class RunwayCard:
    name: str
    approaches: tuple[Approach, ...]


@dataclass(frozen=True)
class Spawn:
    """A flight that starts at this field, and where on it."""

    flight: str
    aircraft: str
    start: str
    slots: tuple[str, ...]
    position: Point
    player: bool


@dataclass(frozen=True)
class AirfieldCard:
    airport: Airport
    elevation_m: float
    runways: tuple[RunwayCard, ...]
    beacons: tuple[Beacon, ...]
    spawns: tuple[Spawn, ...]
    landings: tuple[str, ...]

    @property
    def elevation_ft(self) -> float:
        return self.elevation_m * FT_PER_M

    @property
    def runway_text(self) -> str:
        return " / ".join(r.name for r in self.runways) or "--"

    def spawn_of(self, flight: str) -> Spawn | None:
        return next((s for s in self.spawns if s.flight == flight), None)


def relevant_airfields(m: Mission, *, coalition: str = "blue") -> list[Airport]:
    """Fields a `coalition` flight departs from or lands at, player's first."""
    ordered: list[Airport] = []
    for group in _ordered_groups(m, coalition):
        for point in (group.points[0], group.points[-1]) if group.points else ():
            airport = (
                None
                if point.airdrome_id is None
                else m.terrain.airport_by_id(point.airdrome_id)
            )
            if airport is not None and airport not in ordered:
                ordered.append(airport)
    return ordered


def airfield_cards(
    m: Mission, *, overlay: MapOverlay | None = None, coalition: str = "blue"
) -> list[AirfieldCard]:
    """One card per relevant airfield."""
    return [
        _card(m, airport, overlay=overlay, coalition=coalition)
        for airport in relevant_airfields(m, coalition=coalition)
    ]


# -- internals ---------------------------------------------------------------


def _ordered_groups(m: Mission, coalition: str):
    """Player flights first, so their field heads the list of airfields."""
    players = player_groups(m)
    rest = [
        group
        for name, side in m.coalition.items()
        if name == coalition
        for country in side.countries.values()
        for group in list(country.plane_group) + list(country.helicopter_group)
        if group not in players
    ]
    return players + rest


def _card(
    m: Mission, airport: Airport, *, overlay: MapOverlay | None, coalition: str
) -> AirfieldCard:
    beacons = tuple(
        beacon_data.airfield_beacons(
            m.terrain, airport, include_nearby_m=NEARBY_BEACON_M
        )
    )
    elevation = (
        0.0
        if overlay is None
        else waypoints.ground_elevation_m(overlay, airport.position)
    )
    return AirfieldCard(
        airport=airport,
        elevation_m=elevation,
        runways=_runways(m, airport, beacons),
        beacons=beacons,
        spawns=_spawns(m, airport, coalition),
        landings=_landings(m, airport, coalition),
    )


def sketch_beacons(card: AirfieldCard, terrain: Terrain) -> list[Beacon]:
    """The card's beacons close enough to the field to share a drawing with it."""
    return [
        b
        for b in card.beacons
        if card.airport.position.distance_to_point(b.position(terrain))
        <= SKETCH_RADIUS_M
    ]


def approach_line(approach: Approach) -> str:
    """`13  DESIG 130   ILS CRS 122T` — what the designator says, then what is measured.

    The designator heading is *not* converted to anything.
    `RunwayApproach.heading` is the designator times ten, i.e. the number painted on
    the threshold, which is nominally magnetic and in DCS is carried over from
    real-world charts — so treating it as true and applying a variation would
    introduce an error rather than remove one. The measured ILS course beside it is
    the real bearing.
    """
    text = f"{approach.designator:>3}  DESIG {approach.heading_deg:03d}"
    if approach.measured_true is not None:
        text += f"   ILS CRS {approach.measured_true:03.0f}T"
    return text


def _runways(
    m: Mission, airport: Airport, beacons: Sequence[Beacon]
) -> tuple[RunwayCard, ...]:
    """Every runway, with its beacons and — where measurable — its ILS geometry."""
    by_id = {b.beacon_id: b for b in beacons}
    pairs = _ils_pairs(m.terrain, beacons)
    cards = []
    for runway in airport.runways:
        approaches = []
        for approach in (runway.main, runway.opposite):
            aids = [by_id[rb.id] for rb in approach.beacons if rb.id in by_id]
            ils = _match_ils(pairs, approach.heading)
            if ils is not None:
                # pydcs does not associate a beacon with an approach at every
                # field (Vaziani and Hatay both come through with none), so the
                # pair this end's geometry came from is added to its aid list —
                # otherwise the navaid table shows an ILS serving nothing.
                aids += [
                    b
                    for b in beacons
                    if b.callsign == ils.callsign
                    and b.kind.startswith("ILS")
                    and b not in aids
                ]
            approaches.append(
                Approach(
                    designator=approach.name,
                    heading_deg=approach.heading,
                    aids=tuple(aids),
                    ils=ils,
                )
            )
        cards.append(RunwayCard(name=runway.name, approaches=tuple(approaches)))
    return tuple(cards)


def _ils_pairs(terrain: Terrain, beacons: Sequence[Beacon]) -> list[IlsGeometry]:
    """Every glideslope/localizer pair among `beacons`, grouped by callsign.

    Grouped by callsign rather than through pydcs's `RunwayApproach.beacons`, which
    is empty at several fields: one ILS installation shares one callsign (`IVI` and
    `IVZ` are the two ends of Vaziani), so the callsign is the join that always
    exists.
    """
    out = []
    for callsign in sorted({b.callsign for b in beacons if b.kind.startswith("ILS")}):
        group = [b for b in beacons if b.callsign == callsign]
        glideslope = next((b for b in group if b.kind == "ILS_GLIDESLOPE"), None)
        localizer = next((b for b in group if b.kind == "ILS_LOCALIZER"), None)
        if glideslope is None or localizer is None:
            continue
        gs_pos = glideslope.position(terrain)
        loc_pos = localizer.position(terrain)
        out.append(
            IlsGeometry(
                callsign=callsign,
                course_true=gs_pos.heading_between_point(loc_pos),
                glideslope=gs_pos,
                localizer=loc_pos,
            )
        )
    return out


def _match_ils(pairs: Sequence[IlsGeometry], heading: int) -> IlsGeometry | None:
    """The pair whose measured course is this approach's, or `None`.

    A designator is only nominally the heading, so the match is the closest pair
    inside a tolerance; a pair further off than that is assumed to belong to another
    runway rather than being silently claimed by this one.
    """
    best, best_offset = None, _COURSE_TOLERANCE_DEG
    for pair in pairs:
        offset = abs((pair.course_true - heading + 180.0) % 360.0 - 180.0)
        if offset < best_offset:
            best, best_offset = pair, offset
    return best


def _spawns(m: Mission, airport: Airport, coalition: str) -> tuple[Spawn, ...]:
    """Flights whose first waypoint is this field, with their parking slots."""
    from dcs.unit import Skill

    starts = {
        "TakeOffParking": "COLD",
        "TakeOffParkingHot": "HOT",
        "TakeOff": "RUNWAY",
        "TakeOffGround": "GROUND",
        "TakeOffGroundHot": "GROUND HOT",
    }
    out = []
    for group in _ordered_groups(m, coalition):
        if not group.points or group.points[0].airdrome_id != airport.id:
            continue
        out.append(
            Spawn(
                flight=group.name,
                aircraft=group.units[0].unit_type.id if group.units else "",
                start=starts.get(group.points[0].type, group.points[0].type),
                slots=tuple(
                    str(u.parking_id) for u in group.units if u.parking_id is not None
                ),
                position=group.units[0].position if group.units else airport.position,
                player=any(
                    u.skill in (Skill.Client, Skill.Player) for u in group.units
                ),
            )
        )
    return tuple(out)


def _landings(m: Mission, airport: Airport, coalition: str) -> tuple[str, ...]:
    return tuple(
        group.name
        for group in _ordered_groups(m, coalition)
        if group.points and group.points[-1].airdrome_id == airport.id
    )
