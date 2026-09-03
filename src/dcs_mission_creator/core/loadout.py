"""Who in the flight carries what (project-owned helper).

Every mission here armed the player flight from **one** `stores` list, which
was correct while a mission was a single jet and is a wasted flight the moment
it is two. A two-ship is not two copies of the same aeroplane: it is a lead and
a wingman who between them carry the whole frag, and the F-16C's six weapon
stations are far too few for one jet to hold both halves of any real tasking.
The arithmetic is blunt — stations 4 and 6 are the bags, 5 is the ECM pod, 10
and 11 are the pods, so what is left is 1/2/3/7/8/9, and the two ends of that
(1/9, 2/8) only take a missile. **Three stations decide the sortie**, and a
mission that spends 3/7 on HARM has no bomb and one that spends them on bombs
has no HARM.

So the flight splits the frag instead:

    _FITS = (
        loadout.Loadout(
            role="HARM/HTS",
            carries="two AGM-88C, HTS and the pod, four AIM-120C",
            stores=((1, _AMRAAM), ..., (10, _HTS), (11, _TGP)),
        ),
        loadout.Loadout(
            role="CBU-105/TGP",
            carries="four CBU-105 on BRU-57, the pod, two AIM-120C, two AIM-9X",
            stores=(...),
        ),
    )

`assign` hands one fit to each slot by **cycling the list in order**, which is
the whole scaling rule: two slots are the pair the mission was written for, four
are two elements each carrying the same split (so an element that loses its
partner is still half a package rather than a jet that cannot do the job), and a
mission that wants a third capability at four slots declares a third fit. The
cycle is deterministic and slot 1 is always the first fit, so the briefing can
name who is carrying what without holding the built mission.

Two things follow that are worth stating because they were bugs first:

- **A DCS group's units carry their own pylons.** `FlyingGroup.load_pylon`
  writes the same store to every unit in the group, so arming a flight through
  it can only ever produce a uniform one; the split is written per unit
  (`Unit.load_pylon`), and `arm_unit` clears the stations first for the same
  reason `mission_kit.arm` does — the task default has already run and would
  survive on any station the list skips.
- **The magazine is per fit, not per jet.** `core/mission_builder`'s force
  balance rule ("a mission may not task more kills than the flight is carrying
  weapons for") counts air-to-air missiles, and with a split flight that count
  is a property of the *assignment*: `air_to_air_shots` reads it off the stores
  that were actually loaded, so a mission sizing its opposition off the player
  count cannot drift away from what the jets are carrying.

The briefing renderers are here rather than in the mission for the usual
reason: `readme()`, the in-game briefing text and the kneeboard remark are three
views of one table, and typing the split three times is how a briefing ends up
promising a HARM to a jet that has bombs on 3 and 7.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

import structlog

if TYPE_CHECKING:
    from dcs.flyingunit import FlyingUnit
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup

log = structlog.get_logger(__name__)

__all__ = [
    "Loadout",
    "air_to_air_shots",
    "arm_group",
    "arm_unit",
    "assign",
    "assignments",
    "block",
    "record",
    "remark_lines",
    "shots",
    "table",
]

#: How the pydcs pylon attributes spell an air-to-air missile. Every one of the
#: F-16C's — the AIM-9 family and both AMRAAM — ends in `_AAM`, which is ED's
#: own naming (`AIM_9X_Sidewinder_IR_AAM`,
#: `AIM_120C_AMRAAM___Active_Radar_AAM`), so the count comes off the store names
#: the mission wrote rather than off a per-mission constant that can drift.
_AAM_SUFFIX = "_AAM"


@dataclass(frozen=True)
class Loadout:
    """One jet's fit: what job it is on, what it carries, and the stations.

    `role` is the short label — a dozen characters or so, because it is what the
    kneeboard remark prints and what a radio call would use. Name it after the
    weapon that decides the job (`HARM/HTS`, `GBU-12/TGP`, `AIM-120C*6`) rather
    than after the doctrine word, so the card says something the pilot can act
    on. `carries` is the briefing phrase, in prose, for the README table and the
    in-game briefing. `stores` is `(pylon, weapon attribute)` exactly as
    `mission_kit.arm` takes it.
    """

    role: str
    carries: str
    stores: Sequence[tuple[int, str]]


def assign(loadouts: Sequence[Loadout], slots: int) -> tuple[Loadout, ...]:
    """One fit per slot: the mission's fits cycled in declaration order.

    Slot 1 always gets the first fit, so "the lead carries the HARMs" is a fact
    about the mission rather than about how many people showed up.
    """
    if not loadouts:
        raise ValueError("a flight needs at least one loadout")
    if slots > 1 and len(loadouts) == 1:
        log.warning(
            "the whole flight is flying one fit; a pair should split the frag",
            slots=slots,
            role=loadouts[0].role,
        )
    return tuple(loadouts[i % len(loadouts)] for i in range(slots))


def pylon_entry(plane_type: type, pylon: int, store: str) -> Any:
    """The `PylonN.<store>` value pydcs hangs on a station, or `None`.

    The one place the `Pylon<N>` attribute-name convention is written down. Two
    modules need it and they want opposite failure behaviour, which is why it
    returns rather than raises: arming wants an `AttributeError` at build time
    for a store that station cannot take (a wrong name should not ship), and
    `core/loadout_check` is *asking* whether the name resolves at all.

    The value is `(station number, {clsid, name, weight})` — note the lowercase
    `clsid`, unlike the `CLSID` a *loaded* pylon carries on a unit.
    """
    station = getattr(plane_type, f"Pylon{pylon}", None)
    return getattr(station, store, None) if station is not None else None


def arm_unit(
    unit: FlyingUnit, plane_type: type, stores: Sequence[tuple[int, str]]
) -> None:
    """Load `stores` as this one airframe's whole loadout.

    The per-unit half of `mission_kit.arm`, and the reason a split flight is
    possible at all: `FlyingGroup.load_pylon` writes to every unit in the group.
    """
    unit.pylons.clear()
    for pylon, weapon in stores:
        # `getattr` unguarded on purpose: a store name the station cannot take
        # is an AttributeError at build time rather than a silently empty rail.
        unit.load_pylon(getattr(getattr(plane_type, f"Pylon{pylon}"), weapon))


def arm_group(
    group: FlyingGroup, plane_type: type, stores: Sequence[tuple[int, str]]
) -> None:
    """Load `stores` as every unit of `group`'s loadout — the uniform case."""
    for unit in group.units:
        arm_unit(unit, plane_type, stores)


def air_to_air_shots(fit: Loadout) -> int:
    """How many air-to-air missiles this fit leaves the ramp with.

    Counted off the store names, so the number the force-balance arithmetic
    divides by two is the number actually on the rails.
    """
    return sum(1 for _, weapon in fit.stores if weapon.endswith(_AAM_SUFFIX))


def shots(assignment: Sequence[Loadout]) -> int:
    """The flight's whole air-to-air magazine, across every slot."""
    return sum(air_to_air_shots(fit) for fit in assignment)


def table(slot_names: Sequence[str], assignment: Sequence[Loadout]) -> str:
    """The README's loadout table: one row per coop slot, in slot order.

    The slot column holds the unit name DCS lists on the slot-selection screen,
    so a pilot picking a jet reads the same string in the briefing and in the
    game rather than having to work out which "#2" anybody meant.
    """
    rows = [
        "| Slot | Aircraft | Role | Carries |",
        "|------|----------|------|---------|",
    ]
    for i, (slot, fit) in enumerate(zip(slot_names, assignment), start=1):
        rows.append(f"| #{i} | `{slot}` | {fit.role} | {fit.carries} |")
    return "\n".join(rows)


def block(
    slot_names: Sequence[str],
    assignment: Sequence[Loadout],
    *,
    indent: str = "  ",
    width: int = 52,
) -> str:
    """The same table as plain text, for `set_description_text`.

    Wrapped rather than clipped, and wrapped narrow: the in-game briefing is a
    fixed-width panel and every other block in these missions is hand-wrapped to
    about this column, so a loadout line three times the width of the paragraph
    above it reads as something that escaped rather than as part of the sheet.
    """
    lines: list[str] = []
    for i, (slot, fit) in enumerate(zip(slot_names, assignment), start=1):
        lines.append(f"{indent}#{i} {slot} — {fit.role}")
        body = f"{indent}     "
        lines.extend(
            textwrap.wrap(
                fit.carries, width=width, initial_indent=body, subsequent_indent=body
            )
        )
    return "\n".join(lines)


def remark_lines(flight: str, assignment: Sequence[Loadout]) -> list[str]:
    """The kneeboard's one-line answer to "what has my wingman got?".

    Slots are grouped by fit rather than listed one per slot, because a six-slot
    flight listed individually runs past the card's 98 columns and because the
    fact wanted in the cockpit is which jets hold which half of the frag. Own
    stores are on the SMS page and are not repeated here; the other jet's are
    nowhere in the cockpit at all, which is the test a remark has to pass.
    """
    if not assignment:
        return []
    order: list[str] = []
    slots_by_role: dict[str, list[str]] = {}
    for i, fit in enumerate(assignment, start=1):
        if fit.role not in slots_by_role:
            slots_by_role[fit.role] = []
            order.append(fit.role)
        slots_by_role[fit.role].append(f"#{i}")
    parts = [f"{'/'.join(slots_by_role[role])} {role}" for role in order]
    return [f"{flight} fits: {'; '.join(parts)}"]


#: Where a mission's slot-by-slot assignment is recorded, so the base class can
#: write the kneeboard remark without every mission remembering to. Mirrors the
#: stashes in `core/dtc.py` and `core/mission_kit.py`.
_ASSIGNED = "player_flight_loadouts"


def record(m: Mission, flight: str, assignment: Sequence[Loadout]) -> None:
    """Note what each slot of `flight` was armed with, for the kneeboard."""
    assignments(m).append((flight, tuple(assignment)))


def assignments(m: Mission) -> list[tuple[str, tuple[Loadout, ...]]]:
    """Every player flight's slot-by-slot fit, in the order they were built."""
    stash = getattr(m, _ASSIGNED, None)
    if stash is None:
        stash = []
        setattr(m, _ASSIGNED, stash)
    return stash
