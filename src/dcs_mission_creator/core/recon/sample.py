"""Overlay -> the arrays a frame needs. The only module here that reads a raster.

Two decisions carry most of the honesty of the finished picture.

**Fractional coverage, not class indices.** Each surface (water, forest, urban,
road, river) is sampled as a float 0..1 *fraction of the pixel covered*, not as
the class id of the nearest 50 m post. A pixel's radar return really is the
area-weighted mix of what is inside it, so a soft mixed edge is more correct than
a hard nearest-neighbour one — and it means the 50 m grid stops being the
dominant spatial frequency in the output. Sampling classes and then blurring
would be the same arithmetic in the wrong order, giving grey halos where there
should be mixtures.

**Roads come from the vector sidecar, not from `roads_dt`.** The distance
transform is quantised to 50 m cells, so a road drawn from it is a staircase four
output pixels wide however it is filtered. Walking the real polylines and running
a distance transform *at output resolution* gives a smooth ribbon of any width,
and keeps `rasterio` out of the pixel path entirely.

Elevation is smoothed in cell space before it is sampled, for a reason specific
to this data: the raster is int16 metres over terrain whose local relief is a few
metres, so around 38 % of adjacent posts in the Idlib area are bit-identical and
a gradient taken straight off it produces flat terraces separated by 1 m cliffs.
Averaging ~9 posts first takes the effective quantisation to ~0.1 m, below what
the incidence modulation can show.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

import numpy as np
import structlog
from dcs.mapping import Point
from scipy.ndimage import distance_transform_edt, gaussian_filter, map_coordinates

from dcs_mission_creator.core.recon.frame import Frame

if TYPE_CHECKING:
    from dcs_mission_creator.map_overlay.query import LayerWindow, MapOverlay

log = structlog.get_logger(__name__)

#: `vegetation` raster codes (see `map_overlay.placement.Vegetation`).
_VEG_LIGHT = 1
_VEG_DENSE = 2
_VEG_WATER = 3

#: Half-width of a drawn ribbon, in output pixels. A trunk road is ~15 m wide
#: against a 25 m pixel, so a ribbon narrower than a pixel would alias in and out
#: of existence along its length; 1.6 px is the smallest that stays continuous,
#: which is what a real product shows.
_ROAD_HALF_PX = 1.6
_RIVER_HALF_PX = 2.1

#: Elevation smoothing, in raster cells — see the module docstring.
_ELEV_SMOOTH_CELLS = 0.8

#: Slack added to every window read so bilinear sampling and the rotation both
#: have posts to work with at the frame's corners.
_MARGIN_CELLS = 4


@dataclass(frozen=True)
class Scene:
    """Everything the renderer needs about the ground, at supersampled resolution.

    Every array is `float32` and shares the same shape. `elevation_m` is metres;
    the five surface arrays are coverage fractions in 0..1; `valid` is False where
    the frame fell outside the overlay, so the renderer paints no-data instead of
    inventing ground.
    """

    frame: Frame
    elevation_m: np.ndarray
    urban: np.ndarray
    forest: np.ndarray
    water: np.ndarray
    road: np.ndarray
    river: np.ndarray
    valid: np.ndarray

    def fingerprint(self) -> bytes:
        """Hash of the sampled ground — the cache key's dependency on the terrain.

        Keyed on the *arrays* rather than on the query that produced them, so
        rebuilding the overlay invalidates a cached render instead of serving one
        the current data would no longer reproduce.
        """
        h = hashlib.sha256()
        f = self.frame
        h.update(
            f"{f.center.x:.3f},{f.center.y:.3f},{f.heading_deg:.6f},"
            f"{f.width_m},{f.height_m},{f.px_per_cell},"
            f"{f.cell_size_m},{f.supersample}".encode()
        )
        for name in (
            "elevation_m",
            "urban",
            "forest",
            "water",
            "road",
            "river",
            "valid",
        ):
            h.update(name.encode())
            h.update(np.ascontiguousarray(getattr(self, name)).tobytes())
        return h.digest()


def sample_frame(
    overlay: MapOverlay, frame: Frame, *, vector_roads: bool = True
) -> Scene:
    """Read every layer the renderer needs over `frame`, at supersampled resolution."""
    north, east = frame.world_grid(supersampled=True)

    elev, valid = _sample_elevation(overlay, frame, north, east)
    water = _coverage(overlay, frame, "vegetation", north, east, {_VEG_WATER: 1.0})
    forest = _coverage(
        overlay, frame, "vegetation", north, east, {_VEG_LIGHT: 0.55, _VEG_DENSE: 1.0}
    )
    urban = _coverage(
        overlay, frame, "buildings", north, east, {1: 0.35, 2: 0.7, 3: 1.0}
    )

    shape = elev.shape
    road = (
        _vector_ribbon(overlay, frame, "roads", _ROAD_HALF_PX)
        if vector_roads
        else np.zeros(shape, dtype=np.float32)
    )
    river = (
        _vector_ribbon(overlay, frame, "rivers", _RIVER_HALF_PX)
        if vector_roads
        else np.zeros(shape, dtype=np.float32)
    )

    log.debug(
        "recon.sampled",
        shape=shape,
        valid_frac=round(float(valid.mean()), 4),
        road_frac=round(float((road > 0.05).mean()), 5),
        water_frac=round(float((water > 0.5).mean()), 5),
        urban_frac=round(float((urban > 0.05).mean()), 5),
        elev_span_m=round(float(elev.max() - elev.min()), 1),
    )
    return Scene(
        frame=frame,
        elevation_m=elev,
        urban=urban,
        forest=forest,
        water=water,
        road=road,
        river=river,
        valid=valid,
    )


# -- layer reads -------------------------------------------------------------


def _read(overlay: MapOverlay, frame: Frame, name: str) -> LayerWindow:
    """The overlay window covering `frame` at any rotation."""
    return overlay.read_window(
        name,
        frame.center,
        half_width_m=frame.half_diagonal_m() + frame.cell_size_m * _MARGIN_CELLS,
    )


def _cell_coords(
    overlay: MapOverlay, win: LayerWindow, north: np.ndarray, east: np.ndarray
) -> list[np.ndarray]:
    """World coordinate arrays -> fractional `(row, col)` inside `win`.

    Fractional on purpose: `map_coordinates` interpolates between cells, which
    is why this cannot just call `cell_of`. The transform is the overlay's.
    """
    rows, cols = overlay.cell_coords(north, east, win.cell_size_m)
    return [rows - win.row0, cols - win.col0]


def _sample_elevation(
    overlay: MapOverlay, frame: Frame, north: np.ndarray, east: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    win = _read(overlay, frame, "elevation")
    smoothed = gaussian_filter(win.values.astype(np.float32), sigma=_ELEV_SMOOTH_CELLS)
    coords = _cell_coords(overlay, win, north, east)
    elev = map_coordinates(smoothed, coords, order=1, mode="nearest").astype(np.float32)
    valid = (
        map_coordinates(
            win.valid.astype(np.float32), coords, order=1, mode="constant", cval=0.0
        )
        > 0.5
    )
    return elev, valid


def _coverage(
    overlay: MapOverlay,
    frame: Frame,
    name: str,
    north: np.ndarray,
    east: np.ndarray,
    weights: Mapping[int, float],
) -> np.ndarray:
    """Fraction of each output pixel covered by the weighted classes.

    Builds a float mask in *cell* space — weighted per class, so a city
    contributes more than a village — and samples it bilinearly, which is what
    makes the result a mixture rather than a staircase.
    """
    win = _read(overlay, frame, name)
    mask = np.zeros(win.values.shape, dtype=np.float32)
    for code, weight in weights.items():
        mask[win.values == code] = weight
    out = map_coordinates(
        mask, _cell_coords(overlay, win, north, east), order=1, mode="nearest"
    )
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _vector_ribbon(
    overlay: MapOverlay, frame: Frame, kind: str, half_px: float
) -> np.ndarray:
    """A smooth anti-aliased ribbon for every in-frame polyline of `kind`.

    Rasterised at supersampled output resolution from the vector sidecar, then
    softened with a distance transform, so the width is set in pixels rather than
    inherited from the 50 m grid.
    """
    w, h = frame.size_px
    s = frame.supersample
    shape = (h * s, w * s)
    hit = np.zeros(shape, dtype=bool)
    terrain = frame.center._terrain

    for line in overlay.vector_lines(
        kind, frame.center, frame.half_diagonal_m() + 2_000.0
    ):
        coords = np.asarray(line.coords, dtype=np.float64)
        if len(coords) < 2:
            continue
        # Sidecars store (east, north); pydcs Point is (north, east).
        pxy = [frame.world_to_px(Point(float(n), float(e), terrain)) for e, n in coords]
        for (x0, y0), (x1, y1) in zip(pxy[:-1], pxy[1:]):
            steps = int(max(abs(x1 - x0), abs(y1 - y0)) * s * 2) + 2
            ix = np.rint(np.linspace(x0 * s, x1 * s, steps)).astype(np.int64)
            iy = np.rint(np.linspace(y0 * s, y1 * s, steps)).astype(np.int64)
            keep = (ix >= 0) & (ix < shape[1]) & (iy >= 0) & (iy < shape[0])
            hit[iy[keep], ix[keep]] = True

    if not hit.any():
        return np.zeros(shape, dtype=np.float32)
    dist = distance_transform_edt(~hit)
    assert isinstance(dist, np.ndarray)
    return np.clip(1.0 - dist / (half_px * s), 0.0, 1.0).astype(np.float32)


def road_column(
    overlay: MapOverlay,
    start: Point,
    toward: Point,
    count: int,
    *,
    spacing_m: float = 120.0,
    search_m: float = 4_000.0,
) -> list[Point]:
    """Lay `count` vehicles along the real road from `start` toward `toward`.

    Needed because a group's **build-time** positions are not a column. pydcs
    `vehicle_group_platoon` defaults to `Formation.Line`, which stacks units 20 m
    apart *abeam* the heading, and the DCS engine only strings them out along the
    road once the mission is running. Rendering those positions draws a 200 m dash
    across the countryside, at right angles to the road the briefing says the
    column is driving on.

    Depicting the march instead is the more faithful choice, not a licence: the
    spawn formation is an artefact of how the group is created, the road march is
    what the mission actually plays, and a still is timestamped before the mission
    starts — when the column was demonstrably moving.

    Falls back to the straight bearing if no road is in reach or the polyline runs
    out, so a caller never has to handle "there was no road there".
    """
    from shapely.geometry import Point as ShPoint

    terrain = start._terrain
    bearing = start.heading_between_point(toward)
    straight = [
        start.point_from_heading(bearing, i * spacing_m) for i in range(max(count, 0))
    ]
    if count <= 0:
        return []

    lines = overlay.vector_lines("roads", start, search_m)
    if not lines:
        log.debug("recon.road_column.no_road", search_m=search_m)
        return straight

    # Sidecars store (east, north).
    here = ShPoint(start.y, start.x)
    line = min(lines, key=here.distance)
    at = line.project(here)

    # Walk whichever way along the polyline heads toward the destination.
    probe = min(spacing_m, line.length / 2.0) or 1.0
    ahead = line.interpolate(min(at + probe, line.length))
    behind = line.interpolate(max(at - probe, 0.0))
    goal = ShPoint(toward.y, toward.x)
    step = spacing_m if goal.distance(ahead) <= goal.distance(behind) else -spacing_m

    out: list[Point] = []
    for i in range(count):
        along = at + i * step
        if not 0.0 <= along <= line.length:
            out.append(straight[i])  # ran off the end of this road
            continue
        p = line.interpolate(along)
        out.append(Point(float(p.y), float(p.x), terrain))
    return out


#: Exposed so `render` can build a `Scene` in tests without an overlay.
def empty_scene(frame: Frame) -> Scene:
    """A `Scene` of flat, featureless, wholly valid ground. Test scaffolding."""
    w, h = frame.size_px
    shape = (h * frame.supersample, w * frame.supersample)

    def zeros() -> np.ndarray:
        return np.zeros(shape, dtype=np.float32)

    return Scene(
        frame=frame,
        elevation_m=zeros(),
        urban=zeros(),
        forest=zeros(),
        water=zeros(),
        road=zeros(),
        river=zeros(),
        valid=np.ones(shape, dtype=bool),
    )
