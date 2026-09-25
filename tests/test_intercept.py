"""The editor-side half of `core/intercept.py`: what has to be true before Lua runs."""

from __future__ import annotations

import pytest
from dcs import planes, task
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus

from dcs_mission_creator.core import intercept, lua
from dcs_mission_creator.core.intercept import Calls, Track, arm_intercepts

_ZONE = [Point(0, 0, None), Point(0, 50_000, None), Point(-50_000, 50_000, None),
         Point(-50_000, 0, None)]  # fmt: skip


def _mission() -> tuple[Mission, Track]:
    m = Mission(Caucasus())
    red = m.flight_group_inflight(
        m.country("Russia"), "Zombie", planes.Su_27, Point(10_000, 25_000, m.terrain),
        altitude=7000, speed=800, maintask=task.CAP, group_size=2,
    )  # fmt: skip
    track = Track(
        group=red,
        label="Zombie",
        orbit=Point(-25_000, 25_000, m.terrain),
        exit=Point(30_000, 25_000, m.terrain),
        home=m.terrain.airports["Beslan"],
        altitude_m=7000,
        speed_kph=800,
        hostile_probability=0.5,
        calls=Calls(intercepted="Magic: {label} turning", hostile="Magic: {label} hot"),
    )
    return m, track


def _script(m: Mission) -> str:
    for rule in m.triggerrules.triggers:
        for act in rule.actions:
            if isinstance(act, lua.InlineDoScript):
                return act.script
    raise AssertionError("no DoScript was added")


def test_inside_is_the_polygon_test() -> None:
    assert intercept.inside(Point(-10_000, 10_000, None), _ZONE)
    assert not intercept.inside(Point(10_000, 10_000, None), _ZONE)


def test_the_intruder_starts_weapons_hold() -> None:
    m, track = _mission()
    arm_intercepts(m, [track], zone=_ZONE)
    last_roe = [t for t in track.group.points[0].tasks if isinstance(t, task.OptROE)][
        -1
    ]
    assert last_roe.params["action"]["params"]["value"] == task.OptROE.Values.WeaponHold


def test_a_defender_loses_its_own_engage_task() -> None:
    """Otherwise OPEN_FIRE still lets it shoot a compliant track."""
    m, track = _mission()
    eagle = m.flight_group_inflight(
        m.country("USA"), "Eagle", planes.F_15C, Point(-20_000, 20_000, m.terrain),
        altitude=8000, speed=800, maintask=task.CAP, group_size=2,
    )  # fmt: skip
    assert any(isinstance(t, task.CAPTaskAction) for t in eagle.points[0].tasks)
    arm_intercepts(m, [track], zone=_ZONE, defenders=[eagle])
    assert not any(
        isinstance(t, (task.CAPTaskAction, task.EngageTargets))
        for t in eagle.points[0].tasks
    )
    assert '"Eagle"' in _script(m)


def test_the_calls_are_rendered_per_track() -> None:
    m, track = _mission()
    arm_intercepts(m, [track], zone=_ZONE)
    script = _script(m)
    assert 'text="Magic: Zombie turning"' in script
    assert "__" not in script.replace("__index", "")


def test_a_track_that_cannot_turn_carries_no_hostile_call() -> None:
    m, track = _mission()
    calm = Track(**{**vars(track), "hostile_probability": 0.0})
    arm_intercepts(m, [calm], zone=_ZONE)
    assert "Zombie hot" not in _script(m)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("hostile_probability", 1.5, "hostile_probability"),
        ("hostile_after_s", (100.0, 10.0), "hostile_after_s"),
        ("orbit", Point(20_000, 25_000, None), "orbit is outside"),
        ("exit", Point(-10_000, 25_000, None), "exit point is inside"),
    ],
)
def test_it_refuses_a_track_that_could_not_resolve(field, value, match) -> None:
    m, track = _mission()
    bad = Track(**{**vars(track), field: value})
    with pytest.raises(ValueError, match=match):
        arm_intercepts(m, [bad], zone=_ZONE)


def test_it_refuses_a_zone_that_is_not_a_polygon() -> None:
    m, track = _mission()
    with pytest.raises(ValueError, match="three vertices"):
        arm_intercepts(m, [track], zone=_ZONE[:2])
