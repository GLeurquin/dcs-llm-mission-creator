"""Integrated air defence: Skynet for the net, our own model for HARM reaction.

Out of the box a DCS SAM does two unrealistic things. It sits with its
acquisition radar turning from mission start, so the player's RWR is full before
anyone has detected him. And it keeps radiating while a HARM rides the beam all
the way in, so every anti-radiation shot is a guaranteed kill and SEAD
degenerates into a shooting gallery.

`arm_iads` fixes both by putting two things in the `.miz` and wiring them
together. The division of labour is the whole design:

**Skynet-IADS owns when a site radiates.** Vendored under
`core/lua/vendor/` (Apache-2.0, see the README there), it knows each system's
real envelopes, analyses every launcher and radar against them individually,
cues sites off whichever radars are currently live, tracks ammunition state, and
degrades the net when links or power go down. Writing that from scratch is a lot
of code, and re-deriving each system's reach by hand is a lot of numbers to get
wrong. Per site the mission states `go_live_percent` — a fraction of the
system's *own* reach, so an SA-8 and an S-200 are configured the same way
without anyone looking up either envelope — plus `engagement_zone`
(kill zone or full search range), `act_as_ew` for a radar that stays up
throughout, `autonomous` for what it does when the net is cut, and
`point_defence` for a battery standing guard over another.

The consequence to design missions around, and the one that is easy to get
backwards: killing the early-warning chain does **not** switch the batteries off.
A site with no live parent radar left goes *autonomous*, and with the default
`autonomous="ai"` that means it searches on its own — radiating continuously from
then on. Which is doctrine, and a real trade for the player rather than a win:
every belt becomes an emitter that can be found and shot, and nobody is being
handed a hand-off any more, but nothing is dark any more either. Set
`autonomous="dark"` for a site that should shut down instead, and be aware that
doing it to a whole net makes two HARMs on the search radars end the SAM threat.

**We own what happens when somebody shoots at a site.** Skynet identifies the
missile in flight — over 800 kt with few flight-path changes — and darkens
radars ahead of its track. That is rejected here, and its HARM detection is
switched off at setup: an anti-radiation missile emits nothing, warns nobody and
cannot be seen coming, so reacting to the round itself hands a crew knowledge of
a passive weapon they have no way to have. What a crew *can* see is the shooter.
So a launch only reaches sites that could observe it:

- **Somebody has to have seen it.** A site reacts at its own `probability` if it
  has line of sight to the launch point. If the launch was masked from it, it
  reacts at `probability * net_relay`, and only if some *other* radiating site in
  reach did see it — the call still travels down the net. Mask the launch from
  the whole net and nobody reacts, which makes a lofted shot from behind a ridge
  a real tactic rather than a cosmetic one.
- **Not every crew reacts.** `probability` — a green crew keeps radiating and
  eats the missile.
- **Reaction takes time.** `delay_s` seconds pass between launch and emissions
  drop — tens of seconds, the same order as a HARM's time of flight, because the
  shot has to be spotted, called down the net, believed and acted on. So the
  shooter's range at launch is what decides the duel, and a HARM fired from
  close in still kills. The draw is triangular within the band rather than flat,
  so the middle of it is the common case.
- **The site comes back.** `shutdown_s` later it is released — minutes, not
  Skynet's cap of 180 s past impact, so a HARM buys the package a real working
  gap. Released to *cold*, not hot: it re-radiates only if there is still
  something worth shooting at.
- **Repeat fire makes crews shy.** A second launch while a site is dark extends
  the window instead of restarting it.
- **Range gates the reaction.** Only sites within `react_range_m` of the shooter
  hear about the launch; a HARM at one end of the map does not darken a theater.
- **A cold site is not being shot at.** No radar of its own is up, so the round
  was not aimed at it and its crew has no reason to think otherwise — it is
  skipped, and stays silent.
- **A dead site says nothing.** A site whose radars are gone is destroyed, not
  suppressed. The check is a live radar unit, not a live *group*: DCS keeps the
  group alive while a launcher or the command post stands, and gating on that had
  a site the player had just killed announce that it was going dark and then
  that it was radiating again.

Only radar-guided sites belong in the list. Optically/IR-guided SHORAD (SA-13,
MANPADS) has nothing to shut down, and putting a mixed convoy in here would make
the whole column hold fire on every HARM shot.

Design rule (mirrors `core/air_defense.py` / `core/tasking.py`): built pydcs
groups in, triggers out. The mission owns the wording of the radio calls; pass a
`VoiceSynth` to get them spoken as well as printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence, Union

import structlog
from dcs import action, triggers

from dcs_mission_creator.core import lua

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import VehicleGroup

    from dcs_mission_creator.core.tts import VoiceSynth

log = structlog.get_logger(__name__)

_SIDE = {"blue": "coalition.side.BLUE", "red": "coalition.side.RED"}
_ROLES = ("sam", "ewr")
_ZONES = ("kill", "search")
_AUTONOMY = ("ai", "dark")

# Loaded into the mission in this order, ahead of the generated setup script.
# The shim first because the framework calls it at load time; see
# `core/lua/vendor/README.md` for why it is a shim and not MIST itself.
_PRELUDE = ("mist_shim.lua", "vendor/skynet-iads.lua")
_SCRIPT = "iads.lua"

# Marker on the Mission so two calls do not load the framework twice.
_LOADED = "_dcsmc_iads_prelude"


@dataclass
class Site:
    """One radar site in the net: when it radiates, and how it takes a shot.

    `label` is what the radio call names ("SA-6", "EWR"). `role` picks the
    registration: a `"sam"` site is a group with launchers, an `"ewr"` is a bare
    search radar — different classes inside the framework, so a mission has to
    say which. An `"ewr"` group must hold exactly one unit, since Skynet
    registers early warning per *unit*.

    **Cueing (the framework's half).** `go_live_percent` is the fraction of this
    system's own reach at which it switches on — the point of expressing it as a
    percentage is that nobody has to look the envelope up, and over 100 brings a
    long-range battery up before the target is inside launch range, which is what
    a real one does. `engagement_zone` chooses whether that reach means the kill
    zone or the full search range. `act_as_ew` keeps a site radiating throughout,
    which is what an early-warning radar is for and what a long-range battery
    doubling as one does. **Something in the net must radiate** — an all-cued net
    has nothing to hand tracks down and every battery is left with only its own
    line of sight. `autonomous` is the behaviour when the net is cut: `"ai"`
    leaves DCS to it, `"dark"` shuts the site down. `point_defence` is another
    site in the same list standing guard over this one, which is what keeps a
    battery alive under HARM fire.

    **Reaction (ours).** `probability` is the chance this crew acts on a launch
    it can see, `delay_s` the recognition delay and `shutdown_s` how long it then
    stays dark, both drawn per event within the band (triangular, so the middle
    of the band is the common case). `react_range_m` is how far from the site a
    launch still gets passed down the net. `net_relay` is how much of
    `probability` survives being told about a launch the site could not see
    itself — `0.0` makes terrain masking absolute, `1.0` ignores line of sight
    entirely.

    `delay_s` defaults to tens of seconds, not single digits: a site gets no
    launch warning, so the shot has to be seen, called down the net and acted
    on. Anything shorter darkens the radar while the missile is still in the
    first third of its flight and no HARM ever connects.
    """

    group: "VehicleGroup"
    label: str
    role: str = "sam"
    # -- cueing, handed to the framework
    go_live_percent: Optional[int] = None
    engagement_zone: str = "kill"
    act_as_ew: bool = False
    autonomous: str = "ai"
    point_defence: Optional["VehicleGroup"] = None
    # -- reaction to anti-radiation fire, ours
    probability: float = 0.85
    delay_s: tuple[float, float] = (20.0, 60.0)
    shutdown_s: tuple[float, float] = (240.0, 360.0)
    react_range_m: float = 60_000.0
    net_relay: float = 0.5


def _ewr_unit_name(site: Site) -> str:
    """The unit Skynet should register as an early-warning radar.

    Deliberately strict rather than reaching for `units[0]`: picking the wrong
    unit registers something that is not a radar and the EWR silently never
    contributes to the net.
    """
    units = site.group.units
    if len(units) != 1:
        raise ValueError(
            f"{site.label}: an ewr site registers one radar unit, but "
            f"{site.group.name!r} holds {len(units)} — split it, or pass the "
            f"radar's own group"
        )
    return units[0].name


def _validate(sites: Sequence[Site]) -> None:
    names = {s.group.name for s in sites}
    for s in sites:
        if s.role not in _ROLES:
            raise ValueError(f"{s.label}: role must be one of {_ROLES}, got {s.role!r}")
        if s.engagement_zone not in _ZONES:
            raise ValueError(f"{s.label}: engagement_zone must be one of {_ZONES}")
        if s.autonomous not in _AUTONOMY:
            raise ValueError(f"{s.label}: autonomous must be one of {_AUTONOMY}")
        if s.go_live_percent is not None and s.go_live_percent <= 0:
            raise ValueError(f"{s.label}: go_live_percent must be positive")
        if not 0.0 <= s.net_relay <= 1.0:
            raise ValueError(f"{s.label}: net_relay must be within 0..1")
        if s.point_defence is not None and s.point_defence.name not in names:
            raise ValueError(
                f"{s.label}: point defence {s.point_defence.name!r} is not itself "
                f"in the site list, so the net has no element to wire it to"
            )
        if s.role == "ewr":
            _ewr_unit_name(s)
    if not any(s.role == "ewr" or s.act_as_ew for s in sites):
        # Nothing radiating to hand tracks down: every battery is reduced to its
        # own line of sight and the mission loses the net it thinks it has.
        log.warning(
            "no always-on radar in the IADS — nothing will cue the batteries",
            sites=[s.label for s in sites],
        )


def _load_prelude(m: "Mission") -> list[triggers.TriggerStart]:
    """Add the shim and the framework to the mission, once, as resource files.

    They go in as files rather than inline Lua: the framework alone is 117 KB,
    and `a_do_script_file` is what the Mission Editor itself uses for a script
    of that size. `lua.InlineDoScript` stays the rule for anything *generated*.
    """
    if getattr(m, _LOADED, False):
        return []
    rules = []
    for name in _PRELUDE:
        key = m.map_resource.add_resource_file(str(lua.file_path(name)))
        rule = triggers.TriggerStart(comment=f"IADS — load {name}")
        rule.add_action(action.DoScriptFile(key))
        m.triggerrules.triggers.append(rule)
        rules.append(rule)
    setattr(m, _LOADED, True)
    return rules


def _row(
    site: Site,
    *,
    down: Optional[str],
    up: Optional[str],
    hot: Optional[str],
    sounds: tuple[Optional[str], Optional[str], Optional[str]],
) -> str:
    down_sound, up_sound, hot_sound = sounds
    return (
        "    {{name={name}, ewUnit={ew}, prob={prob:.3f}, delayMin={dmin:.1f}, "
        "delayMax={dmax:.1f}, downMin={smin:.1f}, downMax={smax:.1f}, "
        "range={rng:.1f}, relay={relay:.3f}, golive={golive}, zone={zone}, "
        "actAsEW={ew_flag}, autonomous={auto}, pd={pd}, "
        "downText={dtext}, upText={utext}, hotText={htext}, "
        "downSound={dsound}, upSound={usound}, hotSound={hsound}}},".format(
            name=lua.quote(site.group.name),
            ew=lua.quote(_ewr_unit_name(site) if site.role == "ewr" else None),
            prob=site.probability,
            dmin=site.delay_s[0],
            dmax=site.delay_s[1],
            smin=site.shutdown_s[0],
            smax=site.shutdown_s[1],
            rng=site.react_range_m,
            relay=site.net_relay,
            golive="nil" if site.go_live_percent is None else str(site.go_live_percent),
            zone=lua.quote(site.engagement_zone),
            ew_flag="true" if site.act_as_ew else "false",
            auto=lua.quote(site.autonomous),
            pd=lua.quote(site.point_defence.name if site.point_defence else None),
            dtext=lua.quote(down),
            utext=lua.quote(up),
            htext=lua.quote(hot),
            dsound=lua.quote(down_sound),
            usound=lua.quote(up_sound),
            hsound=lua.quote(hot_sound),
        )
    )


def arm_iads(
    m: "Mission",
    sites: Sequence[Union["VehicleGroup", Site]],
    *,
    voice: Optional["VoiceSynth"] = None,
    coalition: str = "blue",
    name: str = "IADS",
    down_call: Optional[str] = "Magic: {label} has ceased emissions, site is dark.",
    up_call: Optional[str] = "Magic: {label} radar is radiating again.",
    hot_call: Optional[str] = None,
    announce_spacing_s: float = 7.0,
    update_interval_s: float = 5.0,
    debug: bool = False,
    comment: str = "IADS — cueing, and reaction to observed anti-radiation fire",
) -> triggers.TriggerStart:
    """Build an integrated air-defence net out of `sites`.

    Pass plain `VehicleGroup`s for default behaviour or `Site` entries to tune
    cueing and reaction per site. Adds three mission-start triggers the first
    time it is called — the MIST shim, the vendored framework, and the generated
    setup — and returns the setup trigger.

    `down_call`, `up_call` and `hot_call` are `{label}` templates announced to
    `coalition`; with a `VoiceSynth` the same words are also rendered and played
    from the script. `hot_call` fires the *first* time a site comes up and
    defaults to nothing: the player's RWR is that call, and announcing it would
    give away a battery the briefing deliberately left off the map. A site coming
    back after being shot off the air uses `up_call`, since that one is news; a
    site going quiet because the package left says nothing at all, because
    `down_call` means "SEAD worked" and must not be borrowed. Every site gets its
    own call — a shot that darkens a whole belt queues them `announce_spacing_s`
    apart rather than dropping the later ones.

    `update_interval_s` is the framework's go-live cycle. `debug` turns on its
    own in-game and log output, which names every site as it comes up and goes
    down — indispensable while tuning a net, far too chatty to ship.
    """
    if coalition not in _SIDE:
        raise ValueError(f"coalition must be blue/red, got {coalition!r}")
    entries = [s if isinstance(s, Site) else Site(s, s.name) for s in sites]
    if not entries:
        raise ValueError("arm_iads needs at least one site")
    _validate(entries)

    prelude = _load_prelude(m)

    rows: list[str] = []
    for site in entries:
        down = down_call.format(label=site.label) if down_call else None
        up = up_call.format(label=site.label) if up_call else None
        hot = hot_call.format(label=site.label) if hot_call else None
        sounds = (
            voice.register(m, down) if voice and down else None,
            voice.register(m, up) if voice and up else None,
            voice.register(m, hot) if voice and hot else None,
        )
        rows.append(_row(site, down=down, up=up, hot=hot, sounds=sounds))

    # Skynet's switches are individually named, so "on" has to be spelled out.
    # These are the ones that say what the net is doing rather than dumping
    # per-unit telemetry every cycle.
    debug_table = (
        "{radarWentLive = true, radarWentDark = true, contacts = true, "
        "harmDefence = true, IADSStatus = true, addedSAMSite = true, "
        "addedEWRadar = true, warnings = true}"
        if debug
        else "nil"
    )

    script = lua.render(
        _SCRIPT,
        SITES="\n".join(rows),
        SIDE=_SIDE[coalition],
        SPACING=f"{announce_spacing_s:.1f}",
        UPDATE=f"{update_interval_s:.1f}",
        IADSNAME=lua.quote(name),
        DEBUG=debug_table,
    )
    rule = triggers.TriggerStart(comment=comment)
    rule.add_action(lua.InlineDoScript(script))
    m.triggerrules.triggers.append(rule)
    log.debug(
        "armed IADS",
        name=name,
        loaded_framework=bool(prelude),
        sams=[s.group.name for s in entries if s.role == "sam"],
        ewrs=[s.group.name for s in entries if s.role == "ewr"],
    )
    return rule
