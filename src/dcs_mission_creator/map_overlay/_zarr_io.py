"""Shared zarr write helper for the build pipeline.

Writes a 2D numpy array to a zarr v3 directory store, in 512-row stripes so
peak RAM stays bounded even for ~1.3 GB layers (full Caucasus at 50 m).

Verifies chunk files actually appear on disk — earlier prototypes with
`z[:, :] = data` on huge arrays produced a zarr.json + zero chunk files when
something silently failed (OOM, killed process), and we want a loud
exception rather than a half-written overlay.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import structlog
import zarr
import zarr.storage
from zarr.codecs import BloscCodec

_LOGGER = structlog.get_logger(__name__)

_STRIPE_ROWS = 512  # write 512 rows at a time → ~bounded peak RAM during compress


def save_zarr(
    out_path: Path,
    data: np.ndarray,
    chunk: int,
    cell_size_m: int,
    dtype: str,
) -> None:
    """Write `data` to a fresh zarr v3 directory at `out_path`.

    Stripes the write into ~512-row bands so the chunk-compress pipeline keeps
    a single stripe's working set in RAM (≈ stripe_rows × width × dtype) rather
    than the full array plus its compressed mirror.
    """
    if out_path.exists():
        shutil.rmtree(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunk_h = min(chunk, data.shape[0])
    chunk_w = min(chunk, data.shape[1])

    store = zarr.storage.LocalStore(str(out_path))
    z = zarr.create_array(
        store=store,
        shape=data.shape,
        chunks=(chunk_h, chunk_w),
        dtype=dtype,
        compressors=BloscCodec(cname="zstd", clevel=5, shuffle="bitshuffle"),
        attributes={"cell_size_m": cell_size_m},
    )

    stripe = max(_STRIPE_ROWS, chunk_h)
    total_rows = data.shape[0]
    for r0 in range(0, total_rows, stripe):
        r1 = min(r0 + stripe, total_rows)
        z[r0:r1, :] = data[r0:r1, :]

    # Verify at least one chunk file landed on disk. Catches silent
    # writer failures we hit during early prototyping.
    chunk_files = list(out_path.rglob("c/*/*"))
    if not chunk_files:
        raise RuntimeError(
            f"zarr write at {out_path} produced no chunk files — "
            "the array on disk would read back as fill_value. "
            "Likely cause: OOM during compress, or a zarr v3 backend bug."
        )
    _LOGGER.info(
        "zarr.saved",
        path=str(out_path),
        shape=tuple(data.shape),
        dtype=dtype,
        chunk_files=len(chunk_files),
    )
