"""Which airfields the theatre already puts a chart of on the player's kneeboard.

This is the gate on the airfield card, and it exists because the answer is not
"all of them" and not "none of them" — it is per theatre, and per field:

| theatre  | `Mods/terrains/<T>/Kneeboard/`                          |
|----------|---------------------------------------------------------|
| Caucasus | 21 fields, ground diagram *and* approach chart for each |
| Syria    | **three** — Akrotiri, Incirlik, Beirut. Nothing else.    |
| Marianas | one theatre map, no field at all                        |

So on Caucasus this project should draw nothing (ED's surveyed charts are two
pages away and better than anything derivable here), while on Syria a player
starting at Hatay — which `idlib_gauntlet` does — has no page about their own
field at all. Printing one there is the difference between a useful card and a
redundant one, and it is a question about the install rather than a judgement
call, so it is answered by looking.

**With no install we cannot know, and then the card is printed.** The two failure
modes are not symmetric: a redundant page costs a page, a missing one costs the
player the field's elevation, its ATC channel and where their own jet is parked.
That does mean a build without `$DCS_INSTALL_DIR` emits pages a full build would
not, which is a property of the install and is logged.

Matching is on the file name, because that is all the name a chart has
(`07_GND_UGSB_Batumi_18.png`, `Rafic_Hariri_Intl_p1.png`) — the ICAO in the
Caucasus set is not in the Syria set, and pydcs carries no ICAO to join on either
way. Two rules, and the second is what makes Beirut work: a field is covered if
**every** word of its name appears in one file name, or if **two long words** of
it do (`Beirut-Rafic Hariri` against `Rafic_Hariri_Intl`). The all-words rule
alone is what keeps `Krasnodar-Center` from being declared covered by
`Krasnodar-Pashkovsky`'s chart.

**What ED ships is not "all fields".** Caucasus has 21, with a ground diagram
*and* an approach chart for each; Syria has **three** — Akrotiri, Incirlik and
Beirut — and Marianas has one theatre map and no field at all. So
`idlib_gauntlet`'s player, who starts at Hatay, had no page about their own
field.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from dcs_mission_creator.core import dcs_install

if TYPE_CHECKING:
    from dcs.terrain.terrain import Airport, Terrain

log = structlog.get_logger(__name__)

#: Words this short carry no identity ("AB", "p1", "Intl", theatre numbering).
_MIN_WORD = 4
#: A word this long is distinctive enough that two of them are a match on their own.
_LONG_WORD = 5


def has_chart(terrain: Terrain, airport: Airport) -> bool:
    """Does the installed theatre ship a kneeboard chart of `airport`?

    `False` when the theatre ships charts but not for this field, and `False` when
    the install is unavailable — see the module docstring on why the unknown case
    resolves that way.
    """
    names = _chart_names(terrain.name)
    if not names:
        return False
    words = [w for w in re.split(r"\W+", airport.name.lower()) if len(w) >= _MIN_WORD]
    if not words:
        return False
    long_words = [w for w in words if len(w) >= _LONG_WORD]
    for name in names:
        present = [w for w in words if w in name]
        if len(present) == len(words):
            return True
        if len([w for w in long_words if w in name]) >= 2:
            return True
    return False


@lru_cache(maxsize=8)
def _chart_names(theater_name: str) -> tuple[str, ...]:
    """Every chart file name in the theatre's kneeboard folder, lower-cased."""
    install = dcs_install.install_dir()
    if install is None:
        log.warning(
            "cannot tell which airfields DCS charts, printing airfield cards for all",
            theater=theater_name,
        )
        return ()
    wanted = re.sub(r"\W", "", theater_name).lower()
    terrains = install / "Mods" / "terrains"
    if not terrains.is_dir():
        return ()
    for folder in sorted(terrains.iterdir()):
        if re.sub(r"\W", "", folder.name).lower() != wanted:
            continue
        return _names_in(folder / "Kneeboard")
    log.warning("no terrain folder for theater", theater=theater_name)
    return ()


def _names_in(folder: Path) -> tuple[str, ...]:
    if not folder.is_dir():
        log.debug("theater ships no kneeboard charts", folder=str(folder))
        return ()
    names = tuple(sorted(p.name.lower() for p in folder.iterdir() if p.is_file()))
    log.debug("theater kneeboard charts", folder=str(folder), count=len(names))
    return names
