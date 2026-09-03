"""Shared test fixtures — chiefly the synthetic overlay the terrain modules need.

Four test files had grown their own elevation-raster stub and a fifth was about
to. They were not quite the same: two spellings of the same line-of-sight walk,
two names for the same `Point` helper, and one that indexed its array without
clamping. That is a bad place for divergence, because a stub is what decides
whether a *correct* answer from the code under test looks correct — and the one
way it can lie is by disagreeing with the real overlay about which cell a world
coordinate falls in.

So the transform here is the real one, taken from `MapOverlay.cell_coords`
rather than retyped: rows run south from `top`, columns east from `left`, and
truncation is the rounding. A subclass adds whatever else its module asks an
overlay for; nothing overrides the geometry.
"""

from __future__ import annotations

import numpy as np
from dcs.mapping import Point
from dcs.terrain.caucasus.caucasus import Caucasus

TERRAIN = Caucasus()


class RasterOverlay:
    """One elevation array, addressed by DCS world coordinate.

    Enough of `MapOverlay` for `core/waypoints`, `core/route_plan`,
    `core/survey` and `core/audit`: the transform, the point query, one bulk
    window and a straight-line visibility test. Everything else a module wants
    goes on a subclass in that module's own test file, where it can be read
    next to the assertions it serves.
    """

    theater = "Caucasus"

    def __init__(
        self,
        heights: np.ndarray,
        *,
        cell_m: float = 100.0,
        top: float = 10_000.0,
        left: float = 0.0,
    ) -> None:
        self.heights = np.asarray(heights, dtype=float)
        self.cell_m = cell_m
        self.manifest = _Manifest(top, left, self.heights.shape, cell_m)

    # -- geometry, and the reason this class exists -------------------------
    def cell_coords(self, north, east, cell_size_m: float) -> tuple[float, float]:
        b = self.manifest.bounds
        return (b.top - north) / cell_size_m, (east - b.left) / cell_size_m

    def cell_of(self, point: Point, cell_size_m: int | float = 0) -> tuple[int, int]:
        row, col = self.cell_coords(point.x, point.y, self.cell_m)
        return int(row), int(col)

    def elevation_at(self, point: Point) -> int:
        """Whole metres, like the real one, and sea level outside the raster.

        `int` rather than `float` because `MapOverlay.elevation_at` returns an
        int and `survey.Spot` prints it with `:5d` — a stub that is looser than
        the thing it stands for lets a formatting bug through to a build.
        The out-of-raster answer is sea level, which is `read_window`'s no-data
        fill; the old per-file stubs indexed with a negative row and silently
        read the far side of the map.
        """
        row, col = self.cell_of(point)
        rows, cols = self.heights.shape
        if 0 <= row < rows and 0 <= col < cols:
            return int(self.heights[row, col])
        return 0

    def read_window(self, name, center, *, half_width_m, half_height_m=None, fill=0):
        return _Window(self.heights, self.cell_m)

    def ridge(self, y_from_m: float, y_to_m: float, height_m: float) -> None:
        """Raise a north-south wall between two *world* eastings.

        Tests used to index the array directly, which tied them to the raster's
        origin: moving `LEFT` moved every wall somewhere else on the map without
        a single assertion changing. Stating it in world coordinates and letting
        the transform place it is the same discipline the module under test is
        held to.
        """
        _, c0 = self.cell_coords(0.0, y_from_m, self.cell_m)
        _, c1 = self.cell_coords(0.0, y_to_m, self.cell_m)
        self.heights[:, int(c0) : int(c1)] = height_m

    def line_of_sight(
        self, a: Point, b: Point, eye_a_m: float = 2.0, eye_b_m: float = 2.0
    ) -> bool:
        """Straight-line visibility over the array, sampled a cell at a time.

        Both ends are lifted, as the real one does and for the same reason: two
        points on the deck fail across the gentlest rise.
        """
        top_a = self.elevation_at(a) + eye_a_m
        top_b = self.elevation_at(b) + eye_b_m
        steps = max(1, int(a.distance_to_point(b) / self.cell_m))
        for i in range(1, steps):
            f = i / steps
            here = a.new_in_same_map(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)
            if self.elevation_at(here) > top_a * (1.0 - f) + top_b * f:
                return False
        return True


class _Bounds:
    def __init__(self, top: float, left: float, shape, cell_m: float) -> None:
        self.top, self.left = top, left
        self.bottom = top - shape[0] * cell_m
        self.right = left + shape[1] * cell_m


class _Manifest:
    def __init__(self, top: float, left: float, shape, cell_m: float) -> None:
        self.bounds = _Bounds(top, left, shape, cell_m)


class _Window:
    """A `read_window` result covering the whole raster, origin at (0, 0)."""

    def __init__(self, values: np.ndarray, cell_m: float) -> None:
        self.values = values.astype(float)
        self.row0 = self.col0 = 0
        self.cell_size_m = cell_m
        self.valid = np.ones_like(self.values, dtype=bool)


def at(x: float, y: float) -> Point:
    """A world point on the shared test terrain."""
    return Point(x, y, TERRAIN)
