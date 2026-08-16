"""Unit tests for `MissionBuilder` base behavior.

No overlay and no DCS install are involved: `FakeBuilder` overrides `build_miz`
so nothing real is assembled, and the tests exercise the base-class plumbing —
player validation, difficulty resolution, and `generate`'s filesystem contract.
`test_build_miz_*` covers the template method itself with a stub assembler.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dcs.mission import Mission

from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.mission_builder import MissionBuilder


class FakeBuilder(MissionBuilder):
    """Bypasses the template method — for tests about `generate` and validation."""

    name = "fake"
    title = "Fake Mission"

    def _assemble(self, m: Mission):  # pragma: no cover - never called
        raise AssertionError("build_miz is overridden; _assemble should not run")

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
    """A subclass that forgets `_assemble` / `readme` must not instantiate."""

    class Incomplete(MissionBuilder):
        name = "incomplete"
        title = "Incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


# ------------------------------------------------------------------ difficulty
def test_difficulty_defaults_to_trained():
    assert FakeBuilder().difficulty is Difficulty.TRAINED


def test_difficulty_accepts_an_enum():
    class Ace(FakeBuilder):
        difficulty = Difficulty.ACE

    assert Ace().difficulty is Difficulty.ACE


def test_every_mission_declares_an_enum_difficulty():
    """The point of typing it on the base: a bare string can no longer sneak in.

    Missions used to set `difficulty = "trained"`, which meant a typo fell
    through `Difficulty.parse` and silently softened both the F10 reveal and
    the enemy ROE. Now the attribute is the enum itself, so a typo is a
    NameError or an AttributeError at import.
    """
    from dcs_mission_creator.__main__ import _discover

    for slug, cls in _discover().items():
        assert isinstance(cls.difficulty, Difficulty), (
            f"{slug} declares {cls.difficulty!r}, expected a Difficulty member"
        )


# -------------------------------------------------------- the template method
class StubAssembler(MissionBuilder):
    """Exercises the real `build_miz`, recording the order the base calls in."""

    name = "stub"
    title = "Stub"

    def __init__(self, **kw) -> None:
        from dcs.terrain import Caucasus

        super().__init__(**kw)
        self._terrain = Caucasus()
        self.calls: list[str] = []

    def _assemble(self, m: Mission):
        self.calls.append("assemble")
        return None  # snap_base_waypoints tolerates a mission with no flights

    def readme(self) -> str:
        return "stub"


def test_build_miz_assembles_and_saves(tmp_path: Path, monkeypatch):
    """The base owns the tail: assemble, snap base waypoints, then save."""
    from dcs_mission_creator.core import mission_builder as mb

    seen: list[str] = []
    monkeypatch.setattr(
        mb.waypoints,
        "snap_base_waypoints",
        lambda m, overlay: seen.append("snap"),
    )
    builder = StubAssembler()
    out = tmp_path / "deep" / "stub.miz"
    builder.build_miz(out)

    assert builder.calls == ["assemble"]
    assert seen == ["snap"], "base waypoints must be snapped for every mission"
    assert out.is_file(), "parent directory should be created"


def test_build_miz_snaps_after_assembling(tmp_path: Path, monkeypatch):
    """Ordering is the whole point: snapping before the last flight exists is a no-op."""
    from dcs_mission_creator.core import mission_builder as mb

    order: list[str] = []
    monkeypatch.setattr(
        mb.waypoints, "snap_base_waypoints", lambda m, overlay: order.append("snap")
    )
    builder = StubAssembler()
    builder._assemble = lambda m: order.append("assemble")  # type: ignore[method-assign]
    builder.build_miz(tmp_path / "s.miz")
    assert order == ["assemble", "snap"]
