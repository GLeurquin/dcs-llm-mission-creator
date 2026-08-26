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
- **Reaction takes time, and how much depends on the range.** `delay_s` seconds
  pass between launch and emissions drop — tens of seconds, the same order as a
  HARM's time of flight, because the shot has to be spotted, called down the net,
  believed and acted on. The band is the one at the *edge* of `react_range_m` and
  tightens as the launch gets closer, because a launch a few kilometres off is a
  motor and a smoke trail in plain sight, while one at sixty is a report; a
  launch the site could not see itself stays on the slower reading. So the
  shooter's range at launch still decides the duel — a HARM fired from well
  inside the missile engagement zone kills — but the crew is no longer given the
  same half-minute to notice a shot overhead as one on the horizon. The draw is
  triangular within the band rather than flat, so the middle of it is the common
  case.
- **A battery that can drive, drives — and the hop that matters happens before
  the shot.** Going dark saves the system, not the vehicle: a HARM remembers
  where the emitter was and keeps flying there, and one launched in POS or EOM
  mode was aimed at a place to begin with, so shutting down does nothing about
  it at all. What defeats a pre-planned shot is the coordinates being stale, so a
  self-propelled site that has spent `scoot_after_s` on the air relocates once it
  goes quiet — it must assume it was fixed while it emitted, which is the
  doctrine the vehicle exists for. It also displaces reactively when a launch
  puts it dark (`jockey_m`), and both hops are bounded from where it started so
  it can never leave the envelope the briefing drew. The reactive one does not
  defeat the missile, it grades the duel: a shot from 40 km arrives on empty
  ground, one from 15 km arrives before the battery has moved at all. A prepared
  site in revetments has no such answer and stays put.
- **The site comes back.** `shutdown_s` later it is released — minutes, not
  Skynet's cap of 180 s past impact, so a HARM buys the package a real working
  gap. Released to *cold*, not hot: it re-radiates only if there is still
  something worth shooting at. If it displaced, it comes back up from the new
  position — an equipment crew goes to an alternate site, it does not drive
  home.
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

**Nobody reports intel nobody collected.** A radar starting or stopping is an
ESM observation, and the radio calls are gated on somebody having been in a
position to make it. `listeners` is the friendly groups that could — a Rivet
Joint on a track, an AWACS with ESM, a ground collection site — and a site's
emissions change is announced only while one of them is alive, within its own
reach, and in line of sight of the emitter. Declare none and the net says
nothing at all, which is the honest default rather than a broken one: without a
collector, "the SA-6 has ceased emissions" is the mission reading its own
trigger state out to the player, and that is what the briefing rules forbid
everywhere else. It is also a live condition, not a build-time one — shoot the
collector down, or drive it off station, and the calls stop.

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
    from typing import Any

    from dcs.mission import Mission
    from dcs.unitgroup import Group, VehicleGroup

    from dcs_mission_creator.core.tts import VoiceSynth

    # A collector can be a flight, a ship or a ground site, so this is the
    # generic pydcs group rather than any one of its subclasses.
    AnyGroup = Group[Any, Any]

log = structlog.get_logger(__name__)

_SIDE = {"blue": "coalition.side.BLUE", "red": "coalition.side.RED"}

# How long a crew is willing to sit on the air in one look, by how well trained
# it is. "Radars were also forced to operate for only 20 seconds or less to avoid
# destruction by HARMs" (Desert Storm, and again over Yugoslavia in 1999, where
# the standing rule was no more than about forty seconds from one position) — so
# twenty seconds is what the *best* crews managed, and it is the bottom of the
# élite band here rather than a number everybody gets. A conscript battery sits
# there: its band is long enough that the discipline effectively never fires, and
# it dies to the HARM that a drilled crew two ridges away would have been off the
# air for. `Skill.Random` is read as `Good` because the mission cannot know what
# it rolled, and `Player`/`Client` never appear on a radar group.
_EMISSION_BY_SKILL: dict[str, tuple[float, float]] = {
    "Excellent": (20.0, 35.0),
    "High": (30.0, 55.0),
    "Good": (45.0, 80.0),
    "Random": (45.0, 80.0),
    "Average": (90.0, 150.0),
}
_EMISSION_DEFAULT = _EMISSION_BY_SKILL["Good"]
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

# -- shoot and scoot ---------------------------------------------------------
#
# Going dark saves the battery, not the vehicle. A HARM remembers where the
# emitter was and keeps flying there, so a site that only stops radiating still
# loses its radar to a round already in the air. What answers that is moving:
# a self-propelled battery leaves the point the missile was aimed at.
#
# How far. 250 m is about 45 s of cross-country driving, which is the same
# order as the tail of a HARM's flight — enough that a long shot arrives on
# empty ground and a close one still kills, so the shooter's range keeps
# deciding the duel. The ceiling is the honesty bound: `PlanOverlay.threat`
# offsets an estimated ring by 2 km at `trained` and `core/dtc.py` builds the
# cockpit ring from that same return, so a displacement well inside it leaves
# the map, the cartridge and the ground truth saying one thing.
_JOCKEY_M_DEFAULT = 250.0
_JOCKEY_M_MAX = 500.0

# Systems that can shoot, stop, drive a few hundred metres and shoot again
# inside a minute — plus the support vehicles that come with them, which pace
# the group but do not decide anything. pydcs carries no mobility data
# (`AirDefence.*` has `threat_range` and nothing else), so this is a table, the
# same as `air_defense.py`'s site catalogue. Absent from it means no jockey:
# an S-75 or S-125 fires from built revetments, an S-300PS is march-ordered in
# minutes rather than seconds, and a 55G6 needs hours.
_MOBILE_TYPES = frozenset(
    {
        "Kub 1S91 str",  # SA-6 Straight Flush
        "Kub 2P25 ln",
        "Osa 9A33 ln",  # SA-8, radar and rails on one hull
        "SA-11 Buk SR 9S18M1",  # SA-11 Snow Drift / Fire Dome
        "SA-11 Buk LN 9A310M1",
        "SA-11 Buk CC 9S470M1",
        "Tor 9A331",  # SA-15
        "HQ-7_STR_SP",  # HQ-7B, self-propelled variant
        "HQ-7_LN_SP",
        "Ural-375",  # rearm truck, shipped with pydcs's SA-6 template
        "Ural-375 PBU",
    }
)

# Present in a site, these forbid a jockey even when a mission asks for one.
# Infantry walks, and a DCS group moves at its slowest member — a battery that
# displaces at 2 m/s has not displaced. An optically guided launcher is worse:
# the jockey hands the group's AI back (see `iads.lua`), and a Strela or a
# Tunguska would go on fighting from a site the mission believes is suppressed.
_NO_JOCKEY_TYPES = frozenset(
    {
        "Infantry AK",
        "Infantry AK ver2",
        "Infantry AK ver3",
        "Infantry AK Ins",
        "Paratrooper AKS-74",
        "Soldier M4",
        "Soldier M4 GRG",
        "Strela-10M3",
        "Strela-1 9P31",
        "2S6 Tunguska",
    }
)


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

    `scoot_after_s` is the other half of shoot-and-scoot, and the half that
    answers a *pre-planned* shot. An anti-radiation missile in POS or EOM mode is
    aimed at a place, so it flies to the coordinates whether anything is
    radiating there or not: going dark saves nothing, and only the coordinates
    being stale does. So a battery that has spent this long on the air since it
    last moved relocates the next time it goes quiet — it has to assume it was
    fixed while it emitted. Time is accumulated across separate stretches,
    because a cued site flaps on and off at the framework's go-live cycle. `0.0`
    switches it off and leaves the site reacting to launches only. The default is
    the doctrine of the only battery ever to shoot down a stealth aircraft: in
    1999 Dani's standing rule was never to radiate from one position for more
    than forty seconds.

    `emission_limit_s` is how long the crew is willing to sit on the air in one
    look, and `emission_pause_s` how long it stays quiet between looks. Left
    `None` the limit comes off the group's own DCS `Skill` (`_EMISSION_BY_SKILL`),
    which is the point: twenty seconds is what the *best* crews of a real SEAD
    campaign managed, so an élite battery works in short looks and a conscript one
    sits there and eats the missile. The limit never refuses an engagement — the
    clock is held while missiles are in flight or a target is inside the launch
    envelope — so a disciplined site still shoots, it just does not idle on the
    air waiting to be shot at. An early-warning radar and any `act_as_ew` site are
    exempt unless a band is given explicitly: their job is to search, and a net
    where everything works in bursts has nothing to hand a track to. A band of
    `(0.0, 0.0)` switches it off.

    `jockey_m` is how far the battery drives when it goes dark — shoot and
    scoot, bounded from where it started rather than from wherever the last hop
    ended, so four HARMs in a sortie cannot walk it out of the envelope the
    briefing drew. Leave it `None` and the system decides: every unit in
    `_MOBILE_TYPES` earns the default, anything else stays put, which is the
    difference between a Kub and an S-125 in prepared revetments. `0.0` refuses
    it outright, and a number overrides the table for a system it does not list.
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
    jockey_m: Optional[float] = None
    scoot_after_s: float = 45.0
    emission_limit_s: Optional[tuple[float, float]] = None
    emission_pause_s: tuple[float, float] = (40.0, 80.0)


# -- who may know a radar changed state -------------------------------------
#
# A passive receiver against a ground search radar is horizon-limited rather
# than power-limited: the emitter is putting out megawatts and the collector
# only has to hear it, so the reach of an ESM platform at altitude is a
# geometry problem measured in hundreds of kilometres. 250 km sits inside the
# radio horizon of a jet at 9 km (~350 km) deliberately, since the receiver
# still has to resolve the emitter out of the noise floor rather than merely
# have a straight line to it, and DCS models no curvature to be limited by.
# What actually bites at this reach is the other two conditions — terrain, and
# whether the collector is still alive and still on station.
_LISTENER_RANGE_M_DEFAULT = 250_000.0


@dataclass
class Listener:
    """A friendly collector: the reason the net's emissions calls can be made.

    `group` is any pydcs group with a receiver on it — the ELINT track, the
    AWACS, a ground site. It is read live, so a collector that is shot down, has
    not spawned yet (a client slot nobody is in) or has left the area stops
    carrying the reporting, and the calls go quiet without the mission having to
    arrange it.

    `range_m` is that receiver's reach; `label` is what the trace names it, and
    defaults to the group name. Line of sight to the emitter is always required
    — an ESM platform hears an antenna it can see, and a site behind a ridge is
    the case where the reach hardly matters.

    A mission *may* list the player's own flight, and it is not wrong: the strobe
    dropping off the RWR is exactly this observation. It is mostly redundant with
    what the player can already see, so the useful listener is the support asset
    the briefing named.
    """

    group: "AnyGroup"
    label: Optional[str] = None
    range_m: float = _LISTENER_RANGE_M_DEFAULT

    @property
    def named(self) -> str:
        return self.label or self.group.name


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


def jockey_m(site: Site) -> float:
    """How far this site displaces on suppression, table or explicit.

    Public because it is the honest answer to "will this one move?", and a
    mission tuning a SEAD problem should be able to ask without reading the
    table.
    """
    if site.jockey_m is not None:
        return site.jockey_m
    if site.role == "ewr":
        # A search radar is an antenna on a mast, not a firing position. It has
        # nothing to scoot from and hours of work to move.
        return 0.0
    types = {u.type for u in site.group.units}
    return _JOCKEY_M_DEFAULT if types <= _MOBILE_TYPES else 0.0


def emission_limit_s(site: Site) -> tuple[float, float]:
    """How long this crew will sit on the air in one look, table or explicit.

    Public for the same reason as `jockey_m`: a mission tuning a SEAD problem
    should be able to ask what discipline a battery has without reading the
    table. The skill taken is the *best* in the group — the discipline is set by
    whoever is running the site, not by its worst-trained driver — and an
    early-warning or `act_as_ew` radar is exempt, since a net whose search
    coverage works in bursts has nothing to hand a track to.
    """
    if site.emission_limit_s is not None:
        return site.emission_limit_s
    if site.role == "ewr" or site.act_as_ew:
        return (0.0, 0.0)
    order = list(_EMISSION_BY_SKILL)
    best = min(
        (u.skill.value for u in site.group.units if u.skill is not None),
        key=lambda name: order.index(name) if name in order else len(order),
        default=None,
    )
    return _EMISSION_BY_SKILL.get(best or "", _EMISSION_DEFAULT)


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
        if s.jockey_m is not None and not 0.0 <= s.jockey_m <= _JOCKEY_M_MAX:
            raise ValueError(
                f"{s.label}: jockey_m must be within 0..{_JOCKEY_M_MAX:.0f} m — "
                f"further than that and the site leaves the ring the briefing "
                f"drew around it"
            )
        if jockey_m(s) > 0.0:
            types = {u.type for u in s.group.units}
            blocked = sorted(types & _NO_JOCKEY_TYPES)
            if blocked:
                raise ValueError(
                    f"{s.label}: {', '.join(blocked)} cannot be in a site that "
                    f"displaces — infantry paces the group to a walk, and an "
                    f"optically guided launcher keeps fighting once the jockey "
                    f"hands the group's AI back"
                )
            unlisted = sorted(types - _MOBILE_TYPES)
            if unlisted:
                log.warning(
                    "site asked to displace holds units of unknown mobility",
                    site=s.label,
                    types=unlisted,
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


def _validate_listeners(listeners: Sequence[Listener]) -> None:
    for who in listeners:
        if who.range_m <= 0.0:
            raise ValueError(f"{who.named}: range_m must be positive")
        if not who.group.units:
            raise ValueError(
                f"{who.named}: a collector with no units can never hear anything"
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
        "range={rng:.1f}, relay={relay:.3f}, jockey={jockey:.1f}, "
        "scootAfter={scoot:.1f}, emitMin={emin:.1f}, emitMax={emax:.1f}, "
        "pauseMin={pmin:.1f}, pauseMax={pmax:.1f}, "
        "golive={golive}, zone={zone}, "
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
            jockey=jockey_m(site),
            scoot=site.scoot_after_s,
            emin=emission_limit_s(site)[0],
            emax=emission_limit_s(site)[1],
            pmin=site.emission_pause_s[0],
            pmax=site.emission_pause_s[1],
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


def _listener_row(who: Listener) -> str:
    return "    {{name={name}, label={label}, range={rng:.1f}}},".format(
        name=lua.quote(who.group.name),
        label=lua.quote(who.named),
        rng=who.range_m,
    )


def arm_iads(
    m: "Mission",
    sites: Sequence[Union["VehicleGroup", Site]],
    *,
    listeners: Sequence[Union["AnyGroup", Listener]] = (),
    voice: Optional["VoiceSynth"] = None,
    coalition: str = "blue",
    name: str = "IADS",
    down_call: Optional[str] = "Magic: {label} has ceased emissions, site is dark.",
    up_call: Optional[str] = "Magic: {label} radar is radiating again.",
    hot_call: Optional[str] = None,
    announce_spacing_s: float = 7.0,
    alert_window_s: float = 120.0,
    update_interval_s: float = 5.0,
    debug: bool = False,
    trace: Optional[bool] = None,
    comment: str = "IADS — cueing, and reaction to observed anti-radiation fire",
) -> triggers.TriggerStart:
    """Build an integrated air-defence net out of `sites`.

    Pass plain `VehicleGroup`s for default behaviour or `Site` entries to tune
    cueing and reaction per site. Adds three mission-start triggers the first
    time it is called — the MIST shim, the vendored framework, and the generated
    setup — and returns the setup trigger.

    `listeners` is who could have heard any of that: the friendly groups
    carrying a receiver — the ELINT track, the AWACS, a ground collection site —
    each alive, within its own reach and in line of sight of the emitter at the
    moment it changes state. A radar starting or stopping is an ESM observation,
    so with no collector in a position to make it there is no radio call, and a
    net declared without any listener at all is silent by design. Pass plain
    groups for the default reach or `Listener` entries to state it.

    `down_call`, `up_call` and `hot_call` are `{label}` templates announced to
    `coalition` *when a listener heard the change*; with a `VoiceSynth` the same
    words are also rendered and played from the script. `hot_call` fires the
    *first* time a site comes up and
    defaults to nothing: the player's RWR is that call, and announcing it would
    give away a battery the briefing deliberately left off the map. A site coming
    back after being shot off the air uses `up_call`, since that one is news; a
    site going quiet because the package left says nothing at all, because
    `down_call` means "SEAD worked" and must not be borrowed. Every site gets its
    own call — a shot that darkens a whole belt queues them `announce_spacing_s`
    apart rather than dropping the later ones.

    `alert_window_s` is how long a launch somebody saw keeps the net on notice.
    A reaction is otherwise decided at the instant of the shot, so a battery that
    was cold then is in nobody's reaction — and a HARM in POS or EOM mode is
    aimed at a place rather than at an emitter, which makes shooting a dark site
    and waiting for it to come up the standard tactic and, without this, a free
    kill. A site that comes on the air inside the window is told about the shot
    second-hand (its `net_relay` share of `probability`, recognition timed from
    when it came up), once per shot: a site that already rolled at launch does
    not roll again on its way up. `0.0` switches it off, and only an observed
    launch arms it, so masking a shot from the whole net still reaches nobody.

    `update_interval_s` is the framework's go-live cycle.

    Two switches say what the net is doing, one per half of the split above.
    `debug` turns on Skynet's own output — which sites it took, what it is
    tracking, every radar going live and dark — printed **on the player's
    screen** as well as to `dcs.log`. `trace` is this project's own half: which
    sites were in a position to see a launch, what their reaction rolled
    against, how long each stayed off the air and where it drove, written to
    `dcs.log` only under an `IADS/<name>` prefix. It follows `debug` unless set,
    so `debug=True` gives both and `trace=True, debug=False` gives the quiet,
    log-only one. Both are for tuning a net and neither ships in a mission a
    player is meant to fly.
    """
    if coalition not in _SIDE:
        raise ValueError(f"coalition must be blue/red, got {coalition!r}")
    entries = [s if isinstance(s, Site) else Site(s, s.name) for s in sites]
    if not entries:
        raise ValueError("arm_iads needs at least one site")
    _validate(entries)
    ears = [w if isinstance(w, Listener) else Listener(w) for w in listeners]
    _validate_listeners(ears)
    if not ears and any((down_call, up_call, hot_call)):
        # Not an error: a net with no collector behind it is a legitimate,
        # quieter mission. It is worth saying out loud, because the wording is
        # configured here and the silence happens in Lua at run time, so a
        # mission that meant to announce these would otherwise only find out in
        # the air.
        log.warning(
            "IADS radio calls are configured but no listener can hear them — "
            "no emissions change will be reported; pass listeners=[...]",
            name=name,
        )

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
        TRACE="true" if (debug if trace is None else trace) else "false",
        ALERT=f"{alert_window_s:.1f}",
        SITES="\n".join(rows),
        LISTENERS="\n".join(_listener_row(w) for w in ears),
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
        listeners=[w.named for w in ears],
    )
    return rule
