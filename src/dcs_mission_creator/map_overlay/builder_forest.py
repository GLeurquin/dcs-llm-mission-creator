"""ESA WorldCover 2021 v200 → DCS xz vegetation zarr.

Crash-resume pipeline. Per-tile reproject state is checkpointed to disk
before the next tile starts, so a crash anywhere past tile 1 resumes from
the last completed tile rather than redoing every reproject.

Progress layout under `_build_cache/<theater>/worldcover/_progress/`:

    vegetation.bin      uint8 memmap dst raster (height × width)
    processed.jsonl     one absorbed source-tile filename per line
    stage.txt           "reproject" | "save_zarr" | "done"

Pipeline:
    1. Compute lat/lon bbox from terrain.
    2. Determine WorldCover tiles overlapping the bbox.
       Tiles are 3°×3° at 10 m, hosted free on AWS Open Data (no auth).
    3. Download each tile to the build cache.
    4. Open / reopen the memmap dst raster (663 MB uint8 for Caucasus 50 m).
    5. Per tile: remap WorldCover classes → 4-class vegetation
         0 = none, 1 = light_forest, 2 = dense_forest, 3 = water
       then reproject + mode-resample into the memmap dst raster, then
       mark the tile processed.
    6. After every tile is absorbed, stream the memmap to a uint8
       `vegetation.zarr` (chunked + blosc-zstd compressed).
    7. On clean success the `_progress/` directory is removed.
"""

from __future__ import annotations

import gc
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
import structlog
from dcs.terrain.terrain import Terrain
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from dcs_mission_creator.map_overlay._zarr_io import save_zarr
from dcs_mission_creator.map_overlay.builder_elevation import _dcs_crs_enu
from dcs_mission_creator.map_overlay.coords import (
    LatLonBBox,
    rendered_xz_bounds,
    terrain_bbox_latlon,
)
from dcs_mission_creator.map_overlay.manifest import Manifest
from dcs_mission_creator.map_overlay.query import build_cache_root, overlay_root

_LOGGER = structlog.get_logger(__name__)

_WC_BASE = "https://esa-worldcover.s3.amazonaws.com/v200/2021/map"

# WorldCover class codes (https://esa-worldcover.org/en/data-access)
# Mapped to our 4-class vegetation:
#   10 (tree cover)        -> 2 dense_forest
#   20 (shrubland)         -> 1 light_forest
#   30 grassland           -> 0
#   40 cropland            -> 0
#   50 built-up            -> 0 (buildings layer covers urban density)
#   60 bare/sparse veg     -> 0
#   70 snow/ice            -> 0
#   80 permanent water     -> 3 water
#   90 herbaceous wetland  -> 1 light_forest (often shrubby)
#   95 mangroves           -> 2 dense_forest
#  100 moss/lichen         -> 0
_WC_REMAP = np.zeros(256, dtype=np.uint8)
_WC_REMAP[10] = 2
_WC_REMAP[20] = 1
_WC_REMAP[80] = 3
_WC_REMAP[90] = 1
_WC_REMAP[95] = 2

_STAGE_REPROJECT = "reproject"
_STAGE_SAVE_ZARR = "save_zarr"
_STAGE_DONE = "done"


@dataclass
class _ForestBuild:
    cell_size_m: int = 50
    chunk: int = 512


@dataclass
class _DstRaster:
    """Destination raster initialised in DCS-ENU CRS at the configured cell size.

    `data` is a uint8 memmap backed by `_progress/vegetation.bin` so a crash
    mid-reproject doesn't lose the work already absorbed.
    """

    data: np.memmap
    transform: object
    crs: object
    height: int
    width: int


@dataclass
class _ProgressDir:
    """Checkpoint files for one theater's forest build."""

    root: Path

    @property
    def vegetation_bin(self) -> Path:
        return self.root / "vegetation.bin"

    @property
    def processed_jsonl(self) -> Path:
        return self.root / "processed.jsonl"

    @property
    def stage_txt(self) -> Path:
        return self.root / "stage.txt"


def _wc_tile_name(lat_sw: int, lon_sw: int) -> str:
    lat_part = f"N{lat_sw:02d}" if lat_sw >= 0 else f"S{-lat_sw:02d}"
    lon_part = f"E{lon_sw:03d}" if lon_sw >= 0 else f"W{-lon_sw:03d}"
    return f"ESA_WorldCover_10m_2021_v200_{lat_part}{lon_part}_Map.tif"


def _download_wc_tile(lat_sw: int, lon_sw: int, cache_dir: Path) -> Path | None:
    """Fetch one WorldCover 3°×3° tile. Returns None if 404 (tile is all-ocean)."""
    name = _wc_tile_name(lat_sw, lon_sw)
    local = cache_dir / name
    if local.exists() and local.stat().st_size > 0:
        return local
    url = f"{_WC_BASE}/{name}"
    resp = requests.get(url, timeout=300, stream=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(local, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return local


def _tile_grid(bbox: LatLonBBox) -> list[tuple[int, int]]:
    """WorldCover tiles are 3°×3° aligned to multiples of 3 — enumerate SW corners."""
    lat_min = int(math.floor(bbox.south / 3.0) * 3)
    lat_max = int(math.ceil(bbox.north / 3.0) * 3)
    lon_min = int(math.floor(bbox.west / 3.0) * 3)
    lon_max = int(math.ceil(bbox.east / 3.0) * 3)
    return [
        (lat, lon)
        for lat in range(lat_min, lat_max, 3)
        for lon in range(lon_min, lon_max, 3)
    ]


def _download_all_tiles(tiles: list[tuple[int, int]], cache_dir: Path) -> list[Path]:
    """Download every tile sequentially; drop None (all-ocean) entries."""
    paths: list[Path] = []
    for i, (lat, lon) in enumerate(tiles, 1):
        local = _download_wc_tile(lat, lon, cache_dir)
        if local is not None:
            paths.append(local)
        _LOGGER.info("forest.fetch_progress", done=i, total=len(tiles))
    _LOGGER.info("forest.fetch_done", land_tiles=len(paths))
    return paths


def _read_stage(prog: _ProgressDir) -> str:
    if not prog.stage_txt.exists():
        return _STAGE_REPROJECT
    return prog.stage_txt.read_text().strip() or _STAGE_REPROJECT


def _write_stage(prog: _ProgressDir, stage: str) -> None:
    prog.stage_txt.write_text(stage)


def _load_processed(prog: _ProgressDir) -> set[str]:
    if not prog.processed_jsonl.exists():
        return set()
    out: set[str] = set()
    with prog.processed_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.add(line)
    return out


def _mark_processed(prog: _ProgressDir, tile_filename: str) -> None:
    with prog.processed_jsonl.open("a") as f:
        f.write(tile_filename + "\n")


def _open_or_init_dst(
    prog: _ProgressDir, terrain: Terrain, theater_slug: str, cfg: _ForestBuild
) -> _DstRaster:
    """Open the memmap dst raster (resume) or create it (fresh start)."""
    r = rendered_xz_bounds(theater_slug, terrain)
    width = int(round((r.right - r.left) / cfg.cell_size_m))
    height = int(round((r.top - r.bottom) / cfg.cell_size_m))
    transform = from_origin(r.left, r.top, cfg.cell_size_m, cfg.cell_size_m)
    crs = _dcs_crs_enu(terrain)
    mode = "r+" if prog.vegetation_bin.exists() else "w+"
    data = np.memmap(
        prog.vegetation_bin, dtype=np.uint8, mode=mode, shape=(height, width)
    )
    _LOGGER.info(
        "forest.dst_init",
        height=height,
        width=width,
        cell_m=cfg.cell_size_m,
        resumed=(mode == "r+"),
    )
    return _DstRaster(
        data=data, transform=transform, crs=crs, height=height, width=width
    )


def _reproject_tile_into(path: Path, dst: _DstRaster) -> None:
    """Remap one WorldCover tile to the 4-class scheme and reproject into `dst`."""
    with rasterio.open(path) as src:
        raw = src.read(1)
        remapped = _WC_REMAP[raw]
        mem = MemoryFile()
        try:
            with mem.open(
                driver="GTiff",
                height=src.height,
                width=src.width,
                count=1,
                dtype="uint8",
                crs=src.crs,
                transform=src.transform,
            ) as ds:
                ds.write(remapped, 1)
            with mem.open() as tile_src:
                reproject(
                    source=rasterio.band(tile_src, 1),
                    destination=dst.data,
                    src_transform=tile_src.transform,
                    src_crs=tile_src.crs,
                    dst_transform=dst.transform,
                    dst_crs=dst.crs,
                    resampling=Resampling.mode,
                    num_threads=0,
                    # Without this, each call zeroes destination pixels outside
                    # the source's coverage, wiping prior tiles' contributions.
                    init_dest_nodata=False,
                )
        finally:
            mem.close()


def _reproject_all_resumable(
    paths: list[Path], dst: _DstRaster, prog: _ProgressDir
) -> None:
    """Per-tile reproject into memmap dst, skipping already-processed tiles."""
    processed = _load_processed(prog)
    if processed:
        _LOGGER.info("forest.resume", processed_tiles=len(processed))
    for i, p in enumerate(paths, 1):
        if p.name in processed:
            _LOGGER.info("forest.tile_skipped", done=i, total=len(paths))
            continue
        _reproject_tile_into(p, dst)
        # Flush so the on-disk file reflects the absorption before we mark it done.
        dst.data.flush()
        _mark_processed(prog, p.name)
        if i % 5 == 0 or i == len(paths):
            _LOGGER.info("forest.reproject_progress", done=i, total=len(paths))
    gc.collect()


def _save_vegetation_from_memmap(
    prog: _ProgressDir,
    shape: tuple[int, int],
    out_dir: Path,
    cfg: _ForestBuild,
) -> None:
    """Stream the memmap dst raster to vegetation.zarr without loading it fully."""
    mm = np.memmap(prog.vegetation_bin, dtype=np.uint8, mode="r", shape=shape)
    veg_path = out_dir / "vegetation.zarr"
    save_zarr(veg_path, mm, cfg.chunk, cfg.cell_size_m, "uint8")
    del mm
    gc.collect()
    _LOGGER.info("vegetation.saved", path=str(veg_path))


def _refresh_manifest(out_dir: Path, theater_slug: str, terrain: Terrain) -> None:
    """Bump build timestamp on existing manifest, or create a default one."""
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest = Manifest.read(manifest_path)
    else:
        manifest = Manifest.default_for(
            theater_slug,
            rendered_xz_bounds(theater_slug, terrain),
        )
    manifest.build_timestamp = datetime.now(UTC).isoformat()
    manifest.write(manifest_path)
    _LOGGER.info("manifest.saved", path=str(manifest_path))


def build(terrain: Terrain, theater_slug: str) -> None:
    cfg = _ForestBuild()
    bbox = terrain_bbox_latlon(terrain, theater_slug)
    cache_dir = build_cache_root(theater_slug) / "worldcover"
    out_dir = overlay_root(theater_slug)
    prog = _ProgressDir(root=cache_dir / "_progress")
    prog.root.mkdir(parents=True, exist_ok=True)

    tiles = _tile_grid(bbox)
    _LOGGER.info(
        "forest.bbox",
        theater=theater_slug,
        south=round(bbox.south, 3),
        west=round(bbox.west, 3),
        north=round(bbox.north, 3),
        east=round(bbox.east, 3),
        tile_count=len(tiles),
        progress_dir=str(prog.root),
    )

    paths = _download_all_tiles(tiles, cache_dir)
    dst = _open_or_init_dst(prog, terrain, theater_slug, cfg)
    shape = (dst.height, dst.width)
    stage = _read_stage(prog)

    if stage == _STAGE_REPROJECT:
        _reproject_all_resumable(paths, dst, prog)
        # Drop the memmap reference before save_zarr opens it read-only to avoid
        # holding two memmaps to the same file.
        dst.data.flush()
        del dst
        gc.collect()
        _write_stage(prog, _STAGE_SAVE_ZARR)
        stage = _STAGE_SAVE_ZARR

    if stage == _STAGE_SAVE_ZARR:
        _save_vegetation_from_memmap(prog, shape, out_dir, cfg)
        _write_stage(prog, _STAGE_DONE)

    _refresh_manifest(out_dir, theater_slug, terrain)

    shutil.rmtree(prog.root, ignore_errors=True)
    _LOGGER.info("forest.build_done", theater=theater_slug)
