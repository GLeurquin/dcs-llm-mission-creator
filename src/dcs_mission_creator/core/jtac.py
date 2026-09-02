"""JTAC coordinates in the format the asking cockpit takes (project-owned helper).

DCS's own JTAC passes one coordinate system and it is not negotiable: the
9-line and the target-data call both go through `MGRS:make(point, 4)` in
`Scripts/Speech/NATO.lua`, so *every* airframe is read a 4-digit military grid.
That is right for the two cockpits built around grid — the A-10's CDU and the
Apache's TSD take a zone/digraph/easting/northing straight off the radio — and
useless in the ones that are not: an F-16 pilot has a DED that accepts degrees
and decimal minutes, an F/A-18 an UFC that does the same, and neither can enter
a grid at all. The player ends up flying to a mark they had to convert on the
kneeboard, which is not what a JTAC talk-on is supposed to cost.

`arm_jtac_coords` adds the readout the stock task cannot: a radio-menu request
under the controller's callsign that answers in the format of the airframe
that asked. The format is chosen per player group from its aircraft type
(`COCKPIT_COORD_FORMAT`, overridable per mission), so the same JTAC reads a
grid to an A-10 and degrees-and-minutes to a Viper in the same mission, and a
player who swaps airframes gets the new cockpit's format with the new slot.

Two consequences of it being a *request* rather than a briefing line: the
position is read off a live unit each time, so it is current for a column that
is still driving, and it costs the player a radio call instead of handing them
a target on a plate. The reply is text only — the numbers are computed in the
mission, and `VoiceSynth` renders its audio ahead of time.

This does not replace `tasking.fac_attack_group`: that is what makes the
controller acquire and talk. Arm both — the 9-line stays stock, this only adds
the coordinates in a form the cockpit can take. Note that the menu here needs
no radio at all, which the stock controller's own calls do: pair it with
`laser.arm_autolase` on any mission where the player takes the controller's
spot, or the coordinates arrive in the right units and the laser still does not.

A `CoordTarget.laser_code` is checked against `laser.AI_JTAC_CODE` and refused
if it differs, because the ME's FAC task carries no code field: whatever a
mission writes here, the controller lases 1688. Briefing anything else gives
the player a number to dial into a pod that will then track nothing, which
from the cockpit looks exactly like a bomb that failed to guide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

import structlog
from dcs import helicopters, planes, triggers

from dcs_mission_creator.core import laser, lua

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import Group

log = structlog.get_logger(__name__)

_SIDE = {"blue": "coalition.side.BLUE", "red": "coalition.side.RED"}


class CoordFormat(Enum):
    """How a set of coordinates is read out — one per cockpit entry convention.

    `MGRS` is zone + digraph + 4-digit easting/northing (10 m), what a grid
    cockpit is typed. `DDM` is degrees and decimal minutes, what a DED, UFC or
    ODU takes, and the sane default for everything with a lat/long entry page.
    `DMS` adds seconds; nothing maps to it out of the box, it is here for a
    mission that wants an airframe read out that way.
    """

    MGRS = "mgrs"
    DDM = "ddm"
    DMS = "dms"


# Only the grid cockpits are listed: they are the exceptions. Everything else
# enters waypoints as degrees and decimal minutes, so it falls through to
# `default_format` rather than needing a row here that would go stale as
# airframes are added to DCS.
COCKPIT_COORD_FORMAT: Mapping[str, CoordFormat] = {
    planes.A_10A.id: CoordFormat.MGRS,
    planes.A_10C.id: CoordFormat.MGRS,
    planes.A_10C_2.id: CoordFormat.MGRS,
    helicopters.AH_64D_BLK_II.id: CoordFormat.MGRS,
    helicopters.OH58D.id: CoordFormat.MGRS,
}


@dataclass
class CoordTarget:
    """One thing the controller will read coordinates for, as one radio item.

    `label` is the callsign the player hears — it has to be the callsign the
    briefing and `tasking.fac_attack_group`'s `callsign=` give the controller,
    not the pydcs group name. `what` names the target in the reply ("resupply
    column"); `menu_item` is the radio entry, so it reads as a request. Pass
    `laser_code` for a controller that lases, and the reply repeats the code
    with the position the way a real one would.
    """

    group: "Group"
    label: str
    what: str
    laser_code: Optional[int] = None
    menu_item: str = "Target coordinates"


# The Lua handler lives in `core/lua/jtac_coords.lua`; the placeholders it
# declares (`__TARGETS__` / `__FORMATS__` / `__DEFAULT__` / `__MENU__` /
# `__SIDE__` / `__DURATION__` / `__SCAN__` / `__PUSH_AT__`) are filled in below.
_SCRIPT = "jtac_coords.lua"


def arm_jtac_coords(
    m: "Mission",
    targets: Sequence[CoordTarget],
    *,
    coalition: str = "blue",
    menu_title: str = "JTAC",
    formats: Optional[Mapping[str, CoordFormat]] = None,
    default_format: CoordFormat = CoordFormat.DDM,
    duration_s: float = 30.0,
    scan_s: float = 5.0,
    push_at_s: Optional[float] = None,
    comment: str = "JTAC coordinate readout",
) -> triggers.TriggerStart:
    """Give `coalition`'s players a radio request for `targets`' coordinates.

    Each target becomes one entry under a `menu_title` sub-menu (name it after
    the controller's callsign, so the player reads "Hammer 1-1 → Target
    coordinates"). The answer is formatted from the requesting group's aircraft
    type via `COCKPIT_COORD_FORMAT`, which `formats` extends or overrides per
    mission (keys are DCS type names — take them off pydcs, `planes.F_16C_50.id`,
    rather than typing the string); an airframe in neither table gets
    `default_format`. `duration_s` is how long the readout stays on screen and
    `scan_s` how often the script looks for a newly slotted player to wire the
    menu onto.

    Set `push_at_s` — mission seconds, sensibly just after the controller's
    check-in call — and the **first** target's position is also read out once
    unprompted, to whoever is in the cockpit then and to anyone slotting in
    later. That matters more than it sounds: DCS's own 9-line is a grid whatever
    the airframe, so a player who never opens the F10 menu would be read a grid
    all sortie and conclude that is all the controller has. One volunteered call
    in the right format settles it; the rest stays on request, because the target
    moves and a controller does not narrate.

    Returns the mission-start trigger carrying the generated `DoScript`.
    """
    if coalition not in _SIDE:
        raise ValueError(f"coalition must be blue/red, got {coalition!r}")
    if not targets:
        raise ValueError("arm_jtac_coords needs at least one target")
    if duration_s <= 0 or scan_s <= 0:
        raise ValueError("duration_s and scan_s must be positive")
    if push_at_s is not None and push_at_s < 0:
        raise ValueError("push_at_s must be a mission time in seconds")
    for target in targets:
        if target.laser_code is not None and target.laser_code != laser.AI_JTAC_CODE:
            raise ValueError(
                f"{target.label} is briefed on laser code {target.laser_code}, "
                f"but a DCS AI controller lases on {laser.AI_JTAC_CODE} and the "
                "FAC task carries no code field — see core/laser.py"
            )

    table: dict[str, CoordFormat] = dict(COCKPIT_COORD_FORMAT)
    table.update(formats or {})

    target_rows = [
        "    {{group={group}, label={label}, what={what}, item={item}, "
        "code={code}}},".format(
            group=lua.quote(target.group.name),
            label=lua.quote(target.label),
            what=lua.quote(target.what),
            item=lua.quote(target.menu_item),
            code="nil" if target.laser_code is None else str(int(target.laser_code)),
        )
        for target in targets
    ]
    format_rows = [
        f"    [{lua.quote(aircraft)}] = {lua.quote(fmt.value)},"
        for aircraft, fmt in sorted(table.items())
    ]

    script = lua.render(
        _SCRIPT,
        TARGETS="\n".join(target_rows),
        FORMATS="\n".join(format_rows),
        DEFAULT=lua.quote(default_format.value),
        MENU=lua.quote(menu_title),
        SIDE=_SIDE[coalition],
        DURATION=f"{duration_s:.1f}",
        SCAN=f"{scan_s:.1f}",
        PUSH_AT="nil" if push_at_s is None else f"{push_at_s:.1f}",
    )
    rule = triggers.TriggerStart(comment=comment)
    rule.add_action(lua.InlineDoScript(script))
    m.triggerrules.triggers.append(rule)
    log.debug(
        "armed JTAC coordinate readout",
        targets=[t.group.name for t in targets],
        menu=menu_title,
        default=default_format.value,
    )
    return rule
