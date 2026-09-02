"""Smoke test: every discovered mission builds a readable `.miz`.

Deliberately asserts nothing about mission *content* — only that generation
succeeds and produces a well-formed package. What goes inside a mission is a
design decision that changes often; freezing it in a test would make every
balance tweak look like a regression.

Generation reads the per-theater overlay, a multi-GB build artefact that CI
does not have, so the whole module skips when it is missing.
"""

from __future__ import annotations

import re
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
    miz, readme = cls(players=2).generate(tmp_path)

    assert miz.is_file(), f"{slug}: no .miz written"
    assert miz.name == f"{slug}.miz"
    assert readme.is_file() and readme.read_text().strip(), f"{slug}: empty README"

    with zipfile.ZipFile(miz) as z:
        assert z.testzip() is None, f"{slug}: corrupt .miz"
        names = z.namelist()
        assert "mission" in names, f"{slug}: .miz has no mission entry"
        assert z.read("mission"), f"{slug}: empty mission entry"
        _assert_resources_resolve(slug, z)


def _assert_resources_resolve(slug: str, z: zipfile.ZipFile) -> None:
    """Every registered resource must be a real, non-empty entry in the archive.

    Structure, not content — this asserts nothing about *which* resources a
    mission has. It catches the one pydcs failure mode that is otherwise
    invisible until someone opens the mission: `MapResource.store` flattens
    resources to `l10n/DEFAULT/<basename>` and skips a basename it has already
    written, so two files with the same name leave one resource key pointing at
    the other file's bytes.
    """
    listed = set(z.namelist())
    table = z.read("l10n/DEFAULT/mapResource").decode("utf-8", "replace")
    for name in re.findall(r'=\s*"([^"]+)"', table):
        entry = f"l10n/DEFAULT/{name}"
        assert entry in listed, f"{slug}: mapResource names {name}, absent from .miz"
        assert z.getinfo(entry).file_size > 0, f"{slug}: {name} is empty in the .miz"


def test_generation_is_reproducible(tmp_path: Path) -> None:
    """Building the same mission twice must produce the same package *contents*.

    Placement sampling, pydcs's tail numbers and its take-off waypoint default
    were all sources of run-to-run drift; this pins that they stay fixed. It
    checks that two builds agree, not what they agree on.

    Compared entry by entry rather than as whole files, because the archive is
    not byte-identical and never has been: `Mission.save` writes `mission`,
    `options`, `warehouses` and the two `l10n/DEFAULT` files with
    `zipfile.writestr`, which stamps each local header with the current time. A
    whole-file comparison therefore passed only while both builds landed inside
    the same two-second DOS-timestamp bucket — true for two warm builds at 0.08 s
    each, and false whenever the first build paid a cold cost (opening the
    overlay, rendering a recon still) and pushed the pair across a boundary. That
    made a genuine property look flaky and a flaky assertion look like evidence
    of byte-identity. Contents are the property the project actually holds, and
    they still catch every drift the test was written for.
    """
    slug, cls = _MISSIONS[0]
    first = cls(players=2).generate(tmp_path / "a")[0]
    second = cls(players=2).generate(tmp_path / "b")[0]
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert a.namelist() == b.namelist(), f"{slug}: different entries"
        for name in a.namelist():
            assert a.read(name) == b.read(name), f"{slug}: {name} differs on rebuild"
