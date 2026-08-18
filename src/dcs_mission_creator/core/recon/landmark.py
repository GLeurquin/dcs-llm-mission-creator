"""Place names on a frame — the annotation that makes a still *locatable*.

A wide-area radar frame is convincing and unhelpful at the same time: the reader
sees speckled ground, a road net and a bracket, and has no way to tie any of it
to the map they are planning on. Real products solve that with place names, so
this turns the overlay's `places.geojson` settlements into label `Mark`s.

Three rules, and the first is the one that keeps this honest:

- **Only what the raster drew.** `MapOverlay.places` yields the OSM place classes
  `buildings.zarr` was rasterized from, so every label sits on a built-up return
  the picture actually paints. This is the rule `eastern_shield` failed: a
  "KUWEIRES AB" label over ground the overlay knows nothing about (4 building
  cells within 1.5 km) reads as a broken product, not as a coarse one.
- **Legible, or dropped.** Collision is tested on the real ink: `render.mark_extent`
  gives the pixel box a mark covers, symbol *and* text, measured with the font the
  renderer will use. A radius round the target's centre is not good enough — a
  group label like `7 DET  TRK 222  40 KM/H` is 190 px, i.e. 4.7 km of ground at
  25 m/px, so a village three kilometres clear of the bracket still had its name
  printed straight through the target's.
- **Deterministic.** Selection is a sort and a greedy walk, no sampling. The
  render cache keys on the marks, so a different choice per build would mean a
  different picture per build.

Not a reveal channel: a settlement is not intel — it is on every map both sides
have — so this does not go through `PlanOverlay.detections`, which gates *enemy
positions*. What the still may say about the enemy is decided there and here is
only where the reader is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import structlog
from dcs.mapping import Point

from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.render import Mark, mark_extent

if TYPE_CHECKING:
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: Ranking of the settlement classes, most prominent first. A town's name is more
#: use for orientation than a hamlet's, and the raster draws it as a bigger,
#: brighter disk, so it is also the one the reader can actually find.
_CLASS_RANK: dict[str, int] = {"city": 0, "town": 1, "village": 2, "hamlet": 3}

#: Pixels kept clear of the frame edge.
_EDGE_PAD_PX = 26.0
#: Extra clearance between two boxes that would otherwise merely touch.
_GAP_PX = 6.0


def landmark_marks(
    overlay: MapOverlay,
    frame: Frame,
    *,
    avoid: Sequence[Mark] = (),
    limit: int = 6,
    min_separation_m: float = 3_500.0,
) -> list[Mark]:
    """Label `Mark`s for the settlements worth naming inside `frame`.

    `avoid` is the product's own symbology — pass the marks the mission is about to
    draw (the target bracket, the detection ticks) and no place name will be
    printed into any of their ink. `min_separation_m` is a *spread* control on top
    of that: six names crowded into one corner orient nobody even when none of them
    collide, and this frame is 25 km wide.
    """
    candidates = overlay.places(frame.center, frame.half_diagonal_m())
    if not candidates:
        return []

    # Class first, then **distance to what the frame is about**. Ranking by name
    # would be just as deterministic and useless: on this data almost everything is
    # a `village`, so the tie-break decides the whole selection, and by name it
    # produced six labels beginning with A. The reader's eye is on the subject, so
    # the names that locate it are the near ones; the separation rule below then
    # pushes the rest outward and they end up ringing it.
    ranked = sorted(
        (pl for pl in candidates if pl.kind in _CLASS_RANK),
        key=lambda pl: (
            _CLASS_RANK[pl.kind],
            round(frame.center.distance_to_point(pl.position)),
            pl.name,
            pl.position.x,
        ),
    )

    boxes = [mark_extent(frame, mk) for mk in avoid]
    taken: list[Point] = []
    used: set[str] = set()
    marks: list[Mark] = []
    for place in ranked:
        if len(marks) >= limit:
            break
        text = place.name.upper()
        # Distinct villages do share a name — three "Akhywaa" inside one frame on
        # the Caucasus map — and two identical labels locate nothing.
        if text in used:
            continue
        if any(place.position.distance_to_point(p) < min_separation_m for p in taken):
            continue
        candidate = Mark(
            x=place.position.x, y=place.position.y, kind="place", text=text
        )
        box = mark_extent(frame, candidate)
        if not _inside(frame, box) or any(_overlaps(box, other) for other in boxes):
            continue
        boxes.append(box)
        taken.append(place.position)
        used.add(text)
        marks.append(candidate)

    log.debug(
        "recon.landmarks",
        considered=len(candidates),
        drawn=len(marks),
        names=[m.text for m in marks],
    )
    return marks


def _inside(frame: Frame, box: tuple[float, float, float, float]) -> bool:
    """True if a mark's whole box clears the frame edge, label included.

    A half-printed village name is worse than no label, and the chrome owns the
    margins, so the pad keeps names out of the scale bar and the header too.
    """
    x0, y0, x1, y1 = box
    width, height = frame.size_px
    return (
        x0 >= _EDGE_PAD_PX
        and y0 >= _EDGE_PAD_PX
        and x1 <= width - _EDGE_PAD_PX
        and y1 <= height - _EDGE_PAD_PX
    )


def _overlaps(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    """Axis-aligned box intersection, with `_GAP_PX` of breathing room."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return (
        ax0 - _GAP_PX < bx1
        and bx0 - _GAP_PX < ax1
        and ay0 - _GAP_PX < by1
        and by0 - _GAP_PX < ay1
    )
