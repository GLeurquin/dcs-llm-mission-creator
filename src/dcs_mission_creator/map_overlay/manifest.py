"""Per-theater overlay manifest — schema v1.

`manifest.json` lives at `src/resources/overlays/<theater>/manifest.json` and
records everything a reader needs to interpret the sibling `.zarr/` arrays
without re-deriving anything from pydcs at runtime:

- Terrain identity + DCS xz bounds (so we can map cell index ↔ world coords).
- Per-layer cell size + dtype.
- OSM class filters (so a rebuild reproduces the same selection).
- Source provenance (build timestamp, git sha, package versions).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class LayerSpec:
    cell_size_m: int
    dtype: str  # "uint8", "uint16", "int16"


@dataclass
class LayerSet:
    """The v1 raster layers, one named field each.

    A dataclass rather than a `dict[str, LayerSpec]` so a typo in a layer name
    is a type error at the call site instead of a `KeyError` at runtime. The
    field defaults *are* the v1 plan spec (every layer at 50 m); the JSON shape
    stays a `{name: spec}` object, so on-disk manifests are unchanged.
    """

    vegetation: LayerSpec = LayerSpec(cell_size_m=50, dtype="uint8")
    elevation: LayerSpec = LayerSpec(cell_size_m=50, dtype="int16")
    slope: LayerSpec = LayerSpec(cell_size_m=50, dtype="uint8")
    buildings: LayerSpec = LayerSpec(cell_size_m=50, dtype="uint8")
    roads_dt: LayerSpec = LayerSpec(cell_size_m=50, dtype="uint16")
    rivers_dt: LayerSpec = LayerSpec(cell_size_m=50, dtype="uint16")

    def as_dict(self) -> dict[str, LayerSpec]:
        """Layer name → spec, for iteration and serialization."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LayerSet:
        known = {f.name for f in fields(LayerSet)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown overlay layer(s): {sorted(unknown)}")
        return LayerSet(**{k: LayerSpec(**v) for k, v in d.items()})


@dataclass
class XZBounds:
    """DCS xz extent in meters. Matches `dcs.mapping.Rectangle`."""

    top: float  # max x (north)
    bottom: float  # min x (south)
    left: float  # min z (west)
    right: float  # max z (east)

    def width_m(self) -> float:
        return self.right - self.left

    def height_m(self) -> float:
        return self.top - self.bottom


@dataclass
class OsmFilters:
    # Defaults tuned to match what DCS actually renders, not what OSM has.
    # DCS Caucasus only shows the major road network; including secondary /
    # tertiary roads burns tiles on routes the engine never paints. Same logic
    # for canals — DCS rivers are real watercourses, not canals or trickles
    # (hence the river_min_length_m floor on per-way length).
    road_classes_keep: list[str] = field(
        default_factory=lambda: [
            "motorway",
            "trunk",
            "primary",
        ]
    )
    river_classes_keep: list[str] = field(default_factory=lambda: ["river"])
    river_min_length_m: float = 5_000.0
    min_water_polygon_m2: float = 10_000.0
    settlement_radius_m: dict[str, float] = field(
        default_factory=lambda: {
            "city": 2000.0,
            "town": 800.0,
            "village": 300.0,
            "hamlet": 100.0,
        }
    )
    landuse_keep: list[str] = field(
        default_factory=lambda: [
            "residential",
            "industrial",
            "commercial",
            "retail",
        ]
    )


@dataclass
class Manifest:
    version: int
    theater: str  # e.g. "caucasus"
    bounds: XZBounds
    layers: LayerSet
    osm_filters: OsmFilters
    build_timestamp: str = ""
    git_sha: str = ""

    @staticmethod
    def default_for(theater: str, bounds: XZBounds) -> Manifest:
        """Manifest with v1 defaults: all layers at 50 m, plan-spec dtypes."""
        return Manifest(
            version=MANIFEST_VERSION,
            theater=theater,
            bounds=bounds,
            layers=LayerSet(),
            osm_filters=OsmFilters(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "theater": self.theater,
            "bounds": asdict(self.bounds),
            "layers": {k: asdict(v) for k, v in self.layers.as_dict().items()},
            "osm_filters": asdict(self.osm_filters),
            "build_timestamp": self.build_timestamp,
            "git_sha": self.git_sha,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Manifest:
        if d.get("version") != MANIFEST_VERSION:
            raise ValueError(
                f"manifest version {d.get('version')!r} != supported {MANIFEST_VERSION}"
            )
        return Manifest(
            version=d["version"],
            theater=d["theater"],
            bounds=XZBounds(**d["bounds"]),
            layers=LayerSet.from_dict(d["layers"]),
            osm_filters=OsmFilters(**d["osm_filters"]),
            build_timestamp=d.get("build_timestamp", ""),
            git_sha=d.get("git_sha", ""),
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

    @staticmethod
    def read(path: Path) -> Manifest:
        return Manifest.from_dict(json.loads(path.read_text()))
