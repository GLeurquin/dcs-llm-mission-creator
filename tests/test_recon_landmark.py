"""Place-name selection: what gets labelled, and what is refused.

No overlay needed — `landmark_marks` only asks its overlay for `places()`, so a
stub stands in and this runs in the default selection. The geometry it is checked
against is the renderer's own `mark_extent`, which is the point: the collision
test and the drawing must not be able to disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from dcs.mapping import Point
from dcs.terrain import Caucasus

from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.landmark import landmark_marks
from dcs_mission_creator.core.recon.render import Mark, mark_extent

_TERRAIN = Caucasus()
_CENTER = Point(0.0, 0.0, _TERRAIN)


@dataclass(frozen=True)
class _Place:
    name: str
    kind: str
    position: Point


class _StubOverlay:
    """Just enough of `MapOverlay` for `landmark_marks`: the places it offers."""

    def __init__(self, *places: _Place) -> None:
        self._places = list(places)

    def places(self, center: Point, radius_m: float) -> list[_Place]:
        return [
            p for p in self._places if center.distance_to_point(p.position) <= radius_m
        ]


def _at(north: float, east: float, name: str, kind: str = "village") -> _Place:
    return _Place(name=name, kind=kind, position=Point(north, east, _TERRAIN))


def _frame() -> Frame:
    return Frame(center=_CENTER)


def test_a_place_is_labelled_with_an_upper_case_dot_mark() -> None:
    marks = landmark_marks(_StubOverlay(_at(3_000.0, 2_000.0, "Dranda")), _frame())
    assert [(m.kind, m.text) for m in marks] == [("place", "DRANDA")]


def test_nothing_is_labelled_when_the_overlay_has_no_places() -> None:
    assert landmark_marks(_StubOverlay(), _frame()) == []


def test_a_bigger_settlement_outranks_a_nearer_hamlet() -> None:
    overlay = _StubOverlay(
        _at(500.0, 500.0, "Tiny", kind="hamlet"),
        _at(4_000.0, 4_000.0, "Big", kind="town"),
    )
    marks = landmark_marks(overlay, _frame(), limit=1)
    assert [m.text for m in marks] == ["BIG"]


def test_same_class_ranks_by_distance_to_what_the_frame_is_about() -> None:
    overlay = _StubOverlay(
        _at(0.0, 8_000.0, "Zulu"),  # alphabetically last, nearest
        _at(0.0, -11_000.0, "Alpha"),
    )
    marks = landmark_marks(overlay, _frame(), limit=1)
    assert [m.text for m in marks] == ["ZULU"], "name order must not decide this"


def test_two_settlements_sharing_a_name_are_labelled_once() -> None:
    overlay = _StubOverlay(_at(0.0, 5_000.0, "Akhywaa"), _at(0.0, -5_000.0, "Akhywaa"))
    assert len(landmark_marks(overlay, _frame(), min_separation_m=100.0)) == 1


def test_a_label_that_would_print_into_the_target_label_is_refused() -> None:
    """The bug this collision test exists for.

    A group's own text runs ~190 px to the right of its bracket — 4.7 km of ground
    at 25 m/px — so a village comfortably clear of the bracket *centre* still had
    its name printed through the target's. Only an extent test catches it.
    """
    target = Mark(
        x=0.0,
        y=0.0,
        kind="group",
        radius_m=700.0,
        text="7 DET  TRK 222  40 KM/H",
    )
    # 3 km east of the target: well outside the bracket, squarely inside its label.
    victim = _at(0.0, 3_000.0, "Satqebuchavo")
    frame = _frame()
    assert landmark_marks(_StubOverlay(victim), frame) != [], "reachable on its own"
    assert landmark_marks(_StubOverlay(victim), frame, avoid=[target]) == []


def test_labels_do_not_collide_with_each_other() -> None:
    """Two names a few hundred metres apart cannot both be drawn legibly."""
    overlay = _StubOverlay(_at(0.0, 0.0, "Alpha"), _at(120.0, 400.0, "Bravo"))
    marks = landmark_marks(overlay, _frame(), min_separation_m=0.0)
    assert [m.text for m in marks] == ["ALPHA"]


def test_a_label_running_off_the_frame_is_refused() -> None:
    frame = _frame()
    half_width_m = frame.width_m / 2.0
    inside = _at(0.0, half_width_m - 4_000.0, "Short")
    # Same spot, a name long enough that its text would leave the frame.
    edge = _at(0.0, half_width_m - 400.0, "Averyverylongsettlementname")
    assert [m.text for m in landmark_marks(_StubOverlay(inside), frame)] == ["SHORT"]
    assert landmark_marks(_StubOverlay(edge), frame) == []


def test_limit_caps_the_number_of_labels() -> None:
    overlay = _StubOverlay(*(_at(0.0, 4_000.0 * i, f"Place{i}") for i in range(1, 5)))
    assert len(landmark_marks(overlay, _frame(), limit=2)) == 2


def test_mark_extent_covers_the_text_not_just_the_symbol() -> None:
    frame = _frame()
    bare = Mark(x=0.0, y=0.0, kind="group", radius_m=700.0)
    labelled = Mark(x=0.0, y=0.0, kind="group", radius_m=700.0, text="7 DET  TRK 222")
    assert mark_extent(frame, labelled)[2] > mark_extent(frame, bare)[2] + 50.0


def test_selection_is_deterministic() -> None:
    overlay = _StubOverlay(
        *(_at(1_000.0 * i, 900.0 * i, f"P{i}") for i in range(1, 12))
    )
    frame = _frame()
    first = [m.text for m in landmark_marks(overlay, frame)]
    assert first == [m.text for m in landmark_marks(overlay, frame)]
