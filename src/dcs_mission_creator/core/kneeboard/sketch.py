"""The airfield plan view — drawn only for a field the theatre does not chart.

`kneeboard/charts.py` is the gate: on Caucasus, ED ships a surveyed ground diagram
for every field and this never runs. On Syria it ships three, so a player starting
at Hatay gets this instead of nothing.

North up, one scale, no rotation — an airport diagram is read against the map and
a rotated one is read wrong. Everything on it is a surveyed position:

- **parking slots** (pydcs), with the ones this mission spawns a flight into
  filled, and the flights numbered against a legend rather than named on the map;
- **beacons** (the install's `Beacons.lua`), each with its callsign and tuning;
- the **airfield reference point**, which is also what pydcs measures every
  take-off and landing waypoint from;
- the **runway**, and this is the one line with a caveat. Its direction is the
  designator; its extent is not data anybody has offline (see
  `kneeboard/airfields.py`). Where the field has a full ILS the two antennas
  bracket the strip and it is drawn solid between them — real geometry — and
  otherwise it is a dashed centreline through the reference point, which claims a
  direction and nothing about where the concrete starts. The legend under the
  sketch says which of the two the reader is looking at, because a dashed line
  that looked like a runway would be worse than no line.

It is not a taxi chart and does not pretend to be. What it is for is knowing which
way the runway runs, where your jet is parked, and which navaid is which, at a
field the game hands you no page about.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from PIL import ImageDraw

from dcs_mission_creator.core import fonts
from dcs_mission_creator.core.kneeboard.airfields import AirfieldCard, sketch_beacons
from dcs_mission_creator.core.kneeboard.page import INK, MUTED, PAPER, RULE

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.terrain.terrain import Terrain

_LABEL = 18
_PAD_FRAC = 0.12
#: Minimum half-width of the drawn area, so a field with one apron and no beacon
#: does not zoom to a hundred metres and imply survey detail it does not have.
_MIN_HALF_M = 900.0
_SCALE_STEPS_M = (2_000.0, 1_000.0, 500.0, 200.0, 100.0)


@dataclass(frozen=True)
class _Projection:
    """World metres to pixels, north up, one scale on both axes."""

    center_x: float
    center_y: float
    half_m: float
    box: tuple[int, int, int, int]

    @property
    def size_px(self) -> int:
        x0, y0, x1, y1 = self.box
        return min(x1 - x0, y1 - y0)

    @property
    def m_per_px(self) -> float:
        return (2.0 * self.half_m) / self.size_px

    def to_px(self, point: Point) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        east = point.y - self.center_y
        north = point.x - self.center_x
        return cx + east / self.m_per_px, cy - north / self.m_per_px


def draw_airfield(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    card: AirfieldCard,
    terrain: Terrain,
) -> None:
    """Draw `card`'s field into `box`. Called from `Page.art`.

    The plan view takes a square on the left — one scale on both axes, so the field
    is not stretched — and the flights that start here are listed on the right
    against numbers drawn on their parking. Naming them on the map itself put three
    labels on top of each other the first time three flights shared an apron, which
    is the normal case rather than the exception.

    Every symbol is drawn and reserved before any label is placed: a label put down
    as its own symbol was drawn cannot know about the symbols still to come, and the
    first version printed a navaid's name through the next navaid's mark.
    """
    beacons = sketch_beacons(card, terrain)
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    plan_box = (x0, y0, x0 + side, y0 + side)
    legend_x = x0 + side + 24

    points = (
        [card.airport.position]
        + [s.position for s in card.spawns]
        + [slot.position for slot in card.airport.parking_slots]
        + [b.position(terrain) for b in beacons]
    )
    projection = _fit(points, card.airport.position, plan_box)

    placer = _Placer(plan_box)
    draw.rectangle(plan_box, outline=RULE, width=1)
    # Furniture first, and reserved: it is the only decoration on the drawing, so
    # where it and a label want the same pixels the label wins and the arrow takes
    # the break. A threshold number printed through an arrowhead is unreadable; an
    # arrow with a gap in it is still an arrow.
    _draw_north(draw, plan_box, placer)
    _draw_scale(draw, projection, plan_box, placer)
    _draw_runways(draw, projection, card, placer)
    _draw_parking(draw, projection, card, placer)
    marks = [(_draw_reference(draw, projection, card, placer), "ARP")]
    for beacon in beacons:
        marks.append(
            (
                _draw_beacon(
                    draw,
                    projection,
                    beacon.position(terrain),
                    beacon.kind_label,
                    placer,
                ),
                _short(beacon),
            )
        )
    for at, text in marks:
        placer.place(draw, at, text)
    _draw_legend(draw, (legend_x, y0 + 8), card)


def legend(card: AirfieldCard) -> str:
    """One line saying what the runway line in the sketch is — see the docstring."""
    if any(_ils_strip(runway) is not None for runway in card.runways):
        return (
            "Runway drawn between the ILS glideslope and localizer antennas "
            "(surveyed positions; the strip lies between them)."
        )
    return (
        "Runway shown as a dashed centreline on the designator heading through the "
        "reference point — direction only, extent not to scale."
    )


# -- pieces ------------------------------------------------------------------


def _fit(
    points: Sequence[Point], center: Point, box: tuple[int, int, int, int]
) -> _Projection:
    """A square window centred on the reference point holding every point."""
    half = _MIN_HALF_M
    for point in points:
        half = max(half, abs(point.x - center.x), abs(point.y - center.y))
    return _Projection(center.x, center.y, half * (1.0 + _PAD_FRAC), box)


def _draw_runways(
    draw: ImageDraw.ImageDraw,
    projection: _Projection,
    card: AirfieldCard,
    placer: _Placer,
) -> None:
    """One line per runway, with a designator at each end.

    Which designator goes at which end is the easy thing to get backwards: the
    number painted on a threshold is the heading you fly *toward* it, so the end
    lying in direction H from the field carries the **reciprocal** of H. Landing on
    13 you cross the north-west threshold — the one marked 13 — and that end is at
    bearing 310 from the field.
    """
    for runway in card.runways:
        strip = _ils_strip(runway)
        if strip is not None:
            approach, near, far = strip
            near_px = projection.to_px(near)
            far_px = projection.to_px(far)
            draw.line((*near_px, *far_px), fill=INK, width=9)
            # `near` is the glideslope, beside this approach's own threshold.
            near_name = approach.designator
            far_name = _reciprocal(runway, approach).designator
        else:
            approach = runway.approaches[0]
            length = projection.half_m * 1.6
            near_px = projection.to_px(
                card.airport.position.point_from_heading(
                    (approach.heading_deg + 180.0) % 360.0, length / 2
                )
            )
            far_px = projection.to_px(
                card.airport.position.point_from_heading(
                    float(approach.heading_deg), length / 2
                )
            )
            _dashed(draw, near_px, far_px, fill=INK, width=7)
            near_name = _reciprocal(runway, approach).designator
            far_name = approach.designator
        _threshold_label(draw, near_px, far_px, near_name, placer)
        _threshold_label(draw, far_px, near_px, far_name, placer)


def _ils_strip(runway):
    """`(approach, glideslope position, localizer position)`, or `None`.

    The geometry itself is `airfields.IlsGeometry`, measured once when the card was
    built — the sketch only picks the first runway end that has it, so the drawn
    strip and the printed ILS course can never be two different derivations.
    """
    for approach in runway.approaches:
        if approach.ils is not None:
            return approach, approach.ils.glideslope, approach.ils.localizer
    return None


def _reciprocal(runway, approach):
    """The other end of `runway`."""
    others = [a for a in runway.approaches if a is not approach]
    return others[0] if others else approach


def _threshold_label(
    draw: ImageDraw.ImageDraw,
    at: tuple[float, float],
    toward: tuple[float, float],
    text: str,
    placer: _Placer,
) -> None:
    """A designator just inboard of the threshold, on its own patch of paper.

    Drawn 34 px along the runway rather than on the end itself: the ends carry the
    ILS antenna symbols and their labels, and a number printed over those was
    unreadable in both directions.
    """
    dx, dy = toward[0] - at[0], toward[1] - at[1]
    length = math.hypot(dx, dy) or 1.0
    x = at[0] + dx / length * 34.0
    y = at[1] + dy / length * 34.0
    font = fonts.mono(_LABEL, bold=True)
    width = font.getlength(text)
    box = (x - width / 2 - 4, y - 13, x + width / 2 + 4, y + 13)
    draw.rectangle(box, fill=PAPER)
    _label(draw, (x, y), text, anchor="mm", bold=True)
    placer.reserve(box)


def _draw_parking(
    draw: ImageDraw.ImageDraw,
    projection: _Projection,
    card: AirfieldCard,
    placer: _Placer,
) -> None:
    spawn_slots = {slot for spawn in card.spawns for slot in spawn.slots}
    for slot in card.airport.parking_slots:
        x, y = projection.to_px(slot.position)
        occupied = str(slot.slot_name) in spawn_slots
        draw.rectangle(
            (x - 5, y - 5, x + 5, y + 5),
            fill=INK if occupied else None,
            outline=INK if occupied else MUTED,
            width=2,
        )
    for number, spawn in enumerate(card.spawns, start=1):
        x, y = projection.to_px(spawn.position)
        draw.ellipse((x - 13, y - 13, x + 13, y + 13), outline=INK, width=3)
        _label(draw, (x, y), str(number), anchor="mm", bold=True)
        placer.reserve((x - 14, y - 14, x + 14, y + 14))


def _draw_reference(
    draw: ImageDraw.ImageDraw,
    projection: _Projection,
    card: AirfieldCard,
    placer: _Placer,
) -> tuple[float, float]:
    """Draw the reference cross, reserve it, and return where it is."""
    x, y = projection.to_px(card.airport.position)
    draw.line((x - 12, y, x + 12, y), fill=INK, width=2)
    draw.line((x, y - 12, x, y + 12), fill=INK, width=2)
    placer.reserve((x - 13, y - 13, x + 13, y + 13))
    return x, y


def _draw_beacon(
    draw: ImageDraw.ImageDraw,
    projection: _Projection,
    position: Point,
    kind: str,
    placer: _Placer,
) -> tuple[float, float]:
    """Draw a beacon's symbol, reserve it, and return where it is."""
    x, y = projection.to_px(position)
    if kind.startswith("ILS") or kind.startswith("PRMG"):
        draw.rectangle((x - 4, y - 9, x + 4, y + 9), outline=INK, width=2)
    elif kind in {"TACAN", "VORTAC", "VOR", "VOR/DME", "DME", "RSBN"}:
        _polygon(draw, x, y, 10, 6)
    else:
        draw.polygon([(x, y - 9), (x + 9, y + 7), (x - 9, y + 7)], outline=INK)
    placer.reserve((x - 11, y - 11, x + 11, y + 11))
    return x, y


def _polygon(
    draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, sides: int
) -> None:
    draw.polygon(
        [
            (
                x + radius * math.cos(2 * math.pi * i / sides),
                y + radius * math.sin(2 * math.pi * i / sides),
            )
            for i in range(sides)
        ],
        outline=INK,
    )


def _draw_legend(
    draw: ImageDraw.ImageDraw, at: tuple[int, int], card: AirfieldCard
) -> None:
    """The numbered flights, then the symbols, down the right-hand side."""
    x, y = at
    if card.spawns:
        _label(draw, (x, y), "FLIGHTS ON THIS FIELD", bold=True)
        y += 26
        for number, spawn in enumerate(card.spawns, start=1):
            slots = ", ".join(spawn.slots) if spawn.slots else "runway"
            _label(
                draw,
                (x, y),
                f"{number}  {spawn.flight.upper()}  {spawn.start}  {slots}",
                bold=spawn.player,
            )
            y += 24
        y += 12
    _label(draw, (x, y), "SYMBOLS", bold=True)
    y += 26
    for text in (
        "+  airfield reference point",
        "[] parking slot (filled: in use)",
        "() flight start position",
        "#  ILS / PRMG antenna",
        "<> TACAN / VOR / RSBN",
        "/\\ NDB / homer",
    ):
        _label(draw, (x, y), text)
        y += 24


def _draw_north(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], placer: _Placer
) -> None:
    x0, y0, x1, _ = box
    x, y = x1 - 44, y0 + 30
    draw.line((x, y + 26, x, y - 20), fill=INK, width=3)
    draw.polygon([(x, y - 28), (x - 7, y - 14), (x + 7, y - 14)], fill=INK)
    _label(draw, (x, y + 30), "N", anchor="ma", bold=True)
    placer.reserve((x - 20, y - 30, x + 20, y + 54))


def _draw_scale(
    draw: ImageDraw.ImageDraw,
    projection: _Projection,
    box: tuple[int, int, int, int],
    placer: _Placer,
) -> None:
    x0, _, x1, y1 = box
    limit = (x1 - x0) * 0.3 * projection.m_per_px
    length_m = next((s for s in _SCALE_STEPS_M if s <= limit), _SCALE_STEPS_M[-1])
    length_px = length_m / projection.m_per_px
    x, y = x0 + 18, y1 - 26
    draw.line((x, y, x + length_px, y), fill=INK, width=3)
    for tick in (x, x + length_px):
        draw.line((tick, y - 6, tick, y + 6), fill=INK, width=3)
    _label(draw, (x, y + 8), f"{length_m:.0f} M")
    placer.reserve((x - 6, y - 10, x + length_px + 6, y + 34))


def _dashed(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    fill,
    width: int,
    dash_px: float = 26.0,
) -> None:
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return
    steps = max(int(length / dash_px), 1)
    for step in range(steps):
        if step % 2:
            continue
        a = step / steps
        b = min((step + 1) / steps, 1.0)
        draw.line(
            (
                x0 + (x1 - x0) * a,
                y0 + (y1 - y0) * a,
                x0 + (x1 - x0) * b,
                y0 + (y1 - y0) * b,
            ),
            fill=fill,
            width=width,
        )


def _short(beacon) -> str:
    """`ILU 110.30` — enough to recognise a symbol; the table carries the rest."""
    tune = beacon.label.replace(" MHZ", "").replace(" KHZ", "").replace("CH ", "")
    return f"{beacon.callsign} {tune}".strip()


def _label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    anchor: str = "la",
    bold: bool = False,
) -> None:
    draw.text(
        xy,
        text,
        font=fonts.mono(_LABEL, bold=bold),
        fill=INK,
        anchor=anchor,
        stroke_width=3,
        stroke_fill=PAPER,
    )


class _Placer:
    """Puts a mark's label somewhere it does not print through another one.

    Four candidate positions round the mark, in order of preference, tested against
    the boxes already taken and against the edge of the drawing; a label with
    nowhere to go is **dropped** rather than overprinted, because everything on the
    sketch is also in the navaid table above it and two labels through each other
    loses both. Same rule as `core/recon/landmark.py`, for the same reason.
    """

    def __init__(self, bounds: tuple[int, int, int, int]) -> None:
        self._taken: list[tuple[float, float, float, float]] = []
        self._bounds = bounds

    def reserve(self, box: tuple[float, float, float, float]) -> None:
        self._taken.append(box)

    def place(
        self,
        draw: ImageDraw.ImageDraw,
        at: tuple[float, float],
        text: str,
        *,
        bold: bool = False,
    ) -> bool:
        font = fonts.mono(_LABEL, bold=bold)
        width = font.getlength(text)
        height = _LABEL + 4
        x, y = at
        for dx, dy in (
            (14, -height / 2),
            (-14 - width, -height / 2),
            (-width / 2, -height - 10),
            (-width / 2, 14),
        ):
            box = (x + dx, y + dy, x + dx + width, y + dy + height)
            if not _inside(box, self._bounds):
                continue
            if any(_overlaps(box, taken) for taken in self._taken):
                continue
            draw.text(
                (box[0], box[1]),
                text,
                font=font,
                fill=INK,
                anchor="la",
                stroke_width=3,
                stroke_fill=PAPER,
            )
            self._taken.append(box)
            return True
        return False


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(box, bounds) -> bool:
    """A label half off the drawing is worse than no label — see `_Placer`."""
    return (
        box[0] >= bounds[0] + 4
        and box[1] >= bounds[1] + 4
        and box[2] <= bounds[2] - 4
        and box[3] <= bounds[3] - 4
    )
