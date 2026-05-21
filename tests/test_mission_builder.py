"""Unit tests for `MissionBuilder` base behavior.

No real `.miz` is written — a `FakeBuilder` stubs both abstract methods so we
only exercise the base-class plumbing (player validation + `generate`'s
filesystem contract).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dcs_mission_creator.core.mission_builder import MissionBuilder


class FakeBuilder(MissionBuilder):
    name = "fake"
    title = "Fake Mission"

    def build_miz(self, miz_path: Path) -> None:
        miz_path.write_bytes(b"PK\x03\x04fake")  # fake zip prefix

    def readme(self) -> str:
        return f"# {self.title}\nplayers={self.players}\n"


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_players_in_range_ok(n: int):
    assert FakeBuilder(players=n).players == n


@pytest.mark.parametrize("n", [0, -1, 5, 100])
def test_players_out_of_range_raises(n: int):
    with pytest.raises(ValueError, match="players must be 1..4"):
        FakeBuilder(players=n)


def test_generate_creates_dir_and_writes_files(tmp_path: Path):
    out = tmp_path / "new" / "subdir"
    miz, readme = FakeBuilder().generate(out)
    assert miz == out / "fake.miz"
    assert readme == out / "README.md"
    assert miz.exists() and miz.stat().st_size > 0
    assert readme.read_text() == "# Fake Mission\nplayers=1\n"


def test_generate_uses_name_for_miz_filename(tmp_path: Path):
    class Alt(FakeBuilder):
        name = "alt_slug"

    miz, _ = Alt().generate(tmp_path)
    assert miz.name == "alt_slug.miz"


def test_generate_propagates_player_count(tmp_path: Path):
    _, readme = FakeBuilder(players=3).generate(tmp_path)
    assert "players=3" in readme.read_text()


def test_abstract_methods_required():
    """Instantiating a subclass that forgets build_miz/readme must fail."""

    class Incomplete(MissionBuilder):
        name = "incomplete"
        title = "Incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
