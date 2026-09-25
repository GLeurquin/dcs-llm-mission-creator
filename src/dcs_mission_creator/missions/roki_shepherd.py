"""Caucasus 'Roki Shepherd' — F-16C quick-reaction air policing over Georgia.

Player flies a USAF F-16C-50 pair on quick-reaction alert out of Vaziani as
`Uzi`. Russian aircraft out of Beslan, Mozdok and Nalchik are probing Georgian
airspace through the Greater Caucasus passes — Roki, Daryal, Mamison — with no
flight plan and no transponder. The frag is not to shoot them down: it is to
**intercept each track, shadow it, and see it back north across the line**.

The mission is the three things a war-footing DCS mission cannot express, and
all three are `core/intercept.py`: an aircraft that is a coalition enemy but
not yet a target; "intercepted" as a formation state held for long enough to be
seen; and a crew that complies and then, on a roll made in the air rather than
in this file, may turn in on the escort. A track the escort drifts away from
turns back to its orbit and has to be caught again, and the second intercept
rolls again.

`Eagle`, an F-15C pair, holds a barrier CAP along the ridge line, **weapons
tight**. It engages only a track that has turned, and only that track — the
reason is in `core/intercept.py`'s docstring. `Magic` is already on station
and is the only radio in the picture; lose it and the calls stop.

**What is tasked scales with the slots** (`_plan_tracks`): two tracks for a
pair, three for four slots, four for six. Only the fighter tracks can turn, and
the count of fighter airframes is checked against the flight's own magazine at
two shots a kill. The Su-24MR is a camera with a self-defence rail, not a
fighter, and it never turns.

**Why the intruders are late-activated off the player's own take-off**
(`join_up.player_airborne`) rather than on the clock: on QRA the scramble *is*
the detection, and a first track that has done its photo run before the alert
pair has its gear up is a mission lost at startup.

**Why the success condition is the whole set resolved and nothing else.** A
track can end three ways — escorted out, run out after turning, or splashed
after turning — and all three are the airspace defended. The two failures are
different in kind: a track that loitered its full time unchallenged is a
partial failure the sortie can still be flown through, and a hit on a crew that
never turned is the incident that ends the detachment's mandate, whatever else
happened.

Composition (difficulty: trained, sized per player slot):
  - `Zombie 1`: 2x Su-24MR out of Beslan, through the Roki gap to a photo
    orbit over Gori. Never turns.
  - `Zombie 2`: 2x Su-27 out of Mozdok, down the Daryal to an orbit over
    Pasanauri. May turn.
  - `Zombie 3` (4+ slots): 2x MiG-29S out of Nalchik, over the Mamison pass to
    an orbit over Sachkhere. May turn.
  - `Zombie 4` (6 slots): 2x Su-30 out of Mozdok, through the Roki gap to an
    orbit over Kareli. May turn.
  - USA: `Uzi` F-16C-50 QRA, Vaziani, hot ramp; `Eagle` 2x F-15C barrier CAP,
    weapons tight, airborne; `Magic` E-3A, 251.000 AM, airborne.
  - Fall-back: `SENTRY`, a Hawk battery over Vaziani covering Tbilisi-Lochini;
    Beslan under an S-125 on the Russian side.
  - Weather: early-autumn mid-morning, scattered cumulus 3000 m, light W wind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from dcs import action, condition, planes, task, triggers
from dcs.country import Country
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup

from dcs_mission_creator.core import (
    dtc,
    kneeboard,
    loadout,
    sanctuary as sanc,
    triggers as mission_triggers,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.intercept import Calls, Flags, Track, arm_intercepts
from dcs_mission_creator.core.join_up import player_airborne
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import (
    MIN_PLAYERS,
    Assembled,
    MissionBuilder,
)
from dcs_mission_creator.core.mission_kit import arm, player_flight, set_skill
from dcs_mission_creator.core.placement import load_scene
from dcs_mission_creator.core.tasking import apply_ai_difficulty, apply_threat_reaction
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.scene import TacticalScene

#: Georgian airspace north of the Kura, as the detachment polices it: the
#: northern edge follows the border along the ridge, bowing north at each pass.
#: Degrees, so it can be read against a chart. Vaziani sits south of it on
#: purpose — the zone is what the intruders cross, not where the alert pair
#: lives.
_ZONE_DEG = (
    (42.95, 42.90),
    (42.80, 43.70),
    (42.70, 44.10),
    (42.78, 44.65),
    (42.55, 45.20),
    (41.80, 45.20),
    (41.80, 42.90),
)

_SANCTUARY = "SENTRY"
_SANCTUARY_BATTERY = sanc.HAWK
_SHOTS_PER_KILL = 2

#: Magic's frequency, typed here once because both briefings say it.
_MAGIC_MHZ = 251

#: The alert pair launches `_FIRST_TRACK_S` after the first wheel leaves the
#: runway, and each further track `_TRACK_SPACING_S` after the one before. The
#: spacing is a transit: Vaziani to the Roki gap is about eight minutes at the
#: route speed, so a pair that has seen one track to the line is back in the
#: middle of the zone as the next one crosses.
_FIRST_TRACK_S = 60
_TRACK_SPACING_S = 420

#: How long a track loiters, from crossing, before it leaves on its own.
_LOITER_S = 1_500.0


@dataclass(frozen=True)
class _Intruder:
    """One track's order of battle and geography, before anything is built."""

    label: str
    plane: type[planes.PlaneType]
    type_name: str
    home: str
    pass_name: str
    gate: tuple[float, float]  # the pass, just inside the line
    orbit: tuple[float, float]
    altitude_m: int
    speed_kph: int
    hostile_probability: float
    skill: Skill
    task: type[task.MainTask]


#: Every track the mission can field, in the order they cross. Speeds are km/h
#: at 0.26–0.38 of each airframe's `max_speed`. The Su-24MR's `0.0` is the
#: design, not a default: it is a reconnaissance jet with a pair of R-60s for
#: its own skin, and a Fencer turning in on an F-16 is not a threat the crew
#: would choose.
_TRACKS = (
    _Intruder(
        "Zombie 1",
        planes.Su_24MR,
        "Su-24MR",
        "Beslan",
        "Roki",
        (42.62, 44.12),
        (41.98, 44.11),
        6_000,
        620,
        0.0,
        Skill.Good,
        task.Reconnaissance,
    ),
    _Intruder(
        "Zombie 2",
        planes.Su_27,
        "Su-27",
        "Mozdok",
        "Daryal",
        (42.68, 44.63),
        (42.35, 44.69),
        7_500,
        800,
        0.35,
        Skill.High,
        task.CAP,
    ),
    _Intruder(
        "Zombie 3",
        planes.MiG_29S,
        "MiG-29S",
        "Nalchik",
        "Mamison",
        (42.70, 43.78),
        (42.35, 43.40),
        7_000,
        780,
        0.35,
        Skill.High,
        task.CAP,
    ),
    _Intruder(
        "Zombie 4",
        planes.Su_30,
        "Su-30",
        "Mozdok",
        "Roki",
        (42.62, 44.12),
        (42.02, 43.90),
        7_500,
        800,
        0.4,
        Skill.High,
        task.CAP,
    ),
)


def _plan_tracks(players: int) -> tuple[_Intruder, ...]:
    """Two tracks for a pair, one more per extra element: 2 / 3 / 4."""
    return _TRACKS[: 2 + (players - MIN_PLAYERS) // 2]


#: How `Uzi` splits its magazine. An intercept closes to a mile before anything
#: else happens, so if a track turns the fight starts inside AMRAAM minimum
#: range as often as outside it: slot 1 keeps six radar shots for the pair that
#: turns at twenty miles, slot 2 gives two up for AIM-9X. Both are ED payloads
#: station for station (`AIM-120C*6, FUEL*2, ECM` and
#: `AIM-120C*4, AIM-9X*2, FUEL*2, ECM`) — AMRAAM outboard-in on a pure A/A fit.
_AMRAAM = "AIM_120C_AMRAAM___Active_Radar_AAM"
_FITS = (
    loadout.Loadout(
        role="AIM-120C*6",
        carries="six AIM-120C, ALQ-184, two 370 gal — the shot at range",
        stores=(
            (1, _AMRAAM),
            (2, _AMRAAM),
            (3, _AMRAAM),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, _AMRAAM),
            (8, _AMRAAM),
            (9, _AMRAAM),
        ),
    ),
    loadout.Loadout(
        role="AIM-120C*4 + 9X",
        carries="four AIM-120C, two AIM-9X, ALQ-184, two 370 gal — the shot on the wing",
        stores=(
            (1, _AMRAAM),
            (2, _AMRAAM),
            (3, "AIM_9X_Sidewinder_IR_AAM"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "AIM_9X_Sidewinder_IR_AAM"),
            (8, _AMRAAM),
            (9, _AMRAAM),
        ),
    ),
)

#: What Magic says about a track. Radio, not bookkeeping: no distance, no timer.
_CALLS = Calls(
    intercepted=(
        "Magic copies, {label} is rocking wings and turning north. "
        "Stay on him all the way to the line."
    ),
    hostile=(
        "Magic, all players: {label} has turned in on the escort, hostile act. "
        "Uzi, cleared to engage {label} only. Eagle is committing."
    ),
    splashed="Magic: {label} is off the scope. Good kill. Every other track is still weapons tight.",
    down="Magic: {label} has dropped off the scope.",
    cleared="Magic: {label} is north of the line and still going. Let him go, nice work.",
    fled="Magic: {label} has run north across the line. Do not follow him.",
    lapsed=(
        "Magic: nobody is on {label} and he has turned back south. "
        "Somebody get back on him."
    ),
    unchallenged=(
        "Magic: {label} has finished his run and is heading home. "
        "Nobody ever joined on him."
    ),
    foul=(
        "Magic, all players: weapons hold, weapons hold. "
        "Shots on {label}, and he never turned."
    ),
)


@dataclass
class _Scene:
    vaziani: Airport
    lochini: Airport
    beslan: Airport
    zone: list[Point]
    station: Point
    descent: Point
    eagle_p1: Point
    eagle_p2: Point
    magic: Point
    overlay: TacticalScene


class RokiShepherd(MissionBuilder):
    name = "roki_shepherd"
    title = "Roki Shepherd"
    difficulty = Difficulty.TRAINED
    terrain = Caucasus

    blue_task = (
        "Quick-reaction alert out of Vaziani. Intercept every unidentified "
        "track Magic assigns inside Georgian airspace, shadow it, and escort "
        "it north across the border. Weapons tight: fire only on a track "
        "that commits a hostile act. Eagle holds the barrier CAP, weapons "
        "tight. RTB Vaziani."
    )
    red_task = (
        "Probe Georgian airspace through the Roki, Daryal and Mamison passes. "
        "Reconnaissance runs over Gori and the Aragvi valley; comply if "
        "intercepted, unless ordered otherwise."
    )

    #: 10:15 map-local, 2 October 2026.
    start_time = datetime(2026, 10, 2, 10, 15, 0, tzinfo=timezone.utc)

    weather = Weather(
        name="Autumn scattered",
        season_temperature=14.0,
        clouds_base=3000,
        clouds_thickness=700,
        clouds_density=3,
        visibility_distance=40000,
        wind_at_ground=Wind(270, 3),
        wind_at_2000=Wind(275, 7),
        wind_at_8000=Wind(280, 14),
    )

    def __init__(self, *, players: int = MIN_PLAYERS) -> None:
        super().__init__(players=players)
        self._tracks = _plan_tracks(self.players)
        fighters = sum(2 for t in self._tracks if t.hostile_probability > 0)
        kills = self.air_to_air_shots(_FITS) // _SHOTS_PER_KILL
        if fighters > kills:
            raise ValueError(
                f"{fighters} fighters could turn against a magazine worth {kills} kills"
            )

    # -- briefings -----------------------------------------------------------

    def _track_lines(self, width: int) -> str:
        rows = []
        for t in self._tracks:
            may = "may turn" if t.hostile_probability else "camera jet"
            rows.append(
                f"  {t.label:<9}: {t.type_name} pair, {t.pass_name} gap, "
                f"out of {t.home} ({may})"[:width]
            )
        return "\n".join(rows)

    def _in_game_briefing(self) -> str:
        n = len(self._tracks)
        return f"""ROKI SHEPHERD — Caucasus, 2 Oct 2026, 10:15 local
==================================================
SITUATION
  For three weeks Russian aircraft have been crossing
  into Georgian airspace through the Greater Caucasus
  passes: no flight plan, no transponder, turning for
  home once they have been seen. The Georgian
  government has asked the detachment at Vaziani to
  meet every one of them.

MISSION (Uzi — F-16C-50, Vaziani, hot ramp, QRA)
  - Scramble on Magic's call. Expect {n} tracks through
    the morning, one at a time.
  - Intercept each: close to visual, take a position
    off the lead's wing inside a mile and stay there
    until he acknowledges.
  - Shadow him north. The job is done when he is back
    across the border, not before.
  - RTB Vaziani. Divert: Tbilisi-Lochini.

TRACKS EXPECTED (Magic's read)
{self._track_lines(56)}

LOADOUT (one magazine, split two ways)
{self.loadout_brief("Uzi", _FITS)}

PACKAGE
  Uzi   : F-16C-50 QRA, loadout above.
  Eagle : F-15C pair, barrier CAP along the ridge,
          weapons tight, committed on a hostile only.
  Magic : E-3A, {_MAGIC_MHZ}.000 AM, on station south
          of the zone. Every track assignment is his.

INTELLIGENCE
  Magic's tracks and the partner-force radar at
  Tbilisi agree on the pattern: in through the gaps,
  a camera run, home. Most crews turn the moment they
  have an escort. Twice this month one has complied
  and then turned in on the interceptor. Magic has no
  way to tell you which crew that is.

ROE
  - WEAPONS TIGHT. A track is not a target until it
    commits a hostile act; Magic will call it.
  - Cleared to engage only the track that turned.
    The others stay weapons tight.
  - A shot on a crew that never turned ends the
    detachment's mandate. Hold fire if in doubt.
  - Not cleared to pursue north of the border, and
    not over Beslan (S-125 on the field).
  - Bingo 3000 lb.

FALL-BACK ({_SANCTUARY})
  Vaziani and Tbilisi-Lochini sit under a
  {_SANCTUARY_BATTERY.name} battery, {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, cyan ring.
  If a track turns and you are out of shots or
  fuel, come home under it.

FREQUENCIES
  Magic : {_MAGIC_MHZ}.000 AM
  Towers: per kneeboard
"""

    def readme(self) -> str:
        shots = self.air_to_air_shots(_FITS)
        n = len(self._tracks)
        rows = "\n".join(
            f"| `{t.label}` | {t.type_name} ×2 | {t.home} | {t.pass_name} "
            f"| {'may turn' if t.hostile_probability else 'reconnaissance, will not turn'} |"
            for t in self._tracks
        )
        return f"""# Roki Shepherd

**Theater:** Caucasus
**Date / time:** 2 October 2026, 10:15 local
**Player aircraft:** F-16C-50 (`Uzi`), Vaziani, hot ramp
**Players:** {self.slot_summary("Uzi")}
**Difficulty:** trained
**Expected sortie length:** ~60 minutes

## Situation

For three weeks Russian aircraft have been crossing into Georgian airspace
through the Greater Caucasus passes — Roki, Daryal, Mamison — with no flight
plan filed and no transponder. Each has flown a camera run over the valleys
south of the ridge and turned for home once it had been seen. The Georgian
government has asked the USAF detachment at Vaziani to meet every one.

## Mission

`Uzi` sits quick-reaction alert on the hot ramp at Vaziani. On `Magic`'s call,
scramble and **intercept each track `Magic` assigns, shadow it, and escort it
back north across the border**. Expect {n} tracks through the morning, one at a
time.

The intercept is the standard one: close to visual, take a position off the
lead's wing inside a mile, where his crew can see you, and stay there until he
acknowledges. A crew that has seen you rocks its wings and turns north. Stay
with him — a track left alone has turned back south before, and it will have to
be caught again. The job is done when he is across the line.

## Package

| Callsign | Type | Base | Role |
|---|---|---|---|
| Uzi | F-16C-50 | Vaziani | QRA — intercept and escort |
| Eagle | F-15C ×2 | airborne | Barrier CAP along the ridge, **weapons tight** |
| Magic | E-3A | airborne | AWACS, {_MAGIC_MHZ}.000 AM, south of the zone |

`Eagle` is not a second interceptor. It holds its race-track under the passes,
weapons tight, and is committed by `Magic` only on a track that has turned —
and only on that one. `Magic` is the whole picture: every track assignment and
every call is his. No tanker; the sortie fits the bags.

### `Uzi` loadout

{self.loadout_table("Uzi", _FITS)}

An intercept closes to a mile before anything else happens, so a track that
turns may start the fight inside AMRAAM minimum range. Slot 1 keeps six radar
shots for the pair that turns at twenty miles; slot 2 gives two up for AIM-9X
for the one that turns on your wing.

## Intelligence

`Magic`'s track history and the partner-force radar at Tbilisi agree on the
pattern: in through the gaps, a photo run, home. Expected this morning:

| Track | Type | From | Gap | Assessment |
|---|---|---|---|---|
{rows}

Most crews turn the moment they have an escort. **Twice this month one has
complied and then turned in on the interceptor** — minutes later, still south
of the line. `Magic` has no way of telling you which crew that is, and neither
do we. The gaps are marked on your map as the areas these tracks have used, not
as a prediction of where the next one crosses.

Beslan, where the Su-24MRs recover, is defended by an S-125 battery on the
field. It reaches nowhere near the border.

## ROE

- **Weapons tight.** A track is not a target until it commits a hostile act,
  and `Magic` will call it.
- On that call you are cleared to engage **the track that turned, and only that
  track**. Every other track in the zone stays weapons tight.
- A shot on a crew that never turned is the incident this whole detachment
  exists to avoid, and it ends the mandate. If in doubt, hold fire.
- Not cleared to pursue north of the border, and not over Beslan.
- Bingo 3000 lb. RTB Vaziani; divert Tbilisi-Lochini.

## Fall-back

Vaziani is covered by `{_SANCTUARY}`, a {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10
map, and Tbilisi-Lochini is inside the same envelope. A track that turns on an
escort with an empty rail or a low fuel state is a track to drag south under
it. `{_SANCTUARY} MARSHAL` is a hold abeam Vaziani, on the map and in the DED.

## Navigation

- The **zone** is outlined in cyan on the F10 map and loaded as a GEO line: the
  northern edge is the border.
- `STATION`: a hold between the three gaps, 7000 m. Launch to it and take
  vectors from `Magic`.
- `DESCENT`: the let-down point on the way home.

## Frequencies

- Magic AWACS: {_MAGIC_MHZ}.000 AM
- Vaziani / Tbilisi-Lochini: per kneeboard

## Weather

Early autumn, mid-morning. Scattered cumulus base 3000 m, 700 m thick; 40 km
visibility; wind west 3 m/s on the ground, 14 m/s at 8000 m. 14 °C. The passes
themselves are well above the cloud base — the ridge reaches 5000 m at Kazbek.

## Difficulty composition

**Trained.** {n} intruder pairs, one at a time, Skill High fighters and a Good
reconnaissance crew; AWACS and a weapons-tight CAP in support; no ground threat
south of the border. The opposition scales with the slots: at {self.players}
slots `Uzi` carries {shots} air-to-air shots, and every fighter that could turn
is paid for twice over at two shots a kill.

## Win / loss conditions

- **Success:** every track is out of Georgian airspace — escorted out, run out,
  or shot down after turning on its escort.
- **Partial:** a track flew its whole run with nobody alongside.
- **Failure:** a shot on a crew that never turned, or `Uzi` lost.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- orchestration ---------------------------------------------------------

    def _assemble(self, m: Mission, plan: PlanOverlay) -> Assembled:
        """Assemble the mission by calling each step in package order."""
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        magic = self._spawn_awacs(m, usa, scene)
        eagle = self._spawn_cap(m, usa, scene)
        uzi, route = self._spawn_player(m, usa, scene)
        zombies = self._spawn_intruders(m, russia, scene)
        home, beslan = self._spawn_sanctuaries(m, usa, russia, scene, zombies)

        flags = self._arm_intercepts(m, scene, zombies, eagle=eagle, magic=magic)
        self._add_activation_triggers(m, zombies)
        self._add_end_triggers(m, flags, uzi=uzi)
        self._add_intro(m)
        sanc.announce(m, home, at_seconds=150, voice=self._voice)
        sanc.remark_all(m, home, beslan)
        self._add_remarks(m)
        briefed = self._draw_plan(scene, plan, route=route, home=home, beslan=beslan)
        return Assembled(scene.overlay.overlay, briefed)

    def _setup_airports(self, m: Mission) -> _Scene:
        """Vaziani and Lochini ours, Beslan/Mozdok/Nalchik theirs; the zone."""
        t = self._terrain
        vaziani, lochini = t.airports["Vaziani"], t.airports["Tbilisi-Lochini"]
        vaziani.set_blue()
        lochini.set_blue()
        for name in ("Beslan", "Mozdok", "Nalchik"):
            t.airports[name].set_red()
        return _Scene(
            vaziani=vaziani,
            lochini=lochini,
            beslan=t.airports["Beslan"],
            zone=[self.at(lat, lng) for lat, lng in _ZONE_DEG],
            station=self.at(42.28, 44.30),
            descent=self.at(41.88, 44.70),
            eagle_p1=self.at(42.42, 43.70),
            eagle_p2=self.at(42.42, 44.55),
            magic=self.at(41.90, 43.40),
            overlay=load_scene("caucasus"),
        )

    # -- blue ------------------------------------------------------------------

    def _spawn_awacs(self, m: Mission, usa: Country, scene: _Scene) -> FlyingGroup:
        """Magic already on station south of the zone — the scramble's source."""
        return m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=None,
            position=scene.magic,
            race_distance=80_000,
            heading=90,
            altitude=9000,
            speed=740,
            frequency=_MAGIC_MHZ,
        )

    def _spawn_cap(self, m: Mission, usa: Country, scene: _Scene) -> FlyingGroup:
        """Eagle: a race-track under the passes, weapons tight until told.

        Airborne rather than off a ramp: a barrier CAP is established before the
        first track crosses, which is the point of it. `arm_intercepts` strips
        its own engage task, so it shoots only the group it is committed on.
        """
        eagle = m.flight_group_inflight(
            usa,
            "Eagle",
            planes.F_15C,
            position=scene.eagle_p1,
            altitude=8000,
            speed=800,
            maintask=task.CAP,
            group_size=2,
        )
        eagle.points[0].tasks.append(task.OrbitAction(8000, 800))
        eagle.add_waypoint(scene.eagle_p2, altitude=8000, speed=800)
        set_skill(eagle, Skill.High)
        arm(
            eagle,
            planes.F_15C,
            [
                (1, "AIM_9M_Sidewinder_IR_AAM"),
                (3, _AMRAAM),
                (4, "AIM_120B_AMRAAM___Active_Radar_AAM"),
                (5, "AIM_120B_AMRAAM___Active_Radar_AAM"),
                (6, "Fuel_tank_610_gal"),
                (7, "AIM_120B_AMRAAM___Active_Radar_AAM"),
                (8, "AIM_120B_AMRAAM___Active_Radar_AAM"),
                (9, _AMRAAM),
                (11, "AIM_9M_Sidewinder_IR_AAM"),
            ],
        )
        apply_threat_reaction(eagle, reaction=task.OptReactOnThreat.Values.EvadeFire)
        return eagle

    def _spawn_player(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[list[FlyingGroup], list[Point]]:
        """Uzi on the Vaziani hot ramp; every section flies `_route_qra`."""
        sections = player_flight(
            m,
            country=usa,
            name="Uzi",
            aircraft_type=planes.F_16C_50,
            airport=scene.vaziani,
            maintask=task.Intercept,
            start_type=StartType.Warm,
            slots=self.players,
            loadouts=_FITS,
        )
        routes = [self._route_qra(section, scene) for section in sections]
        return sections, routes[0]

    def _route_qra(self, flight: FlyingGroup, scene: _Scene) -> list[Point]:
        """Take-off → STATION → DESCENT → land. Magic's vectors do the rest."""
        flight.add_runway_waypoint(scene.vaziani)
        flight.add_waypoint(scene.station, altitude=7000, speed=820, name="STATION")
        flight.add_waypoint(scene.descent, altitude=3500, speed=700, name="DESCENT")
        flight.add_runway_waypoint(scene.vaziani)
        flight.land_at(scene.vaziani)
        return [scene.vaziani.position, scene.station, scene.descent]

    # -- red -------------------------------------------------------------------

    def _spawn_intruders(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[tuple[_Intruder, FlyingGroup]]:
        return [(t, self._spawn_intruder(m, russia, t)) for t in self._tracks]

    def _spawn_intruder(self, m: Mission, russia: Country, t: _Intruder) -> FlyingGroup:
        """One pair, held north of its gap until Magic calls it: gap → orbit."""
        gate = self.at(*t.gate)
        spawn = gate.point_from_heading(0, 45_000)
        group = m.flight_group_inflight(
            russia,
            t.label,
            t.plane,
            position=spawn,
            altitude=t.altitude_m,
            speed=t.speed_kph,
            maintask=t.task,
            group_size=2,
        )
        group.late_activation = True
        group.add_waypoint(gate, altitude=t.altitude_m, speed=t.speed_kph, name="GATE")
        orbit = group.add_waypoint(
            self.at(*t.orbit), altitude=t.altitude_m, speed=t.speed_kph, name="RUN"
        )
        orbit.tasks.append(
            task.OrbitAction(
                t.altitude_m, t.speed_kph, task.OrbitAction.OrbitPattern.Circle
            )
        )
        if t.plane is planes.Su_24MR:
            # The camera fit without the laser pod: Tangazh for the emitters,
            # two R-60M for its own skin, both bags for the run.
            arm(
                group,
                planes.Su_24MR,
                [
                    (1, "APU_60_2M_with_2_x_R_60M__AA_8_Aphid_B____IR_AAM__"),
                    (2, "Fuel_tank_3000L"),
                    (5, "Tangazh_ELINT_pod"),
                    (7, "Fuel_tank_3000L"),
                    (8, "ETHER"),
                ],
            )
        set_skill(group, t.skill)
        apply_ai_difficulty(group, self.difficulty)
        return group

    # -- sanctuaries -----------------------------------------------------------

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        zombies: Sequence[tuple[_Intruder, FlyingGroup]],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """Hawk over Vaziani, S-125 over Beslan.

        Out of ours goes every point a track is routed to — its gap and its
        orbit — because a Hawk that reaches a compliant intruder is a foul the
        player did not commit. Out of theirs goes the whole police zone.
        """
        tracks = [self.at(*t.gate) for t, _ in zombies] + [
            self.at(*t.orbit) for t, _ in zombies
        ]
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.vaziani,
            callsign=_SANCTUARY,
            facing=scene.station,
            battery=_SANCTUARY_BATTERY,
            keep_clear=tracks,
            alternates=[scene.lochini],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        beslan = sanc.build_sanctuary(
            m,
            russia,
            scene.beslan,
            callsign="Beslan field",
            facing=scene.station,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Beslan",
            keep_clear=[*scene.zone, scene.eagle_p1, scene.eagle_p2, scene.magic],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, beslan

    # -- triggers --------------------------------------------------------------

    def _arm_intercepts(
        self,
        m: Mission,
        scene: _Scene,
        zombies: Sequence[tuple[_Intruder, FlyingGroup]],
        *,
        eagle: FlyingGroup,
        magic: FlyingGroup,
    ) -> Flags:
        """Each track's way home runs back up its own gap to its own field."""
        tracks = [
            Track(
                group=group,
                label=t.label,
                orbit=self.at(*t.orbit),
                exit=self.at(*t.gate).point_from_heading(0, 40_000),
                home=self._terrain.airports[t.home],
                altitude_m=t.altitude_m,
                speed_kph=t.speed_kph,
                hostile_probability=t.hostile_probability,
                deadline_s=_LOITER_S,
                calls=_CALLS,
            )
            for t, group in zombies
        ]
        return arm_intercepts(
            m,
            tracks,
            zone=scene.zone,
            defenders=[eagle],
            controller=magic,
            voice=self._voice,
        )

    def _add_activation_triggers(
        self, m: Mission, zombies: Sequence[tuple[_Intruder, FlyingGroup]]
    ) -> None:
        """The first wheel up starts the morning; each track is Magic's call."""
        airborne_flag = 20
        mark = triggers.TriggerOnce(comment="QRA airborne")
        for cond in player_airborne(m):
            mark.add_condition(cond)
        mark.add_action(action.SetFlag(airborne_flag))
        m.triggerrules.triggers.append(mark)
        for i, (t, group) in enumerate(zombies):
            rule = mission_triggers.message_to_coalition(
                m,
                comment=f"{t.label} crosses",
                conditions=(
                    condition.TimeSinceFlag(
                        airborne_flag, _FIRST_TRACK_S + i * _TRACK_SPACING_S
                    ),
                ),
                voice=self._voice,
                text=(
                    f"Magic: new track, two contacts southbound through the "
                    f"{t.pass_name} gap, no squawk, no flight plan. Assigning "
                    f"{t.label}. Uzi, intercept."
                ),
            )
            rule.add_action(action.ActivateGroup(group.id))

    def _add_end_triggers(
        self, m: Mission, flags: Flags, *, uzi: Sequence[FlyingGroup]
    ) -> None:
        mission_triggers.message_to_all(
            m,
            comment="Airspace clear",
            conditions=(
                condition.FlagIsTrue(flags.done),
                condition.FlagIsFalse(flags.foul),
                condition.FlagIsFalse(flags.unchallenged),
            ),
            voice=self._voice,
            text=(
                "Magic: scope is clean south of the ridge, every track is home. "
                "Uzi, that is the morning done. RTB Vaziani."
            ),
            seconds=25,
        )
        mission_triggers.message_to_all(
            m,
            comment="Airspace clear, one run unchallenged",
            conditions=(
                condition.FlagIsTrue(flags.done),
                condition.FlagIsFalse(flags.foul),
                condition.FlagIsTrue(flags.unchallenged),
            ),
            voice=self._voice,
            text=(
                "Magic: scope is clean, but one of them got his pictures and "
                "nobody saw him out. Uzi, RTB Vaziani."
            ),
            seconds=25,
        )
        mission_triggers.message_to_all(
            m,
            comment="Incident",
            conditions=(condition.FlagIsTrue(flags.foul),),
            voice=self._voice,
            text=(
                "Magic: Tbilisi is recalling the detachment. That was a crew "
                "that had not turned. All players, RTB Vaziani."
            ),
            seconds=25,
        )
        mission_triggers.message_to_all(
            m,
            comment="Uzi lost",
            conditions=tuple(condition.GroupDead(g.id) for g in uzi),
            voice=self._voice,
            text="Magic: Uzi is down. The alert is broken, Eagle is holding the line.",
            seconds=25,
        )

    def _add_intro(self, m: Mission) -> None:
        mission_triggers.intro(
            m,
            comment="QRA picture",
            voice=self._voice,
            text=(
                f"Magic on {_MAGIC_MHZ} decimal zero. Eagle is established on the "
                "barrier, weapons tight. Activity building north of the ridge. "
                "Uzi, you are cleared to launch."
            ),
        )

    def _add_remarks(self, m: Mission) -> None:
        kneeboard.remark(
            m, "Intercept: visual, off his wing inside 1 NM until he rocks wings."
        )
        kneeboard.remark(m, "Weapons tight. Engage only the track Magic calls hostile.")

    # -- plan ------------------------------------------------------------------

    def _draw_plan(
        self,
        scene: _Scene,
        plan: PlanOverlay,
        *,
        route: list[Point],
        home: sanc.Sanctuary,
        beslan: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """The zone, the friendly stations, and the gaps as vague areas.

        The zone and every friendly line are ours and drawn exactly. The gaps
        are areas: the prose sources them to a history of tracks, which says
        where these crews have come through, not where the next one will.
        """
        home.draw(plan)
        plan.boundary(scene.zone, "Police zone — border to the north")
        plan.route(route, "Uzi QRA")
        plan.orbit(scene.eagle_p1, scene.eagle_p2, "Eagle barrier CAP")
        plan.waypoint_label(scene.magic, "Magic AWACS")
        drawn: set[str] = set()
        for t in self._tracks:
            if t.pass_name in drawn:
                continue
            drawn.add(t.pass_name)
            plan.threat_area(self.at(*t.gate), 12_000.0, f"{t.pass_name} gap — probes")
        return beslan.draw(plan)


def main() -> None:
    run_cli(RokiShepherd)


if __name__ == "__main__":
    main()
