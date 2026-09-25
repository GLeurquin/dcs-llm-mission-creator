"""Intercept an intruder, shadow it, and escort it out of a zone (project-owned).

DCS has no model of an aircraft that is *not yet* an enemy. Two coalitions are
at war from the first frame: a red Su-27 over a blue CAP is a target, and a blue
CAP over a red Su-27 shoots it. Air policing is the opposite problem — the
intruder is a coalition enemy that nobody is cleared to fire on until it does
something — and the mission editor reaches none of the three things it needs:

- **"Intercepted" is a formation state, not a trigger.** No condition says "a
  player has been sitting on this aircraft's wing". The nearest is a moving
  zone, which cannot express "for long enough to be seen" and cannot re-arm.
- **Compliance is a change of route.** An intercepted aircraft turns for its
  own field, which is a new `Mission` task built from where it is *now*.
- **The flip is a coin the pilot cannot see.** It has to be rolled at run
  time: pydcs' RNG is seeded from the mission slug (`MissionBuilder._seed_rng`),
  so a roll made at build time would be the same roll on every sortie flown on
  that `.miz`.

So all three live in `core/lua/intercept.lua`, one state machine per track
(`pending → loose → escorted → hostile → resolved`, with `escorted → loose`
when the escort drifts off). What is here is the table that feeds it and the
editor-side half that has to be true before the script runs.

**Why the defenders are held on `OPEN_FIRE` rather than `WEAPON_HOLD` alone.**
A friendly CAP is the obvious backstop for a track that turns hostile, and the
obvious way to commit it — weapons free — commits it on *every* red aircraft in
range, including the compliant pair its own lead is escorting. DCS's
`OPEN_FIRE` is "engage only what you are tasked against", so on the flip the
script sets that and pushes an `AttackGroup` on the one group that turned. To
make it true the defender must carry no en-route engage task of its own — the
`CAPTaskAction` `Mission._load_tasks` adds for a CAP main task is exactly that —
so `arm_intercepts` strips them.

**Why a compliant track is resolved on leaving the zone, and not on reaching
home.** The mission's claim is sovereignty over an airspace; what happens north
of the line is somebody else's. It is also what makes the escort finite.

**Why a lapse re-arms the roll.** A track left alone turns back to its orbit
and has to be intercepted again, and the second intercept rolls again. A player
who lets them go pays for it twice — in time, and in the chance of a flip.

**Why the entropy comes from the player.** DCS's scripting `math.random`
replays one stream from mission start. The only thing that differs between two
sorties on the same file is the human, so the generator is reseeded at each
intercept off the millisecond clock and the intruder's position.

**Why one hit ends the policing, whoever fired.** The foul flag is on any hit
by the players' coalition on a track that has not turned, AI defenders
included; a compliant aircraft shot down is the one outcome the whole ROE
exists to prevent, and it is not less of an incident because a wingman's
missile did it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence

import structlog
from dcs import task, triggers

from dcs_mission_creator.core import lua

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.terrain.terrain import Airport
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.core.tts import VoiceSynth

log = structlog.get_logger(__name__)

__all__ = ["Calls", "Flags", "Track", "arm_intercepts", "inside"]

_SCRIPT = "intercept.lua"
_SIDE = {"blue": "coalition.side.BLUE", "red": "coalition.side.RED"}
_HOSTILE_ONLY = frozenset({"hostile", "splashed", "fled"})
#: En-route tasks that let a flight pick its own targets. `CAPTaskAction` is the
#: one `Mission._load_tasks` adds for a CAP main task; the other two are what a
#: mission would reach for by hand.
_SELF_ENGAGING = (task.CAPTaskAction, task.EngageTargets, task.EngageTargetsInZone)

#: A player this close, 3D, counts as alongside. One nautical mile is the
#: ICAO interception procedure's own "visual, abeam the cockpit" distance.
INTERCEPT_M = 1_852.0
#: How long alongside before the intruder is taken to have seen the escort.
HOLD_S = 20.0
#: How far an escort may drift from a compliant track before it notices.
ESCORT_M = 18_520.0
#: How long it has to be unwatched before it turns back in.
LAPSE_S = 90.0
#: Poll period. Two seconds is under a tenth of `HOLD_S`, so the hold is
#: resolved to within one tick without the script walking every unit per frame.
TICK_S = 2.0


@dataclass(frozen=True)
class Calls:
    """What the controller says about a track, as `{label}` templates.

    `None` keeps a call silent. Each one is spoken and printed from Lua with the
    same string (`VoiceSynth.register`), the `core/triggers.py` rule applied to
    a call a trigger cannot make because it can happen more than once.
    """

    intercepted: Optional[str] = None
    hostile: Optional[str] = None
    splashed: Optional[str] = None
    down: Optional[str] = None
    cleared: Optional[str] = None
    fled: Optional[str] = None
    lapsed: Optional[str] = None
    unchallenged: Optional[str] = None
    foul: Optional[str] = None

    def items(self) -> list[tuple[str, str]]:
        return [(k, v) for k, v in vars(self).items() if v is not None]


@dataclass(frozen=True)
class Track:
    """One intruder group and everything it does once intercepted.

    `orbit` is where it loiters inside the zone and where it goes back to on a
    lapse; `exit` is the first point of its way home and must be outside the
    zone; `home` is the field it lands at. `hostile_probability` is rolled at
    each intercept, and a flip happens `hostile_after_s` later, drawn
    triangularly, only if the track is still escorted and still inside.
    `deadline_s` is how long it loiters, counted from activation, before it
    leaves on its own with its job done. Speeds are km/h, like every pydcs
    speed; altitudes metres.
    """

    group: FlyingGroup
    label: str
    orbit: Point
    exit: Point
    home: Airport
    altitude_m: float
    speed_kph: float
    hostile_probability: float
    hostile_after_s: tuple[float, float] = (45.0, 240.0)
    deadline_s: float = 1_500.0
    calls: Calls = field(default_factory=Calls)


@dataclass(frozen=True)
class Flags:
    """User flags the script sets; the mission writes its outcome calls on them.

    `done` — every track resolved (out of the zone or dead). `foul` — a track
    that had not turned was hit by our side. `unchallenged` — at least one track
    loitered its full time with nobody alongside.
    """

    done: int
    foul: int
    unchallenged: int


def inside(p: Point, zone: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon, the same test the Lua runs."""
    hit = False
    j = len(zone) - 1
    for i in range(len(zone)):
        a, b = zone[i], zone[j]
        if (a.y > p.y) != (b.y > p.y) and p.x < (b.x - a.x) * (p.y - a.y) / (
            b.y - a.y
        ) + a.x:
            hit = not hit
        j = i
    return hit


def _validate(tracks: Sequence[Track], zone: Sequence[Point]) -> None:
    if len(zone) < 3:
        raise ValueError("an intercept zone needs at least three vertices")
    if not tracks:
        raise ValueError("arm_intercepts needs at least one track")
    for t in tracks:
        if not 0.0 <= t.hostile_probability <= 1.0:
            raise ValueError(f"{t.label}: hostile_probability must be in [0, 1]")
        lo, hi = t.hostile_after_s
        if not 0.0 <= lo <= hi:
            raise ValueError(f"{t.label}: hostile_after_s must be (lo, hi), lo <= hi")
        if not inside(t.orbit, zone):
            raise ValueError(f"{t.label}: its orbit is outside the zone")
        if inside(t.exit, zone):
            # A track whose way home starts inside the zone may never leave it
            # on the compliant route, and the mission never resolves.
            raise ValueError(f"{t.label}: its exit point is inside the zone")
        if not t.group.units:
            raise ValueError(f"{t.label}: {t.group.name} has no units")


def _hold_fire(group: FlyingGroup) -> None:
    """Weapons hold from the spawn waypoint, appended last so it wins."""
    group.points[0].tasks.append(task.OptROE(task.OptROE.Values.WeaponHold))


def _commit_only_when_told(group: FlyingGroup) -> None:
    """A defender engages nothing on its own — see the module docstring."""
    for point in group.points:
        point.tasks = [t for t in point.tasks if not isinstance(t, _SELF_ENGAGING)]
    _hold_fire(group)


def _xy(p: Point) -> str:
    return f"{{x={p.x:.1f}, y={p.y:.1f}}}"


def _row(t: Track, calls: dict[str, tuple[str, Optional[str]]]) -> str:
    call_rows = ", ".join(
        f"{key}={{text={lua.quote(text)}, sound={lua.quote(sound)}}}"
        for key, (text, sound) in calls.items()
    )
    return (
        "    {{group={group}, label={label}, p={p:.3f}, after={{{lo:.1f}, {hi:.1f}}}, "
        "deadline={deadline:.1f}, alt={alt:.1f}, speed={speed:.2f}, orbit={orbit}, "
        "exit={exit}, home={home}, airdrome={airdrome}, calls={{{calls}}}}},".format(
            group=lua.quote(t.group.name),
            label=lua.quote(t.label),
            p=t.hostile_probability,
            lo=t.hostile_after_s[0],
            hi=t.hostile_after_s[1],
            deadline=t.deadline_s,
            alt=t.altitude_m,
            speed=t.speed_kph / 3.6,
            orbit=_xy(t.orbit),
            exit=_xy(t.exit),
            home=_xy(t.home.position),
            airdrome=int(t.home.id),
            calls=call_rows,
        )
    )


def arm_intercepts(
    m: Mission,
    tracks: Sequence[Track],
    *,
    zone: Sequence[Point],
    defenders: Sequence[FlyingGroup] = (),
    controller: Optional[FlyingGroup] = None,
    voice: Optional[VoiceSynth] = None,
    coalition: str = "blue",
    intercept_m: float = INTERCEPT_M,
    hold_s: float = HOLD_S,
    escort_m: float = ESCORT_M,
    lapse_s: float = LAPSE_S,
    flag_base: int = 900,
    trace: bool = True,
) -> Flags:
    """Arm the intercept state machine for `tracks` inside `zone`.

    Sets every track and every defender to weapons hold from spawn, strips the
    defenders' own engage tasks, and adds one mission-start `DoScript`.
    `controller` is whose radio the calls go out on — the AWACS — and they stop
    when it is dead. `trace` writes each decision to `dcs.log` under
    `INTERCEPT/<label>`, and nothing to the screen.

    Returns the three flags the mission writes its outcome calls on, numbered
    from `flag_base`.
    """
    if coalition not in _SIDE:
        raise ValueError(f"coalition must be blue/red, got {coalition!r}")
    _validate(tracks, zone)
    for t in tracks:
        _hold_fire(t.group)
    for d in defenders:
        _commit_only_when_told(d)

    rows: list[str] = []
    for t in tracks:
        calls: dict[str, tuple[str, Optional[str]]] = {}
        for key, template in t.calls.items():
            if key in _HOSTILE_ONLY and t.hostile_probability == 0.0:
                continue  # a call this track cannot make is not worth a render
            text = template.format(label=t.label)
            calls[key] = (text, voice.register(m, text) if voice else None)
        rows.append(_row(t, calls))

    flags = Flags(done=flag_base, foul=flag_base + 1, unchallenged=flag_base + 2)
    script = lua.render(
        _SCRIPT,
        TRACKS="\n".join(rows),
        ZONE="\n".join(f"    {_xy(p)}," for p in zone),
        SIDE=_SIDE[coalition],
        INTERCEPT_M=f"{intercept_m:.1f}",
        HOLD_S=f"{hold_s:.1f}",
        ESCORT_M=f"{escort_m:.1f}",
        LAPSE_S=f"{lapse_s:.1f}",
        TICK=f"{TICK_S:.1f}",
        CONTROLLER=lua.quote(controller.name if controller else None),
        DEFENDERS=", ".join(lua.quote(d.name) for d in defenders),
        FLAG_DONE=str(flags.done),
        FLAG_FOUL=str(flags.foul),
        FLAG_UNCHALLENGED=str(flags.unchallenged),
        TRACE="true" if trace else "false",
    )
    rule = triggers.TriggerStart(comment="Intercept — escort-out state machine")
    rule.add_action(lua.InlineDoScript(script))
    m.triggerrules.triggers.append(rule)
    log.debug(
        "armed intercepts",
        tracks=[t.label for t in tracks],
        defenders=[d.name for d in defenders],
        flags=flags,
    )
    return flags
