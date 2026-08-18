"""Tests for `core.map_draw` — the reveal policy, on a bare `Mission`.

Only the front-line rule is pinned here, because it is the one exception in the
module and the easy one to "fix" by mistake: every other enemy drawing is
withheld as difficulty rises, and this one is not.
"""

from __future__ import annotations

from dcs.drawing.drawings import StandardLayer
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay

TERRAIN = Caucasus()
TRACE = [
    Point(0.0, 0.0, TERRAIN),
    Point(10_000.0, 20_000.0, TERRAIN),
    Point(20_000.0, 45_000.0, TERRAIN),
]


def blue_layer(m: Mission) -> list[object]:
    return m.drawings.get_layer(StandardLayer.Blue).objects


def test_the_trace_is_drawn_at_every_difficulty() -> None:
    """Both armies know where the line is, so no difficulty withholds it."""
    for difficulty in Difficulty:
        m = Mission(TERRAIN)
        PlanOverlay(m, difficulty).frontline(TRACE, "FRONT LINE")
        drawn = blue_layer(m)
        assert len(drawn) == 2, f"{difficulty}: expected a line and a label"


def test_an_enemy_site_is_still_withheld_at_ace() -> None:
    """The contrast that makes the test above meaningful."""
    m = Mission(TERRAIN)
    plan = PlanOverlay(m, Difficulty.ACE)
    assert plan.threat(TRACE[0], radius=25_000.0, label="SA-3") is None
    assert blue_layer(m) == []


def test_a_trace_that_is_not_a_line_draws_nothing() -> None:
    m = Mission(TERRAIN)
    PlanOverlay(m, Difficulty.TRAINED).frontline(TRACE[:1], "FRONT LINE")
    assert blue_layer(m) == []


# -- detections: the reveal channel a recon still is allowed to draw from ----


def _detections(difficulty: Difficulty, count: int = 11) -> list[Point]:
    m = Mission(Caucasus())
    truth = [Point(100_000.0 + i * 120.0, 200_000.0, m.terrain) for i in range(count)]
    return PlanOverlay(m, difficulty).detections(truth)


def test_detections_are_withheld_at_veteran_and_ace() -> None:
    """A still with nothing to plot is a frame of empty ground, so none is published."""
    assert _detections(Difficulty.VETERAN) == []
    assert _detections(Difficulty.ACE) == []


def test_detections_at_recruit_stay_essentially_on_truth() -> None:
    m = Mission(Caucasus())
    truth = [Point(100_000.0, 200_000.0, m.terrain)]
    got = PlanOverlay(m, Difficulty.RECRUIT).detections(truth, jitter_m=120.0)
    assert len(got) == 1
    assert truth[0].distance_to_point(got[0]) <= 120.0


def test_trained_detections_share_one_bias_so_the_column_survives() -> None:
    """Per-point offsets would scatter an 11-vehicle column over kilometres.

    The spread between returns must stay close to the truth's own spread; only the
    whole cluster moves.
    """
    m = Mission(Caucasus())
    truth = [Point(100_000.0 + i * 120.0, 200_000.0, m.terrain) for i in range(11)]
    got = PlanOverlay(m, Difficulty.TRAINED).detections(truth, bias_m=1_200.0)

    assert len(got) == len(truth)
    true_span = truth[0].distance_to_point(truth[-1])
    got_span = got[0].distance_to_point(got[-1])
    assert abs(got_span - true_span) < 400.0
    # The cluster as a whole has moved by roughly the registration bias.
    shift = truth[0].midpoint(truth[-1]).distance_to_point(got[0].midpoint(got[-1]))
    assert 800.0 < shift < 1_600.0


def test_detections_of_nothing_is_nothing() -> None:
    assert _detections(Difficulty.TRAINED, count=0) == []
