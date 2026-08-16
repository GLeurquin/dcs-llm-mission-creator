"""AI-tasking helpers (project-owned).

Thin wrappers over pydcs `dcs.task` verbs that missions reach for repeatedly
but that take several fiddly, easy-to-get-wrong steps by hand. Each one takes
already-built pydcs groups and appends the right task/option to the group's
first waypoint (the ComboTask that runs at spawn), matching how pydcs's own
flight helpers apply enroute tasks (`group.points[0].tasks.append(...)`).

Covered:

- `apply_ai_difficulty` — the ROE / reaction-to-threat difficulty dial,
  mapping the mission's recruit→ace label onto the AI behaviour options so
  "harder" means a more aggressive, more capable, more persistent enemy.
- `apply_threat_reaction` — the *friendly* counterpart: a package flight that
  defends itself, avoids threat zones and uses its countermeasures, instead of
  boring straight through a SAM belt on the pydcs default.
- `fac_attack_group` — turn a group into a JTAC/FAC that lases a target group
  and talks the player onto it.
- `scramble_on_trigger` — cold-ramp alert AI: the flight sits shut down until
  a condition fires, then starts engines and launches (generalises pydcs
  `FlyingGroup.delay_start`, which is time-only).

Carrier / nav-aid tasks (`ActivateBeaconCommand`, `ActivateICLSCommand`,
`RecoveryTanker`) are **not** wrapped here — they are one-line
`group.points[0].tasks.append(...)` passthroughs with no project policy of
their own; call pydcs directly (see PYDCS_REFERENCE.md §6).

Design rule (mirrors the other `core/` helpers): built pydcs objects in,
nothing returned but the created task/trigger for the caller to annotate.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Optional, Union

import structlog
from dcs import action, task, triggers
from dcs.mapping import Vector2
from dcs.task import Designation, Modulation, WeaponType

from dcs_mission_creator.core.difficulty import Difficulty

if TYPE_CHECKING:
    from dcs.condition import Condition
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup, Group, MovingGroup

log = structlog.get_logger(__name__)


# -- ROE / reaction difficulty dial -----------------------------------------

# Per-difficulty AI behaviour. Harder → weapons freer, defends harder, radar
# and ECM used more freely, less eager to bug out on bingo. These are airborne
# options; on a ground group the flight-only ones are simply inert.
_ROE = {
    Difficulty.RECRUIT: task.OptROE.Values.ReturnFire,
    Difficulty.TRAINED: task.OptROE.Values.OpenFireWeaponFree,
    Difficulty.VETERAN: task.OptROE.Values.WeaponFree,
    Difficulty.ACE: task.OptROE.Values.WeaponFree,
}
_REACT = {
    Difficulty.RECRUIT: task.OptReactOnThreat.Values.NoReaction,
    Difficulty.TRAINED: task.OptReactOnThreat.Values.PassiveDefense,
    Difficulty.VETERAN: task.OptReactOnThreat.Values.EvadeFire,
    Difficulty.ACE: task.OptReactOnThreat.Values.EvadeFire,
}
_RADAR = {
    Difficulty.RECRUIT: task.OptRadarUsing.Values.UseForAttackOnly,
    Difficulty.TRAINED: task.OptRadarUsing.Values.UseForSearchIfRequired,
    Difficulty.VETERAN: task.OptRadarUsing.Values.UseForContinuousSearch,
    Difficulty.ACE: task.OptRadarUsing.Values.UseForContinuousSearch,
}
_ECM = {
    Difficulty.RECRUIT: task.OptECMUsing.Values.NeverUse,
    Difficulty.TRAINED: task.OptECMUsing.Values.UseIfOnlyLockByRadar,
    Difficulty.VETERAN: task.OptECMUsing.Values.UseIfDetectedLockByRadar,
    Difficulty.ACE: task.OptECMUsing.Values.AlwaysUse,
}


def apply_ai_difficulty(
    group: "MovingGroup", difficulty: Union[str, Difficulty]
) -> None:
    """Set ROE / reaction / radar / ECM / bingo options from a difficulty label.

    Call once per AI group after it is built; appends the options to the
    group's spawn-waypoint ComboTask. Recruit = passive, radar-shy, bugs out on
    bingo; ace = weapons-free, defends and uses ECM aggressively, stays on task.
    """
    d = Difficulty.coerce(difficulty)
    tasks = group.points[0].tasks
    tasks.append(task.OptROE(_ROE[d]))
    tasks.append(task.OptReactOnThreat(_REACT[d]))
    tasks.append(task.OptRadarUsing(_RADAR[d]))
    tasks.append(task.OptECMUsing(_ECM[d]))
    tasks.append(task.OptRTBOnBingoFuel(d in (Difficulty.RECRUIT, Difficulty.TRAINED)))
    tasks.append(task.OptRestrictAfterburner(d is Difficulty.RECRUIT))
    log.debug("applied AI difficulty", group=group.name, difficulty=d.value)


# -- friendly threat reaction ------------------------------------------------


def apply_threat_reaction(
    group: "FlyingGroup",
    *,
    reaction: task.OptReactOnThreat.Values = (
        task.OptReactOnThreat.Values.ByPassAndEscape
    ),
    chaff_flare: task.OptChaffFlareUsing.Values = (
        task.OptChaffFlareUsing.Values.UseWhenFlyingInSAMWEZ
    ),
    ecm: task.OptECMUsing.Values = task.OptECMUsing.Values.UseIfDetectedLockByRadar,
    rtb_on_bingo: bool = True,
) -> None:
    """Make a package flight behave like a crew that was briefed on the SAMs.

    The pydcs / DCS defaults leave an AI flight with no reaction to a threat at
    all: it flies its route into an engagement zone, takes the shot and dies,
    which reads as the friendly package being stupid rather than the enemy
    being dangerous. This sets the three options that fix that —
    `ByPassAndEscape` (fly around or above a threat zone rather than through
    it), chaff/flare inside a SAM WEZ, and ECM once something locks — plus RTB
    on bingo so a flight that has been dragged around still gets home.

    The route matters more than the option: DCS only avoids what it can see
    coming, so plan the ingress with `core/routing.py` and use this to cover
    the site the planner did not know about. Escalate `reaction` to
    `AllowAbortMission` for a flight that should turn around rather than press
    a target through a live belt.
    """
    tasks = group.points[0].tasks
    tasks.append(task.OptReactOnThreat(reaction))
    tasks.append(task.OptChaffFlareUsing(chaff_flare))
    tasks.append(task.OptECMUsing(ecm))
    tasks.append(task.OptRTBOnBingoFuel(rtb_on_bingo))
    log.debug("applied threat reaction", group=group.name, reaction=reaction.name)


# -- JTAC / FAC --------------------------------------------------------------


class FacCallsign(IntEnum):
    """The DCS FAC callname list — what the player reads in the radio menu.

    A FAC does *not* answer to its group name: DCS names it from this fixed
    table (the "GroundUnits" callsign list in `dcs.countries`) plus the flight
    number, so a group called `Hammer` tasked with the default index 1 checks in
    as *Axeman 1-1* and the briefing lies. Pick the member that matches the
    callsign the briefing uses.
    """

    AXEMAN = 1
    DARKNIGHT = 2
    WARRIOR = 3
    POINTER = 4
    EYEBALL = 5
    MOONBEAM = 6
    WHIPLASH = 7
    FINGER = 8
    PINPOINT = 9
    FERRET = 10
    SHABA = 11
    PLAYBOY = 12
    HAMMER = 13
    JAGUAR = 14
    DEATHSTAR = 15
    ANVIL = 16
    FIREFLY = 17
    MANTIS = 18
    BADGER = 19


def fac_attack_group(
    fac_group: "Group",
    target_group: "Group",
    *,
    weapon_type: WeaponType = WeaponType.Auto,
    designation: Designation = Designation.Laser,
    frequency: int = 30,
    modulation: Modulation = Modulation.FM,
    callsign: Union[int, FacCallsign] = FacCallsign.AXEMAN,
    number: int = 1,
) -> task.FACAttackGroup:
    """Task `fac_group` as a JTAC/FAC lasing `target_group` for the player.

    `designation=Laser` (the default) makes the controller lase the target so a
    laser-guided weapon tracks it; `frequency` (MHz) is where the controller
    talks. Works for a ground JTAC vehicle group or an airborne FAC(A) flight.

    Two things the task alone does not buy, and that the caller still owns:
    the FAC has to be close enough to *see* the target (DCS gives it no
    omniscience — park an airborne FAC within roughly 10 km of the target and
    with line of sight, or it never acquires and never lases), and it has to
    stay on station (a route that runs out sends it home mid-sortie). `callsign`
    is the name the player hears, `number` the flight number after it.
    """
    fac = task.FACAttackGroup(
        target_group.id,
        target_group.name,
        Vector2(0, 0),
        weapon_type,
        int(callsign),
        designation,
        frequency,
        modulation,
        number=number,
    )
    fac_group.points[0].tasks.append(fac)
    log.debug(
        "tasked FAC",
        fac=fac_group.name,
        target=target_group.name,
        callsign=int(callsign),
        frequency=frequency,
    )
    return fac


# -- cold-ramp scramble ------------------------------------------------------


def scramble_on_trigger(
    m: "Mission",
    group: "FlyingGroup",
    *conditions: "Condition",
    comment: Optional[str] = None,
) -> triggers.TriggerOnce:
    """Keep `group` cold on the ramp until `conditions` fire, then launch it.

    Sets the flight uncontrolled with a queued `StartCommand`, then adds a
    one-shot trigger that pushes that task (engines start, taxi, take off) when
    every condition in `conditions` is true — e.g. a `PartOfCoalitionInZone`
    for an alert-5 scramble. Returns the trigger so the caller can extend it.
    """
    group.add_trigger_action(task.StartCommand())
    group.uncontrolled = True
    trig = triggers.TriggerOnce(comment=comment or f"scramble {group.name}")
    for cond in conditions:
        trig.rules.append(cond)
    trig.actions.append(action.AITaskPush(group.id, 1))
    m.triggerrules.triggers.append(trig)
    log.debug("armed scramble", group=group.name, conditions=len(conditions))
    return trig
