"""DCS world ↔ WGS84 lat/lon helpers.

Thin wrapper around pydcs's `Point.latlng()` / `Point.from_latlng()` so callers
in the map_overlay package don't need to touch `dcs.mapping` directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from dcs.mapping import LatLng, Point
from dcs.terrain.terrain import Terrain

from dcs_mission_creator.map_overlay.manifest import XZBounds

# Subset of each terrain's xz rectangle that DCS actually renders. The pydcs
# `terrain.bounds` rectangle extends well past the rendered map on multiple
# sides (north / south / west of Caucasus are unrendered ocean and Russian
# steppe). Building overlays over those areas wastes Overpass requests, SRTM
# fetches, WorldCover reprojection, and grid cells. Each entry below was
# derived from the corner lat/lon of the visible map; missing slugs fall back
# to the full pydcs bounds.
_RENDERED_XZ_BOUNDS: dict[str, XZBounds] = {
    # Caucasus visible map corners: TL N45°29' E33°16', BR N40°09' E45°17'.
    "caucasus": XZBounds(
        top=112_453.0, bottom=-553_675.0, left=-78_677.0, right=948_008.0
    ),
}


@dataclass(frozen=True)
class LatLonBBox:
    """WGS84 bounding box (degrees)."""

    south: float
    west: float
    north: float
    east: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.south, self.west, self.north, self.east)


def dcs_to_latlon(x: float, z: float, terrain: Terrain) -> LatLng:
    """DCS world (x=northing, z=easting) → WGS84 lat/lon."""
    return Point(x, z, terrain).latlng()


def latlon_to_dcs(lat: float, lon: float, terrain: Terrain) -> Point:
    """WGS84 lat/lon → DCS world Point."""
    return Point.from_latlng(LatLng(lat, lon), terrain)


def rendered_xz_bounds(theater_slug: str, terrain: Terrain) -> XZBounds:
    """Return the DCS-rendered subregion of a terrain in xz meters.

    Falls back to `terrain.bounds` if no entry is registered for the slug.
    Normalizes the fallback because pydcs Rectangle inputs disagree on
    top/bottom ordering across terrains (e.g. Syria builds the Rectangle with
    top<bottom while Caucasus builds it with top>bottom); downstream code
    treats `top` as the max-x corner.
    """
    clip = _RENDERED_XZ_BOUNDS.get(theater_slug)
    if clip is not None:
        return clip
    r = terrain.bounds
    return XZBounds(
        top=max(r.top, r.bottom),
        bottom=min(r.top, r.bottom),
        left=min(r.left, r.right),
        right=max(r.left, r.right),
    )


def xzbounds_to_latlon(bounds: XZBounds, terrain: Terrain) -> LatLonBBox:
    """Convert an xz rectangle to a WGS84 bbox by projecting its four corners."""
    corners_xz = [
        (bounds.bottom, bounds.left),
        (bounds.bottom, bounds.right),
        (bounds.top, bounds.left),
        (bounds.top, bounds.right),
    ]
    lats, lons = [], []
    for x, z in corners_xz:
        ll = dcs_to_latlon(x, z, terrain)
        lats.append(ll.lat)
        lons.append(ll.lng)
    return LatLonBBox(south=min(lats), west=min(lons), north=max(lats), east=max(lons))


def terrain_bbox_latlon(terrain: Terrain, theater_slug: str) -> LatLonBBox:
    """WGS84 bbox covering the rendered subregion of `terrain`.

    Walks the four corners of the rendered xz rectangle (see
    `rendered_xz_bounds`) through pydcs's projection so the bbox is correct
    regardless of map rotation.
    """
    return xzbounds_to_latlon(rendered_xz_bounds(theater_slug, terrain), terrain)
