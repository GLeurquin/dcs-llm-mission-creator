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
from dcs import planes
from dcs.countries import USA
from dcs.flyingunit import Plane
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Vehicle
from dcs.unitgroup import PlaneGroup, VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import lua
from dcs_mission_creator.core.iads import Listener, Site, arm_iads

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
-- The ESM collector every radio call is gated on: 80 km from the SA-6, 160 km
-- from the chain, so the sites are inside its reach and the conditions that
-- decide anything are terrain and whether it is still alive.
TESTGROUP{name="Magic", x=-40000, z=0, side=coalition.side.BLUE,
          category=Group.Category.AIRPLANE, unitCategory=Unit.Category.AIRPLANE,
          units={{type="E-3A", y=9000}}}
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

    def __init__(
        self,
        *,
        arm: dict[str, Any] | None = None,
        hearing_m: float | None = 250_000.0,
        **overrides: Any,
    ) -> None:
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
        # The one thing that can hear a radar change state. `hearing_m=None`
        # takes it away, which is how a net with no collector is tested.
        magic = PlaneGroup(5, "Magic")
        magic.add_unit(Plane(Caucasus(), 501, "Magic Unit #1", planes.E_3A, USA()))

        # delay/shutdown are pinned to single values so the timings below are
        # exact rather than a band the test has to tolerate. Emission discipline
        # is off unless a test asks for it: it goes through the same silence
        # mechanism as suppression, so a site quiet by its own choice is
        # indistinguishable from a suppressed one to `suppressed()` below, and
        # every test about anti-radiation fire would be reading the wrong signal.
        pinned = dict(
            delay_s=(20.0, 20.0),
            shutdown_s=(300.0, 300.0),
            probability=1.0,
            emission_limit_s=(0.0, 0.0),
        )
        mission = Mission(Caucasus())
        rule = arm_iads(
            mission,
            [
                Site(sa6, "SA-6", go_live_percent=150, **{**pinned, **overrides}),
                Site(sa2, "SA-2", go_live_percent=130, net_relay=0.0, **pinned),
                Site(e1, "EWR", role="ewr", probability=0.0),
                Site(e2, "EWR", role="ewr", probability=0.0),
            ],
            listeners=(
                [] if hearing_m is None else [Listener(magic, "Magic", hearing_m)]
            ),
            **(arm or {}),
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

    def trace(self) -> list[str]:
        """The `dcs.log` lines this project's own half of the net wrote."""
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 5) == 'INFO ' and l:find('IADS/', 1, true) then "
            "t[#t + 1] = l:sub(6) end end return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def errors(self) -> list[str]:
        raw = self.rt.eval(
            "(function() local t = {} for _, l in ipairs(TESTLOG) do "
            "if l:sub(1, 5) == 'ERROR' or l:sub(1, 8) == 'SCHEDERR' then "
            "t[#t + 1] = l end end return table.concat(t, '\\n') end)"
        )()
        return [line for line in raw.splitlines() if line]

    def position(self, group: str) -> tuple[float, float]:
        """Where the group's leader is — what a route order steers."""
        return self.rt.eval(
            "(function(n) local u = Group.getByName(n).units[1] return u.x, u.z end)"
        )(group)

    def ai_on(self, group: str) -> bool:
        return self.rt.eval(
            "(function(n) return Group.getByName(n).onoff ~= false end)"
        )(group)

    def combat_ready(self, group: str) -> bool:
        """Whether the group is still in ALARM_STATE RED — deployed to fight."""
        return self.rt.eval(
            "(function(n) return Group.getByName(n).alarm "
            "== AI.Option.Ground.val.ALARM_STATE.RED end)"
        )(group)

    def emitting(self, group: str) -> bool:
        return self.rt.eval(
            "(function(n) return Group.getByName(n).emitting == true end)"
        )(group)

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


# ------------------------------------------------ who may report an emission
#
# A radar starting or stopping is an ESM observation, so the call needs somebody
# who could have made it. These are the four ways that fails, and in every one
# of them the *net* behaves identically — the site still goes dark, still
# displaces, still comes back. Only the reporting stops.
def test_a_net_with_no_collector_goes_dark_without_a_word() -> None:
    """The honest default: with nothing listening, there is nothing to report."""
    net = Net(hearing_m=None)
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    assert net.suppressed("SA-6 belt"), "the gate is on the radio call, not the net"
    assert net.calls() == []


def test_killing_the_collector_ends_the_reporting() -> None:
    """It is a live condition — the picture belongs to an aircraft that can die."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.kill("Magic")
    net.clear_log()
    net.shoot()
    net.advance(120)
    assert net.suppressed("SA-6 belt")
    assert net.calls() == []


def test_a_collector_out_of_reach_hears_nothing() -> None:
    """20 km of reach, and the nearest belt is 80 km from the track."""
    net = Net(hearing_m=20_000.0)
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    assert net.suppressed("SA-6 belt")
    assert net.calls() == []


def test_a_site_masked_from_the_collector_is_not_reported() -> None:
    """The case that bites at ESM reach: a battery behind high ground.

    The mask blocks only traces starting at the collector, so the sites still see
    the launch and still react — which is what makes this a test of the reporting
    rather than of the reaction.
    """
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.rt.execute("TESTMASK = function(a) return a.x > -1000 end")
    net.shoot()
    net.advance(120)
    assert net.suppressed("SA-6 belt"), "it saw the launch itself"
    assert net.calls() == []


def test_the_trace_says_which_collector_heard_it() -> None:
    net = Net(arm={"trace": True})
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    log = "\n".join(net.trace())
    assert "SA-6 belt went off the air, heard by Magic at 80000 m — calling it" in log


def test_the_trace_says_when_nobody_could_hear_it() -> None:
    """The question this answers: why did the site go dark and nobody call it?"""
    net = Net(hearing_m=None, arm={"trace": True})
    # Said once, at setup, so a net nobody is listening to is visible in the log
    # before anything happens rather than only when a call goes missing.
    assert any("0 collector(s)" in line for line in net.trace())
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    log = "\n".join(net.trace())
    assert (
        "SA-6 belt went off the air, and nothing of ours could hear it — no call" in log
    )


# ------------------------------------------------------------ shoot and scoot
#
# The SA-6 in this scenario is a 1S91 and a 2P25 — both self-propelled, so
# `core/iads.py` gives it the default hop. The SA-2 belt beside it fires from
# prepared revetments and is refused one by the same table, which is what makes
# these a test of the policy rather than of the mechanism alone.
_JOCKEY_M = 250.0


def _moved(before: tuple[float, float], after: tuple[float, float]) -> float:
    return ((after[0] - before[0]) ** 2 + (after[1] - before[1]) ** 2) ** 0.5


def test_a_battery_that_can_drive_leaves_the_point_it_was_shot_at() -> None:
    """Going dark saves the system; a HARM still flies to where the radar was."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")

    net.shoot()
    net.advance(60)  # 20 s after the shot: the crew has just reacted
    assert net.suppressed("SA-6 belt")
    net.advance(160, step=5)  # and then drives

    hop = _moved(home, net.position("SA-6 belt"))
    assert hop > 60.0, "the site never left the aim point"
    assert hop <= _JOCKEY_M * 1.05
    assert net.errors() == []


def test_the_hop_starts_only_once_the_crew_reacts() -> None:
    """A close-in shot arrives before the battery has moved at all.

    That is the property the whole reaction model is built on — the shooter's
    range at launch decides the duel — and a jockey that began at the launch
    would quietly delete it.
    """
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    net.shoot()
    net.advance(50)  # 10 s in, inside the pinned 20 s recognition delay
    assert _moved(home, net.position("SA-6 belt")) == pytest.approx(0.0, abs=1.0)


def test_a_battery_in_revetments_stays_where_it_is() -> None:
    """An S-125 has no answer to a HARM but the dark window, and that is right."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-2 belt")
    net.shoot()
    net.advance(220, step=5)
    assert net.suppressed("SA-2 belt")
    assert _moved(home, net.position("SA-2 belt")) == pytest.approx(0.0, abs=1.0)


def test_repeat_fire_cannot_walk_a_battery_out_of_its_briefed_ring() -> None:
    """Every hop is measured from the start point, never from the last one.

    The map drew a ring around where this site began, and `core/dtc.py` put the
    same ring in the cockpit. Four HARMs in a sortie must not make both wrong.
    """
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    at = 40
    for _ in range(4):
        net.shoot()
        at += 200
        net.advance(at, step=5)
    assert _moved(home, net.position("SA-6 belt")) <= _JOCKEY_M * 1.05


def test_a_displacing_battery_gets_its_ai_back_but_not_its_radar() -> None:
    """The trade the jockey makes with Skynet, in both directions.

    Skynet's HARM path cuts the group's AI — its workaround for a DCS quirk —
    and a group with its AI off does not drive, so the jockey hands it back. The
    emissions must stay off regardless, or the site is not suppressed at all.
    """
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    net.shoot()
    net.advance(70)
    assert net.suppressed("SA-6 belt")
    assert net.ai_on("SA-6 belt"), "AI still off — the battery cannot move"
    assert not net.emitting("SA-6 belt"), "it went dark and then came back up"
    assert not net.combat_ready("SA-6 belt"), (
        "still in ALARM_STATE RED — Skynet set it going live and DCS barely "
        "moves a combat-ready vehicle, so the order would be taken and ignored"
    )


def test_a_battery_with_nowhere_to_go_stays_put_quietly() -> None:
    """Water on every bearing is a fact about where the mission put it."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    net.rt.execute("TESTSURFACE = function() return land.SurfaceType.WATER end")
    net.shoot()
    net.advance(220, step=5)
    assert net.suppressed("SA-6 belt"), "it should still go dark"
    assert _moved(home, net.position("SA-6 belt")) == pytest.approx(0.0, abs=1.0)
    assert net.errors() == []


def test_a_site_the_mission_refused_the_hop_does_not_move() -> None:
    net = Net(jockey_m=0.0)
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    net.shoot()
    net.advance(220, step=5)
    assert net.suppressed("SA-6 belt")
    assert _moved(home, net.position("SA-6 belt")) == pytest.approx(0.0, abs=1.0)


def test_a_dead_site_does_not_displace() -> None:
    """A wreck neither goes dark nor drives away."""
    net = Net()
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    net.kill("SA-6 belt")
    net.shoot()
    net.advance(220, step=5)
    assert _moved(home, net.position("SA-6 belt")) == pytest.approx(0.0, abs=1.0)


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


# ------------------------------------------------------------------- tracing
def test_the_trace_follows_one_shot_from_launch_to_release() -> None:
    """What the log is for: the whole chain of decisions behind one HARM.

    Sites are named by their DCS group name rather than their briefing label, so
    a line here lines up with Skynet's own output and with the `.miz`.
    """
    net = Net(arm={"trace": True})
    net.close_to(10_000)
    net.advance(40)
    net.clear_log()
    net.shoot()
    net.advance(120)
    log = "\n".join(net.trace())

    assert "anti-radiation launch (weapons.missiles.AGM_88C) by Uzi Unit #1" in log
    assert "SA-6 belt is 30000 m from the launch and sees it" in log
    assert "EWR-1 is 110000 m from the launch, past its 60000 m reach" in log
    assert "2 site(s) in reach, launch seen by the net" in log
    assert "SA-6 belt acts on it (rolled" in log and "radar off in" in log
    assert "SA-6 belt reacting: off the air for 300s" in log
    assert "SA-6 belt has gone dark — reacting to an anti-radiation launch" in log
    assert "SA-6 belt displacing" in log, "the shoot-and-scoot hop"
    assert "SA-2 belt fires from prepared positions and stays put" in log

    # The window is stated when it opens, so the release has to be visible too or
    # a site that never came back would look the same as one that did.
    net.advance(400, step=5)
    log = "\n".join(net.trace())
    assert log.count("SA-6 belt is released, cold") == 1
    assert "SA-6 belt is radiating again after being shot off the air" in log
    assert net.errors() == []


def test_the_trace_names_the_sites_left_out_and_why() -> None:
    """The question asked of this log most often: why did nothing happen?"""
    net = Net(arm={"trace": True})
    net.advance(30)  # the jet is 160 km out, so both belts are still cold
    net.clear_log()
    net.shoot()
    net.advance(60)
    log = "\n".join(net.trace())
    assert "SA-6 belt was cold — nothing was aimed at it" in log
    assert "SA-2 belt was cold — nothing was aimed at it" in log
    assert "acts on it" not in log


def test_reading_the_trace_does_not_change_what_the_net_does() -> None:
    """The roll is drawn either way, so a traced sortie decides what a quiet one does."""
    for arm in ({"trace": True}, None):
        net = Net(arm=arm)
        net.close_to(10_000)
        net.advance(40)
        net.shoot()
        net.advance(120)
        assert net.suppressed("SA-6 belt")
        assert net.suppressed("SA-2 belt")
        assert net.live("EWR-1"), "nobody shot at the chain"
        assert net.errors() == []
        assert (net.trace() != []) == (arm is not None)


# ------------------------------------- a round already in the air (POS/EOM)
#
# The jet sits 30 km off an EWR and 50 km short of the SA-6's cue range, so the
# launch is seen by the net while the belt it is aimed at is still cold — which
# is the geometry of a HARM shot in POS or EOM mode, at a place rather than at an
# emitter.
_COLD_SHOT_X = 90_000


def test_a_battery_coming_up_into_a_launch_it_did_not_see_still_reacts() -> None:
    """The pre-emptive shot, and the free kill it used to be.

    At launch the belt is cold and so in nobody's reaction; before the alert
    window it came up into a round already on its way and could never react, no
    matter how good the crew was. Now the net tells it, and the recognition delay
    runs from the moment it came on the air.
    """
    net = Net(arm={"trace": True}, net_relay=1.0)
    net.close_to(_COLD_SHOT_X)
    net.advance(40)
    assert not net.live("SA-6 belt"), "the belt must be cold at launch"
    net.clear_log()
    net.shoot()
    net.advance(70)
    assert not net.suppressed("SA-6 belt"), "cold at launch — nothing to shut down yet"

    net.close_to(10_000)  # cued up, with the round still in the air
    net.advance(90)
    assert net.live("SA-6 belt"), (
        "it should come up — being warned is not being told to hide"
    )
    net.advance(180, step=5)  # its 20 s of pinned recognition, timed from coming up
    assert net.suppressed("SA-6 belt")
    log = "\n".join(net.trace())
    assert "SA-6 belt came up" in log and "acts on it" in log
    assert net.errors() == []


def test_the_alert_window_expires() -> None:
    """A crew does not stay off the air over a shot from ten minutes ago."""
    net = Net(net_relay=1.0)
    net.close_to(_COLD_SHOT_X)
    net.advance(40)
    net.shoot()
    net.advance(200, step=5)  # past the 120 s window
    net.close_to(10_000)
    net.advance(340, step=5)
    assert net.live("SA-6 belt")
    assert not net.suppressed("SA-6 belt")


def test_a_masked_launch_leaves_the_net_unalerted() -> None:
    """The model holds either way: a launch nobody saw cannot be called down."""
    net = Net(net_relay=1.0)
    net.close_to(_COLD_SHOT_X)
    net.advance(40)
    net.rt.execute("TESTMASK = function() return false end")
    net.shoot()
    net.advance(70)
    net.rt.execute("TESTMASK = nil")
    net.close_to(10_000)
    net.advance(220, step=5)
    assert net.live("SA-6 belt")
    assert not net.suppressed("SA-6 belt")


def test_a_site_that_rolled_at_launch_does_not_roll_again_on_its_way_up() -> None:
    """One roll per site per shot, or being released would re-suppress it free."""
    net = Net(arm={"trace": True}, net_relay=1.0)
    net.close_to(10_000)
    net.advance(40)
    assert net.live("SA-6 belt")
    net.shoot()
    net.advance(420, step=5)  # dark, released, and back up
    log = "\n".join(net.trace())
    assert log.count("SA-6 belt acts on it") == 1
    assert "SA-6 belt came up" not in log


# --------------------------------------- relocating after being on the air
def test_a_battery_that_has_been_radiating_relocates_when_it_goes_quiet() -> None:
    """The hop that answers a pre-planned shot, because it happens before it.

    A HARM in POS or EOM mode flies to a coordinate, so going dark saves nothing
    and only a stale coordinate does. A battery that has been emitting has to
    assume it was fixed while it did, so it moves once it is quiet again.
    """
    net = Net(arm={"trace": True})
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    assert net.live("SA-6 belt")
    net.advance(170, step=5)  # ~130 s on the air, past the 90 s default
    net.close_to(-160_000)  # the package leaves and the belt goes quiet
    net.advance(280, step=5)
    assert not net.live("SA-6 belt")
    assert not net.suppressed("SA-6 belt"), "nobody shot at it"
    assert _moved(home, net.position("SA-6 belt")) > 30
    assert "position compromised" in "\n".join(net.trace())
    assert net.errors() == []


def test_relocating_stays_inside_the_briefed_ring() -> None:
    """Every hop is measured from the start point, so repeated ones cannot walk."""
    net = Net()
    home = net.position("SA-6 belt")
    at = 0
    for _ in range(3):
        net.close_to(10_000)
        at += 170
        net.advance(at, step=5)
        net.close_to(-160_000)
        at += 120
        net.advance(at, step=5)
    assert _moved(home, net.position("SA-6 belt")) <= _JOCKEY_M * 1.05


def test_a_mission_can_switch_the_relocation_off() -> None:
    net = Net(scoot_after_s=0.0)
    net.close_to(10_000)
    net.advance(40)
    home = net.position("SA-6 belt")
    net.advance(170, step=5)
    net.close_to(-160_000)
    net.advance(280, step=5)
    assert not net.live("SA-6 belt")
    assert _moved(home, net.position("SA-6 belt")) == pytest.approx(0.0, abs=1.0)


# ------------------------------------------- recognition scales with range
def test_a_launch_in_plain_sight_up_close_is_recognised_faster_than_a_far_one() -> None:
    """The band a mission states is the band at the edge of `react_range_m`.

    A launch a few kilometres off is a motor and a smoke trail; one at the edge of
    the net's reach is a report that has to be made, believed and passed on. Both
    shots below are seen by the SA-6 — only the range differs.
    """
    bands = {}
    for x, key in ((25_000, "near"), (-15_000, "far")):
        net = Net(arm={"trace": True}, delay_s=(20.0, 60.0))
        net.close_to(10_000)
        net.advance(40)
        net.close_to(x)  # 15 km from the belt, then 55 km
        net.clear_log()
        net.shoot()
        net.advance(60)
        line = next(ln for ln in net.trace() if "SA-6 belt acts on it" in ln)
        lo, hi = line.split("recognition ")[1].split("s at this range")[0].split("-")
        bands[key] = (float(lo), float(hi))

    assert bands["near"][1] < bands["far"][1], bands
    assert bands["far"][1] <= 60.0, "never slower than the stated band"
    assert bands["near"][0] >= 6.0, "never faster than a crew can reach the switch"


def test_a_relayed_launch_is_slower_as_well_as_less_likely() -> None:
    """Being told takes longer than looking, which `net_relay` alone did not say."""
    net = Net(arm={"trace": True}, delay_s=(20.0, 60.0), net_relay=1.0)
    net.close_to(10_000)
    net.advance(40)
    # Terrain that blocks only traces starting at the SA-6.
    net.rt.execute("""
    TESTMASK = function(a, b)
      local dx, dz = a.x - 40000, a.z - 0
      return math.sqrt(dx * dx + dz * dz) > 500
    end""")
    net.clear_log()
    net.shoot()
    net.advance(90)
    line = next(ln for ln in net.trace() if "SA-6 belt acts on it" in ln)
    assert "relayed" in line
    assert net.suppressed("SA-6 belt")


# ------------------------------------------------------- emission discipline
#
# 30 km from the SA-6 is inside the 36 km it is cued at and outside the 24 km its
# launchers reach, so the site comes up and has nothing to shoot — which is
# exactly the situation the twenty-second rule was written for.
_CUED_NOT_ENGAGING_X = 10_000
_INSIDE_THE_MEZ_X = 25_000


def test_a_disciplined_crew_takes_a_look_and_goes_quiet_again() -> None:
    """The Wikipedia rule, and what kept batteries alive over Iraq and Kosovo."""
    net = Net(
        arm={"trace": True},
        emission_limit_s=(20.0, 20.0),
        emission_pause_s=(60.0, 60.0),
    )
    net.close_to(_CUED_NOT_ENGAGING_X)
    net.advance(10)
    assert net.live("SA-6 belt"), "cued on the first cycle, so the look starts"

    net.advance(40, step=5)  # its 20 s look is over
    assert not net.live("SA-6 belt"), "it should be off the air by its own choice"
    log = "\n".join(net.trace())
    assert "SA-6 belt intends a look of at most 20s" in log
    assert "has held its look long enough — off the air for 60s" in log
    assert "SA-6 belt has gone dark — holding emissions" in log

    # It keeps working the target in looks rather than staying down: sampled on
    # the trace, not on `live` at one instant, because with a 20 s look and a 60 s
    # pause the site is only on the air a quarter of the time.
    net.advance(260, step=5)
    log = "\n".join(net.trace())
    assert log.count("SA-6 belt intends a look of at most 20s") >= 3
    assert log.count("off the air for 60s by its own discipline") >= 3
    assert net.errors() == []


def test_discipline_never_cuts_an_engagement() -> None:
    """Dani's own radar stayed up the extra twenty seconds to finish the shot.

    The limit is about idling on the air for a HARM shooter's benefit, not about
    declining to shoot: with the jet inside the launchers' reach the clock holds.
    """
    net = Net(
        arm={"trace": True},
        emission_limit_s=(20.0, 20.0),
        emission_pause_s=(60.0, 60.0),
    )
    net.close_to(_INSIDE_THE_MEZ_X)
    net.advance(40)
    assert net.live("SA-6 belt")
    net.advance(120, step=5)
    assert net.live("SA-6 belt"), "it went quiet with a target in its own envelope"
    assert "off the air for 60s by its own discipline" not in "\n".join(net.trace())

    # Once the target leaves the envelope the look is over on the next check.
    net.close_to(_CUED_NOT_ENGAGING_X)
    net.advance(160, step=5)
    assert not net.live("SA-6 belt")


def test_an_undisciplined_crew_sits_on_the_air() -> None:
    """A conscript battery is the one the HARM finds still radiating."""
    net = Net(emission_limit_s=(0.0, 0.0))
    net.close_to(_CUED_NOT_ENGAGING_X)
    net.advance(40)
    assert net.live("SA-6 belt")
    net.advance(300, step=5)
    assert net.live("SA-6 belt")


def test_a_look_that_ended_does_not_cut_the_next_one_short() -> None:
    """Each go-live arms its own timer; a stale one must not fire on a fresh look."""
    net = Net(
        arm={"trace": True},
        emission_limit_s=(20.0, 20.0),
        emission_pause_s=(30.0, 30.0),
    )
    net.close_to(_CUED_NOT_ENGAGING_X)
    net.advance(40)
    at = 40
    looks = 0
    for _ in range(4):
        at += 30
        net.advance(at, step=5)
        looks = "\n".join(net.trace()).count("intends a look")
    assert looks >= 2, "it should be working in repeated looks"
    assert net.errors() == []
