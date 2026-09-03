"""`core/survey` — checking a layout before a mission is written around it.

The property that matters is the one the module exists for: **a briefed point
inside a system's real envelope is reported, and one outside it is not.**
`ansariyah_works` had its target sited 9.4 km from an 18 km battery, and nobody
found out until a corridor had been planned and a briefing half-written; this is
that check, and these tests are the case that got past everybody.

The overlay is a synthetic elevation array with the real row/column transform on
it, the same stub shape `test_route_plan.py` uses, so a test states a landscape
in a couple of lines and needs neither a DCS install nor the built map overlay.
"""

from __future__ import annotations

import numpy as np
import pytest
from dcs.mapping import Point
from dcs.terrain.caucasus.caucasus import Caucasus

from dcs_mission_creator.core import survey
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import reveal_policy

TERRAIN = Caucasus()

#: A coarse raster on purpose: `survey` does no fine terrain work beyond line of
#: sight, and the distances it is asked about are tens of kilometres, so 500 m
#: cells over 200 km is the shape of the question rather than a compromise.
CELL_M = 500
TOP, LEFT = 100_000.0, 0.0


class _Place:
    def __init__(self, name: str, x: float, y: float) -> None:
        self.name = name
        self.kind = "village"
        self.position = Point(x, y, TERRAIN)


class _Overlay:
    """Everything `survey` asks an overlay for, over one elevation array."""

    theater = "Caucasus"

    def __init__(self, heights: np.ndarray, places: list[_Place] | None = None) -> None:
        self.heights = heights
        self._places = places or []
        self.asked: list[tuple[Point, float]] = []

    def _cell(self, point: Point) -> tuple[int, int]:
        return int((TOP - point.x) / CELL_M), int((point.y - LEFT) / CELL_M)

    def elevation_at(self, point: Point) -> int:
        row, col = self._cell(point)
        return int(self.heights[row, col])

    def slope_at(self, point: Point) -> float:
        return 3.0

    def vegetation_at(self, point: Point):
        class _V:
            name = "NONE"

        return _V()

    def distance_to_road_m(self, point: Point) -> float:
        return 120.0

    def is_built_up(self, point: Point) -> bool:
        return False

    def local_prominence_m(self, point: Point, radius_m: float = 2_000.0) -> float:
        return -40.0

    def places(self, point: Point, radius_m: float) -> list[_Place]:
        return [
            p
            for p in self._places
            if point.distance_to_point(Point(p.position.x, p.position.y, TERRAIN))
            <= radius_m
        ]

    def line_of_sight(
        self, a: Point, b: Point, eye_a_m: float = 2.0, eye_b_m: float = 2.0
    ) -> bool:
        """Straight sample along the row/col line, the same shape as the real one."""
        (ra, ca), (rb, cb) = self._cell(a), self._cell(b)
        steps = max(abs(ra - rb), abs(ca - cb), 1)
        top_a = float(self.heights[ra, ca]) + eye_a_m
        top_b = float(self.heights[rb, cb]) + eye_b_m
        for i in range(1, steps):
            t = i / steps
            r = int(round(ra + (rb - ra) * t))
            c = int(round(ca + (cb - ca) * t))
            if self.heights[r, c] > top_a + (top_b - top_a) * t:
                return False
        return True

    def find_placement(self, near: Point, radius_m: float, require, count: int = 1):
        """A deterministic stand-in: cells on a coarse grid inside the radius.

        Deliberately returned **out of distance order**, because that is what the
        real one does — it samples its mask — and putting the nearest one first
        is the whole reason `survey.spots` exists.
        """
        self.asked.append((near, radius_m))
        step = 5 * CELL_M
        found = [
            Point(near.x + dx, near.y + dy, TERRAIN)
            for dy in range(int(radius_m), -int(radius_m) - 1, -step)
            for dx in range(int(radius_m), -int(radius_m) - 1, -step)
            if (dx * dx + dy * dy) <= radius_m * radius_m
        ]
        return found[:count]


def _flat(value: float = 100.0) -> _Overlay:
    return _Overlay(np.full((400, 400), value, dtype=float))


def _at(x: float, y: float) -> Point:
    return Point(x, y, TERRAIN)


# -- the check the module exists for ----------------------------------------


def test_a_point_inside_a_real_envelope_is_reported() -> None:
    """The `ansariyah_works` case: a target 9.4 km from an 18 km battery."""
    overlay = _flat()
    battery = survey.Site("S-125 Tartus", _at(0.0, 0.0), 18_000.0)
    rows = survey.reaches(overlay, {"TARGET": _at(9_400.0, 0.0)}, [battery])
    assert [r.site.label for r in survey.covered(rows)] == ["S-125 Tartus"]
    assert rows[0].margin_m == pytest.approx(-8_600.0)


def test_a_point_outside_it_is_not() -> None:
    overlay = _flat()
    battery = survey.Site("S-125 Tartus", _at(0.0, 0.0), 18_000.0)
    rows = survey.reaches(overlay, {"TARGET": _at(34_400.0, 0.0)}, [battery])
    assert survey.covered(rows) == []
    assert rows[0].margin_m == pytest.approx(16_400.0)


def test_a_site_with_no_envelope_reports_distance_only() -> None:
    """An EWR cannot shoot, so a margin against it would be a made-up number."""
    overlay = _flat()
    rows = survey.reaches(
        overlay, {"P": _at(5_000.0, 0.0)}, [survey.Site("EWR", _at(0.0, 0.0))]
    )
    assert rows[0].margin_m is None
    assert rows[0].covered is False


def test_the_objective_s_own_defences_are_not_a_finding() -> None:
    """`ansariyah_works`: the run-in enters the Osa on the works, necessarily.

    It still has to appear on the table — its line of sight is the column that
    decides whether the point survives — but it must not swamp `covered`, which
    is the list a build gate reads.
    """
    overlay = _flat()
    osa = survey.Site("SA-8 works", _at(0.0, 0.0), 10_300.0, defends_objective=True)
    points = {"TARGET": _at(1_400.0, 0.0), "IP": _at(4_000.0, 0.0)}
    rows = survey.reaches(overlay, points, [osa])
    assert all(r.margin_m is not None and r.margin_m < 0.0 for r in rows)
    assert survey.covered(rows) == []
    assert "defends the objective" in survey.report(rows)


def test_a_ring_over_the_target_is_still_a_finding_unless_declared() -> None:
    """The counter-example that killed the derived rule.

    Inferring "a ring covering the objective is not a finding" from the geometry
    reads as principled and is exactly wrong: the defect this module was written
    after — a coastal battery 9.4 km from a target it had no business reaching —
    *is* a ring covering the objective. Undeclared, it must still fire.
    """
    overlay = _flat()
    stray = survey.Site("S-125 Tartus", _at(0.0, 0.0), 18_000.0)
    rows = survey.reaches(overlay, {"TARGET": _at(9_400.0, 0.0)}, [stray])
    assert [r.site.label for r in survey.covered(rows)] == ["S-125 Tartus"]


# -- terrain is the other half ----------------------------------------------


def test_a_ridge_between_them_masks_the_point() -> None:
    """The claim every low corridor in this project rests on, measured."""
    heights = np.full((400, 400), 100.0)
    heights[:, 12:16] = 3_000.0  # a wall between the site and the point
    overlay = _Overlay(heights)
    site = survey.Site("SA-11", _at(5_000.0, 0.0), 50_000.0)
    rows = survey.reaches(overlay, {"IP": _at(5_000.0, 8_000.0)}, [site], agl_m=150.0)
    assert rows[0].covered is True, "inside the envelope on distance"
    assert rows[0].visible is False, "and masked from it by the ridge"


def test_line_of_sight_is_tested_at_the_height_asked_for() -> None:
    """The same point at 150 m and at 3,000 m is a different answer."""
    heights = np.full((400, 400), 100.0)
    heights[:, 12:16] = 1_500.0
    overlay = _Overlay(heights)
    site = survey.Site("SA-11", _at(5_000.0, 0.0), 50_000.0)
    point = {"IP": _at(5_000.0, 8_000.0)}
    assert survey.reaches(overlay, point, [site], agl_m=150.0)[0].visible is False
    assert survey.reaches(overlay, point, [site], agl_m=5_000.0)[0].visible is True


# -- what the map will show, versus what is true -----------------------------


def test_the_drawn_margin_is_tighter_than_the_real_one() -> None:
    """And it comes from `map_draw`, so the two cannot drift apart."""
    site = survey.Site("S-125", _at(0.0, 0.0), 18_000.0)
    factor = reveal_policy(Difficulty.VETERAN).radius_factor
    assert site.drawn_margin_m(34_400.0, Difficulty.VETERAN) == pytest.approx(
        34_400.0 - 18_000.0 * factor
    )
    assert site.drawn_margin_m(34_400.0, Difficulty.VETERAN) < 16_400.0


def test_a_site_with_no_envelope_has_no_drawn_margin() -> None:
    assert survey.Site("EWR", _at(0.0, 0.0)).drawn_margin_m(5_000.0, "trained") is None


def test_reveal_policy_accepts_a_label_as_well_as_the_enum() -> None:
    assert reveal_policy("ace") == reveal_policy(Difficulty.ACE)


# -- siting: ranked, and described ------------------------------------------


def test_spots_come_back_nearest_first() -> None:
    """`find_placement` samples; siting wants the best one near where you asked."""
    overlay = _flat()
    anchor = _at(0.0, 0.0)
    found = survey.spots(overlay, anchor, 4_000.0, require=object(), count=5)
    distances = [anchor.distance_to_point(s.position) for s in found]
    assert distances == sorted(distances)


def test_spots_are_described_and_named() -> None:
    """A bare `Point` is not enough to decide anything — that was the loop."""
    overlay = _flat()
    overlay._places = [_Place("Al-Ghansala", 0.0, 500.0)]
    spot = survey.spots(overlay, _at(0.0, 0.0), 2_000.0, require=object(), count=1)[0]
    assert spot.places[0] == "Al-Ghansala"
    assert spot.elevation_m == 100
    assert "Al-Ghansala" in spot.row()
    assert f"{spot.lat:.4f}" in spot.row(), "degrees, for a table somebody can check"


def test_spots_draws_a_wider_pool_than_it_returns() -> None:
    """Otherwise ranking picks the best of one and the order means nothing."""
    overlay = _flat()
    survey.spots(overlay, _at(0.0, 0.0), 4_000.0, require=object(), count=3, pool=40)
    assert overlay.asked, "the overlay was queried"


def test_report_is_empty_talk_rather_than_a_crash_with_no_rows() -> None:
    assert "no points" in survey.report([])
