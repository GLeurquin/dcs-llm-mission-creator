"""Mission-gen placement helpers: archetype defaults around `TacticalScene`.

Mission builders import these instead of constructing `Placement` filters by
hand. Each helper applies sane defaults for one unit archetype (convoy spawn,
SAM site, EWR, AAA, MANPADS, infantry). Sampling is reproducible because the
`MapOverlay` behind the scene carries the seed — see `MapOverlay.seed`.

The helpers do not touch DCS objects — they only return `Point` instances.
Mission builders thread the points into `vehicle_group(..., position=p)` /
`add_waypoint(p, move_formation=PointAction.OnRoad)` themselves.

The exception is `snap_units_clear`, which mutates a `VehicleGroup` in place
after pydcs has applied its formation, because we cannot otherwise constrain
individual scatter offsets.
"""

from __future__ import annotations

import math

import structlog
from dcs.mapping import Point
from dcs.terrain.terrain import Terrain
from dcs.unitgroup import VehicleGroup

from dcs_mission_creator.map_overlay.placement import Placement, Vegetation
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene

log = structlog.get_logger(__name__)

# Standard "no canopy, no water" exclusion. Ground vehicles, AAA, SAMs and
# EWR all want this — DCS ground units render and engage poorly inside dense
# canopy and cannot stand in water.
NO_FOREST: tuple[Vegetation, ...] = (
    Vegetation.WATER,
    Vegetation.DENSE_FOREST,
    Vegetation.LIGHT_FOREST,
)
# Default dilation (m) around the `NO_FOREST` mask. 80 m is roughly two
# elevation cells at the v1 50 m resolution — enough to keep a single unit
# off the canopy edge, not enough to absorb a Scattered formation that
# spreads as `unit_count × 20 m` (see `snap_units_clear`).
FOREST_BUFFER_M = 80.0
# Search radius for `snap_units_clear` per-unit relocation.
UNIT_SNAP_RADIUS_M = 250.0


def find_clear_spot(
    overlay: MapOverlay,
    anchor: Point,
    terrain: Terrain,
    *,
    radius_m: float,
    require: Placement | None = None,
) -> Point:
    """Return a non-forest, non-water position near `anchor`, never empty.

    Tries `require` (with `NO_FOREST` + `FOREST_BUFFER_M` baked in if absent),
    widens the radius, then drops the buffer, then drops every tactical
    filter except `not_in=NO_FOREST`. Last resort: single-cell vegetation
    spiral around `anchor`. Mountain/valley terrain (Kodori, Daryal, etc.)
    routinely leaves the strict pass empty; falling back to a raw offset
    drops units into canopy.
    """
    base = require if require is not None else Placement()
    if not base.not_in:
        base = base.merged_with(not_in=NO_FOREST, forest_buffer_m=FOREST_BUFFER_M)
    attempts: list[tuple[Placement, float]] = [
        (base, radius_m),
        (base, radius_m * 2.0),
        (base.merged_with(forest_buffer_m=0.0), radius_m * 2.0),
        (Placement(not_in=NO_FOREST), radius_m * 4.0),
    ]
    for req, r in attempts:
        spots = overlay.find_placement(anchor, r, req)
        if spots:
            return spots[0]
    for r in range(50, int(radius_m * 4) + 1, 50):
        for deg in range(0, 360, 15):
            dx = r * math.cos(math.radians(deg))
            dy = r * math.sin(math.radians(deg))
            cand = Point(anchor.x + dx, anchor.y + dy, terrain)
            if overlay.vegetation_at(cand) not in NO_FOREST:
                return cand
    # Every strategy failed. Handing back the anchor drops the unit wherever it
    # was — in the canopy or the water this function exists to avoid — so say so
    # rather than letting a mission ship with a drowned platoon in silence.
    log.warning(
        "no clear spot found, falling back to the raw anchor",
        x=round(anchor.x),
        y=round(anchor.y),
        radius_m=radius_m,
        vegetation=overlay.vegetation_at(anchor).name,
    )
    return anchor


def snap_units_clear(
    overlay: MapOverlay,
    terrain: Terrain,
    group: VehicleGroup,
    *,
    search_radius_m: float = UNIT_SNAP_RADIUS_M,
) -> None:
    """Nudge each unit off any forest/water cell after platoon scatter.

    pydcs `Formation.Scattered` spreads units up to ~`unit_count × 20 m` from
    the group origin, so a 9-unit platoon can blow past any reasonable
    placement-time `forest_buffer_m`. Call this once after the group is
    built — it mutates `group.units[*].position` in place.
    """
    require = Placement(not_in=NO_FOREST)
    for u in group.units:
        pt = Point(u.position.x, u.position.y, terrain)
        if overlay.vegetation_at(pt) not in NO_FOREST:
            continue
        spots = overlay.find_placement(pt, search_radius_m, require)
        if spots:
            target = spots[0]
        else:
            target = find_clear_spot(
                overlay, pt, terrain, radius_m=search_radius_m, require=require
            )
        u.position.x = target.x
        u.position.y = target.y


def convoy_spawn(
    scene: TacticalScene,
    near: Point,
    *,
    radius_m: float = 5_000.0,
    avoid_built_up: bool = True,
) -> Point:
    """Snap a convoy origin/destination onto a real road within `radius_m`.

    Wraps `MapOverlay.find_road_spawn`; raises `LookupError` if no road found.
    """
    return scene.overlay.find_road_spawn(
        near,
        radius_m=radius_m,
        min_distance_to_built_up_m=200.0 if avoid_built_up else 0.0,
    )


def sam_site_on_ridge(
    scene: TacticalScene,
    defends: Point,
    *,
    threat_axis_deg: float,
    envelope_radius_m: float = 25_000.0,
    min_prominence_m: float = 40.0,
) -> Point:
    """SA-class SAM site on prominent terrain with LOS to the defended asset.

    `threat_axis_deg` is the direction the threat *comes from* (0=N, 90=E).
    """
    return scene.place_sam_defending(
        asset=defends,
        threat_axis_deg=threat_axis_deg,
        envelope_radius_m=envelope_radius_m,
        min_prominence_m=min_prominence_m,
    )


def ewr_high_ground(
    scene: TacticalScene,
    near: Point,
    *,
    radius_m: float = 10_000.0,
    min_elevation_m: float = 300.0,
    min_prominence_m: float = 60.0,
) -> Point:
    """Single EWR location: high absolute + relative elevation, open terrain."""
    require = Placement(
        max_slope_deg=20,
        not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
        not_in_built_up=True,
        min_elevation_m=min_elevation_m,
        min_relative_height_m=min_prominence_m,
        relative_height_radius_m=3_000.0,
    )
    spots = scene.overlay.find_placement(near, radius_m=radius_m, require=require)
    if not spots:
        raise LookupError(
            f"no EWR spot within {radius_m:.0f} m of {near.x:.0f},{near.y:.0f}"
        )
    return spots[0]


def aaa_overwatch(
    scene: TacticalScene,
    defended_axis: list[Point],
    *,
    count: int = 3,
) -> list[Point]:
    """AAA on hilltops covering an ingress corridor."""
    return scene.place_aaa_overwatch(defended_axis=defended_axis, count=count)


def manpads_in_valley(
    scene: TacticalScene,
    near: Point,
    *,
    radius_m: float = 5_000.0,
) -> Point:
    """MANPADS team hidden in low ground along an ingress route."""
    require = Placement.in_valley(
        max_relative_height_m=-20.0,
        max_slope_deg=25,
        not_in=(Vegetation.WATER,),
        not_in_built_up=True,
    )
    spots = scene.overlay.find_placement(near, radius_m=radius_m, require=require)
    if not spots:
        raise LookupError("no MANPADS spot found")
    return spots[0]


def infantry_treeline(
    scene: TacticalScene,
    near: Point,
    *,
    radius_m: float = 2_000.0,
) -> Point:
    """Infantry concealed at a forest edge — light forest OK."""
    require = Placement.near_treeline(
        within_m=50.0,
        light_forest_ok=True,
        max_slope_deg=30,
        not_in_built_up=True,
    )
    spots = scene.overlay.find_placement(near, radius_m=radius_m, require=require)
    if not spots:
        raise LookupError("no infantry treeline spot found")
    return spots[0]


def load_scene(theater: str) -> TacticalScene:
    """Open the overlay for `theater` and wrap it in a `TacticalScene`."""
    return TacticalScene(overlay=MapOverlay.load(theater))
