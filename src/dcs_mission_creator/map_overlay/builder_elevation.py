"""SRTM-1 elevation → DCS xz zarr + Horn-method slope. Phase 1.

Pipeline:
    1. Compute lat/lon bbox from terrain (via pydcs projection).
    2. Download SRTM-1 .hgt.gz tiles from AWS Open Data (skadi). Cache locally.
    3. Convert each .hgt to an in-memory `MemoryFile` so rasterio can mosaic.
    4. Reproject WGS84 mosaic → DCS xz CRS, resampled to 50 m.
    5. Save elevation as int16 zarr; derive slope (Horn) as uint8 zarr.

The destination CRS is the same TransverseMercator pydcs uses, but rebuilt
with the default `+axis=enu` (east-north-up) so rasterio's image conventions
line up — image col → easting (= DCS z), image row → −northing (= −DCS x).
Cell (row, col) maps to DCS world (x = top − row·cell, z = left + col·cell).
"""

from __future__ import annotations

import gzip
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
import structlog
from dcs.terrain.terrain import Terrain
from pyproj import CRS
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from dcs_mission_creator.map_overlay._zarr_io import save_zarr
from dcs_mission_creator.map_overlay.coords import (
    rendered_xz_bounds,
    terrain_bbox_latlon,
)
from dcs_mission_creator.map_overlay.manifest import Manifest
from dcs_mission_creator.map_overlay.query import build_cache_root, overlay_root

_LOGGER = structlog.get_logger(__name__)

_SRTM_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
_HGT_DIM = 3601  # SRTM-1 tiles are 3601×3601 samples (1°×1° at 1″)


@dataclass
class _ElevationBuild:
    cell_size_m: int = 50
    chunk: int = 512


def _tile_name(lat: int, lon: int) -> str:
    lat_part = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    lon_part = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    return f"{lat_part}{lon_part}"


def _download_tile(lat: int, lon: int, cache_dir: Path) -> Path | None:
    """Fetch one SRTM-1 .hgt tile to `cache_dir`. None for ocean (404)."""
    name = _tile_name(lat, lon)
    local = cache_dir / f"{name}.hgt"
    if local.exists():
        return local
    lat_prefix = name[:3]
    url = f"{_SRTM_BASE}/{lat_prefix}/{name}.hgt.gz"
    resp = requests.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    local.write_bytes(gzip.decompress(resp.content))
    return local


def _open_hgt_as_dataset(hgt_path: Path, lat: int, lon: int) -> MemoryFile:
    """Wrap a raw .hgt file in a georeferenced rasterio MemoryFile."""
    data = np.frombuffer(hgt_path.read_bytes(), dtype=">i2").reshape(_HGT_DIM, _HGT_DIM)
    # SRTM voids are -32768; treat as sea level so reprojection doesn't smear.
    data = np.where(data == -32768, 0, data).astype(np.int16)
    transform = from_origin(lon, lat + 1, 1.0 / 3600, 1.0 / 3600)
    mem = MemoryFile()
    with mem.open(
        driver="GTiff",
        height=_HGT_DIM,
        width=_HGT_DIM,
        count=1,
        dtype="int16",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data, 1)
    return mem


def _dcs_crs_enu(terrain: Terrain) -> CRS:
    """Same TransverseMercator pydcs uses, but with standard east-north-up axes.

    pydcs declares the CRS with `+axis=neu` so the pyproj transformer matches
    pydcs's (x=northing, z=easting) Point semantics. For rasterio we want the
    default image convention where col → easting, row → −northing, so we
    rebuild the CRS without the `+axis` flag.
    """
    p = terrain.projection_parameters
    return CRS.from_proj4(
        " ".join(
            [
                "+proj=tmerc",
                "+lat_0=0",
                f"+lon_0={p.central_meridian}",
                f"+k_0={p.scale_factor}",
                f"+x_0={p.false_easting}",
                f"+y_0={p.false_northing}",
                "+towgs84=0,0,0,0,0,0,0",
                "+units=m",
                "+vunits=m",
                "+ellps=WGS84",
                "+no_defs",
            ]
        )
    )


def _compute_slope_deg(elev: np.ndarray, cell_m: float) -> np.ndarray:
    """Horn's method slope in degrees, as uint8 [0..90].

    Computed in row-stripes with a 1-row halo so peak RAM stays bounded —
    ``np.gradient`` on a full Caucasus int16 (1.3 GB) produces two float32
    arrays (5 GB) plus the float32 cast (2.6 GB), enough to OOM on a 16 GB box.
    Each stripe uses ~stripe_rows × width × 12 B working RAM.
    """
    out = np.zeros_like(elev, dtype=np.uint8)
    stripe = 512
    h = elev.shape[0]
    for r0 in range(0, h, stripe):
        r1 = min(r0 + stripe, h)
        # 1-row halo on each side so the centred-difference gradient at the
        # stripe boundary uses the correct neighbour.
        halo_lo = max(0, r0 - 1)
        halo_hi = min(h, r1 + 1)
        chunk = elev[halo_lo:halo_hi, :].astype(np.float32)
        dz_dy, dz_dx = np.gradient(chunk, cell_m)
        slope_deg = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
        # Trim the halo we asked for back to the stripe window.
        lo_offset = r0 - halo_lo
        hi_offset = lo_offset + (r1 - r0)
        out[r0:r1, :] = np.clip(slope_deg[lo_offset:hi_offset, :], 0, 90).astype(
            np.uint8
        )
    return out


def _fetch_srtm_tiles(
    bbox_min_lat: int,
    bbox_max_lat: int,
    bbox_min_lon: int,
    bbox_max_lon: int,
    cache_dir: Path,
) -> list[tuple[int, int, Path]]:
    """Download all SRTM-1 tiles covering the lat/lon bbox, skipping ocean 404s."""
    tile_count = (bbox_max_lat - bbox_min_lat) * (bbox_max_lon - bbox_min_lon)
    fetched: list[tuple[int, int, Path]] = []
    for i, lat in enumerate(range(bbox_min_lat, bbox_max_lat)):
        for lon in range(bbox_min_lon, bbox_max_lon):
            hgt = _download_tile(lat, lon, cache_dir)
            if hgt is not None:
                fetched.append((lat, lon, hgt))
        done = (i + 1) * (bbox_max_lon - bbox_min_lon)
        _LOGGER.info("elevation.fetch_progress", done=done, total=tile_count)
    _LOGGER.info("elevation.fetch_done", land_tiles=len(fetched))
    return fetched


def _reproject_into_dst(
    fetched: list[tuple[int, int, Path]],
    dst: np.ndarray,
    dst_transform,
    dst_crs: CRS,
) -> None:
    """Reproject each SRTM tile in-place into `dst` (DCS-xz raster)."""
    for i, (lat, lon, hgt) in enumerate(fetched, 1):
        mem = _open_hgt_as_dataset(hgt, lat, lon)
        try:
            with mem.open() as src:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.average,
                    num_threads=0,
                    # Without this, each call zeroes destination pixels outside
                    # the source's coverage, wiping prior tiles' contributions.
                    init_dest_nodata=False,
                )
        finally:
            mem.close()
        if i % 10 == 0 or i == len(fetched):
            _LOGGER.info("elevation.reproject_progress", done=i, total=len(fetched))


def _load_or_build_dst(
    fetched: list[tuple[int, int, Path]],
    terrain: Terrain,
    theater_slug: str,
    cfg: _ElevationBuild,
    cache_dir: Path,
) -> np.ndarray:
    """Return the assembled DCS-xz int16 elevation grid, using the .npy cache when possible.

    Re-runs (e.g. for zarr write tweaks) hit the cache and skip the multi-minute
    per-tile reproject loop. Cache is keyed on shape + cell size so a config
    change forces a rebuild.
    """
    r = rendered_xz_bounds(theater_slug, terrain)
    width = int(round((r.right - r.left) / cfg.cell_size_m))
    height = int(round((r.top - r.bottom) / cfg.cell_size_m))

    dst_cache = cache_dir / f"dst_{cfg.cell_size_m}m_{height}x{width}.npy"
    if dst_cache.exists():
        dst = np.load(dst_cache)
        if dst.shape == (height, width) and dst.dtype == np.int16:
            _LOGGER.info("elevation.dst_cache_hit", path=str(dst_cache))
            return dst
        _LOGGER.warning(
            "elevation.dst_cache_mismatch",
            got_shape=tuple(dst.shape),
            want_shape=(height, width),
            got_dtype=str(dst.dtype),
        )

    dst = np.zeros((height, width), dtype=np.int16)
    _LOGGER.info(
        "elevation.dst_init", height=height, width=width, cell_m=cfg.cell_size_m
    )
    dst_transform = from_origin(r.left, r.top, cfg.cell_size_m, cfg.cell_size_m)
    dst_crs = _dcs_crs_enu(terrain)
    _reproject_into_dst(fetched, dst, dst_transform, dst_crs)

    np.save(dst_cache, dst)
    _LOGGER.info("elevation.dst_cache_saved", path=str(dst_cache))
    return dst


def _save_elevation_and_slope(
    dst: np.ndarray, cfg: _ElevationBuild, out_dir: Path
) -> None:
    """Write elevation.zarr + derived slope.zarr to `out_dir`."""
    elev_path = out_dir / "elevation.zarr"
    save_zarr(elev_path, dst, cfg.chunk, cfg.cell_size_m, "int16")
    _LOGGER.info("elevation.saved", path=str(elev_path))

    slope = _compute_slope_deg(dst, cfg.cell_size_m)
    slope_path = out_dir / "slope.zarr"
    save_zarr(slope_path, slope, cfg.chunk, cfg.cell_size_m, "uint8")
    _LOGGER.info("slope.saved", path=str(slope_path))


def _refresh_manifest(theater_slug: str, terrain: Terrain, out_dir: Path) -> None:
    """Read existing manifest or create default; stamp timestamp and rewrite."""
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
    """Assemble Caucasus-style elevation + slope rasters from SRTM-1 tiles."""
    cfg = _ElevationBuild()
    bbox = terrain_bbox_latlon(terrain, theater_slug)
    cache_dir = build_cache_root(theater_slug) / "srtm"
    out_dir = overlay_root(theater_slug)

    lat_min = math.floor(bbox.south)
    lat_max = math.ceil(bbox.north)
    lon_min = math.floor(bbox.west)
    lon_max = math.ceil(bbox.east)
    _LOGGER.info(
        "elevation.bbox",
        theater=theater_slug,
        south=round(bbox.south, 3),
        west=round(bbox.west, 3),
        north=round(bbox.north, 3),
        east=round(bbox.east, 3),
        tile_count=(lat_max - lat_min) * (lon_max - lon_min),
    )

    fetched = _fetch_srtm_tiles(lat_min, lat_max, lon_min, lon_max, cache_dir)
    dst = _load_or_build_dst(fetched, terrain, theater_slug, cfg, cache_dir)
    _save_elevation_and_slope(dst, cfg, out_dir)
    _refresh_manifest(theater_slug, terrain, out_dir)
