"""Threat-aware routing for AI flights (project-owned).

pydcs plans AI routes as straight lines. `Mission.strike_flight` is the worst
offender — it drops an IP 30 km off the target on the reciprocal of the
departure heading and joins base → IP → target → base with no idea that a SAM
belt sits on that line. An AI package planned that way flies into the missile
engagement zone the briefing told the *player* to work around, which reads as
the package being stupid rather than the enemy being dangerous.

This module plans the same route the way a mission planner would: around the
rings, holding outside them, with the run-in as the only exposure.

    from dcs_mission_creator.core.routing import ThreatRing, avoid_threats, standoff_point

    rings = (ThreatRing(sa2_pos, 40_000.0, "SA-2"),
             ThreatRing(sa6_pos, 25_000.0, "SA-6"),
             ThreatRing(sa8_pos, 10_000.0, "SA-8"))
    ip = standoff_point(target, toward=hatay.position, threats=rings)
    legs = avoid_threats(push, ip, rings, clearance_m=5_000.0)

Design rule (as in `core/air_defense.py` / `core/waypoints.py`): absolute world
`Point`s in, plain `Point`s out — the caller turns them into waypoints, so the
same geometry serves an AI flight, a drawn corridor, or a hold point.

Rings here are the site's **briefed envelope at its true position** — the reach
the briefing names, where the launchers actually are. That is deliberately not
what `PlanOverlay` draws: the drawn ring carries the difficulty's positional
error, and routing a package around a ring known to be a few kilometres off
would bend the plan away from empty sky and leave it exposed where the site
really is. Routing is the margin that keeps a flight alive; the estimate is the
claim the player is shown. Where the two visibly disagree on the map, the
briefing has to be the thing that explains it.

Geometry only — this module never touches a group, a task or a waypoint. The
matching AI *behaviour* dial (react-on-threat, chaff/flare, ECM) lives in
`core/tasking.py` as `apply_threat_reaction`; a well-routed flight still wants
it, because a route avoids the ring the planner knew about and the option
handles the one that turns up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

import structlog

if TYPE_CHECKING:
    from dcs.mapping import Point

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ThreatRing:
    """A circular no-go envelope: where a site is, how far it reaches, its name.

    `radius_m` is the briefed envelope of the whole site, so it already covers
    the launchers spread around the radar; `label` only ever shows up in logs.
    """

    position: Point
    radius_m: float
    label: str = "threat"

    def margin_m(self, point: Point) -> float:
        """Distance from the ring edge — negative inside, positive outside."""
        return point.distance_to_point(self.position) - self.radius_m

    def covers(self, point: Point, clearance_m: float = 0.0) -> bool:
        """True if `point` is inside the ring (plus `clearance_m` of buffer)."""
        return self.margin_m(point) < clearance_m


def clear_of_threats(
    point: Point, threats: Sequence[ThreatRing], *, clearance_m: float = 0.0
) -> bool:
    """True if `point` sits outside every ring by at least `clearance_m`."""
    return all(not ring.covers(point, clearance_m) for ring in threats)


def avoid_threats(
    start: Point,
    target: Point,
    threats: Sequence[ThreatRing],
    *,
    clearance_m: float = 5_000.0,
    max_detours: int = 8,
) -> list[Point]:
    """Waypoint chain `start` → `target` that stays out of the threat rings.

    Walks the route, finds the ring it cuts deepest, and bends that leg out to
    `clearance_m` beyond that ring's edge, on whichever side costs less track
    miles; repeats until no leg enters a ring or `max_detours` bends have been
    spent. Returns `[start, …detours…, target]` — at minimum the two points it
    was given, so the caller can always route with the result.

    A leg counts as clean once it is outside the ring itself; the clearance is
    the headroom the detour is given, not a second ring to route around. Bends
    would otherwise chase their own tail — every turning point placed on a ring
    edge leaves the two legs into it fractionally inside that edge — and the
    route would spend its whole detour budget shaving metres.

    Rings that already cover `start` or `target` are ignored: a flight taking
    off inside a SAM envelope, or striking something parked in one, cannot
    detour its way out, and pretending otherwise would only bend the route
    into a ring it *could* have avoided. Pair the call with `standoff_point`
    so the exposure is a short run-in rather than the whole ingress.
    """
    relevant = [
        ring for ring in threats if not ring.covers(start) and not ring.covers(target)
    ]
    if skipped := [r.label for r in threats if r not in relevant]:
        log.debug("threat rings cover an endpoint, not routed around", rings=skipped)

    route = [start, target]
    for _ in range(max_detours):
        breach = _deepest_breach(route, relevant)
        if breach is None:
            return route
        index, ring = breach
        route.insert(
            index + 1, _bend_around(route[index], route[index + 1], ring, clearance_m)
        )
    log.warning(
        "threat-avoiding route hit the detour limit, some exposure remains",
        detours=max_detours,
        rings=[r.label for r in relevant],
    )
    return route


def standoff_point(
    target: Point,
    *,
    toward: Point,
    threats: Sequence[ThreatRing],
    min_distance_m: float = 20_000.0,
    max_distance_m: float = 70_000.0,
    step_m: float = 2_500.0,
    clearance_m: float = 3_000.0,
    arc_deg: float = 75.0,
    bearing_step_deg: float = 15.0,
) -> Point:
    """An IP / hold point on the `toward` side of `target`, outside the rings.

    Sweeps outward from `target` — nearest distance first, and at each distance
    the `toward` bearing first, then alternating either side of it out to
    `arc_deg` — and returns the first point clear of every ring. `toward` is
    normally the launch field or the friendly side of the AO, so the search
    prefers the shortest run-in from home and only swings the approach round
    the flank when the direct side is inside an envelope.

    A target ringed on every side (the usual case for something a whole IADS is
    built around) has no clean answer: the least-exposed point tried is
    returned instead, so the flight still holds where it is *least* likely to
    be shot at, and the miss is logged with the margin it settled for.
    """
    home_bearing = target.heading_between_point(toward)
    offsets = [0.0]
    offset = bearing_step_deg
    while offset <= arc_deg:
        offsets += [offset, -offset]
        offset += bearing_step_deg

    best: Optional[Point] = None
    best_margin = float("-inf")
    distance = min_distance_m
    while distance <= max_distance_m:
        for delta in offsets:
            candidate = target.point_from_heading(home_bearing + delta, distance)
            margin = min(
                (ring.margin_m(candidate) for ring in threats), default=float("inf")
            )
            if margin >= clearance_m:
                return candidate
            if margin > best_margin:
                best, best_margin = candidate, margin
        distance += step_m
    log.warning(
        "no clear standoff point found, using the least exposed one",
        margin_m=round(best_margin),
        rings=[r.label for r in threats],
    )
    return (
        best
        if best is not None
        else target.point_from_heading(home_bearing, min_distance_m)
    )


# -- internals ---------------------------------------------------------------


def _deepest_breach(
    route: Sequence[Point], threats: Sequence[ThreatRing]
) -> Optional[tuple[int, ThreatRing]]:
    """Leg index + ring for the worst ring incursion on `route`, if any."""
    worst: Optional[tuple[int, ThreatRing]] = None
    worst_depth = 0.0
    for i in range(len(route) - 1):
        for ring in threats:
            depth = ring.radius_m - _distance_to_leg(
                ring.position, route[i], route[i + 1]
            )
            if depth > worst_depth:
                worst, worst_depth = (i, ring), depth
    return worst


def _bend_around(a: Point, b: Point, ring: ThreatRing, clearance_m: float) -> Point:
    """A turning point that pushes leg `a`→`b` onto the edge of `ring`.

    Offsets perpendicular to the leg from the ring centre, in both directions,
    and keeps the one with the shorter `a` → point → `b` distance — that is the
    side the flight was already passing on.
    """
    radius = ring.radius_m + clearance_m
    bearing = a.heading_between_point(b)
    candidates = [
        ring.position.point_from_heading(bearing + 90.0, radius),
        ring.position.point_from_heading(bearing - 90.0, radius),
    ]
    return min(
        candidates, key=lambda p: a.distance_to_point(p) + p.distance_to_point(b)
    )


def _distance_to_leg(point: Point, a: Point, b: Point) -> float:
    """Shortest distance from `point` to the *segment* `a`→`b` (not the line)."""
    dx, dy = b.x - a.x, b.y - a.y
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return point.distance_to_point(a)
    t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return point.distance_to_point(a.new_in_same_map(a.x + t * dx, a.y + t * dy))
