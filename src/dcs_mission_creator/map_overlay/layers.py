"""Typed layer identifiers shared between the build pipeline, CLI, and viz.

Three enums:
- `BuildLayer`: which build step the CLI runs (also includes the `all` shortcut).
- `RenderLayer`: which raster layers the viz step can render.
- `QueryLayer`: which point-query exposed by `MapOverlay` the CLI surfaces.

Using `StrEnum` keeps the values comparable to plain strings (argparse, JSON,
dict lookups) while still giving us a closed, typed set the IDE can complete.
"""

from __future__ import annotations

from enum import StrEnum


class BuildLayer(StrEnum):
    ELEVATION = "elevation"
    OSM = "osm"
    FOREST = "forest"
    ALL = "all"

    @classmethod
    def expand(cls, selected: set[BuildLayer]) -> set[BuildLayer]:
        """Resolve the `ALL` sentinel into the concrete build steps."""
        if cls.ALL in selected:
            return {cls.ELEVATION, cls.OSM, cls.FOREST}
        return selected


class RenderLayer(StrEnum):
    ELEVATION = "elevation"
    SLOPE = "slope"
    VEGETATION = "vegetation"
    BUILDINGS = "buildings"
    ROADS_DT = "roads_dt"
    RIVERS_DT = "rivers_dt"
    ALL = "all"

    @classmethod
    def expand(cls, selected: set[RenderLayer]) -> set[RenderLayer]:
        if cls.ALL in selected:
            return {m for m in cls if m is not cls.ALL}
        return selected


class QueryLayer(StrEnum):
    ELEVATION = "elevation"
    SLOPE = "slope"
    VEGETATION = "vegetation"
    ROAD_DISTANCE = "road_distance"
    RIVER_DISTANCE = "river_distance"
    BUILT_UP = "built_up"
    FOREST_EDGE = "forest_edge"
    PROMINENCE = "prominence"
