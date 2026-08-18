"""Cache, determinism pins, and getting the still into the `.miz`.

A bare `Mission(Syria())` is cheap and needs neither a DCS install nor the map
overlay, so this runs in the default selection — `still_from_scene` exists to make
that possible. Modelled on `tests/test_dtc.py`, the other "extra artefact in the
package" test.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Syria

from dcs_mission_creator.core.recon import publish as recon
from dcs_mission_creator.core.recon.chrome import Chrome
from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.render import Mark
from dcs_mission_creator.core.recon.sample import Scene, empty_scene

_CHROME = Chrome(
    platform="MQ-9 / LYNX II",
    mode="WAS-MTI 5 LOOK",
    taken_at="0540L 12SEP",
    classification="SECRET//REL FVEY",
    footer="11 DET  TRK 314",
    caption="Eleven moving-target returns on the highway.",
)


def _scene() -> Scene:
    return empty_scene(
        Frame(center=Point(0.0, 0.0, Syria()), width_m=3_000.0, height_m=3_000.0)
    )


def _still(m: Mission, tmp_path: Path, **kwargs: object) -> recon.ReconStill:
    opts: dict = {
        "slug": "test_mission",
        "label": "ao",
        "cache_dir": tmp_path / "cache",
    }
    opts.update(kwargs)
    return recon.still_from_scene(m, _scene(), [Mark(x=0.0, y=0.0)], _CHROME, **opts)


def test_still_is_rendered_cached_and_named_after_its_inputs(tmp_path: Path) -> None:
    still = _still(Mission(Syria()), tmp_path)
    assert still.cache_path.is_file() and still.cache_path.stat().st_size > 0
    assert still.filename.startswith("test_mission-ao-")
    assert still.filename.endswith(".png")
    assert still.cache_path.name == still.filename


def test_the_key_changes_with_the_marks_and_with_the_chrome(tmp_path: Path) -> None:
    base = _still(Mission(Syria()), tmp_path).filename
    other_marks = recon.still_from_scene(
        Mission(Syria()),
        _scene(),
        [Mark(x=500.0, y=0.0)],
        _CHROME,
        slug="test_mission",
        label="ao",
        cache_dir=tmp_path / "cache",
    ).filename
    other_chrome = recon.still_from_scene(
        Mission(Syria()),
        _scene(),
        [Mark(x=0.0, y=0.0)],
        Chrome(**{**_CHROME.__dict__, "taken_at": "0600L 12SEP"}),
        slug="test_mission",
        label="ao",
        cache_dir=tmp_path / "cache",
    ).filename
    assert base != other_marks
    assert base != other_chrome


def test_a_warm_cache_is_not_re_rendered(tmp_path: Path, monkeypatch) -> None:
    first = _still(Mission(Syria()), tmp_path)

    def explode(*_a: object, **_k: object) -> None:
        raise AssertionError("re-rendered a still that was already cached")

    monkeypatch.setattr(recon, "render", explode)
    again = _still(Mission(Syria()), tmp_path)
    assert again.cache_path == first.cache_path


def test_an_empty_cache_file_is_treated_as_a_miss(tmp_path: Path) -> None:
    """Guards against a truncated write being served forever."""
    first = _still(Mission(Syria()), tmp_path)
    first.cache_path.write_bytes(b"")
    again = _still(Mission(Syria()), tmp_path)
    assert again.cache_path.stat().st_size > 0


def test_mtime_and_mode_are_pinned(tmp_path: Path) -> None:
    """`zipf.write` records both into the archive, so both decide the `.miz` bytes."""
    still = _still(Mission(Syria()), tmp_path)
    st = still.cache_path.stat()
    assert st.st_mtime == recon._FIXED_MTIME
    assert st.st_mode & 0o777 == recon._FIXED_MODE


def test_the_still_becomes_a_briefing_slide_in_the_miz(tmp_path: Path) -> None:
    m = Mission(Syria())
    still = _still(m, tmp_path)

    assert len(m.pictureFileNameB) == 1
    assert str(still.cache_path) in m.map_resource.files["DEFAULT"].values()

    miz = tmp_path / "out.miz"
    m.save(str(miz))
    with zipfile.ZipFile(miz) as z:
        entry = f"l10n/DEFAULT/{still.filename}"
        assert entry in z.namelist()
        assert z.read(entry) == still.cache_path.read_bytes()


def test_a_colliding_basename_raises_instead_of_being_dropped(tmp_path: Path) -> None:
    """pydcs would silently keep the first file and point both keys at it."""
    m = Mission(Syria())
    still = _still(m, tmp_path)
    impostor = tmp_path / "elsewhere" / still.filename
    impostor.parent.mkdir(parents=True)
    impostor.write_bytes(b"not the same file")
    m.map_resource.add_resource_file(str(impostor))

    with pytest.raises(ValueError, match="already taken"):
        _still(m, tmp_path)


def test_publish_stills_copies_beside_the_package(tmp_path: Path) -> None:
    m = Mission(Syria())
    still = _still(m, tmp_path)
    dest = tmp_path / "out"

    written = recon.publish_stills(m, dest)

    assert written == [dest / still.filename]
    assert written[0].read_bytes() == still.cache_path.read_bytes()
    assert written[0].stat().st_mtime == recon._FIXED_MTIME


def test_publish_stills_on_a_mission_without_any_is_a_no_op(tmp_path: Path) -> None:
    assert recon.publish_stills(Mission(Syria()), tmp_path / "out") == []
    assert not (tmp_path / "out").exists()


def test_markdown_embeds_the_published_filename_and_the_caption(tmp_path: Path) -> None:
    still = _still(Mission(Syria()), tmp_path)
    md = still.markdown()
    assert f"]({still.filename})" in md
    assert _CHROME.caption in md
