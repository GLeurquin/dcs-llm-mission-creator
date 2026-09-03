"""Site a mission before any of it is written (project-owned).

`core/route_plan.py` answers "can the aeroplane fly this line". This answers the
question that comes *before* it: **where does everything go, and does that
layout hold together?** Between them they cover the two halves of planning a
mission that were, until now, a folder of throwaway scripts.

Building `ansariyah_works` took about fifteen of those scripts, and they asked
three questions over and over:

1. *Find me a place near here that is flat, clear, on a road, not in a town, in
   this height band, and masked from that radar.*
2. *How far is each of these things from each of those, and does it clear their
   envelope?*
3. *What is this place called?* — because a waypoint named `ALQIN` after the
   village under it is a waypoint somebody can check against a map, and
   `_CORRIDOR` tables are written in degrees for exactly that reason.

`MapOverlay.find_placement` answers a slice of (1) and hands back bare `Point`s,
so every caller writes the reporting loop again. Nothing answered (2) at all,
and that is the expensive one: `ansariyah_works` had its plant sited, its
corridor planned and its briefing half-written before anyone measured the
distance from the target to the southern coastal battery and found the target
inside its envelope. That is a one-line check that arrived four hours late.

    from dcs_mission_creator.core import survey

    for spot in survey.spots(overlay, anchor, 20_000.0, require, count=8):
        print(spot.row())                       # pasteable lat/lng + terrain

    print(survey.report(survey.reaches(
        overlay,
        {"TARGET": plant, "FEET DRY": crossing},
        [survey.Site("S-125 Tartus", tartus, 18_000.0),
         survey.Site("S-200", jableh, 160_000.0, defends_objective=True)],
        agl_m=150.0,
    )))

Design rule, as in `core/routing.py`, `core/frontline.py` and
`core/route_plan.py`: absolute world `Point`s and a `MapOverlay` in, plain data
out. No pydcs groups, no mission, no opinion about what any of it means.

**The objective's own defences are declared, not inferred.** `core/routing.py`
skips a ring covering the target because a *route* cannot detour out of one, and
borrowing that rule here was the first thing tried and the first thing wrong: the
defect this module was written after — a coastal battery 9.4 km from a target it
had no business reaching — *is* a ring covering the objective, and the derived
rule explained it away. Only the author can tell the point defence that belongs
on the works from the belt that reaches it by accident, so they say which is
which and the check believes nothing else.

**Margins are measured against what a system reaches, not against the ring the
map draws.** Those are different numbers and the difference is the reveal
policy: at `veteran` a ring is drawn a quarter wider and four kilometres off, so
`ansariyah_works`' coast crossing is 18.1 and 16.4 km outside what the two
coastal batteries can actually do and only 9.7 and 8.2 km outside the circles
the player is shown. The first pair is what decides whether a pilot who complies
with the briefing lives; the second is what the plan *looks* like. `reaches`
reports the first and `drawn_margin_m` the second, because a seam check made
against the wrong one is either a lie or a mission that throws away good ground.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

import structlog

from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import reveal_policy

if TYPE_CHECKING:
    from dcs.mapping import Point

    from dcs_mission_creator.map_overlay.placement import Placement
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: How far from a spot to look for a settlement worth naming it after. Wider
#: than it sounds: in the Syrian coastal hills the nearest named place to a
#: candidate is usually inside 2 km, and anything past this is not a name for
#: *this* spot, it is the next valley.
PLACES_WITHIN_M = 8_000.0

#: Default height above the ground to test a threat's line of sight at. A route
#: is the thing being checked, and 150 m AGL is the bottom of what this project
#: writes into a land corridor.
DEFAULT_AGL_M = 150.0

#: How high a ground radar's antenna sits. `MapOverlay.line_of_sight` adds this
#: to the terrain under it, and two points on the deck fail across the gentlest
#: rise — the same reason `core/lua/autolase.lua` lifts both ends of its own
#: test.
RADAR_MAST_M = 6.0


@dataclass(frozen=True)
class Spot:
    """One candidate position and everything a siting decision asks about it."""

    position: "Point"
    lat: float
    lng: float
    elevation_m: int
    slope_deg: float
    vegetation: str
    road_m: float
    built_up: bool
    prominence_m: float
    #: Nearest named settlements, closest first — what to call a waypoint here.
    places: tuple[str, ...] = ()

    def row(self) -> str:
        """One line, in the shape a `_CORRIDOR`-style constant wants.

        Degrees first and deliberately: a mission's fixed positions are written
        as `(lat, lng)` so each one can be checked against a real map, which is
        the lesson `daryal_run` paid for with two waypoints inside a mountain.
        """
        near = self.places[0] if self.places else "-"
        return (
            f"{self.lat:9.4f},{self.lng:9.4f}  "
            f"({self.position.x:8.0f},{self.position.y:8.0f})  "
            f"elev {self.elevation_m:5d}  slope {self.slope_deg:4.1f}  "
            f"{self.vegetation:13s} road {self.road_m:6.0f}  "
            f"prom {self.prominence_m:6.0f}  "
            f"{'built-up ' if self.built_up else '         '}{near}"
        )


@dataclass(frozen=True)
class Site:
    """A named thing in the layout, and how far it reaches.

    `radius_m` is what the system **actually does** — the envelope a pilot dies
    inside — not the ring the F10 plan will draw for it. Zero means the site has
    no envelope at all (an EWR, a road junction, a friendly station), and then
    only distance and line of sight are reported, which is the honest answer for
    something that cannot shoot.
    """

    label: str
    position: "Point"
    radius_m: float = 0.0
    #: Is this system *for* the objective — its point defence, or an area
    #: system whose envelope the whole sortie is flown inside? Then being inside
    #: it is the mission rather than a defect, and it stays out of `covered`.
    #:
    #: Declared rather than derived, and the counter-example is why. "A ring
    #: covering the target is not a finding" is the rule `routing.avoid_threats`
    #: applies, and it is right *for a route*: you cannot detour out of an
    #: envelope your target sits in. It is wrong for a **layout**, because the
    #: first thing this module ever caught was a coastal battery 9.4 km from a
    #: target it had no business reaching — which is exactly a ring covering the
    #: objective, and exactly the defect. Geometry cannot tell the Osa emplaced
    #: on the works from the belt that reaches it by accident; only the author
    #: can, and saying so is a claim they should be able to defend the way
    #: `_threat_rings` defends leaving a ring out of the routing set.
    defends_objective: bool = False

    def drawn_margin_m(
        self, distance_m: float, difficulty: Difficulty | str
    ) -> Optional[float]:
        """The margin a player would read off the F10 map at `difficulty`.

        Smaller than the real one, because `PlanOverlay` inflates an estimated
        ring and offsets it. Only the inflation is accounted for here: the
        offset is a bearing drawn once per site at draw time and is as likely to
        move a ring away as toward you, so folding it in would report a margin
        no particular mission has. Treat this as the optimistic reading of the
        pessimistic circle, and `reaches` as the truth.
        """
        if not self.radius_m:
            return None
        return distance_m - self.radius_m * reveal_policy(difficulty).radius_factor


@dataclass(frozen=True)
class Reach:
    """What one site can do to one point."""

    point: str
    site: Site
    distance_m: float
    #: Distance past the site's true envelope. `None` when it has none.
    margin_m: Optional[float]
    #: Can the site see a target at the tested height, terrain only?
    visible: bool
    agl_m: float

    @property
    def covered(self) -> bool:
        """Inside an envelope the plan was supposed to stay out of.

        A system declared as defending the objective is excluded: being inside
        that one is the sortie, and the honest reading for it is the `visible`
        column beside it. Everything else reaching a briefed point is a finding.
        """
        return (
            not self.site.defends_objective
            and self.margin_m is not None
            and self.margin_m < 0.0
        )


def describe(
    overlay: "MapOverlay", point: "Point", *, places_within_m: float = PLACES_WITHIN_M
) -> Spot:
    """Everything the overlay knows about one position, as one record."""
    latlng = point.latlng()
    places = overlay.places(point, places_within_m)
    named = tuple(
        p.name
        for p in sorted(
            places,
            key=lambda p: point.distance_to_point(
                point.new_in_same_map(p.position.x, p.position.y)
            ),
        )
    )
    return Spot(
        position=point,
        lat=latlng.lat,
        lng=latlng.lng,
        elevation_m=overlay.elevation_at(point),
        slope_deg=overlay.slope_at(point),
        vegetation=overlay.vegetation_at(point).name,
        road_m=overlay.distance_to_road_m(point),
        built_up=overlay.is_built_up(point),
        prominence_m=overlay.local_prominence_m(point),
        places=named[:4],
    )


def spots(
    overlay: "MapOverlay",
    near: "Point",
    radius_m: float,
    require: "Placement",
    *,
    count: int = 8,
    pool: int = 60,
    places_within_m: float = PLACES_WITHIN_M,
) -> list[Spot]:
    """Candidate positions matching `require`, nearest to `near` first.

    A thin ranking-and-reporting layer over `MapOverlay.find_placement`, which
    is where all the raster work lives and stays. Two things it adds, and both
    are why the loop kept being rewritten by hand:

    - **Order.** `find_placement` *samples* its mask, so it answers "a cell that
      qualifies" and the first one back may be at the far edge of the search.
      Siting wants "the best one near where I asked", so a wider pool is drawn
      and sorted by distance to the anchor. Sampling is seeded from the
      overlay's own `seed`, so both are reproducible; what changes is which of
      the qualifying cells you are shown first.
    - **The report.** A bare `Point` is not enough to decide anything. Every
      candidate comes back described, with the settlements near it, so a
      position and the name to give it arrive together.
    """
    found = overlay.find_placement(near, radius_m, require, count=max(pool, count))
    ranked = sorted(found, key=near.distance_to_point)
    log.debug("surveyed spots", found=len(found), returned=min(count, len(ranked)))
    return [
        describe(overlay, p, places_within_m=places_within_m) for p in ranked[:count]
    ]


def reaches(
    overlay: "MapOverlay",
    points: Mapping[str, "Point"],
    sites: Sequence[Site],
    *,
    agl_m: float = DEFAULT_AGL_M,
    mast_m: float = RADAR_MAST_M,
) -> list[Reach]:
    """Every (point, site) pair: how far, how much margin, and can it see you.

    This is the check that a layout is legal before a route is planned around
    it. Run it on the target, the coast crossing, every corridor point and every
    friendly station against every emplaced red system, and read two columns:
    nothing briefed may be `covered`, and anything not `visible` is masked by
    terrain and therefore survivable even where it is.

    Mark the objective's own defences `defends_objective` and they stay off the
    findings list while staying on the table — on `ansariyah_works` that is the
    Osa on the works, whose ring the run-in has to enter, and the S-200 the whole
    sortie is flown inside. Everything else reaching a briefed point is a
    finding, **including a ring over the target**: that was the actual defect
    this module was written after, so it is the one thing the check must never
    explain away on its own.

    **Line of sight is terrain only, and that is the whole model DCS has.**
    There is no earth curvature in the game, so a deck run across open water is
    seen as far as a radar's detection range reaches and this will say so. Over
    water it says so slightly *too* loudly: the elevation raster holds depth
    below datum out at sea, so `agl_m` is measured from a negative number and
    the test puts the aircraft a few tens of metres lower than it flies. The
    error is in the safe direction — it reports visible more often than the game
    will — and correcting it would mean lying about the raster.
    """
    out: list[Reach] = []
    for label, point in points.items():
        for site in sites:
            distance = site.position.distance_to_point(point)
            out.append(
                Reach(
                    point=label,
                    site=site,
                    distance_m=distance,
                    margin_m=distance - site.radius_m if site.radius_m else None,
                    visible=overlay.line_of_sight(
                        site.position, point, eye_a_m=mast_m, eye_b_m=agl_m
                    ),
                    agl_m=agl_m,
                )
            )
    return out


def report(
    rows: Sequence[Reach], *, difficulty: Difficulty | str = Difficulty.TRAINED
) -> str:
    """`reaches` as a table, one line per pair, worst first within each point.

    The `DRAWN` column is what the same margin looks like on the F10 map at
    `difficulty` — always the tighter number, and printed beside the real one so
    a seam check cannot quietly be made against the wrong circle.
    """
    if not rows:
        return "no points to survey"
    head = (
        f"{'POINT':14s} {'SITE':22s} {'KM':>7s} {'REACH':>7s} "
        f"{'MARGIN':>8s} {'DRAWN':>8s}  LOS"
    )
    lines = [head, "-" * len(head)]
    for row in sorted(rows, key=lambda r: (r.point, r.margin_m or 1e12)):
        reach = f"{row.site.radius_m / 1000:7.1f}" if row.site.radius_m else "      -"
        margin = (
            f"{row.margin_m / 1000:8.1f}" if row.margin_m is not None else "       -"
        )
        drawn = row.site.drawn_margin_m(row.distance_m, difficulty)
        drawn_s = f"{drawn / 1000:8.1f}" if drawn is not None else "       -"
        flag = "SEEN" if row.visible else "masked"
        mark = "  <-- INSIDE" if row.covered else ""
        if row.site.defends_objective and row.margin_m is not None and row.margin_m < 0:
            # Inside by design. Say so rather than leaving the row looking clean:
            # the reader still has to know the ring is over this point and that
            # the answer to it is altitude or terrain rather than a detour.
            mark = "  (defends the objective)"
        lines.append(
            f"{row.point:14s} {row.site.label:22s} {row.distance_m / 1000:7.1f} "
            f"{reach} {margin} {drawn_s}  {flag}{mark}"
        )
    return "\n".join(lines)


def covered(rows: Sequence[Reach]) -> list[Reach]:
    """The pairs where a briefed point sits inside a system's real envelope.

    The one result worth a non-zero exit code: everything else on the table is
    information, and this is the layout being wrong. Only a site declared as
    defending the objective is excluded — see `Site.defends_objective`.
    """
    return [row for row in rows if row.covered]
