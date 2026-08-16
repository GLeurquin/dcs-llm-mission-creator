"""What the map does *not* show — the other half of the F10 briefing policy.

`core/map_draw.py` decides what the player is told; this decides what he has to
find out. Enemy groups never appear as stock unit icons, so his picture of the
enemy is the briefing plus whatever `PlanOverlay` deliberately draws.

Separate from `map_draw` because these functions never touch a drawing: they
flip visibility flags on groups. Sharing a module only because both are "about
the F10 map" meant a mission that wanted to hide its enemies had to import the
whole `dcs.drawing` stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from dcs.country import Country
    from dcs.unitgroup import Group


def conceal(*groups: Optional[Group]) -> None:
    """Hide `groups` from the F10 map, the mission planner, and the datalink.

    Purely cosmetic: a concealed group still spawns, radiates, moves and
    shoots. `None` entries are skipped so callers can pass optional spawns (a
    reserve that failed placement) straight in.
    """
    for group in groups:
        if group is None:
            continue
        group.hidden = True  # F10 map in game
        group.hidden_on_planner = True  # briefing / mission-planner map
        group.hidden_on_mfd = True  # datalink & MFD symbology


def conceal_country(*countries: Country) -> None:
    """`conceal` every group a country owns — aircraft, vehicles, ships, statics.

    The blanket form, and the one missions should call: it cannot miss the
    late-activated reserve or the EWR added three months after the briefing
    was written. Call it once all enemy spawns exist (just before the
    `_draw_plan` step).
    """
    for country in countries:
        conceal(
            *country.vehicle_group,
            *country.ship_group,
            *country.plane_group,
            *country.helicopter_group,
            *country.static_group,
        )
