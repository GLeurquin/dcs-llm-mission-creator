"""Run the generated IADS against a stubbed DCS scripting environment.

`test_iads.py` checks what the generated Lua *says*. This runs it: the MIST shim,
the vendored Skynet build and the generated setup script are loaded into an
embedded Lua with `tests/lua/dcs_env.lua` standing in for the DCS scripting
environment, and then the clock is driven forward.

That distinction matters more here than anywhere else in this project, because
every interesting way this integration can break is a runtime failure that leaves
a perfectly valid `.miz`: a Skynet method that moved between versions, a site
that is never cued, suppression that does not stick, a radio call that fires for
the wrong reason. Writing these tests caught four such things — that Skynet's
`enableEmission` path needs `Group` itself as the group metatable, that an EWR's
`actAsEW` is set during registration rather than defaulting on, that a live site
stays live only because its own radar still holds the contact, and that losing
the whole EWR chain makes batteries go autonomous *and radiate continuously*
rather than dark.

Needs `lupa` (dev dependency). Skips without it rather than failing, so a
checkout that only wants the pure-Python tests still passes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Vehicle
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import lua
from dcs_mission_creator.core.iads import Site, arm_iads

lupa = pytest.importorskip("lupa", reason="needs the lupa embedded Lua interpreter")

_ENV = Path(__file__).parent / "lua" / "dcs_env.lua"

# One jet, two EWRs 120 km back, two belts between them. Distances are chosen so
# the jet starts outside every cue range and ends inside the SA-6's.
_SCENARIO = """
TESTGROUP{name="EWR-1", x=120000, z=0,
          units={{type="55G6 EWR", attrs={["EWR"]=true}, radar_m=300000}}}
TESTGROUP{name="EWR-2", x=120000, z=30000,
          units={{type="55G6 EWR", attrs={["EWR"]=true}, radar_m=300000}}}
TESTGROUP{name="SA-6 belt", x=40000, z=0, units={
  {type="Kub 1S91 str", attrs={["SAM SR"]=true, ["SAM TR"]=true}, radar_m=70000},
  {type="Kub 2P25 ln", dx=100, ammo=3, missile_m=24000, ceiling_m=8000},
}}
TESTGROUP{name="SA-2 belt", x=60000, z=20000, units={
  {type="p-19 s-125 sr", attrs={["SAM SR"]=true}, radar_m=90000},
  {type="SNR_75V", dx=100, attrs={["SAM TR"]=true}, radar_m=65000},
  {type="S_75M_Volhov", dx=200, ammo=2, missile_m=43000, ceiling_m=20000},
}}
TESTGROUP{name="Uzi", x=-160000, z=0, side=coalition.side.BLUE,
          category=Group.Category.AIRPLANE, unitCategory=Unit.Category.AIRPLANE,
          units={{type="F-16C_50", y=6000}}}
"""

_SHOT = """
TESTFIRE({id = world.event.S_EVENT_SHOT,
          initiator = Unit.getByName("Uzi Unit #1"),
          weapon = {getDesc = function()
            return {typeName = "weapons.missiles.AGM_88C"}
          end}})
"""


def _group(gid: int, name: str, *types: Any) -> VehicleGroup:
    terrain = Caucasus()
    vg = VehicleGroup(gid, name)
    for i, unit_type in enumerate(types, start=1):
        vg.add_unit(Vehicle(terrain, gid * 100 + i, f"{name} Unit #{i}", unit_type.id))
    return vg


class Net:
    """A loaded, running IADS the test can poke at."""

    def __init__(self, **overrides: Any) -> None:
        sa6 = _group(1, "SA-6 belt", AirDefence.Kub_1S91_str, AirDefence.Kub_2P25_ln)
        sa2 = _group(
            2,
            "SA-2 belt",
            AirDefence.P_19_s_125_sr,
            AirDefence.SNR_75V,
            AirDefence.S_75M_Volhov,
        )
        e1 = _group(3, "EWR-1", AirDefence.X_55G6_EWR)
        e2 = _group(4, "EWR-2", AirDefence.X_55G6_EWR)

        # delay/shutdown are pinned to single values so the timings below are
        # exact rather than a band the test has to tolerate.
        pinned = dict(delay_s=(20.0, 20.0), shutdown_s=(300.0, 300.0), probability=1.0)
        mission = Mission(Caucasus())
        rule = arm_iads(
            mission,
            [
                Site(sa6, "SA-6", go_live_percent=150, **{**pinned, **overrides}),
                Site(sa2, "SA-2", go_live_percent=130, net_relay=0.0, **pinned),
                Site(e1, "EWR", role="ewr", probability=0.0),
                Site(e2, "EWR", role="ewr", probability=0.0),
            ],
        )
        self.rt = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.rt.execute(_ENV.read_text(encoding="utf-8"))
        self.rt.execute(_SCENARIO)
        self.rt.execute(lua.source("mist_shim.lua"))
        self.rt.execute(lua.source("vendor/skynet-iads.lua"))
        self.rt.execute(rule.actions[0].script)

    def element(self, name: str) -> str:
        return self.rt.eval(
            """(function(n)
              local el = dcsmcIADS:getSAMSiteByGroupName(n)
              if el == nil then
                el = dcsmcIADS:getEarlyWarningRadarByUnitName(n .. " Unit #1")
              end
              if el == nil then return "MISSING" end
              return tostring(el.aiState) .. "/" ..
                     tostring(el.harmSilenceID ~= nil) .. "/" ..
                     tostring(el:getAutonomousState())
            end)"""
        )(name)

    def live(self, name: str) -> bool:
        return self.element(name).split("/")[0] == "true"

    def suppressed(self, name: str) -> bool:
        return self.element(name).split("/")[1] == "true"

    def autonomous(self, name: str) -> bool:
        return self.element(name).split("/")[2] == "true"

    def advance(self, to: float, step: float = 1.0) -> None:
        self.rt.eval("TESTADVANCE")(to, step)

    def close_to(self, x: float) -> None:
        self.rt.eval("(function(x) Unit.getByName('Uzi Unit #1').x = x end)")(x)

    def shoot(self) -> None:
        self.rt.execute(_SHOT)

    def clear_log(self) -> None:
        self.rt.execute("_G.TESTLOG = {}")

    def calls(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 4) == 'TEXT' then t[#t + 1] = l:sub(6) end end "
            "return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def errors(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 5) == 'ERROR' or l:sub(1, 8) == 'SCHEDERR' then "
            "t[#t + 1] = l end end return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def kill(self, group: str) -> None:
        """Destroy every unit in a group, announcing each death.

        The event is the point: Skynet re-evaluates a battery's autonomous state
        from `S_EVENT_DEAD` on its parent radar, so silently flipping a flag
        leaves the framework believing the net is intact.
        """
        self.rt.eval(
            """(function(n)
              for _, u in ipairs(Group.getByName(n).units) do
                u.alive = false
                TESTFIRE({id = world.event.S_EVENT_DEAD, initiator = u})
              end
            end)"""
        )(group)


# ------------------------------------------------------------------- it loads
def test_the_whole_stack_loads_without_error() -> None:
    net = Net()
    assert net.errors() == []
    assert net.element("SA-6 belt") != "MISSING"
    assert net.element("EWR-1") != "MISSING"


def test_batteries_start_dark_and_early_warning_starts_live() -> None:
    """The stock game has every radar turning from mission start; this does not."""
    net = Net()
    assert not net.live("SA-6 belt")
    assert not net.live("SA-2 belt")
    assert net.live("EWR-1")
    assert net.live("EWR-2")


def test_a_battery_with_a_live_parent_radar_is_not_autonomous() -> None:
    """If the EWRs failed to parent the belts, every belt would radiate from t=0."""
    net = Net()
    assert not net.autonomous("SA-6 belt")
    assert not net.autonomous("SA-2 belt")


# ------------------------------------------------------------------- cueing
def test_a_distant_jet_does_not_bring_a_battery_up() -> None:
    net = Net()
    net.advance(30)
    assert not net.live("SA-6 belt")


def test_closing_inside_the_cue_range_brings_the_battery_up() -> None:
    net = Net()
    net.advance(30)
    net.close_to(10_000)  # 30 km from the SA-6, inside 150% of its 24 km reach
    net.advance(60)
    assert net.live("SA-6 belt")
    assert net.errors() == []


def test_a_battery_goes_quiet_again_once_the_jet_leaves() -> None:
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    assert net.live("SA-6 belt")
    net.close_to(-160_000)
    net.advance(80)
    assert not net.live("SA-6 belt")


def test_losing_the_whole_chain_makes_a_battery_autonomous_not_dark() -> None:
    """The easy thing to get backwards, and the briefings depend on which it is.

    Doctrine, and what Skynet implements: a battery cut off from the net searches
    on its own, so it radiates continuously from then on.
    """
    net = Net()
    net.advance(30)
    assert not net.live("SA-6 belt")
    net.kill("EWR-1")
    net.kill("EWR-2")
    net.advance(90)
    assert net.autonomous("SA-6 belt")
    assert net.live("SA-6 belt")


# -------------------------------------------------- reaction to observed fire
def test_a_shot_in_plain_view_darkens_the_site_after_the_recognition_delay() -> None:
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    assert net.live("SA-6 belt")

    net.clear_log()
    net.shoot()
    net.advance(50)  # 10 s after the shot, inside the pinned 20 s delay
    assert net.live("SA-6 belt"), "reacted before the crew could have"

    net.advance(70)  # 30 s after the shot
    assert net.suppressed("SA-6 belt")
    assert not net.live("SA-6 belt")
    assert any("SA-6 has ceased emissions" in c for c in net.calls())


def test_the_site_is_released_after_the_shutdown_window_and_says_so() -> None:
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    assert net.suppressed("SA-6 belt")
    net.advance(330, step=5)
    assert net.suppressed("SA-6 belt"), "released early — the window is 300 s"
    net.advance(420, step=5)
    assert not net.suppressed("SA-6 belt")
    assert net.live("SA-6 belt"), "the jet is still there, so it should come back"
    assert any("SA-6 radar is radiating again" in c for c in net.calls())


def test_a_launch_masked_from_the_whole_net_reaches_nobody() -> None:
    """The point of the model: an ARM is passive, so an unseen launch is unknown."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    assert net.live("SA-6 belt")

    net.clear_log()
    net.rt.execute("TESTMASK = function() return false end")
    net.shoot()
    net.advance(90)
    assert not net.suppressed("SA-6 belt")
    assert not net.suppressed("SA-2 belt")
    assert net.calls() == []


def test_a_site_that_could_not_see_the_launch_needs_the_relay_to_hear_of_it() -> None:
    """The SA-2 here has `net_relay=0.0`, so being told second-hand does nothing.

    The SA-6 sees the same launch and reacts, which is what makes this a test of
    the relay rather than of the shot being detected at all.
    """
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    assert net.live("SA-6 belt") and net.live("SA-2 belt")

    net.clear_log()
    # Terrain that blocks only traces starting at the SA-2 site.
    net.rt.execute("""
    TESTMASK = function(a, b)
      local dx, dz = a.x - 60000, a.z - 20000
      if math.sqrt(dx * dx + dz * dz) < 1000 then return false end
      return true
    end
    """)
    net.shoot()
    net.advance(90)
    assert net.suppressed("SA-6 belt"), "it had line of sight to the launch"
    assert not net.suppressed("SA-2 belt"), "masked, and relaying nothing"
    assert [c for c in net.calls() if "SA-2" in c] == []


def test_repeat_fire_extends_the_window_rather_than_restarting_it() -> None:
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.shoot()
    net.advance(70)
    assert net.suppressed("SA-6 belt")
    # A second shot 100 s in: the window should run 300 s from *this* reaction,
    # so the site is still dark well past where the first window alone would end.
    net.advance(170)
    net.shoot()
    net.advance(400, step=5)
    assert net.suppressed("SA-6 belt"), "the second shot did not extend the window"


def test_a_site_whose_radars_are_dead_is_not_suppressed_or_announced() -> None:
    """A destroyed site must not report going dark and then coming back up."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.kill("SA-6 belt")
    net.shoot()
    net.advance(90)
    assert [c for c in net.calls() if "SA-6" in c] == []


def test_a_non_anti_radiation_shot_is_ignored() -> None:
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.rt.execute("""
    TESTFIRE({id = world.event.S_EVENT_SHOT,
              initiator = Unit.getByName("Uzi Unit #1"),
              weapon = {getDesc = function()
                return {typeName = "weapons.missiles.AIM_120C"}
              end}})
    """)
    net.advance(90)
    assert net.live("SA-6 belt")
    assert net.calls() == []
