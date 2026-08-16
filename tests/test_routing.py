"""Tests for `core.routing` — pure geometry, no Mission, no overlay, no DCS.

The module decides where AI packages fly relative to SAM envelopes, so the
properties worth pinning are the guarantees the docstrings make: routes leave
the rings, standoff points sit outside them, and the endpoint-covering case
degrades predictably rather than bending the route somewhere worse.
"""

from __future__ import annotations

import pytest
from dcs.mapping import Point
from dcs.terrain import Caucasus

from dcs_mission_creator.core.routing import (
    ThreatRing,
    _bend_around,
    _deepest_breach,
    _distance_to_leg,
    avoid_threats,
    clear_of_threats,
    standoff_point,
)

TERRAIN = Caucasus()


def pt(x: float, y: float) -> Point:
    return Point(x, y, TERRAIN)


def legs_clear(route: list[Point], rings: list[ThreatRing]) -> bool:
    """No segment of `route` passes through any ring."""
    return all(
        _distance_to_leg(ring.position, route[i], route[i + 1]) >= ring.radius_m
        for i in range(len(route) - 1)
        for ring in rings
    )


# --------------------------------------------------------------- ThreatRing
def test_margin_is_signed_from_the_edge() -> None:
    ring = ThreatRing(pt(0, 0), 10_000.0, "SA-6")
    assert ring.margin_m(pt(0, 25_000)) == pytest.approx(15_000.0)
    assert ring.margin_m(pt(0, 4_000)) == pytest.approx(-6_000.0)
    assert ring.margin_m(pt(0, 10_000)) == pytest.approx(0.0)


def test_covers_uses_clearance_as_a_buffer() -> None:
    ring = ThreatRing(pt(0, 0), 10_000.0)
    just_outside = pt(0, 11_000)
    assert not ring.covers(just_outside)
    assert ring.covers(just_outside, clearance_m=5_000.0)


def test_clear_of_threats_requires_every_ring() -> None:
    rings = [ThreatRing(pt(0, 0), 10_000.0), ThreatRing(pt(50_000, 0), 10_000.0)]
    assert clear_of_threats(pt(25_000, 0), rings)
    assert not clear_of_threats(pt(5_000, 0), rings)


# ------------------------------------------------------------ _distance_to_leg
def test_distance_to_leg_measures_the_segment_not_the_line() -> None:
    a, b = pt(0, 0), pt(0, 10_000)
    # Beyond the far end: nearest point is the endpoint, not the infinite line.
    assert _distance_to_leg(pt(0, 20_000), a, b) == pytest.approx(10_000.0)
    # Perpendicular to the middle of the segment.
    assert _distance_to_leg(pt(3_000, 5_000), a, b) == pytest.approx(3_000.0)


def test_distance_to_leg_handles_a_degenerate_segment() -> None:
    """A zero-length leg must not divide by zero — it is a point distance."""
    a = pt(1_000, 1_000)
    assert _distance_to_leg(pt(4_000, 5_000), a, a) == pytest.approx(5_000.0)


# --------------------------------------------------------------- avoid_threats
def test_unobstructed_route_is_left_alone() -> None:
    rings = [ThreatRing(pt(0, 200_000), 10_000.0, "far away")]
    route = avoid_threats(pt(0, 0), pt(0, 50_000), rings)
    assert route == [pt(0, 0), pt(0, 50_000)]


def test_route_bends_around_a_ring_on_the_direct_line() -> None:
    rings = [ThreatRing(pt(0, 50_000), 20_000.0, "SA-6")]
    start, target = pt(0, 0), pt(0, 100_000)
    route = avoid_threats(start, target, rings, clearance_m=5_000.0)

    assert route[0] == start and route[-1] == target
    assert len(route) > 2, "a ring straddling the route should force a detour"
    assert legs_clear(route, rings)


def test_route_clears_several_stacked_rings() -> None:
    rings = [
        ThreatRing(pt(0, 40_000), 15_000.0, "SA-2"),
        ThreatRing(pt(10_000, 70_000), 12_000.0, "SA-6"),
        ThreatRing(pt(-8_000, 100_000), 10_000.0, "SA-8"),
    ]
    route = avoid_threats(pt(0, 0), pt(0, 140_000), rings, clearance_m=4_000.0)
    assert legs_clear(route, rings)


def test_rings_covering_an_endpoint_are_ignored() -> None:
    """You cannot detour out of the envelope your target sits in."""
    target = pt(0, 50_000)
    rings = [ThreatRing(pt(0, 50_000), 20_000.0, "over the target")]
    route = avoid_threats(pt(0, 0), target, rings)
    assert route == [pt(0, 0), target], "should not try to route around the target"


def test_route_always_starts_and_ends_where_asked() -> None:
    """Even when the detour budget runs out, the caller can still fly it."""
    rings = [ThreatRing(pt(0, 10_000 * i), 9_000.0, f"ring-{i}") for i in range(2, 12)]
    start, target = pt(0, 0), pt(0, 130_000)
    route = avoid_threats(start, target, rings, max_detours=2)
    assert route[0] == start and route[-1] == target


# -------------------------------------------------------------- _deepest_breach
def test_deepest_breach_picks_the_worst_incursion() -> None:
    route = [pt(0, 0), pt(0, 100_000)]
    shallow = ThreatRing(pt(9_000, 30_000), 10_000.0, "shallow")
    deep = ThreatRing(pt(1_000, 60_000), 20_000.0, "deep")
    breach = _deepest_breach(route, [shallow, deep])
    assert breach is not None
    _, ring = breach
    assert ring.label == "deep"


def test_deepest_breach_returns_none_when_clean() -> None:
    route = [pt(0, 0), pt(0, 10_000)]
    assert _deepest_breach(route, [ThreatRing(pt(0, 500_000), 1_000.0)]) is None


def test_bend_around_takes_the_near_side() -> None:
    """The detour should follow the side the leg already passes on."""
    a, b = pt(0, 0), pt(0, 100_000)
    ring = ThreatRing(pt(5_000, 50_000), 20_000.0)
    bend = _bend_around(a, b, ring, clearance_m=0.0)
    assert bend.x < ring.position.x, "should bend west, the shorter way round"
    assert ring.position.distance_to_point(bend) == pytest.approx(ring.radius_m)


# -------------------------------------------------------------- standoff_point
def test_standoff_respects_minimum_distance_and_home_side() -> None:
    target, home = pt(0, 100_000), pt(0, 0)
    ip = standoff_point(target, toward=home, threats=[], min_distance_m=25_000.0)
    assert target.distance_to_point(ip) >= 25_000.0
    assert ip.y < target.y, "with no threats the IP sits on the home side"


def test_standoff_stays_outside_every_ring() -> None:
    target, home = pt(0, 100_000), pt(0, 0)
    rings = [ThreatRing(pt(0, 70_000), 25_000.0, "blocks the direct side")]
    ip = standoff_point(
        target, toward=home, threats=rings, min_distance_m=20_000.0, clearance_m=3_000.0
    )
    assert clear_of_threats(ip, rings, clearance_m=3_000.0)


def test_standoff_falls_back_to_the_least_exposed_point() -> None:
    """A target ringed on every side has no clean answer; still return one."""
    target, home = pt(0, 100_000), pt(0, 0)
    rings = [ThreatRing(target, 500_000.0, "covers everything")]
    ip = standoff_point(target, toward=home, threats=rings, max_distance_m=40_000.0)
    assert target.distance_to_point(ip) > 0.0
