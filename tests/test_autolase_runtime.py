"""Run the generated laser spot against a stubbed DCS scripting environment.

`core/laser.py`'s `arm_autolase` exists because the stock AI controller only
lases inside a radio conversation, and the whole point of it is a spot that is
already burning when the flight arrives. Every way that can break is a runtime
failure in a perfectly valid `.miz` — a spot that never comes up, one that comes
up on the wrong vehicle, one that keeps burning through a ridge or after the
team that was holding it is dead — and from the cockpit all four look like a
bomb that failed to guide. So the script is loaded into an embedded Lua with
`tests/lua/dcs_env.lua` standing in for DCS, and the clock is driven forward.

Needs `lupa` (dev dependency). Skips without it, like `test_iads_runtime.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Vehicle
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import Infantry, Unarmed

from dcs_mission_creator.core import laser

lupa = pytest.importorskip("lupa", reason="needs the lupa embedded Lua interpreter")

_ENV = Path(__file__).parent / "lua" / "dcs_env.lua"

_TACP = "Pinpoint"
_COLUMN = "Column"


@dataclass
class Spot:
    """One spot the script asked DCS for, as the stub recorded it."""

    kind: str
    source: str
    code: str
    alive: bool
    x: float
    y: float
    z: float


def _group(gid: int, name: str, *types: Any) -> VehicleGroup:
    terrain = Caucasus()
    vg = VehicleGroup(gid, name)
    for i, unit_type in enumerate(types, start=1):
        vg.add_unit(Vehicle(terrain, gid * 100 + i, f"{name} Unit #{i}", unit_type.id))
    return vg


class Party:
    """A designating team, its target column, and the running script."""

    def __init__(
        self,
        *,
        column_x: float = 3_000.0,
        spot: dict[str, Any] | None = None,
        arm: dict[str, Any] | None = None,
    ) -> None:
        tacp = _group(1, _TACP, Unarmed.Hummer, Infantry.JTAC)
        column = _group(
            2, _COLUMN, Unarmed.Ural_375, Unarmed.Ural_375, Unarmed.Ural_375
        )
        mission = Mission(Caucasus())
        rule = laser.arm_autolase(
            mission,
            [laser.LaserSpot(tacp, column, label="Pinpoint 1-1", **(spot or {}))],
            trace=True,
            **(arm or {}),
        )
        self.rt = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.rt.execute(_ENV.read_text(encoding="utf-8"))
        self.rt.execute(
            f"""
            TESTGROUP{{name="{_TACP}", x=0, z=0, side=coalition.side.BLUE, units={{
              {{type="Hummer"}}, {{type="JTAC"}},
            }}}}
            TESTGROUP{{name="{_COLUMN}", x={column_x:.1f}, z=0, units={{
              {{type="Ural-375"}}, {{type="Ural-375", dx=200}},
              {{type="Ural-375", dx=400}},
            }}}}
            """
        )
        self.rt.execute(rule.actions[0].script)

    # ------------------------------------------------------------- the world
    def advance(self, to: float, step: float = 1.0) -> None:
        self.rt.eval("TESTADVANCE")(to, step)

    def move(self, unit: str, x: float) -> None:
        self.rt.eval("(function(n, x) Unit.getByName(n).x = x end)")(unit, x)

    def drive(self, unit: str, vx: float) -> None:
        self.rt.eval("(function(n, v) Unit.getByName(n).vx = v end)")(unit, vx)

    def kill(self, *units: str) -> None:
        for unit in units:
            self.rt.eval("(function(n) Unit.getByName(n).alive = false end)")(unit)

    def mask(self, blocked: bool) -> None:
        self.rt.execute(
            "TESTMASK = function() return false end" if blocked else "TESTMASK = nil"
        )

    def position(self, unit: str) -> tuple[float, float]:
        raw = self.rt.eval(
            "(function(n) local u = Unit.getByName(n) "
            "return string.format('%.1f|%.1f', u.x, u.z) end)"
        )(unit)
        x, z = raw.split("|")
        return float(x), float(z)

    # ------------------------------------------------------------ the spots
    def spots(self) -> list[Spot]:
        raw = self.rt.eval(
            """(function()
              local out = {}
              for _, s in ipairs(TESTSPOTS) do
                out[#out + 1] = string.format("%s|%s|%s|%s|%.2f|%.2f|%.2f",
                  s.kind, s.source, tostring(s.code), tostring(s.alive),
                  s.point.x, s.point.y, s.point.z)
              end
              return table.concat(out, "\\n")
            end)"""
        )()
        found = []
        for line in raw.splitlines():
            if not line:
                continue
            kind, source, code, alive, x, y, z = line.split("|")
            found.append(
                Spot(kind, source, code, alive == "true", float(x), float(y), float(z))
            )
        return found

    def live(self, kind: str = "laser") -> list[Spot]:
        return [s for s in self.spots() if s.alive and s.kind == kind]

    def trace(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 5) == 'INFO ' then t[#t + 1] = l:sub(6) end end "
            "return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]


# --------------------------------------------------------------------- tests


def test_the_spot_is_up_before_anybody_says_anything() -> None:
    """The whole feature: no check-in, no player, a burning spot regardless."""
    party = Party()
    party.advance(5)
    live = party.live()
    assert len(live) == 1
    spot = live[0]
    # Created *from* the designating vehicle, which is what makes it die with
    # the team, and on the code the AI controller would have transmitted.
    assert spot.source == f"{_TACP} Unit #1"
    assert spot.code == str(laser.DEFAULT_CODE)
    x, z = party.position(f"{_COLUMN} Unit #1")
    assert (spot.x, spot.z) == (x, z)
    # Lifted clear of the deck rather than on the ground under the truck.
    assert spot.y == pytest.approx(2.0)


def test_an_ir_pointer_goes_up_with_it() -> None:
    party = Party()
    party.advance(5)
    assert len(party.live("ir")) == 1
    assert party.live("ir")[0].source == f"{_TACP} Unit #1"


def test_the_ir_pointer_can_be_left_off() -> None:
    party = Party(arm={"infrared": False})
    party.advance(5)
    assert len(party.live()) == 1
    assert party.live("ir") == []


def test_the_nearest_vehicle_gets_the_spot() -> None:
    party = Party()
    party.move(f"{_COLUMN} Unit #2", 1_500.0)
    party.advance(5)
    assert party.live()[0].x == pytest.approx(1_500.0)


def test_terrain_takes_the_spot_away_and_gives_it_back() -> None:
    """A column behind a spur takes the laser with it — measured, not assumed."""
    party = Party()
    party.advance(5)
    assert len(party.live()) == 1
    party.mask(True)
    party.advance(20)
    assert party.live() == []
    assert party.live("ir") == []
    party.mask(False)
    party.advance(40)
    assert len(party.live()) == 1


def test_nothing_out_of_reach_is_lased() -> None:
    party = Party(column_x=15_000.0)
    party.advance(20)
    assert party.live() == []
    # Inside the team's reach it comes up, so the bound is the range and not a
    # broken selection.
    for i in (1, 2, 3):
        party.move(f"{_COLUMN} Unit #{i}", 6_000.0)
    party.advance(40)
    assert len(party.live()) == 1


def test_a_shorter_reach_is_honoured() -> None:
    party = Party(spot={"max_range_m": 2_000.0})
    party.advance(20)
    assert party.live() == []


def test_the_spot_follows_the_vehicle_it_is_on() -> None:
    """The spot is moved as the target drives, or the bomb lands in its dust."""
    party = Party()
    party.advance(5)
    party.move(f"{_COLUMN} Unit #1", 3_400.0)
    party.advance(15)
    assert party.live()[0].x == pytest.approx(3_400.0)


def test_the_spot_holds_the_vehicle_it_started_on() -> None:
    """A spot that hops to a closer truck mid-fall throws the weapon off."""
    party = Party()
    party.advance(5)
    assert party.live()[0].x == pytest.approx(3_000.0)
    party.move(f"{_COLUMN} Unit #3", 1_000.0)
    party.advance(30)
    assert party.live()[0].x == pytest.approx(3_000.0)


def test_the_spot_moves_on_when_its_own_vehicle_dies() -> None:
    party = Party()
    party.advance(5)
    party.kill(f"{_COLUMN} Unit #1")
    party.advance(30)
    live = party.live()
    assert len(live) == 1
    assert live[0].x == pytest.approx(3_200.0)  # Unit #2, 200 m up the column


def test_losing_the_team_puts_the_spot_out() -> None:
    """The laser is a thing the player can be made to defend."""
    party = Party()
    party.advance(5)
    party.kill(f"{_TACP} Unit #1", f"{_TACP} Unit #2")
    party.advance(20)
    assert party.live() == []
    assert party.live("ir") == []
    # And nothing rebuilds it afterwards.
    party.advance(120)
    assert party.live() == []


def test_a_dead_target_group_is_not_lased() -> None:
    party = Party()
    party.advance(5)
    party.kill(*(f"{_COLUMN} Unit #{i}" for i in (1, 2, 3)))
    party.advance(20)
    assert party.live() == []


def test_a_team_that_goes_to_work_later_lases_later() -> None:
    party = Party(spot={"start_at_s": 600.0})
    party.advance(120)
    assert party.spots() == []
    party.advance(660)
    assert len(party.live()) == 1


def test_the_lead_correction_pushes_the_spot_ahead_of_the_vehicle() -> None:
    """CTLD's own correction: one second of travel, and only when asked for."""
    plain = Party()
    plain.drive(f"{_COLUMN} Unit #1", 10.0)
    plain.advance(5)
    assert plain.live()[0].x == pytest.approx(3_000.0)

    led = Party(spot={"lead_correction": True})
    led.drive(f"{_COLUMN} Unit #1", 10.0)
    led.advance(5)
    assert led.live()[0].x == pytest.approx(3_010.0)


def test_a_moving_target_is_updated_more_often_than_a_parked_one() -> None:
    """The update rate comes off the target's speed, bounded by max_drift_m."""
    party = Party(arm={"max_drift_m": 5.0, "min_update_s": 0.2})
    party.drive(f"{_COLUMN} Unit #1", 10.0)
    party.advance(5, step=0.5)
    # 5 m of drift at 10 m/s is an update every half second, so the spot is
    # moved several times inside the window a parked target would have waited.
    party.move(f"{_COLUMN} Unit #1", 3_100.0)
    party.advance(6, step=0.5)
    assert party.live()[0].x == pytest.approx(3_100.0)


def test_the_trace_says_what_it_decided() -> None:
    party = Party()
    party.advance(5)
    assert any("lasing Column Unit #1 on 1688" in line for line in party.trace())
    party.mask(True)
    party.advance(20)
    assert any("spot off" in line for line in party.trace())


def test_a_team_that_is_not_activated_yet_is_waited_for() -> None:
    """A held-back designator is not a dead one, and must not be given up on."""
    party = Party()
    # Stand in for a group DCS has not activated yet: nothing under that name.
    party.rt.execute(
        'local g = Group.getByName("Pinpoint") '
        "for _, u in ipairs(g.units) do u.alive = false end"
    )
    party.advance(30)
    assert party.spots() == []
    party.rt.execute(
        'local g = Group.getByName("Pinpoint") '
        "for _, u in ipairs(g.units) do u.alive = true end"
    )
    party.advance(60)
    assert len(party.live()) == 1
