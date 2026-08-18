"""Frame geometry for a synthetic sensor still — where the picture looks.

One rotation, defined once. Every other module in this package consumes it:
the sampler builds its read grid from `world_grid`, the renderer places
detections with `world_to_px`, and the chrome draws its north arrow from
`heading_deg`. Getting the rotation right in two places would mean getting it
wrong in one of them.

Two conventions worth stating, because both are easy to invert:

- pydcs `Point.x` is **north** and `Point.y` is **east** (see
  `core/mission_kit.offset`). The frame's "up" axis is `heading_deg` measured
  from north, so `heading_deg=0` is a north-up frame.
- Image rows grow **downward**, i.e. toward the bottom of the frame. So a point
  further along the frame's up axis lands on a *lower* row number, which is what
  `test_recon_frame` pins.

The frame is deliberately sized so that one 50 m raster post is an exact whole
number of output pixels (`px_per_cell`). That removes every resampling-convention
question from the sampler: there is no fractional window origin, and the
supersampled grid decimates by an exact integer box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from dcs.mapping import Point


@dataclass(frozen=True)
class Frame:
    """A rotated, north-agnostic rectangle of ground, in output pixels.

    `width_m` / `height_m` are the ground the frame covers; `cell_size_m` is the
    overlay's post spacing and `px_per_cell` how many output pixels one post
    becomes. Their ratio is the ground sample distance, so a frame is described
    by what it covers and how coarsely, never by a pixel count that might not
    divide.
    """

    center: Point
    heading_deg: float = 0.0
    width_m: float = 25_600.0
    height_m: float = 19_200.0
    px_per_cell: int = 2
    cell_size_m: int = 50
    supersample: int = 2

    def __post_init__(self) -> None:
        if self.px_per_cell < 1:
            raise ValueError(f"px_per_cell must be >= 1, got {self.px_per_cell}")
        if self.supersample < 1:
            raise ValueError(f"supersample must be >= 1, got {self.supersample}")
        if self.cell_size_m <= 0:
            raise ValueError(f"cell_size_m must be > 0, got {self.cell_size_m}")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError(
                f"frame must be non-empty, got {self.width_m}x{self.height_m}"
            )

    # -- scale ---------------------------------------------------------------

    @property
    def gsd_m(self) -> float:
        """Ground sample distance of one output pixel, in metres."""
        return self.cell_size_m / self.px_per_cell

    @property
    def size_px(self) -> tuple[int, int]:
        """Output size as `(width, height)` in pixels.

        Raises rather than rounding: a frame whose extent is not a whole number
        of pixels would put the centre half a pixel off and make the exact
        integer decimation in `render` a lie.
        """
        return (
            self._exact_px(self.width_m, "width_m"),
            self._exact_px(self.height_m, "height_m"),
        )

    def _exact_px(self, extent_m: float, field: str) -> int:
        exact = extent_m / self.gsd_m
        rounded = round(exact)
        if abs(exact - rounded) > 1e-9:
            raise ValueError(
                f"{field}={extent_m} is {exact} px at {self.gsd_m} m/px — "
                f"must be a whole number of pixels"
            )
        return int(rounded)

    def half_diagonal_m(self) -> float:
        """Radius that contains the whole frame at any rotation.

        What the sampler must read from the overlay: a rotated rectangle needs a
        window big enough for its corners, not for its sides.
        """
        return math.hypot(self.width_m / 2.0, self.height_m / 2.0)

    # -- world <-> pixel -----------------------------------------------------

    def _axes(self) -> tuple[float, float, float, float]:
        """Frame up and right unit vectors, each as `(north, east)` components."""
        rad = math.radians(self.heading_deg)
        up_n, up_e = math.cos(rad), math.sin(rad)
        # Right is up rotated +90 degrees (clockwise, i.e. north -> east).
        return up_n, up_e, -up_e, up_n

    def world_to_px(self, p: Point) -> tuple[float, float]:
        """DCS `Point` -> `(px_x, px_y)`, with `(0, 0)` at the frame's top-left."""
        up_n, up_e, right_n, right_e = self._axes()
        dn = p.x - self.center.x
        de = p.y - self.center.y
        along_up = dn * up_n + de * up_e
        along_right = dn * right_n + de * right_e
        w, h = self.size_px
        return (w / 2.0 + along_right / self.gsd_m, h / 2.0 - along_up / self.gsd_m)

    def world_grid(
        self, *, supersampled: bool = False
    ) -> tuple[np.ndarray, np.ndarray]:
        """World `(north, east)` coordinate arrays, one entry per output pixel.

        The inverse of `world_to_px` in bulk — what `sample.py` feeds to
        `map_coordinates`. Pixel centres, so the grid is offset by half a pixel
        and the frame's own centre falls between the two middle pixels rather
        than on one of them.
        """
        up_n, up_e, right_n, right_e = self._axes()
        w, h = self.size_px
        scale = self.supersample if supersampled else 1
        gsd = self.gsd_m / scale
        cols = (np.arange(w * scale, dtype=np.float64) + 0.5 - w * scale / 2.0) * gsd
        rows = (np.arange(h * scale, dtype=np.float64) + 0.5 - h * scale / 2.0) * gsd
        # rows run down the image, i.e. against the frame's up axis.
        right = cols[None, :]
        up = -rows[:, None]
        north = self.center.x + up * up_n + right * right_n
        east = self.center.y + up * up_e + right * right_e
        return north, east

    # -- convenience ---------------------------------------------------------

    def contains(self, p: Point) -> bool:
        """True if `p` falls inside the frame's pixel extent."""
        px, py = self.world_to_px(p)
        w, h = self.size_px
        return 0.0 <= px < w and 0.0 <= py < h

    @staticmethod
    def along_axis(
        a: Point, b: Point, *, heading_offset_deg: float = 0.0, **kwargs: object
    ) -> Frame:
        """A frame centred between `a` and `b`, rotated so the axis runs up it.

        The usual construction for a route: the leg the mission cares about runs
        vertically up the picture, which is how a sensor operator would frame it.

        `heading_offset_deg=-90` turns the frame a quarter turn so the axis runs
        *across* it instead, which is what a leg longer than the frame is tall
        wants — a landscape frame has its long dimension horizontal, and DCS shows
        briefing slides in a landscape panel.
        """
        return Frame(
            center=a.midpoint(b),
            heading_deg=float(a.heading_between_point(b)) + heading_offset_deg,
            **kwargs,  # ty: ignore[invalid-argument-type]
        )
