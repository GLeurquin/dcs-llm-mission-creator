"""Plan a low route the terrain will actually allow (project-owned).

Writing a mountain corridor by hand is the most expensive thing in this
project, and it is expensive for a reason no amount of care fixes: a pydcs
altitude is metres **AMSL**, so a number that reads as "six hundred metres
through the valley" is a claim about a river bed nobody can check by eye, and
DCS then ramps *linearly* between two waypoints, so two points that each clear
their own valley floor still draw a chord through the spur the river bends
around. `daryal_run` shipped two waypoints inside a mountainside, one of them by
2.7 km, under a briefing describing a gorge run. `core/waypoints.clear_terrain`
catches that at build time — but only by lifting the route, and a corridor that
has to be lifted two kilometres is not a corridor, it is a cruise.

So this plans the corridor instead of checking it. Three questions, and the
third is the one nobody thinks to ask until the cartridge is full:

    from dcs_mission_creator.core.route_plan import (
        nav_headroom, plan_corridor, sighting, valley_path,
    )

    route = plan_corridor(overlay, [senaki, kodori_mouth, klukhori, works])
    print(route.table(("PUSH", "OCHAMCHIRA", ...)))     # paste into _CORRIDOR
    for look in sighting(overlay, route.points, [(sa11, "SA-11"), (ewr, "EWR")]):
        print(look.first_seen_at, look.summary())        # where the masking ends
    nav_headroom(len(route.points) + 1)                  # marks left in the DTC

**What is the lowest this route can be flown for a waypoint budget I can
afford?** is the question, and both halves are real. `kuban_forge`'s corridor
needs twenty-three waypoints at 250 m AGL and eleven at 600 m — and the 600 m
version is still masked from every radar that matters, because on that map the
massif does the hiding rather than the last three hundred metres. Nobody can see
that trade without measuring it, and measuring it by hand took most of a day.

Design rule, as in `core/routing.py` and `core/frontline.py`: absolute world
`Point`s and a `MapOverlay` in, plain `Point`s out. No pydcs groups, no
waypoints, no mission. What a route *means* — which valley, what the briefing
calls each point, where the IP goes — stays the mission's decision; this only
answers whether the aeroplane fits.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

import numpy as np
import structlog
from dcs.mapping import Point

from dcs_mission_creator.core import dtc, waypoints
from dcs_mission_creator.map_overlay.query import SEARCH_MAST_M

if TYPE_CHECKING:
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: Default height-above-ground bands, as `(ground at or above, height to fly)`,
#: highest band first. They are not a preference — they are the shape of the
#: trade this module exists to expose. Down on a valley floor 300 m is flyable
#: and buys the terrain masking; through a gorge at 1,500 m the walls are close
#: enough that a straight leg between two floor points cuts rock, and pretending
#: otherwise just moves the cost into the waypoint count; over a 3,000 m
#: watershed there is no "low" at all and the honest profile is a climb.
DEFAULT_AGL_BANDS: tuple[tuple[float, float], ...] = (
    (2_000.0, 900.0),
    (800.0, 600.0),
    (0.0, 300.0),
)

#: How far above the ground the whole route has to stay, at a waypoint and along
#: every leg. Matches `core/waypoints.clear_terrain`'s own default, because the
#: two have to agree about what "flyable" means or the planner hands back
#: corridors the build then lifts.
CLEARANCE_M = 150.0

#: Grid step for the valley search. Coarser than the 50 m elevation raster on
#: purpose: the search is looking for *which valley*, and at 50 m it would spend
#: its time resolving individual boulders. 750 m is fine enough to follow a
#: gorge and coarse enough that a 200 km leg is a second of work.
SEARCH_CELL_M = 750.0

#: How far outside the straight line between two anchors the search may wander.
#: A valley that doglegs 20 km to get round a massif is normal on the Caucasus;
#: much more than this and the "route" stops being recognisable as the way the
#: briefing describes.
SEARCH_PAD_M = 20_000.0

#: How sharply height is punished when choosing a path. Cost per step is
#: `distance * (1 + (elevation / 1000) ** COST_EXPONENT)`, so at a cube a 3,000 m
#: ridge costs 28 times a sea-level flat and the search will happily fly 20 km
#: further to avoid it — which is what a pilot picking a valley does.
COST_EXPONENT = 3.0

#: Route points a mission spends before its own corridor: the spawn point, the
#: departure runway gate, the approach runway gate and the landing point. The
#: cartridge's navigation tab counts all of them, so this is what a mission has
#: to subtract before asking how many plan marks will fit.
ROUTE_OVERHEAD = 4

#: A leg the planner may not subdivide further, so that a corridor through a
#: knife-edge saddle terminates instead of inserting waypoints forever. A route
#: still violating clearance at this spacing is telling you the profile is
#: wrong, not that it needs another point.
MIN_LEG_M = 800.0


AglFor = Callable[[float], float]


def agl_bands(bands: Sequence[tuple[float, float]] = DEFAULT_AGL_BANDS) -> AglFor:
    """Turn `(ground at or above, height to fly)` pairs into a height rule."""
    ordered = sorted(bands, key=lambda pair: pair[0], reverse=True)

    def height(ground_m: float) -> float:
        for floor, agl in ordered:
            if ground_m >= floor:
                return agl
        return ordered[-1][1]

    return height


@dataclass(frozen=True)
class PlannedRoute:
    """A corridor, and what the terrain did to it.

    `altitude_m` is what `core/waypoints.clear_terrain` returns for these points,
    so it is the profile the mission will actually fly. `lift_m` is how far that
    is above what the height rule asked for, and it is the number to read: a
    handful of metres is the raster being lumpy, a few hundred is one spur the
    route clips, and a kilometre means the corridor is going through a mountain
    and wants another waypoint or a higher band.
    """

    points: tuple[Point, ...]
    ground_m: tuple[float, ...]
    requested_m: tuple[float, ...]
    altitude_m: tuple[float, ...]

    @property
    def lift_m(self) -> tuple[float, ...]:
        """Per point, how far `clear_terrain` had to raise the requested altitude."""
        return tuple(a - r for a, r in zip(self.altitude_m, self.requested_m))

    @property
    def worst_lift_m(self) -> float:
        return max(self.lift_m, default=0.0)

    @property
    def length_m(self) -> float:
        return sum(a.distance_to_point(b) for a, b in zip(self.points, self.points[1:]))

    def table(self, names: Sequence[str] = (), *, speed_kph: float = 0.0) -> str:
        """The corridor as a `_CORRIDOR` table, ready to paste into a mission.

        Degrees rather than DCS metres, because a coordinate somebody can put on
        a map is a coordinate somebody can check — which is the whole reason
        `daryal_run` rewrote its route table after shipping one in raw map
        metres. Heights are above the ground, matching how every mission here
        states a low route; `speed_kph` adds the fifth column when the mission
        keeps its speeds beside its altitudes.
        """
        rows = []
        for i, (point, ground, requested) in enumerate(
            zip(self.points, self.ground_m, self.requested_m)
        ):
            name = names[i] if i < len(names) else f"WP{i:02d}"
            ll = point.latlng()
            agl = requested - ground
            speed = f", {speed_kph:.0f}" if speed_kph else ""
            leg = (
                ""
                if i == 0
                else f"  leg {self.points[i - 1].distance_to_point(point) / 1000:.1f} km,"
            )
            rows.append(
                f'    ("{name}", {ll.lat:.3f}, {ll.lng:.3f}, {agl:.0f}{speed}),'
                f"  #{leg} gnd {ground:.0f} m, flown {self.altitude_m[i]:.0f} m"
            )
        return "\n".join(rows)


@dataclass(frozen=True)
class Sighting:
    """Which corridor points one site can see, at the altitudes actually flown.

    `first_seen` is the index the masking runs out at, and it is the number a
    briefing is written from: everything before it is terrain doing the work,
    and the distance at that point is how long the player has between being
    detected and being over the target.
    """

    label: str
    site: Point
    seen: tuple[bool, ...]
    range_m: tuple[float, ...]

    @property
    def first_seen(self) -> int | None:
        return next((i for i, s in enumerate(self.seen) if s), None)

    def summary(self) -> str:
        i = self.first_seen
        if i is None:
            return f"{self.label}: masked at every point"
        return (
            f"{self.label}: first seen at point {i}, "
            f"{self.range_m[i] / 1000:.0f} km out "
            f"({sum(self.seen)}/{len(self.seen)} points exposed)"
        )


# -- the valley search -------------------------------------------------------


class _Elevation:
    """One bulk elevation window, sampled by world coordinate.

    The search reads a few hundred thousand cells and the point queries open a
    zarr read each; pulling the window once turns minutes into milliseconds.
    Out-of-extent cells come back as sea level rather than as somebody else's
    terrain, which is `read_window`'s whole reason for existing.
    """

    def __init__(self, overlay: MapOverlay, points: Sequence[Point], pad_m: float):
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        center = points[0].new_in_same_map(
            (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        )
        window = overlay.read_window(
            "elevation",
            center,
            half_width_m=(max(ys) - min(ys)) / 2.0 + pad_m,
            half_height_m=(max(xs) - min(xs)) / 2.0 + pad_m,
        )
        self._values = np.asarray(window.values, dtype=float)
        self._cell = float(window.cell_size_m)
        self._row0, self._col0 = window.row0, window.col0
        self._overlay = overlay

    def grid(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Elevation over the outer product of `xs` (north) and `ys` (east).

        The bulk form, and the reason the search runs in a second rather than a
        minute: the per-point path builds a `Point` and walks the same transform
        for every cell of a quarter-million-cell grid. The transform itself is
        `MapOverlay.cell_coords`, so this cannot come to disagree with the point
        query about which cell a coordinate is in — which would be a planner
        whose answers were quietly not about the terrain it reported. `np.trunc`
        is `cell_of`'s `int()` on an array.
        """
        height, width = self._values.shape
        row_f, _ = self._overlay.cell_coords(xs, 0.0, self._cell)
        _, col_f = self._overlay.cell_coords(0.0, ys, self._cell)
        rows = np.trunc(row_f).astype(int) - self._row0
        cols = np.trunc(col_f).astype(int) - self._col0
        rows_in = (rows >= 0) & (rows < height)
        cols_in = (cols >= 0) & (cols < width)
        values = self._values[
            np.clip(rows, 0, height - 1)[:, None],
            np.clip(cols, 0, width - 1)[None, :],
        ]
        return np.where(rows_in[:, None] & cols_in[None, :], values, 0.0)


def valley_path(
    overlay: MapOverlay,
    start: Point,
    end: Point,
    *,
    cell_m: float = SEARCH_CELL_M,
    pad_m: float = SEARCH_PAD_M,
    exponent: float = COST_EXPONENT,
) -> list[Point]:
    """The lowest way from `start` to `end`, as a dense trace.

    A least-cost search over the elevation raster, where a step's cost is its
    length times a steep function of the ground under it — so the result follows
    river valleys and takes passes at their saddles, which is what a pilot
    reading a chart picks and what nobody can pick out of a grid of numbers.

    Dense on purpose: this is not a route, it is the *set of places a route may
    go*. `plan_corridor` chooses waypoints from it.
    """
    elevation = _Elevation(overlay, (start, end), pad_m)
    x0 = min(start.x, end.x) - pad_m
    y0 = min(start.y, end.y) - pad_m
    nx = int((max(start.x, end.x) + pad_m - x0) / cell_m) + 1
    ny = int((max(start.y, end.y) + pad_m - y0) / cell_m) + 1

    ground = np.maximum(
        0.0,
        elevation.grid(
            x0 + np.arange(nx) * cell_m,
            y0 + np.arange(ny) * cell_m,
        ),
    )
    cost = 1.0 + (ground / 1000.0) ** exponent

    def index(p: Point) -> tuple[int, int]:
        return (
            min(nx - 1, max(0, round((p.x - x0) / cell_m))),
            min(ny - 1, max(0, round((p.y - y0) / cell_m))),
        )

    source, target = index(start), index(end)
    best: dict[tuple[int, int], float] = {source: 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    queue: list[tuple[float, tuple[int, int]]] = [(0.0, source)]
    steps = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)]
    while queue:
        spent, node = heapq.heappop(queue)
        if node == target:
            break
        if spent > best.get(node, math.inf):
            continue
        i, j = node
        for di, dj in steps:
            u, v = i + di, j + dj
            if not (0 <= u < nx and 0 <= v < ny):
                continue
            walked = spent + cell_m * math.hypot(di, dj) * cost[u, v]
            if walked < best.get((u, v), math.inf):
                best[(u, v)] = walked
                came[(u, v)] = node
                heapq.heappush(queue, (walked, (u, v)))

    trace: list[Point] = []
    node = target
    while True:
        trace.append(
            start.new_in_same_map(x0 + node[0] * cell_m, y0 + node[1] * cell_m)
        )
        if node == source:
            break
        parent = came.get(node)
        if parent is None:  # unreachable: hand back the straight line instead
            log.warning("no valley path found, falling back to a straight leg")
            return [start, end]
        node = parent
    trace.reverse()
    trace[0], trace[-1] = start, end
    return trace


# -- choosing the waypoints --------------------------------------------------


def plan_corridor(
    overlay: MapOverlay,
    anchors: Sequence[Point],
    *,
    agl_for: AglFor | None = None,
    clearance_m: float = CLEARANCE_M,
    max_waypoints: int = 40,
    min_leg_m: float = MIN_LEG_M,
    cell_m: float = SEARCH_CELL_M,
    pad_m: float = SEARCH_PAD_M,
) -> PlannedRoute:
    """The fewest waypoints that fly `anchors` without going through the ground.

    `anchors` is the steering — the places the route has to pass, in order, in
    the mission's own terms (the field, the valley mouth, the pass, the target).
    Between them the search finds the valley; here we thin that trace down to
    the waypoints a flight plan can carry, by repeatedly asking
    `waypoints.leg_violation` which leg goes deepest into rock and putting a
    point at exactly that spot.

    Greedy rather than optimal, and that is the right trade: the answer is used
    to decide a *profile*, and the difference between eleven waypoints and the
    theoretical ten does not change any decision. What matters is that the count
    is honest, because it is the count that has to fit in
    `dtc.MAX_NAV_POINTS` alongside the plan's own marks.

    **An intermediate anchor is not guaranteed to survive as a waypoint.** It
    steers the search, and the trace therefore goes through it, but the thinning
    keeps only the points the terrain requires — so a route may pass over the
    valley mouth the mission named without stopping there. Add it back by hand
    if the briefing needs a steerpoint on it; that is a naming decision rather
    than a flying one, and it is the mission's.
    """
    height = agl_for or agl_bands()
    trace: list[Point] = []
    for a, b in zip(anchors, anchors[1:]):
        leg = valley_path(overlay, a, b, cell_m=cell_m, pad_m=pad_m)
        trace += leg if not trace else leg[1:]

    chosen = [0, len(trace) - 1]
    # Legs are scored once each. A leg is identified by its two trace indices,
    # and an insertion only invalidates the leg it splits — without the memo the
    # search re-samples every surviving leg on every iteration, which on a
    # 180 km corridor is most of a minute of raster reads for an answer it
    # already had.
    scored: dict[tuple[int, int], tuple[float, float]] = {}
    for _ in range(max_waypoints):
        worst = _worst_leg(
            overlay, trace, chosen, height, clearance_m, min_leg_m, scored
        )
        if worst is None:
            break
        chosen.insert(worst[0] + 1, worst[1])
    else:
        log.warning(
            "corridor still cuts terrain at the waypoint cap; "
            "raise the height bands or add an anchor",
            waypoints=len(chosen),
        )

    points = tuple(trace[i] for i in chosen)
    ground = tuple(waypoints.ground_elevation_m(overlay, p) for p in points)
    requested = tuple(g + height(g) for g in ground)
    altitude = tuple(
        waypoints.clear_terrain(
            list(points), list(requested), overlay=overlay, clearance_m=clearance_m
        )
    )
    route = PlannedRoute(points, ground, requested, altitude)
    log.debug(
        "planned corridor",
        waypoints=len(points),
        length_km=round(route.length_m / 1000, 1),
        worst_lift_m=round(route.worst_lift_m),
    )
    return route


def _worst_leg(
    overlay: MapOverlay,
    trace: Sequence[Point],
    chosen: Sequence[int],
    height: AglFor,
    clearance_m: float,
    min_leg_m: float,
    scored: dict[tuple[int, int], tuple[float, float]],
) -> tuple[int, int] | None:
    """`(leg index, trace index to insert)` for the leg deepest into the ground."""

    def altitude(i: int) -> float:
        ground = waypoints.ground_elevation_m(overlay, trace[i])
        return ground + height(ground)

    worst: tuple[float, int, int] | None = None
    for k, (i, j) in enumerate(zip(chosen, chosen[1:])):
        if j - i < 2 or trace[i].distance_to_point(trace[j]) < min_leg_m:
            continue
        hit = scored.get((i, j))
        if hit is None:
            hit = waypoints.leg_violation(
                trace[i],
                trace[j],
                altitude(i),
                altitude(j),
                overlay,
                clearance_m=clearance_m,
            )
            scored[(i, j)] = hit
        short, where = hit
        if short > 0.0 and (worst is None or short > worst[0]):
            at = i + max(1, min(j - i - 1, int((j - i) * where)))
            worst = (short, k, at)
    return None if worst is None else (worst[1], worst[2])


# -- what the route is exposed to -------------------------------------------


def sighting(
    overlay: MapOverlay,
    route: Sequence[Point],
    sites: Iterable[tuple[Point, str]],
    *,
    altitudes_m: Sequence[float] = (),
    agl_for: AglFor | None = None,
    mast_m: float = SEARCH_MAST_M,
) -> list[Sighting]:
    """Which of `sites` can see each point of `route`, at the altitude flown.

    The half of a low route that cannot be argued: line of sight against the
    elevation raster, from a radar mast to the aeroplane where it actually is.
    Pass `altitudes_m` (a `PlannedRoute.altitude_m`) to ask about the planned
    profile, or leave it out and the height bands are used.

    It answers only about terrain, which is the only thing DCS models — there is
    no earth curvature in the sim, so a site with an unobstructed line sees a
    wave-top run 250 km away. A mission may promise masking and may not promise
    a radar horizon.
    """
    height = agl_for or agl_bands()
    if altitudes_m:
        heights = [
            alt - waypoints.ground_elevation_m(overlay, p)
            for p, alt in zip(route, altitudes_m)
        ]
    else:
        heights = [height(waypoints.ground_elevation_m(overlay, p)) for p in route]

    looks: list[Sighting] = []
    for site, label in sites:
        seen = tuple(
            overlay.line_of_sight(site, p, eye_a_m=mast_m, eye_b_m=max(1.0, h))
            for p, h in zip(route, heights)
        )
        looks.append(
            Sighting(
                label=label,
                site=site,
                seen=seen,
                range_m=tuple(site.distance_to_point(p) for p in route),
            )
        )
    return looks


# -- what is left for the plan ----------------------------------------------


def nav_headroom(en_route: int, *, overhead: int = ROUTE_OVERHEAD) -> int:
    """How many `PlanOverlay` marks will still fit in the cartridge's NAV tab.

    The F-16C holds `dtc.MAX_NAV_POINTS` navigation steerpoints and the flight's
    own route wins every budget fight, so this is the answer to "how much of my
    F10 plan reaches the cockpit" — and it is worth asking before the route is
    written rather than reading it out of `arm_plan`'s warning after a build.
    Negative means the route itself is over the tab and the plan gets nothing.

    `overhead` is the four points a mission does not write: the spawn, the two
    runway gates and the landing. Adding them is this function's whole job —
    the subtraction itself is `dtc.nav_headroom`, so a planner and a built
    mission cannot come to different answers about the same route.
    """
    return dtc.nav_headroom(en_route + overhead)
