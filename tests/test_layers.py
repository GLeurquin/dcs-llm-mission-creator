"""Enum-level checks for the build / render / query layer registries."""

from __future__ import annotations

from dcs_mission_creator.map_overlay.layers import BuildLayer, QueryLayer, RenderLayer


def test_build_layer_expand_all():
    out = BuildLayer.expand({BuildLayer.ALL})
    assert out == set(BuildLayer) - {BuildLayer.ALL}
    assert BuildLayer.ALL not in out


def test_build_layer_expand_subset_unchanged():
    sel = {BuildLayer.OSM}
    assert BuildLayer.expand(sel) == {BuildLayer.OSM}


def test_build_layer_expand_all_with_extras_collapses():
    out = BuildLayer.expand({BuildLayer.ALL, BuildLayer.OSM})
    assert out == set(BuildLayer) - {BuildLayer.ALL}


def test_render_layer_expand_all_drops_sentinel():
    out = RenderLayer.expand({RenderLayer.ALL})
    assert RenderLayer.ALL not in out
    assert out == set(RenderLayer) - {RenderLayer.ALL}


def test_render_layer_expand_subset_unchanged():
    sel = {RenderLayer.SLOPE, RenderLayer.VEGETATION}
    assert RenderLayer.expand(sel) == sel


def test_strenum_values_are_lowercase_strings():
    for member in BuildLayer:
        assert isinstance(member.value, str)
        assert member.value == member.value.lower()
    for member in RenderLayer:
        assert isinstance(member.value, str)
    for member in QueryLayer:
        assert isinstance(member.value, str)


def test_query_layer_covers_documented_queries():
    expected = {
        "elevation",
        "slope",
        "vegetation",
        "road_distance",
        "river_distance",
        "built_up",
        "forest_edge",
        "prominence",
    }
    assert {m.value for m in QueryLayer} == expected


def test_build_layer_equals_plain_string():
    assert BuildLayer.OSM == "osm"
    assert BuildLayer("osm") is BuildLayer.OSM
