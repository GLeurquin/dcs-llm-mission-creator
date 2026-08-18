"""Tests for `core.frontline` — pure geometry, no Mission, no overlay, no DCS.

The module exists so a target cannot be attacked from an arbitrary bearing, so
the properties worth pinning are the ones that claim holds: the line lies across
the approach axis rather than along it, the seam is the only frontage without a
position on it, the wings lead, and a line that cannot hold positions says so
instead of returning something degenerate.
"""

from __future__ import annotations

import math

import pytest
from dcs.mapping import Point
from dcs.terrain import Caucasus

from dcs_mission_creator.core.frontline import Frontline, plan_frontline

TERRAIN = Caucasus()

AO = Point(0.0, 0.0, TERRAIN)
#: The friendly field, due north of the AO in DCS terms (`x` is north).
HOME = Point(100_000.0, 0.0, TERRAIN)


def plan(
    *,
    standoff_m: float = 30_000.0,
    span_m: float = 90_000.0,
    bow_m: float = 12_000.0,
    sectors_per_side: int = 2,
    seam_width_m: float = 30_000.0,
) -> Frontline:
    """A front line 30 km north of the AO, 90 km wide, with a 30 km seam."""
    return plan_frontline(
        defends=AO,
        facing=HOME,
        standoff_m=standoff_m,
        span_m=span_m,
        bow_m=bow_m,
        sectors_per_side=sectors_per_side,
        seam_width_m=seam_width_m,
    )


# ------------------------------------------------------------------ the geometry
def test_the_seam_sits_on_the_axis_at_the_standoff_distance() -> None:
    front = plan()
    assert front.seam.distance_to_point(AO) == pytest.approx(30_000.0, abs=1.0)
    assert front.seam.y == pytest.approx(0.0, abs=1.0)


def test_the_line_runs_across_the_axis_not_along_it() -> None:
    """Both shoulders are off to the sides, at half the span each."""
    front = plan()
    for shoulder in front.shoulders:
        lateral = abs(shoulder.y - front.seam.y)
        assert lateral == pytest.approx(45_000.0, abs=1.0)
    assert front.shoulders[0].y < front.seam.y < front.shoulders[1].y


def test_the_wings_lead_by_the_bow() -> None:
    """A bowed line puts its tips nearer home than its middle."""
    front = plan()
    for shoulder in front.shoulders:
        assert shoulder.x - front.seam.x == pytest.approx(12_000.0, abs=1.0)
    for sector in front.sectors:
        assert front.seam.x < sector.x < front.shoulders[0].x


def test_a_straight_line_has_no_bow() -> None:
    front = plan(bow_m=0.0)
    for position in front.positions:
        assert position.x == pytest.approx(front.seam.x, abs=1.0)


def test_the_seam_is_the_only_frontage_without_a_position() -> None:
    """Nothing is dug in inside the seam, and every sector is outside it."""
    front = plan()
    for position in front.positions:
        assert abs(position.y - front.seam.y) >= 15_000.0


def test_sectors_come_in_pairs_ordered_along_the_line() -> None:
    front = plan(sectors_per_side=3)
    assert len(front.sectors) == 6
    laterals = [s.y for s in front.sectors]
    assert laterals == sorted(laterals)


def test_the_trace_is_the_whole_line_including_the_seam() -> None:
    """The front is continuous even where its air defence is not."""
    front = plan()
    assert front.trace[0] == front.shoulders[0]
    assert front.trace[-1] == front.shoulders[1]
    assert front.seam in front.trace
    assert len(front.trace) == len(front.sectors) + 3


def test_facing_deg_points_back_at_the_friendly_side() -> None:
    front = plan()
    assert front.facing_deg == pytest.approx(AO.heading_between_point(HOME), abs=0.5)
    # ...and the line stands between the AO and it.
    assert front.seam.distance_to_point(HOME) < AO.distance_to_point(HOME)


def test_positions_are_the_shoulders_plus_every_sector() -> None:
    front = plan()
    assert len(front.positions) == len(front.sectors) + 2
    assert front.positions[0] == front.shoulders[0]
    assert front.positions[-1] == front.shoulders[1]
    assert front.positions[1:-1] == front.sectors


def test_a_line_with_no_sectors_is_still_two_shoulders() -> None:
    front = plan(sectors_per_side=0)
    assert front.sectors == ()
    assert len(front.positions) == 2


def test_the_frontage_is_wide_enough_to_be_worth_flying_round() -> None:
    """The span is end to end, so the detour is the number the mission set."""
    front = plan()
    tip_to_tip = front.shoulders[0].distance_to_point(front.shoulders[1])
    assert tip_to_tip == pytest.approx(90_000.0, abs=1_000.0)


# -------------------------------------------------------------------- the guards
@pytest.mark.parametrize("span", [0.0, -1_000.0])
def test_a_line_with_no_frontage_is_rejected(span: float) -> None:
    with pytest.raises(ValueError, match="span_m must be positive"):
        plan(span_m=span)


def test_a_seam_as_wide_as_the_line_is_rejected() -> None:
    """Otherwise the sectors would be placed on top of the shoulders."""
    with pytest.raises(ValueError, match="leaves no frontage"):
        plan(span_m=40_000.0, seam_width_m=40_000.0)


def test_a_negative_sector_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="sectors_per_side must be >= 0"):
        plan(sectors_per_side=-1)


# ------------------------------------------------------------------ reproducible
def test_two_plans_of_the_same_line_agree() -> None:
    """No sampling anywhere in here — the same call gives the same line."""
    first, second = plan(), plan()
    assert [(p.x, p.y) for p in first.trace] == [(p.x, p.y) for p in second.trace]


def test_the_line_is_perpendicular_to_the_axis() -> None:
    """Shoulder-to-shoulder crosses the AO -> home bearing at a right angle."""
    front = plan(bow_m=0.0)
    line_deg = front.shoulders[0].heading_between_point(front.shoulders[1])
    assert abs((line_deg - front.facing_deg) % 180.0) == pytest.approx(90.0, abs=0.5)
    assert math.isclose(front.shoulders[0].x, front.shoulders[1].x, abs_tol=1.0)
