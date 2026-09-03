"""Render the cards, put them in the `.miz`, and leave a copy beside the README.

Two things about how a kneeboard page gets into a mission are worth stating,
because both pushed this off pydcs's own helper:

- **`Mission.add_aircraft_kneeboard` writes the archive entry as
  `f'{directory}/{page.name}'` where `directory` already ends in `/`**, so every
  page lands at `KNEEBOARD/<type>/IMAGES//<name>.png` — an empty path component
  in the middle. Rather than ship a path shape DCS may or may not resolve, the
  entries are appended here with the arcname spelled out, exactly as
  `core/dtc.py` appends its cartridges and for the same reason: `Mission.save`
  writes a fixed set of entries with no hook for another one.
- **Writing them ourselves also fixes the timestamp.** pydcs would use
  `zipf.write`, which records the source file's mtime and mode into the archive,
  so a re-render changed the `.miz` even when every pixel was identical —
  `core/recon/publish.py` has to pin mtime and mode on disk to work around
  exactly that. An explicit `ZipInfo` needs no pin.

DCS has no per-flight kneeboard: a page goes into a folder named after an
aircraft *type* and everyone in that type sees it. So a mission with two player
flights of different airframes gets both route cards in both folders, and each
card names its flight in the title — better than a card that silently belongs to
someone else's jet.

It also already has the theatre's aerodrome and approach charts on that same
kneeboard — for the fields the theatre ships them for, which on Syria is three of
them. So an airfield page is written only for a field `kneeboard/charts.py` found
no shipped chart of: on Caucasus that is none, and on Syria it is Hatay, where
`idlib_gauntlet`'s player starts.

The PNGs also land in `<output>/kneeboard/`, next to the README, because a card
that cannot be read outside the game is hard to check and impossible to review.

**A remark's wrap is a floor, not a licence.** Wrapping guarantees nothing is
silently truncated — which it was, until `coastal_cover` put two long remarks on
the card and lost the halves that mattered: the laser code survived and "where to
find the readout" ran off the right edge. The *ceiling* is one line of
`page.COLUMNS`, because a mission with a primary field, a divert and a JTAC
carries five remarks and one that runs over costs two lines of the block. Both
halves matter because they fail differently: without the floor a remark loses its
back half and nobody can tell, and without the ceiling every remark is two lines
and the block is prose.

The test for a line that will not fit is not "shorten it" but **"which half of
this is a fact the page cannot derive, and which half is *explaining* the
fact"** — the first stays on the card, the second is briefing prose and is
probably already in the README. `coastal_cover`'s readout line was 109
characters, of which 35 explained that DCS's own nine-line is a grid; the README
had said so all along, so the card lost that clause and kept the menu path.
Verify on the rendered page rather than by counting characters: the width is a
function of the font, so the card is what knows.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import structlog
from PIL import Image

from dcs_mission_creator.core import mission_kit
from dcs_mission_creator.core.kneeboard import charts, pages as page_content
from dcs_mission_creator.core.kneeboard.airfields import airfield_cards

if TYPE_CHECKING:
    from dcs.mission import Mission

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: 1980-01-01T00:00:00Z as a zip date tuple — the earliest the format can hold,
#: and the same fixed stamp `core/dtc.py` writes, so two builds match entry for
#: entry.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: Where a mission parks its extra kneeboard remarks (laser codes, a controller's
#: entry in the F10 menu) until the comms card is built. Mirrors the stashes in
#: `core/dtc.py` and `core/recon/publish.py`.
_STASH = "kneeboard_remarks"


@dataclass(frozen=True)
class KneeboardPage:
    """One rendered page: its name in the archive and its copy on disk."""

    name: str
    path: Path


def remark(m: Mission, text: str) -> None:
    """Add a line to the comms card's REMARKS block.

    For the handful of facts that are real but not in the mission file: a JTAC's
    laser code (DCS's own default, not a field pydcs writes), where a radio
    request lives in the F10 menu, a bingo number the briefing sets. Everything
    else on the cards is derived, and should stay that way — a remark is prose and
    prose goes stale.
    """
    _stash(m).append(text)


def publish(
    m: Mission,
    miz_path: Path,
    *,
    overlay: MapOverlay | None = None,
    title: str | None = None,
) -> list[KneeboardPage]:
    """Build every card, write the PNGs, and append them to the saved `.miz`.

    Called by `MissionBuilder.build_miz` after the save, so a mission cannot
    forget it and no mission has to know the archive layout. Returns the pages
    written, in kneeboard order.
    """
    flights = mission_kit.player_groups(m)
    if not flights:
        log.warning("no client slot, no kneeboard written", miz=str(miz_path))
        return []

    name = title or miz_path.stem.replace("_", " ")
    cards = airfield_cards(m, overlay=overlay)
    built: list[tuple[str, Image.Image]] = []
    index = 1
    for flight in flights:
        label = (
            f"FLIGHT-PLAN-{_slug(flight.name)}" if len(flights) > 1 else "FLIGHT-PLAN"
        )
        index = _collect(
            built,
            index,
            label,
            page_content.flight_plan_page(
                m, flight, title=name, cards=cards, overlay=overlay
            ),
        )
    index = _collect(
        built,
        index,
        "COMMS",
        page_content.comms_page(m, cards, title=name, remarks=_stash(m)),
    )
    uncharted = [c for c in cards if not charts.has_chart(m.terrain, c.airport)]
    for card in uncharted:
        index = _collect(
            built,
            index,
            f"AIRFIELD-{_slug(card.airport.name)}",
            page_content.airfield_page(m, card, title=name),
        )

    written = _write_files(built, miz_path.parent / "kneeboard")
    _append_to_miz(m, miz_path, written, flights)
    log.info(
        "kneeboard published",
        pages=[p.name for p in written],
        airframes=sorted({_type_id(f) for f in flights}),
        uncharted=[c.airport.name for c in uncharted],
    )
    return written


# -- internals ---------------------------------------------------------------


def _collect(built: list[tuple[str, Image.Image]], index: int, label: str, page) -> int:
    """Render `page` (which may paginate) into numbered entries."""
    images = page.images()
    for number, image in enumerate(images, start=1):
        suffix = "" if len(images) == 1 else f"-{number}"
        built.append((f"{index:02d}-{label}{suffix}.png", image))
        index += 1
    return index


def _write_files(
    built: Sequence[tuple[str, Image.Image]], dest: Path
) -> list[KneeboardPage]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for name, image in built:
        path = dest / name
        # No `pnginfo`: Pillow writes a tIME chunk only when handed one, and a
        # build timestamp inside the file would defeat the fixed zip stamp.
        image.save(path, format="PNG", compress_level=6, optimize=False)
        out.append(KneeboardPage(name=name, path=path))
    return out


def _append_to_miz(
    m: Mission, miz_path: Path, written: Sequence[KneeboardPage], flights
) -> None:
    """One copy of every page per player airframe — see the module docstring."""
    types = sorted({_type_id(flight) for flight in flights})
    with zipfile.ZipFile(miz_path, "a", compression=zipfile.ZIP_DEFLATED) as zipf:
        for type_id in types:
            for page in written:
                info = zipfile.ZipInfo(
                    f"KNEEBOARD/{type_id}/IMAGES/{page.name}", date_time=_ZIP_DATE
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                zipf.writestr(info, page.path.read_bytes())


def _type_id(flight) -> str:
    return flight.units[0].unit_type.id if flight.units else "UNKNOWN"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.upper()).strip("-")


def _stash(m: Mission) -> list[str]:
    remarks = getattr(m, _STASH, None)
    if remarks is None:
        remarks = []
        setattr(m, _STASH, remarks)
    return remarks
