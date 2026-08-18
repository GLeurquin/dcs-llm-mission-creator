"""Frame geometry: the rotation every other recon module trusts.

Pure arithmetic, so this needs neither a DCS install nor the map overlay and runs
in the default selection. It is also where a sign error is cheapest to catch —
an inverted axis here would show up as a mirrored picture much later.
"""

from __future__ import annotations

import math

import pytest
from dcs.mapping import Point
from dcs.terrain import Syria

from dcs_mission_creator.core.recon.frame import Frame

_TERRAIN = Syria()


def _pt(north: float, east: float) -> Point:
    return Point(north, east, _TERRAIN)


def _frame(**kwargs: object) -> Frame:
    return Frame(center=_pt(0.0, 0.0), **kwargs)  # ty: ignore[invalid-argument-type]


def test_default_frame_is_1024x768_at_25m() -> None:
    f = _frame()
    assert f.gsd_m == 25.0
    assert f.size_px == (1024, 768)


def test_non_integral_extent_raises_rather_than_rounding() -> None:
    """Half a pixel of slop would put the centre off and break exact decimation."""
    with pytest.raises(ValueError, match="whole number of pixels"):
        _frame(width_m=25_610.0).size_px


def test_centre_maps_to_image_centre() -> None:
    f = _frame()
    w, h = f.size_px
    assert f.world_to_px(f.center) == pytest.approx((w / 2.0, h / 2.0))


def test_north_up_frame_puts_north_at_a_lower_row() -> None:
    """heading_deg=0 is north-up, and image rows grow downward."""
    f = _frame(heading_deg=0.0)
    _, cy = f.world_to_px(f.center)
    px, py = f.world_to_px(_pt(1_000.0, 0.0))
    assert py < cy
    assert py == pytest.approx(cy - 1_000.0 / f.gsd_m)
    assert px == pytest.approx(f.size_px[0] / 2.0)


def test_north_up_frame_puts_east_at_a_higher_column() -> None:
    f = _frame(heading_deg=0.0)
    cx, _ = f.world_to_px(f.center)
    px, py = f.world_to_px(_pt(0.0, 1_000.0))
    assert px == pytest.approx(cx + 1_000.0 / f.gsd_m)
    assert py == pytest.approx(f.size_px[1] / 2.0)


@pytest.mark.parametrize("heading", [0.0, 45.0, 133.8, 270.0, 359.0])
def test_a_point_along_the_heading_stays_on_the_centre_column(heading: float) -> None:
    """Whatever the rotation, the frame's up axis is the centre column."""
    f = _frame(heading_deg=heading)
    ahead = f.center.point_from_heading(heading, 4_000.0)
    px, py = f.world_to_px(ahead)
    assert px == pytest.approx(f.size_px[0] / 2.0, abs=1e-6)
    assert py == pytest.approx(f.size_px[1] / 2.0 - 4_000.0 / f.gsd_m, abs=1e-6)


@pytest.mark.parametrize("heading", [0.0, 45.0, 133.8, 270.0])
def test_world_grid_round_trips_through_world_to_px(heading: float) -> None:
    """The bulk inverse and the scalar forward transform must agree."""
    f = _frame(heading_deg=heading)
    north, east = f.world_grid()
    w, h = f.size_px
    assert north.shape == (h, w)
    for row, col in ((0, 0), (h - 1, w - 1), (h // 2, w // 3)):
        p = _pt(float(north[row, col]), float(east[row, col]))
        px, py = f.world_to_px(p)
        # world_grid returns pixel centres, hence the half-pixel offset.
        assert px == pytest.approx(col + 0.5, abs=1e-6)
        assert py == pytest.approx(row + 0.5, abs=1e-6)


def test_supersampled_grid_is_denser_by_exactly_the_factor() -> None:
    f = _frame(supersample=2)
    w, h = f.size_px
    north, _ = f.world_grid(supersampled=True)
    assert north.shape == (h * 2, w * 2)


def test_half_diagonal_contains_every_corner_at_any_rotation() -> None:
    for heading in (0.0, 37.0, 133.8, 201.0, 300.0):
        f = _frame(heading_deg=heading)
        radius = f.half_diagonal_m()
        north, east = f.world_grid()
        for row, col in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
            d = math.hypot(
                float(north[row, col]) - f.center.x, float(east[row, col]) - f.center.y
            )
            assert d <= radius + f.gsd_m


def test_along_axis_points_the_frame_up_the_leg() -> None:
    a, b = _pt(0.0, 0.0), _pt(10_000.0, 10_000.0)
    f = Frame.along_axis(a, b)
    assert f.center.x == pytest.approx(5_000.0)
    assert f.center.y == pytest.approx(5_000.0)
    assert f.heading_deg == pytest.approx(45.0, abs=0.5)
    # Both endpoints sit on the centre column, b above a.
    ax, ay = f.world_to_px(a)
    bx, by = f.world_to_px(b)
    assert ax == pytest.approx(bx, abs=1e-6)
    assert by < ay


def test_contains_rejects_a_point_outside_the_extent() -> None:
    f = _frame()
    assert f.contains(f.center)
    assert not f.contains(_pt(0.0, 40_000.0))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"px_per_cell": 0}, "px_per_cell"),
        ({"supersample": 0}, "supersample"),
        ({"cell_size_m": 0}, "cell_size_m"),
        ({"width_m": 0.0}, "non-empty"),
    ],
)
def test_degenerate_frames_are_rejected(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _frame(**kwargs)
