"""`core/audit` — the mechanical half of the mission rules, checked mechanically.

Every check here exists because the project shipped the mistake it looks for:
knots in a km/h argument, an enemy group left visible on the F10 map, a flight
with empty pylons, a waypoint inside a mountain. The tests build tiny missions
by hand rather than a real one, so each states the defect in a couple of lines
and needs neither the map overlay nor a DCS install.

Two of them are about the audit's own false positives, and those are worth as
much as the rest: an audit that reports the approach gate on every flight in the
project, or a wave-top run over the sea as flying underground, gets read once
and then ignored.
"""

from __future__ import annotations

from dcs import condition, statics, triggers
from dcs.mapping import Point
from dcs.mission import Mission
from dcs.planes import E_3A, F_16C_50
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.unit import Skill

from dcs_mission_creator.core import audit as audit_mod
from dcs_mission_creator.core.audit import Finding, audit_mission, report

TERRAIN = Caucasus()


class _Bounds:
    bottom, top, left, right = -1e9, 1e9, -1e9, 1e9


class _Manifest:
    bounds = _Bounds()


class _Overlay:
    """`ground_elevation_m` asks for these three things and nothing else."""

    theater = "Caucasus"
    manifest = _Manifest()

    def __init__(self, height: float = 0.0) -> None:
        self.height = height

    def elevation_at(self, position: Point) -> float:
        return self.height


def mission() -> Mission:
    return Mission(terrain=TERRAIN)


def flight(m: Mission, name: str, *, client: bool, country: str = "USA"):
    """A two-point flight in the air, with no airfield events to filter out."""
    group = m.flight_group_inflight(
        m.country(country),
        name,
        F_16C_50,
        position=Point(0.0, 0.0, TERRAIN),
        altitude=5_000,
        speed=800,
    )
    for unit in group.units:
        unit.skill = Skill.Client if client else Skill.Excellent
    return group


def only(findings, check: str) -> list[Finding]:
    return [f for f in findings if f.check == check]


# -- speeds ------------------------------------------------------------------


def test_a_knots_shaped_speed_on_a_fast_jet_is_an_error():
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.points[0].speed = 400 / 3.6  # 400 "knots", read as km/h
    findings = only(audit_mission(m, _Overlay()), "speed")
    assert findings and findings[0].severity == "error"
    assert "knots-shaped" in findings[0].message


def test_a_sane_cruise_is_silent():
    m = mission()
    flight(m, "Colt", client=True)  # built at 800 km/h — 0.38 of an F-16C
    assert not only(audit_mission(m, _Overlay()), "speed")


def test_afterburner_speeds_are_a_warning_not_an_error():
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.points[0].speed = 1_200 / 3.6
    findings = only(audit_mission(m, _Overlay()), "speed")
    assert findings and findings[0].severity == "warn"


def test_a_subsonic_type_is_exempt_from_the_band():
    """An E-3A cruises at 0.86 of its max_speed and has no afterburner."""
    m = mission()
    m.flight_group_inflight(
        m.country("USA"),
        "Magic",
        E_3A,
        position=Point(0.0, 0.0, TERRAIN),
        altitude=9_000,
        speed=740,
    )
    assert not only(audit_mission(m, _Overlay()), "speed")


def test_the_approach_gate_is_not_reported_as_a_unit_error():
    """The loudest false positive available: every flight in the project has one."""
    m = mission()
    group = m.flight_group_from_airport(
        m.country("USA"), "Colt", F_16C_50, TERRAIN.airports["Senaki-Kolkhi"]
    )
    for unit in group.units:
        unit.skill = Skill.Client
    group.add_runway_waypoint(TERRAIN.airports["Senaki-Kolkhi"])
    group.land_at(TERRAIN.airports["Senaki-Kolkhi"])
    speeds = only(audit_mission(m, _Overlay()), "speed")
    assert not speeds, [str(f) for f in speeds]


# -- route against the terrain ----------------------------------------------


def test_a_waypoint_inside_a_mountain_is_an_error():
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.points[0].alt = 800  # against 2,600 m of rock
    findings = only(audit_mission(m, _Overlay(2_600.0)), "route")
    assert findings and findings[0].severity == "error"


def test_a_leg_through_a_ridge_is_found_even_when_both_ends_clear():
    """The half a per-waypoint check misses, and the one `daryal_run` shipped."""
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.add_waypoint(Point(60_000.0, 0.0, TERRAIN), altitude=5_000, speed=800)
    high = _Overlay(6_000.0)
    assert only(audit_mission(m, high), "route")


def test_a_wave_top_run_over_water_is_not_underground():
    """The sea reads below datum, and sixty metres over it is a deliberate deck run."""
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.points[0].alt = 60
    assert not only(audit_mission(m, _Overlay(-40.0)), "route")


def test_an_ai_route_is_not_checked_against_the_terrain():
    """DCS flies an AI flight round the rock; a player follows his steerpoints."""
    m = mission()
    hawg = flight(m, "Hawg", client=False)
    hawg.points[0].alt = 800
    assert not only(audit_mission(m, _Overlay(2_600.0)), "route")


# -- the enemy on the map ----------------------------------------------------


def test_a_visible_enemy_group_is_reported():
    m = mission()
    flight(m, "Colt", client=True)
    m.vehicle_group(
        m.country("Russia"),
        "SAM",
        __import__("dcs.vehicles", fromlist=["AirDefence"]).AirDefence.ZSU_23_4_Shilka,
        position=Point(1_000.0, 1_000.0, TERRAIN),
    )
    findings = only(audit_mission(m, _Overlay()), "concealment")
    assert findings and "F10 map" in findings[0].message


def test_a_concealed_enemy_group_is_silent():
    from dcs_mission_creator.core.visibility import conceal_country

    m = mission()
    flight(m, "Colt", client=True)
    m.vehicle_group(
        m.country("Russia"),
        "SAM",
        __import__("dcs.vehicles", fromlist=["AirDefence"]).AirDefence.ZSU_23_4_Shilka,
        position=Point(1_000.0, 1_000.0, TERRAIN),
    )
    conceal_country(m.country("Russia"))
    assert not only(audit_mission(m, _Overlay()), "concealment")


def test_our_own_side_is_never_reported_as_visible():
    m = mission()
    flight(m, "Colt", client=True)
    flight(m, "Hawg", client=False)
    assert not only(audit_mission(m, _Overlay()), "concealment")


# -- armament ----------------------------------------------------------------


def test_a_flight_with_empty_pylons_is_a_warning():
    m = mission()
    flight(m, "Colt", client=True)
    findings = only(audit_mission(m, _Overlay()), "armament")
    assert findings and findings[0].severity == "warn"


def test_an_armed_flight_is_silent():
    from dcs_mission_creator.core.mission_kit import arm

    m = mission()
    colt = flight(m, "Colt", client=True)
    arm(colt, F_16C_50, [(1, "AIM_120C_AMRAAM___Active_Radar_AAM")])
    assert not only(audit_mission(m, _Overlay()), "armament")


# -- the laser code ----------------------------------------------------------

_GBU_12 = "TER_9A_with_2_x_GBU_12___500lb_Laser_Guided_Bomb"


def test_a_laser_weapon_with_no_stated_code_is_a_warning():
    """The one thing the `.miz` cannot answer, so the audit has to.

    An F-16C carries no laser-code property, so a mission that never called
    `laser.set_code` writes a file identical to one that did — and from the
    cockpit a bomb tracking a spot on the wrong code looks exactly like a bomb
    that failed to guide.
    """
    from dcs_mission_creator.core.mission_kit import arm

    m = mission()
    colt = flight(m, "Colt", client=True)
    arm(colt, F_16C_50, [(3, _GBU_12)])
    findings = only(audit_mission(m, _Overlay()), "laser")
    assert findings and findings[0].severity == "warn"


def test_a_stated_laser_code_is_silent():
    from dcs_mission_creator.core import laser
    from dcs_mission_creator.core.mission_kit import arm

    m = mission()
    colt = flight(m, "Colt", client=True)
    arm(colt, F_16C_50, [(3, _GBU_12)])
    laser.set_code(colt, laser.DEFAULT_CODE)
    assert not only(audit_mission(m, _Overlay()), "laser")


def test_a_flight_with_no_laser_weapon_needs_no_code():
    from dcs_mission_creator.core.mission_kit import arm

    m = mission()
    colt = flight(m, "Colt", client=True)
    arm(colt, F_16C_50, [(1, "AIM_120C_AMRAAM___Active_Radar_AAM")])
    assert not only(audit_mission(m, _Overlay()), "laser")


# -- the cartridge -----------------------------------------------------------


def test_a_route_over_the_navigation_tab_is_a_warning():
    from dcs_mission_creator.core import dtc

    m = mission()
    colt = flight(m, "Colt", client=True)
    for i in range(dtc.MAX_NAV_POINTS + 2):
        colt.add_waypoint(Point(float(i) * 1_000, 0.0, TERRAIN), 5_000, 800)
    findings = only(audit_mission(m, _Overlay()), "cartridge")
    assert findings and findings[0].severity == "warn"


def test_a_short_route_leaves_the_tab_alone():
    m = mission()
    flight(m, "Colt", client=True)
    assert not only(audit_mission(m, _Overlay()), "cartridge")


# -- reporting ---------------------------------------------------------------


def test_report_puts_errors_before_warnings_before_notes():
    findings = [
        Finding("a", "note", "n"),
        Finding("b", "error", "e"),
        Finding("c", "warn", "w"),
    ]
    lines = report(findings).splitlines()
    assert lines[0].startswith("ERROR")
    assert lines[1].startswith("WARN")
    assert lines[2].startswith("NOTE")


def test_report_says_so_when_there_is_nothing_to_say():
    assert report([]) == "no findings"


def test_a_finding_renders_its_location():
    assert "[Colt PUSH]" in str(Finding("route", "error", "boom", "Colt PUSH"))


# -- the altitude datum ------------------------------------------------------


def test_radio_altitudes_are_read_as_above_the_ground():
    """pydcs writes the runway gates `alt_type="RADIO"`; 300 there is 300 AGL."""
    m = mission()
    colt = flight(m, "Colt", client=True)
    colt.add_waypoint(Point(20_000.0, 0.0, TERRAIN), altitude=4_000, speed=800)
    gate, baro = colt.points[0], colt.points[1]
    gate.alt, gate.alt_type = 300, "RADIO"
    assert audit_mod._amsl(gate, _Overlay(2_000.0)) == 2_300.0
    assert audit_mod._amsl(baro, _Overlay(2_000.0)) == float(baro.alt)


# -- target waypoints --------------------------------------------------------

#: Well away from the origin, where `flight()` spawns: the spawn point is a
#: steerpoint like any other, and a building sited on it would be "found" by a
#: route that never planned for it.
_AO = Point(50_000.0, 0.0, TERRAIN)


def _objective_building(m: Mission, name: str, north_m: float):
    """A static the triggers score, `north_m` up the AO from `_AO`.

    The mission does not declare which building is the objective; the audit
    reads that out of the trigger, the way a mission really writes one — a
    static is not a group in the scripting sense, so "destroy it" is
    `UnitDead` on `group.units[0].id`.
    """
    hall = m.static_group(
        m.country("Russia"),
        name,
        statics.Fortification.Workshop_A,
        position=Point(_AO.x + north_m, _AO.y, TERRAIN),
    )
    rule = triggers.TriggerOnce(comment=f"{name} down")
    rule.add_condition(condition.UnitDead(hall.units[0].id))
    m.triggerrules.triggers.append(rule)
    return hall


def _bomber(m: Mission, aim_north_m: float):
    """A client flight with one steerpoint, `aim_north_m` up the AO."""
    colt = flight(m, "Colt", client=True)
    colt.add_waypoint(
        Point(_AO.x + aim_north_m, _AO.y, TERRAIN),
        altitude=5_000,
        speed=800,
        name="TGT",
    )
    return colt


def test_a_building_objective_the_route_only_approximates_is_a_warning():
    """`ansariyah_works` briefed a surveyed aimpoint and flew a 2 km estimate."""
    m = mission()
    _bomber(m, aim_north_m=2_000.0)
    _objective_building(m, "casting hall", north_m=0.0)
    findings = only(audit_mission(m, _Overlay()), "target waypoint")
    assert findings and findings[0].severity == "warn"
    assert "2,000 m from this building" in findings[0].message


def test_a_steerpoint_on_the_building_is_silent():
    m = mission()
    _bomber(m, aim_north_m=0.0)
    _objective_building(m, "casting hall", north_m=0.0)
    assert not only(audit_mission(m, _Overlay()), "target waypoint")


def test_one_steerpoint_cannot_answer_for_two_aimpoints():
    """`kuban_forge`'s halls are 220 m apart; a point between them hits neither."""
    m = mission()
    _bomber(m, aim_north_m=110.0)
    _objective_building(m, "casting hall A", north_m=0.0)
    _objective_building(m, "casting hall B", north_m=220.0)
    findings = only(audit_mission(m, _Overlay()), "target waypoint")
    assert len(findings) == 2
    assert {"Colt \u2192 casting hall A", "Colt \u2192 casting hall B"} == {
        finding.where for finding in findings
    }


def test_a_building_in_no_trigger_is_not_an_objective():
    """The compound around the frag — tanks, a crane, a garage — is scenery."""
    m = mission()
    _bomber(m, aim_north_m=0.0)
    m.static_group(
        m.country("Russia"),
        "oxidiser tank",
        statics.Fortification.Chemical_tank_A,
        position=Point(_AO.x + 9_000.0, _AO.y, TERRAIN),
    )
    assert not only(audit_mission(m, _Overlay()), "target waypoint")
