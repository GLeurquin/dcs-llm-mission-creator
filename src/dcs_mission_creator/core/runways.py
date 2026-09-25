"""Where an airfield's runways are, well enough to keep vehicles off them.

pydcs knows a runway's designators and nothing else: `Airport.runways` carries
`13R-31L` and a heading of 130, and no position, length or width. So nothing in
the project could tell that `core/sanctuary.py`'s point-defence ring, three
sections at 0/120/240 degrees and 1.8 km out, put one Avenger on the Vaziani
runway, whose axis runs 132/312 — nor that the battery's last-resort position,
the airfield reference point, *is* the runway.

Two facts make a usable model out of what is available:

- **The airport reference point is the runway midpoint** on a single-runway
  field. Measured against every ILS in the Caucasus and Syria tables it sits
  within 80 m of the centreline — about the glideslope antenna's own lateral
  offset — and between the two thresholds.
- **The ILS localizer stands on the extended centreline** a little past the far
  end (`Mods/terrains/<Theater>/Beacons.lua`, read by `core/kneeboard/beacons`
  and joined to the field by its `airfield_id`). Reference point to localizer is
  therefore the true runway bearing, and its length is half the runway plus the
  localizer's stand-off.

Where there is no localizer — no install, or a field without an ILS — the
designator heading stands in for the bearing and a generous length for the
measured one. A designator is nominally magnetic and a few degrees off true, so
that strip is drawn wider, never narrower.

What this is *not* is a survey: parallel and crossing runways share the one
reference point, so a multi-runway field gets one strip per designator pair
through it, widened for parallels. It is a keep-out, and every error in it is on
the side of keeping a vehicle further away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

import structlog

from dcs_mission_creator.core.kneeboard.beacons import theater_beacons

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.terrain.terrain import Airport, Terrain
    from dcs.unitgroup import Group

log = structlog.get_logger(__name__)

__all__ = [
    "KEEP_OUT_M",
    "Strip",
    "clear_of_runways",
    "field_strips",
    "fouled_strip",
    "push_clear",
    "strips",
]

#: How far either side of the centreline nothing may stand: the ICAO graded
#: runway strip is 150 m each side for an instrument runway.
KEEP_OUT_M = 150.0
#: Extra lateral room for a strip whose bearing is a designator rather than a
#: measurement — five degrees over 1.7 km is 150 m at the threshold.
_UNMEASURED_EXTRA_M = 150.0
#: A parallel pair is typically 200-1,500 m apart and the reference point is on
#: one of them or between; this covers the common spacing either side.
_PARALLEL_EXTRA_M = 500.0
#: Past each threshold: the overrun and the first of the approach.
OVERRUN_M = 300.0
#: How far past the far end a localizer typically stands.
_LOCALIZER_STANDOFF_M = 150.0
#: Half-length assumed with nothing measured: a 3.4 km runway.
_DEFAULT_HALF_LENGTH_M = 1_700.0
#: A localizer is this runway's only when its bearing from the reference point
#: is within this of the designator (either end).
_LOCALIZER_MATCH_DEG = 12.0
_LOCALIZER_MAX_M = 5_000.0


@dataclass(frozen=True)
class Strip:
    """One runway's keep-out rectangle, centred on the field reference point."""

    airport: str
    name: str
    center: Point
    heading_deg: float
    half_length_m: float
    half_width_m: float
    measured: bool

    def offsets(self, p: Point) -> tuple[float, float]:
        """`(along, across)` the centreline from the midpoint, in metres."""
        h = math.radians(self.heading_deg)
        dx, dy = p.x - self.center.x, p.y - self.center.y
        return dx * math.cos(h) + dy * math.sin(h), -dx * math.sin(h) + dy * math.cos(h)

    def contains(self, p: Point, *, margin_m: float = 0.0) -> bool:
        """Whether `p`, or anything within `margin_m` of it, is on the strip."""
        along, across = self.offsets(p)
        return (
            abs(along) <= self.half_length_m + OVERRUN_M + margin_m
            and abs(across) <= self.half_width_m + margin_m
        )

    def nearest_clear(self, p: Point, *, margin_m: float = 25.0) -> Point:
        """`p` moved square off the centreline to just outside the strip.

        Sideways rather than off the end: the long sides are where a field's
        own parking, dispersal and perimeter road are, and the ends are the
        approach.
        """
        _, across = self.offsets(p)
        side = 1.0 if across >= 0.0 else -1.0
        shift = self.half_width_m + margin_m - abs(across)
        return p.point_from_heading(self.heading_deg + 90.0 * side, shift)


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


_CACHE: dict[tuple[str, int], list[Strip]] = {}


def strips(airport: Airport, terrain: Terrain) -> list[Strip]:
    """Every runway of `airport` as a keep-out strip (memoised per field)."""
    key = (terrain.name, airport.id)
    if key not in _CACHE:
        _CACHE[key] = _strips(airport, terrain)
    return _CACHE[key]


def _strips(airport: Airport, terrain: Terrain) -> list[Strip]:
    localizers = [
        b.position(terrain)
        for b in theater_beacons(terrain)
        if b.kind == "ILS_LOCALIZER" and b.airfield_id == airport.id
    ]
    headings = [r.main.heading % 180 for r in airport.runways]
    out = []
    for runway, heading in zip(airport.runways, headings):
        parallel = headings.count(heading) > 1
        best: Optional[tuple[float, float]] = None
        for loc in localizers:
            distance = airport.position.distance_to_point(loc)
            if not 0.0 < distance <= _LOCALIZER_MAX_M:
                continue
            bearing = airport.position.heading_between_point(loc)
            if min(_angle(bearing, heading), _angle(bearing, heading + 180)) > (
                _LOCALIZER_MATCH_DEG
            ):
                continue
            if best is None or distance > best[1]:
                best = (bearing, distance)
        width = KEEP_OUT_M + (_PARALLEL_EXTRA_M if parallel else 0.0)
        if best is None:
            out.append(
                Strip(airport.name, runway.name, airport.position, float(heading),
                      _DEFAULT_HALF_LENGTH_M, width + _UNMEASURED_EXTRA_M, False)
            )  # fmt: skip
        else:
            bearing, distance = best
            out.append(
                Strip(airport.name, runway.name, airport.position, bearing % 180,
                      max(distance - _LOCALIZER_STANDOFF_M, 800.0), width, True)
            )  # fmt: skip
    return out


def fouled_strip(
    p: Point, airports: Iterable[Airport], terrain: Terrain
) -> Optional[Strip]:
    """The runway strip `p` stands on, among `airports`, or `None`."""
    for airport in airports:
        if airport.position.distance_to_point(p) > 6_000.0:
            continue
        for strip in strips(airport, terrain):
            if strip.contains(p):
                return strip
    return None


def field_strips(
    airports: Iterable[Airport],
    terrain: Terrain,
    near: Point,
    *,
    within_m: float = 8_000.0,
) -> list[Strip]:
    """Every strip of every field within `within_m` of `near`."""
    return [
        strip
        for airport in airports
        if airport.position.distance_to_point(near) <= within_m
        for strip in strips(airport, terrain)
    ]


def clear_of_runways(
    p: Point, runway_strips: Sequence[Strip], *, radius_m: float = 0.0
) -> bool:
    """Whether nothing within `radius_m` of `p` is on any of `runway_strips`."""
    return not any(s.contains(p, margin_m=radius_m) for s in runway_strips)


def push_clear(group: Group, runway_strips: Sequence[Strip]) -> int:
    """Move every unit of `group` standing on a strip to just outside it.

    The safety net, not the placement: a caller picks a clear position first,
    and this catches the one unit a dispersal or a terrain snap carried back
    onto the runway — which a snap will, because a runway is the clearest open
    ground on the map. Returns how many units moved.
    """
    moved = 0
    for unit in group.units:
        for _ in range(len(runway_strips) + 1):
            hit = next((s for s in runway_strips if s.contains(unit.position)), None)
            if hit is None:
                break
            unit.position = hit.nearest_clear(unit.position)
            moved += 1
    if moved:
        log.debug("units moved off a runway", group=group.name, units=moved)
    return moved
