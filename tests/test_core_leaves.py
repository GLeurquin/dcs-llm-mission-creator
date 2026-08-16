"""Tests for the small pure helpers in `core/` — no Mission, no overlay, no DCS.

These are the bits that fail *silently* when they break: a Lua placeholder that
survives into a mission reaches DCS as a syntax error at start-up, a mangled
WSL path leaves every flight unarmed, and a mistyped difficulty label quietly
downgrades both the map reveal and the AI ROE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dcs_mission_creator.core import lua
from dcs_mission_creator.core.dcs_install import _local_path, _variant_suffix
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.emcon import _lua_str


# ------------------------------------------------------------------- lua.source
def test_source_rejects_a_name_that_is_not_lua() -> None:
    with pytest.raises(ValueError, match="must end in .lua"):
        lua.source("emcon.txt")


def test_source_reads_a_real_script() -> None:
    assert "function" in lua.source("emcon.lua")


# ------------------------------------------------------------------- lua.render
def test_render_substitutes_every_placeholder() -> None:
    out = lua.render("emcon.lua", SITES="{}", SIDE="coalition.side.BLUE", SPACING="7.0")
    assert "__SITES__" not in out
    assert "coalition.side.BLUE" in out


def test_render_rejects_an_unknown_placeholder() -> None:
    """A typo'd key would otherwise be dropped on the floor."""
    with pytest.raises(KeyError, match="no placeholder __NOPE__"):
        lua.render(
            "emcon.lua",
            SITES="{}",
            SIDE="coalition.side.BLUE",
            SPACING="7.0",
            NOPE="x",
        )


def test_render_rejects_a_leftover_placeholder() -> None:
    """An unsubstituted token reaches DCS as a Lua syntax error."""
    with pytest.raises(KeyError, match="left unsubstituted"):
        lua.render("emcon.lua", SITES="{}")


# --------------------------------------------------------------- emcon._lua_str
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "nil"),
        ("plain", '"plain"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
    ],
)
def test_lua_str_quotes_and_escapes(value: str | None, expected: str) -> None:
    assert _lua_str(value) == expected


def test_lua_str_escapes_backslash_before_quote() -> None:
    """Order matters: escaping quotes first would double-escape the backslash."""
    assert _lua_str('a\\"b') == '"a\\\\\\"b"'


# ------------------------------------------------------------ dcs_install paths
@pytest.mark.skipif(not Path("/mnt").is_dir(), reason="WSL mount layout only")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"E:\Games\DCS World", "/mnt/e/Games/DCS World"),
        ("E:/Games/DCS World", "/mnt/e/Games/DCS World"),
        (r'"E:\Games\DCS World"', "/mnt/e/Games/DCS World"),
        (r"c:\DCS", "/mnt/c/DCS"),
    ],
)
def test_local_path_maps_windows_drives_onto_wsl(raw: str, expected: str) -> None:
    assert _local_path(raw) == Path(expected)


def test_local_path_passes_posix_paths_through() -> None:
    assert _local_path("/opt/dcs") == Path("/opt/dcs")


def test_local_path_strips_surrounding_whitespace_and_quotes() -> None:
    assert _local_path('  "/opt/dcs"  ') == Path("/opt/dcs")


def test_variant_suffix_is_empty_without_the_marker(tmp_path: Path) -> None:
    assert _variant_suffix(tmp_path) == ""


def test_variant_suffix_reads_the_marker(tmp_path: Path) -> None:
    (tmp_path / "dcs_variant.txt").write_text("openbeta")
    assert _variant_suffix(tmp_path) == ".openbeta"


def test_variant_suffix_strips_stray_characters(tmp_path: Path) -> None:
    """The file is written by the installer and often carries a newline."""
    (tmp_path / "dcs_variant.txt").write_text("openbeta\n")
    assert _variant_suffix(tmp_path) == ".openbeta"


# ------------------------------------------------------------ Difficulty.parse
@pytest.mark.parametrize(
    "label", ["recruit", "trained", "veteran", "ace", "ACE", "  Veteran  "]
)
def test_parse_accepts_known_labels(label: str) -> None:
    assert Difficulty.parse(label) is Difficulty(label.strip().lower())


def test_parse_falls_back_to_trained() -> None:
    """A typo currently downgrades silently — pin the behaviour so a change is visible."""
    assert Difficulty.parse("vetran") is Difficulty.TRAINED


def test_parse_is_idempotent_over_values() -> None:
    for member in Difficulty:
        assert Difficulty.parse(member.value) is member
