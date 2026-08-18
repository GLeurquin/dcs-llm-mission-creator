"""Front-line geometry (project-owned helper).

A target sitting in open airspace can be attacked from any bearing, which makes
the briefed ingress a suggestion: the player circles to whichever side nothing
covers, and the SAM belts the mission was built around never get a say. A front
line is what takes that option away — a line of ground forces with air defence
strung along it, wide enough that going round the end of it costs fuel and
minutes instead of one turn.

This module owns the *geometry* of one: where the line runs, where its sectors
sit, which positions are the shoulders holding the flanks, and where the seam
— the sector a package is meant to cross — is. It builds nothing. Force
composition (what sits on each sector, which shoulder is a Buk battery and
which is a pair of Shilkas) is mission policy, exactly as in `core/routing.py`.

    front = plan_frontline(
        defends=scene.route_mid, facing=scene.hatay.position,
        standoff_m=30_000.0, span_m=110_000.0, bow_m=15_000.0,
        sectors_per_side=2, seam_width_m=30_000.0,
        overlay=scene.overlay.overlay, terrain=self._terrain,
    )

`defends` is what the line stands in front of (the AO) and `facing` is the side
the threat comes from (the player's field), so the line lands between the two,
**across** the ingress axis rather than along it. `bow_m` sweeps the wings back
toward `facing`: a straight line is flown round at its tips, a bowed one makes
the flanks a longer way in than the middle. `seam_width_m` is the frontage left
without a sector position — the crossing the briefing points the package at, and
the reason the mission still has a plan rather than a wall.

Design rule (as in `core/routing.py` / `core/air_defense.py`): absolute world
`Point`s in, absolute `Point`s out; the caller turns them into groups. Pass
`overlay` **and** `terrain` to have every position snapped clear of canopy and
water — the same pairing `core/air_defense.py` takes.

`TacticalScene.place_frontline` answers a different question: a FLOT meandering
between two anchors that are already known, biased onto prominent ground. This
one is for the case where the line has to cross a given approach axis and the
anchors are what needs deriving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import structlog

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.terrain.terrain import Terrain

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Frontline:
    """One planned front line: the trace, its positions, and the seam.

    `trace` is the whole line flank to flank, for drawing — the front is
    continuous even where its air defence is not, so the seam is a vertex of it
    like any other. `shoulders` are the two flank positions (the ones that make
    flanking expensive) and `sectors` the positions between them, ordered along
    the line from `shoulders[0]` to `shoulders[1]`. `facing_deg` is the bearing
    from the line toward the side the threat comes from, which is the heading a
    site dug in on the line wants.
    """

    trace: tuple[Point, ...]
    shoulders: tuple[Point, Point]
    sectors: tuple[Point, ...]
    seam: Point
    facing_deg: float

    @property
    def positions(self) -> tuple[Point, ...]:
        """Every position holding the line — both shoulders and every sector."""
        return (self.shoulders[0], *self.sectors, self.shoulders[1])


def plan_frontline(
    *,
    defends: Point,
    facing: Point,
    standoff_m: float,
    span_m: float,
    bow_m: float = 0.0,
    sectors_per_side: int = 2,
    seam_width_m: float = 0.0,
    overlay: Optional[MapOverlay] = None,
    terrain: Optional[Terrain] = None,
    clear_radius_m: float = 3_000.0,
) -> Frontline:
    """Plan a front line `standoff_m` in front of `defends`, across the axis.

    The line is centred on the `defends` → `facing` axis, runs perpendicular to
    it for `span_m`, and has `2 * sectors_per_side` positions spread over the
    frontage outside the seam, plus one shoulder at each tip. `bow_m` displaces
    each position toward `facing` in proportion to the square of how far out the
    wing it sits, so the tips lead and the middle trails.

    Raises `ValueError` on a line that cannot hold positions (non-positive span,
    a seam as wide as the line, a negative count).
    """
    if span_m <= 0.0:
        raise ValueError(f"span_m must be positive, got {span_m}")
    if sectors_per_side < 0:
        raise ValueError(f"sectors_per_side must be >= 0, got {sectors_per_side}")
    half = span_m / 2.0
    seam_edge = seam_width_m / 2.0
    if seam_edge >= half:
        raise ValueError(
            f"seam_width_m ({seam_width_m}) leaves no frontage inside span_m ({span_m})"
        )

    facing_deg = defends.heading_between_point(facing)
    center = defends.point_from_heading(facing_deg, standoff_m)

    def at(lateral_m: float) -> Point:
        """One position on the line, `lateral_m` off centre (signed), bowed."""
        side = 90.0 if lateral_m >= 0.0 else -90.0
        point = center.point_from_heading(facing_deg + side, abs(lateral_m))
        bow = bow_m * (lateral_m / half) ** 2
        if bow:
            point = point.point_from_heading(facing_deg, bow)
        return _clear(point, overlay, terrain, clear_radius_m)

    step = (half - seam_edge) / sectors_per_side if sectors_per_side else 0.0
    laterals = [seam_edge + (i + 0.5) * step for i in range(sectors_per_side)]
    sectors = tuple(at(-u) for u in reversed(laterals)) + tuple(at(u) for u in laterals)
    shoulders = (at(-half), at(half))
    log.debug(
        "planned front line",
        span_km=round(span_m / 1_000.0),
        seam_km=round(seam_width_m / 1_000.0),
        sectors=len(sectors),
    )
    return Frontline(
        trace=(
            shoulders[0],
            *sectors[:sectors_per_side],
            center,
            *sectors[sectors_per_side:],
            shoulders[1],
        ),
        shoulders=shoulders,
        sectors=sectors,
        seam=center,
        facing_deg=facing_deg,
    )


def _clear(
    point: Point,
    overlay: Optional[MapOverlay],
    terrain: Optional[Terrain],
    radius_m: float,
) -> Point:
    """Snap a position off canopy and water, if the caller supplied the raster.

    Same contract as `core/air_defense.py`: both arguments or neither, and one
    alone warns rather than skipping the snap in silence.
    """
    if overlay is not None and terrain is not None:
        # Imported here for the reason `core.air_defense._finish` gives: this
        # module is geometry, and `core.placement` pulls in the raster stack.
        from dcs_mission_creator.core.placement import find_clear_spot

        return find_clear_spot(overlay, point, terrain, radius_m=radius_m)
    if overlay is not None or terrain is not None:
        log.warning(
            "front-line position not snapped to clear ground: pass overlay and terrain",
            overlay=overlay is not None,
            terrain=terrain is not None,
        )
    return point
