"""F10 map briefing drawings (project-owned helper).

`PlanOverlay` wraps the pydcs `Drawings` API (`m.drawings`, layers, `Rgba`,
`StandardIcon`, the `add_*` methods) and paints the *plan* on the F10 map so
the player reads the sortie at a glance. It owns two concerns the raw pydcs
layer does not:

1. **Faction-correct placement.** Everything lands on the blue `StandardLayer`
   so only the player's coalition sees it.
2. **Difficulty-scaled enemy reveal.** The friendly plan (routes, orbits,
   objective) is always drawn precisely; enemy threats are shown in full at
   `recruit`, coarsened at `trained`, reduced to a vague area at `veteran`,
   and omitted at `ace`. See the `dcs-mission` skill for the design intent.

Design rule (mirrors `core/placement.py` / `core/tts`): the mission passes in
**absolute** world `Point`s; this helper does the layer selection, colour
choice, difficulty policy, and the offset math the point-list drawings need.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional, Sequence, Union

from dcs.drawing.drawing import LineStyle, Rgba
from dcs.drawing.drawings import StandardLayer
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point

from dcs_mission_creator.core.difficulty import Difficulty

if TYPE_CHECKING:
    from dcs.mission import Mission


# -- palette (see skill: enemy red, friendly cyan, objective amber, notes white)
_ENEMY = Rgba(200, 30, 30, 255)
_ENEMY_FILL = Rgba(200, 30, 30, 40)
_FRIENDLY = Rgba(0, 160, 255, 255)
_OBJECTIVE = Rgba(255, 170, 0, 255)
_OBJECTIVE_FILL = Rgba(255, 170, 0, 35)
_TRANSPARENT = Rgba(0, 0, 0, 0)


class PlanOverlay:
    """Difficulty-aware F10 briefing overlay on the blue coalition layer."""

    def __init__(self, m: "Mission", difficulty: Union[str, Difficulty]) -> None:
        self._m = m
        self._layer = m.drawings.get_layer(StandardLayer.Blue)
        self._d = Difficulty.coerce(difficulty)

    # -- friendly own-plan: always drawn precisely -------------------------

    def route(self, points: Sequence[Point], label: Optional[str] = None) -> None:
        """Draw the player's flown route as a cyan polyline through `points`."""
        pts = [p for p in points]
        if len(pts) < 2:
            return
        anchor = pts[0]
        # pydcs point-list drawings take offsets relative to the anchor.
        offsets = [p - anchor for p in pts]
        self._layer.add_line_segments(
            anchor, offsets, color=_FRIENDLY, line_thickness=4
        )
        if label:
            self._label(pts[0], label, _FRIENDLY)

    def orbit(self, p1: Point, p2: Point, label: Optional[str] = None) -> None:
        """Draw a friendly race-track leg (tanker / AWACS / CAP) as a dashed line."""
        self._layer.add_line_segment(
            p1, p2 - p1, color=_FRIENDLY, line_thickness=3, line_style=LineStyle.Dash
        )
        if label:
            self._label(p1.midpoint(p2), label, _FRIENDLY)

    def waypoint_label(self, pos: Point, text: str) -> None:
        """Drop a plain friendly text label at `pos`."""
        self._label(pos, text, _FRIENDLY)

    # -- objective area: precision scales with difficulty ------------------

    def objective(self, center: Point, label: str, *, radius: float = 6_000.0) -> None:
        """Ring the target area. Tight/solid when easy, large/dashed when hard."""
        if self._d in (Difficulty.RECRUIT, Difficulty.TRAINED):
            self._layer.add_circle(
                center,
                radius=radius,
                color=_OBJECTIVE,
                fill=_OBJECTIVE_FILL,
                line_thickness=3,
            )
            self._label(center, label, _OBJECTIVE)
        else:
            # veteran / ace: a vague area, offset from truth, "vicinity" wording.
            vague = self._offset(center, 4_000.0)
            self._layer.add_circle(
                vague,
                radius=max(radius * 1.8, 12_000.0),
                color=_OBJECTIVE,
                fill=_TRANSPARENT,
                line_thickness=2,
                line_style=LineStyle.Dash,
            )
            self._label(vague, f"{label} — vicinity", _OBJECTIVE)

    # -- enemy threats: reveal scales with difficulty ----------------------

    def threat(
        self,
        center: Point,
        *,
        radius: float,
        label: str,
        icon: Optional[StandardIcon] = None,
    ) -> None:
        """Mark an enemy threat, revealed per difficulty.

        recruit  → exact icon + true-radius ring, plain label.
        trained  → icon + slightly coarse ring at a small offset, "(est.)".
        veteran  → no per-unit mark (use `threat_area` for a vague zone).
        ace      → nothing; the player builds the picture from RWR / AWACS.
        """
        if self._d == Difficulty.RECRUIT:
            self._ring(center, radius, label, icon)
        elif self._d == Difficulty.TRAINED:
            self._ring(
                self._offset(center, 2_000.0),
                radius * 1.15,
                f"{label} (est.)",
                icon,
            )
        # veteran / ace: intentionally no per-unit reveal.

    def threat_area(self, center: Point, radius: float, label: str) -> None:
        """Draw a deliberately vague enemy zone (veteran air threat / ace hint)."""
        vague = self._offset(center, 3_000.0)
        self._layer.add_circle(
            vague,
            radius=radius,
            color=_ENEMY,
            fill=_ENEMY_FILL,
            line_thickness=2,
            line_style=LineStyle.Dash,
        )
        self._label(vague, label, _ENEMY)

    # -- internals ---------------------------------------------------------

    def _ring(
        self,
        center: Point,
        radius: float,
        label: str,
        icon: Optional[StandardIcon],
    ) -> None:
        self._layer.add_circle(
            center, radius=radius, color=_ENEMY, fill=_ENEMY_FILL, line_thickness=2
        )
        if icon is not None:
            self._layer.add_icon(center, icon, scale=1.0, color=_ENEMY)
        self._label(center, label, _ENEMY)

    def _label(self, pos: Point, text: str, color: Rgba) -> None:
        self._layer.add_text_box(
            pos, text, color=color, fill=_TRANSPARENT, font_size=16, border_thickness=0
        )

    def _offset(self, center: Point, distance: float) -> Point:
        """Offset a point on a random bearing so estimates don't pinpoint truth.

        The bearing comes from the stdlib `random` module, which
        `MissionBuilder._seed_rng` seeds from the mission slug — so the error
        is unpredictable to the player but identical between two builds of the
        same mission, and it no longer biases every estimate to the NE.
        """
        return center.point_from_heading(random.uniform(0.0, 360.0), distance)
