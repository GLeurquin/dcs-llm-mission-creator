"""Caucasus 'Daryal Run' — F-16C ace SEAD strike with low valley ingress.

Player flies a USAF F-16C-50 out of Vaziani as `Dodge`. The target is a
Russian S-300PS (SA-10) battery emplaced south of Beslan, covering the
North Caucasus from a ridge that denies any high-altitude approach. The
only viable ingress is the Georgian Military Road: north over the Jvari
Pass, then down the Terek through the Daryal Gorge between Mt Kazbek
(5033 m) and the eastern ridge — terrain-masked from the Big Bird radar
until the gorge ends about 16 km short of the battery and the player pops
for the HARM shot. Egress is west across the plain and south up the Ardon
over the Roki Pass; the whole route is checked against the elevation
raster (`_CORRIDOR`, `_route_altitudes`). AWACS `Magic` holds a southern
race-track. No tanker, no escort, no SEAD support.

Composition (difficulty: ace):
  - SA-10 site: 64H6E (Big Bird) SR + 30H6 Flap Lid TR + 54K6 CP +
    4x 5P85C/D launchers. Skill Excellent.
  - 1x SA-15 Tor terminal SHORAD adjacent to the SAM site (Skill Excellent).
  - 2x ZSU-23-4 Shilka AAA at the SAM site (Skill High).
  - 1x 1L13 EWR on a ridge near Mozdok (Skill Excellent), feeding GCI.
  - 2x Russian MiG-29S out of Mozdok, late-activated on an intrusion zone
    just south of Beslan, R-77 / R-27 class, Skill Excellent.
  - USA support: E-3A `Magic` AWACS only, 251.000 AM. No tanker, no
    escort, no SEAD wingman — bandits 2x player, denied support.
  - Weather: autumn dusk, broken layer at 2200 m, light N wind, 12 C,
    30 km visibility (haze + low light).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from dcs import action, condition, planes, task, templates, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import LatLng, Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    offset,
    player_flight,
    set_skill,
    unit_of_type,
)
from dcs_mission_creator.core.placement import load_scene
from dcs_mission_creator.core.tasking import apply_ai_difficulty
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene

#: What the briefing claims each emplaced system reaches, before the difficulty
#: coarsens it. The S-300's 75 km is why this sortie is flown up a gorge at a
#: couple of hundred metres over the river: the ring covers everything above the
#: ridges, and drawing it is what shows the player *why* the plan looks the way
#: it does.
_SA10_RING_M = 75_000.0
_TOR_RING_M = 12_000.0

#: Commanded true airspeeds, **km/h** — the unit every pydcs speed argument
#: takes and none of them names. On an F-16C-50 (`max_speed` 2120 km/h) a
#: cruise sits at 0.30–0.40 of that; a bombed-up jet at the bottom of the band,
#: which is where the gorge number is, and it is slow for a second reason —
#: 700 km/h is what a 200 m run between 3 km walls at dusk is worth.
_TRANSIT_SPEED_KPH = 800.0
_GORGE_SPEED_KPH = 700.0
_EGRESS_SPEED_KPH = 800.0

#: How far above the ground the whole flown route has to stay, both at a
#: waypoint and along the straight leg between two of them. `core/waypoints.py`
#: enforces it; see `_route_altitudes` for why a mission on this map cannot
#: write altitudes by hand.
_LEG_CLEARANCE_M = 150.0

#: The ingress corridor: the Georgian Military Road north over the Jvari Pass,
#: then the Terek down the Daryal Gorge to its mouth at Balta. `(name, lat,
#: lng, height above the ground)` — degrees rather than DCS metres because
#: every one of these is a real place on that road, and a coordinate you can
#: put on a map is a coordinate somebody can check. That is not a style
#: preference here: the route this replaced was written in raw map metres and
#: shipped with two of its three valley waypoints inside a mountainside, one of
#: them by 2.7 km, which nobody could see by reading it.
#:
#: The AGL column is the descent, and it is what the mission is: transit above
#: the ridges while the massif itself masks the battery, cross at the only pass
#: the road takes, then go down the gorge and stay under the Big Bird's horizon
#: until the pop. Measured against the elevation raster, the corridor is masked
#: from the site at every one of these points and stops being masked about
#: 16 km out — which is where the gorge ends and the run-in starts.
_CORRIDOR = (
    ("PUSH", 41.8592, 44.9748, 2_000.0),  # climb-out N of Vaziani
    ("ARAGVI", 42.3530, 44.6870, 2_000.0),  # Pasanauri, joins the road
    ("JVARI", 42.5050, 44.4520, 1_200.0),  # Jvari Pass, 2400 m
    ("KOBI", 42.5450, 44.5020, 700.0),  # head of the Terek
    ("TEREK", 42.5900, 44.5700, 500.0),  # into the upper gorge
    ("KAZBEGI", 42.6676, 44.6434, 400.0),  # Stepantsminda
    ("DARYAL", 42.7434, 44.6230, 300.0),  # the narrows
    ("LARS", 42.8199, 44.6442, 250.0),
    ("CHMI", 42.8827, 44.6323, 250.0),
    ("BALTA", 42.9135, 44.6388, 200.0),
    ("IP", 42.9416, 44.6632, 200.0),  # gorge mouth — pop point
)

#: Egress: west across the plain, then south up the Ardon and over the Roki
#: Pass. Same table, same rules. The first leg is the exposed one and is meant
#: to be — it leaves the target area on the shortest vector out of the ring —
#: and the Ardon takes the flight back under cover from Buron southward.
#:
#: `MTSKHETA` is the let-down, and it earns its place on the timing rather than
#: the geography: pydcs writes the *approach* runway waypoint at a hard-coded
#: 108 kt (`waypoints.set_departure_speeds` fixes the departure one and leaves
#: this one alone on purpose), so whatever leg ends there is flown on the
#: kneeboard at approach speed. Running it straight from Roki made that a
#: 76 NM leg and put 42 minutes of the sortie's stated hour inside it. Coming
#: down at the foot of the Georgian Military Road, where the route joined it on
#: the way out, leaves a 19 NM final instead.
#:
#: Between them the two tables put 21 points on the route, and `core/dtc.py`
#: writes the route into the Viper's own steerpoint tab, which holds
#: `dtc.MAX_NAV_POINTS` = 25. The cartridge currently packs 24. Adding a
#: corridor point means the plan's marks start being dropped off the end of the
#: tab — `arm_plan` warns rather than truncating silently, but the warning is
#: the only thing that will tell you.
_EGRESS = (
    ("EGRESS_W", 42.9840, 44.2903, 350.0),  # the plain west of Vladikavkaz
    ("ARDON", 42.9500, 44.2000, 300.0),  # mouth of the Ardon
    ("BURON", 42.8000, 44.0000, 1_450.0),  # up the Transkam
    ("ROKI", 42.5000, 43.9200, 1_600.0),  # over the pass into Georgia
    ("MTSKHETA", 41.8450, 44.7200, 1_500.0),  # let-down at the foot of the road
)


@dataclass(frozen=True)
class _Leg:
    """One en-route point and how far above the ground it is to be flown."""

    name: str
    position: Point
    agl_m: float


@dataclass
class _Scene:
    """Resolved airports + key positions used by every spawn step."""

    vaziani: Airport
    soganlug: Airport
    mozdok: Airport
    beslan: Airport
    sa10_site: Point
    shorad: Point
    ewr_pos: Point
    ingress: tuple[_Leg, ...]
    egress: tuple[_Leg, ...]
    awacs_anchor: Point
    intrusion_center: Point
    overlay: TacticalScene

    @property
    def ip(self) -> Point:
        """The pop point: the last corridor point, at the mouth of the gorge."""
        return self.ingress[-1].position


# Vaziani's own air defence, and the reason an ace mission needs it most.
#
# This is the sortie where the player is deepest and most alone: 168 km out,
# one flight, no tanker, no escort, against a battery flown by the best crew in
# the game. `RAMPART` is what makes turning for home a plan rather than a hope,
# and Soganlug 8 km away means the same envelope covers a second runway for a
# jet that will not make a normal approach.
_SANCTUARY = "RAMPART"
_SANCTUARY_BATTERY = sanc.HAWK


class DaryalRun(MissionBuilder):
    name = "daryal_run"
    title = "Daryal Run"
    difficulty = Difficulty.ACE

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""DARYAL RUN — Caucasus, 12 Oct 2026, 18:15 local (dusk)
========================================================
SITUATION
  ELINT has been reading a Big Bird and a Flap Lid south
  of Beslan for three days: a Russian S-300PS battery has
  emplaced on a ridge there and shut down the airspace
  over the North Caucasus to any high-altitude push. The
  bearings cross to within a few kilometres — good enough
  for a target area, not for a pinpoint. Command wants
  those radars off the air tonight, before the layer
  thickens overnight.

  The only viable ingress is the Georgian Military Road.
  North to Pasanauri, over the Jvari Pass, down the Terek
  into the Daryal Gorge between Mt Kazbek and the eastern
  ridge, out at Balta. The massif masks you as far as the
  pass; below Stepantsminda only the gorge does. It narrows
  to roughly 3 km in places — pick your line.

MISSION (Dodge — F-16C-50, Vaziani, hot ramp)
  - Push north above the ridges. The massif is between you
    and the Big Bird the whole way to the pass; no reason
    to be low yet, every reason to be fast.
  - Cross at Jvari, 2400 m, then follow the Terek down. Be
    on the deck by Stepantsminda and stay there.
  - Run the gorge. Pop at the mouth north of Balta, HARM
    the Big Bird, re-attack the Flap Lid and the 54K6 CP.
    Launchers are bonus; the radars are the kill.
  - Egress WEST across the plain, then south up the Ardon
    and over the Roki Pass. Do NOT re-cross Daryal — the
    MiG-29S will be in by then. The climb out of the Ardon
    mouth is the exposed minute of this sortie; take it
    west of Vladikavkaz, not before.
  - RTB Vaziani. Divert: Soganlug.

PACKAGE
  Dodge 1 (you) : F-16C-50, Vaziani, hot ramp, SEAD frag.
  Magic         : E-3A AWACS, 251.000 AM, race-track over
                  Georgia. No tanker, no escort, no Weasel
                  wingman. You are alone tonight.

INTELLIGENCE
  No overhead of the site — cloud for two days. What we
  have is the ELINT cut and pattern-of-life. Every ring
  on your map is that cut: drawn wide, dashed, marked
  approximate, and out by some kilometres. Your TARGET
  steerpoint is the same cut, not a survey — expect to
  find the radars, not to fly to them.
  SAM : S-300PS battery. Search radar, tracking radar,
        command post and launchers, reach out to about
        75 km at altitude. Their best crew — assume they
        are alert and assume terminal SHORAD is sited
        with them to close the low block, guns as well.
  Air : Mozdok holds a MiG-29S pair, R-77 shooters,
        experienced. They will launch once you are
        detected south of Beslan.
  EWR : Early-warning radar on a ridge near Mozdok feeds
        the fighters their picture.
  Base: Mozdok is defended in its own right — an S-125
        battery on the field, guns in the overhead. It
        reaches nothing on your route. Do not follow the
        MiGs home to find out.

ROE / FRAGS
  - Weapons free on the SA-10 cluster and any Russian
    aircraft that comes up against you north of
    the border.
  - Keep below 1000 m AGL from Stepantsminda to the gorge
    mouth — Big Bird sees you the moment you crest.
  - Not cleared to pursue over Mozdok.
  - Bingo fuel: 2500 lb. RTB Vaziani via the Ardon and
    Roki, not back through Daryal.

FALL-BACK ({_SANCTUARY})
  Vaziani and Soganlug both sit under a
  {_SANCTUARY_BATTERY.name} battery — {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km,
  cyan ring on the map, guns in the overhead of Vaziani.
  You are alone out there and you will know before
  anyone else does whether this is still working. If it
  is not, the ring is the plan: cross it and the sortie
  is over. {_SANCTUARY} MARSHAL is a hold abeam Vaziani,
  on the map and in the DED. Either runway takes you.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  PUSH                : climb-out NNW of Vaziani.
  ARAGVI              : Pasanauri. Joins the road.
  JVARI               : the Jvari Pass, 2400 m.
  KOBI / TEREK        : head of the Terek, descending.
  KAZBEGI             : Stepantsminda. Deck from here.
  DARYAL / LARS       : the narrows and Verkhniy Lars.
  CHMI / BALTA        : lower gorge, 200 m AGL.
  IP                  : gorge mouth north of Balta. Pop.
  TARGET              : the ELINT cut, ~12 km south of
                        Beslan. Approximate, not surveyed.
  EGRESS_W            : the plain west of Vladikavkaz.
  ARDON / BURON       : up the Ardon on the Transkam.
  ROKI                : over the pass, into Georgia.
  MTSKHETA            : let-down for Vaziani.

FREQUENCIES
  Magic AWACS   : 251.000 AM
  Vaziani tower : per kneeboard

NOTES
  Sunset ~18:40 local. The valley will be in shadow before
  you reach the IP. Broken layer base 2200 m — you descend
  through it into the Terek on the way in and climb back
  through it over the Ardon on the way out. Check your six
  before the second one.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Daryal Run

**Theater:** Caucasus
**Date / time:** 12 October 2026, 18:15 local (dusk)
**Player aircraft:** F-16C-50 (`Dodge`), Vaziani, hot ramp
**Players:** {self.slot_summary("Dodge")}
**Difficulty:** ace
**Expected sortie length:** ~55 minutes

## Situation

ELINT has been reading a Big Bird and a Flap Lid south of Beslan for three
days: a Russian S-300PS (SA-10) battery has emplaced on a ridge there and
shut the North Caucasus airspace to any high-altitude push. The bearings
cross to within a few kilometres — good enough for a target area, not for a
pinpoint. Command wants those radars off the air tonight, before the cloud
layer thickens overnight.

The only viable ingress is the **Georgian Military Road**: north to
Pasanauri, over the **Jvari Pass** at 2400 m, down the Terek into the
**Daryal Gorge** between Mt Kazbek (5033 m) and the eastern ridge, and out at
Balta. As far as the pass the massif itself does the masking and there is no
reason to be low; below Stepantsminda only the gorge does, and it narrows to
roughly 3 km in places — pick your line.

## Mission

Push north out of Vaziani above the ridges and fast. Cross at Jvari, follow
the Terek down, and be on the deck by Stepantsminda. Run the gorge to its
mouth north of Balta, pop there, HARM the Big Bird, re-attack the Flap Lid
and the 54K6 CP. Egress west across the plain, then south up the Ardon and
over the Roki Pass. Do **not** re-cross Daryal on egress — the MiG-29S CAP
will be airborne by then.

The climb out of the Ardon mouth is the one deliberately exposed minute of
the sortie: the plain west of Vladikavkaz is still inside the battery's reach
and there is no terrain to use until you are into the Ardon. Take it west of
Vladikavkaz rather than off the target, and take it assuming the radars are
already down — which is what you were sent to do.

## Package

| Callsign | Type     | Base    | Role                           |
|----------|----------|---------|--------------------------------|
| Dodge    | F-16C-50 | Vaziani | Player SEAD strike (frag SA-10)|
| Magic    | E-3A     | Vaziani | AWACS, 251.000 AM, south of mtns|

No tanker, no escort, no Weasel wingman — denied support is part of the
ace composition. Carry externals.

## Intelligence

No overhead of the site — cloud for two days. What we have is the ELINT cut
and pattern-of-life, so every position below is approximate — and the map is
drawn to say so. The rings are dashed, wider than the systems reach and
labelled `(approx.)`; so is the pre-planned pair on your HSD. Your `TARGET`
steerpoint is the same cut rather than a survey, so it puts you over the area,
not over the launchers. Find the radars with the HTS.

- **SAM (boss):** the S-300PS battery — search radar, tracking radar, command
  post and launchers — reaching out to roughly 75 km against a fast jet at
  altitude. That ring is on your map, and it is the whole reason for this
  routing: it covers every approach above the ridges, which leaves the gorge.
  Their best crew; assume they are alert.
- **Terminal SHORAD:** a point-defence system is sited with the battery to
  close the low block — assessed, ringed on your map at the same confidence as
  everything else — and assume guns with it.
- **EWR:** early-warning radar on a ridge near Mozdok feeding the fighters.
- **Air:** a MiG-29S pair at Mozdok, R-77 shooters, experienced crews. They
  will launch once you are detected south of Beslan.
- **Mozdok field defence:** the same ELINT work puts an S-125 battery on the
  airfield, with self-propelled guns in the overhead. It reaches 18 km, 78 km
  from your target, so it touches no part of the run — it is the reason a MiG
  that turns for home stops being a target.

## ROE

- Weapons free on the SA-10 cluster and any Russian aircraft that comes up
  against you north of the border.
- Stay below 1000 m AGL from Stepantsminda to the gorge mouth — Big Bird
  sees you the moment you crest.
- **Not cleared to pursue over Mozdok.** A withdrawing MiG is not worth an
  S-125, least of all on the fuel you will have by then.
- Bingo fuel: 2500 lb. RTB Vaziani via the Ardon and Roki, **not** back
  through Daryal.

## Fall-back

Vaziani is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10 map, with gun sections in
the overhead. Soganlug is 8 km away and **inside the same envelope**, so a jet
that cannot fly a normal approach has two runways under one battery.

That matters more on this sortie than on any other. You go 168 km alone against
the best crew on the map, with four HARMs, no tanker, no escort and no wingman —
and the one thing that makes "abort and run" a plan rather than a slower loss is
that it ends somewhere. If the run has gone wrong, turn south, take the Ardon and
the Roki as briefed, and cross that ring. `{_SANCTUARY} MARSHAL` is a hold abeam
Vaziani inside the envelope, on the map and in the DED, for a damaged jet waiting
on the pattern.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- `PUSH` — climb-out NNW of Vaziani
- `ARAGVI` — Pasanauri, where the route joins the Georgian Military Road
- `JVARI` — the Jvari Pass, 2400 m: the only crossing the road takes
- `KOBI`, `TEREK` — head of the Terek, descending into the gorge
- `KAZBEGI` — Stepantsminda. On the deck from here
- `DARYAL`, `LARS`, `CHMI`, `BALTA` — the gorge, 200–570 m above the floor
- `IP` — the gorge mouth north of Balta. Pop here
- `TARGET` — the ELINT cut, roughly 12 km south of Beslan airfield —
  approximate, not a surveyed fix
- `EGRESS_W` — the plain west of Vladikavkaz
- `ARDON`, `BURON` — up the Ardon on the Transkam
- `ROKI` — over the Roki Pass, into Georgia
- `MTSKHETA` — let-down at the foot of the Georgian Military Road, where the
  route joined it on the way out

The heights above are over the *valley floor*. Your kneeboard prints altitudes,
which is a different and always larger number, and the profile on it clears the
ground on every leg — including the spurs between waypoints, which the Terek
bends round four times between Kobi and Balta. Fly the card, not the difference
between the two.

## Frequencies

- Magic AWACS: 251.000 AM
- Vaziani tower: per kneeboard
- `{_SANCTUARY}` details and the Soganlug divert are on the kneeboard comms card.

## Weather

Autumn dusk, broken layer base 2200 m, 800 m thick, density 6.
Light north wind 4 m/s ground, 8 m/s at 8000 m. 12 °C, QNH 760 mmHg.
Visibility 30 km (haze). Sunset ~18:40 local — the valley will be in
shadow by the time you reach the IP.

## Difficulty composition

**Ace.** Excellent SA-10 + SA-15 + EWR, Excellent MiG-29S CAP, bandits
2x player flight, R-77 class missiles, AWACS-only support (no tanker, no
escort, no Weasel wingman), dusk with broken layer, a 180 km gorge-only
viable ingress, west-only viable egress. One mistake ends the sortie.

## Win / loss conditions

- **Success:** the battery's search and tracking radars are both off the air
  for good — the North Caucasus is open again.
- **Failure:** `Dodge` goes down with the battery still tracking.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- top-level orchestration --------------------------------------------

    def _assemble(self, m: Mission) -> MapOverlay:
        """Assemble the mission by calling each step in package order."""
        self._set_time(m)
        self._set_weather(m)
        # The overlay comes first because the *flight plan* is built off it:
        # every steerpoint that refers to the battery refers to the estimate
        # this will draw, never to the battery. Nothing the player can read —
        # map, DED, HSD, kneeboard — then carries a position better than the
        # ELINT cut the briefing admits to.
        plan = PlanOverlay(m, self.difficulty)
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        sa10, _tor, _shilkas, _ewr = self._spawn_red_ground(m, russia, scene)
        self._spawn_awacs(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        dodge, route = self._spawn_player(m, usa, scene, plan=plan)

        home, mozdok_ad = self._spawn_sanctuaries(m, usa, russia, scene, route=route)

        self._add_end_triggers(m, sa10=sa10, dodge=dodge)
        self._add_sanctuary_checkin(m, home)
        sanc.remark_all(m, home, mozdok_ad)
        self._conceal_red(russia)
        briefed_threats = self._draw_plan(
            m, scene, plan=plan, route=route, home=home, mozdok_ad=mozdok_ad
        )
        self._load_cartridge(m, scene, briefed_threats, plan=plan)
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """18:15 map-local on 12 October 2026 — dusk, the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 10, 12, 18, 15, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Autumn dusk, broken layer at 2200 m, light N wind, 12 C, 30 km visibility."""
        Weather(
            name="Autumn dusk",
            season_temperature=12.0,
            clouds_base=2200,
            clouds_thickness=800,
            clouds_density=6,
            visibility_distance=30000,
            wind_at_ground=Wind(0, 4),
            wind_at_2000=Wind(10, 6),
            wind_at_8000=Wind(350, 8),
        ).apply(m)

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Vaziani for blue, Mozdok for red, derive valley + target geometry."""
        t = self._terrain
        vaziani = t.airports["Vaziani"]
        mozdok = t.airports["Mozdok"]
        beslan = t.airports["Beslan"]
        # Soganlug is 8 km from Vaziani and inside the same missile umbrella —
        # a second runway for a jet coming back damaged, at no cost in cover.
        soganlug = t.airports["Soganlug"]
        vaziani.set_blue()
        soganlug.set_blue()
        mozdok.set_red()
        beslan.set_red()

        # SA-10 cluster sits ~12 km south of Beslan, in the open ground east
        # of Vladikavkaz where the SR has a clear horizon to the south.
        sa10_site = offset(beslan.position, east_m=2_000, north_m=-12_000)
        shorad = offset(sa10_site, east_m=-1_200, north_m=-600)
        ewr_pos = offset(mozdok.position, east_m=-8_000, north_m=-6_000)

        # The corridor is terrain, not intelligence: a road, a pass and a river,
        # all of them on both sides' maps. So it is written down here rather
        # than derived from the estimate the way `TARGET` is — a point that
        # refers to the gorge leaks nothing about where the battery is, and the
        # IP lands at the gorge mouth because that is where the masking stops,
        # not because it is a chosen distance from the site.
        ingress = tuple(
            _Leg(name, Point.from_latlng(LatLng(lat, lng), t), agl)
            for name, lat, lng, agl in _CORRIDOR
        )
        egress = tuple(
            _Leg(name, Point.from_latlng(LatLng(lat, lng), t), agl)
            for name, lat, lng, agl in _EGRESS
        )

        awacs_anchor = offset(vaziani.position, east_m=-25_000, north_m=15_000)
        intrusion_center = offset(beslan.position, east_m=0, north_m=-8_000)

        return _Scene(
            vaziani=vaziani,
            soganlug=soganlug,
            mozdok=mozdok,
            beslan=beslan,
            sa10_site=sa10_site,
            shorad=shorad,
            ewr_pos=ewr_pos,
            ingress=ingress,
            egress=egress,
            awacs_anchor=awacs_anchor,
            intrusion_center=intrusion_center,
            overlay=load_scene("caucasus"),
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """SA-10 radars + launchers, SA-15 SHORAD, ZSU-23-4 AAA, 1L13 EWR."""
        sa10 = self._spawn_sa10_site(m, russia, scene)
        tor = self._spawn_shorad(m, russia, scene.shorad)
        shilkas = self._spawn_shilkas(m, russia, scene.sa10_site)
        ewr = self._spawn_ewr(m, russia, scene.ewr_pos)
        return sa10, tor, shilkas, ewr

    def _spawn_sa10_site(self, m: Mission, russia: Country, scene: _Scene):
        """Full SA-10 site from the pydcs template: SR + TR + CP + launchers.

        Radar and launchers must share the group — an S-300 launcher only
        engages while its track radar is in-group, which the template already
        gets right. A fourth launcher is added because a site guarding the one
        pass through the ridge fields more than three rails.

        Do **not** index into `units` to find a radar here: the template puts a
        paratrooper at index 1. `_add_end_triggers` looks both radars up by
        type through `unit_of_type`.
        """
        # The template registers the group with Russia itself — it takes no
        # country argument, unlike `sa6_site`. Adding it again duplicates the
        # whole site.
        pos = scene.sa10_site
        sa10 = templates.VehicleTemplate.Russia.sa10_site(
            m, pos, 180, prefix="Grumble ", skill=Skill.Excellent
        )
        launcher = m.vehicle("Launcher 4", vehicles.AirDefence.S_300PS_5P85D_ln)
        launcher.position = pos.point_from_heading(180 + 90, 50)
        launcher.heading = 180
        launcher.skill = Skill.Excellent
        sa10.add_unit(launcher)
        # After the fourth rail, or it stays in the template's huddle. An S-300
        # battalion occupies most of a kilometre; the template gives it 100 m,
        # and this one guards the only pass on the route, so it is the site the
        # player has the most reason to try to remove in one pass.
        return ad.disperse_site(
            sa10,
            radius_m=500.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_shorad(self, m: Mission, russia: Country, pos: Point):
        """SA-15 Tor adjacent to the SAM site — terminal SHORAD vs HARM and bombs."""
        tor = m.vehicle_group(
            russia,
            "SAM Tor",
            vehicles.AirDefence.Tor_9A331,
            position=pos,
            heading=180,
        )
        set_skill(tor, Skill.Excellent)
        return tor

    def _spawn_shilkas(self, m: Mission, russia: Country, site: Point):
        """2x ZSU-23-4 inside the SAM perimeter — close-in AAA pop-up coverage."""
        pos = offset(site, east_m=-300, north_m=400)
        shilkas = m.vehicle_group(
            russia,
            "AAA Bear-23",
            vehicles.AirDefence.ZSU_23_4_Shilka,
            position=pos,
            heading=180,
            group_size=2,
        )
        set_skill(shilkas, Skill.High)
        return shilkas

    def _spawn_ewr(self, m: Mission, russia: Country, pos: Point):
        """1L13 EWR on a ridge near Mozdok, feeding GCI to the MiG-29S."""
        ewr = m.vehicle_group(
            russia,
            "EWR Box Spring",
            vehicles.AirDefence.X_1L13_EWR,
            position=pos,
            heading=180,
        )
        set_skill(ewr, Skill.Excellent)
        return ewr

    def _spawn_red_intercept(self, m: Mission, russia: Country, scene: _Scene):
        """2x MiG-29S out of Mozdok, late-activated by blue intrusion zone."""
        intrusion_zone = m.triggers.add_triggerzone(
            position=scene.intrusion_center,
            radius=40_000,
            hidden=True,
            name="MIG intrusion",
        )
        boris = m.intercept_flight(
            russia,
            "Boris",
            planes.MiG_29S,
            airport=scene.mozdok,
            zone=intrusion_zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=900,
            altitude=8000,
            max_engage_distance=110_000,
            group_size=2,
        )
        set_skill(boris, Skill.Excellent)
        apply_ai_difficulty(boris, self.difficulty)
        announce = triggers.TriggerOnce(comment="MiG launch announcement")
        announce.add_condition(
            condition.PartOfCoalitionInZone("blue", intrusion_zone.id)
        )
        mig_call = (
            "Russian MiG-29 airborne from Mozdok, vectoring on the strike package."
        )
        announce.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue, m.string(mig_call), seconds=15
            )
        )
        self._voice.attach_to_coalition(m, announce, mig_call, coalition="blue")
        m.triggerrules.triggers.append(announce)
        return boris

    # -- blue side ----------------------------------------------------------

    def _spawn_awacs(self, m: Mission, usa: Country, scene: _Scene) -> None:
        """E-3A Magic on a Georgia race-track south of the mountains, 251.000 AM."""
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.vaziani,
            position=scene.awacs_anchor,
            race_distance=100_000,
            heading=270,
            altitude=8500,
            speed=740,
            start_type=StartType.Warm,
            frequency=251,
        )

    def _spawn_player(
        self, m: Mission, usa: Country, scene: _Scene, *, plan: PlanOverlay
    ):
        """Dodge F-16C-50 from Vaziani, hot ramp; gorge ingress, Ardon egress."""
        sections = player_flight(
            m,
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.vaziani,
            maintask=task.SEAD,
            start_type=StartType.Warm,
            slots=self.players,
            stores=[
                (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (2, "AIM_9X_Sidewinder_IR_AAM"),
                (3, "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"),
                (4, "Fuel_tank_370_gal"),
                (6, "Fuel_tank_370_gal"),
                (7, "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"),
                (8, "AIM_9X_Sidewinder_IR_AAM"),
                (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (10, "AN_ASQ_213_HTS___HARM_Targeting_System"),
            ],
        )

        overlay = scene.overlay.overlay
        ingress = self._route_altitudes(scene.ingress, overlay)
        egress = self._route_altitudes(scene.egress, overlay)
        # The target steerpoint marks where the battery is *assessed* to be —
        # `plan.estimate`, the same point the map rings and the HSD carries —
        # not where it is. This mission hid every Russian icon, drew its S-300
        # as a vague area and then wrote this steerpoint on the launchers to
        # within 170 m, which the player reads out of the DED before taxiing:
        # the whole reveal policy was undone by the one channel it never
        # looked at. Finding the radars inside the ELINT cut is the sortie, and
        # that is what the HTS and the HARM are aboard for.
        target, _ = plan.estimate(scene.sa10_site, radius=_SA10_RING_M)
        for player in sections:
            self._route_dodge(player, scene, ingress, egress, target)
        route = [
            *(leg.position for leg, _ in ingress),
            target,
            *(leg.position for leg, _ in egress),
        ]
        return sections, route

    def _route_dodge(
        self,
        player: FlyingGroup,
        scene: _Scene,
        ingress: Sequence[tuple[_Leg, float]],
        egress: Sequence[tuple[_Leg, float]],
        target: Point,
    ) -> None:
        """Vaziani → the gorge → TARGET → the Ardon → Vaziani, per section.

        The altitudes and the assessed target are worked out once in
        `_spawn_player` and handed in: they are reads against the elevation
        raster and against the plan's estimate, and two sections deriving them
        separately could fly two different plans under one briefing.
        """
        player.add_runway_waypoint(scene.vaziani)
        for leg, altitude in ingress:
            player.add_waypoint(
                leg.position,
                altitude=altitude,
                speed=_TRANSIT_SPEED_KPH if altitude > 2_500 else _GORGE_SPEED_KPH,
                name=leg.name,
            )
        # The pop altitude is flown off the IP leg above, not written here.
        waypoints.add_ground_waypoint(
            player,
            target,
            overlay=scene.overlay.overlay,
            speed=700,
            name="TARGET",
        )
        # Egress: west off the target, then south up the Ardon and over the
        # Roki Pass. Do NOT re-cross Daryal — the MiG pair is in by then.
        for leg, altitude in egress:
            player.add_waypoint(
                leg.position, altitude=altitude, speed=_EGRESS_SPEED_KPH, name=leg.name
            )
        player.add_runway_waypoint(scene.vaziani)
        player.land_at(scene.vaziani)

    def _route_altitudes(
        self, legs: tuple[_Leg, ...], overlay: MapOverlay
    ) -> list[tuple[_Leg, float]]:
        """Turn each leg's height-above-ground into an altitude that clears rock.

        pydcs waypoint altitudes are metres **AMSL**, and on this map that is
        the whole difficulty: the gorge floor is 800–2400 m and the walls run to
        3700, so a plausible-looking "700 m through the valley" is two and a
        half kilometres of mountain. This mission shipped exactly that. Stating
        the height above the ground and reading the elevation under each point
        is the half of the fix that is obvious.

        The other half is that DCS ramps linearly between two waypoints, so two
        points that each clear their own valley floor still draw a chord through
        the spur the river bends around — which is what the Terek does four
        times between Kobi and Balta. `waypoints.clear_terrain` checks the legs
        as well as the points and lifts the cheaper end of any that would hit,
        so the descent stays a descent and only the ramps that needed it move.
        """
        positions = [leg.position for leg in legs]
        altitudes = waypoints.clear_terrain(
            positions,
            [
                waypoints.ground_elevation_m(overlay, leg.position) + leg.agl_m
                for leg in legs
            ],
            overlay=overlay,
            clearance_m=_LEG_CLEARANCE_M,
        )
        return list(zip(legs, altitudes))

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        Ace: the battery is a target area on the map, not a set of icons —
        the player finds the radars with the HTS and the RWR.
        """
        conceal_country(russia)

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        *,
        route: list[Point],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Vaziani under Hawk, Mozdok under S-125.

        The blue half matters more here than in any other mission in the project.
        `Dodge` flies 168 km alone into an S-300 with four HARMs and no support
        beyond an AWACS track behind the border; the only reason "abort and run"
        is a real option rather than a slower loss is that there is somewhere for
        it to end. Soganlug is 8 km from Vaziani, so it comes free inside the
        same envelope and a jet that cannot fly a normal approach has two
        runways.

        Mozdok gets the red battery because that is where the MiG-29S pair
        recovers — 78 km from the target, so 18 km of S-125 cannot touch the
        SEAD run and can absolutely punish a Viper that follows a withdrawing
        MiG north with two missiles left. Beslan, 12 km from the battery, is the
        field a mission would reach for first and the one `build_sanctuary` would
        refuse: nothing emplaced there clears the objective.

        `keep_clear` on our side is the whole red order of battle, which at 168
        and 245 km is never in question — it is passed anyway so a future change
        to the AO fails loudly instead of quietly switching the mission off.
        On theirs it is the objective plus every flown point of the route,
        including the egress up the Ardon, which is the leg that comes back
        nearest Russian territory.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.vaziani,
            callsign=_SANCTUARY,
            facing=scene.sa10_site,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[scene.sa10_site, scene.shorad, scene.ewr_pos],
            alternates=[scene.soganlug],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        mozdok_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.mozdok,
            callsign="Mozdok field",
            facing=scene.sa10_site,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Mozdok",
            keep_clear=[scene.sa10_site, scene.awacs_anchor, *route],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, mozdok_ad

    def _add_sanctuary_checkin(self, m: Mission, home: sanc.Sanctuary) -> None:
        """Read the umbrella out once, on the climb-out up the Aragvi.

        Without it the feature is invisible: a cyan ring on the F10 map reads as
        decoration, and on this route nobody opens the map again after Pasanauri.
        """
        mission_triggers.checkin(
            m,
            at_seconds=200,
            comment="RAMPART umbrella check-in",
            voice=self._voice,
            text=sanc.checkin_text(home, controller="Magic"),
        )

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        route: list[Point],
        home: sanc.Sanctuary,
        mozdok_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan on the F10 map (ace: the ELINT cut, drawn as a cut).

        Ace withholds the *fix*, not the site: every ring here is drawn several
        kilometres off truth, wider than the system reaches, dashed and
        labelled "(approx.)" — which is exactly what the Intelligence section
        claims to have, two days of cloud and an ELINT cut. The target area and
        the S-300 ring come from one estimate, so the map cannot put the
        battery in two places.

        Drawing the S-300's envelope is what makes the plan legible: it covers
        every approach above the ridgelines, and the low run up Daryal stops
        looking like an eccentric routing choice and starts looking like the
        only way in. The MiG CAP stays a `threat_area` — a fighter pair is not
        an emplaced envelope.

        Returns the two emplaced systems as HSD threat points. The EWR is not
        among them: a search radar has no envelope to fly around.
        """
        # The sanctuary goes on first so its marshal point is the first mark in
        # the cartridge's navigation tab: `core/dtc.py` fills those in draw order
        # after the flight's own route, and on this sortie the one mark a pilot
        # may need with a broken jet should not lose to the AWACS anchor.
        home.draw(plan)
        plan.objective(scene.sa10_site, "TARGET — SA-10", radius=8_000.0)
        plan.route(route, "Dodge ingress (Daryal)")
        plan.waypoint_label(scene.awacs_anchor, "Magic AWACS")
        briefed = [
            *dtc.briefed(
                plan.threat(
                    scene.sa10_site,
                    radius=_SA10_RING_M,
                    label="SA-10",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_10,
                label="SA-10",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.shorad,
                    radius=_TOR_RING_M,
                    label="SA-15 (point defence)",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_15,
                label="SA-15",
            ),
        ]
        plan.threat(
            scene.ewr_pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar
        )
        plan.threat_area(scene.intrusion_center, 30_000.0, "MiG-29S CAP — vicinity")
        # Mozdok's own belt is a red ring like any other, drawn at ace confidence
        # — approximate, and into the cartridge beside the Big Bird. It reaches
        # 18 km and the target is 78 km away, so it costs the SEAD run nothing
        # and costs a chase everything.
        briefed += mozdok_ad.draw(plan)
        return briefed

    def _load_cartridge(
        self,
        m: Mission,
        scene: _Scene,
        points: list[dtc.ThreatPoint],
        *,
        plan: PlanOverlay,
    ) -> None:
        """Put the assessed envelopes on `Dodge`'s HSD, where the map drew them.

        Ace used to load nothing, which sounded right for a picture this thin
        and was not: the player got the battery's position anyway, out of a
        target steerpoint no reveal policy had touched. Two deliberately
        imprecise rings in the cockpit are both more honest and more use — they
        are the same wrong-by-kilometres claim the F10 map makes, carried where
        the player can see it with their head in the pit.

        The same cartridge carries the rest of the plan the F10 map shows: the
        flight's own route and the plan's marks as steerpoints, its lines as the
        HSD's GEO lines. The map and the cockpit are one briefing, drawn from
        one set of positions.
        """
        dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)
        dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(
        self, m: Mission, *, sa10, dodge: Sequence[FlyingGroup]
    ) -> None:
        """Success when both SA-10 radars dead; failure when Dodge dies first.

        Both radars are found by type, not by index: the site comes from
        pydcs's template, whose `units[1]` is a paratrooper. Gating on the two
        radars lets the shot-capable launchers stay in the same group, which an
        S-300 launcher needs in order to fire at all.

        "Dodge is down" is every section down, ANDed: above four coop slots the
        flight is more than one DCS group, and gating the failure call on the
        lead section alone would sound the mission over while the second section
        is still in the gorge.
        """
        big_bird = unit_of_type(sa10, vehicles.AirDefence.S_300PS_64H6E_sr).id
        flap_lid = unit_of_type(sa10, vehicles.AirDefence.S_300PS_40B6M_tr).id
        mission_triggers.message_to_all(
            m,
            comment="SA-10 radars destroyed",
            conditions=(
                condition.UnitDead(big_bird),
                condition.UnitDead(flap_lid),
            ),
            voice=self._voice,
            text=(
                "Magic: Big Bird and Flap Lid are off the air, that battery is "
                "blind. Dodge, egress west and return to base, Vaziani. Do not "
                "re-cross Daryal."
            ),
            seconds=25,
        )

        mission_triggers.message_to_all(
            m,
            comment="Dodge lost before kill",
            conditions=(
                *(condition.GroupDead(group.id) for group in dodge),
                condition.UnitAlive(flap_lid),
            ),
            voice=self._voice,
            text=(
                "Magic: Dodge is down and that battery is still radiating. "
                "The North Caucasus stays closed to us tonight."
            ),
            seconds=25,
        )

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Ingress the Daryal Gorge below 1000 m AGL, destroy the Russian "
            "S-300PS radars (Big Bird SR + Flap Lid TR) south of Beslan, then "
            "egress WEST around the western ridges and RTB Vaziani. Do not "
            "re-cross Daryal on egress — the MiG-29S CAP will be up."
        )
        m.set_description_redtask_text(
            "Hold the S-300PS battery south of Beslan. MiG-29S from Mozdok "
            "intercept any USAF push that comes north up the gorge."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(DaryalRun)


if __name__ == "__main__":
    main()
