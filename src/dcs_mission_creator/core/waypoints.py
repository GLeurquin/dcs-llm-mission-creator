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

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.point import MovingPoint
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

# Waypoint types that are, by definition, on the airfield surface. pydcs emits
# the take-off variants from `flight_group_from_airport` / `flight_group` and
# "Land" from `land_at`; all of them ship with `alt = 0`.
_BASE_POINT_TYPES = frozenset(
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
    for coalition in m.coalition.values():
        for country in coalition.countries.values():
            for group in (*country.plane_group, *country.helicopter_group):
                snapped += _snap_group_base_points(group, overlay)
    log.debug("base waypoints snapped to field elevation", count=snapped)


def _snap_group_base_points(group: FlyingGroup, overlay: MapOverlay) -> int:
    """Snap one group's ground points; also its units when it starts on a field."""
    snapped = 0
    for i, point in enumerate(group.points):
        if point.type not in _BASE_POINT_TYPES:
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
