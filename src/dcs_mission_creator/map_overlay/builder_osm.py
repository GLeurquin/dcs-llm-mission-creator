"""OSM ingest: roads / rivers / buildings → DCS xz zarr + sidecar GeoJSON.

Crash-resume pipeline. All per-tile state is checkpointed to disk before the
next tile starts, so a crash anywhere past tile 1 resumes from the last
completed tile rather than redoing Overpass JSON parse + projection.

Progress layout under `_build_cache/<theater>/osm/_progress/`:

    roads.bin / rivers.bin / buildings.bin   uint8 memmap masks
    processed.jsonl                          one tile bbox per line
    seen_ways.jsonl                          one OSM way id per line
    roads.jsonl                              one road LineString per line
    rivers.jsonl                             one waterway / water polygon per line
    settlements.jsonl                        one (x, z, density) per line
    stage.txt                                "absorb" | "dt_roads" | "dt_rivers"
                                             | "buildings" | "geojson" | "done"

Memory ceiling: only one mask is "active" in RAM at a time for the DT step,
the others stay in memmap files (file-backed pages, paged out by the kernel).
The EDT's float64 scratch is ~5.3 GB for a Caucasus 50 m grid — we clip it
in place and cast straight to uint16 (no float32 intermediate), so peak DT
heap is roughly mask + float64 buffer + uint16 result ≈ 7 GB.

On a clean success the `_progress/` directory is removed; on a failure it is
left in place so the next `build caucasus --layers osm` invocation resumes.
"""

from __future__ import annotations

import gc
import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import numpy as np
import requests
import structlog
from dcs.terrain.terrain import Terrain
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString, Point as ShPoint, Polygon, mapping
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dcs_mission_creator.map_overlay._zarr_io import save_zarr
from dcs_mission_creator.map_overlay.coords import (
    LatLonBBox,
    latlon_to_dcs,
    rendered_xz_bounds,
    terrain_bbox_latlon,
)
from dcs_mission_creator.map_overlay.manifest import (
    Manifest,
    OsmFilters,
    XZBounds,
    osm_filters_for,
)
from dcs_mission_creator.map_overlay.query import build_cache_root, overlay_root

_LOGGER = structlog.get_logger(__name__)

_TILE_DEG = 2.0
_TIMEOUT_S = 600

_PLACE_DENSITY: dict[str, int] = {"city": 3, "town": 2, "village": 1, "hamlet": 1}
_DENSITY_TO_CLASS: dict[int, str] = {1: "village", 2: "town", 3: "city"}

_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
_RETRY_STATUS = {429, 500, 502, 503, 504}

_STAGE_ABSORB = "absorb"
_STAGE_DT_ROADS = "dt_roads"
_STAGE_DT_RIVERS = "dt_rivers"
_STAGE_BUILDINGS = "buildings"
_STAGE_GEOJSON = "geojson"
_STAGE_DONE = "done"


@dataclass
class _OsmBuild:
    cell_size_m: int = 50
    chunk: int = 512


@dataclass
class _Grid:
    height: int
    width: int
    transform: object


@dataclass
class _ProgressDir:
    """All checkpoint files for one theater's OSM build."""

    root: Path

    @property
    def roads_bin(self) -> Path:
        return self.root / "roads.bin"

    @property
    def rivers_bin(self) -> Path:
        return self.root / "rivers.bin"

    @property
    def buildings_bin(self) -> Path:
        return self.root / "buildings.bin"

    @property
    def processed_jsonl(self) -> Path:
        return self.root / "processed.jsonl"

    @property
    def seen_jsonl(self) -> Path:
        return self.root / "seen_ways.jsonl"

    @property
    def roads_jsonl(self) -> Path:
        return self.root / "roads.jsonl"

    @property
    def rivers_jsonl(self) -> Path:
        return self.root / "rivers.jsonl"

    @property
    def settlements_jsonl(self) -> Path:
        return self.root / "settlements.jsonl"

    @property
    def stage_txt(self) -> Path:
        return self.root / "stage.txt"


def _overpass_query(s: float, w: float, n: float, e: float) -> str:
    bb = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:{_TIMEOUT_S - 30}];
(
  way["highway"]({bb});
  way["waterway"]({bb});
  way["natural"="water"]({bb});
  relation["natural"="water"]({bb});
  node["place"]({bb});
  way["landuse"]({bb});
  relation["landuse"]({bb});
);
out body;
>;
out skel qt;
"""


def _places_query(s: float, w: float, n: float, e: float) -> str:
    """Named settlements only — the `node["place"]` slice of the main query.

    A separate, far cheaper request rather than a re-parse of the cached tiles:
    those are 100 files and 19 GB for Caucasus because they carry every highway
    and building node, while the place nodes in them are a few thousand objects.
    One `node["place"]` query per tile is ~100 KB and seconds.
    """
    return f"""[out:json][timeout:{_TIMEOUT_S - 30}];
(
  node["place"]({s},{w},{n},{e});
);
out body;
"""


def _log_overpass_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    sleep_s = retry_state.next_action.sleep if retry_state.next_action else 0.0
    next_endpoint = _OVERPASS_ENDPOINTS[
        retry_state.attempt_number % len(_OVERPASS_ENDPOINTS)
    ]
    _LOGGER.warning(
        "osm.fetch_retry",
        attempt=retry_state.attempt_number,
        error=str(exc) if exc else "",
        sleep_s=sleep_s,
        next_endpoint=next_endpoint,
    )


def _fetch_tile_json(s: float, w: float, n: float, e: float, cache_dir: Path) -> dict:
    """Fetch + cache one Overpass tile. Cache key: bbox to 3 dp."""
    cache = cache_dir / f"osm_{s:.3f}_{w:.3f}_{n:.3f}_{e:.3f}.json"
    return _fetch_overpass(_overpass_query(s, w, n, e), cache)


def _fetch_overpass(query: str, cache: Path) -> dict:
    """POST one Overpass query, caching the raw response body at `cache`.

    Shared by the full tile fetch and the far smaller places fetch: same endpoint
    rotation, same retry policy, same on-disk cache, so a places run costs nothing
    on the second invocation and a rate-limited endpoint is handled once.
    """
    if cache.exists() and cache.stat().st_size > 0:
        return json.loads(cache.read_text())
    cache.parent.mkdir(parents=True, exist_ok=True)

    for attempt in Retrying(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=5, max=60),
        retry=retry_if_exception_type(requests.RequestException),
        before_sleep=_log_overpass_retry,
        reraise=True,
    ):
        with attempt:
            url = _OVERPASS_ENDPOINTS[
                (attempt.retry_state.attempt_number - 1) % len(_OVERPASS_ENDPOINTS)
            ]
            resp = requests.post(
                url,
                data={"data": query},
                timeout=_TIMEOUT_S,
                headers={
                    "User-Agent": "dcs-mission-creator/0.1 (+https://github.com/gleurquin)",
                    "Accept": "application/json",
                },
            )
            if resp.status_code in _RETRY_STATUS:
                raise requests.HTTPError(
                    f"{resp.status_code} from {url}", response=resp
                )
            resp.raise_for_status()
            cache.write_text(resp.text)
            return resp.json()
    raise RuntimeError(f"Overpass fetch exhausted retries for {cache.name}")


def _tile_steps(bbox: LatLonBBox) -> tuple[list[int], list[int]]:
    south, west = math.floor(bbox.south), math.floor(bbox.west)
    north, east = math.ceil(bbox.north), math.ceil(bbox.east)
    lat_steps = list(range(int(south), int(north), int(_TILE_DEG)))
    lon_steps = list(range(int(west), int(east), int(_TILE_DEG)))
    return lat_steps, lon_steps


def _iter_tile_bboxes(
    lat_steps: list[int], lon_steps: list[int]
) -> Iterator[tuple[int, int, int, int]]:
    for lat0 in lat_steps:
        for lon0 in lon_steps:
            yield lat0, lon0, lat0 + int(_TILE_DEG), lon0 + int(_TILE_DEG)


def _project_nodes_bulk(
    nodes_latlon: dict[int, tuple[float, float]], terrain: Terrain
) -> dict[int, tuple[float, float]]:
    """Bulk WGS84 → DCS xz via pyproj on numpy arrays. ~1000× faster than per-call."""
    if not nodes_latlon:
        return {}
    ids = list(nodes_latlon.keys())
    lats = np.fromiter(
        (nodes_latlon[i][0] for i in ids), dtype=np.float64, count=len(ids)
    )
    lons = np.fromiter(
        (nodes_latlon[i][1] for i in ids), dtype=np.float64, count=len(ids)
    )
    xs, ys = terrain._ll_to_point_transformer.transform(lats, lons)
    return {nid: (float(xs[i]), float(ys[i])) for i, nid in enumerate(ids)}


@dataclass
class _TileAccumulator:
    """Per-tile shapely geometries accumulated into the global rasters/lists."""

    roads: list[LineString]
    waterways: list[LineString]
    water_polys: list[Polygon]
    urban: list[Polygon]
    settlements: list[tuple[ShPoint, int]]
    new_way_ids: list[int]


def _accumulate_tile(
    tile: dict, filters: OsmFilters, terrain: Terrain, seen_way_ids: set[int]
) -> _TileAccumulator:
    """Filter + project one tile's features. Mutates `seen_way_ids` for dedup."""
    nodes_latlon: dict[int, tuple[float, float]] = {}
    for el in tile.get("elements", []):
        if el["type"] == "node":
            nodes_latlon[el["id"]] = (el["lat"], el["lon"])
    nodes_xz = _project_nodes_bulk(nodes_latlon, terrain)

    acc = _TileAccumulator(
        roads=[],
        waterways=[],
        water_polys=[],
        urban=[],
        settlements=[],
        new_way_ids=[],
    )

    for el in tile.get("elements", []):
        tags = el.get("tags") or {}
        if el["type"] == "way":
            wid = el["id"]
            if wid in seen_way_ids:
                continue
            seen_way_ids.add(wid)
            acc.new_way_ids.append(wid)
            # rasterio's `transform` treats shape coords as (X_east, Y_north),
            # but pydcs Point uses (x_north, y_east). Swap to (east, north)
            # when handing geometry to shapely so rasterize lands pixels in
            # the right cells and the geojson sidecars stay self-consistent
            # with `find_road_spawn` (which also queries in (east, north)).
            coords = [
                (nodes_xz[n][1], nodes_xz[n][0])
                for n in el.get("nodes", [])
                if n in nodes_xz
            ]
            hwy = tags.get("highway")
            wwy = tags.get("waterway")
            nat = tags.get("natural")
            lu = tags.get("landuse")
            if hwy in filters.road_classes_keep and len(coords) >= 2:
                acc.roads.append(LineString(coords))
            elif wwy in filters.river_classes_keep and len(coords) >= 2:
                ls = LineString(coords)
                if ls.length >= filters.river_min_length_m:
                    acc.waterways.append(ls)
            elif nat == "water" and len(coords) >= 3:
                try:
                    poly = Polygon(coords)
                    if poly.is_valid and poly.area >= filters.min_water_polygon_m2:
                        acc.water_polys.append(poly)
                except (ValueError, TypeError):
                    pass
            elif lu in filters.landuse_keep and len(coords) >= 3:
                try:
                    poly = Polygon(coords)
                    if poly.is_valid:
                        acc.urban.append(poly)
                except (ValueError, TypeError):
                    pass
        elif el["type"] == "node":
            place = tags.get("place")
            if place in _PLACE_DENSITY and el["id"] in nodes_xz:
                px, pz = nodes_xz[el["id"]]
                # Store as (east, north) — see comment in the way branch above.
                acc.settlements.append((ShPoint(pz, px), _PLACE_DENSITY[place]))
    return acc


def _rasterize_into(
    target: np.ndarray,
    shapes: list[tuple[LineString | Polygon, int]],
    transform: object,
    all_touched: bool,
) -> None:
    """Rasterize shapes onto `target` in place, ORing the result."""
    if not shapes:
        return
    layer = rasterize(
        shapes=shapes,
        out_shape=target.shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=all_touched,
    )
    np.maximum(target, layer, out=target)
    del layer


def _settlement_disk(
    pt: ShPoint, density: int, settlement_radius_m: dict[str, float], cell_size_m: int
) -> Polygon:
    radius = settlement_radius_m[_DENSITY_TO_CLASS.get(density, "village")]
    if density == 1 and radius < cell_size_m:
        radius = cell_size_m
    return pt.buffer(radius)


def _absorb_tile(
    acc: _TileAccumulator,
    roads_mask: np.ndarray,
    rivers_mask: np.ndarray,
    buildings: np.ndarray,
    grid: _Grid,
    filters: OsmFilters,
    cfg: _OsmBuild,
) -> None:
    """Burn one tile's geometries into the shared raster masks.

    All shapes for a given mask are passed to a single `rasterize` call to avoid
    re-allocating a height×width scratch buffer per geometry.
    """
    _rasterize_into(
        roads_mask, [(g, 1) for g in acc.roads], grid.transform, all_touched=True
    )
    rivers_shapes: list[tuple[LineString | Polygon, int]] = [
        (g, 1) for g in acc.waterways
    ]
    rivers_shapes.extend((g, 1) for g in acc.water_polys)
    _rasterize_into(rivers_mask, rivers_shapes, grid.transform, all_touched=True)
    bldg_shapes: list[tuple[LineString | Polygon, int]] = [(p, 2) for p in acc.urban]
    disks_by_density: list[tuple[Polygon, int]] = [
        (
            _settlement_disk(pt, d, filters.settlement_radius_m, cfg.cell_size_m),
            d,
        )
        for pt, d in acc.settlements
    ]
    disks_by_density.sort(key=lambda pd: pd[1])
    bldg_shapes.extend(disks_by_density)
    _rasterize_into(buildings, bldg_shapes, grid.transform, all_touched=False)


def _stream_sidecar(prog: _ProgressDir, acc: _TileAccumulator) -> None:
    """Append the tile's features to the per-layer JSONL files (crash-safe)."""
    with prog.roads_jsonl.open("a") as f:
        for g in acc.roads:
            f.write(json.dumps([list(p) for p in g.coords]) + "\n")
    with prog.rivers_jsonl.open("a") as f:
        for g in acc.waterways:
            f.write(json.dumps({"k": "l", "c": [list(p) for p in g.coords]}) + "\n")
        for poly in acc.water_polys:
            f.write(
                json.dumps({"k": "p", "c": [list(p) for p in poly.exterior.coords]})
                + "\n"
            )
    with prog.settlements_jsonl.open("a") as f:
        for pt, d in acc.settlements:
            f.write(json.dumps([pt.x, pt.y, int(d)]) + "\n")


def _append_seen_ways(prog: _ProgressDir, new_ids: list[int]) -> None:
    if not new_ids:
        return
    with prog.seen_jsonl.open("a") as f:
        for wid in new_ids:
            f.write(f"{wid}\n")


def _mark_processed(prog: _ProgressDir, bbox: tuple[int, int, int, int]) -> None:
    with prog.processed_jsonl.open("a") as f:
        f.write(json.dumps(list(bbox)) + "\n")


def _load_processed(prog: _ProgressDir) -> set[tuple[int, int, int, int]]:
    if not prog.processed_jsonl.exists():
        return set()
    out: set[tuple[int, int, int, int]] = set()
    with prog.processed_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            arr = json.loads(line)
            out.add((int(arr[0]), int(arr[1]), int(arr[2]), int(arr[3])))
    return out


def _load_seen_ways(prog: _ProgressDir) -> set[int]:
    if not prog.seen_jsonl.exists():
        return set()
    out: set[int] = set()
    with prog.seen_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.add(int(line))
    return out


def _read_stage(prog: _ProgressDir) -> str:
    if not prog.stage_txt.exists():
        return _STAGE_ABSORB
    return prog.stage_txt.read_text().strip() or _STAGE_ABSORB


def _write_stage(prog: _ProgressDir, stage: str) -> None:
    prog.stage_txt.write_text(stage)


def _open_mask(path: Path, shape: tuple[int, int]) -> np.memmap:
    """Open or create a uint8 memmap mask, zero-initialized on first creation."""
    if path.exists():
        return np.memmap(path, dtype=np.uint8, mode="r+", shape=shape)
    return np.memmap(path, dtype=np.uint8, mode="w+", shape=shape)


_DT_STRIP_ROWS = 2000
_DT_HALO_ROWS = 1500  # ≈75 km at 50 m: max EDT distance we care about


def _chunked_dt_to_memmap(
    mask_path: Path,
    shape: tuple[int, int],
    out_path: Path,
    layer_name: str,
    strip_rows: int = _DT_STRIP_ROWS,
    halo_rows: int = _DT_HALO_ROWS,
) -> Path:
    """Compute EDT in vertical strips, write uint16 result to a memmap on disk.

    Each strip processes `strip_rows` center rows plus a `halo_rows` halo on
    each side so that distances ≤ halo_rows are correct for the center.
    Output values are clipped to halo_rows — cells farther than that read as
    halo_rows, which is fine for proximity-threshold queries.

    Peak per-strip alloc (Caucasus 50 m, 33800 cols):
        bool inv:     ~(strip_rows + 2*halo_rows) × 33800 × 1 B
        float64 dt:   ~(strip_rows + 2*halo_rows) × 33800 × 8 B
    At strip_rows=2000, halo_rows=1500 → padded 5000 rows → ~1.35 GB float64.
    """
    h, _ = shape
    out_mm = np.memmap(out_path, dtype=np.uint16, mode="w+", shape=shape)
    max_val = min(halo_rows, 65535)
    src_mm = np.memmap(mask_path, dtype=np.uint8, mode="r", shape=shape)
    for r0 in range(0, h, strip_rows):
        r1 = min(r0 + strip_rows, h)
        r0_pad = max(0, r0 - halo_rows)
        r1_pad = min(h, r1 + halo_rows)
        strip = np.asarray(src_mm[r0_pad:r1_pad])  # forces load of strip into RAM
        inv = strip == 0
        del strip
        gc.collect()
        dt = distance_transform_edt(inv)
        del inv
        gc.collect()
        c0 = r0 - r0_pad
        c1 = c0 + (r1 - r0)
        center = dt[c0:c1]
        np.clip(center, 0, max_val, out=center)
        out_mm[r0:r1] = center.astype(np.uint16)
        del dt, center
        gc.collect()
        _LOGGER.info(
            "osm.dt_strip_done",
            layer=layer_name,
            r0=r0,
            r1=r1,
            total_rows=h,
        )
    del src_mm
    out_mm.flush()
    del out_mm
    gc.collect()
    return out_path


def _save_geojson(
    out_path: Path, features: list[LineString | Polygon], properties_list: list[dict]
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = {
        "type": "FeatureCollection",
        "crs": "DCS_xz",
        "features": [
            {"type": "Feature", "geometry": mapping(g), "properties": p}
            for g, p in zip(features, properties_list, strict=True)
        ],
    }
    out_path.write_text(json.dumps(fc))


def _load_or_create_manifest(
    out_dir: Path, theater_slug: str, terrain: Terrain
) -> Manifest:
    """Load the theater's manifest, reconciled against the current filter policy.

    The filters decide which OSM ways get rasterised, and they are read back off
    the manifest rather than from the defaults — so a manifest written before a
    policy change would silently rebuild with the *old* selection and the rebuild
    would be a no-op. Overwrite them from `osm_filters_for` instead, and say so:
    the layers on disk no longer match the ones the new filters describe until
    this build finishes.
    """
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return Manifest.default_for(
            theater_slug,
            rendered_xz_bounds(theater_slug, terrain),
        )
    manifest = Manifest.read(manifest_path)
    policy = osm_filters_for(theater_slug)
    if manifest.osm_filters != policy:
        _LOGGER.info(
            "osm.filters_changed",
            theater=theater_slug,
            was=manifest.osm_filters.road_classes_keep,
            now=policy.road_classes_keep,
        )
        manifest.osm_filters = policy
    return manifest


def _init_grid(bounds: XZBounds, cfg: _OsmBuild) -> _Grid:
    width = int(round((bounds.right - bounds.left) / cfg.cell_size_m))
    height = int(round((bounds.top - bounds.bottom) / cfg.cell_size_m))
    transform = from_origin(bounds.left, bounds.top, cfg.cell_size_m, cfg.cell_size_m)
    return _Grid(height=height, width=width, transform=transform)


def _absorb_all_tiles(
    prog: _ProgressDir,
    cache_dir: Path,
    terrain: Terrain,
    grid: _Grid,
    cfg: _OsmBuild,
    manifest: Manifest,
    lat_steps: list[int],
    lon_steps: list[int],
    total: int,
) -> None:
    """Stream every tile into the memmap masks + JSONL sidecars (resumable)."""
    shape = (grid.height, grid.width)
    roads_mask = _open_mask(prog.roads_bin, shape)
    rivers_mask = _open_mask(prog.rivers_bin, shape)
    buildings = _open_mask(prog.buildings_bin, shape)
    processed = _load_processed(prog)
    seen_way_ids = _load_seen_ways(prog)
    if processed or seen_way_ids:
        _LOGGER.info(
            "osm.resume",
            processed_tiles=len(processed),
            seen_ways=len(seen_way_ids),
        )

    for i, bbox in enumerate(_iter_tile_bboxes(lat_steps, lon_steps), 1):
        if bbox in processed:
            _LOGGER.info("osm.tile_skipped", done=i, total=total)
            continue
        tile = _fetch_tile_json(bbox[0], bbox[1], bbox[2], bbox[3], cache_dir)
        acc = _accumulate_tile(tile, manifest.osm_filters, terrain, seen_way_ids)
        del tile
        _absorb_tile(
            acc, roads_mask, rivers_mask, buildings, grid, manifest.osm_filters, cfg
        )
        _stream_sidecar(prog, acc)
        _append_seen_ways(prog, acc.new_way_ids)
        del acc
        gc.collect()
        _mark_processed(prog, bbox)
        _LOGGER.info("osm.tile_done", done=i, total=total)

    # Flush memmaps to disk so a crash after this point doesn't lose mask state.
    roads_mask.flush()
    rivers_mask.flush()
    buildings.flush()
    del roads_mask, rivers_mask, buildings
    gc.collect()


def _dt_and_save_zarr(
    prog: _ProgressDir,
    mask_path: Path,
    shape: tuple[int, int],
    out_path: Path,
    cfg: _OsmBuild,
    layer_name: str,
) -> None:
    """Strip-chunked EDT → uint16 memmap on disk → zarr (low-RAM throughout)."""
    _LOGGER.info(
        "osm.distance_transform.start",
        layer=layer_name,
        strip_rows=_DT_STRIP_ROWS,
        halo_rows=_DT_HALO_ROWS,
    )
    dt_bin = prog.root / f"{layer_name}_dt.bin"
    _chunked_dt_to_memmap(mask_path, shape, dt_bin, layer_name)
    # Re-open the on-disk memmap read-only and stream stripes into zarr.
    dt_mm = np.memmap(dt_bin, dtype=np.uint16, mode="r", shape=shape)
    save_zarr(out_path, dt_mm, cfg.chunk, cfg.cell_size_m, "uint16")
    del dt_mm
    gc.collect()
    dt_bin.unlink(missing_ok=True)
    _LOGGER.info("osm.distance_transform.done", layer=layer_name, path=str(out_path))


def _save_buildings_zarr(
    prog: _ProgressDir,
    shape: tuple[int, int],
    out_path: Path,
    cfg: _OsmBuild,
) -> None:
    mask = np.memmap(prog.buildings_bin, dtype=np.uint8, mode="r", shape=shape)
    mask_dense = np.asarray(mask)
    del mask
    gc.collect()
    save_zarr(out_path, mask_dense, cfg.chunk, cfg.cell_size_m, "uint8")
    del mask_dense
    gc.collect()
    _LOGGER.info("buildings.saved", path=str(out_path))


def _save_sidecars_from_jsonl(prog: _ProgressDir, out_dir: Path) -> None:
    """Stream roads.jsonl / rivers.jsonl into FeatureCollection geojson on disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _stream_roads_geojson(prog.roads_jsonl, out_dir / "roads.geojson")
    _stream_rivers_geojson(prog.rivers_jsonl, out_dir / "rivers.geojson")


def _stream_roads_geojson(jsonl_path: Path, out_path: Path) -> None:
    if not jsonl_path.exists():
        out_path.write_text(
            json.dumps({"type": "FeatureCollection", "crs": "DCS_xz", "features": []})
        )
        return
    with out_path.open("w") as out:
        out.write('{"type":"FeatureCollection","crs":"DCS_xz","features":[')
        first = True
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                coords = json.loads(line)
                feat = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"kind": "road"},
                }
                if not first:
                    out.write(",")
                out.write(json.dumps(feat))
                first = False
        out.write("]}")


def _stream_rivers_geojson(jsonl_path: Path, out_path: Path) -> None:
    if not jsonl_path.exists():
        out_path.write_text(
            json.dumps({"type": "FeatureCollection", "crs": "DCS_xz", "features": []})
        )
        return
    with out_path.open("w") as out:
        out.write('{"type":"FeatureCollection","crs":"DCS_xz","features":[')
        first = True
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj["k"] == "l":
                    geom = {"type": "LineString", "coordinates": obj["c"]}
                    kind = "waterway"
                else:
                    geom = {"type": "Polygon", "coordinates": [obj["c"]]}
                    kind = "water"
                feat = {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {"kind": kind},
                }
                if not first:
                    out.write(",")
                out.write(json.dumps(feat))
                first = False
        out.write("]}")


def build_places(terrain: Terrain, theater_slug: str) -> Path:
    """Write `places.geojson`: the named settlements, in DCS xz.

    Why this exists at all: the OSM pass already reads these very nodes — they are
    what `buildings.zarr` is built from, buffered into disks by class — and then
    drops the name, so every bright settlement in a recon still was anonymous. The
    names are what let a still be *located* rather than merely believed.

    Only the classes in `_PLACE_DENSITY` are kept, and that is the honesty
    constraint rather than a filter for tidiness: those are exactly the classes
    the raster was rasterized from, so a label written from this sidecar always
    names a return the picture actually draws. A `locality` or a `neighbourhood`
    (5,700 of them on Caucasus) was never rasterized, so labelling one would put a
    place name on empty ground.

    Runs standalone (`overlay build <theater> --layers places`) and touches no
    raster and no manifest, so it can be added to an overlay that is already
    built. Per-tile fetches are cached beside the main tiles, so a re-run is
    offline.
    """
    out_dir = overlay_root(theater_slug)
    cache_dir = build_cache_root(theater_slug) / "osm"
    bbox = terrain_bbox_latlon(terrain, theater_slug)
    lat_steps, lon_steps = _tile_steps(bbox)
    tiles = list(_iter_tile_bboxes(lat_steps, lon_steps))

    # Keyed on rounded xz, not on the name: tiles share edges, so the same village
    # comes back twice, and two distinct villages do share a name (three "Ахыуаа"
    # inside one 25 km frame on this map).
    found: dict[tuple[int, int], tuple[str, str]] = {}
    for i, (s_lat, w_lon, n_lat, e_lon) in enumerate(tiles, start=1):
        cache = (
            cache_dir / f"places_{s_lat:.3f}_{w_lon:.3f}_{n_lat:.3f}_{e_lon:.3f}.json"
        )
        tile = _fetch_overpass(_places_query(s_lat, w_lon, n_lat, e_lon), cache)
        added = 0
        for el in tile.get("elements", ()):
            tags = el.get("tags", {})
            kind = tags.get("place")
            name = _place_label(tags)
            if kind not in _PLACE_DENSITY or not name:
                continue
            pt = latlon_to_dcs(float(el["lat"]), float(el["lon"]), terrain)
            key = (round(pt.x), round(pt.y))
            if key not in found:
                found[key] = (name, kind)
                added += 1
        _LOGGER.info(
            "places.tile",
            theater=theater_slug,
            tile=f"{i}/{len(tiles)}",
            added=added,
            total=len(found),
        )

    # Sorted so the sidecar is byte-stable across runs, like every other artefact.
    items = sorted(found.items(), key=lambda kv: (kv[1][0], kv[0]))
    out_path = out_dir / "places.geojson"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": "DCS_xz",
                "features": [
                    {
                        "type": "Feature",
                        # (east, north), the order the other sidecars use.
                        "geometry": {"type": "Point", "coordinates": [y, x]},
                        "properties": {"name": name, "kind": kind},
                    }
                    for (x, y), (name, kind) in items
                ],
            }
        )
    )
    _LOGGER.info("places.written", path=str(out_path), count=len(items))
    return out_path


def _place_label(tags: dict) -> str:
    """The name to print on a product, or "" if there is none we can print.

    Prefers `name:en`: the native names here are Abkhaz and Georgian, and the
    briefing font (DejaVu Sans Mono, from matplotlib) has no glyph for several
    Abkhaz letters — `Ԥshaԥ` and `Aӡҩybzha` come out as tofu boxes, which on a
    product reads as a broken render. ASCII-only is also the right register for a
    NATO graphic whose other furniture is `POST 50M` and `SECRET // REL FVEY`.
    45 of 46 settlements around the Kodori delta carry `name:en`.
    """
    for key in ("name:en", "int_name", "name"):
        value = (tags.get(key) or "").strip()
        if value and value.isascii():
            return value
    return ""


def build(terrain: Terrain, theater_slug: str) -> None:
    cfg = _OsmBuild()
    bbox = terrain_bbox_latlon(terrain, theater_slug)
    cache_dir = build_cache_root(theater_slug) / "osm"
    out_dir = overlay_root(theater_slug)
    prog = _ProgressDir(root=cache_dir / "_progress")
    prog.root.mkdir(parents=True, exist_ok=True)

    lat_steps, lon_steps = _tile_steps(bbox)
    total = len(lat_steps) * len(lon_steps)
    _LOGGER.info(
        "osm.bbox",
        theater=theater_slug,
        south=round(bbox.south, 3),
        west=round(bbox.west, 3),
        north=round(bbox.north, 3),
        east=round(bbox.east, 3),
        tile_count=total,
        progress_dir=str(prog.root),
    )

    manifest = _load_or_create_manifest(out_dir, theater_slug, terrain)
    grid = _init_grid(rendered_xz_bounds(theater_slug, terrain), cfg)
    shape = (grid.height, grid.width)

    stage = _read_stage(prog)

    if stage == _STAGE_ABSORB:
        _absorb_all_tiles(
            prog, cache_dir, terrain, grid, cfg, manifest, lat_steps, lon_steps, total
        )
        _write_stage(prog, _STAGE_DT_ROADS)
        stage = _STAGE_DT_ROADS

    if stage == _STAGE_DT_ROADS:
        _dt_and_save_zarr(
            prog,
            prog.roads_bin,
            shape,
            out_dir / "roads_dt.zarr",
            cfg,
            "roads",
        )
        _write_stage(prog, _STAGE_DT_RIVERS)
        stage = _STAGE_DT_RIVERS

    if stage == _STAGE_DT_RIVERS:
        _dt_and_save_zarr(
            prog,
            prog.rivers_bin,
            shape,
            out_dir / "rivers_dt.zarr",
            cfg,
            "rivers",
        )
        _write_stage(prog, _STAGE_BUILDINGS)
        stage = _STAGE_BUILDINGS

    if stage == _STAGE_BUILDINGS:
        _save_buildings_zarr(prog, shape, out_dir / "buildings.zarr", cfg)
        _write_stage(prog, _STAGE_GEOJSON)
        stage = _STAGE_GEOJSON

    if stage == _STAGE_GEOJSON:
        _save_sidecars_from_jsonl(prog, out_dir)
        _write_stage(prog, _STAGE_DONE)

    manifest_path = out_dir / "manifest.json"
    manifest.build_timestamp = datetime.now(UTC).isoformat()
    manifest.write(manifest_path)
    _LOGGER.info("manifest.saved", path=str(manifest_path))

    shutil.rmtree(prog.root, ignore_errors=True)
    _LOGGER.info("osm.build_done", theater=theater_slug)
