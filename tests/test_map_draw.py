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
