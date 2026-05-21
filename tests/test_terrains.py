"""Tests for the terrain slug → pydcs class registry."""

from __future__ import annotations

import pytest
from dcs.terrain import Caucasus
from dcs.terrain.terrain import Terrain

from dcs_mission_creator.map_overlay.terrains import known_theaters, terrain_for


def test_terrain_for_caucasus_returns_instance():
    t = terrain_for("caucasus")
    assert isinstance(t, Caucasus)
    assert isinstance(t, Terrain)


def test_terrain_for_unknown_slug_raises_value_error():
    with pytest.raises(ValueError, match="unknown theater slug"):
        terrain_for("atlantis")


@pytest.mark.parametrize(
    "slug",
    [
        "syria",
        "persiangulf",
        "nevada",
        "normandy",
        "thechannel",
        "sinai",
        "falklands",
        "marianaislands",
    ],
)
def test_every_known_slug_instantiates(slug: str):
    assert isinstance(terrain_for(slug), Terrain)


def test_known_theaters_is_sorted_and_full():
    known = known_theaters()
    assert known == sorted(known)
    assert "caucasus" in known
    assert "syria" in known
    assert len(known) == 9
