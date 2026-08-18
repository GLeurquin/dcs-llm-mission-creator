"""Tests for `core.air_defense` — needs a Mission, but no overlay and no DCS.

pydcs ships its own terrain and unit data, so a `Mission(Caucasus())` is cheap
to build here. That matters because only two of the twelve site builders are
used by any mission today; without these the rest are unexercised.
"""

from __future__ import annotations

import random

import pytest
from dcs import templates
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import air_defense as ad

#: The dispersion pass wobbles every placement, so a footprint lands within this
#: much of the radius asked for rather than exactly on it.
_JITTER_TOLERANCE = 0.25


@pytest.fixture
def mission() -> Mission:
    return Mission(Caucasus())


@pytest.fixture
def site_position(mission: Mission) -> Point:
    return mission.terrain.airports["Batumi"].position


# name -> (builder, expected unit count at default size)
SITES = {
    "sa2": (ad.build_sa2_site, 9),  # SR + Fan Song + RD-75 + 6 launchers
    "sa3": (ad.build_sa3_site, 6),  # SR + Low Blow + 4 launchers
    "sa5": (ad.build_sa5_site, 6),  # SR + Square Pair + 4 launchers
    "nasams": (ad.build_nasams_site, 5),  # SR + C2 + 3 launchers
    "irist": (ad.build_irist_site, 5),  # STR + C2 + 3 launchers
    "roland": (ad.build_roland_site, 3),  # radar + 2 ADS
    "rapier": (ad.build_rapier_site, 4),  # blindfire + tracker + 2 launchers
    "hq7": (ad.build_hq7_site, 5),  # STR + 4 TELARs
    "sa8": (ad.build_sa8_site, 3),  # 3 self-contained TELARs
    "sa15": (ad.build_sa15_site, 2),  # 2 self-contained TELARs
    "sa19": (ad.build_sa19_site, 2),  # 2 self-contained TELARs
}


@pytest.mark.parametrize(("key", "expected"), [(k, v[1]) for k, v in SITES.items()])
def test_site_has_the_expected_units(
    mission: Mission, site_position: Point, key: str, expected: int
) -> None:
    build = SITES[key][0]
    vg = build(mission, mission.country("Russia"), site_position)
    assert len(vg.units) == expected


@pytest.mark.parametrize("key", list(SITES))
def test_launcher_count_is_configurable(
    mission: Mission, site_position: Point, key: str
) -> None:
    """Force composition is a per-call argument, not baked into the table."""
    build = SITES[key][0]
    small = build(mission, mission.country("Russia"), site_position, launchers=2)
    large = build(mission, mission.country("Russia"), site_position, launchers=5)
    assert len(large.units) - len(small.units) == 3


@pytest.mark.parametrize("key", list(SITES))
def test_prefix_and_skill_apply(
    mission: Mission, site_position: Point, key: str
) -> None:
    build = SITES[key][0]
    vg = build(
        mission,
        mission.country("Russia"),
        site_position,
        prefix="North ",
        skill=Skill.Excellent,
    )
    assert vg.name.startswith("North ")
    assert all(u.skill == Skill.Excellent for u in vg.units)


def _min_spacing(vg: VehicleGroup) -> float:
    """Closest two units in a site — the measure of how much of it one bomb kills."""
    pts = [u.position for u in vg.units]
    return min(a.distance_to_point(b) for i, a in enumerate(pts) for b in pts[i + 1 :])


# name -> smallest footprint the system should occupy (m). A prepared site is
# genuinely compact and disperses in its radars; a self-propelled one disperses
# in its launchers. See the offset constants in `air_defense`.
FOOTPRINTS = {
    "sa2": 200.0,
    "sa3": 120.0,
    "sa5": 300.0,
    "nasams": 200.0,
    "irist": 200.0,
    "roland": 240.0,
    "rapier": 160.0,
    "hq7": 260.0,
    "sa8": 320.0,
    "sa15": 260.0,
    "sa19": 240.0,
}


@pytest.mark.parametrize("key", list(SITES))
def test_a_site_is_not_built_in_a_heap(
    mission: Mission, site_position: Point, key: str
) -> None:
    """The point of the dispersion pass: no site fits under one bomb.

    Two separate claims. Nothing is stacked — a pair of vehicles a few metres
    apart is a modelling error, not a tight formation. And the site occupies the
    footprint its own family should: everything here used to fit inside a 160 m
    circle regardless of what the system was.
    """
    vg = SITES[key][0](mission, mission.country("Russia"), site_position)
    assert _min_spacing(vg) > 15.0, "two units almost on top of each other"
    assert ad.footprint_m(vg) >= FOOTPRINTS[key], "site is still in a heap"


@pytest.mark.parametrize("key", list(SITES))
def test_the_site_stays_inside_the_ring_the_briefing_would_draw(
    mission: Mission, site_position: Point, key: str
) -> None:
    """Dispersion is bounded, because the drawn threat ring is a claim.

    `PlanOverlay.threat` offsets an estimated ring by 2 km at `trained` and
    inflates its radius; a site whose own vehicles wandered further than that
    would make the map wrong rather than approximate.
    """
    vg = SITES[key][0](mission, mission.country("Russia"), site_position)
    assert ad.footprint_m(vg) < 700.0


def test_layout_is_deterministic_for_a_given_seed(
    mission: Mission, site_position: Point
) -> None:
    """The jitter is drawn from the seeded stdlib `random`, so builds repeat.

    `MissionBuilder.generate` seeds it from the mission slug, which is what
    keeps a rebuilt `.miz` identical entry for entry.
    """
    russia = mission.country("Russia")

    def positions(seed: int) -> list[tuple[float, float]]:
        random.seed(seed)
        vg = ad.build_sa2_site(mission, russia, site_position)
        return [(u.position.x, u.position.y) for u in vg.units]

    assert positions(7) == positions(7)
    assert positions(7) != positions(8), "the layout is not jittered at all"


def test_the_launcher_ring_turns_with_the_site_heading(
    mission: Mission, site_position: Point
) -> None:
    """A dispersed site has a gap in its fan, so where the fan points matters."""
    russia = mission.country("Russia")
    random.seed(3)
    north = ad.build_sa3_site(mission, russia, site_position, heading=0)
    random.seed(3)
    east = ad.build_sa3_site(mission, russia, site_position, heading=90)
    bearings = [
        [round(site_position.heading_between_point(u.position)) for u in vg.units[1:]]
        for vg in (north, east)
    ]
    assert bearings[0] != bearings[1]
    # Same shape, rotated: every bearing moved by the same 90 degrees.
    turned = sorted((b + 90) % 360 for b in bearings[0])
    assert turned == sorted(bearings[1])


def test_disperse_site_opens_up_a_pydcs_template(
    mission: Mission, site_position: Point
) -> None:
    """The worst heap in the project is not ours: pydcs parks the SA-6 at 30 m."""
    russia = mission.country("Russia")
    packed = templates.VehicleTemplate.sa6_site(mission, russia, site_position, 0)
    before = _min_spacing(packed), ad.footprint_m(packed)
    types_before = [u.type for u in packed.units]

    ad.disperse_site(packed, radius_m=300.0)

    assert _min_spacing(packed) > before[0], "still as easy to kill in one pass"
    assert ad.footprint_m(packed) > before[1]
    assert ad.footprint_m(packed) == pytest.approx(300.0, rel=_JITTER_TOLERANCE)
    assert [u.type for u in packed.units] == types_before, (
        "unit order must survive — missions read the radar out of units[0]"
    )


def test_disperse_site_leaves_a_group_it_cannot_spread_alone(
    mission: Mission, site_position: Point
) -> None:
    """A one-unit group (an EWR) has no shape to inflate and no heap to break."""
    ewr = ad.build_ewr_chain(
        mission, mission.country("Russia"), [site_position], prefix="EWR"
    )[0]
    before = ewr.units[0].position
    ad.disperse_site(ewr, radius_m=300.0)
    assert ewr.units[0].position == before


def test_disperse_site_rings_units_stacked_on_the_leader(
    mission: Mission, site_position: Point
) -> None:
    """Nothing to scale, so fall back to a ring rather than leaving the stack."""
    russia = mission.country("Russia")
    vg = mission.vehicle_group(
        russia, "stacked", AirDefence.Osa_9A33_ln, site_position, 0
    )
    for i in range(3):
        u = mission.vehicle(f"stuck {i}", AirDefence.Osa_9A33_ln)
        u.position = site_position
        vg.add_unit(u)
    ad.disperse_site(vg, radius_m=300.0)
    assert _min_spacing(vg) > 15.0
    assert ad.footprint_m(vg) == pytest.approx(300.0, rel=_JITTER_TOLERANCE)


def test_a_dispersed_site_warns_when_it_was_never_checked_against_terrain(
    mission: Mission, site_position: Point, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skipping the snap is cheap on a 65 m site and a gamble on a 400 m one."""
    ad.build_sa8_site(mission, mission.country("Russia"), site_position)
    assert "dispersed air-defense site" in capsys.readouterr().out


def test_sa13_dog_ear_is_optional(mission: Mission, site_position: Point) -> None:
    russia = mission.country("Russia")
    with_radar = ad.build_sa13_site(mission, russia, site_position)
    without = ad.build_sa13_site(mission, russia, site_position, with_dog_ear=False)
    assert len(with_radar.units) - len(without.units) == 1
    assert any(u.type == AirDefence.Dog_Ear_radar.id for u in with_radar.units)
    assert not any(u.type == AirDefence.Dog_Ear_radar.id for u in without.units)


def test_self_contained_shorad_has_no_separate_radar(
    mission: Mission, site_position: Point
) -> None:
    """An SA-15 section is all TELARs — there is no radar to kill."""
    vg = ad.build_sa15_site(mission, mission.country("Russia"), site_position)
    assert {u.type for u in vg.units} == {AirDefence.Tor_9A331.id}


def test_builders_keep_their_names_and_docstrings() -> None:
    """They are built by a factory; they should not all report as `build`."""
    assert ad.build_sa2_site.__name__ == "build_sa2_site"  # ty: ignore[unresolved-attribute]
    assert "SA-2" in (ad.build_sa2_site.__doc__ or "")


def test_snap_warns_when_only_one_of_overlay_and_terrain_is_given(
    mission: Mission, site_position: Point, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing overlay without terrain used to skip snapping in silence.

    structlog writes to stdout rather than through stdlib logging, so this
    reads the captured stream rather than `caplog`.
    """
    ad.build_sa8_site(
        mission,
        mission.country("Russia"),
        site_position,
        terrain=mission.terrain,
    )
    assert "not snapped" in capsys.readouterr().out


def test_no_mismatch_warning_when_neither_overlay_nor_terrain_is_given(
    mission: Mission, site_position: Point, capsys: pytest.CaptureFixture[str]
) -> None:
    """Opting out of snapping is a choice; passing half of what it needs is not.

    A wide site still says something about never having been checked against the
    terrain — that is the test above this one, and a different message.
    """
    ad.build_sa8_site(mission, mission.country("Russia"), site_position)
    assert "not snapped" not in capsys.readouterr().out
