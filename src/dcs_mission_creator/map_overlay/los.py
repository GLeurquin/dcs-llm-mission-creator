"""Bresenham line-of-sight over the elevation raster.

`line_of_sight(a, b, eye_a_m, eye_b_m)` is True iff a straight ray from
(a + eye_a) to (b + eye_b) is unobstructed by intervening terrain.

The algorithm walks every cell the ray traverses (8-connected Bresenham), and
at each step compares the linearly-interpolated ray altitude to the ground
altitude at that cell. The ray is blocked at the first cell where ground
exceeds the ray.

Cost: O(cells along ray). At 50 m cells, a 20 km ray = ~400 cells; ~1 µs per
cell on hot zarr chunks = sub-ms per check.
"""

from __future__ import annotations

import numpy as np


def line_of_sight_cells(
    elevation: np.ndarray,
    row_a: int,
    col_a: int,
    elev_a_m: float,
    row_b: int,
    col_b: int,
    elev_b_m: float,
) -> bool:
    """Bresenham LOS in image-cell space.

    `elevation[row, col]` is the ground height at cell (row, col). `elev_a_m`
    and `elev_b_m` are the heights of the ray endpoints (ground + eye).
    """
    h, w = elevation.shape
    if not (0 <= row_a < h and 0 <= col_a < w and 0 <= row_b < h and 0 <= col_b < w):
        # Ray endpoint outside the raster — be conservative and assume blocked.
        return False
    dr = row_b - row_a
    dc = col_b - col_a
    n = max(abs(dr), abs(dc))
    if n == 0:
        return True
    # Pre-step the endpoints out of the start cell so the first sample is the
    # next cell along the ray (we don't test the start cell itself).
    rows = np.linspace(row_a, row_b, n + 1).round().astype(np.int32)
    cols = np.linspace(col_a, col_b, n + 1).round().astype(np.int32)
    ray_h = np.linspace(elev_a_m, elev_b_m, n + 1)
    # Skip endpoints — we only check intervening terrain.
    inner = slice(1, -1) if n >= 2 else slice(0, 0)
    ground = elevation[rows[inner], cols[inner]]
    return bool(np.all(ground <= ray_h[inner] + 1e-3))
