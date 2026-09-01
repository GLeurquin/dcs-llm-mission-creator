"""Small helpers every mission script needs, and every one had its own copy of.

`offset`, `mark_clients` and `set_skill` were defined at module scope in five
of the six missions, byte-identical apart from the terrain annotation. They are
here so a mission file starts with its mission rather than with scaffolding.

Deliberately tiny and free of policy: anything that encodes *how hard* a
mission is, or what a package is made of, belongs in the mission or in one of
the opinionated core helpers, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from dcs.unit import Skill
from dcs.unittype import UnitType

# Re-exported: `set_skill` has lived in air_defense.py since the site builders
# needed it, and it applies to any group, so missions should not have to know
# that. Importing it here keeps that detail out of the mission files.
from dcs_mission_creator.core.air_defense import set_skill

if TYPE_CHECKING:
    from dcs.country import Country
    from dcs.mapping import Point
    from dcs.mission import Mission, StartType
    from dcs.task import MainTask
    from dcs.terrain.terrain import Airport
    from dcs.unit import Unit
    from dcs.unitgroup import FlyingGroup, Group
    from dcs.unittype import FlyingType

__all__ = [
    "arm",
    "mark_clients",
    "MAX_FLIGHT_SIZE",
    "offset",
    "player_flight",
    "RaceTrack",
    "race_track",
    "section_names",
    "section_sizes",
    "sections_of",
    "set_skill",
    "unit_of_type",
]


def offset(origin: Point, *, east_m: float = 0.0, north_m: float = 0.0) -> Point:
    """Return a point offset from `origin` in DCS world metres.

    DCS's world axes read the other way round from the names: `x` is north and
    `y` is east. Every mission had this wrapper precisely so that its call sites
    could say `east_m=` / `north_m=` and stop re-deriving which axis is which.

    Takes no terrain argument — the origin already carries one.
    """
    return origin.new_in_same_map(origin.x + north_m, origin.y + east_m)


def mark_clients(group: Group) -> None:
    """Mark every unit in `group` as a coop client slot."""
    for u in group.units:
        u.skill = Skill.Client


def arm(
    group: FlyingGroup,
    plane_type: type,
    stores: Sequence[tuple[int, str]],
) -> None:
    """Load `stores` — `(pylon, weapon attribute)` — as the flight's whole loadout.

    Spell a loadout out whenever the briefing promises specific stores. pydcs
    fills pylons from `load_task_default_loadout`, which reads the *installed
    game* — so with `DCS_INSTALL_DIR` unset every flight launches clean, and
    with it set the flight carries whatever DCS's task default happens to be
    rather than what the briefing said.

    Stations are cleared first: `Mission.flight_group_*` has already run the
    task default, and without the clear those weapons survive on every station
    this list skips.

    The `PylonN` classes on each `PlaneType` enumerate what a station legally
    accepts, so these pairs are checked against pydcs rather than guessed —
    a wrong name is an `AttributeError` at build time, not a silent empty rail.
    """
    for unit in group.units:
        unit.pylons.clear()
    for pylon, weapon in stores:
        group.load_pylon(getattr(getattr(plane_type, f"Pylon{pylon}"), weapon))


def unit_of_type(group: Group, vehicle_type: type[UnitType]) -> Unit:
    """The first unit in `group` of `vehicle_type`, or `LookupError`.

    Objectives that mean "kill the radar" should say so. Reaching for
    `group.units[0]` works only while the site is hand-built in a known order —
    pydcs's own `VehicleTemplate.Russia.sa10_site`, for instance, puts a
    paratrooper at index 1, so an index-based win condition silently becomes
    "kill one infantryman". Raising here turns that into a build failure.
    """
    for unit in group.units:
        if unit.type == vehicle_type.id:
            return unit
    raise LookupError(
        f"{group.name} has no {vehicle_type.id}; it has "
        f"{sorted({u.type for u in group.units})}"
    )


@dataclass(frozen=True)
class RaceTrack:
    """An orbit leg as pydcs wants it: one end, a length, and a bearing.

    `Mission.awacs_flight` and `Mission.refuel_flight` do not take the two ends
    of the track. Every AWACS and tanker in the project converted them the same
    way, and the conversion has two easy mistakes in it — dropping the `int()`,
    and swapping the ends so the aircraft flies the leg backwards.
    """

    position: Point
    race_distance: int
    heading: int


def race_track(p1: Point, p2: Point) -> RaceTrack:
    """The orbit from `p1` to `p2`, in the terms pydcs asks for.

    Altitude, speed, frequency and TACAN stay at the call site — those are
    per-mission decisions, and this only converts the geometry.
    """
    return RaceTrack(
        position=p1,
        race_distance=int(p1.distance_to_point(p2)),
        heading=int(p1.heading_between_point(p2)),
    )


#: The most airframes a DCS fixed-wing group can hold. It is a hard limit of
#: the format rather than a convention, and pydcs does not enforce it — it
#: *clamps*: `Mission.flight_group_from_airport` does
#: `group_size = min(group_size, aircraft_type.group_size_max)` and returns
#: silently, so asking for six slots in one group used to hand back four and
#: say nothing. Anything above this is a list of flights, never a bigger one.
MAX_FLIGHT_SIZE = 4


def section_sizes(total: int, *, maximum: int = MAX_FLIGHT_SIZE) -> tuple[int, ...]:
    """Split `total` airframes into DCS-legal flights, biggest first.

    A four-ship trailed by a single ship is neither realistic nor useful — on
    the enemy side the lone jet dies first and its `GroupDead` gates a win
    condition on one airframe, on ours it is a player sitting on his own — so a
    would-be remainder of one is taken out of the flight ahead of it instead.
    Five is `(3, 2)`, six is `(4, 2)`.
    """
    sizes: list[int] = []
    left = total
    while left > 0:
        take = min(left, maximum)
        if left - take == 1:
            take -= 1
        sizes.append(take)
        left -= take
    return tuple(sizes)


#: Where the sections of one player flight are recorded on the mission, so the
#: helpers that used to assume "one player flight, one group" can still tell a
#: second section from a second flight. Mirrors the stashes in `core/dtc.py`
#: and `core/kneeboard/publish.py`.
_SECTIONS = "player_flight_sections"


def player_flight(
    m: Mission,
    *,
    country: Country,
    name: str,
    aircraft_type: type[FlyingType],
    airport: Airport,
    maintask: type[MainTask],
    start_type: StartType,
    slots: int,
    stores: Sequence[tuple[int, str]],
) -> list[FlyingGroup]:
    """Build the player flight as however many DCS-legal sections it takes.

    Above `MAX_FLIGHT_SIZE` coop slots the flight is two groups — `Dodge` and
    `Dodge 2` — because a plane group holds four aircraft and pydcs clamps
    rather than raises. They are one flight in every sense the player cares
    about: same field, same loadout, and the caller gives each the same route.
    What differs is what DCS makes differ — parking, callsign, track-number
    block — so each section still reads as itself on the net and on its card.

    Returns the sections in slot order, lead first; a mission that needs "the
    player flight" for a trigger wants all of them (`sections_of`).
    """
    sections: list[FlyingGroup] = []
    sizes = section_sizes(slots)
    for section_name, size in zip(section_names(name, len(sizes)), sizes):
        group = m.flight_group_from_airport(
            country=country,
            name=section_name,
            aircraft_type=aircraft_type,
            airport=airport,
            maintask=maintask,
            start_type=start_type,
            group_size=size,
        )
        mark_clients(group)
        arm(group, aircraft_type, stores)
        sections.append(group)
    _sections(m).append(tuple(sections))
    return sections


def sections_of(m: Mission, group: FlyingGroup) -> tuple[FlyingGroup, ...]:
    """The sections `group` was built as part of, or just `group` itself.

    For the callers that hold one flight and have to act on all of it: a trigger
    gated on the player being somewhere, a cartridge that refuses two routes.
    """
    for sections in _sections(m):
        if group in sections:
            return sections
    return (group,)


def section_names(name: str, sections: int) -> tuple[str, ...]:
    """What each section of `name` is called: `Dodge`, `Dodge 2`, ...

    The briefing has to be able to say this without holding the built groups —
    `readme()` takes no mission — so the naming lives here rather than inside
    `player_flight`, and both read it from the same place.
    """
    return tuple(name if i == 1 else f"{name} {i}" for i in range(1, sections + 1))


def _sections(m: Mission) -> list[tuple[FlyingGroup, ...]]:
    """The mission's record of which groups are sections of one flight."""
    stash = getattr(m, _SECTIONS, None)
    if stash is None:
        stash = []
        setattr(m, _SECTIONS, stash)
    return stash
