"""What laser code the ordnance is actually on (project-owned helper).

A briefing that names a laser code is making two claims at once: that the
controller's spot is on that code, and that the bombs in the bay will track it.
DCS decides both, and for most of the fleet it decides them the same way — with
a number the mission file cannot reach:

- **An AI JTAC lases on 1688 and on nothing else.** The mission editor's own
  `FAC - Attack Group` action carries exactly two parameters, `groupId` and
  `weaponType` (`<DCS>/MissionEditor/modules/me_action_db.lua`), and pydcs's
  `FACAttackGroup` adds only designation, frequency, callsign and datalink —
  there is no code field anywhere in the task, so the code the controller
  transmits in the 9-line is the game's own default.
- **Most cockpits set their own.** The F-16C, F/A-18C and A-10C carry no
  laser-code property at all: the pilot dials it on the TGP, the SMS or the
  DSMS, and it comes up at DCS's default. Only four families in pydcs expose
  the code as a mission-file field (`AddPropAircraft`) — the AV-8B, the JF-17,
  the F-4E and the F-15E — and every one of them *defaults to 1688* too.

So the honest rule, and the one this module enforces: **one code per mission,
and unless every laser weapon in it belongs to an airframe whose code the
mission can write, that code is `DEFAULT_CODE`.** `set_code` writes the
properties where they exist and refuses anything else rather than shipping a
briefing that says 1511 while `Ferret` lases 1688 and the player's GBU-12s come
up on 1688 — which is indistinguishable, from the cockpit, from a bomb that
simply did not guide.

    from dcs_mission_creator.core import laser

    _LASER_CODE = laser.DEFAULT_CODE          # the mission's one code
    laser.set_code(player, _LASER_CODE)       # after `mission_kit.arm`
    laser.set_code(pontiac, _LASER_CODE)      # the AI strike drops on it too

`core/jtac.py` checks every `CoordTarget.laser_code` against `AI_JTAC_CODE`, so
a controller cannot be briefed on a code it will not lase. What stays the
mission's job is saying the number out loud: the code the bombs are on belongs
in `readme()`, in the in-game briefing and — since pydcs writes it nowhere a
card could derive — in a `kneeboard.remark`.

**The other half of the same claim is whether the spot is on at all**, and as
DCS ships it that is a radio conversation rather than a fact: the AI controller
lases inside a check-in and a talk-on the player has to be in range and in line
of sight to have, which a flight coming out of a masked valley onto a moving
target is not. `arm_autolase` puts the spot where the briefing says it is —
already burning when the aeroplane arrives, held on what the designator can
actually see, and gone when the team is dead. The method is Ciribob's, from
DCS-CTLD; see `arm_autolase` and `core/lua/vendor/README.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

import structlog
from dcs import planes, triggers
from dcs.weapons_data import weapon_ids

from dcs_mission_creator.core import lua

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup, Group

log = structlog.get_logger(__name__)

__all__ = [
    "AI_JTAC_CODE",
    "DEFAULT_CODE",
    "LaserSpot",
    "arm_autolase",
    "code_for",
    "is_settable",
    "laser_guided_stores",
    "set_code",
    "validate_code",
]

#: DCS's default laser code, and the one every module comes up on: the AV-8B,
#: JF-17, F-4E and F-15E property defaults all spell it, and so does the
#: per-pylon `laser_code` in ED's own F-14 payloads.
DEFAULT_CODE = 1688

#: What a DCS AI JTAC or FAC(A) lases on. The ME's FAC task has no code field,
#: so this is not a choice the mission gets to make — see the module docstring.
AI_JTAC_CODE = DEFAULT_CODE

# The legal digits, off the F-4E's own laser-code spinboxes in
# `<DCS>/CoreMods/aircraft/F-4E/Entry/F-4E.lua`: 1, then 5-7, then 1-8 twice.
_DIGIT_BOUNDS = ((1, 1), (5, 7), (1, 8), (1, 8))

_AVIONICS_DIGITS = ("LaserCode100", "LaserCode10", "LaserCode1")
_GBU_DIGITS = ("GBULaserCode100", "GBULaserCode10", "GBULaserCode1")
_PHANTOM_DIGITS = (
    "LaserCodeDigit1",
    "LaserCodeDigit2",
    "LaserCodeDigit3",
    "LaserCodeDigit4",
)
# The Strike Eagle codes each LGB station separately; a mission that wants two
# codes on one jet can write these itself, this puts the whole jet on one.
_STRIKE_EAGLE_STATIONS = (
    "Sta2LaserCode",
    "LCFTLaserCode",
    "Sta5LaserCode",
    "RCFTLaserCode",
    "Sta8LaserCode",
)

# Designations that ride a laser but whose DCS display name does not say so.
# Everything else is caught by "laser" in the name, which is how ED writes the
# Paveways ("GBU-12 - 500lb Laser Guided Bomb") and the AGM-65E.
_LASER_RIDERS = (
    "gbu-10",
    "gbu-12",
    "gbu-16",
    "gbu-24",
    "gbu-27",
    "gbu-28",
    "gbu-54",
    "agm-114k",
    "kh-25ml",
    "kh-29l",
    "kab-500l",
    "kab-1500l",
    "apkws",
    "bolt-117",
)


def validate_code(code: int) -> int:
    """Return `code` if DCS would accept it as a laser code, else raise."""
    digits = [int(d) for d in str(int(code))]
    if len(digits) != 4 or any(
        d < low or d > high for d, (low, high) in zip(digits, _DIGIT_BOUNDS)
    ):
        raise ValueError(
            f"{code} is not a DCS laser code: four digits, 1 then 5-7 then "
            "1-8 twice (1511 and 1688 are legal, 1234 and 688 are not)"
        )
    return int(code)


def _properties(aircraft_id: str, code: int) -> Optional[dict[str, int]]:
    """The `AddPropAircraft` entries putting `aircraft_id` on `code`, or None.

    None means the airframe carries no laser-code field, so the code is
    whatever the pilot dials in the cockpit — starting at `DEFAULT_CODE`.
    """
    d1, d2, d3, d4 = (int(d) for d in str(code))
    if aircraft_id == planes.AV8BNA.id:
        # Two codes on the Harrier: the pod's own designator and the seekers.
        return dict(zip(_AVIONICS_DIGITS + _GBU_DIGITS, (d2, d3, d4, d2, d3, d4)))
    if aircraft_id == planes.JF_17.id:
        return dict(zip(_AVIONICS_DIGITS, (d2, d3, d4)))
    if aircraft_id in (planes.F_4E_45MC.id, planes.QF_4E.id):
        return dict(zip(_PHANTOM_DIGITS, (d1, d2, d3, d4)))
    if aircraft_id == planes.F_15ESE.id:
        return {station: d2 * 100 + d3 * 10 + d4 for station in _STRIKE_EAGLE_STATIONS}
    return None


def is_settable(group: "FlyingGroup") -> bool:
    """Whether the mission file can put `group`'s laser code where it likes."""
    return _properties(group.units[0].unit_type.id, DEFAULT_CODE) is not None


def set_code(group: "FlyingGroup", code: int = DEFAULT_CODE) -> int:
    """Put every unit of `group` on `code`, or refuse a code it cannot hold.

    Call it after `mission_kit.arm`, once per flight carrying a laser-guided
    weapon — including the AI ones, since an AI strike pair dropping on the
    controller's spot has the same problem the player does. On an airframe with
    no laser-code field it writes nothing and only checks the number, which is
    the whole point: the failure it exists to catch is a mission briefing a code
    the jet will never come up on.
    """
    validate_code(code)
    aircraft = group.units[0].unit_type.id
    props = _properties(aircraft, code)
    if props is None:
        if code != DEFAULT_CODE:
            raise ValueError(
                f"{group.name} flies the {aircraft}, whose laser code is not a "
                f"mission-file field — it comes up on {DEFAULT_CODE} and the "
                f"pilot retunes it in the cockpit, so a briefed {code} would "
                "be a code nothing in the package is actually on. Use "
                "laser.DEFAULT_CODE, or fly an airframe that carries the "
                "property (AV-8B, JF-17, F-4E, F-15E)."
            )
        log.debug("laser code left at the cockpit default", group=group.name, code=code)
        return code
    for unit in group.units:
        for key, value in props.items():
            unit.set_property(key, value)
    log.debug("wrote laser code", group=group.name, code=code, aircraft=aircraft)
    return code


def code_for(group: "FlyingGroup") -> int:
    """The code `group`'s laser-guided stores are on, written or default."""
    unit = group.units[0]
    props = unit.addpropaircraft or {}
    digits = [props.get(key) for key in _AVIONICS_DIGITS]
    if all(isinstance(d, int) for d in digits):
        return int("1" + "".join(str(d) for d in digits))
    phantom = [props.get(key) for key in _PHANTOM_DIGITS]
    if all(isinstance(d, int) for d in phantom):
        return int("".join(str(d) for d in phantom))
    station = props.get(_STRIKE_EAGLE_STATIONS[0])
    if isinstance(station, int):
        return 1000 + station
    return DEFAULT_CODE


def laser_guided_stores(group: "FlyingGroup") -> list[str]:
    """The names of the loaded stores on `group` that ride a laser spot.

    Read off the CLSIDs actually on the pylons, so it answers for the loadout
    the mission wrote rather than for what the airframe could carry. Used to
    tell a flight that needs a code from one that does not; the match is on
    DCS's own display names, which spell "Laser Guided" for the Paveways and
    need `_LASER_RIDERS` for the handful that do not.
    """
    found: list[str] = []
    for unit in group.units:
        for pylon in unit.pylons.values():
            weapon = weapon_ids.get(pylon.get("CLSID", ""))
            if weapon is None:
                continue
            name = str(weapon["name"])
            low = name.lower()
            if "laser" in low or any(rider in low for rider in _LASER_RIDERS):
                if name not in found:
                    found.append(name)
    return found


#: How far a ground designation team is taken to reach. A dismounted GLTD is
#: good for about 5 km and a vehicle-mounted set for about 10, and CTLD's
#: `JTAC_maxDistance` — the reference implementation of this feature — is
#: 10,000 m as well. A mission whose geometry needs another number says so.
DESIGNATOR_RANGE_M = 10_000.0

# The Lua handler lives in `core/lua/autolase.lua`; the placeholders it declares
# (`__SPOTS__` / `__VERIFY__` / `__DRIFT__` / `__TICK__` / `__CEILING__` /
# `__AIM_UP__` / `__INFRARED__` / `__TRACE__`) are filled in below.
_AUTOLASE = "autolase.lua"


@dataclass
class LaserSpot:
    """One designator holding one target group's nearest visible vehicle.

    `observer` is the group with the designator — a ground JTAC/TACP platoon or
    an airborne FAC — and `target` what it lases. `label` is what the trace
    lines call it (the controller's callsign reads best); it never reaches the
    player. `code` is the code the spot is on, and unless every laser weapon in
    the package belongs to an airframe whose code the mission file can write it
    has to be `DEFAULT_CODE` — see the module docstring.

    `start_at_s` is the mission second the team goes to work: `0.0`, the
    default, is a team already on the target at mission start, which is the
    whole point of the helper. `max_range_m` is how far it can hold a spot and
    `require_los` whether terrain stops it — leave that on unless the mission
    has a reason to model a designator that sees through a ridge.

    `lead_correction` pushes the spot a second ahead of the vehicle and a second
    upwind (CTLD's `laseSpotCorrections`, factors and all). It is off by default
    because it is not something the crew is really computing: it is a correction
    for the way a DCS LGB trails a moving spot. Turn it on for a mission whose
    target is a column at road speed and whose player has one pass.
    """

    observer: "Group"
    target: "Group"
    code: int = DEFAULT_CODE
    label: str = ""
    start_at_s: float = 0.0
    max_range_m: float = DESIGNATOR_RANGE_M
    require_los: bool = True
    lead_correction: bool = False


def arm_autolase(
    m: "Mission",
    spots: Sequence[LaserSpot],
    *,
    verify_s: float = 5.0,
    max_drift_m: float = 5.0,
    min_update_s: float = 0.2,
    max_update_s: float = 5.0,
    aim_height_m: float = 2.0,
    infrared: bool = True,
    trace: bool = False,
    comment: str = "JTAC laser spot",
) -> triggers.TriggerStart:
    """Keep a designator's spot on the target without a radio conversation.

    **DCS's AI controller lases as part of a conversation, and the conversation
    is the problem.** `tasking.fac_attack_group` buys the acquisition, the
    check-in, the 9-line and the spot — but all of it goes over a radio the
    player has to be tuned to, in range of and in line of sight of, because a
    ground controller's set is a ground unit's set. A flight that spends its
    ingress masked in a valley — which is what every low route in this project
    is *for* — cannot raise the party until it comes out, and then the talk-on
    costs a minute or two of a run-in that does not last that long. The laser
    comes up, if it comes up, after the pass. From the cockpit that is
    indistinguishable from a wrong code or a broken JTAC, and the player's own
    recourse — retune the pod — cannot help, because the pod was never the half
    that was wrong.

    A real party does not work that way round: it is on the target long before
    the aircraft is anywhere near, and the talk-on confirms a spot that is
    already burning. This puts that in the mission — `Spot.createLaser` from the
    designating unit onto the nearest vehicle of `target` it can see, moved as
    the vehicle drives, from `start_at_s` onward, with no reference to the player
    at all. The stock task stays: it is still what talks, what reads a 9-line to
    a grid cockpit and what makes the controller acquire. What it stops being is
    the only thing holding the laser.

    **The method is Ciribob's** — DCS-CTLD's `ctld.JTACAutoLase` is the
    reference implementation and this follows it where it matters, reimplemented
    rather than vendored (CTLD ships no licence and requires MIST, which this
    project deliberately does not carry — `core/lua/vendor/README.md`). Five of
    its decisions are load-bearing and were each worth taking:

    - the beam leaves the designating vehicle 2 m up and the aim point is the
      target's own origin lifted `aim_height_m`, not the ground under it;
    - line of sight is `land.isVisible` with **both** ends lifted 2 m, because
      two points on the deck fail across the gentlest rise;
    - the reach is 10 km (`DESIGNATOR_RANGE_M`, CTLD's `JTAC_maxDistance`);
    - the nearest visible vehicle is lased, and the one already lased is held
      while it still qualifies rather than hopping to a closer truck mid-fall;
    - **the spot is moved often enough that the target never travels more than
      `max_drift_m` between updates** (bounded by `min_update_s` /
      `max_update_s`), which is the difference between an LGB on the truck and
      one in its dust. Target selection — range, sight line, which vehicle —
      runs on its own slower `verify_s` clock, since it costs a terrain query
      per vehicle.

    `infrared` puts an IR pointer on the same point (CTLD does both), which is
    what makes the target findable through goggles or a pod in IR at night.

    Three bounds keep it from being a cheat rather than a fix: the sight line is
    measured, nothing is lased past `max_range_m`, and the spot is created *from*
    a live unit of `observer` and dies with the group — so a mission that lets
    the player lose the controller loses the laser with it.

    It reports nothing on the radio, deliberately. Every call in this project
    goes through `core/triggers.py` so the on-screen text and the TTS render
    cannot drift apart, and a Lua-side call would be text-only; when a laser
    comes and goes with the terrain, the mission says so with its own triggers
    on the geometry that decides it (`coastal_cover` is the worked example).
    `trace=True` writes each decision to `dcs.log` under `LASER/<label>` and
    nothing to the screen, like `core/iads.py`'s own trace.

    Returns the mission-start trigger carrying the generated `DoScript`.
    """
    if not spots:
        raise ValueError("arm_autolase needs at least one spot")
    if verify_s <= 0 or min_update_s <= 0 or max_drift_m <= 0:
        raise ValueError("verify_s, min_update_s and max_drift_m must be positive")
    if max_update_s < min_update_s:
        raise ValueError("max_update_s cannot be below min_update_s")
    rows: list[str] = []
    for spot in spots:
        validate_code(spot.code)
        if spot.code != AI_JTAC_CODE:
            log.warning(
                "scripted spot is not on the code the AI controller transmits",
                label=spot.label or spot.observer.name,
                code=spot.code,
                ai_jtac_code=AI_JTAC_CODE,
            )
        if not spot.observer.units:
            raise ValueError(f"{spot.observer.name} has no units to lase from")
        if not spot.target.units:
            raise ValueError(f"{spot.target.name} has no units to lase")
        if spot.start_at_s < 0:
            raise ValueError("start_at_s must be a mission time in seconds")
        if spot.max_range_m <= 0:
            raise ValueError("max_range_m must be positive")
        rows.append(
            "    {{observer={observer}, target={target}, code={code}, "
            "label={label}, startAt={start:.1f}, range={reach:.1f}, "
            "los={los}, lead={lead}}},".format(
                observer=lua.quote(spot.observer.name),
                target=lua.quote(spot.target.name),
                code=int(spot.code),
                label=lua.quote(spot.label or spot.observer.name),
                start=float(spot.start_at_s),
                reach=float(spot.max_range_m),
                los="true" if spot.require_los else "false",
                lead="true" if spot.lead_correction else "false",
            )
        )

    script = lua.render(
        _AUTOLASE,
        SPOTS="\n".join(rows),
        VERIFY=f"{verify_s:.2f}",
        DRIFT=f"{max_drift_m:.2f}",
        TICK=f"{min_update_s:.2f}",
        CEILING=f"{max_update_s:.2f}",
        AIM_UP=f"{aim_height_m:.2f}",
        INFRARED="true" if infrared else "false",
        TRACE="true" if trace else "false",
    )
    rule = triggers.TriggerStart(comment=comment)
    rule.add_action(lua.InlineDoScript(script))
    m.triggerrules.triggers.append(rule)
    log.debug(
        "armed scripted laser spots",
        spots=[(s.observer.name, s.target.name, s.code) for s in spots],
        verify_s=verify_s,
        infrared=infrared,
    )
    return rule
