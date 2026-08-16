"""Smoke test: every discovered mission builds a readable `.miz`.

Deliberately asserts nothing about mission *content* — only that generation
succeeds and produces a well-formed package. What goes inside a mission is a
design decision that changes often; freezing it in a test would make every
balance tweak look like a regression.

Generation reads the per-theater overlay, a multi-GB build artefact that CI
does not have, so the whole module skips when it is missing.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from dcs_mission_creator.__main__ import _discover
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.map_overlay.query import overlay_root

_THEATERS = ("caucasus", "syria")


def _overlay_available(theater: str) -> bool:
    return (overlay_root(theater) / "manifest.json").is_file()


_MISSIONS = sorted(_discover().items())

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not all(_overlay_available(t) for t in _THEATERS),
        reason="map overlay not built (multi-GB artefact, absent in CI)",
    ),
]


def test_missions_were_discovered() -> None:
    """A silent discovery failure would make every case below vacuous."""
    assert _MISSIONS, "no MissionBuilder subclasses discovered"


@pytest.mark.parametrize(
    ("slug", "cls"), _MISSIONS, ids=[slug for slug, _ in _MISSIONS]
)
def test_mission_generates(
    slug: str, cls: type[MissionBuilder], tmp_path: Path
) -> None:
    miz, readme = cls(players=1).generate(tmp_path)

    assert miz.is_file(), f"{slug}: no .miz written"
    assert miz.name == f"{slug}.miz"
    assert readme.is_file() and readme.read_text().strip(), f"{slug}: empty README"

    with zipfile.ZipFile(miz) as z:
        assert z.testzip() is None, f"{slug}: corrupt .miz"
        names = z.namelist()
        assert "mission" in names, f"{slug}: .miz has no mission entry"
        assert z.read("mission"), f"{slug}: empty mission entry"


def test_generation_is_reproducible(tmp_path: Path) -> None:
    """Building the same mission twice must produce the same bytes.

    Placement sampling, pydcs's tail numbers and its take-off waypoint default
    were all sources of run-to-run drift; this pins that they stay fixed. It
    checks that two builds agree, not what they agree on.
    """
    slug, cls = _MISSIONS[0]
    first = cls(players=1).generate(tmp_path / "a")[0].read_bytes()
    second = cls(players=1).generate(tmp_path / "b")[0].read_bytes()
    assert first == second, f"{slug}: regenerating produced a different .miz"
