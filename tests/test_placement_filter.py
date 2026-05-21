"""Unit tests for the `Placement` dataclass and its sugar constructors."""

from __future__ import annotations

import pytest

from dcs_mission_creator.map_overlay.placement import Placement, Vegetation


def test_defaults_are_all_ignore():
    p = Placement()
    assert p.near_road_m is None
    assert p.min_distance_to_road_m is None
    assert p.max_slope_deg is None
    assert p.not_in == ()
    assert p.not_in_built_up is False
    assert p.forest_buffer_m == 0.0
    assert p.near_forest_edge_m is None
    assert p.min_elevation_m is None
    assert p.max_elevation_m is None
    assert p.min_relative_height_m is None
    assert p.max_relative_height_m is None
    assert p.line_of_sight_to == ()
    assert p.no_line_of_sight_to == ()
    assert p.near_water_m is None
    assert p.in_sector_from is None
    assert p.min_distance_to == ()
    assert p.max_distance_to == ()
    assert p.reachable_by_road_from is None


def test_placement_is_frozen():
    p = Placement()
    with pytest.raises(Exception):
        setattr(p, "max_slope_deg", 5)


def test_on_hilltop_sets_relative_height():
    p = Placement.on_hilltop(min_prominence_m=75.0, max_slope_deg=15)
    assert p.min_relative_height_m == 75.0
    assert p.max_slope_deg == 15


def test_on_hilltop_uses_default_prominence():
    p = Placement.on_hilltop()
    assert p.min_relative_height_m == 50.0


def test_in_valley_sets_negative_relative_height():
    p = Placement.in_valley(max_relative_height_m=-30.0, not_in_built_up=True)
    assert p.max_relative_height_m == -30.0
    assert p.not_in_built_up is True


def test_near_treeline_excludes_dense_forest_and_water():
    p = Placement.near_treeline(within_m=60.0)
    assert p.near_forest_edge_m == 60.0
    assert Vegetation.DENSE_FOREST in p.not_in
    assert Vegetation.WATER in p.not_in
    assert Vegetation.LIGHT_FOREST not in p.not_in  # light_forest_ok=True default


def test_near_treeline_no_light_forest():
    p = Placement.near_treeline(light_forest_ok=False)
    assert Vegetation.LIGHT_FOREST in p.not_in


def test_coastal_sets_water_proximity():
    p = Placement.coastal(within_m=300.0)
    assert p.near_water_m == 300.0


def test_urban_outskirts_sets_built_up_flag():
    p = Placement.urban_outskirts()
    assert p.not_in_built_up is True


def test_merged_with_returns_new_instance():
    base = Placement(max_slope_deg=10)
    merged = base.merged_with(max_slope_deg=20, forest_buffer_m=100.0)
    # Base unchanged (frozen dataclass invariant).
    assert base.max_slope_deg == 10
    assert base.forest_buffer_m == 0.0
    # Copy reflects overrides.
    assert merged.max_slope_deg == 20
    assert merged.forest_buffer_m == 100.0
    assert merged is not base


def test_vegetation_enum_values_are_distinct():
    assert {v.value for v in Vegetation} == {0, 1, 2, 3}
