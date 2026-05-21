"""Round-trip tests for the `save_zarr` helper."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import zarr

from dcs_mission_creator.map_overlay._zarr_io import save_zarr


def test_save_zarr_roundtrip(tmp_path: Path):
    data = np.arange(100, dtype=np.int16).reshape(10, 10)
    out = tmp_path / "fresh" / "layer.zarr"
    save_zarr(out, data, chunk=4, cell_size_m=50, dtype="int16")

    arr = zarr.open_array(str(out), mode="r")
    np.testing.assert_array_equal(arr[:], data)
    assert arr.dtype == np.int16
    assert arr.attrs["cell_size_m"] == 50


def test_save_zarr_creates_chunk_files(tmp_path: Path):
    # Non-fill-value data so zarr actually writes chunks (uniform fill chunks
    # are elided by zarr v3 and would trip save_zarr's safety check).
    data = np.full((10, 10), 9, dtype=np.uint8)
    out = tmp_path / "layer.zarr"
    save_zarr(out, data, chunk=4, cell_size_m=50, dtype="uint8")
    chunks = list(out.rglob("c/*/*"))
    assert chunks, "expected at least one chunk file under c/*/*"


def test_save_zarr_overwrites_existing(tmp_path: Path):
    out = tmp_path / "layer.zarr"
    first = np.full((6, 6), 1, dtype=np.uint8)
    second = np.full((6, 6), 7, dtype=np.uint8)
    save_zarr(out, first, chunk=4, cell_size_m=50, dtype="uint8")
    save_zarr(out, second, chunk=4, cell_size_m=50, dtype="uint8")
    arr = zarr.open_array(str(out), mode="r")
    np.testing.assert_array_equal(arr[:], second)


def test_save_zarr_clamps_chunk_to_shape(tmp_path: Path):
    """Chunk > shape is fine — code clamps it down."""
    data = np.full((3, 3), 5, dtype=np.int16)
    out = tmp_path / "small.zarr"
    save_zarr(out, data, chunk=128, cell_size_m=50, dtype="int16")
    arr = zarr.open_array(str(out), mode="r")
    assert arr.shape == (3, 3)


@pytest.mark.parametrize("dtype", ["uint8", "uint16", "int16"])
def test_save_zarr_dtypes(tmp_path: Path, dtype: str):
    data = np.ones((5, 5), dtype=dtype)
    out = tmp_path / f"{dtype}.zarr"
    save_zarr(out, data, chunk=4, cell_size_m=50, dtype=dtype)
    arr = zarr.open_array(str(out), mode="r")
    assert str(arr.dtype) == dtype
