"""Check a loadout against what the game itself flies (project-owned).

pydcs answers one question about a store: does this station accept it. That is
a real check and `core/loadout.arm_unit` gets it for free — a wrong attribute
name is an `AttributeError` at build time rather than a silently empty rail.
It is also a low bar. **Legal is not realistic**: pydcs will happily put a
Sidewinder on an F-16C's station 3 and an AMRAAM on its station 2, which is the
pair every mission in this project had the wrong way round until somebody read
ED's own payload tables and found that the wingtips carry the AMRAAM.

Those tables are the authority, and they are on disk:

    <DCS>/CoreMods/aircraft/<module>/UnitPayloads/<type>.lua
    <DCS>/MissionEditor/data/scripts/UnitPayloads/<type>.lua

Every loadout ED ships for an airframe, as (CLSID, station) pairs. So "which
stations does the game actually hang this store on" is a lookup rather than a
memory, and a store on a station no shipped payload uses is worth a second look
— not an error, because a mission may have a reason, but the kind of thing that
should be a decision rather than an accident.

    from dcs_mission_creator.core import loadout_check

    for note in loadout_check.check(planes.F_16C_50, fit.stores):
        print(note)

With no `DCS_INSTALL_DIR` there is nothing to read and every check comes back
empty, exactly as `load_task_default_loadout` does — the tables ship with the
game, not with this project, and a check that guessed without them would be
worse than no check. `core/audit.py` says so out loud rather than reporting a
clean bill of health it did not earn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import structlog
from dcs.weapons_data import weapon_ids

from dcs_mission_creator.core import dcs_install

if TYPE_CHECKING:
    from dcs.unitgroup import FlyingGroup
    from dcs.unittype import FlyingType

log = structlog.get_logger(__name__)

#: Where the game keeps its payload tables, relative to the install root. The
#: first is per-module (the F-16C, the Hornet, anything with its own folder),
#: the second the editor's own set for the types that do not have one.
_PAYLOAD_DIRS = (
    "CoreMods/aircraft/*/UnitPayloads",
    "MissionEditor/data/scripts/UnitPayloads",
)

#: `["CLSID"] = "..."` and `["num"] = N`, in the order the file writes them —
#: pairing each CLSID with the next `num` is what turns a payload block into
#: (store, station) pairs without parsing Lua properly. The tables are generated
#: and uniform, so this holds; anything it mis-reads shows up as a store with no
#: stations at all rather than as a wrong answer.
_ENTRY = re.compile(r'\["CLSID"\]\s*=\s*"([^"]*)"|\["num"\]\s*=\s*(\d+)')


@dataclass(frozen=True)
class Note:
    """One thing worth a second look about a station assignment."""

    pylon: int
    store: str
    message: str

    def __str__(self) -> str:
        return f"station {self.pylon}: {self.store} — {self.message}"


@lru_cache(maxsize=None)
def ed_stations(aircraft_id: str) -> dict[str, frozenset[int]]:
    """`{CLSID: stations ED puts it on}` for one airframe, from the install.

    Empty when the game is not installed or ships no table for the type, which
    the caller has to treat as "unknown" rather than as "nothing is allowed".
    """
    root = dcs_install.install_dir()
    if root is None:
        return {}
    seen: dict[str, set[int]] = {}
    for pattern in _PAYLOAD_DIRS:
        for directory in sorted(root.glob(pattern)):
            path = directory / f"{aircraft_id}.lua"
            if path.is_file():
                _read_payloads(path, seen)
    return {clsid: frozenset(stations) for clsid, stations in seen.items()}


def _read_payloads(path: Path, into: dict[str, set[int]]) -> None:
    """Fold one payload file's (CLSID, station) pairs into `into`."""
    text = path.read_text(encoding="utf-8", errors="replace")
    pending: str | None = None
    for match in _ENTRY.finditer(text):
        clsid, num = match.groups()
        if clsid is not None:
            pending = clsid
        elif pending is not None:
            into.setdefault(pending, set()).add(int(num))
            pending = None


def clsid_for(aircraft_type: type[FlyingType], pylon: int, store: str) -> str | None:
    """The CLSID a `(pylon, store attribute)` pair names, or None if it is not one.

    The stores in a `core/loadout.Loadout` are pydcs attribute names on the
    airframe's `PylonN` class, which is what makes a wrong one fail at build
    time; the payload tables are keyed by CLSID, so the two have to be joined
    before anything can be compared.
    """
    station = getattr(aircraft_type, f"Pylon{pylon}", None)
    entry = getattr(station, store, None) if station is not None else None
    # A `PylonN` attribute is `(station number, {clsid, name, weight})` — a
    # lowercase `clsid`, unlike the `CLSID` a *loaded* pylon carries on a unit.
    # The two spellings are the reason `check` and `check_group` cannot share
    # one lookup.
    if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], dict):
        value = entry[1].get("clsid")
        return str(value) if value else None
    return None


def store_name(clsid: str) -> str:
    """DCS's own display name for a CLSID, or the CLSID when it has none.

    Both sides of this comparison are CLSIDs and neither is readable, so every
    message goes through here — "the game ships the LITENING here" is a note
    somebody can act on and a pair of GUIDs is not.
    """
    weapon = weapon_ids.get(clsid)
    return str(weapon["name"]) if weapon else clsid


def _shipped_here(table: dict[str, frozenset[int]], pylon: int) -> list[str]:
    """Every store ED's own payloads hang on `pylon`, by display name."""
    return sorted({store_name(other) for other, at in table.items() if pylon in at})


def _station_note(
    table: dict[str, frozenset[int]], pylon: int, clsid: str, shown: str
) -> Note | None:
    """What is unusual about `clsid` on `pylon`, if anything."""
    stations = table.get(clsid)
    if stations is not None and pylon in stations:
        return None
    if stations is not None:
        usual = ", ".join(str(s) for s in sorted(stations))
        # Naming what the station *does* carry is what separates the two things
        # this branch catches, and they are not the same finding at all. An
        # F-16C with an AMRAAM on station 2 is a swapped pair and a bug — the
        # game hangs a Sidewinder there and nothing else. An F-15C with an
        # AIM-120C on station 4 is a sub-variant substitution: ED's own payloads
        # put an AIM-120*B* on that station, so the jet is carrying the right
        # kind of missile in the right place and a better model of it. Printed
        # without the second half, those read identically, and twenty of the
        # thirty-one findings across this repo were the harmless one.
        instead = _shipped_here(table, pylon)
        if instead:
            return Note(
                pylon,
                shown,
                f"the game flies this on station(s) {usual}; "
                f"here it ships {', '.join(instead)}",
            )
        return Note(pylon, shown, f"the game flies this on station(s) {usual}")
    # Not in any shipped payload for the type. What the game *does* put here is
    # the useful half — an unlisted store is often a working alternative to a
    # listed one (the Sniper pod where ED ships the LITENING), and naming the
    # alternative is the difference between a note and a shrug.
    instead = _shipped_here(table, pylon)
    if instead:
        return Note(
            pylon,
            shown,
            f"no shipped payload carries it; the game ships {', '.join(instead)} here",
        )
    return Note(pylon, shown, "no shipped payload carries this store")


def check(
    aircraft_type: type[FlyingType], stores: Sequence[tuple[int, str]]
) -> list[Note]:
    """What is unusual about hanging `stores` on `aircraft_type`.

    Two findings, and neither is fatal: a store the game never puts on this
    station, and a store no shipped payload for this airframe carries at all.
    Both are "check this was deliberate" rather than "this is broken" — a
    mission is allowed to frag something ED does not, and should be able to say
    why. The pair that made this worth writing was AMRAAM and Sidewinder on an
    F-16C: legal either way round, and wrong one way round in every mission
    here for months.
    """
    table = ed_stations(aircraft_type.id)
    if not table:
        return []
    notes: list[Note] = []
    for pylon, store in stores:
        clsid = clsid_for(aircraft_type, pylon, store)
        if clsid is None:
            continue
        note = _station_note(table, pylon, clsid, store)
        if note is not None:
            notes.append(note)
    return notes


def check_group(group: FlyingGroup) -> list[Note]:
    """The same check against a *built* flight, read off its loaded pylons.

    Works from CLSIDs directly, so it answers for whatever ended up on the jet
    — a task default pydcs filled in, a store a mission set by hand — rather
    than for what a `Loadout` said. That is the version `core/audit.py` wants:
    the question there is what is about to ship, not what was intended.
    """
    unit = group.units[0]
    table = ed_stations(unit.unit_type.id)
    if not table:
        return []
    notes: list[Note] = []
    for pylon, loaded in sorted(unit.pylons.items()):
        clsid = loaded.get("CLSID", "")
        if not clsid:
            continue
        note = _station_note(table, pylon, clsid, store_name(clsid))
        if note is not None:
            notes.append(note)
    return notes
