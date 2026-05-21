"""Single-cell `Placement` filter + convenience constructors.

A `Placement` is a bag of constraints applied to candidate cells inside
`MapOverlay.find_placement(...)`. Every field is independent and optional;
None / empty means "ignore this constraint".

The runtime evaluation lives in `query.py`; this module is data + sugar only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dcs.mapping import Point


class Vegetation(IntEnum):
    NONE = 0
    LIGHT_FOREST = 1
    DENSE_FOREST = 2
    WATER = 3


@dataclass(frozen=True)
class Placement:
    # Safety filters
    near_road_m: float | None = None
    min_distance_to_road_m: float | None = None
    max_slope_deg: float | None = None
    not_in: tuple[Vegetation, ...] = ()
    not_in_built_up: bool = False
    forest_buffer_m: float = 0.0

    # Tactical filters
    near_forest_edge_m: float | None = None
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    min_relative_height_m: float | None = None
    max_relative_height_m: float | None = None
    relative_height_radius_m: float = 2_000.0
    line_of_sight_to: tuple["Point", ...] = ()
    no_line_of_sight_to: tuple["Point", ...] = ()
    near_water_m: float | None = None
    in_sector_from: tuple["Point", float, float] | None = None
    min_distance_to: tuple[tuple["Point", float], ...] = field(default_factory=tuple)
    max_distance_to: tuple[tuple["Point", float], ...] = field(default_factory=tuple)
    reachable_by_road_from: "Point | None" = None

    # ------------------------------------------------------------------ sugar
    @classmethod
    def on_hilltop(cls, min_prominence_m: float = 50.0, **base: Any) -> Placement:
        """High ground: positive local prominence over a 2 km radius."""
        return cls(min_relative_height_m=min_prominence_m, **base)  # type: ignore[arg-type]

    @classmethod
    def in_valley(cls, max_relative_height_m: float = -20.0, **base: Any) -> Placement:
        """Valley floor: cell elev below the local mean."""
        return cls(max_relative_height_m=max_relative_height_m, **base)  # type: ignore[arg-type]

    @classmethod
    def near_treeline(
        cls,
        within_m: float = 80.0,
        light_forest_ok: bool = True,
        **base: Any,
    ) -> Placement:
        """Concealment edge: close to forest but not buried in dense canopy."""
        not_in: tuple[Vegetation, ...] = (Vegetation.DENSE_FOREST, Vegetation.WATER)
        if not light_forest_ok:
            not_in = not_in + (Vegetation.LIGHT_FOREST,)
        return cls(near_forest_edge_m=within_m, not_in=not_in, **base)  # type: ignore[arg-type]

    @classmethod
    def coastal(cls, within_m: float = 500.0, **base: Any) -> Placement:
        """Near a river or coastline."""
        return cls(near_water_m=within_m, **base)  # type: ignore[arg-type]

    @classmethod
    def urban_outskirts(cls, within_m: float = 300.0, **base: Any) -> Placement:
        """Outside the built-up mask but close to it."""
        return cls(not_in_built_up=True, **base)  # type: ignore[arg-type]

    def merged_with(self, **overrides: object) -> Placement:
        """Return a copy with the given fields overridden — for layering filters."""
        return replace(self, **overrides)  # type: ignore[arg-type]
