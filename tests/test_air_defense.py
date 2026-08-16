"""Tests for `core.air_defense` — needs a Mission, but no overlay and no DCS.

pydcs ships its own terrain and unit data, so a `Mission(Caucasus())` is cheap
to build here. That matters because only two of the twelve site builders are
used by any mission today; without these the rest are unexercised.
"""

from __future__ import annotations

import pytest
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.terrain import Caucasus
from dcs.unit import Skill
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import air_defense as ad


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


def test_units_are_placed_around_the_site_centre(
    mission: Mission, site_position: Point
) -> None:
    """Every unit lands within a sane radius, and not all on the same spot."""
    vg = ad.build_sa2_site(mission, mission.country("Russia"), site_position)
    distances = [site_position.distance_to_point(u.position) for u in vg.units]
    assert max(distances) < 200.0, "components should sit close to the centre"
    assert len({(round(u.position.x), round(u.position.y)) for u in vg.units}) == len(
        vg.units
    ), "units must not be stacked on one point"


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


def test_no_warning_when_neither_overlay_nor_terrain_is_given(
    mission: Mission, site_position: Point, capsys: pytest.CaptureFixture[str]
) -> None:
    """Opting out of snapping entirely is a normal, quiet choice."""
    ad.build_sa8_site(mission, mission.country("Russia"), site_position)
    assert "not snapped" not in capsys.readouterr().out
