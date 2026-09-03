"""Waypoints that sit on the terrain instead of floating above it.

pydcs writes every waypoint altitude as metres AMSL (`alt_type="BARO"`) and
carries no elevation data of its own, so two classes of waypoint come out
wrong:

- **base waypoints** — the take-off point `flight_group_from_airport` builds
  and the point `land_at` appends are both hard-coded to `alt = 0`, i.e.
  buried under any field above sea level (Vaziani, Incirlik, Kutaisi …);
- **ground-target waypoints** — a steerpoint placed on a convoy, a depot or a
  SAM site inherits the route's ingress altitude, so it hangs kilometres above
  the thing it marks.

In the cockpit those altitudes *are* the steerpoint elevations: the F-16's
CCRP/CCIP solution, the HUD symbology and the ME readout all take them at face
value. Both cases therefore have to be put back on the deck, and the elevation
comes from the project's own `MapOverlay` raster (`elevation_at`) since pydcs
has no height map.

Design rule (as in `core/air_defense.py` / `core/map_draw.py`): absolute world
`Point` in, raw pydcs objects in, a built pydcs object out.

    from dcs_mission_creator.core import waypoints

    waypoints.add_ground_waypoint(player, scene.route_mid, overlay=ov,
                                  speed=750, name="CONVOY AO")
    waypoints.snap_base_waypoints(m, ov)   # once, just before `m.save(...)`

Only client-flown routes want a deck-level target waypoint: an AI flight flies
the altitudes on its route, so grounding one of its turning points flies it
into the terrain. `snap_base_waypoints` is safe for every group — take-off and
landing points are ground events already.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

import structlog

from dcs_mission_creator.core import mission_kit

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.point import MovingPoint
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: Waypoint types that are, by definition, on the airfield surface. pydcs emits
#: the take-off variants from `flight_group_from_airport` / `flight_group` and
#: "Land" from `land_at`; all of them ship with `alt = 0`.
#:
#: Public because it is the answer to "is this point a flight profile or an
#: airfield event", which `core/audit.py` has to ask before it complains about
#: an altitude or a speed: the take-off and landing points carry the field's
#: elevation and pydcs's hard-coded 108 kt, and neither is a defect.
BASE_POINT_TYPES = frozenset(
    {
        "TakeOff",
        "TakeOffParking",
        "TakeOffParkingHot",
        "TakeOffGround",
        "TakeOffGroundHot",
        "Land",
    }
)


def ground_elevation_m(overlay: MapOverlay, position: Point) -> float:
    """Terrain elevation (m AMSL) under `position`; 0.0 outside the overlay.

    The raster is clipped per theater, so a point beyond its bounds would index
    the array from the wrong end. Sea level is the honest answer there — it
    matches what pydcs already writes — and the miss is logged.
    """
    b = overlay.manifest.bounds
    if not (b.bottom <= position.x <= b.top and b.left <= position.y <= b.right):
        log.warning(
            "elevation lookup outside overlay bounds, using sea level",
            theater=overlay.theater,
            x=position.x,
            y=position.y,
        )
        return 0.0
    return float(overlay.elevation_at(position))


def add_ground_waypoint(
    group: FlyingGroup,
    position: Point,
    *,
    overlay: MapOverlay,
    speed: float = 600,
    name: str | None = None,
) -> MovingPoint:
    """Add a waypoint whose altitude is the terrain elevation under it.

    For steerpoints that mark something on the ground — the convoy, the depot,
    the SAM site. The run-in altitude belongs on the preceding IP waypoint;
    this one only has to tell the jet how high the *target* is.

    `speed` is **km/h true airspeed**, the unit every pydcs speed argument
    takes and none of them name (`add_waypoint` stores `speed / 3.6` m/s).
    A knots-shaped number here commands roughly half the intended speed —
    see the unit rule in CLAUDE.md.
    """
    return group.add_waypoint(
        position, altitude=ground_elevation_m(overlay, position), speed=speed, name=name
    )


def snap_base_waypoints(m: Mission, overlay: MapOverlay) -> None:
    """Put every take-off / landing waypoint at its field elevation.

    Walks all flying groups in the mission, so it cannot miss the flight added
    after this call site was written. Call it once at the end of `build_miz`,
    just before `m.save(...)`.
    """
    snapped = 0
    for group in mission_kit.flying_groups(m):
        snapped += _snap_group_base_points(group, overlay)
    log.debug("base waypoints snapped to field elevation", count=snapped)


def _snap_group_base_points(group: FlyingGroup, overlay: MapOverlay) -> int:
    """Snap one group's ground points; also its units when it starts on a field."""
    snapped = 0
    for i, point in enumerate(group.points):
        if point.type not in BASE_POINT_TYPES:
            continue
        elevation = ground_elevation_m(overlay, point.position)
        point.alt = elevation
        point.alt_type = "BARO"
        if i == 0:
            for u in group.units:
                u.alt = elevation
                u.alt_type = "BARO"
        snapped += 1
    return snapped


def set_departure_speeds(m: Mission) -> None:
    """Give every departure waypoint the flight's own climb-out speed.

    pydcs's `add_runway_waypoint` hard-codes `speed = 200 / 3.6` — **108 kt**,
    at 300 m AGL — and exposes no parameter for it, so the first waypoint after
    rotation orders every flight in the mission to fly slower than it can. For
    `idlib_gauntlet`'s Pontiac, an F/A-18C at ~19.6 t (two 330 gal tanks, four
    GBU-12 on BRU-33 racks, ATFLIR, three AAMs), holding 300 m at 108 kt needs
    a lift coefficient of **2.8** against a CLmax of about 1.8 — the command
    sits 19 % below the jet's stall speed in that configuration. What the AI
    does with an unflyable speed is pitch to max alpha and firewall the
    throttle, which is the reported symptom: a very high angle of attack off
    the runway, then full afterburner, all the way to the first en-route point.

    The fix is the flight's *next* en-route speed — the mission already tuned
    that per airframe (`speed` is km/h TAS everywhere, see CLAUDE.md), so the
    departure leg becomes an ordinary accelerating climb instead of a speed
    discontinuity, and no per-airframe table has to be invented here. Speeds
    are only ever raised, so a mission that set its own departure speed keeps
    it, and running this twice changes nothing.

    The **approach** runway waypoint carries the same hard-coded 108 kt and is
    deliberately left alone: by then the jet is light (fuel burned, stores
    gone) and 108 kt is near its real approach speed rather than 19 % below
    stall, and DCS runs its own pattern logic off the landing waypoint.

    Missions never call this — `MissionBuilder.build_miz` does, for the same
    reason as `snap_base_waypoints`: a flight added later cannot miss it.
    """
    fixed = 0
    for group in mission_kit.flying_groups(m):
        fixed += _set_group_departure_speed(group)
    log.debug("departure waypoints given climb-out speed", count=fixed)


def _set_group_departure_speed(group: FlyingGroup) -> int:
    """Raise one group's departure waypoint to its first en-route speed."""
    points = group.points
    if len(points) < 3 or points[0].type not in BASE_POINT_TYPES:
        return 0
    departure = points[1]
    # `add_runway_waypoint` is the only point pydcs writes as AGL.
    if departure.type != "Turning Point" or departure.alt_type != "RADIO":
        return 0
    cruise = next((p.speed for p in points[2:] if p.speed > 0), 0.0)
    if cruise <= departure.speed:
        return 0
    departure.speed = cruise
    return 1


def clear_terrain(
    route: Sequence[Point],
    altitudes: Sequence[float],
    *,
    overlay: MapOverlay,
    clearance_m: float = 150.0,
    sample_m: float = 50.0,
) -> list[float]:
    """Raise `altitudes` until nothing on the route is inside the terrain.

    Two separate ways a hand-written altitude buries a route, and a mountain
    theater hits both:

    - **the waypoint itself.** An altitude is metres AMSL, so "800 m through
      the gorge" is 800 m *above the sea*, not above the valley floor. On the
      Caucasus that number is underground for most of the map;
    - **the leg between two waypoints.** DCS ramps linearly from one waypoint's
      altitude to the next, so a chord drawn between two points that are each
      safely over their own valley floor still goes through the spur the river
      bends around. This is the half that survives a per-waypoint fix, and it
      is the reason this takes the whole route rather than one point.

    Both are checked against the elevation raster and answered the same way:
    the *lower* end of an offending leg is lifted, so a descending profile
    stays a descending profile and only the ramp that would have hit rock
    moves. Altitudes are never lowered — a mission's own numbers are a floor,
    and this only says where they are not survivable.

    `clearance_m` is how far above the ground the whole route has to stay.
    Legs are sampled every `sample_m`, whose default is the elevation raster's
    own cell size — sampling coarser than the data steps straight over a
    one-cell spur, and the whole point here is the ground *between* the points
    somebody wrote down. Returns a new list; the input is not modified.
    """
    if len(route) != len(altitudes):
        raise ValueError(
            f"route has {len(route)} points but {len(altitudes)} altitudes"
        )
    alts = [
        max(float(a), ground_elevation_m(overlay, p) + clearance_m)
        for p, a in zip(route, altitudes)
    ]
    # Each pass can only raise an altitude, and a raised end can only relax the
    # legs either side of it, so this converges; the bound is a guard, not a
    # schedule.
    for _ in range(len(alts)):
        if not _raise_offending_legs(
            route, alts, overlay, clearance_m=clearance_m, sample_m=sample_m
        ):
            break
    return alts


def _raise_offending_legs(
    route: Sequence[Point],
    alts: list[float],
    overlay: MapOverlay,
    *,
    clearance_m: float,
    sample_m: float,
) -> bool:
    """Lift one end of every leg that cuts terrain. True if anything moved."""
    moved = False
    for i in range(len(route) - 1):
        need_a, need_b = _leg_requirement(
            route[i],
            route[i + 1],
            alts[i],
            alts[i + 1],
            overlay,
            clearance_m=clearance_m,
            sample_m=sample_m,
        )
        if need_a <= alts[i] and need_b <= alts[i + 1]:
            continue
        # Raising either end clears the leg; take the one that costs less
        # altitude, which on a descent is the end that was already higher.
        if need_a - alts[i] <= need_b - alts[i + 1]:
            alts[i] = need_a
        else:
            alts[i + 1] = need_b
        moved = True
    return moved


def leg_violation(
    a: Point,
    b: Point,
    alt_a: float,
    alt_b: float,
    overlay: MapOverlay,
    *,
    clearance_m: float = 150.0,
    sample_m: float = 50.0,
    floor_m: float | None = None,
) -> tuple[float, float]:
    """How far this leg goes *into* the terrain, and where along it.

    Returns `(metres short, fraction along)` — `(0.0, 0.0)` for a leg that
    clears. `clear_terrain` answers the same question by solving for what each
    end would have to be; this answers it as a depth, which is what a planner
    choosing *where to put another waypoint* needs. `core/route_plan.py` inserts
    at the returned fraction and re-asks.

    Sampling matches `clear_terrain`'s — same walk, `sample_m` default included
    — so the two cannot disagree about whether a route is flyable; a planner
    that used a coarser sampler would hand back corridors the mission then had
    to lift. `floor_m` is the waterline; see `_walk_leg`.
    """
    worst, where = 0.0, 0.0
    for f, ground in _walk_leg(a, b, overlay, sample_m=sample_m, floor_m=floor_m):
        short = (ground + clearance_m) - (alt_a * (1.0 - f) + alt_b * f)
        if short > worst:
            worst, where = short, f
    return worst, where


def _walk_leg(
    a: Point,
    b: Point,
    overlay: MapOverlay,
    *,
    sample_m: float,
    floor_m: float | None = None,
) -> Iterator[tuple[float, float]]:
    """Every sample along the leg as `(fraction along, ground elevation)`.

    One walk for both leg checks below. They ask different questions of the
    same points — one for the depth of the worst penetration, one for what each
    end would have to be — and having written the stepping twice is how the two
    could have come to disagree about which cell a leg passes through.

    `floor_m` raises any ground below it to it, and the case it exists for is
    **water**: the elevation layer holds depth below datum out at sea, so a
    wave-top leg reads as tens of metres underground unless the waterline is
    the floor. `None` leaves the raster alone, because a below-datum reading is
    real data everywhere except under an aeroplane.
    """
    steps = max(1, int(a.distance_to_point(b) / sample_m))
    for step in range(steps + 1):
        f = step / steps
        here = a.new_in_same_map(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)
        ground = ground_elevation_m(overlay, here)
        yield f, ground if floor_m is None else max(floor_m, ground)


def _leg_requirement(
    a: Point,
    b: Point,
    alt_a: float,
    alt_b: float,
    overlay: MapOverlay,
    *,
    clearance_m: float,
    sample_m: float,
) -> tuple[float, float]:
    """What each end would have to be, alone, for this leg to clear the ground.

    The leg is a straight ramp from `alt_a` to `alt_b`, so a sample at fraction
    `f` sits at `alt_a (1-f) + alt_b f`. Solving that for one end with the
    other held fixed gives what it would take to lift the ramp over the highest
    ground on the leg. Both answers are returned; the caller picks.
    """
    need_a, need_b = alt_a, alt_b
    for f, ground in _walk_leg(a, b, overlay, sample_m=sample_m):
        floor = ground + clearance_m
        if alt_a * (1.0 - f) + alt_b * f >= floor:
            continue
        if f < 1.0:
            need_a = max(need_a, (floor - alt_b * f) / (1.0 - f))
        if f > 0.0:
            need_b = max(need_b, (floor - alt_a * (1.0 - f)) / f)
    return need_a, need_b
