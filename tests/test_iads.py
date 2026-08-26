"""Tests for `core.iads` — needs a Mission, but no overlay and no DCS.

Everything this helper does happens in generated Lua handed to a vendored
framework, so the whole class of failure is silent: a site that never reached the
table, a cue percentage dropped on the floor, an EWR registered as a SAM site, a
net with no early warning in it, or a call into Skynet API that no longer exists
all build a perfectly valid `.miz` and then behave like the stock game.

The last of those is what `test_setup_only_calls_api_the_vendored_build_has`
guards: the framework is pinned third-party code, and a version bump that renames
a method would otherwise only show up in the air.
"""

from __future__ import annotations

import re

import pytest
from dcs import action, planes
from dcs.countries import USA, Russia
from dcs.flyingunit import Plane
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.triggers import TriggerStart
from dcs.unit import Vehicle
from dcs.unitgroup import PlaneGroup, VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import lua
from dcs_mission_creator.core.iads import (
    Listener,
    Site,
    arm_iads,
    jockey_m as iads_jockey_m,
)


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


def _group(gid: int, name: str, *types: object) -> VehicleGroup:
    """A group with `types` as its units, named the way pydcs names them."""
    terrain = Caucasus()
    vg = VehicleGroup(gid, name)
    for i, unit_type in enumerate(types, start=1):
        vg.add_unit(
            Vehicle(terrain, gid * 100 + i, f"{name} Unit #{i}", unit_type.id)  # type: ignore[attr-defined]
        )
    return vg


@pytest.fixture
def sa6() -> VehicleGroup:
    return _group(1, "SA-6 belt", AirDefence.Kub_1S91_str, AirDefence.Kub_2P25_ln)


@pytest.fixture
def ewr() -> VehicleGroup:
    return _group(2, "EWR north", AirDefence.X_55G6_EWR)


@pytest.fixture
def sa15() -> VehicleGroup:
    return _group(3, "SA-15 guard", AirDefence.Tor_9A331)


@pytest.fixture
def magic() -> PlaneGroup:
    """An AWACS to hang the emissions calls on — the collector, in ESM terms."""
    pg = PlaneGroup(5, "Magic")
    pg.add_unit(Plane(Caucasus(), 501, "Magic Unit #1", planes.E_3A, USA()))
    return pg


def script_of(rule: TriggerStart) -> str:
    """The Lua the trigger carries, as DCS would compile it."""
    act = rule.actions[0]
    assert isinstance(act, lua.InlineDoScript)
    return act.script


def setup_of(mission: Mission) -> str:
    return script_of(mission.triggerrules.triggers[-1])


# ------------------------------------------------------- loading the framework
def test_the_framework_and_shim_load_before_the_setup(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Order matters: the shim is called by the framework at its own load time."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    rules = mission.triggerrules.triggers
    assert len(rules) == 3
    assert [r.comment for r in rules[:2]] == [
        "IADS — load mist_shim.lua",
        "IADS — load vendor/skynet-iads.lua",
    ]
    assert all(isinstance(r.actions[0], action.DoScriptFile) for r in rules[:2])
    assert isinstance(rules[2].actions[0], lua.InlineDoScript)


def test_the_framework_is_only_loaded_once_per_mission(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """A second net in the same mission must not re-run 117 KB of framework."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")], name="second")
    loads = [
        r for r in mission.triggerrules.triggers if r.comment.startswith("IADS — load")
    ]
    assert len(loads) == 2


def test_the_vendored_files_are_added_as_resources(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    added = list(mission.map_resource.files["DEFAULT"].values())
    assert any(p.endswith("mist_shim.lua") for p in added)
    assert any(p.endswith("skynet-iads.lua") for p in added)


# ------------------------------------------------------------------- the table
def test_a_sam_is_registered_by_group_and_an_ewr_by_unit(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Skynet takes a group for a SAM site and a unit for an EWR — different calls."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    script = setup_of(mission)
    assert 'name="SA-6 belt", ewUnit=nil' in script
    assert 'name="EWR north", ewUnit="EWR north Unit #1"' in script


def test_the_cue_percentage_and_zone_reach_the_table(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    arm_iads(
        mission,
        [
            Site(sa6, "SA-6", go_live_percent=150, engagement_zone="search"),
            Site(ewr, "EWR", role="ewr"),
        ],
    )
    script = setup_of(mission)
    assert "golive=150" in script
    assert 'zone="search"' in script


def test_point_defence_names_the_guarding_site(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, sa15: VehicleGroup
) -> None:
    arm_iads(
        mission,
        [
            Site(sa6, "SA-6", point_defence=sa15),
            Site(sa15, "SA-15"),
            Site(ewr, "EWR", role="ewr"),
        ],
    )
    assert 'pd="SA-15 guard"' in setup_of(mission)


def test_the_relay_factor_reaches_the_table(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Without it a launch masked from a site would still shut it down."""
    arm_iads(mission, [Site(sa6, "SA-6", net_relay=0.25), Site(ewr, "EWR", role="ewr")])
    assert "relay=0.250" in setup_of(mission)


def test_the_first_time_up_is_silent_unless_the_mission_asks(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """The RWR is that call; announcing it gives away an unbriefed battery."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    assert "hotText=nil" in setup_of(mission)

    loud = Mission(Caucasus())
    arm_iads(
        loud,
        [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")],
        hot_call="Magic: {label} just came up.",
    )
    assert 'hotText="Magic: SA-6 just came up."' in setup_of(loud)


def test_skynets_own_harm_identification_is_switched_off(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """The one behaviour this project rejects: reacting to the missile in flight."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    script = setup_of(mission)
    assert "harmDetection.evaluateContacts = function() return nil end" in script
    assert "setHARMDetectionChance(0)" in script


def test_debug_is_off_by_default(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    assert "local debugSwitches = nil" in setup_of(mission)

    loud = Mission(Caucasus())
    arm_iads(loud, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")], debug=True)
    assert "radarWentLive = true" in setup_of(loud)


def test_our_own_trace_follows_debug_unless_it_is_asked_for(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Two halves of the net, two logs — `debug` turns both on, `trace` only ours."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    assert "local tracing = false" in setup_of(mission)

    for kwargs in ({"debug": True}, {"trace": True}, {"debug": False, "trace": True}):
        loud = Mission(Caucasus())
        arm_iads(loud, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")], **kwargs)
        assert "local tracing = true" in setup_of(loud)

    # Skynet's own output prints on the player's screen; ours does not, so it is
    # the one a mission can leave on while the framework's stays quiet.
    quiet = Mission(Caucasus())
    arm_iads(quiet, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")], trace=True)
    assert "local debugSwitches = nil" in setup_of(quiet)

    both_off = Mission(Caucasus())
    arm_iads(
        both_off,
        [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")],
        debug=True,
        trace=False,
    )
    assert "local tracing = false" in setup_of(both_off)


# --------------------------------------------------- who may report an emission
def test_a_declared_collector_reaches_the_table(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, magic: PlaneGroup
) -> None:
    """Group name, label and reach: everything the gate needs at run time."""
    arm_iads(
        mission,
        [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")],
        listeners=[Listener(magic, "Magic", 180_000.0)],
    )
    assert 'name="Magic", label="Magic", range=180000.0' in setup_of(mission)


def test_a_plain_group_is_taken_as_a_collector_under_its_own_name(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, magic: PlaneGroup
) -> None:
    arm_iads(
        mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")], listeners=[magic]
    )
    assert 'name="Magic", label="Magic", range=250000.0' in setup_of(mission)


def test_a_net_with_no_collector_declares_none(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """An empty table is a valid net — it just reports nothing."""
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    assert "local listeners = {\n\n  }" in setup_of(mission)


def test_calls_without_a_collector_warn(
    mission: Mission,
    sa6: VehicleGroup,
    ewr: VehicleGroup,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The wording is configured here and the silence happens in Lua.

    A mission that meant those calls to be heard would otherwise only find out in
    the air, so this is the one place it can be said.
    """
    arm_iads(mission, [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")])
    assert "no listener can hear them" in capsys.readouterr().out


def test_a_silent_net_without_a_collector_does_not_warn(
    mission: Mission,
    sa6: VehicleGroup,
    ewr: VehicleGroup,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing to report and nobody to report it is consistent, not a mistake."""
    arm_iads(
        mission,
        [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")],
        down_call=None,
        up_call=None,
    )
    assert "no listener" not in capsys.readouterr().out


def test_a_collector_with_a_bad_reach_or_no_units_is_rejected(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, magic: PlaneGroup
) -> None:
    sites = [Site(sa6, "SA-6"), Site(ewr, "EWR", role="ewr")]
    with pytest.raises(ValueError, match="range_m"):
        arm_iads(mission, sites, listeners=[Listener(magic, "Magic", 0.0)])
    with pytest.raises(ValueError, match="never hear anything"):
        arm_iads(mission, sites, listeners=[Listener(PlaneGroup(6, "Empty"))])


# ------------------------------------------------------------------ validation
def test_an_empty_site_list_is_rejected(mission: Mission) -> None:
    with pytest.raises(ValueError, match="at least one site"):
        arm_iads(mission, [])


def test_an_unknown_coalition_is_rejected(mission: Mission, sa6: VehicleGroup) -> None:
    with pytest.raises(ValueError, match="blue/red"):
        arm_iads(mission, [sa6], coalition="green")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("role", "radar", "role must be"),
        ("engagement_zone", "everywhere", "engagement_zone"),
        ("autonomous", "asleep", "autonomous must be"),
        ("go_live_percent", 0, "go_live_percent"),
        ("net_relay", 1.5, "net_relay"),
    ],
)
def test_a_bad_dial_is_rejected(
    mission: Mission, sa6: VehicleGroup, field: str, value: object, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        arm_iads(mission, [Site(sa6, "SA-6", **{field: value})])  # type: ignore[arg-type]


def test_a_multi_unit_ewr_is_rejected(mission: Mission, sa6: VehicleGroup) -> None:
    """Guessing which unit is the radar registers something that never contributes."""
    with pytest.raises(ValueError, match="one radar unit"):
        arm_iads(mission, [Site(sa6, "EWR", role="ewr")])


def test_a_point_defence_outside_the_net_is_rejected(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, sa15: VehicleGroup
) -> None:
    """The framework can only guard with an element it owns."""
    with pytest.raises(ValueError, match="not itself"):
        arm_iads(
            mission,
            [Site(sa6, "SA-6", point_defence=sa15), Site(ewr, "EWR", role="ewr")],
        )


def test_a_net_with_nothing_always_on_warns(
    mission: Mission,
    sa6: VehicleGroup,
    sa15: VehicleGroup,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No early warning means nothing cues the batteries.

    structlog writes to stdout rather than through stdlib logging, so this reads
    the captured stream rather than `caplog`.
    """
    arm_iads(mission, [Site(sa6, "SA-6"), Site(sa15, "SA-15")])
    assert "no always-on radar" in capsys.readouterr().out


def test_act_as_ew_counts_as_always_on(
    mission: Mission,
    sa6: VehicleGroup,
    sa15: VehicleGroup,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A long-range battery doubling as early warning is a legitimate net."""
    arm_iads(mission, [Site(sa6, "SA-6", act_as_ew=True), Site(sa15, "SA-15")])
    assert "no always-on radar" not in capsys.readouterr().out
    assert "actAsEW=true" in setup_of(mission)


# ------------------------------------------------- against the vendored build
# ------------------------------------------------------------- shoot and scoot
def test_a_self_propelled_battery_is_given_a_hop_and_a_prepared_one_is_not(
    sa6: VehicleGroup, sa15: VehicleGroup
) -> None:
    """The table decides, because pydcs carries no mobility data at all.

    A Kub or a Tor shoots, drives and shoots again; an S-125 fires from built
    revetments and an S-300PS is march-ordered in minutes, so neither moves
    inside the window a HARM leaves.
    """
    s125 = _group(
        9,
        "S-125 battery",
        AirDefence.P_19_s_125_sr,
        AirDefence.Snr_s_125_tr,
        AirDefence.X_5p73_s_125_ln,
    )
    assert iads_jockey_m(Site(sa6, "SA-6")) > 0.0
    assert iads_jockey_m(Site(sa15, "SA-15")) > 0.0
    assert iads_jockey_m(Site(s125, "S-125")) == 0.0


def test_a_search_radar_never_scoots(ewr: VehicleGroup) -> None:
    """An antenna on a mast has no firing position to leave and hours of work."""
    assert iads_jockey_m(Site(ewr, "EWR", role="ewr")) == 0.0
    assert iads_jockey_m(Site(ewr, "EWR", role="ewr", jockey_m=120.0)) == 120.0


def test_a_mission_can_override_the_table_in_both_directions(
    sa6: VehicleGroup,
) -> None:
    assert iads_jockey_m(Site(sa6, "SA-6", jockey_m=0.0)) == 0.0
    assert iads_jockey_m(Site(sa6, "SA-6", jockey_m=400.0)) == 400.0


def test_a_hop_past_the_briefed_ring_is_rejected(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """The bound that keeps the F10 ring and the HSD cartridge true."""
    with pytest.raises(ValueError, match="jockey_m"):
        arm_iads(
            mission,
            [Site(sa6, "SA-6", jockey_m=5_000.0), Site(ewr, "EWR", role="ewr")],
        )


def test_infantry_in_a_site_forbids_the_hop(
    mission: Mission, ewr: VehicleGroup
) -> None:
    """A DCS group moves at its slowest member, and a walking battery has not moved.

    pydcs's own `sa11_site` template ships an `Infantry AK` with the battery,
    which is exactly how a mission acquires one without noticing.
    """
    buk = _group(
        7,
        "SA-11 belt",
        AirDefence.SA_11_Buk_SR_9S18M1,
        AirDefence.SA_11_Buk_LN_9A310M1,
        Russia.Vehicle.Infantry.Infantry_AK,
    )
    assert iads_jockey_m(Site(buk, "SA-11")) == 0.0
    with pytest.raises(ValueError, match="Infantry AK"):
        arm_iads(
            mission,
            [Site(buk, "SA-11", jockey_m=250.0), Site(ewr, "EWR", role="ewr")],
        )


def test_an_optically_guided_launcher_forbids_the_hop(
    mission: Mission, ewr: VehicleGroup
) -> None:
    """The jockey hands the group's AI back, and a Strela would go on fighting."""
    mixed = _group(8, "mixed section", AirDefence.Kub_1S91_str, AirDefence.Strela_10M3)
    with pytest.raises(ValueError, match="Strela-10M3"):
        arm_iads(
            mission,
            [Site(mixed, "mixed", jockey_m=250.0), Site(ewr, "EWR", role="ewr")],
        )


def test_an_unlisted_system_asked_to_move_warns_but_builds(
    mission: Mission, ewr: VehicleGroup, capsys: pytest.CaptureFixture[str]
) -> None:
    """The table is incomplete by construction, so an override is not an error."""
    rapier = _group(6, "Rapier", AirDefence.Rapier_fsa_launcher)
    arm_iads(
        mission,
        [Site(rapier, "Rapier", jockey_m=200.0), Site(ewr, "EWR", role="ewr")],
    )
    assert "unknown mobility" in capsys.readouterr().out


def test_the_hop_distance_reaches_the_table(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Per site, since it is the difference between a Kub and an S-125."""
    s125 = _group(
        9, "S-125 battery", AirDefence.P_19_s_125_sr, AirDefence.X_5p73_s_125_ln
    )
    arm_iads(
        mission, [Site(sa6, "SA-6"), Site(s125, "S-125"), Site(ewr, "EWR", role="ewr")]
    )
    rows = {
        m.group(1): float(m.group(2))
        for m in re.finditer(r'name="([^"]+)".*?jockey=([\d.]+)', setup_of(mission))
    }
    assert rows["SA-6 belt"] > 0.0
    assert rows["S-125 battery"] == 0.0
    assert rows["EWR north"] == 0.0


def test_setup_only_calls_api_the_vendored_build_has(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup, sa15: VehicleGroup
) -> None:
    """Every Skynet name the setup script uses must exist in the pinned framework.

    This is the test that makes a version bump safe: drop in a newer build, and a
    renamed method fails here instead of in the air.
    """
    arm_iads(
        mission,
        [
            Site(sa6, "SA-6", point_defence=sa15),
            Site(sa15, "SA-15"),
            Site(ewr, "EWR", role="ewr"),
        ],
    )
    framework = lua.source("vendor/skynet-iads.lua")
    setup = setup_of(mission)

    # Methods called on the IADS or on an element, and the class constants used.
    called = set(
        re.findall(r"\b(?:iads|el|this|guard|site\.el):([A-Za-z_]\w*)\(", setup)
    )
    called |= set(re.findall(r"\bSkynetIADS:(\w+)\(", setup))
    assert called, "no Skynet calls found — the extraction regex has rotted"
    for method in sorted(called):
        assert f":{method}(" in framework, f"Skynet has no method {method}"

    for constant in sorted(
        set(re.findall(r"SkynetIADSAbstractRadarElement\.([A-Z_]+)", setup))
    ):
        assert constant in framework, f"Skynet has no constant {constant}"

    # Fields and the class-level function the suppression path reaches into.
    for field in ("harmSilenceID", "harmShutdownTime", "aiState"):
        assert field in framework, f"Skynet no longer has field {field}"
    assert "SkynetIADSAbstractRadarElement.finishHarmDefence" in framework
    assert "harmDetection" in framework


def test_the_shim_covers_every_mist_call_the_framework_makes() -> None:
    """A framework bump that reaches for a new MIST function must fail here.

    Otherwise it fails at mission start with a nil-index error, or worse, inside
    a scheduled task where nobody sees it.
    """
    framework = lua.source("vendor/skynet-iads.lua")
    shim = lua.source("mist_shim.lua")
    used = set(re.findall(r"\bmist\.((?:\w+\.)?\w+)", framework))
    assert used, "no mist calls found — the extraction regex has rotted"
    for name in sorted(used):
        leaf = name.rsplit(".", 1)[-1]
        assert leaf in shim, f"mist_shim.lua does not provide mist.{name}"


# ---------------------------------------------------------- emission discipline
def test_the_look_a_crew_takes_comes_off_its_skill(
    sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    """Twenty seconds is what the *best* crews of a real campaign managed.

    So it is the bottom of the élite band rather than a number everybody gets, and
    a conscript battery is the one a HARM finds still radiating.
    """
    from dcs.unit import Skill

    from dcs_mission_creator.core.iads import emission_limit_s

    for unit in sa6.units:
        unit.skill = Skill.Excellent
    assert emission_limit_s(Site(sa6, "SA-6")) == (20.0, 35.0)

    for unit in sa6.units:
        unit.skill = Skill.Average
    conscript = emission_limit_s(Site(sa6, "SA-6"))
    assert conscript[0] >= 90.0, conscript

    # The discipline is set by whoever runs the site, not by its worst driver.
    sa6.units[0].skill = Skill.High
    assert emission_limit_s(Site(sa6, "SA-6")) == (30.0, 55.0)

    # A search radar's job is to search; a net working in bursts throughout has
    # nothing to hand a track to.
    assert emission_limit_s(Site(ewr, "EWR", role="ewr")) == (0.0, 0.0)
    assert emission_limit_s(Site(sa6, "SA-6", act_as_ew=True)) == (0.0, 0.0)

    # And a mission can always say it outright.
    assert emission_limit_s(Site(sa6, "SA-6", emission_limit_s=(15.0, 15.0))) == (
        15.0,
        15.0,
    )


def test_the_generated_setup_carries_the_look_and_the_pause(
    mission: Mission, sa6: VehicleGroup, ewr: VehicleGroup
) -> None:
    arm_iads(
        mission,
        [
            Site(
                sa6,
                "SA-6",
                emission_limit_s=(25.0, 40.0),
                emission_pause_s=(50.0, 90.0),
            ),
            Site(ewr, "EWR", role="ewr"),
        ],
    )
    script = setup_of(mission)
    assert "emitMin=25.0, emitMax=40.0" in script
    assert "pauseMin=50.0, pauseMax=90.0" in script
    # The early-warning radar stays up: its exemption has to survive into the Lua.
    assert "emitMin=0.0, emitMax=0.0" in script
