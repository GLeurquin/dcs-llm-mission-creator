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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import structlog
from dcs import planes
from dcs.weapons_data import weapon_ids

if TYPE_CHECKING:
    from dcs.unitgroup import FlyingGroup

log = structlog.get_logger(__name__)

__all__ = [
    "AI_JTAC_CODE",
    "DEFAULT_CODE",
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
