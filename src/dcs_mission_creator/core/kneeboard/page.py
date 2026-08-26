"""A kneeboard page: monospaced text blocks, an optional sketch, and paging.

Everything on a kneeboard is a table, so the page is a list of text lines in one
monospaced face and the layout is character columns — no measuring, no wrapping
heuristics, and a column that lines up in the source lines up in the cockpit.
The only non-text block is `art`, a reserved band a caller draws into (the
airfield sketch), which keeps the drawing code out of the layout code.

Three decisions worth stating:

- **Portrait 3:4 at 1536 x 2048.** DCS scales a kneeboard page into a panel of
  that shape, so anything else is letterboxed; 1536 wide fits 98 monospace
  columns at the body size, which is what makes a route table with headings,
  distances and times fit on one line.
- **Dark ink on warm paper.** The kneeboard is drawn in the cockpit at whatever
  the ambient light is, and a light page stays readable in a dark pit while a
  dark one washes out in daylight.
- **Overflow paginates, it does not clip.** A page that silently dropped the
  last two waypoints of a route would be worse than no page, so blocks are
  packed into as many images as they need and each carries `(n/N)`.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field, replace
from typing import Callable, Sequence

from PIL import Image, ImageDraw

from dcs_mission_creator.core import fonts

#: DCS renders a kneeboard page into a 3:4 portrait panel.
PAGE_W, PAGE_H = 1536, 2048

_MARGIN = 56
_BODY = 24
_SMALL = 20
_HEADING = 28
_TITLE = 40
_LINE_H = 32
_SMALL_LINE_H = 27

#: Warm paper, near-black ink — see the module docstring.
PAPER = (246, 243, 236)
INK = (24, 24, 24)
MUTED = (96, 96, 96)
RULE = (150, 146, 138)

#: Body columns available between the margins, at the body size, and at the
#: smaller size the notes are set in. A table is written to fit `COLUMNS`; prose
#: is wrapped to `NOTE_COLUMNS` by `Page.note`, because a note long enough to be
#: worth writing is long enough to run off the page.
COLUMNS = int((PAGE_W - 2 * _MARGIN) / fonts.char_width(fonts.mono(_BODY)))
NOTE_COLUMNS = int((PAGE_W - 2 * _MARGIN) / fonts.char_width(fonts.mono(_SMALL)))


@dataclass(frozen=True)
class Column:
    """One table column: its heading, its width in characters, its alignment."""

    header: str
    width: int
    align: str = "<"


@dataclass
class _Text:
    text: str
    bold: bool = False
    small: bool = False
    gap_before: int = 0
    keep_with_next: bool = False
    muted: bool = False
    #: Which table this line belongs to, and which of the table's three roles it
    #: plays: its heading (`ROUTE`), its column headers, or one of its rows. A
    #: table broken across pages repeats the first two and numbers the parts.
    table_id: int | None = None
    table_header: bool = False
    table_title: bool = False

    @property
    def height(self) -> int:
        return self.gap_before + (_SMALL_LINE_H if self.small else _LINE_H)


@dataclass
class _Art:
    height: int
    draw: Callable[[ImageDraw.ImageDraw, tuple[int, int, int, int]], None]
    gap_before: int = 0

    @property
    def height_total(self) -> int:
        return self.height + self.gap_before


_Block = _Text | _Art


@dataclass
class Page:
    """A page under construction. Call `images()` when the blocks are in."""

    title: str
    subtitle: str = ""
    label: str = ""
    footer: str = ""
    blocks: list[_Block] = field(default_factory=list)
    #: Table bookkeeping: the running id, the heading text each table was filed
    #: under, and the section block that heading came from — see `table`.
    _tables: int = 0
    _titles: dict[int, str] = field(default_factory=dict)
    _section: _Text | None = None

    # -- content ------------------------------------------------------------

    def line(
        self,
        text: str = "",
        *,
        bold: bool = False,
        small: bool = False,
        muted: bool = False,
        gap_before: int = 0,
        keep_with_next: bool = False,
    ) -> None:
        """One body line. `keep_with_next` refuses to be the last line on a page."""
        self.blocks.append(
            _Text(
                text,
                bold=bold,
                small=small,
                muted=muted,
                gap_before=gap_before,
                keep_with_next=keep_with_next,
            )
        )

    def note(self, text: str) -> None:
        """Prose in the small muted face, wrapped to the page instead of clipped."""
        for line in textwrap.wrap(text, width=NOTE_COLUMNS) or [""]:
            self.line(line, small=True, muted=True)

    def section(self, heading: str) -> None:
        """A heading, kept on the same page as the line that follows it."""
        block = _Text(heading.upper(), bold=True, gap_before=18, keep_with_next=True)
        self.blocks.append(block)
        self._section = block

    def table(
        self,
        columns: Sequence[Column],
        rows: Sequence[Sequence[str]],
        *,
        title: str | None = None,
    ) -> None:
        """A heading row plus `rows`, padded to `columns` — one space between.

        A table too long for one page repeats its column headers on the next and
        numbers the parts — `ROUTE (1 OF 2)`, `ROUTE (2 OF 2)` — because a column
        of figures with nothing written over it is a column nobody can read, and
        because a route continued overleaf has to say so or it reads as the whole
        route. The number is only written when there *is* more than one part.
        `title` names the parts; it defaults to the section heading above.
        """
        self._tables += 1
        table_id = self._tables
        self._titles[table_id] = (
            title or (self._section.text if self._section else "") or "TABLE"
        ).upper()
        if self._section is not None and self._section.table_id is None:
            self._section.table_id = table_id
            self._section.table_title = True
        self.blocks.append(
            _Text(
                _row(columns, [c.header for c in columns]),
                bold=True,
                keep_with_next=True,
                table_id=table_id,
                table_header=True,
            )
        )
        for row in rows:
            self.blocks.append(_Text(_row(columns, row), table_id=table_id))

    def art(
        self,
        height: int,
        draw: Callable[[ImageDraw.ImageDraw, tuple[int, int, int, int]], None],
    ) -> None:
        """Reserve `height` px and let `draw(draw, (x0, y0, x1, y1))` fill it."""
        self.blocks.append(_Art(height=height, draw=draw, gap_before=14))

    # -- output -------------------------------------------------------------

    def parts(self) -> list[list[_Block]]:
        """The blocks laid out page by page, with every split table labelled.

        Separate from `images` so the layout can be asserted on as text: what a
        broken table does at the seam is the part worth testing, and comparing
        pixels would not say which page a row landed on.
        """
        headers = {
            b.table_id: b
            for b in self.blocks
            if isinstance(b, _Text) and b.table_header and b.table_id is not None
        }
        pages = _paginate(self.blocks, self._body_height(), headers)
        self._number_parts(pages)
        return pages

    def images(self) -> list[Image.Image]:
        """Render to one image per page, in order."""
        pages = self.parts()
        return [
            self._render(page, index + 1, len(pages))
            for index, page in enumerate(pages)
        ]

    def _number_parts(self, pages: Sequence[Sequence[_Block]]) -> None:
        """Label a split table's headings `(n OF N)`, in place on the page copies.

        Pagination hands back copies, so this rewrites what is about to be
        rendered and never the block list — calling `images()` twice numbers the
        same table the same way rather than nesting one label inside the last.
        """
        for table_id, title in self._titles.items():
            parts = [
                index
                for index, page in enumerate(pages)
                if any(getattr(b, "table_id", None) == table_id for b in page)
            ]
            if len(parts) < 2:
                continue
            for number, index in enumerate(parts, start=1):
                for block in pages[index]:
                    if isinstance(block, _Text) and block.table_title:
                        if block.table_id == table_id:
                            block.text = f"{title} ({number} OF {len(parts)})"

    def _body_height(self) -> int:
        top = _MARGIN + _TITLE + 12 + (_LINE_H if self.subtitle else 0) + 20
        bottom = _MARGIN + (_SMALL_LINE_H if self.footer else 0) + 8
        return PAGE_H - top - bottom

    def _render(self, blocks: Sequence[_Block], number: int, total: int) -> Image.Image:
        img = Image.new("RGB", (PAGE_W, PAGE_H), PAPER)
        draw = ImageDraw.Draw(img)
        y = _MARGIN

        label = self.label
        if total > 1:
            label = f"{label}  ({number}/{total})" if label else f"({number}/{total})"
        draw.text(
            (_MARGIN, y),
            self.title.upper(),
            font=fonts.mono(_TITLE, bold=True),
            fill=INK,
        )
        if label:
            draw.text(
                (PAGE_W - _MARGIN, y + 8),
                label.upper(),
                font=fonts.mono(_HEADING, bold=True),
                fill=MUTED,
                anchor="ra",
            )
        y += _TITLE + 12
        if self.subtitle:
            draw.text((_MARGIN, y), self.subtitle, font=fonts.mono(_BODY), fill=MUTED)
            y += _LINE_H
        y += 8
        draw.line((_MARGIN, y, PAGE_W - _MARGIN, y), fill=INK, width=3)
        y += 12

        for block in blocks:
            if isinstance(block, _Art):
                y += block.gap_before
                block.draw(draw, (_MARGIN, y, PAGE_W - _MARGIN, y + block.height))
                y += block.height
                continue
            y += block.gap_before
            size = _SMALL if block.small else _BODY
            draw.text(
                (_MARGIN, y),
                block.text,
                font=fonts.mono(size, bold=block.bold),
                fill=MUTED if block.muted else INK,
            )
            if block.bold and not block.small:
                # Underscore a heading rather than boxing it: a rule costs one
                # pixel row and survives being photographed off a monitor.
                y += _LINE_H - 8
                draw.line((_MARGIN, y, PAGE_W - _MARGIN, y), fill=RULE, width=1)
                y += 8
            else:
                y += _SMALL_LINE_H if block.small else _LINE_H

        if self.footer:
            draw.line(
                (
                    _MARGIN,
                    PAGE_H - _MARGIN - _SMALL_LINE_H - 10,
                    PAGE_W - _MARGIN,
                    PAGE_H - _MARGIN - _SMALL_LINE_H - 10,
                ),
                fill=RULE,
                width=1,
            )
            draw.text(
                (_MARGIN, PAGE_H - _MARGIN - _SMALL_LINE_H),
                self.footer,
                font=fonts.mono(_SMALL),
                fill=MUTED,
            )
        return img


def _row(columns: Sequence[Column], cells: Sequence[str]) -> str:
    """Pad `cells` into `columns`; a cell longer than its column is not cut.

    Truncating would hide the one thing worth seeing (a long waypoint name, a
    callsign), so an over-long cell pushes the rest of its row right and the
    column below it stays where it was.
    """
    out = []
    for column, cell in zip(columns, list(cells) + [""] * len(columns)):
        out.append(f"{cell:{column.align}{column.width}}")
    return " ".join(out).rstrip()


def _paginate(
    blocks: Sequence[_Block],
    height: int,
    headers: dict[int, _Text] | None = None,
) -> list[list[_Block]]:
    """Greedy packing, with a heading never left alone at the foot of a page.

    A break that lands inside a table re-opens it on the new page: its own
    heading (numbered by `Page._number_parts` once the parts are known) and its
    column headers go in first, and the rows carry on under them.
    """
    headers = headers or {}
    pages: list[list[_Block]] = [[]]
    used = 0
    for index, block in enumerate(blocks):
        cost = block.height_total if isinstance(block, _Art) else block.height
        keep = isinstance(block, _Text) and block.keep_with_next
        following = 0
        if keep and index + 1 < len(blocks):
            nxt = blocks[index + 1]
            following = nxt.height_total if isinstance(nxt, _Art) else nxt.height
        if used and used + cost + following > height:
            pages.append([])
            used = 0
            block = _first_on_page(block)
            cost = block.height_total if isinstance(block, _Art) else block.height
            for extra in _reopen_table(block, headers):
                pages[-1].append(extra)
                used += extra.height
        pages[-1].append(block)
        used += cost
    return pages


def _reopen_table(block: _Block, headers: dict[int, _Text]) -> list[_Text]:
    """The heading and column headers a table needs when it resumes on a page."""
    if not isinstance(block, _Text) or block.table_id is None or block.table_header:
        return []
    header = headers.get(block.table_id)
    if header is None:
        return []
    return [
        _Text(
            "",  # filled in by `Page._number_parts`, which knows how many parts.
            bold=True,
            keep_with_next=True,
            table_id=block.table_id,
            table_title=True,
        ),
        replace(header, gap_before=0),
    ]


def _first_on_page(block: _Block) -> _Block:
    """Drop the leading gap a block carries when it starts a page."""
    return replace(block, gap_before=0)
