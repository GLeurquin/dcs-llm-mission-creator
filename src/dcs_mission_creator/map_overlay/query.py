"""Read-only runtime API. Implementations land per-phase.

Phase 0: shape only — every method raises `NotImplementedError`.
Phase 1: elevation + slope queries become real.
Phase 2: road/river/buildings queries become real.
Phase 3: vegetation + forest-edge become real.
Phase 4: find_placement + LOS + relative-prominence become real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from dcs.mapping import Point

from dcs_mission_creator import resources
from dcs_mission_creator.map_overlay.manifest import Manifest
from dcs_mission_creator.map_overlay.placement import Placement, Vegetation

if TYPE_CHECKING:
    from shapely.geometry import LineString
    from shapely.strtree import STRtree

DEFAULT_SEED = 0
"""Sampling seed used when a caller does not ask for a different one.

Placement sampling is random, so an unseeded overlay would put the SAM belt
somewhere new on every build. Fixing it here makes a mission reproducible:
same overlay, same query, same answer.
"""


def _resources_root() -> Path:
    """Filesystem root of the `dcs_mission_creator.resources` package.

    Editable + wheel installs both resolve to a real filesystem path here
    (we only support installs where the resource dir is unpacked, not zipped).
    """
    return Path(str(files(resources)))


def overlay_root(theater: str) -> Path:
    """Canonical on-disk location for a theater's overlay.

    The build pipeline writes here; the runtime API reads from here.
    """
    return _resources_root() / "overlays" / theater


def build_cache_root(theater: str) -> Path:
    """Scratch cache for downloaded SRTM/OSM/WorldCover tiles. Gitignored."""
    return _resources_root() / "_build_cache" / theater


@dataclass(frozen=True)
class LayerWindow:
    """A clamped rectangle of one raster layer, plus where it sits in the raster.

    `valid` is False for any cell that fell outside the raster, which is the whole
    reason this type exists rather than a bare array — see `MapOverlay.read_window`.
    """

    layer: str
    cell_size_m: int
    row0: int
    col0: int
    values: np.ndarray
    valid: np.ndarray


@dataclass
class MapOverlay:
    """Lazy, memory-mapped accessor for a built overlay.

    Instantiate via `MapOverlay.load(theater)`. Backed by zarr chunks +
    sidecar GeoJSON; nothing is read until queried.
    """

    theater: str
    manifest: Manifest
    root: Path
    seed: int = DEFAULT_SEED

    #: Lazily built vector indexes by kind — `{"roads": (STRtree, lines)}`,
    #: see `_vector_index`.
    _vector_indexes: dict[str, tuple[STRtree, list[LineString]]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    #: Opened zarr arrays, keyed by layer name — see `_open_layer`.
    _layers: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @staticmethod
    def load(theater: str, *, seed: int = DEFAULT_SEED) -> MapOverlay:
        root = overlay_root(theater)
        return MapOverlay(
            theater=theater,
            manifest=Manifest.read(root / "manifest.json"),
            root=root,
            seed=seed,
        )

    def _xz_to_cell(self, point: Point, cell_size_m: int) -> tuple[int, int]:
        """DCS (x, z) → (row, col) in a raster whose (0,0) is the NW corner."""
        b = self.manifest.bounds
        row = int((b.top - point.x) / cell_size_m)
        col = int((point.y - b.left) / cell_size_m)
        return row, col

    def _open_layer(self, name: str) -> Any:
        """Open a layer's zarr array once and keep it.

        The point queries below run per unit and, inside `find_placement`'s
        fallback spiral, per candidate cell — reopening the array every time
        made a snap of one platoon hundreds of opens deep. zarr reads stay
        lazy either way; only the handle is cached.
        """
        cached = self._layers.get(name)
        if cached is None:
            import zarr  # local import keeps cold-start cheap for one-layer callers

            cached = zarr.open_array(str(self.root / f"{name}.zarr"), mode="r")
            self._layers[name] = cached
        return cached

    # --------------------------------------------------------- window queries
    def cell_center(self, row: int, col: int, cell_size_m: int) -> tuple[float, float]:
        """(row, col) → the world `(x_north, y_east)` of that cell.

        The inverse of `_xz_to_cell`, which `find_placement` open-codes twice.
        """
        b = self.manifest.bounds
        return (b.top - row * cell_size_m, b.left + col * cell_size_m)

    def read_window(
        self,
        name: str,
        center: Point,
        *,
        half_width_m: float,
        half_height_m: float | None = None,
        fill: int = 0,
    ) -> LayerWindow:
        """Read a rectangle of a layer around `center`, clamped to the raster.

        The bulk counterpart to the point queries. It exists as a public method
        for one specific reason: `_xz_to_cell` performs **no bounds check**, so an
        out-of-extent point produces negative indices and zarr silently returns
        cells from the opposite edge of the map. A caller rasterising a window
        near a map boundary would get a plausible picture of the wrong place.
        Here, out-of-extent cells come back as `fill` with `valid` False, so the
        caller can render no-data rather than someone else's terrain.

        `row0` / `col0` are the window's origin in the full raster, which is what a
        caller needs to turn world coordinates into window-relative indices.
        """
        spec = self.manifest.layers.as_dict()[name]
        cell = spec.cell_size_m
        z = self._open_layer(name)
        h, w = z.shape
        half_h = half_width_m if half_height_m is None else half_height_m

        row_c, col_c = self._xz_to_cell(center, cell)
        half_rows = int(half_h / cell) + 1
        half_cols = int(half_width_m / cell) + 1
        row0, row1 = row_c - half_rows, row_c + half_rows + 1
        col0, col1 = col_c - half_cols, col_c + half_cols + 1

        values = np.full((row1 - row0, col1 - col0), fill, dtype=z.dtype)
        valid = np.zeros(values.shape, dtype=bool)

        # Clip to the raster, then place the clipped block at its own offset.
        cr0, cr1 = max(0, row0), min(h, row1)
        cc0, cc1 = max(0, col0), min(w, col1)
        if cr0 < cr1 and cc0 < cc1:
            block = np.asarray(z[cr0:cr1, cc0:cc1])
            values[cr0 - row0 : cr1 - row0, cc0 - col0 : cc1 - col0] = block
            valid[cr0 - row0 : cr1 - row0, cc0 - col0 : cc1 - col0] = True

        return LayerWindow(
            layer=name,
            cell_size_m=cell,
            row0=row0,
            col0=col0,
            values=values,
            valid=valid,
        )

    def vector_lines(
        self, kind: str, center: Point, radius_m: float
    ) -> list[LineString]:
        """In-range polylines from a vector sidecar (`roads` / `rivers`).

        Wraps the lazily built STRtree so callers outside this package never touch
        `_road_lines`. Coordinates stay in the sidecars' own `(east, north)` order
        — see `find_road_spawn` for why that convention exists.
        """
        from shapely.geometry import Point as ShPoint

        tree, lines = self._vector_index(kind)
        query = ShPoint(center.y, center.x).buffer(radius_m)
        return [lines[int(i)] for i in tree.query(query)]

    # ---------------------------------------------------------- point queries
    def vegetation_at(self, point: Point) -> Vegetation:
        z = self._open_layer("vegetation")
        spec = self.manifest.layers.vegetation
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return Vegetation(int(z[row, col]))

    def elevation_at(self, point: Point) -> int:
        z = self._open_layer("elevation")
        spec = self.manifest.layers.elevation
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return int(z[row, col])

    def slope_at(self, point: Point) -> float:
        z = self._open_layer("slope")
        spec = self.manifest.layers.slope
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return float(z[row, col])

    def distance_to_road_m(self, point: Point) -> float:
        z = self._open_layer("roads_dt")
        spec = self.manifest.layers.roads_dt
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return float(z[row, col]) * spec.cell_size_m

    def distance_to_river_m(self, point: Point) -> float:
        z = self._open_layer("rivers_dt")
        spec = self.manifest.layers.rivers_dt
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return float(z[row, col]) * spec.cell_size_m

    def distance_to_forest_edge_m(
        self, point: Point, window_radius_m: float = 10_000.0
    ) -> float:
        """Signed distance: negative inside forest, positive outside.

        Computes a windowed signed EDT around the query point (no full-map
        materialisation, no global cache). Result is exact for distances up to
        roughly `window_radius_m`; cells whose nearest forest edge lies outside
        the window saturate at the window boundary distance.
        """
        from scipy.ndimage import distance_transform_edt

        spec = self.manifest.layers.vegetation
        z = self._open_layer("vegetation")
        h, w = z.shape
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        if not (0 <= row < h and 0 <= col < w):
            return float(window_radius_m)
        win = int(window_radius_m / spec.cell_size_m)
        r0 = max(0, row - win)
        r1 = min(h, row + win + 1)
        c0 = max(0, col - win)
        c1 = min(w, col + win + 1)
        veg = np.asarray(z[r0:r1, c0:c1])
        dense = veg == int(Vegetation.DENSE_FOREST)
        outside = distance_transform_edt(~dense).astype(np.float32)
        inside = distance_transform_edt(dense).astype(np.float32)
        signed = (outside - inside) * spec.cell_size_m
        return float(signed[row - r0, col - c0])

    def is_built_up(self, point: Point) -> bool:
        z = self._open_layer("buildings")
        spec = self.manifest.layers.buildings
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        return int(z[row, col]) > 0

    def local_prominence_m(self, point: Point, radius_m: float = 2_000.0) -> float:
        """Elevation at point minus mean elevation in the radius window."""
        spec = self.manifest.layers.elevation
        row, col = self._xz_to_cell(point, spec.cell_size_m)
        z = self._open_layer("elevation")
        win_cells = int(radius_m / spec.cell_size_m)
        r0 = max(0, row - win_cells)
        r1 = min(z.shape[0], row + win_cells + 1)
        c0 = max(0, col - win_cells)
        c1 = min(z.shape[1], col + win_cells + 1)
        window = np.asarray(z[r0:r1, c0:c1])
        return float(z[row, col]) - float(window.mean())

    def line_of_sight(
        self, a: Point, b: Point, eye_a_m: float = 2.0, eye_b_m: float = 2.0
    ) -> bool:
        from dcs_mission_creator.map_overlay.los import line_of_sight_cells

        spec = self.manifest.layers.elevation
        z = self._open_layer("elevation")
        ra, ca = self._xz_to_cell(a, spec.cell_size_m)
        rb, cb = self._xz_to_cell(b, spec.cell_size_m)
        h, w = z.shape
        if not (0 <= ra < h and 0 <= ca < w and 0 <= rb < h and 0 <= cb < w):
            return False
        pad = 2
        r0 = max(0, min(ra, rb) - pad)
        r1 = min(h, max(ra, rb) + pad + 1)
        c0 = max(0, min(ca, cb) - pad)
        c1 = min(w, max(ca, cb) + pad + 1)
        window = np.asarray(z[r0:r1, c0:c1])
        elev_a = float(window[ra - r0, ca - c0]) + eye_a_m
        elev_b = float(window[rb - r0, cb - c0]) + eye_b_m
        return line_of_sight_cells(
            window, ra - r0, ca - c0, elev_a, rb - r0, cb - c0, elev_b
        )

    # ------------------------------------------------------------- searches
    def find_placement(
        self,
        near: Point,
        radius_m: float,
        require: Placement,
        count: int = 1,
    ) -> list[Point]:
        """Sample up to `count` cells in radius around `near` matching all filters.

        Strategy: build a boolean mask over the bounded window from cheap
        per-cell criteria (slope, vegetation, distance transforms, prominence),
        sample candidate cells uniformly, then apply expensive per-candidate
        criteria (LOS, road-reachability) one at a time until `count` cells
        pass or the candidate pool is exhausted.

        Sampling is seeded from the overlay's `seed`, so the same query always
        gives the same answer and a mission regenerates identically. To draw a
        different sample, build the overlay with a different seed.
        """
        rng = np.random.default_rng(self.seed)

        # Window in elevation/slope coords — they share 50m and define the
        # bounding box for the search. Other layers' cell sizes may differ but
        # default v1 has all layers at 50m so we treat them uniformly.
        cell = self.manifest.layers.elevation.cell_size_m
        cr, cc = self._xz_to_cell(near, cell)
        win = int(radius_m / cell)
        elev_zarr = self._open_layer("elevation")
        h, w = elev_zarr.shape
        r0 = max(0, cr - win)
        r1 = min(h, cr + win + 1)
        c0 = max(0, cc - win)
        c1 = min(w, cc + win + 1)
        if r0 >= r1 or c0 >= c1:
            return []

        elev_window = np.asarray(elev_zarr[r0:r1, c0:c1])

        # Build a single boolean mask of cells inside the radius circle.
        rows = np.arange(r0, r1)
        cols = np.arange(c0, c1)
        dr = (rows[:, None] - cr) * cell
        dc = (cols[None, :] - cc) * cell
        ok = (dr * dr + dc * dc) <= radius_m * radius_m

        # Slope
        if require.max_slope_deg is not None:
            slope = np.asarray(self._open_layer("slope")[r0:r1, c0:c1])
            ok &= slope <= require.max_slope_deg

        # Vegetation, plus optional forest_buffer_m
        if require.not_in:
            veg = np.asarray(self._open_layer("vegetation")[r0:r1, c0:c1])
            bad = np.zeros_like(veg, dtype=bool)
            for cls in require.not_in:
                bad |= veg == int(cls)
            if require.forest_buffer_m > 0:
                from scipy.ndimage import binary_dilation

                buf_cells = int(require.forest_buffer_m / cell)
                if buf_cells > 0:
                    bad = binary_dilation(bad, iterations=buf_cells)
            ok &= ~bad

        # Road / river distance (near / min)
        if require.near_road_m is not None:
            rdt = np.asarray(self._open_layer("roads_dt")[r0:r1, c0:c1])
            ok &= rdt * cell <= require.near_road_m
        if require.min_distance_to_road_m is not None:
            rdt = np.asarray(self._open_layer("roads_dt")[r0:r1, c0:c1])
            ok &= rdt * cell >= require.min_distance_to_road_m
        if require.near_water_m is not None:
            wdt = np.asarray(self._open_layer("rivers_dt")[r0:r1, c0:c1])
            ok &= wdt * cell <= require.near_water_m

        # Built-up
        if require.not_in_built_up:
            bldg = np.asarray(self._open_layer("buildings")[r0:r1, c0:c1])
            ok &= bldg == 0

        # Forest edge proximity
        if require.near_forest_edge_m is not None:
            # Local distance transform on dense-forest mask in the window
            from scipy.ndimage import distance_transform_edt

            veg = np.asarray(self._open_layer("vegetation")[r0:r1, c0:c1])
            dense = (veg == int(Vegetation.DENSE_FOREST)).astype(np.uint8)
            edge_dist = distance_transform_edt(dense == 0).astype(np.float32) * cell
            ok &= edge_dist <= require.near_forest_edge_m

        # Absolute elevation
        if require.min_elevation_m is not None:
            ok &= elev_window >= require.min_elevation_m
        if require.max_elevation_m is not None:
            ok &= elev_window <= require.max_elevation_m

        # Relative height (prominence) within `relative_height_radius_m`.
        # We need an extended elevation window so `uniform_filter` sees the
        # full radius around every center cell.
        if (
            require.min_relative_height_m is not None
            or require.max_relative_height_m is not None
        ):
            from scipy.ndimage import uniform_filter

            size = max(1, int(2 * require.relative_height_radius_m / cell))
            pad = size // 2 + 1
            rp0 = max(0, r0 - pad)
            rp1 = min(h, r1 + pad)
            cp0 = max(0, c0 - pad)
            cp1 = min(w, c1 + pad)
            elev_ext = np.asarray(elev_zarr[rp0:rp1, cp0:cp1]).astype(np.float32)
            mean_ext = uniform_filter(elev_ext, size=size, mode="reflect")
            # Map (r0..r1, c0..c1) into the extended window.
            sr0 = r0 - rp0
            sr1 = sr0 + (r1 - r0)
            sc0 = c0 - cp0
            sc1 = sc0 + (c1 - c0)
            rel = elev_window.astype(np.float32) - mean_ext[sr0:sr1, sc0:sc1]
            del elev_ext, mean_ext
            if require.min_relative_height_m is not None:
                ok &= rel >= require.min_relative_height_m
            if require.max_relative_height_m is not None:
                ok &= rel <= require.max_relative_height_m

        # Sector filter
        if require.in_sector_from is not None:
            anchor, h_min, h_max = require.in_sector_from
            # Heading from anchor → cell, 0 = north (=+x = -row), 90 = east (=+z = +col)
            cell_rows = np.arange(r0, r1)[:, None] * 1.0
            cell_cols = np.arange(c0, c1)[None, :] * 1.0
            world_x = self.manifest.bounds.top - cell_rows * cell
            world_z = self.manifest.bounds.left + cell_cols * cell
            dx = world_x - anchor.x
            dz = world_z - anchor.y
            heading_deg = np.degrees(np.arctan2(dz, dx)) % 360
            if h_min <= h_max:
                ok &= (heading_deg >= h_min) & (heading_deg <= h_max)
            else:  # wraparound (e.g. 350..10)
                ok &= (heading_deg >= h_min) | (heading_deg <= h_max)

        # min/max distance to anchor points
        for anchor, dist in require.min_distance_to:
            cell_rows = np.arange(r0, r1)[:, None] * 1.0
            cell_cols = np.arange(c0, c1)[None, :] * 1.0
            wx = self.manifest.bounds.top - cell_rows * cell
            wz = self.manifest.bounds.left + cell_cols * cell
            d2 = (wx - anchor.x) ** 2 + (wz - anchor.y) ** 2
            ok &= d2 >= dist * dist
        for anchor, dist in require.max_distance_to:
            cell_rows = np.arange(r0, r1)[:, None] * 1.0
            cell_cols = np.arange(c0, c1)[None, :] * 1.0
            wx = self.manifest.bounds.top - cell_rows * cell
            wz = self.manifest.bounds.left + cell_cols * cell
            d2 = (wx - anchor.x) ** 2 + (wz - anchor.y) ** 2
            ok &= d2 <= dist * dist

        # Collect candidate (row, col) coordinates in original raster space
        candidates_local = np.argwhere(ok)
        if candidates_local.size == 0:
            return []
        candidates = candidates_local + np.array([r0, c0])
        # Shuffle for stochastic sampling
        rng.shuffle(candidates)

        # Apply expensive per-candidate filters (LOS, reachability) lazily.
        results: list[Point] = []
        for row, col in candidates:
            wx = self.manifest.bounds.top - row * cell
            wz = self.manifest.bounds.left + col * cell
            cand_pt = Point(float(wx), float(wz), near._terrain)
            if require.line_of_sight_to and not all(
                self.line_of_sight(cand_pt, t) for t in require.line_of_sight_to
            ):
                continue
            if require.no_line_of_sight_to and any(
                self.line_of_sight(cand_pt, t) for t in require.no_line_of_sight_to
            ):
                continue
            # `reachable_by_road_from`: simple check — both this point and the
            # anchor must be within 500 m of a road. Engine handles the actual
            # routing via OnRoad waypoints.
            if require.reachable_by_road_from is not None:
                if self.distance_to_road_m(cand_pt) > 500.0:
                    continue
                if self.distance_to_road_m(require.reachable_by_road_from) > 500.0:
                    continue
            results.append(cand_pt)
            if len(results) >= count:
                break
        return results

    def _vector_index(self, kind: str) -> tuple[STRtree, list[LineString]]:
        """Polylines of one sidecar + their spatial index, read from disk once.

        `roads.geojson` is tens of megabytes of vertices, so the parse and the
        STRtree build happen on first use and are cached on the instance for every
        later `find_road_spawn` / `vector_lines` call.
        """
        from shapely.geometry import LineString
        from shapely.strtree import STRtree

        cached = self._vector_indexes.get(kind)
        if cached is None:
            path = self.root / f"{kind}.geojson"
            if not path.is_file():
                raise FileNotFoundError(f"no {kind}.geojson in overlay at {self.root}")
            data = json.loads(path.read_text())
            lines = [
                LineString(feat["geometry"]["coordinates"])
                for feat in data["features"]
                if feat["geometry"]["type"] == "LineString"
            ]
            cached = (STRtree(lines), lines)
            self._vector_indexes[kind] = cached
        return cached

    def _road_lines(self) -> tuple[STRtree, list[LineString]]:
        """Road polylines + their spatial index. See `_vector_index`."""
        return self._vector_index("roads")

    def find_road_spawn(
        self,
        near: Point,
        radius_m: float = 5_000.0,
        min_distance_to_built_up_m: float = 0.0,
    ) -> Point:
        """Return the nearest road-polyline vertex to `near`, subject to filters.

        Reads `roads.geojson` once (lazily); subsequent calls reuse the cached
        STRtree spatial index.
        """
        from shapely.geometry import Point as ShPoint

        tree, lines = self._road_lines()

        # Sidecar geojson stores shapes in (east, north) = (y_dcs, x_dcs) so
        # rasterize and the geojson agree; query in the same convention.
        query_pt = ShPoint(near.y, near.x)
        candidate_idx = tree.query(query_pt.buffer(radius_m))
        best: tuple[float, float, float] | None = None  # (dist, east, north)
        for idx in candidate_idx:
            line = lines[int(idx)]
            cand = line.interpolate(line.project(query_pt))
            d = query_pt.distance(cand)
            if d > radius_m:
                continue
            # cand.x = east, cand.y = north → pydcs Point(x_north, y_east)
            cand_pt = Point(cand.y, cand.x, near._terrain)
            if min_distance_to_built_up_m > 0 and self.is_built_up(cand_pt):
                continue
            if best is None or d < best[0]:
                best = (d, cand.x, cand.y)
        if best is None:
            raise LookupError(
                f"no road within {radius_m:.0f} m of ({near.x:.0f}, {near.y:.0f})"
            )
        # best[1]=east, best[2]=north → Point(x_north=best[2], y_east=best[1])
        return Point(best[2], best[1], near._terrain)
