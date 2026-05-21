"""Unit tests for the Bresenham LOS routine on synthetic elevation rasters."""

from __future__ import annotations

import numpy as np

from dcs_mission_creator.map_overlay.los import line_of_sight_cells


def test_flat_terrain_is_visible():
    grid = np.zeros((10, 10), dtype=np.int16)
    assert line_of_sight_cells(grid, 0, 0, 5.0, 9, 9, 5.0)


def test_same_cell_is_visible():
    grid = np.zeros((10, 10), dtype=np.int16)
    assert line_of_sight_cells(grid, 3, 3, 1.0, 3, 3, 1.0)


def test_hill_in_middle_blocks_low_ray():
    grid = np.zeros((1, 11), dtype=np.int16)
    grid[0, 5] = 500  # mountain between two valley endpoints
    # Eye height 10 m at endpoints — ray hits the mountain at col 5.
    assert not line_of_sight_cells(grid, 0, 0, 10.0, 0, 10, 10.0)


def test_high_enough_ray_clears_hill():
    grid = np.zeros((1, 11), dtype=np.int16)
    grid[0, 5] = 100
    # Eye height 1000 m at both endpoints — ray altitude stays above peak.
    assert line_of_sight_cells(grid, 0, 0, 1000.0, 0, 10, 1000.0)


def test_endpoint_terrain_does_not_block():
    """Only intervening cells block — endpoint columns are excluded."""
    grid = np.zeros((1, 5), dtype=np.int16)
    grid[0, 0] = 10_000
    grid[0, 4] = 10_000
    # Endpoint columns are sky-high, but they are excluded from the test.
    assert line_of_sight_cells(grid, 0, 0, 10_000.0, 0, 4, 10_000.0)


def test_out_of_bounds_returns_false():
    grid = np.zeros((5, 5), dtype=np.int16)
    assert not line_of_sight_cells(grid, -1, 0, 5.0, 4, 4, 5.0)
    assert not line_of_sight_cells(grid, 0, 0, 5.0, 5, 4, 5.0)
    assert not line_of_sight_cells(grid, 0, 99, 5.0, 4, 4, 5.0)


def test_ray_slopes_with_endpoint_heights():
    """Interpolated ray rises along the path — high-end side clears taller hills."""
    grid = np.zeros((1, 11), dtype=np.int16)
    # Tall hill near the high-altitude endpoint, low hill near the low side.
    grid[0, 8] = 800
    # A 50 m → 1000 m linear ramp clears col 8 (ray ≈ 810 m there).
    assert line_of_sight_cells(grid, 0, 0, 50.0, 0, 10, 1000.0)
