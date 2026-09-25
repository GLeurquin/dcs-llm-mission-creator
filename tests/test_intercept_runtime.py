"""Run the generated intercept script against a stubbed DCS scripting environment.

Every way `core/intercept.py` can break is a runtime failure in a valid `.miz`:
a track that never registers the escort, one that turns hostile after it has
left, a defender committed on the wrong group, a mission that never resolves.
So the script is loaded into an embedded Lua with `tests/lua/dcs_env.lua`
standing in for DCS and the clock is driven forward. The stub flies a group
straight at the last point of whatever route it was given, which is enough to
carry a compliant track out of the zone.

Needs `lupa` (dev dependency). Skips without it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from dcs import planes, task
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core.intercept import Calls, Track, arm_intercepts

lupa = pytest.importorskip("lupa", reason="needs the lupa embedded Lua interpreter")

_ENV = Path(__file__).parent / "lua" / "dcs_env.lua"

#: North edge at x = 0; the track starts 45 km inside it.
_ZONE = [Point(0, 0, None), Point(0, 50_000, None), Point(-80_000, 50_000, None),
         Point(-80_000, 0, None)]  # fmt: skip
_START_X, _Z = -45_000.0, 25_000.0

_CALLS = Calls(
    intercepted="{label} turning",
    hostile="{label} hostile",
    cleared="{label} cleared",
    fled="{label} fled",
    lapsed="{label} lapsed",
    unchallenged="{label} unchallenged",
    foul="{label} foul",
)


class Sortie:
    """One intruder, one player, Magic and Eagle, and the running script."""

    def __init__(self, **track: Any) -> None:
        m = Mission(Caucasus())
        red = m.flight_group_inflight(
            m.country("Russia"), "Zombie", planes.Su_27, Point(_START_X, _Z, m.terrain),
            altitude=7000, speed=800, maintask=task.CAP, group_size=2,
        )  # fmt: skip
        eagle = m.flight_group_inflight(
            m.country("USA"), "Eagle", planes.F_15C, Point(-60_000, _Z, m.terrain),
            altitude=8000, speed=800, maintask=task.CAP, group_size=2,
        )  # fmt: skip
        magic = m.flight_group_inflight(
            m.country("USA"), "Magic", planes.E_3A, Point(-70_000, _Z, m.terrain),
            altitude=9000, speed=740, maintask=task.AWACS, group_size=1,
        )  # fmt: skip
        home = SimpleNamespace(position=Point(100_000, _Z, None), id=99)
        spec: dict[str, Any] = {
            "group": red,
            "label": "Zombie",
            "orbit": Point(-60_000, _Z, None),
            "exit": Point(20_000, _Z, None),
            "home": cast(Any, home),
            "altitude_m": 7000,
            "speed_kph": 800,
            "hostile_probability": 0.0,
            "calls": _CALLS,
        } | track
        self.flags = arm_intercepts(
            m, [Track(**spec)], zone=_ZONE, defenders=[eagle], controller=magic
        )
        script = m.triggerrules.triggers[-1].actions[0].script
        self.rt = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.rt.execute(_ENV.read_text(encoding="utf-8"))
        self.rt.execute(
            f"""
            TESTGROUP{{name="Zombie", x={_START_X}, z={_Z}, side=coalition.side.RED,
              category=Group.Category.AIRPLANE, units={{
              {{type="Su-27", y=7000}}, {{type="Su-27", y=7000, dz=150}}}}}}
            TESTGROUP{{name="Eagle", x=-60000, z={_Z}, side=coalition.side.BLUE,
              category=Group.Category.AIRPLANE, units={{{{type="F-15C", y=8000}}}}}}
            TESTGROUP{{name="Magic", x=-70000, z={_Z}, side=coalition.side.BLUE,
              category=Group.Category.AIRPLANE, units={{{{type="E-3A", y=9000}}}}}}
            TESTGROUP{{name="Uzi", x=-80000, z=0, side=coalition.side.BLUE,
              category=Group.Category.AIRPLANE,
              units={{{{type="F-16C_50", y=7000, player=true}}}}}}
            """
        )
        self.rt.execute(script)

    def advance(self, to: float) -> None:
        self.rt.eval("TESTADVANCE")(to, 1.0)

    def escort(self, to: float, *, abeam_m: float = 900.0) -> None:
        """Sit off the lead's wing, second by second, until `to`."""
        now = int(self.rt.eval("timer.getTime()"))
        for t in range(now + 1, int(to) + 1):
            self.rt.eval(
                "(function(d) local z = Unit.getByName('Zombie Unit #1') "
                "local u = Unit.getByName('Uzi Unit #1') "
                "u.x = z.x; u.z = z.z + d end)"
            )(abeam_m)
            self.advance(t)

    def leave(self) -> None:
        self.rt.execute("Unit.getByName('Uzi Unit #1').x = -80000")
        self.rt.execute("Unit.getByName('Uzi Unit #1').z = 0")

    def flag(self, n: int) -> int:
        return int(self.rt.eval(f"TESTFLAGS[{n}] or 0"))

    def said(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 5) == 'TEXT ' then t[#t + 1] = l:sub(6) end end "
            "return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def errors(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:find('ERR') then t[#t + 1] = l end end "
            "return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def defender(self) -> tuple[Any, Any]:
        roe = self.rt.eval("Group.getByName('Eagle').roe")
        pushed = self.rt.eval(
            "(function() local p = Group.getByName('Eagle').pushed "
            "return p and p[1].id .. '|' .. tostring(p[1].params.groupId) end)()"
        )
        return roe, pushed


def test_an_escorted_track_is_seen_out_and_the_mission_resolves() -> None:
    s = Sortie()
    s.escort(260)
    assert s.said()[0] == "Zombie turning"
    assert "Zombie cleared" in s.said()
    assert s.flag(s.flags.done) == 1
    assert s.flag(s.flags.foul) == 0
    assert s.errors() == []


def test_a_distant_player_is_not_an_intercept() -> None:
    s = Sortie()
    s.escort(60, abeam_m=5_000.0)
    assert s.said() == []


def test_a_track_that_turns_commits_the_defender_on_it_alone() -> None:
    s = Sortie(hostile_probability=1.0, hostile_after_s=(10.0, 10.0))
    s.escort(60)
    assert "Zombie hostile" in s.said()
    roe, pushed = s.defender()
    assert roe == 2  # OPEN_FIRE: only what it is tasked against
    assert pushed == "AttackGroup|Zombie"
    assert s.errors() == []


def test_no_flip_once_the_track_is_out() -> None:
    s = Sortie(hostile_probability=1.0, hostile_after_s=(400.0, 400.0))
    s.escort(500)
    assert "Zombie cleared" in s.said()
    assert "Zombie hostile" not in s.said()


def test_an_unwatched_track_turns_back_and_a_flip_is_cancelled() -> None:
    s = Sortie(hostile_probability=1.0, hostile_after_s=(150.0, 150.0))
    s.escort(30)
    s.leave()
    s.advance(200)
    assert "Zombie lapsed" in s.said()
    assert "Zombie hostile" not in s.said()


def test_a_track_nobody_meets_leaves_unchallenged() -> None:
    s = Sortie(deadline_s=30.0)
    s.advance(400)
    assert "Zombie unchallenged" in s.said()
    assert s.flag(s.flags.unchallenged) == 1
    assert s.flag(s.flags.done) == 1


def test_a_hit_on_a_compliant_track_is_the_foul() -> None:
    s = Sortie()
    s.advance(5)
    s.rt.execute(
        "TESTFIRE({id = world.event.S_EVENT_HIT, "
        "target = Unit.getByName('Zombie Unit #1'), "
        "initiator = Unit.getByName('Uzi Unit #1')})"
    )
    s.advance(10)
    assert s.flag(s.flags.foul) == 1
    assert "Zombie foul" in s.said()


def test_no_controller_no_calls() -> None:
    s = Sortie()
    s.rt.execute("Unit.getByName('Magic Unit #1').alive = false")
    s.escort(60)
    assert s.said() == []
