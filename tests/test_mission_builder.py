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


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_players_in_range_ok(n: int):
    assert FakeBuilder(players=n).players == n


@pytest.mark.parametrize("n", [0, -1, 7, 100])
def test_players_out_of_range_raises(n: int):
    with pytest.raises(ValueError, match="players must be 1..6"):
        FakeBuilder(players=n)


def test_slot_summary_is_plain_below_the_group_limit():
    assert FakeBuilder(players=4).slot_summary("Dodge") == "4 coop slot(s)"


@pytest.mark.parametrize(
    "players, expected",
    [
        (5, "5 coop slot(s), flown as 2 sections: Dodge (3), Dodge 2 (2)"),
        (6, "6 coop slot(s), flown as 2 sections: Dodge (4), Dodge 2 (2)"),
    ],
)
def test_slot_summary_names_the_sections(players: int, expected: str):
    """Past four slots the briefing has to name the second group the ME shows."""
    assert FakeBuilder(players=players).slot_summary("Dodge") == expected


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


def test_build_miz_permits_crash_recovery(tmp_path: Path):
    """A crash must return to the slot list, not to the debriefing.

    Checked on the saved file rather than on the builder: the whole point is
    that the option reaches the `.miz`, where DCS reads it as a mission-level
    override of the player's own gameplay settings.
    """
    import zipfile

    out = tmp_path / "stub.miz"
    StubAssembler().build_miz(out)
    with zipfile.ZipFile(out) as z:
        mission = z.read("mission").decode("utf-8", "replace")
    assert '["permitCrash"]=true' in mission


def test_permit_crash_recovery_forces_nothing_else():
    """The rest of the gameplay options stay the player's."""
    from dcs.terrain import Caucasus

    m = Mission(Caucasus())
    MissionBuilder._permit_crash_recovery(m)
    assert m.forced_options.dict() == {"permitCrash": True}


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


def test_departure_speed_replaces_pydcs_108kt():
    """`add_runway_waypoint` commands 108 kt — below a loaded jet's stall speed.

    pydcs hard-codes `speed = 200 / 3.6` on the departure waypoint and offers
    no parameter, so the AI is ordered to hold 300 m AGL slower than it can
    fly: it pitches to max alpha and firewalls the throttle off the runway.
    The flight's own first en-route speed is what belongs there.
    """
    from dcs import planes, task
    from dcs.mission import Mission, StartType
    from dcs.terrain import Caucasus

    from dcs_mission_creator.core import waypoints

    m = Mission(Caucasus())
    batumi = m.terrain.airports["Batumi"]
    batumi.set_blue()
    flight = m.flight_group_from_airport(
        m.country("USA"),
        "Pontiac",
        planes.FA_18C_hornet,
        batumi,
        maintask=task.CAS,
        start_type=StartType.Warm,
    )
    departure = flight.add_runway_waypoint(batumi)
    flight.add_waypoint(batumi.position.point_from_heading(90, 60_000), 6400, 700)
    flight.land_at(batumi)

    assert round(departure.speed * 3.6) == 200, "pydcs default changed"
    waypoints.set_departure_speeds(m)
    assert round(departure.speed * 3.6) == 700, "departure keeps the climb-out speed"


def test_departure_speed_only_raises_and_is_idempotent():
    """A mission that set its own departure speed keeps it; re-running is a no-op."""
    from dcs import planes, task
    from dcs.mission import Mission, StartType
    from dcs.terrain import Caucasus

    from dcs_mission_creator.core import waypoints

    m = Mission(Caucasus())
    batumi = m.terrain.airports["Batumi"]
    batumi.set_blue()
    flight = m.flight_group_from_airport(
        m.country("USA"),
        "Hawg",
        planes.A_10C,
        batumi,
        maintask=task.CAS,
        start_type=StartType.Warm,
    )
    departure = flight.add_runway_waypoint(batumi)
    departure.speed = 600 / 3.6  # mission knows better than the next leg
    flight.add_waypoint(batumi.position.point_from_heading(90, 60_000), 4600, 520)
    flight.land_at(batumi)

    waypoints.set_departure_speeds(m)
    assert round(departure.speed * 3.6) == 600, "an explicit departure speed survives"
    waypoints.set_departure_speeds(m)
    assert round(departure.speed * 3.6) == 600, "second run changes nothing"


def test_approach_runway_waypoint_is_left_alone():
    """Only the departure point is touched — the approach one is a different regime."""
    from dcs import planes, task
    from dcs.mission import Mission, StartType
    from dcs.terrain import Caucasus

    from dcs_mission_creator.core import waypoints

    m = Mission(Caucasus())
    batumi = m.terrain.airports["Batumi"]
    batumi.set_blue()
    flight = m.flight_group_from_airport(
        m.country("USA"),
        "Dodge",
        planes.F_16C_50,
        batumi,
        maintask=task.CAP,
        start_type=StartType.Warm,
    )
    flight.add_runway_waypoint(batumi)
    flight.add_waypoint(batumi.position.point_from_heading(90, 60_000), 7000, 800)
    approach = flight.add_runway_waypoint(batumi)
    flight.land_at(batumi)

    waypoints.set_departure_speeds(m)
    assert round(approach.speed * 3.6) == 200, "approach speed is not this helper's job"
