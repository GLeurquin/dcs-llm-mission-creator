"""Check a built mission against the rules it was written under (project-owned).

Most of what CLAUDE.md asks of a mission is mechanical and none of it was
mechanised: speeds in km/h that the airframe can hold, waypoints that are not
inside a mountain, no enemy group left visible on the F10 map, a cartridge that
is not oversubscribed, a flight that is actually carrying something. Every one
of those has been shipped wrong at least once here, and every one is a
comparison a machine can make.

The reason it was not being made is cost: the only way to look at a finished
mission was to `generate` it, which renders text-to-speech, writes a
five-megabyte archive and draws kneeboard pages — the better part of a minute,
none of it about whether the aeroplane fits. `MissionBuilder.assemble` splits
the build at the save, so this runs against exactly the mission that would have
shipped, in a second or two, without touching the disk.

    from dcs_mission_creator.core.audit import audit

    for finding in audit(KubanForge(players=2)):
        print(finding)

Findings, not failures. Several checks here are heuristics about *design* — a
speed that looks like afterburner, a magazine that looks short — and a mission
is allowed to be deliberate about any of them. What the audit is for is that
the deliberate cases should be the only ones left, and at the moment nobody can
tell which is which without reading the whole file.

**The speed check is here because the repo shipped every one of these.** Every
pydcs speed argument is km/h true airspeed, stored as `speed / 3.6` m/s; none
says so in its signature and none validates the number, so a knots-shaped value
is accepted in silence and commands about 54 % of what was meant. Every mission
shipped that way — the `patrol_flight` / `awacs_flight` / `refuel_flight` /
`intercept_flight` calls held knots-shaped numbers (380-490) while the
hand-built `add_waypoint` routes in the same files held km/h-shaped ones
(750-850), so the repo disagreed with itself and the whole friendly package was
ordered to hold 137-167 KIAS at FL210-FL295:

    flight                       was    commanded            should be
    E-3A orbit @9000 m           410    221 kt TAS/137 KIAS  740
    KC-135 track @6500 m         407    220 kt TAS/157 KIAS  750
    F-15C CAP @8000 m            430    232 kt TAS/152 KIAS  800
    MiG-29S intercept @7500 m    440    238 kt TAS/160 KIAS  900
    A-10C ingress @4600 m        400    216 kt TAS/171 KIAS  520

The symptom is the package flying its whole sortie in afterburner: at those
speeds a fighter is far below best-climb speed and deep on the back side of the
drag curve, and the AI holds the commanded altitude on the throttle.

The floor and the ceiling below therefore catch two different mistakes. Under
`SLOW_RATIO` is the unit error. Over `FAST_RATIO` is a number that is a sane
cruise for a *different* jet — `idlib_gauntlet`'s Hornet kept the 800/850 km/h
that reads as 0.38/0.40 on the F-16C beside it and is 0.41/0.44 on an F/A-18C,
the slowest fast jet in the fleet. Include the sub-300 km/h waypoints in any
manual audit too: the first pass filtered them out as noise and walked straight
past pydcs's hard-coded 200 km/h departure gate on all thirty flights, which was
a worse bug than the cruise numbers it was looking for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

import structlog
from dcs.mission import Mission

from dcs_mission_creator.core import (
    dcs_install,
    dtc,
    laser,
    loadout,
    loadout_check,
    mission_kit,
    visibility,
    waypoints,
)

if TYPE_CHECKING:
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.core.mission_builder import MissionBuilder
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: Cruise and orbit speeds as a fraction of the airframe's own `max_speed`.
#: Below the floor is the knots-for-km/h mistake — every `patrol_flight` call in
#: this project once held one, commanding about 54 % of the intended speed and
#: putting the whole package in afterburner at 150 KIAS. Above the ceiling the
#: jet is holding the profile in burner by definition.
SLOW_RATIO = 0.20
FAST_RATIO = 0.42

#: Airframes whose `max_speed` is barely above their cruise — an E-3A sits at
#: 0.86 of it and an A-10C at 0.72, and neither has an afterburner to worry
#: about. The band above only means anything for something supersonic.
SUPERSONIC_KPH = 1_500.0

#: How far above the ground a *built* route has to stay before this complains,
#: and it is deliberately far below `clear_terrain`'s 150 m planning margin. A
#: mission is allowed to fly lower than a planner would plan — `ansariyah_works`
#: crosses 250 km of sea at sixty metres, and that hard deck is a number out of
#: the S-200's own weapon table rather than a mistake. What a mission may not do
#: is fly through the ground, and that is what this catches: `daryal_run` once
#: shipped a waypoint 2.7 km inside a mountainside.
ROUTE_FLOOR_M = 30.0

#: The lowest ground a route can be measured against. The elevation layer holds
#: depth below datum out at sea, so without this every over-water waypoint in
#: `ansariyah_works` — a mission that crosses 250 km of it at sixty metres —
#: reads as flying underground.
SEA_LEVEL_M = 0.0


@dataclass(frozen=True)
class Finding:
    """One thing worth looking at, and where."""

    check: str
    severity: str  # "error" | "warn" | "note"
    message: str
    where: str = ""

    def __str__(self) -> str:
        place = f" [{self.where}]" if self.where else ""
        return f"{self.severity.upper():5s} {self.check}{place}: {self.message}"


def audit(builder: MissionBuilder) -> list[Finding]:
    """Build `builder` without saving it and report everything that looks wrong."""
    mission, overlay = builder.assemble()
    return audit_mission(mission, overlay)


def audit_mission(m: Mission, overlay: MapOverlay) -> list[Finding]:
    """The checks themselves, against an already-built mission."""
    findings: list[Finding] = []
    for check in (
        _check_speeds,
        _check_departure,
        _check_base_waypoints,
        _check_route_terrain,
        _check_cartridge,
        _check_concealment,
        _check_armament,
        _check_laser_code,
        _check_stations,
        _check_magazine,
    ):
        findings += list(check(m, overlay))
    return findings


# -- flights -----------------------------------------------------------------


def _profile_points(group: FlyingGroup) -> list:
    """The waypoints that are a flight profile rather than an airfield event.

    The take-off and landing points carry the field's elevation, and neither is
    a defect — so every check about an altitude has to drop them first or it
    reports the same two non-problems on every flight in the project.
    """
    return [p for p in group.points if p.type not in waypoints.BASE_POINT_TYPES]


def _runway_gates(group: FlyingGroup) -> set[int]:
    """Indices of the two points `add_runway_waypoint` writes, in `points` terms.

    They are ordinary turning points by type, so nothing about the waypoint
    itself says what they are — what identifies them is that each sits directly
    beside a take-off or landing point. Both matter to a speed check and for
    opposite reasons: pydcs writes **both** at 108 kt, `set_departure_speeds`
    corrects the departure one because that number is below a loaded jet's stall
    speed, and the approach one is deliberately left alone because by then the
    jet is light and that is roughly its real approach speed. Flagging the
    approach gate as a unit error is the single loudest false positive this
    audit can produce — every flight in the project has one.
    """
    base = [
        i for i, p in enumerate(group.points) if p.type in waypoints.BASE_POINT_TYPES
    ]
    gates = set()
    for i in base:
        for neighbour in (i - 1, i + 1):
            if 0 <= neighbour < len(group.points) and neighbour not in base:
                gates.add(neighbour)
    return gates


def _amsl(point, overlay: MapOverlay) -> float:
    """A waypoint's altitude in metres above **sea level**, whatever it is stored as.

    pydcs writes most altitudes as `alt_type="BARO"` (AMSL) and the two runway
    gates as `"RADIO"` (above the ground), and every check here compares against
    the elevation raster, which is AMSL. Reading the field without the type is
    the mistake that made this audit's first run report a 300 m gate at Vaziani
    as 133 m of solid rock: it is 300 m over the field, and it is the flight
    profile pydcs intends.
    """
    if point.alt_type == "RADIO":
        return waypoints.ground_elevation_m(overlay, point.position) + point.alt
    return float(point.alt)


def _departure_gate(group: FlyingGroup) -> int | None:
    """The runway gate *after* the take-off point — the one that has to be fast."""
    for i, point in enumerate(group.points):
        if point.type in waypoints.BASE_POINT_TYPES and point.type != "Land":
            gate = i + 1
            if gate < len(group.points):
                return gate
    return None


# -- checks ------------------------------------------------------------------


def _check_speeds(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Every commanded speed against what the airframe can hold."""
    for _side, group in mission_kit.flying_groups_by_side(m):
        top = float(group.units[0].unit_type.max_speed)
        if top < SUPERSONIC_KPH:
            continue
        gates = _runway_gates(group)
        for i, point in enumerate(group.points):
            if point.type in waypoints.BASE_POINT_TYPES or i in gates:
                continue
            kph = point.speed * 3.6
            ratio = kph / top
            if ratio < SLOW_RATIO:
                yield Finding(
                    "speed",
                    "error",
                    f"{kph:.0f} km/h is {ratio:.2f} of max_speed — "
                    "a knots-shaped number in a km/h argument?",
                    f"{group.name} {point.name or point.type}",
                )
            elif ratio > FAST_RATIO:
                yield Finding(
                    "speed",
                    "warn",
                    f"{kph:.0f} km/h is {ratio:.2f} of max_speed — "
                    "the flight holds this in afterburner",
                    f"{group.name} {point.name or point.type}",
                )


def _check_departure(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """The first point after rotation, which pydcs writes at 108 kt."""
    for _side, group in mission_kit.flying_groups_by_side(m):
        gate = _departure_gate(group)
        if gate is None:
            continue
        top = float(group.units[0].unit_type.max_speed)
        kph = group.points[gate].speed * 3.6
        if kph and kph / top < SLOW_RATIO:
            yield Finding(
                "departure",
                "error",
                f"climb-out commanded at {kph:.0f} km/h "
                f"({kph / top:.2f} of max_speed) — "
                "waypoints.set_departure_speeds did not reach this flight",
                group.name,
            )


def _check_base_waypoints(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Take-off and landing points sitting on the field rather than under it."""
    for _side, group in mission_kit.flying_groups_by_side(m):
        for point in group.points:
            if point.type not in waypoints.BASE_POINT_TYPES:
                continue
            ground = waypoints.ground_elevation_m(overlay, point.position)
            if abs(_amsl(point, overlay) - ground) > 1.0:
                yield Finding(
                    "base waypoint",
                    "error",
                    f"altitude {point.alt:.0f} m against {ground:.0f} m of terrain — "
                    "snap_base_waypoints did not reach this flight",
                    f"{group.name} {point.type}",
                )


def _check_route_terrain(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Client routes that go through the ground, at a point or along a leg.

    Client flights only. An AI flight's route altitudes are advisory — DCS flies
    it round the terrain — while a player follows the steerpoints he is given,
    and a ground-target steerpoint is deliberately *on* the surface, so those
    are skipped rather than reported as a route that hits it.
    """
    for _side, group in mission_kit.flying_groups_by_side(m):
        if not mission_kit.is_client(group):
            continue
        profile = _profile_points(group)
        # Sea level is the floor, not the raster. Over water the elevation layer
        # holds the depth below datum, so a wave-top leg reads as flying tens of
        # metres *underground* if the number is taken at face value — which is
        # every over-water waypoint in `ansariyah_works`.
        heights = [
            max(SEA_LEVEL_M, waypoints.ground_elevation_m(overlay, p.position))
            for p in profile
        ]
        flown = [_amsl(p, overlay) for p in profile]
        on_ground = [abs(a - g) <= 1.0 for a, g in zip(flown, heights)]
        for point, ground, alt, flat in zip(profile, heights, flown, on_ground):
            if not flat and alt < ground + ROUTE_FLOOR_M:
                yield Finding(
                    "route",
                    "error",
                    f"{alt:.0f} m over {ground:.0f} m of terrain",
                    f"{group.name} {point.name or point.type}",
                )
        for i in range(len(profile) - 1):
            if on_ground[i] or on_ground[i + 1]:
                continue  # a leg to a target steerpoint is a dive, not a defect
            short, _where = waypoints.leg_violation(
                profile[i].position,
                profile[i + 1].position,
                flown[i],
                flown[i + 1],
                overlay,
                clearance_m=ROUTE_FLOOR_M,
                floor_m=SEA_LEVEL_M,
            )
            if short > 0.0:
                yield Finding(
                    "route",
                    "error",
                    f"the leg cuts {short:.0f} m into terrain between waypoints",
                    f"{group.name} "
                    f"{profile[i].name or i} → {profile[i + 1].name or i + 1}",
                )


def _check_cartridge(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Whether the F10 plan will still fit in the cockpit beside the route."""
    for _side, group in mission_kit.flying_groups_by_side(m):
        if not mission_kit.is_client(group):
            continue
        spare = dtc.nav_headroom(len(group.points))
        if spare < 0:
            yield Finding(
                "cartridge",
                "warn",
                f"{len(group.points)} route points against "
                f"{dtc.MAX_NAV_POINTS} navigation steerpoints — "
                "the route itself is over the tab",
                group.name,
            )
        elif spare <= 2:
            yield Finding(
                "cartridge",
                "note",
                f"{spare} navigation steerpoint(s) left for the plan's marks "
                f"after {len(group.points)} route points",
                group.name,
            )


def _check_concealment(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Enemy groups still showing as unit icons on the F10 map.

    "Enemy" is derived rather than assumed: it is whichever coalition the client
    slots are not on, so a mission that flies red needs no special case.
    """
    ours = {
        side
        for side, group in mission_kit.flying_groups_by_side(m)
        if mission_kit.is_client(group)
    }
    if not ours:
        return
    for side, coalition in m.coalition.items():
        if side in ours:
            continue
        for country in coalition.countries.values():
            for group in visibility.groups_of(country):
                if not getattr(group, "hidden", False):
                    yield Finding(
                        "concealment",
                        "warn",
                        "visible on the F10 map — conceal_country missed it",
                        f"{country.name} {group.name}",
                    )


def _check_armament(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Flights that launch with nothing on the rails.

    The failure mode this is for is silent: pydcs fills pylons from the
    *installed game*, so without `DCS_INSTALL_DIR` a task default comes back
    empty and five of the six missions here once shipped clean jets under
    briefings naming specific stores.
    """
    for _side, group in mission_kit.flying_groups_by_side(m):
        if any(unit.pylons for unit in group.units):
            continue
        if not group.units[0].unit_type.pylons:
            continue  # an E-3A or a tanker has nowhere to hang anything
        yield Finding("armament", "warn", "every pylon is empty", group.name)


def _check_laser_code(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """A flight carrying a laser-guided weapon whose code nobody stated.

    Not a question the `.miz` can answer, which is why it is asked here. The
    F-16C carries no laser-code property, so `laser.set_code` writes nothing and
    a mission that never called it looks identical to one that did — right up to
    the cockpit, where a bomb tracking a spot on another code is
    indistinguishable from a bomb that failed to guide, and where the pilot's
    one recourse (retune the pod) is the half that was never wrong.

    `kodori_strike` flew four GBU-12 and briefed them for months with no call.
    It was harmless, because the default is what both ends come up on anyway —
    but that is luck rather than a check, and it is exactly the claim
    `core/laser.py` exists to make hold.
    """
    for _side, group in mission_kit.flying_groups_by_side(m):
        stores = laser.laser_guided_stores(group)
        if not stores or laser.stated_code(group) is not None:
            continue
        yield Finding(
            "laser",
            "warn",
            f"carries {', '.join(stores)} and no laser.set_code call — "
            f"the briefed code is whatever the cockpit comes up on",
            group.name,
        )


def _check_stations(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """Stores on stations the game itself does not use for them."""
    if dcs_install.install_dir() is None:
        yield Finding(
            "stations",
            "note",
            f"no {dcs_install.INSTALL_ENV}: cannot check stations against "
            "the game's own payload tables",
        )
        return
    for _side, group in mission_kit.flying_groups_by_side(m):
        for note in loadout_check.check_group(group):
            yield Finding(
                "stations",
                "note",
                f"{note.store}: {note.message}",
                f"{group.name} station {note.pylon}",
            )


def _check_magazine(m: Mission, overlay: MapOverlay) -> Iterable[Finding]:
    """What the player flight is carrying, against what the mission tasks.

    Reported rather than judged: the force-balance rule divides the magazine by
    two for a kill budget, but only the mission knows how many of the aircraft
    it puts up are a *tasked* kill and how many are a threat to survive. Putting
    the number on the page is what lets that be checked by eye in one line
    instead of by reading two spawn helpers.
    """
    ours = {
        side
        for side, group in mission_kit.flying_groups_by_side(m)
        if mission_kit.is_client(group)
    }
    bandits = sum(
        len(group.units)
        for side, group in mission_kit.flying_groups_by_side(m)
        if side not in ours
    )
    for flight, assignment in loadout.assignments(m):
        shots = loadout.shots(assignment)
        yield Finding(
            "magazine",
            "note",
            f"{shots} air-to-air shot(s) across {len(assignment)} slot(s) "
            f"≈ {shots // 2} kill(s) against {bandits} enemy aircraft airborne "
            "or on alert",
            flight,
        )


def report(findings: Sequence[Finding]) -> str:
    """The findings as text, worst first."""
    order = {"error": 0, "warn": 1, "note": 2}
    if not findings:
        return "no findings"
    ranked = sorted(findings, key=lambda f: (order.get(f.severity, 3), f.check))
    return "\n".join(str(f) for f in ranked)
