"""The sensor chain, driven off a hand-built `Scene`.

No overlay and no DCS install, so this runs in the default selection — which is
the point of keeping `render` pure. The assertions are renderer *contract*, never
mission content: polarity, determinism, and that the physical constants still
reach the pixels.
"""

from __future__ import annotations

import numpy as np
import pytest
from dcs.mapping import Point
from dcs.terrain import Syria

from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.render import Mark, render
from dcs_mission_creator.core.recon.sample import Scene, empty_scene

_TERRAIN = Syria()


def _frame(**kwargs: object) -> Frame:
    # A small frame keeps these tests fast; the geometry is checked in
    # test_recon_frame, so size is irrelevant here.
    return Frame(
        center=Point(0.0, 0.0, _TERRAIN),
        width_m=4_000.0,
        height_m=4_000.0,
        **kwargs,  # ty: ignore[invalid-argument-type]
    )


def _patched(field: str, *, value: float = 1.0) -> tuple[Scene, tuple[slice, slice]]:
    """A scene with one surface painted over a block, plus that block's slice."""
    scene = empty_scene(_frame())
    h, w = scene.elevation_m.shape
    block = (slice(h // 4, h // 2), slice(w // 4, w // 2))
    getattr(scene, field)[block] = value
    # The block in output pixels, after decimation.
    s = scene.frame.supersample
    out = (
        slice(block[0].start // s, block[0].stop // s),
        slice(block[1].start // s, block[1].stop // s),
    )
    return scene, out


def _mean_of(img, region: tuple[slice, slice]) -> float:
    arr = np.asarray(img).astype(np.float64)
    # Trim the edges of the region so PSF bleed from outside does not count.
    rows, cols = region
    pad = 6
    return float(
        arr[
            rows.start + pad : rows.stop - pad, cols.start + pad : cols.stop - pad
        ].mean()
    )


def test_output_is_8bit_grey_at_the_frame_size() -> None:
    scene = empty_scene(_frame())
    img = render(scene, seed=1)
    assert img.mode == "L"
    assert img.size == scene.frame.size_px


def test_same_seed_is_byte_identical_and_a_different_seed_is_not() -> None:
    """The whole cache contract rests on this."""
    scene = empty_scene(_frame())
    a = render(scene, seed=7).tobytes()
    b = render(scene, seed=7).tobytes()
    c = render(scene, seed=8).tobytes()
    assert a == b
    assert a != c


def test_radar_polarity_water_and_road_dark_urban_bright() -> None:
    """The ordering that separates a radar frame from an optical one.

    Water and asphalt are specular and reflect away from the sensor; towns
    double-bounce off walls; forest volume-scatters above bare ground.
    """
    bare = _mean_of(render(empty_scene(_frame()), seed=3), (slice(0, 80), slice(0, 80)))
    levels = {}
    for field in ("water", "road", "forest", "urban"):
        scene, region = _patched(field)
        levels[field] = _mean_of(render(scene, seed=3), region)

    assert levels["water"] < levels["road"] < bare < levels["forest"] < levels["urban"]


def test_water_is_lifted_off_pure_black_by_the_noise_floor() -> None:
    """Guards the NESZ term, which is easy to lose in a refactor and is the
    single most obvious tell of a synthesised radar image when it is missing."""
    scene, region = _patched("water")
    assert _mean_of(render(scene, seed=4), region) > 12.0


def test_ground_carries_correlated_texture_not_flat_noise() -> None:
    """A blurred copy must still vary: pure per-pixel speckle would average out.

    This is what stops the roughness field being silently dropped, which would
    leave a frame of uniform white noise that reads as broken rather than coarse.
    """
    from scipy.ndimage import uniform_filter

    arr = np.asarray(render(empty_scene(_frame()), seed=5)).astype(np.float64)
    assert uniform_filter(arr, size=9).std() > 3.0


def test_no_data_is_painted_where_the_frame_left_the_overlay() -> None:
    scene = empty_scene(_frame())
    h, _ = scene.elevation_m.shape
    scene.valid[: h // 2, :] = False
    arr = np.asarray(render(scene, seed=6))
    top = arr[: arr.shape[0] // 2 - 2, :]
    assert top.std() == 0.0
    assert top.mean() < 20.0


def test_a_detection_marks_its_own_position_and_not_elsewhere() -> None:
    scene = empty_scene(_frame())
    plain = np.asarray(render(scene, seed=9)).astype(np.int16)
    centre = Point(0.0, 0.0, _TERRAIN)
    marked = np.asarray(render(scene, [Mark(x=centre.x, y=centre.y)], seed=9)).astype(
        np.int16
    )

    cx, cy = (v // 2 for v in scene.frame.size_px)
    assert not np.array_equal(
        plain[cy - 3 : cy + 4, cx - 3 : cx + 4],
        marked[cy - 3 : cy + 4, cx - 3 : cx + 4],
    )
    far = (slice(cy + 30, cy + 60), slice(cx + 30, cx + 60))
    assert np.array_equal(plain[far], marked[far])


def test_annotate_false_leaves_the_image_untouched() -> None:
    scene = empty_scene(_frame())
    mark = [Mark(x=0.0, y=0.0, kind="group", radius_m=300.0, text="11 DET")]
    assert (
        render(scene, mark, seed=2, annotate=False).tobytes()
        == render(scene, seed=2).tobytes()
    )


@pytest.mark.parametrize("kind", ["detection", "group", "aimpoint", "label"])
def test_every_mark_kind_renders(kind: str) -> None:
    scene = empty_scene(_frame())
    mark = Mark(x=0.0, y=0.0, kind=kind, text="X", radius_m=200.0, track_deg=90.0)  # ty: ignore[invalid-argument-type]
    assert render(scene, [mark], seed=1).mode == "L"


def test_a_mark_outside_the_frame_does_not_raise() -> None:
    scene = empty_scene(_frame())
    render(scene, [Mark(x=500_000.0, y=500_000.0)], seed=1)
