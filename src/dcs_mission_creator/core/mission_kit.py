"""Small helpers every mission script needs, and every one had its own copy of.

`offset`, `mark_clients` and `set_skill` were defined at module scope in five
of the six missions, byte-identical apart from the terrain annotation. They are
here so a mission file starts with its mission rather than with scaffolding.

Deliberately tiny and free of policy: anything that encodes *how hard* a
mission is, or what a package is made of, belongs in the mission or in one of
the opinionated core helpers, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from dcs.unit import Skill

# Re-exported: `set_skill` has lived in air_defense.py since the site builders
# needed it, and it applies to any group, so missions should not have to know
# that. Importing it here keeps that detail out of the mission files.
from dcs_mission_creator.core.air_defense import set_skill

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.unitgroup import FlyingGroup, Group

__all__ = ["arm", "mark_clients", "offset", "set_skill"]


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
