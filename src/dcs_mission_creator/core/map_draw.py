"""F10 map briefing drawings (project-owned helper).

`PlanOverlay` wraps the pydcs `Drawings` API (`m.drawings`, layers, `Rgba`,
`StandardIcon`, the `add_*` methods) and paints the *plan* on the F10 map so
the player reads the sortie at a glance. It owns two concerns the raw pydcs
layer does not:

1. **Faction-correct placement.** Everything lands on the blue `StandardLayer`
   so only the player's coalition sees it.
2. **Difficulty-scaled enemy reveal.** The friendly plan (routes, orbits,
   objective) is always drawn precisely; an emplaced enemy threat is drawn at
   every difficulty, but as a claim that gets steadily less precise — exact at
   `recruit`, a coarsened "(est.)" ring at `trained`, and a wide, dashed
   "(approx.)" ring further off truth at `veteran` and `ace`. See the
   `dcs-mission` skill for the design intent.

   Higher difficulty stopped meaning "no ring at all" because of what that did
   to the rest of the plan. A mission still has to put steerpoints somewhere,
   and with nothing to draw from they were built off the site's true position:
   `daryal_run` hid every Russian icon, drew its S-300 as a vague area 4 km
   off, and then wrote a `TARGET` steerpoint on the battery to the metre, which
   the player reads out of the DED before rolling. Withholding the ring did not
   withhold the position; it just moved the leak somewhere the reveal policy
   could not see it. Drawing an approximate ring and handing that estimate back
   gives every channel — map, cartridge, steerpoint — one deliberately
   imprecise thing to agree on.

Design rule (mirrors `core/placement.py` / `core/tts`): the mission passes in
**absolute** world `Point`s; this helper does the layer selection, colour
choice, difficulty policy, and the offset math the point-list drawings need.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence, Union

from dcs.drawing.drawing import LineStyle, Rgba
from dcs.drawing.drawings import StandardLayer
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point

from dcs_mission_creator.core.difficulty import Difficulty

if TYPE_CHECKING:
    from dcs.mission import Mission


# -- how precise a claim each difficulty is willing to make about a site ------
#
# `(offset_m, radius_factor, suffix)`: how far the drawn estimate sits from
# truth, how much the radius is inflated, and what the label admits to. The
# radius grows with the offset on purpose — an estimate that is further from
# truth has to cover more ground to still be worth flying around, so the ring
# stays a usable "do not enter" area while becoming useless for threading a
# tight route past one edge of it. That is the difficulty dial: not whether the
# player is told, but how much airspace the telling costs them.
#
# The inflation is deliberately modest — the ring still has to read as this
# system's envelope, not as a blanket over the theatre — so the offset is what
# does most of the work. `trained` is unchanged from when the higher labels
# drew nothing, so the four trained missions' rings and cartridges are exactly
# as they were.
_REVEAL = {
    Difficulty.RECRUIT: (0.0, 1.00, ""),
    Difficulty.TRAINED: (2_000.0, 1.15, " (est.)"),
    Difficulty.VETERAN: (4_000.0, 1.25, " (approx.)"),
    Difficulty.ACE: (6_000.0, 1.35, " (approx.)"),
}

# -- what was drawn, kept so the cockpit can be given the same plan ---------
#
# `core/dtc.py` turns these into the Viper's own steerpoints and GEO lines, so
# the F10 map and the DTE page are one statement rather than two. Recording is
# deliberately done at *draw* time and stores the **drawn** position, never the
# true one: an estimate that has been offset by `_REVEAL` stays offset, and a
# site the mission never drew is not in the list at all. That is what keeps the
# reveal policy in this module even though the cartridge is written elsewhere.


@dataclass(frozen=True)
class PlanLine:
    """A polyline this overlay painted: a route, an orbit leg, a front line."""

    points: tuple[Point, ...]
    label: Optional[str]
    #: `"route"` | `"orbit"` | `"frontline"`.
    kind: str
    enemy: bool


@dataclass(frozen=True)
class PlanMark:
    """A labelled point this overlay painted, at the position it painted it."""

    position: Point
    label: str
    #: `"objective"` | `"waypoint"` | `"threat"` | `"mobile"` | `"area"`.
    kind: str
    enemy: bool


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
        self._estimates: dict[tuple[int, int, int], tuple[Point, float]] = {}
        self._lines: list[PlanLine] = []
        self._marks: list[PlanMark] = []

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
        self._lines.append(PlanLine(tuple(pts), label, "route", enemy=False))

    def orbit(self, p1: Point, p2: Point, label: Optional[str] = None) -> None:
        """Draw a friendly race-track leg (tanker / AWACS / CAP) as a dashed line."""
        self._layer.add_line_segment(
            p1, p2 - p1, color=_FRIENDLY, line_thickness=3, line_style=LineStyle.Dash
        )
        if label:
            self._label(p1.midpoint(p2), label, _FRIENDLY)
        self._lines.append(PlanLine((p1, p2), label, "orbit", enemy=False))

    def waypoint_label(self, pos: Point, text: str) -> None:
        """Drop a plain friendly text label at `pos`."""
        self._label(pos, text, _FRIENDLY)
        self._marks.append(PlanMark(pos, text, "waypoint", enemy=False))

    # -- ground truth both sides already have ------------------------------

    def frontline(self, trace: Sequence[Point], label: Optional[str] = None) -> None:
        """Draw a front line — the forward edge of the enemy's ground forces.

        Drawn precisely at **every** difficulty, unlike anything else red on the
        map. A front line is where two armies have been dug in facing each other
        for weeks: the side the player flies for holds the other half of it, so
        withholding the trace would model an ignorance nobody has, and the
        briefing's "cross at the seam" would have nothing to point at.

        What the reveal policy still governs is the air defence *on* the line —
        that goes through `threat` / `mobile_threat` like any other site, and a
        position the intel never fixed gets nothing at all.
        """
        pts = [p for p in trace]
        if len(pts) < 2:
            return
        anchor = pts[0]
        self._layer.add_line_segments(
            anchor, [p - anchor for p in pts], color=_ENEMY, line_thickness=6
        )
        if label:
            self._label(pts[len(pts) // 2], label, _ENEMY)
        self._lines.append(PlanLine(tuple(pts), label, "frontline", enemy=True))

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
            self._marks.append(PlanMark(center, label, "objective", enemy=False))
        else:
            # veteran / ace: a vague area, offset from truth, "vicinity" wording.
            # Through `estimate` rather than a fresh offset of its own: on a
            # mission whose objective *is* the battery (`daryal_run`), the two
            # would otherwise put the target area and the SAM ring in different
            # places and the map would contradict itself about one site.
            vague, _ = self.estimate(center)
            self._layer.add_circle(
                vague,
                radius=max(radius * 1.8, 12_000.0),
                color=_OBJECTIVE,
                fill=_TRANSPARENT,
                line_thickness=2,
                line_style=LineStyle.Dash,
            )
            vicinity = f"{label} — vicinity"
            self._label(vague, vicinity, _OBJECTIVE)
            self._marks.append(PlanMark(vague, vicinity, "objective", enemy=False))

    # -- enemy threats: reveal scales with difficulty ----------------------

    def estimate(self, center: Point, *, radius: float = 0.0) -> tuple[Point, float]:
        """This mission's one claim about where a site is and how far it reaches.

        Draws nothing. Returns the `(position, radius)` the difficulty allows —
        truth at `recruit`, and progressively further off and wider above it,
        per `_REVEAL`.

        **Memoised on the true position and radius**, and that is the whole
        point of the method existing. The error comes from a random bearing, so
        two calls used to give two different answers about the same battery, and
        a mission that wanted the estimate anywhere other than inside
        `_draw_plan` had no way to ask for it — which is why every steerpoint
        built off an enemy site was built off the *true* one instead. Now the
        first caller fixes the claim and every later channel gets the same
        point: the ring on the F10 map, the pre-planned threat in the cartridge,
        the target steerpoint in the flight plan, the label on the kneeboard.

        Call it early — before the routes are laid — whenever a route needs it.
        Order does not otherwise matter; only the true position keys the memo.
        """
        offset_m, factor, _ = _REVEAL[self._d]
        key = (round(center.x), round(center.y), round(radius))
        if key not in self._estimates:
            self._estimates[key] = (
                self._offset(center, offset_m) if offset_m else center,
                radius * factor,
            )
        return self._estimates[key]

    def threat(
        self,
        center: Point,
        *,
        radius: float,
        label: str,
        icon: Optional[StandardIcon] = None,
    ) -> Optional[tuple[Point, float]]:
        """Mark an **emplaced** enemy threat, drawn as precisely as the difficulty allows.

        recruit  → exact icon + true-radius ring, plain label.
        trained  → icon + slightly coarse ring at a small offset, "(est.)".
        veteran  → a wider dashed ring, further off truth, "(approx.)".
        ace      → wider still and further off — the ring says the airspace is
                   denied, not where the launchers are standing.

        Above `trained` the ring is drawn dashed and unfilled, the register
        `objective` already uses for a vague area: a solid ring reads as a
        survey, and this is an assessment. What the higher difficulties take
        away is *precision*, not the warning — the ace ring is wide enough that
        it cannot be threaded, so the player still has to find the battery, but
        the plan they were handed no longer quietly contains its exact position.

        A ring is a claim that the envelope *is there*, so it belongs only to a
        site that stays put. Air defence riding with a column has driven out of
        any ring by the time the player is overhead, and the ring then reads as
        a safe area everywhere it no longer covers — use `mobile_threat`.

        Returns the `(center, radius)` actually drawn — the same estimate
        `estimate()` hands anyone else who asks about this site, so the cockpit
        display of the same briefing cannot disagree with the map. See
        `core/dtc.py`, which loads it onto the F-16C's HSD. The `Optional` in
        the signature is for the caller's convenience: `dtc.briefed` takes it
        directly, and `mobile_threat` genuinely has nothing to give back.
        """
        drawn = self.estimate(center, radius=radius)
        suffix = _REVEAL[self._d][2]
        text = f"{label}{suffix}"
        self._ring(*drawn, text, icon, dashed=self._is_vague)
        self._marks.append(PlanMark(drawn[0], text, "threat", enemy=True))
        return drawn

    def detections(
        self,
        positions: Sequence[Point],
        *,
        bias_m: float = 1_200.0,
        jitter_m: float = 120.0,
    ) -> list[Point]:
        """Positions a **sensor product** may show, per difficulty. Draws nothing.

        A rendered recon still (`core/recon`) is a third channel of enemy reveal
        alongside the F10 plan and the HSD cartridge, so it has to answer to the
        same policy — otherwise a mission could publish a photograph of ground
        truth at `ace` while the map deliberately showed nothing. This method is
        that gate: it returns where the product is allowed to put its detections,
        and drawing is entirely the caller's business.

        recruit  → per-return jitter only, essentially truth.
        trained  → **one** registration bias shared by the whole cluster, plus
                   per-return jitter.
        veteran / ace → empty, and a mission with nothing to plot then publishes
                   no still at all — the same call `threat_area` makes.

        The shared bias is the part worth keeping. Offsetting each vehicle
        independently by a kilometre scatters an eleven-vehicle column over four
        and the picture stops reading as a column at all. A real product's error
        is exactly this shape: a geolocation registration error common to the
        frame, plus a small per-detection accuracy. So the physically honest model
        is also the one that preserves the formation.

        **`bias_m` is a property of the frame, not of the difficulty**, and the
        1.2 km default is the value for a frame with nothing in it to register
        against — which is what `idlib_gauntlet` renders, its ground measuring no
        road, no water and no tree. Cut it hard as soon as the picture draws
        landmarks: an exploitation system ties the product to the road net it can
        see, and what survives is the sensor's own cross-range accuracy, a couple
        of hundred metres. The default over `coastal_cover`'s valley put the
        column 1.0–1.2 km from any road in a frame that paints the roads, so the
        still contradicted its own footer, and a mover sitting a field away from
        the highway reads as a broken product rather than as an estimate. The
        gate above is the difficulty policy; this is calibration.
        """
        if not positions:
            return []
        if self._d in (Difficulty.VETERAN, Difficulty.ACE):
            return []
        if self._d == Difficulty.TRAINED:
            bearing = random.uniform(0.0, 360.0)
            shifted = [p.point_from_heading(bearing, bias_m) for p in positions]
        else:
            shifted = list(positions)
        return [
            p.point_from_heading(
                random.uniform(0.0, 360.0), random.uniform(0.0, jitter_m)
            )
            for p in shifted
        ]

    def mobile_threat(
        self, center: Point, label: str, icon: Optional[StandardIcon] = None
    ) -> None:
        """Mark air defence that moves — an icon and a label, never an envelope.

        Same reveal policy as `threat` — the mark walks further from truth as
        the difficulty rises — and deliberately no return value: a mark with no
        radius is not something `core/dtc.py` can turn into a pre-planned
        threat point, which is the right answer for a system that will not be
        where the cartridge says. Name what it rides with ("Convoy SHORAD") so
        the label carries the reach the briefing prose states.

        The offset here is a smaller lie than it is for a site: the mark is
        already only a claim about where something *was*, and the label says
        what it rides with. What it must never grow is a circle.
        """
        position, _ = self.estimate(center)
        text = f"{label}{_REVEAL[self._d][2]}"
        self._mark(position, text, icon)
        self._marks.append(PlanMark(position, text, "mobile", enemy=True))

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
        self._marks.append(PlanMark(vague, label, "area", enemy=True))

    # -- what the cockpit is allowed to be handed --------------------------

    def lines(self) -> list[PlanLine]:
        """Every polyline drawn, in draw order — see `core/dtc.py`.

        The order is the mission's own `_draw_plan`, which is what the cartridge
        falls back on when the Viper's four GEO lines are oversubscribed: a
        mission that cares which line survives says so by drawing it first.
        """
        return list(self._lines)

    def marks(self) -> list[PlanMark]:
        """Every labelled point drawn, at the position it was drawn, in draw order.

        Enemy marks are in here too, at their **estimated** positions — a
        consumer that would out-claim the map cannot, because there is no truth
        in the list to out-claim it with. `core/dtc.py` still drops the `threat`
        kind before writing steerpoints, since those are already the cartridge's
        pre-planned threat points and would otherwise be in the jet twice.
        """
        return list(self._marks)

    # -- internals ---------------------------------------------------------

    @property
    def _is_vague(self) -> bool:
        """True where the plan is an assessment rather than a fix."""
        return self._d in (Difficulty.VETERAN, Difficulty.ACE)

    def _ring(
        self,
        center: Point,
        radius: float,
        label: str,
        icon: Optional[StandardIcon],
        *,
        dashed: bool = False,
    ) -> None:
        self._layer.add_circle(
            center,
            radius=radius,
            color=_ENEMY,
            fill=_TRANSPARENT if dashed else _ENEMY_FILL,
            line_thickness=2,
            line_style=LineStyle.Dash if dashed else LineStyle.Solid,
        )
        self._mark(center, label, icon)

    def _mark(
        self, center: Point, label: str, icon: Optional[StandardIcon] = None
    ) -> None:
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
