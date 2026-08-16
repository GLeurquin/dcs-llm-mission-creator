"""JSON round-trip + invariants for the per-theater overlay manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from dcs_mission_creator.map_overlay.manifest import (
    MANIFEST_VERSION,
    LayerSpec,
    Manifest,
    OsmFilters,
    XZBounds,
)


def _sample_bounds() -> XZBounds:
    return XZBounds(top=100_000.0, bottom=-100_000.0, left=-50_000.0, right=50_000.0)


def test_xzbounds_dimensions():
    b = _sample_bounds()
    assert b.width_m() == 100_000.0
    assert b.height_m() == 200_000.0


def test_default_for_has_six_layers_at_50m():
    m = Manifest.default_for("caucasus", _sample_bounds())
    assert m.version == MANIFEST_VERSION
    assert m.theater == "caucasus"
    assert set(m.layers.as_dict()) == {
        "vegetation",
        "elevation",
        "slope",
        "buildings",
        "roads_dt",
        "rivers_dt",
    }
    for spec in m.layers.as_dict().values():
        assert isinstance(spec, LayerSpec)
        assert spec.cell_size_m == 50


def test_layer_set_rejects_unknown_layer_name():
    payload = Manifest.default_for("caucasus", _sample_bounds()).to_dict()
    payload["layers"]["treeline"] = {"cell_size_m": 50, "dtype": "uint8"}
    with pytest.raises(ValueError, match="unknown overlay layer"):
        Manifest.from_dict(payload)


def test_roundtrip_to_dict_from_dict():
    original = Manifest.default_for("caucasus", _sample_bounds())
    original.build_timestamp = "2026-05-21T10:00:00Z"
    original.git_sha = "deadbeef"
    restored = Manifest.from_dict(original.to_dict())
    assert restored == original


def test_from_dict_rejects_other_version():
    payload = Manifest.default_for("caucasus", _sample_bounds()).to_dict()
    payload["version"] = MANIFEST_VERSION + 1
    with pytest.raises(ValueError, match="manifest version"):
        Manifest.from_dict(payload)


def test_write_read_roundtrip(tmp_path: Path):
    original = Manifest.default_for("caucasus", _sample_bounds())
    path = tmp_path / "subdir" / "manifest.json"
    original.write(path)
    assert path.exists()
    restored = Manifest.read(path)
    assert restored == original


def test_osm_filters_defaults_are_sane():
    f = OsmFilters()
    assert "motorway" in f.road_classes_keep
    assert "river" in f.river_classes_keep
    assert f.river_min_length_m > 0
    assert f.min_water_polygon_m2 > 0
    assert f.settlement_radius_m["city"] > f.settlement_radius_m["hamlet"]


def test_from_dict_preserves_optional_provenance_fields():
    payload = Manifest.default_for("caucasus", _sample_bounds()).to_dict()
    # Older snapshots may omit timestamps; default-empty fields must be tolerated.
    payload.pop("build_timestamp", None)
    payload.pop("git_sha", None)
    restored = Manifest.from_dict(payload)
    assert restored.build_timestamp == ""
    assert restored.git_sha == ""
