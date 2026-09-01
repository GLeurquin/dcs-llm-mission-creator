"""`core/join_up` — the package waits for the player instead of leaving without him.

Every AI flight launches at `TriggerStart` by default, which is eight to twelve
minutes before a player who is cold-starting a Viper is airborne. The tests here
are about the *filter*, because that is where this can go quietly wrong: holding
a tanker strands the track the briefing promised, and holding a flight that
spawns airborne drops it out of the sky.

A `Mission(Caucasus())` is cheap and needs neither a DCS install nor the built
map overlay, so these run in the default selection.
"""

from __future__ import annotations

from dcs import task
from dcs.mission import Mission, StartType
from dcs.planes import A_10C, E_3A, F_16C_50, KC_135
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.unit import Skill

from dcs_mission_creator.core import join_up
from dcs_mission_creator.core.tasking import scramble_on_trigger


def _mission() -> tuple[Mission, object]:
    m = Mission(Caucasus())
    return m, m.terrain.airport_by_id(12)  # Batumi


def _player(m: Mission, airport, *, name: str = "Dodge", size: int = 1):
    group = m.flight_group_from_airport(
        country=m.country("USA"),
        name=name,
        aircraft_type=F_16C_50,
        airport=airport,
        maintask=task.CAP,
        start_type=StartType.Warm,
        group_size=size,
    )
    for unit in group.units:
        unit.skill = Skill.Client
    return group


def _hog(m: Mission, airport, *, name: str = "Hawg"):
    return m.flight_group_from_airport(
        country=m.country("USA"),
        name=name,
        aircraft_type=A_10C,
        airport=airport,
        maintask=task.CAS,
        start_type=StartType.Warm,
        group_size=2,
    )


def _pushed_group_ids(m: Mission) -> set[int]:
    """Every group some trigger pushes an AI task onto."""
    return {
        action.groupid
        for trig in m.triggerrules.triggers
        for action in trig.actions
        if hasattr(action, "groupid")
    }


def test_a_field_departing_ai_flight_is_held() -> None:
    m, airport = _mission()
    _player(m, airport)
    hog = _hog(m, airport)

    assert join_up.hold_package_for_player(m) == 1
    assert hog.uncontrolled
    assert any(isinstance(t, task.StartCommand) for t in hog.tasks)
    assert hog.id in _pushed_group_ids(m)


def test_the_release_is_any_player_airborne_or_the_fallback() -> None:
    m, airport = _mission()
    player = _player(m, airport, size=2)
    _hog(m, airport)

    join_up.hold_package_for_player(m, agl_m=50.0, fallback_s=600)
    rules = m.triggerrules.triggers[0].rules
    altitude = [r for r in rules if r.predicate == "c_unit_altitude_higher_AGL"]
    assert [r.unit for r in altitude] == [u.id for u in player.units]
    assert all(r.altitude == 50.0 for r in altitude)
    assert [r.seconds for r in rules if r.predicate == "c_time_after"] == [600]
    # pydcs ANDs a rule list, so the alternatives need an `or` between each pair.
    assert sum(r.predicate == "or" for r in rules) == len(altitude)


def test_the_player_flight_is_not_held() -> None:
    m, airport = _mission()
    player = _player(m, airport)

    assert join_up.hold_package_for_player(m) == 0
    assert not player.uncontrolled


def test_station_holders_launch_at_mission_start() -> None:
    """An AWACS, a tanker and a CAP have somewhere to *be* before the player needs it.

    The CAP is the one worth a test rather than a comment: `eastern_shield`'s
    Eagle needs 21 minutes to reach its station against a player who is over the
    target at 9, so holding it would leave the whole ingress uncovered.
    """
    m, airport = _mission()
    _player(m, airport)
    eagle = m.flight_group_from_airport(
        country=m.country("USA"),
        name="Eagle",
        aircraft_type=F_16C_50,
        airport=airport,
        maintask=task.CAP,
        start_type=StartType.Warm,
        group_size=2,
    )
    magic = m.awacs_flight(
        m.country("USA"),
        "Magic",
        plane_type=E_3A,
        airport=airport,
        position=airport.position,
        race_distance=80_000,
        heading=0,
        altitude=9_000,
        speed=740,
        start_type=StartType.Warm,
    )
    texaco = m.refuel_flight(
        m.country("USA"),
        "Texaco",
        plane_type=KC_135,
        airport=airport,
        position=airport.position,
        race_distance=60_000,
        heading=0,
        altitude=6_500,
        speed=750,
        start_type=StartType.Warm,
    )

    assert join_up.hold_package_for_player(m) == 0
    assert not magic.uncontrolled
    assert not texaco.uncontrolled
    assert not eagle.uncontrolled


def test_a_flight_that_spawns_airborne_is_not_held() -> None:
    """An uncontrolled aircraft in the air does not start up, it falls."""
    m, airport = _mission()
    _player(m, airport)
    hammer = m.flight_group(
        country=m.country("USA"),
        name="Hammer",
        aircraft_type=A_10C,
        airport=None,
        position=airport.position,
        altitude=5_000,
        speed=500,
        maintask=task.AFAC,
        group_size=1,
    )

    assert join_up.hold_package_for_player(m) == 0
    assert not hammer.uncontrolled


def test_the_enemy_is_left_alone() -> None:
    m, airport = _mission()
    _player(m, airport)
    airport.set_blue()
    krymsk = m.terrain.airport_by_id(15)
    krymsk.set_red()
    boris = m.flight_group_from_airport(
        country=m.country("Russia"),
        name="Boris",
        aircraft_type=A_10C,
        airport=krymsk,
        maintask=task.CAP,
        start_type=StartType.Warm,
        group_size=2,
    )

    assert join_up.hold_package_for_player(m) == 0
    assert not boris.uncontrolled


def test_a_flight_the_mission_already_holds_is_left_alone() -> None:
    """`scramble_on_trigger` owns its own release; two pushes would race."""
    m, airport = _mission()
    _player(m, airport)
    alert = _hog(m, airport, name="Alert")
    scramble_on_trigger(m, alert)

    assert join_up.hold_package_for_player(m) == 0
    assert len(m.triggerrules.triggers) == 1


def test_launch_immediately_opts_a_flight_out() -> None:
    m, airport = _mission()
    _player(m, airport)
    hog = join_up.launch_immediately(_hog(m, airport))

    assert join_up.hold_package_for_player(m) == 0
    assert not hog.uncontrolled


def test_a_mission_with_no_player_slots_launches_everything() -> None:
    """Holding a package for a player who does not exist strands it forever."""
    m, airport = _mission()
    hog = _hog(m, airport)

    assert join_up.hold_package_for_player(m) == 0
    assert not hog.uncontrolled
