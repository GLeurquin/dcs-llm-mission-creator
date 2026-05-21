"""Round-trip DCS xz → WGS84 → DCS xz for every supported terrain corner.

A 1 m tolerance is generous: pyproj's TransverseMercator round-trip is
typically sub-millimeter, so any drift past 1 m is a sign that the terrain's
projection parameters changed upstream or the pydcs entry-point moved.
"""

from __future__ import annotations

import pytest

from dcs_mission_creator.map_overlay.coords import (
    dcs_to_latlon,
    latlon_to_dcs,
    rendered_xz_bounds,
    terrain_bbox_latlon,
    xzbounds_to_latlon,
)
from dcs_mission_creator.map_overlay.manifest import XZBounds
from dcs_mission_creator.map_overlay.terrains import known_theaters, terrain_for


def _corners(terrain):
    r = terrain.bounds
    return [
        (r.bottom, r.left),
        (r.bottom, r.right),
        (r.top, r.left),
        (r.top, r.right),
    ]


def test_corner_roundtrip_within_1m():
    for slug in known_theaters():
        terrain = terrain_for(slug)
        for x, z in _corners(terrain):
            ll = dcs_to_latlon(x, z, terrain)
            back = latlon_to_dcs(ll.lat, ll.lng, terrain)
            dx, dz = back.x - x, back.y - z
            assert abs(dx) < 1.0 and abs(dz) < 1.0, (
                f"{slug} corner ({x},{z}) drifted: dx={dx:.3f}, dz={dz:.3f}"
            )


def test_bbox_covers_rendered_corners():
    """The lat/lon bbox must contain the four rendered-xz corners."""
    for slug in known_theaters():
        terrain = terrain_for(slug)
        bbox = terrain_bbox_latlon(terrain, slug)
        bounds = rendered_xz_bounds(slug, terrain)
        corners = [
            (bounds.bottom, bounds.left),
            (bounds.bottom, bounds.right),
            (bounds.top, bounds.left),
            (bounds.top, bounds.right),
        ]
        for x, z in corners:
            ll = dcs_to_latlon(x, z, terrain)
            assert bbox.south <= ll.lat <= bbox.north, slug
            assert bbox.west <= ll.lng <= bbox.east, slug


def test_rendered_xz_bounds_caucasus_uses_clip():
    """Caucasus has a registered clip — should not match raw terrain.bounds."""
    terrain = terrain_for("caucasus")
    clipped = rendered_xz_bounds("caucasus", terrain)
    raw = terrain.bounds
    # Must be strictly tighter on at least one side.
    assert (
        clipped.top < raw.top
        or clipped.bottom > raw.bottom
        or clipped.left > raw.left
        or clipped.right < raw.right
    )


def test_rendered_xz_bounds_unknown_slug_falls_back():
    """Unknown slug → returns raw terrain.bounds as XZBounds."""
    terrain = terrain_for("caucasus")
    out = rendered_xz_bounds("does-not-exist", terrain)
    r = terrain.bounds
    assert (out.top, out.bottom, out.left, out.right) == (
        r.top,
        r.bottom,
        r.left,
        r.right,
    )


def test_xzbounds_to_latlon_min_max_ordering():
    terrain = terrain_for("caucasus")
    bounds = rendered_xz_bounds("caucasus", terrain)
    bbox = xzbounds_to_latlon(bounds, terrain)
    assert bbox.south < bbox.north
    assert bbox.west < bbox.east


def test_latlonbbox_as_tuple_order():
    terrain = terrain_for("caucasus")
    bbox = terrain_bbox_latlon(terrain, "caucasus")
    t = bbox.as_tuple()
    assert t == (bbox.south, bbox.west, bbox.north, bbox.east)


@pytest.mark.parametrize(
    "bounds",
    [
        XZBounds(top=100.0, bottom=-100.0, left=-200.0, right=200.0),
    ],
)
def test_xzbounds_width_height(bounds: XZBounds):
    assert bounds.width_m() == 400.0
    assert bounds.height_m() == 200.0
