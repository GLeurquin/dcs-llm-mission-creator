"""Hold the package on the ground until the player is off it.

Every AI flight in these missions launches at `TriggerStart`, which is what
pydcs and the ME both do by default, and it is wrong for the one flight the
player is supposed to *fly with*. A cold or warm Viper is eight to twelve
minutes of alignment, INS, DTE, checklist and taxi; an AI A-10 pair at the same
field is rolling in ninety seconds and is a hundred kilometres down the route by
the time the player rotates. The briefing says "escort Hawg to the AO" and the
player spends the sortie chasing a flight he was never in front of. Nothing in
the route can fix that — the AI is flying its plan correctly, it just started
without him.

So the package waits. Every friendly flight that departs from a field is set
uncontrolled with a queued `StartCommand` (the ME's "Uncontrolled" checkbox),
and one `TriggerOnce` per flight pushes it the moment **any player slot is
airborne**. The AI then starts up, taxis and takes off behind a player who is
already in a holding turn overhead, which is the join-up the briefing describes.

Three things this deliberately does not touch:

- **Anything whose job is a station.** An AWACS, a tanker and a CAP are all
  defined by somewhere they have to *be* rather than somebody they fly with,
  and all three have to be there before the package needs them: an E-3A pushed
  at the player's rotation is thirty minutes from its orbit, a KC-135 not yet on
  the track is worse than no tanker because the briefing promised one, and a
  TARCAP is doctrinally established ahead of the strike it covers. Measured on
  the routes here, that last one is not a nicety — `eastern_shield`'s Eagle
  needs 21 minutes to reach its station and `idlib_gauntlet`'s 14, against a
  player who is over the convoy at 9, so holding them would leave the whole
  ingress uncovered.
- **A flight that spawns airborne.** `Mission.flight_group` with `airport=None`
  is already flying — `idlib_gauntlet`'s Reaper has been over the road since
  before dawn, which is the mission's whole intelligence claim — and an
  uncontrolled aircraft in the air does not start, it falls.
- **A flight the mission already holds.** `tasking.scramble_on_trigger` sets
  `uncontrolled` itself, and a red alert-5 pair released by the player crossing
  a zone must not also be released by the player getting airborne.

`launch_immediately(group)` is the per-flight opt-out for the case the task
name cannot express — a strike the mission wants ahead of the player on purpose.

**Any** player slot rather than all of them, and that is the deliberate choice:
in a six-slot coop, waiting for the last pilot to finish an alignment stalls the
mission behind whoever is slowest, and in single-player "all" and "any" are the
same test. The fallback timer is the other half of that — with no client
airborne at all (a server with nobody slotted, a mission opened to look at) the
package would otherwise sit on the ramp for the whole sortie, so a `TimeAfter`
is OR'd in and the flight launches on its own after `fallback_s`.

Missions never call this — `MissionBuilder.build_miz` does, for the same reason
as `waypoints.snap_base_waypoints`: a flight added after the call site was
written cannot miss it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from dcs import condition
from dcs.unit import Skill

from dcs_mission_creator.core.tasking import scramble_on_trigger

if TYPE_CHECKING:
    from dcs.condition import Condition
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup

log = structlog.get_logger(__name__)

__all__ = ["hold_package_for_player", "launch_immediately", "PLAYER_AIRBORNE_AGL_M"]


#: Height above the runway that counts as "airborne". A parked jet sits a metre
#: or two up and a taxiing one no higher, so anything clear of a bounce works;
#: 50 m fires within a few seconds of rotation without being reachable by a
#: take-off run that is later aborted.
PLAYER_AIRBORNE_AGL_M = 50.0

#: How long the package waits for a player who never gets airborne. Generous on
#: purpose — a cold Viper start plus a careful checklist is fifteen minutes, and
#: firing early only restores the behaviour this module exists to change.
FALLBACK_S = 900

#: Flights whose value is being *there first*: they hold a station rather than
#: fly in the package, and each takes long enough to reach it that launching one
#: with the player is close to not having it. The task name is the mission
#: author's own declaration of which kind of flight this is — `patrol_flight`
#: writes "CAP", a strike or an escort does not — so it is what the split reads.
ON_STATION_TASKS = frozenset({"AWACS", "Refueling", "CAP"})

#: Waypoint types that mean the flight starts on an airfield. Same list as
#: `core/waypoints._BASE_POINT_TYPES` minus "Land": a group that spawns in the
#: air has nothing to be held on.
_GROUND_START_TYPES = frozenset(
    {
        "TakeOff",
        "TakeOffParking",
        "TakeOffParkingHot",
        "TakeOffGround",
        "TakeOffGroundHot",
    }
)

#: Marker set by `launch_immediately`, read by the sweep.
_OPT_OUT = "join_up_launch_immediately"

_CLIENT_SKILLS = frozenset({Skill.Client, Skill.Player})


def launch_immediately(group: FlyingGroup) -> FlyingGroup:
    """Exempt `group` from the hold: it launches at mission start as before.

    For the flight whose head start is the point — something already committed
    when the sortie begins, or an asset the player is meant to arrive behind.
    Returns the group so it can be used inline at the spawn site.
    """
    setattr(group, _OPT_OUT, True)
    return group


def hold_package_for_player(
    m: Mission,
    *,
    agl_m: float = PLAYER_AIRBORNE_AGL_M,
    fallback_s: int = FALLBACK_S,
) -> int:
    """Hold every friendly field-departing AI flight until a player is airborne.

    Walks the whole mission, so it cannot miss a flight added later. Returns the
    number of flights held; zero when the mission has no player slots (there is
    then nothing to join up with, and holding would strand the package).
    """
    clients = _client_units(m)
    if not clients:
        log.debug("no player slots; package launches at mission start")
        return 0
    coalitions = {coalition for coalition, _unit in clients}
    held = 0
    for coalition, group in _flying_groups(m):
        if coalition not in coalitions or not _should_hold(group):
            continue
        scramble_on_trigger(
            m,
            group,
            *_player_airborne(clients, coalition, agl_m=agl_m, fallback_s=fallback_s),
            comment=f"{group.name} launches once a player is airborne",
        )
        held += 1
    log.debug("package held for player join-up", flights=held, fallback_s=fallback_s)
    return held


def _player_airborne(
    clients: list[tuple[str, int]],
    coalition: str,
    *,
    agl_m: float,
    fallback_s: int,
) -> list[Condition]:
    """`any own-side client above `agl_m` AGL` — or the fallback timer.

    pydcs's rule list is ANDed, so the alternatives are separated by
    `condition.Or()` the way `idlib_gauntlet` separates its two seam-crossing
    sections. An unoccupied client slot has no unit in the running mission and
    its condition is simply false, which is what makes "any" work on a
    single-player build of a six-slot mission.
    """
    ours = [unit for side, unit in clients if side == coalition]
    rules: list[Condition] = []
    for unit in ours:
        if rules:
            rules.append(condition.Or())
        rules.append(condition.UnitAltitudeHigherAGL(unit, agl_m))
    rules.append(condition.Or())
    rules.append(condition.TimeAfter(fallback_s))
    return rules


def _should_hold(group: FlyingGroup) -> bool:
    """Whether this flight is one the player is meant to launch ahead of."""
    if any(u.skill in _CLIENT_SKILLS for u in group.units):
        return False  # the player flight itself
    if getattr(group, _OPT_OUT, False):
        return False
    if group.uncontrolled or group.late_activation:
        return False  # the mission already owns when this one shows up
    if group.task in ON_STATION_TASKS:
        return False
    return bool(group.points) and group.points[0].type in _GROUND_START_TYPES


def _client_units(m: Mission) -> list[tuple[str, int]]:
    """Every player slot in the mission as `(coalition, unit id)`."""
    return [
        (coalition, unit.id)
        for coalition, group in _flying_groups(m)
        for unit in group.units
        if unit.skill in _CLIENT_SKILLS
    ]


def _flying_groups(m: Mission) -> list[tuple[str, FlyingGroup]]:
    """Every plane and helicopter group, tagged with the coalition that owns it."""
    return [
        (name, group)
        for name, coalition in m.coalition.items()
        for country in coalition.countries.values()
        for group in (*country.plane_group, *country.helicopter_group)
    ]
