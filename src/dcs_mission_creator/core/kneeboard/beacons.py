"""Navaids for a theater, read off the installed game's `Beacons.lua`.

pydcs knows a beacon *exists* and nothing else. `Airport.beacons` is a list of
`AirportBeacon(id='airfield22_3')` — an id with no type, no frequency and no
position — and its own docstring says so ("This is currently the raw data from
DCS, not a useful API"). `Airport.tacan` is `None` for every Caucasus field,
including Batumi, which has one.

The real data is a file in the install: `Mods/terrains/<Theater>/Beacons.lua`,
which DCS's own F10 airdrome panel reads — type, callsign, frequency, TACAN
channel, world position and antenna direction for every ILS, PRMG, VOR, RSBN,
TACAN and homer on the map. `core/dcs_install.py` already locates the install
for loadouts, so this needs no new configuration; when the install is absent
(CI, a fresh clone) `theater_beacons` logs once and returns `[]`, and the
kneeboard's navaid block comes out empty rather than the build failing.

Two details that decide whether the numbers are right:

- **The join to a pydcs airport is the beacon id**, not proximity.
  `beaconId = 'airfield22_3'` is airport id 22 — Batumi — so the field's own
  navaids are exact rather than "whatever is within 5 km", which would drag in
  the next field's outer homer along a shared approach corridor. Beacons whose
  id carries no airfield number (an en-route VOR, a standalone RSBN) are matched
  by distance instead, and only when the caller asks for them.
- **`position` is `{x, altitude, z}`** in DCS world metres, where `x` is north
  and `z` is east — the same axis swap `mission_kit.offset` exists to hide. So a
  pydcs `Point` is `(position[0], position[2])`; reading it as `(x, y)` puts
  every navaid on the map's equator.

This is a reader, not a redistributor: nothing from the install is copied into
the generated mission, only frequencies and bearings computed from it and
printed on a page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from dcs_mission_creator.core import dcs_install

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.terrain.terrain import Airport, Terrain

log = structlog.get_logger(__name__)

#: `BEACON_TYPE_*` → what a chart calls it. Covers every type present in the
#: Caucasus, Syria and Marianas tables; an unknown one falls back to its own
#: name with the prefix stripped, so a new theater degrades to an ugly label
#: rather than a missing navaid.
_KIND_LABEL = {
    "ILS_LOCALIZER": "ILS LOC",
    "ILS_GLIDESLOPE": "ILS GS",
    "ILS_FAR_HOMER": "OUTER NDB",
    "ILS_NEAR_HOMER": "INNER NDB",
    "PRMG_LOCALIZER": "PRMG LOC",
    "PRMG_GLIDESLOPE": "PRMG GS",
    "AIRPORT_HOMER": "NDB",
    "AIRPORT_HOMER_WITH_MARKER": "NDB (MKR)",
    "HOMER": "NDB",
    "TACAN": "TACAN",
    "VOR": "VOR",
    "VORTAC": "VORTAC",
    "VOR_DME": "VOR/DME",
    "DME": "DME",
    "RSBN": "RSBN",
}

#: Types whose useful number is a TACAN-style channel rather than a frequency.
_CHANNEL_KINDS = frozenset(
    {"TACAN", "VORTAC", "RSBN", "PRMG_LOCALIZER", "PRMG_GLIDESLOPE"}
)

_ENTRY_KEY = re.compile(r"^\s*(\w+)\s*=\s*(.+?);\s*$", re.MULTILINE)
_TRIPLE = re.compile(r"\{\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\}")
_AIRFIELD_ID = re.compile(r"^airfield(\d+)_")


@dataclass(frozen=True)
class Beacon:
    """One navaid, as `Beacons.lua` states it.

    `frequency_hz` is what the file carries for every type; `channel` is set
    only for the channelised systems. `label` turns the pair into the one string
    a kneeboard prints, because "110.30" and "16X" are the same field on a chart.
    """

    beacon_id: str
    display_name: str
    kind: str
    callsign: str
    frequency_hz: float
    channel: int | None
    direction_deg: float | None
    x: float
    y: float
    airfield_id: int | None

    @property
    def kind_label(self) -> str:
        return _KIND_LABEL.get(self.kind, self.kind.replace("_", " "))

    @property
    def tacan_mode(self) -> str | None:
        """`X` or `Y`, worked back from the channel's ground-mode frequency.

        The file states a frequency and a channel but not the mode. A ground
        beacon on channel `n` transmits 962+n-1 MHz (or 1151+n-64) in X and
        1088+n-1 (or 1025+n-64) in Y, so the mode is a subtraction rather than
        a guess. Returns `None` when the pair matches neither, which is how a
        misread would show up instead of printing a confident wrong letter.
        """
        if self.channel is None:
            return None
        mhz = round(self.frequency_hz / 1_000_000.0)
        n = self.channel
        x_mhz = (962 + n - 1) if n < 64 else (1151 + n - 64)
        y_mhz = (1088 + n - 1) if n < 64 else (1025 + n - 64)
        if mhz == x_mhz:
            return "X"
        if mhz == y_mhz:
            return "Y"
        return None

    @property
    def label(self) -> str:
        """The tuning line: a channel for TACAN-likes, MHz or kHz otherwise."""
        if self.kind in _CHANNEL_KINDS and self.channel is not None:
            mode = self.tacan_mode
            return f"CH {self.channel}{mode or ''}"
        khz = self.frequency_hz / 1_000.0
        if khz < 1_000.0:  # NDBs and homers are tuned in kHz.
            return f"{khz:.0f} KHZ"
        return f"{khz / 1_000.0:.2f} MHZ"

    def position(self, terrain: Terrain) -> Point:
        """The beacon's world position, with the axis swap applied once, here."""
        from dcs.mapping import Point

        return Point(self.x, self.y, terrain)


def theater_beacons(terrain: Terrain) -> list[Beacon]:
    """Every beacon on `terrain`'s map, or `[]` with one warning if unavailable."""
    path = _beacons_path(terrain.name)
    if path is None:
        return []
    return list(_parse(path))


def airfield_beacons(
    terrain: Terrain,
    airport: Airport,
    *,
    include_nearby_m: float | None = None,
) -> list[Beacon]:
    """`airport`'s own navaids, by beacon id — see the module docstring.

    `include_nearby_m` additionally takes en-route beacons (the ones with no
    airfield number in their id) within that distance, which is how a field
    inherits the VOR or RSBN sitting next to it. Sorted so the approach aids
    read before the field aids, and deterministically inside each group.
    """
    beacons = [b for b in theater_beacons(terrain) if b.airfield_id == airport.id]
    if include_nearby_m is not None:
        beacons += [
            b
            for b in theater_beacons(terrain)
            if b.airfield_id is None
            and airport.position.distance_to_point(b.position(terrain))
            <= include_nearby_m
        ]
    return sorted(beacons, key=lambda b: (b.kind, b.callsign, b.beacon_id))


# -- internals ---------------------------------------------------------------


def _beacons_path(theater_name: str) -> Path | None:
    """`Mods/terrains/<Theater>/Beacons.lua` for the configured install."""
    install = dcs_install.install_dir()
    if install is None:
        return None
    terrains = install / "Mods" / "terrains"
    wanted = re.sub(r"\W", "", theater_name).lower()
    if not terrains.is_dir():
        log.warning("no terrain data in DCS install", path=str(terrains))
        return None
    for folder in sorted(terrains.iterdir()):
        if re.sub(r"\W", "", folder.name).lower() != wanted:
            continue
        path = folder / "Beacons.lua"
        if path.is_file():
            return path
    log.warning("no Beacons.lua for theater, navaids unavailable", theater=theater_name)
    return None


@lru_cache(maxsize=4)
def _parse(path: Path) -> tuple[Beacon, ...]:
    """Read every depth-2 table out of the `beacons = { … }` list.

    A brace walk rather than a regex over whole entries: an entry contains three
    nested tables (`position`, `positionGeo`, `sceneObjects`), so any
    `\\{[^{}]*\\}` pattern matches those instead of the record holding them.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find("beacons = {")
    if start < 0:
        log.warning("Beacons.lua has no beacons table", path=str(path))
        return ()
    out: list[Beacon] = []
    depth = 0
    entry_start = 0
    for i in range(text.index("{", start), len(text)):
        char = text[i]
        if char == "{":
            depth += 1
            if depth == 2:
                entry_start = i + 1
        elif char == "}":
            depth -= 1
            if depth == 1:
                beacon = _beacon(text[entry_start:i])
                if beacon is not None:
                    out.append(beacon)
            elif depth == 0:
                break
    log.debug("read theater beacons", path=str(path), count=len(out))
    return tuple(out)


def _beacon(block: str) -> Beacon | None:
    """One entry's key/value pairs, or `None` for a record we cannot place."""
    fields = {k: v.strip() for k, v in _ENTRY_KEY.findall(block)}
    beacon_id = _unquote(fields.get("beaconId", ""))
    kind = fields.get("type", "").removeprefix("BEACON_TYPE_")
    position = _TRIPLE.search(fields.get("position", ""))
    if not beacon_id or not kind or position is None:
        return None
    airfield = _AIRFIELD_ID.match(beacon_id)
    return Beacon(
        beacon_id=beacon_id,
        display_name=_unquote(fields.get("display_name", "")),
        kind=kind,
        callsign=_unquote(fields.get("callsign", "")),
        frequency_hz=_number(fields.get("frequency"), 0.0),
        channel=None if "channel" not in fields else int(_number(fields["channel"], 0)),
        direction_deg=None
        if "direction" not in fields
        else _number(fields["direction"]),
        # `{x, altitude, z}`: north, up, east.
        x=float(position.group(1)),
        y=float(position.group(3)),
        airfield_id=None if airfield is None else int(airfield.group(1)),
    )


def _unquote(raw: str) -> str:
    """Strip Lua quoting, including the `_('…')` translation wrapper."""
    text = raw.strip()
    match = re.fullmatch(r"_\(\s*(.*?)\s*\)", text, re.DOTALL)
    if match is not None:
        text = match.group(1).strip()
    return text.strip("'\"")


def _number(raw: str | None, default: float | None = None) -> float:
    if raw is None:
        return 0.0 if default is None else default
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group(0)) if match else (default or 0.0)
