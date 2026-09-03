"""Syria 'Ansariyah Works' — F-16C deep strike on a rocket-motor plant, on the deck.

The player flies a USAF F-16C-50 out of **Akrotiri** as `Colt`. The target is a
Syrian solid-rocket-motor plant in a basin on the seaward side of the Jebel
al-Ansariyah, above the village of Al-Ghansala: a casting hall, a motor store
and an oxidiser plant, 279 km east of the field with the whole eastern
Mediterranean in between.

**The ingress is low because the arithmetic says so, not because it reads well.**
A Russian-supplied S-200 sits on the coastal plain behind Jableh, and the 5V28's
own numbers — `H_min = 300 m`, `D_min = 17 km`, `D_max = 240 km`, straight out of
`<DCS>/CoreMods/tech/TechWeaponPack/Database/Weapons/misc_sams.lua` — decide the
whole sortie. It reaches most of the way to Cyprus, so there is no altitude to
cross the sea at; and it cannot bring a missile below three hundred metres, so
there is a floor under it that nothing on this coast can take away. The mission
is the 250 km of water between those two facts, flown at fifty metres.

Note what that premise deliberately is **not**. DCS models no earth curvature, so
"under the horizon" is not a thing a mission may promise over open water: a
coastal radar sees a wave-top jet as far as its detection range reaches, and the
Syrians here *will* see `Colt` cross the beach. What the briefing claims is only
what the game will honour — that they cannot shoot him — and the detection is
load-bearing rather than a leak: it is what rolls the load-out out of the plant
and scrambles the alert pair, so the mission's whole second half is caused by the
player being seen.

Three things layer on top of that, and they are the sortie rather than a target
list:

1. **Two bombs, three aimpoints.** The casting hall is the campaign objective and
   is not negotiable. The second bomb is a choice the briefing lays out and does
   not make: the **motor store**, which is this month's production and only worth
   a bomb while it is still in the building, or the **oxidiser plant**, which is
   next year's and cannot be replaced inside a year. A pair still chooses —
   only one of its two jets carries bombs, the other is the flight's air cover
   — and a four-ship does not have to, which is the one place `--players`
   changes the shape of the mission rather than its size.
2. **The load-out is a clock.** The transporters roll for a tunnel portal 13 km
   up the ridge the moment the coastal radar calls `Colt` feet dry, and past the
   portal they are out of reach. That is what makes the store worth less the
   longer the run takes — and it is what `Chevy`, an F/A-18C pair holding on the
   deck 130 km west of the beach, is for.
3. **Getting out is a third problem.** Hitting the plant is what puts the Hama
   alert pair overhead and releases `Chevy` across a coast that is now awake.
   `Colt` carries four air-to-air missiles and `Eagle` cannot follow him past the
   Gammon's ring, so nothing airborne is a required kill — the frag is the plant.

Two things are deliberately absent from the F10 map, the cartridge and every
friendly flight plan, and both are named as gaps in the briefing rather than
sprung (the rules are in the `dcs-mission` skill):

- **an SA-11 in the Ghab**, 26 km east of the plant. Measured against the
  elevation raster it has no line of sight to any briefed point of the corridor
  or to the target below 2,000 m, and it has line of sight to the target at
  3,000 m — so it is aimed at the climb and at anyone who follows the column
  east, and it cannot touch the plan the player was handed.
- **the guns in the seam.** The gap between the Latakia and Tartus batteries is a
  missile gap, not a defence gap, and the briefing says so without saying where.

Composition (difficulty: veteran):
  - S-200 'Gammon' site behind Jableh — Tin Shield SR + Square Pair TR + 4
    launchers, Skill High, radiating throughout. Briefed at 160 km. Not a target.
  - 2x S-125 coastal batteries, Latakia and Tartus, 71 km apart, Skill Average.
    The 25 km of coast between their briefed rings is the crossing.
  - 1x 55G6 EWR on the coastal ridge above Baniyas, Skill High — the trip wire.
  - 1x SA-8 Osa and 2x ZU-23 at the plant, Skill High / Average.
  - 1x SA-11 Buk in the Ghab, Skill High. On no map and in no cartridge.
  - Syrian load-out: 3x Ural-375 transporters, 2x BTR-80, 1x Strela-10M3,
    late-activated on the coast crossing, road-marching to a tunnel portal.
  - 2-6x MiG-29A alert at Hama, Skill High, scrambled on the coast crossing,
    scaled off the player's own magazine.
  - An S-125 battery with gun sections over Hama, so a MiG that turns for home
    stops being a target.
  - USAF support: E-3A `Magic`, KC-135 `Texaco` (TACAN 10X), F-15C `Eagle`
    TARCAP, F/A-18C `Chevy` on the load-out, and a Patriot battery (`BULWARK`)
    over Akrotiri whose envelope is the sixteen kilometres of sky the tanker
    track lives in — inside our missiles and outside theirs.
  - Weather: spring first light, broken layer at 1,500 m, haze, light NW wind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from dcs import (
    action,
    condition,
    planes,
    statics,
    task,
    templates,
    triggers,
    vehicles,
)
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.point import PointAction
from dcs.terrain.syria.syria import Syria
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, StaticGroup, VehicleGroup

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    kneeboard,
    loadout,
    routing,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.iads import Listener, Site, arm_iads
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MIN_PLAYERS, MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    arm,
    offset,
    player_flight,
    race_track,
    set_skill,
)
from dcs_mission_creator.core.placement import load_scene
from dcs_mission_creator.core.routing import ThreatRing
from dcs_mission_creator.core.tasking import (
    apply_ai_difficulty,
    apply_threat_reaction,
    scramble_on_trigger,
)
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.waypoints import Leg
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene

# -- what the briefing claims each emplaced system reaches --------------------
#
# The Gammon's number is the one that matters, and it is deliberately *not* the
# 240 km its own missile is good for (`D_max` in DCS's weapon table) nor the
# 255 km the F-16C's threat page prints for an SA-5. 160 km is what an intel
# officer credits a fighter-sized target with against that radar, and it is the
# number every other piece of this mission is built from: the descent point, the
# tanker track, the CAP station and the sentence in the ROE. Change it and all
# four move together, which is the point of it being one constant.
_SA5_RING_M = 160_000.0
_SA3_RING_M = 18_000.0
_SA8_RING_M = 10_300.0

#: The Gammon's own floor and the reason this sortie exists in this shape.
#: `H_min = 300.0` for the 5V28 in
#: `<DCS>/CoreMods/tech/TechWeaponPack/Database/Weapons/misc_sams.lua`. The
#: briefed hard deck is well under it rather than just under it, because a jet
#: manoeuvring at 60 m does not hold 60 m.
_GAMMON_FLOOR_M = 300.0
_SEA_DECK_M = 60.0
_LAND_AGL_M = 150.0

#: How far above the ground the flown route has to stay at a waypoint and along
#: every leg between two of them. `core/waypoints.clear_terrain` enforces it.
_LEG_CLEARANCE_M = 120.0

# Commanded true airspeeds, **km/h**, the unit every pydcs speed argument takes
# and none of them names. On an F-16C-50 (`max_speed` 2120) a cruise sits at
# 0.30-0.40 of that; this jet launches at 86 % of max gross with two 370 gal
# bags and two 2,000 lb JDAM, so it flies the bottom of the band, and the deck
# legs are slower again because 700 km/h at 60 m over water is already a lot to
# ask of anybody's crosscheck.
_TRANSIT_SPEED_KPH = 800.0
_TANKER_SPEED_KPH = 750.0
_SEA_DECK_SPEED_KPH = 700.0
_LAND_SPEED_KPH = 650.0
_EGRESS_SPEED_KPH = 750.0

# Radios and the TACAN. Both briefings quote all of these, so they are constants
# rather than literals at the call site.
_FREQ_AWACS = 251
_FREQ_TANKER = 270
_TANKER_TACAN = "10X"

# Akrotiri's own air defence, and the one place in this project where a Patriot
# is the right answer rather than the greedy one. The AO is 279 km away, so
# 100 km cannot reach anything the Syrians need left standing; what it *can* do
# is hold every friendly station in the package at once — the AWACS track, the
# tanker track, the CAP station and Paphos — and the sixteen kilometres between
# its own edge and the Gammon's briefed ring is the only sky in this mission
# that is inside our missiles and outside theirs. That band is where `Texaco`
# and `Eagle` live, and it is why they cannot come with you.
_SANCTUARY = "BULWARK"
_SANCTUARY_BATTERY = sanc.PATRIOT

#: The load-out has rolled and the alert pair is airborne. One flag, because both
#: are the same event — the coastal radar calling `Colt` feet dry.
_FLAG_COAST_CROSSED = 30
#: The plant has been hit, which is what releases `Chevy` onto the column.
_FLAG_PLANT_STRUCK = 31

# The sortie's own clock, in mission seconds, set against the route rather than
# guessed. Akrotiri to the tanker is 55 km, so the AR track is worth calling at
# about four minutes; the umbrella check-in goes just after it, on the climb, and
# both land well before the descent at T+13 when the player stops having spare
# attention.
_TANKER_CHECKIN_S = 260
_SANCTUARY_CHECKIN_S = 380

#: Road-march speed for a column of loaded transporters on a mountain road. With
#: 12.6 km to run that is the tunnel portal about twenty-two minutes after the
#: coastal radar calls the crossing, and the player is four minutes from the
#: target when it starts — so the store is worth a bomb if the run went well and
#: is worth less if it did not. That is the clock, and no trigger states it.
_COLUMN_SPEED_KPH = 35

#: Where the plant sits and what stands on it, as offsets in metres from the
#: basin centre. A rocket-motor plant is a compound rather than a building, and
#: the three aimpoints have to be far enough apart that one 2,000 lb bomb cannot
#: take two of them — 300 m is beyond a Mk 84's lethal radius against structures
#: by a comfortable margin, which is what makes the second bomb a decision.
_HALL_OFFSET = (0.0, 0.0)
_STORE_OFFSET = (-320.0, 260.0)
_OXIDISER_OFFSET = (260.0, 340.0)

#: The whole layout in degrees rather than DCS metres, because every one of these
#: is a real place that can be checked against a map — the lesson `daryal_run`
#: paid for with two waypoints inside a mountainside. `(lat, lng)`.
_PLANT_LATLNG = (35.1640, 36.0860)  # basin above Al-Ghansala, 292 m, no relief
_SA5_LATLNG = (35.4000, 36.0000)  # coastal plain behind Jableh, 103 m
_SA3_NORTH_LATLNG = (35.5200, 35.8100)  # Latakia
_SA3_SOUTH_LATLNG = (34.9000, 35.9200)  # Tartus
_EWR_LATLNG = (35.2600, 36.0000)  # coastal ridge above Baniyas, 283 m
_SA11_LATLNG = (35.1000, 36.3600)  # the Ghab, east of the range, 428 m
_PORTAL_SEED_LATLNG = (35.1500, 36.2300)  # the tunnel road, snapped at build

#: The ingress corridor, `(name, lat, lng, height above the ground)`. It crosses
#: the coast north of Baniyas, 36.1 km from the Latakia battery and 34.4 km from
#: the Tartus one — **18.1 and 16.4 km outside what those systems actually
#: reach**, which is the margin that decides whether a player who complies with
#: the briefing lives. On the map the margin looks tighter (9.7 and 8.2 km),
#: because the veteran reveal draws each ring wider than the system and a few
#: kilometres off truth; that gap is the reveal policy working, and the map being
#: the pessimistic one is the right way round.
#:
#: Measured against the elevation raster: everything after the beach is masked
#: from the Gammon, everything after `ALQIN` from the Baniyas EWR, the Osa on the
#: works cannot see the corridor until the IP at 4.2 km, and no point of it is
#: visible to the unlocated Gadfly at any altitude the briefing asks for.
#:
#: The AGL column is the mission. 60 m over the water is a floor chosen against
#: the Gammon's own 300 m, not against a radar horizon DCS does not model.
_CORRIDOR = (
    ("FEET DRY", 35.2100, 35.9300, _SEA_DECK_M),
    ("ALQIN", 35.1450, 35.9750, _LAND_AGL_M),
    ("TANITA", 35.1050, 36.0350, _LAND_AGL_M),
    ("IP", 35.1200, 36.0850, _LAND_AGL_M),
)

#: Egress, and it is deliberately not the way in. The ingress was about not being
#: seen and cost 25 km of weaving; by the time the bombs are off, the coastal
#: radar has had the flight for four minutes and the alert pair is airborne, so
#: the only thing worth buying is water. This is the shortest line from the basin
#: to the sea — 16 km, still under the Gammon's floor the whole way.
_EGRESS = (
    ("EGRESS", 35.1850, 35.9900, _LAND_AGL_M),
    ("FEET WET", 35.2000, 35.9150, _SEA_DECK_M),
)


@dataclass
class _Scene:
    """Resolved airports + the whole fixed geometry, shared by every spawn step."""

    akrotiri: Airport
    paphos: Airport
    hama: Airport
    plant: Point
    sa5_pos: Point
    sa3_north: Point
    sa3_south: Point
    ewr_pos: Point
    sa8_pos: Point
    sa11_pos: Point
    portal: Point
    ingress: tuple[Leg, ...]
    egress: tuple[Leg, ...]
    overlay: TacticalScene

    @property
    def feet_dry(self) -> Point:
        """The coast crossing — the seam between the two coastal batteries."""
        return self.ingress[0].position

    @property
    def ip(self) -> Point:
        """The last corridor point, south of the basin: the run-in starts here."""
        return self.ingress[-1].position


@dataclass
class _Plant:
    """The three aimpoints, so the trigger step stops unpacking a tuple.

    Named rather than indexed for the reason `mission_kit.unit_of_type` exists:
    an objective that means "the casting hall" should say so, and the end
    triggers here read three different sentences off these three statics.
    """

    hall: StaticGroup
    store: StaticGroup
    oxidiser: StaticGroup


@dataclass
class _RedGround:
    """The Syrian ground picture, in the order the sortie meets it."""

    sa5: VehicleGroup
    sa3_north: VehicleGroup
    sa3_south: VehicleGroup
    ewr: VehicleGroup
    sa8: VehicleGroup
    #: The Buk in the Ghab. On no map, in no cartridge, in no friendly route.
    unfixed: VehicleGroup
    plant: _Plant
    column: VehicleGroup


#: How `Colt` splits the frag across its slots (`core/loadout.py`).
#:
#: The mission's own tension is **two bombs against three aimpoints**, spaced so
#: that one pass cannot take all three, and that has to survive the flight
#: growing. So the second jet is not a second bomber: it is the flight's air
#: cover, and this is the one mission in the project where that is a briefing
#: fact rather than a convenience. `Eagle` holds in a sixteen-kilometre band
#: between our own Patriot and the Gammon's ring and **cannot follow the strike
#: in** — from the letdown to feet wet the flight is the only friendly airborne
#: thing east of that band, and the coastal EWR is briefed to call the crossing
#: and scramble the alert pair.
#:
#: A four-slot flight puts a second bomber up and can take all three aimpoints;
#: a pair chooses, which is the sortie as written.
#:
#: Both are ED payloads station for station, off
#: `<DCS>/CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua`:
#: `AIM-120C*2, AIM-9X*2, GBU-31-3B*2, FUEL*2, ECM, TGP` and
#: `AIM-120C*4, AIM-9X*2, FUEL*2, ECM, TGP`. No laser anywhere in the flight —
#: there is no altitude over this basin from which a jet could hold a spot and
#: live, which is why the bombs are satellite-aided.
_FITS = (
    loadout.Loadout(
        role="GBU-31(V)3/B",
        carries=(
            "two GBU-31(V)3/B 2,000 lb penetrators, LITENING pod, "
            "two AIM-120C, two AIM-9X, ALQ-184, two 370 gal"
        ),
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_9X_Sidewinder_IR_AAM"),
            (3, "GBU_31_V_3_B___JDAM__2000lb_GPS_Guided_Penetrator_Bomb"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "GBU_31_V_3_B___JDAM__2000lb_GPS_Guided_Penetrator_Bomb"),
            (8, "AIM_9X_Sidewinder_IR_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
        ),
    ),
    loadout.Loadout(
        role="AIM-120C*4",
        carries=(
            "four AIM-120C, two AIM-9X, LITENING pod, ALQ-184, two 370 gal — "
            "the only cover east of the band"
        ),
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (3, "AIM_9X_Sidewinder_IR_AAM"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "AIM_9X_Sidewinder_IR_AAM"),
            (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
        ),
    ),
)


class AnsariyahWorks(MissionBuilder):
    name = "ansariyah_works"
    title = "Ansariyah Works"
    difficulty = Difficulty.VETERAN

    def __init__(self, *, players: int = MIN_PLAYERS) -> None:
        super().__init__(players=players)
        self._terrain = Syria()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""ANSARIYAH WORKS — Syria, 3 April 2026, 06:10 local
====================================================
SITUATION
  A Syrian solid-rocket-motor plant is running in a
  basin on the seaward side of the Jebel al-Ansariyah,
  above Al-Ghansala. Three buildings matter: a casting
  hall, a motor store and an oxidiser plant. Command
  wants the hall down this morning.

  The plant is 279 km east of Akrotiri and every metre
  of that is water. A Russian-supplied S-200 battery on
  the plain behind Jableh has been radiating for weeks
  and reaches most of the way back to Cyprus. There is
  no altitude to cross in.

  What that battery cannot do is bring a missile below
  300 m. That is the whole plan: you go the width of the
  eastern Mediterranean at 60 m over the water and you
  do not climb. They will see you — assume they do — but
  seeing you and reaching you are different problems.

MISSION (Colt — F-16C-50, Akrotiri, hot ramp)
  - Climb east and take your gas from Texaco.
  - Start down at LETDOWN and be level at DECK. Those
    four minutes are the exposed part of the outbound:
    you are inside the Gammon's envelope and above its
    floor the whole way down, and there is no profile
    that avoids it. Do not stretch it.
  - Cross the coast north of Baniyas, between the
    Latakia and Tartus batteries. That gap is missiles
    only; there are guns under it. Do not linger on it.
  - Work inland behind the coastal hills to the IP, then
    north into the basin.
  - TWO BOMBS, THREE AIMPOINTS. The casting hall is the
    mission and is not a choice. The second is:
      MOTOR STORE   — this month's motors, and only
                      while they are still in it.
      OXIDISER PLANT — next year's, and a year to
                      replace.
  - Egress WEST to the water, the short way, still on
    the deck. Climb only when you are outside the ring.
  - RTB Akrotiri. Divert: Paphos.

LOADOUT (one bomber, one escort)
{self.loadout_brief("Colt", _FITS)}
  Two bombs, three aimpoints. Slot 2 carries no bomb: it
  is the only cover you have east of the band.

PACKAGE
  Colt         : F-16C-50 pair, Akrotiri. Loadout above.
  Magic        : E-3A AWACS, 251.000 AM, over Cyprus.
  Texaco       : KC-135, 270.000 AM, TACAN 10X, on the
                 line between our Patriot and their
                 Gammon. Plan on him twice.
  Eagle        : F-15C pair, TARCAP on the same band.
                 He cannot follow you past the ring.
  Chevy        : F/A-18C pair holding on the deck 130 km
                 west of the beach, fragged against the
                 plant's load-out. He goes when you have
                 put the hall down.

INTELLIGENCE
  No overhead of the basin — the pass was cloud and the
  next one is tomorrow. What we have is three weeks of
  emitter work and partner-force reporting on the plant
  itself, so every ring on your map is drawn wide,
  dashed and marked approximate. Your steerpoints are
  the same assessment, not a survey.
  SAM : S-200 behind Jableh. Assess 160 km against a
        fighter. Floor 300 m — that is the number this
        sortie is built on. It is NOT your target.
  SAM : two S-125 batteries, Latakia and Tartus, about
        18 km each. Your crossing is the water between
        them.
  SAM : short-range cover on the plant itself, Osa
        class, and guns with it.
  EWR : search radar on the ridge above Baniyas. It
        will call you across the beach. Expect the
        alert pair to launch off that call and expect
        the load-out to start rolling on it too.
  GAP : a Gadfly-class emitter came up east of the
        range twice last week and we never fixed it.
        There is no ring on your map because we would
        be drawing a guess. It cannot see the corridor
        or the basin from where we think it is. It can
        see anything that climbs, and it can see the
        road east of the portal. Stay low, do not
        follow the column inland.
  Air : Hama holds the alert pair for this sector,
        MiG-29A, experienced. Hama is defended in its
        own right.

ROE / FRAGS
  - Weapons free on the plant, on its own air defence
    and on the load-out.
  - HARD DECK IS A HARD FLOOR: 60 m over water, 150 m
    over land, from the letdown to feet wet. Above 300 m
    inside the ring you are a target and there is no
    manoeuvre that fixes it.
  - The Gammon is not fragged. Two bombs do not open an
    S-200 and you do not have the fuel to argue with it.
  - Do not follow the column past the tunnel portal.
  - Not cleared to pursue over Hama.
  - Nothing airborne is a required kill. The plant is
    the frag.
  - Bingo fuel: 3000 lb at feet wet. Texaco is where you
    left him.

FALL-BACK ({_SANCTUARY})
  Akrotiri sits under a {_SANCTUARY_BATTERY.name} battery —
  {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, cyan ring on the map, guns in the
  overhead. Paphos is inside the same envelope. That
  ring and the Gammon's very nearly touch, and the sky
  between them is where Texaco and Eagle are holding:
  cross it westbound and the sortie is over.
  {_SANCTUARY} MARSHAL is a hold abeam Akrotiri, on the map
  and in the DED.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  TEXACO    : the AR track, and the same point on
              the way home.
  LETDOWN   : the briefed edge of the ring. Start down.
  DECK      : level at sixty metres. Stay there.
  FEET DRY  : the crossing, north of Baniyas.
  ALQIN     : first turn inland, behind the hills.
  TANITA    : south leg, masked from the EWR.
  IP        : south of the basin. Run in north.
  TARGET    : the works. Assessed, not surveyed.
  EGRESS    : straight out west.
  FEET WET  : water. Still on the deck.
  CLIMB     : the ring edge westbound. Climb FROM here,
              not before — the whole egress is on the
              deck until this point.
  DESCENT   : let-down for Akrotiri.

FREQUENCIES
  Magic AWACS    : 251.000 AM
  Texaco tanker  : 270.000 AM, TACAN 10X
  Akrotiri tower : per kneeboard

NOTES
  Sunrise about 06:25 — you cross the beach in the last
  of the half-light with the sun coming up behind the
  target. Broken layer at 1,500 m, haze underneath. The
  layer costs you nothing: the bombs are satellite-aided
  and the aimpoints are surveyed onto the cartridge.
  Expect roughly 80 minutes. Take the gas.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Ansariyah Works

**Theater:** Syria
**Date / time:** 3 April 2026, 06:10 local (first light)
**Player aircraft:** F-16C-50 (`Colt`), Akrotiri, hot ramp
**Players:** {self.slot_summary("Colt")}
**Difficulty:** veteran
**Expected sortie length:** ~80 minutes

## Situation

A Syrian solid-rocket-motor plant is running in a basin on the seaward side of
the **Jebel al-Ansariyah**, above the village of Al-Ghansala. Three buildings
matter: the **casting hall**, the **motor store** and the **oxidiser plant**.
Command wants the hall down this morning.

The plant is **279 km east of Akrotiri** and every metre of that is water. A
Russian-supplied **S-200** battery on the coastal plain behind Jableh has been
radiating for weeks and reaches most of the way back to Cyprus, so there is no
altitude to cross the sea at.

What that battery cannot do is bring a missile below **300 m**. That is the
whole plan, and it is the reason this sortie looks the way it does: you fly the
width of the eastern Mediterranean at sixty metres, you cross the coast in the
one stretch the short-range batteries cannot reach, and you do not climb.

**They will see you.** Assume the coastal radar calls you across the beach —
that is what it is for, and the mission is built on the assumption that it
works. Seeing you and reaching you are different problems, and the second one is
the only one you can do anything about.

## Mission

Climb east out of Akrotiri and take gas from `Texaco` on the way out. Start
down at `LETDOWN`, on the briefed edge of the Gammon's ring, and be level at
sixty metres by `DECK`.

**Those four minutes are the exposed part of the outbound and no profile avoids
them.** The ring is 160 km wide and the tanker has to sit outside it, so there
is no altitude at which the descent can be flown clear — you come off `Texaco`,
cross the edge and go down through their envelope. Fly it at idle, do not
stretch it, and understand that this is the price of not swimming home. From
`DECK` to `FEET WET` you are under their floor and they have nothing that
reaches you.

Cross the coast north of Baniyas, work inland behind the coastal hills, and run
into the basin from the south.

**Two bombs, three aimpoints.** The **casting hall** is the campaign objective
and is not a choice — it is what makes motors, and until it is gone nothing else
here matters. The second bomb is yours:

| Aimpoint | What it costs them | When it is worth a bomb |
|----------|--------------------|-------------------------|
| **Motor store** | This month's finished motors | Only while they are still inside. The transporters roll as soon as you are called across the beach. |
| **Oxidiser plant** | Next year's production — a year to replace | Always. It is the slower and larger loss, and it is the one that does not care how long your run took. |

A pair still has to choose: only slot 1 carries bombs, and slot 2 is the cover
that lets it get to the basin. Four slots put a second bomber up and the choice
goes away. Choose from what you can actually see through the pod on the way in.

Egress **west** to the water — the short way, still on the deck — and climb only
when you are outside the ring. RTB Akrotiri; divert Paphos.

## Package

| Callsign | Type     | Base     | Role                                        |
|----------|----------|----------|---------------------------------------------|
| Colt     | F-16C-50 | Akrotiri | Player strike pair — one bomber, one escort  |
| Magic    | E-3A     | Akrotiri | AWACS + ESM watch, 251.000 AM                |
| Texaco   | KC-135   | Akrotiri | Tanker, 270.000 AM, TACAN 10X                |
| Eagle    | F-15C x2 | Akrotiri | TARCAP over the tanker and the recovery      |
| Chevy    | F/A-18C x2 | Akrotiri | Held on the deck west of the beach, fragged against the plant's load-out |

`Eagle` holds on the same band as the tanker and **cannot follow you in**. The
sky he is holding in is about sixteen kilometres wide: inside `BULWARK`'s
envelope and outside the Gammon's briefed ring. That is not a scripting
convenience, it is the geometry of this coast, and it is why the flight is on
its own from the letdown to feet wet.

### `Colt` loadout

{self.loadout_table("Colt", _FITS)}

**Two bombs against three aimpoints** is the sortie, and it stays the sortie:
the second jet is not a second bomber. It is the only friendly cover east of
that sixteen-kilometre band — the coastal radar is briefed to call your crossing
and the alert pair launches off that call, and `Eagle` cannot come. A four-slot
flight puts a second bomber up and can take all three aimpoints; a pair chooses.

No laser anywhere in the flight. There is no altitude over this basin from which
a jet could hold a spot and live, which is why the bombs are satellite-aided and
why the aimpoints have to be surveyed onto the cartridge before start.

## Intelligence

No overhead of the basin — the pass was cloud and the next one is tomorrow. What
we have is three weeks of emitter work and partner-force reporting on the plant,
so **every ring on your map is drawn wide, dashed and marked approximate**, and
so is the pre-planned pair on your HSD. Your `TARGET` steerpoint is that same
assessment rather than a survey: it puts you over the basin, not over the hall.

- **S-200 'Gammon' (the premise):** on the plain behind Jableh. Assess 160 km
  against a fighter-sized target at altitude, and a **300 m floor** it cannot
  shoot below. That ring is on your map and it covers your entire route from
  about a hundred kilometres east of Akrotiri to the beach. It is **not** your
  target: two bombs do not open an S-200 and you do not have the fuel to argue
  with it.
- **S-125 x2:** Latakia and Tartus, about 18 km each. They are the reason the
  crossing is where it is — there are twenty-five kilometres of coast between
  their rings and you are briefed into the middle of it.
- **The seam is a missile gap, not a defence gap.** There are guns on that
  stretch. Nobody has fixed them and nobody is going to; cross it fast and once.
- **Plant defences:** an Osa-class system on the works and guns with it. Ringed
  on your map at the same confidence as everything else.
- **EWR:** search radar on the ridge above Baniyas. Expect it to call you across
  the beach, expect the alert pair to launch off that call, and expect the
  load-out to start rolling on it as well.
- **Gap — the Gadfly:** a Gadfly-class emitter came up east of the range twice
  last week and we never got a fix on it. There is **no ring on your map because
  we would be drawing a guess.** From where we think it is, it cannot see the
  corridor or the basin at the heights you are briefed to fly. It can see
  anything that climbs, and it can see the road east of the portal. Stay low and
  do not follow the column inland.
- **Air:** Hama holds the alert pair for this sector — MiG-29A, experienced
  crews. Hama is defended in its own right; a battery on the field and guns in
  the overhead.

## The load-out

The plant runs a load-out at first light: motor transporters with an escort and
their own short-range cover, taking finished motors to a hardened tunnel portal
about thirteen kilometres up the ridge. Past the portal they are out of reach and
we are not going to dig them out.

They roll on the same call that scrambles the MiGs. That is what puts a clock on
the **motor store** and it is what `Chevy` is for — an F/A-18C pair holding on
the deck a hundred and thirty kilometres west of the beach, who will run the
column on the road once you have put the hall down.

## ROE

- Weapons free on the plant, its own air defence, and the load-out.
- **The hard deck is a hard floor:** 60 m over water, 150 m over land, from
  `LETDOWN` to `FEET WET`. Above 300 m inside the ring you are a target, and
  there is no manoeuvre that fixes that.
- **The Gammon is not fragged.**
- **Do not follow the column past the tunnel portal.**
- **Not cleared to pursue over Hama.**
- **Nothing airborne is a required kill.** Four missiles and no wingman below
  three slots: the alert pair is a threat to survive, not a target list. The
  plant is the frag.
- Bingo fuel: 3000 lb at feet wet.

## Fall-back

Akrotiri is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10 map, with gun sections in
the overhead. **Paphos is inside the same envelope.**

That ring and the Gammon's briefed ring very nearly touch, and the band between
them is where `Texaco` and `Eagle` are holding. Westbound, crossing it is the end
of the sortie: an S-200 cannot follow you into it and a MiG that does is inside a
Patriot. `{_SANCTUARY} MARSHAL` is a hold abeam Akrotiri, on the map and in the
DED, for a jet waiting on the pattern.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- `TEXACO` — the AR track, and where you take gas again on the way home
- `LETDOWN` — the briefed edge of the Gammon's ring. **Start down here**
- `DECK` — level at sixty metres, 80 km short of the beach
- `FEET DRY` — the crossing, north of Baniyas, between the two coastal batteries
- `ALQIN` — first turn inland, behind the coastal hills
- `TANITA` — the south leg; masked from the Baniyas radar from here
- `IP` — south of the basin. Run in to the north
- `TARGET` — the works. Assessed, not surveyed
- `EGRESS` — straight out west off the target
- `FEET WET` — water, still on the deck
- `CLIMB` — the ring edge westbound. Climb **from** here and not before: the
  whole egress from `FEET WET` is flown on the deck, and the card shows it
- `DESCENT` — let-down for Akrotiri

The heights in the briefing are above the *ground*. Your kneeboard prints
altitudes, which is a different and always larger number, and the profile on it
clears the terrain on every leg including the ones between waypoints. Fly the
card.

## Frequencies

- Magic AWACS: 251.000 AM
- Texaco tanker: 270.000 AM, TACAN 10X
- Akrotiri tower: per kneeboard
- `{_SANCTUARY}` details and the Paphos divert are on the kneeboard comms card.

## Weather

Spring first light. Broken layer base 1,500 m, 700 m thick, density 6.
Visibility 20 km in haze. 14 °C. Light NW wind — 5 m/s ground, 9 m/s at 2,000 m,
16 m/s at 8,000 m. Sunrise about 06:25: you cross the beach in the last of the
half-light with the sun coming up behind the target.

The layer costs you nothing. The bombs are satellite-aided and the aimpoints are
on the cartridge — which is *why* you are carrying JDAM on a target you could
otherwise have lased. There is no altitude here from which you could self-lase
and live.

## Difficulty composition

**Veteran.** S-200 denying every usable altitude over a 279 km over-water
ingress, two coastal S-125 batteries defining the only crossing, an Osa and guns
on the objective, an unlocated Gadfly aimed at the climb, MiG-29A alert scaled
off the player count, a hard fuel case that makes the tanker structural, escort
that cannot cross the ring, first light and a broken layer. Enemy positions are
drawn as assessments, several kilometres off truth.

## Win / loss conditions

- **Success:** the casting hall is down. What else went with it — the month's
  motors, next year's oxidiser, the column on the road — is the difference
  between a good morning and a very good one.
- **Failure:** `Colt` goes down with the hall still standing.

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
        # The overlay is built before anything flies, because the *flight plan*
        # is derived from it: the letdown and climb points are where the briefed
        # Gammon ring begins, and the target steerpoint is where the plant is
        # assessed to be. Both are claims this object owns, and building it here
        # is what stops the route carrying a better position than the briefing.
        plan = PlanOverlay(m, self.difficulty)
        scene = self._setup_airports(m)
        usa, syria, russia = m.country("USA"), m.country("Syria"), m.country("Russia")

        red = self._spawn_red_ground(m, syria, russia, scene)
        migs = self._spawn_red_alert_fighters(m, syria, scene)
        belts = self._threat_rings(scene)

        magic, awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        tarcap_track = self._spawn_tarcap(m, usa, scene)
        chevy = self._spawn_strike(m, usa, scene, column=red.column, threats=belts)
        colt, route = self._spawn_player(m, usa, scene, plan=plan)

        home, hama_ad = self._spawn_sanctuaries(m, usa, syria, scene, route=route)
        sanc.remark_all(m, home, hama_ad)
        kneeboard.remark(
            m,
            f"Hard deck east of LETDOWN: {_SEA_DECK_M:.0f} m over water, "
            f"{_LAND_AGL_M:.0f} m AGL inland.",
        )

        self._add_iads(m, magic=magic, red=red, hama_ad=hama_ad)
        self._add_intro_voice(m)
        self._add_support_checkins(m)
        sanc.announce(m, home, at_seconds=_SANCTUARY_CHECKIN_S, voice=self._voice)
        self._add_coast_crossing_triggers(
            m, scene, colt=colt, migs=migs, column=red.column
        )
        self._add_strike_release_triggers(m, plant=red.plant, chevy=chevy)
        self._add_end_triggers(m, scene, red=red, colt=colt)

        self._conceal_red(syria, russia)
        briefed_threats = self._draw_plan(
            m,
            scene,
            plan=plan,
            route=route,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
            tarcap_track=tarcap_track,
            home=home,
            hama_ad=hama_ad,
        )
        self._load_cartridge(m, scene, briefed_threats, plan=plan)
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, geometry --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """06:10 map-local on 3 April 2026 — the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want. Sunrise
        on this coast is about 06:25, which puts the deck run in the last of the
        half-light and the run-in into a rising sun.
        """
        m.start_time = datetime(2026, 4, 3, 6, 10, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Spring first light: broken layer at 1,500 m, haze, light NW wind."""
        Weather(
            name="Spring first light",
            season_temperature=14.0,
            clouds_base=1500,
            clouds_thickness=700,
            clouds_density=6,
            visibility_distance=20000,
            wind_at_ground=Wind(300, 5),
            wind_at_2000=Wind(290, 9),
            wind_at_8000=Wind(280, 16),
        ).apply(m)

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Akrotiri and Paphos for blue, Hama for red, resolve the geometry.

        Every enemy position and every corridor point is a `(lat, lng)` constant
        rather than a search, and that is deliberate rather than lazy. The whole
        mission is a set of measured claims about this coast — that the crossing
        is fourteen kilometres outside the Latakia battery's briefed ring, that
        the corridor is masked from the Gammon after the beach, that the Gadfly
        cannot see the basin below two thousand metres — and a sampled placement
        would re-roll all of them on any change to the seed. Degrees also mean
        each one can be checked against a real map, which is how the AGL column
        of `_CORRIDOR` stopped being a guess.

        The one thing still snapped at build time is the tunnel portal, because
        the only property that matters about it is that it is on the road the
        column actually drives, and that is a question for the road layer.
        """
        t = self._terrain
        akrotiri = t.airports["Akrotiri"]
        paphos = t.airports["Paphos"]
        hama = t.airports["Hama"]
        akrotiri.set_blue()
        paphos.set_blue()
        hama.set_red()

        overlay = load_scene("syria")
        plant = self.at(*_PLANT_LATLNG)
        return _Scene(
            akrotiri=akrotiri,
            paphos=paphos,
            hama=hama,
            plant=plant,
            sa5_pos=self.at(*_SA5_LATLNG),
            sa3_north=self.at(*_SA3_NORTH_LATLNG),
            sa3_south=self.at(*_SA3_SOUTH_LATLNG),
            ewr_pos=self.at(*_EWR_LATLNG),
            # The Osa sits on the seaward lip of the basin, which is the side
            # every briefed approach comes from and the side the guns cannot
            # cover on their own.
            sa8_pos=offset(plant, east_m=-1_600, north_m=-900),
            sa11_pos=self.at(*_SA11_LATLNG),
            portal=overlay.overlay.find_road_spawn(
                self.at(*_PORTAL_SEED_LATLNG),
                radius_m=6_000.0,
                min_distance_to_built_up_m=200.0,
            ),
            ingress=tuple(
                Leg(name, self.at(lat, lng), agl) for name, lat, lng, agl in _CORRIDOR
            ),
            egress=tuple(
                Leg(name, self.at(lat, lng), agl) for name, lat, lng, agl in _EGRESS
            ),
            overlay=overlay,
        )

    @staticmethod
    def _ring_edge(
        origin: Point,
        toward: Point,
        center: Point,
        radius_m: float,
        *,
        margin_m: float = 8_000.0,
    ) -> Point:
        """The last point on `origin` → `toward` still clear of a drawn ring.

        This is what the letdown and climb steerpoints are, and deriving them
        rather than typing them is the rule the whole reveal policy rests on:
        **every planned point that refers to an enemy site derives from the
        estimate, never from the site.** Feed it `PlanOverlay.estimate`'s pair
        and the descent moves outward as the difficulty coarsens the ring, which
        is correct — a wider claim is a longer run on the deck, and that is the
        cost of a thinner picture rather than an inconsistency to paper over.
        """
        heading = origin.heading_between_point(toward)
        total = origin.distance_to_point(toward)
        edge = origin
        step = 1_000.0
        walked = 0.0
        while walked <= total:
            candidate = origin.point_from_heading(heading, walked)
            if candidate.distance_to_point(center) < radius_m + margin_m:
                break
            edge = candidate
            walked += step
        return edge

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(
        self, m: Mission, syria: Country, russia: Country, scene: _Scene
    ) -> _RedGround:
        """The whole Syrian ground picture, in the order the sortie meets it.

        Two countries, and the split is the briefing's: the long-range battery
        and the unlocated Gadfly are Russian-supplied and Russian-crewed, which
        is what the Intelligence section says and what makes their skill levels
        different from the coastal batteries' conscripts.
        """
        sa5 = self._spawn_sa5_site(m, russia, scene)
        sa3_north, sa3_south = self._spawn_coastal_belts(m, syria, scene)
        ewr = self._spawn_ewr(m, syria, scene)
        plant = self._spawn_plant(m, syria, scene)
        sa8 = self._spawn_plant_shorad(m, syria, scene)
        unfixed = self._spawn_unfixed_sam(m, russia, scene)
        column = self._spawn_loadout(m, syria, scene)
        return _RedGround(
            sa5=sa5,
            sa3_north=sa3_north,
            sa3_south=sa3_south,
            ewr=ewr,
            sa8=sa8,
            unfixed=unfixed,
            plant=plant,
            column=column,
        )

    def _spawn_sa5_site(self, m: Mission, russia: Country, scene: _Scene):
        """The S-200 behind Jableh: Tin Shield SR + Square Pair TR + 4 launchers.

        The premise of the mission, and the one site here the player is told not
        to attack. It sits on the coastal plain rather than on a summit because
        that is where an S-200 battalion goes — it needs road access for
        transporter-loaders and a clear horizon, not height — and 9 km inland of
        Jableh gives it an uninterrupted look west over the whole approach.

        Four launchers and `Skill.High`: this is the crew the briefing calls
        Russian-supplied, and unlike everything else on this coast it is
        radiating from the moment the mission loads (see `_add_iads`), because
        that is what the Intelligence section claims and what the player's RWR
        should confirm on the runway.
        """
        site = ad.build_sa5_site(
            m,
            russia,
            scene.sa5_pos,
            heading=270,
            launchers=4,
            prefix="Gammon ",
            skill=Skill.High,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        site.name = "SAM Gammon"
        return site

    def _spawn_coastal_belts(self, m: Mission, syria: Country, scene: _Scene):
        """Two S-125 batteries, Latakia and Tartus, and the seam between them.

        69.5 km apart, so their 18 km envelopes leave 33.5 km of coast uncovered.
        The crossing in `_CORRIDOR` sits in the middle of it, 18.1 km outside
        what the northern battery reaches and 16.4 km outside the southern one —
        which is the margin the design rules ask for, and the one that has to be
        measured against the *real* envelope rather than the drawn one: a player
        who complies with the briefing and dies read a briefing that lied.

        On the F10 map the same crossing looks tighter, 9.7 and 8.2 km clear,
        because the veteran reveal inflates each ring by a quarter and offsets it
        four kilometres. That asymmetry is the right way round — the map is
        pessimistic and the ground truth is generous — and it is worth knowing
        which of the two numbers a seam check is being made against.

        `Skill.Average` on both. These are the conscript end of this coast — it
        is why `_add_iads` gives them the longest emission looks in the net, and
        why the briefing calls the seam a *missile* gap rather than a safe one.
        """
        north = ad.build_sa3_site(
            m,
            syria,
            scene.sa3_north,
            heading=270,
            prefix="Latakia ",
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        north.name = "SAM Latakia"
        south = ad.build_sa3_site(
            m,
            syria,
            scene.sa3_south,
            heading=270,
            prefix="Tartus ",
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        south.name = "SAM Tartus"
        self._spawn_seam_guns(m, syria, scene)
        return north, south

    def _spawn_seam_guns(self, m: Mission, syria: Country, scene: _Scene) -> None:
        """Two ZU-23 sections on the beach the crossing runs over.

        The seam is a *missile* gap and the briefing says so without saying
        where, which is the whole reason these exist: a corridor with nothing on
        it at all would make "cross fast and once" advice with no consequence
        behind it. They get no ring and no cartridge point — a towed gun has no
        envelope worth a steerpoint — and at 60 m they are a real problem for
        exactly as long as it takes to fly past them.
        """
        for i, along in enumerate((-3_500.0, 3_500.0)):
            pos = offset(scene.feet_dry, east_m=2_400, north_m=along)
            guns = m.vehicle_group(
                syria,
                f"AAA Baniyas-{i + 1}",
                vehicles.AirDefence.ZU_23_Emplacement_Closed,
                position=pos,
                heading=270,
                group_size=2,
                formation=VehicleGroup.Formation.Scattered,
            )
            set_skill(guns, Skill.High)

    def _spawn_ewr(self, m: Mission, syria: Country, scene: _Scene):
        """The 55G6 on the coastal ridge above Baniyas — the net's only eyes.

        Sited for line of sight to the sea rather than for height for its own
        sake: measured against the elevation raster it sees the water west of the
        crossing, which is the whole point. Everything else in the net is dark
        until this radar hands it a track, so killing it is worth something and
        the mission never pretends otherwise.

        It is also the mission's causal spine. DCS models no earth curvature, so
        a deck run over open water is not hidden from this radar and the briefing
        does not claim it is — the crossing *is* detected, and that detection is
        what rolls the load-out and scrambles the alert pair.
        """
        ewr = m.vehicle_group(
            syria,
            "EWR Baniyas",
            vehicles.AirDefence.X_55G6_EWR,
            position=scene.ewr_pos,
            heading=270,
        )
        set_skill(ewr, Skill.High)
        return ewr

    def _spawn_plant(self, m: Mission, syria: Country, scene: _Scene) -> _Plant:
        """The works: three aimpoints and the compound they stand in.

        Statics rather than vehicles, because a factory is buildings — and the
        spacing is the design. `_STORE_OFFSET` and `_OXIDISER_OFFSET` put the
        other two aimpoints 400 m and 430 m from the hall, well outside what one
        2,000 lb weapon does to a structure, so a jet carrying two bombs really
        does have to spend the second one on a decision rather than on a lucky
        pattern.

        The rest is compound: tanks, a crane, a container yard and a garage. None
        of it is an objective and none of it is in a trigger — it is there so the
        pod picture is a plant rather than three unexplained buildings in a field,
        and so that finding the right roof is a task.
        """
        hall = self._structure(
            m,
            syria,
            "Ansariyah casting hall",
            statics.Fortification.Workshop_A,
            scene.plant,
            _HALL_OFFSET,
            heading=15,
        )
        store = self._structure(
            m,
            syria,
            "Ansariyah motor store",
            statics.Warehouse.Warehouse,
            scene.plant,
            _STORE_OFFSET,
            heading=15,
        )
        oxidiser = self._structure(
            m,
            syria,
            "Ansariyah oxidiser plant",
            statics.Fortification.Chemical_tank_A,
            scene.plant,
            _OXIDISER_OFFSET,
            heading=0,
        )
        for i, (kind, east, north, heading) in enumerate(
            (
                (statics.Warehouse.Tank, 120.0, 420.0, 0),
                (statics.Warehouse.Tank_2, 210.0, 460.0, 0),
                (statics.Fortification.Tower_Crane, -180.0, -60.0, 90),
                (statics.Fortification.Garage_B, -60.0, -330.0, 15),
                (statics.Fortification.Container_40ft, -260.0, -150.0, 15),
                (statics.Fortification.Container_40ft, -290.0, -180.0, 15),
            )
        ):
            self._structure(
                m,
                syria,
                f"Ansariyah compound {i + 1}",
                kind,
                scene.plant,
                (east, north),
                heading=heading,
            )
        return _Plant(hall=hall, store=store, oxidiser=oxidiser)

    @staticmethod
    def _structure(
        m: Mission,
        country: Country,
        name: str,
        kind,
        anchor: Point,
        offsets: tuple[float, float],
        *,
        heading: int,
    ) -> StaticGroup:
        """One building of the compound, placed as an east/north offset."""
        east_m, north_m = offsets
        return m.static_group(
            country,
            name,
            kind,
            position=offset(anchor, east_m=east_m, north_m=north_m),
            heading=heading,
        )

    def _spawn_plant_shorad(self, m: Mission, syria: Country, scene: _Scene):
        """An Osa on the seaward lip of the basin, plus guns in the compound.

        The one emplaced system on the objective, so it is briefed, ringed and
        loaded into the cartridge like any other — and at 10.3 km it covers every
        approach to the works, which is exactly what makes the run-in a run-in
        rather than an overflight. `Skill.High`: this is what the plant is worth
        to them.

        On the map that ring swallows the last three quarters of the corridor,
        and on the ground it does not: measured against the elevation raster the
        launchers cannot see any corridor point until the IP, 4.2 km out, and
        cannot see the egress point either. The basin is 213 m below the ground
        around it, which is why the plant is where it is. So the drawn ring is an
        honest statement of reach and the terrain is what the player actually
        flies against — and with the site cold until the net cues it
        (`_add_iads`), the first the pilot hears of it is on the run-in.
        """
        osa = ad.build_sa8_site(
            m,
            syria,
            scene.sa8_pos,
            heading=270,
            prefix="Ansariyah ",
            skill=Skill.High,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        osa.name = "SAM Ansariyah"
        guns = m.vehicle_group(
            syria,
            "AAA Ansariyah",
            vehicles.AirDefence.ZU_23_Emplacement,
            position=offset(scene.plant, east_m=520, north_m=-420),
            heading=270,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(guns, Skill.Average)
        return osa

    def _spawn_unfixed_sam(self, m: Mission, russia: Country, scene: _Scene):
        """The Gadfly in the Ghab — on no map, in no cartridge, in no route.

        A withheld threat is only honest under conditions, and this one is sited
        against them rather than dropped where it would be nastiest. Measured
        against the elevation raster from `_SA11_LATLNG`: no line of sight to any
        briefed corridor point at 200 m, none to the target below 2,000 m, and
        line of sight to the target at 3,000 m. So it cannot touch the plan the
        player was handed and it can absolutely punish the two deviations the
        briefing names — climbing off the target, and following the column east
        past the portal.

        The briefing names the gap with a source and an age, `_add_unfixed_sam_trigger`
        gives it a moment somebody was actually in a position to observe, and
        nothing friendly is routed around it: `_threat_rings` does not know it
        exists, which is precisely the case `tasking.apply_threat_reaction`
        covers. Left in the net as a cold, autonomous site, so it comes up on its
        own when something gives it a reason to.
        """
        pos = scene.sa11_pos
        buk = templates.VehicleTemplate.sa11_site(
            m,
            russia,
            pos,
            heading=int(pos.heading_between_point(scene.plant)),
            prefix="Gadfly ",
            skill=Skill.High,
        )
        # pydcs's template ships a rifleman with the battery, and a DCS group
        # moves at the speed of its slowest member — so one man on foot is what
        # would stop `core/iads.py` giving this site the shoot-and-scoot hop it
        # is the best candidate here for. Every other unit is a tracked TELAR.
        buk.units = [u for u in buk.units if u.type != vehicles.Infantry.Infantry_AK.id]
        return ad.disperse_site(
            buk,
            radius_m=400.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_loadout(self, m: Mission, syria: Country, scene: _Scene):
        """The transporters, their escort and their own SHORAD, on the road.

        One group rather than three, unlike `coastal_cover`'s second echelon, and
        for the opposite reason: nothing here lases it and nothing here asks the
        player to distinguish the trucks from the escort. `Chevy` is fragged
        against the column as a column, and the win call is about the column as a
        column, so splitting it would only make the trigger harder to read.

        Late-activated, and pushed by the coast crossing. That is the mission's
        clock made causal: they are loading when the sortie starts and they roll
        when the coastal radar calls `Colt` across the beach, which is the same
        call that scrambles the alert pair. Nothing in the briefing states a
        time, because nothing in the mission holds one — the clock is the
        player's own run.
        """
        route = scene.overlay.place_convoy_route(scene.plant, scene.portal)
        spawn = route.waypoints[0]
        heading = int(spawn.heading_between_point(route.waypoints[-1]))
        column = m.vehicle_group_platoon(
            syria,
            "Ansariyah load-out",
            [
                vehicles.Unarmed.Ural_375,
                vehicles.Unarmed.Ural_375,
                vehicles.Unarmed.Ural_375,
                vehicles.Armor.BTR_80,
                vehicles.Armor.BTR_80,
                vehicles.AirDefence.Strela_10M3,
            ],
            position=spawn,
            heading=heading,
            move_formation=PointAction.OnRoad,
        )
        column.add_waypoint(
            route.waypoints[-1],
            move_formation=PointAction.OnRoad,
            speed=_COLUMN_SPEED_KPH,
        )
        column.late_activation = True
        set_skill(column, Skill.Average)
        return column

    def _bandit_flights(self) -> tuple[int, ...]:
        """How many MiG-29A, as flights, derived from the player's magazine.

        `Colt`'s bomber launches with two AMRAAM and two Sidewinders — six
                stations, two of which take no missile at all, and the other four spent
                on the fuel and the bombs this sortie cannot be flown without — and its
                escort with six. At two shots per kill against experienced crews that is
                two kills off the bomber and three off the escort, and that is the whole
                air-to-air budget. The formula below stays deliberately behind it: a pair
                of Fulcrums per two slots leaves the flight able to decline the merge,
                which is the point.

                So the opposition is a function of `--players` rather than a number
                somebody liked, and even then no part of it is a required kill: the frag
                is the plant, `Eagle` cannot cross the ring to help, and the honest
                answer to a pair of Fulcrums on the egress is usually the water rather
                than the merge. See the force-balance section of CLAUDE.md — this is the
                arithmetic `abkhaz_sweep` was rebuilt around.
        """
        pairs = 1 + (self.players - 1) // 2
        return tuple(2 for _ in range(pairs))

    def _spawn_red_alert_fighters(
        self, m: Mission, syria: Country, scene: _Scene
    ) -> list[FlyingGroup]:
        """MiG-29A alert at Hama, cold on the ramp until the coast is crossed.

        Hama rather than Bassel Al-Assad, and the reason is doctrine before it is
        geometry: an alert commitment for this sector belongs behind the range,
        out of reach of exactly the kind of raid this mission is. It is also what
        makes the red sanctuary possible — a battery on a coastal field 21 km
        from the crossing would have reached the briefed corridor, and
        `sanc.build_sanctuary` would have refused it rather than shipping it.

        `scramble_on_trigger` rather than `late_activation`: they start engines
        when the radar calls the crossing, which is a reaction the player caused
        and can hear happening, not a pair that appears in the air behind him.
        The 71 km from Hama to the basin is the grace it buys.
        """
        flights: list[FlyingGroup] = []
        for i, size in enumerate(self._bandit_flights()):
            mig = m.flight_group_from_airport(
                country=syria,
                name=f"Kadeem {i + 1}",
                aircraft_type=planes.MiG_29A,
                airport=scene.hama,
                maintask=task.CAP,
                start_type=StartType.Cold,
                group_size=size,
            )
            arm(
                mig,
                planes.MiG_29A,
                [
                    (1, "R_73__AA_11_Archer____Infra_Red"),
                    (2, "R_73__AA_11_Archer____Infra_Red"),
                    (3, "R_27R__AA_10_Alamo_A____Semi_Act_Rdr"),
                    (4, "Fuel_tank_1400L"),
                    (5, "R_27R__AA_10_Alamo_A____Semi_Act_Rdr"),
                    (6, "R_73__AA_11_Archer____Infra_Red"),
                    (7, "R_73__AA_11_Archer____Infra_Red"),
                ],
            )
            set_skill(mig, Skill.High)
            apply_ai_difficulty(mig, self.difficulty)
            self._route_intercept(mig, scene)
            flights.append(mig)
        return flights

    def _route_intercept(self, mig: FlyingGroup, scene: _Scene) -> None:
        """Hama → over the basin → search the coastal strip → Hama.

        A route rather than a `Mission.intercept_flight`, because the helper's
        zone-triggered form wants a late-activated flight in the air and this
        pair is meant to be seen starting engines. 900 km/h is 0.37 of the
        MiG-29A's `max_speed` — faster than the package it is chasing, which is
        the point of an interceptor, and still short of the burner band.
        """
        mig.add_runway_waypoint(scene.hama)
        mig.add_waypoint(scene.plant, altitude=6000, speed=900, name="VECTOR")
        mig.add_waypoint(
            offset(scene.feet_dry, east_m=-20_000, north_m=0),
            altitude=4500,
            speed=900,
            name="SEARCH",
        )
        mig.add_runway_waypoint(scene.hama)
        mig.land_at(scene.hama)

    # -- blue side ----------------------------------------------------------

    def _station(
        self, scene: _Scene, out_m: float, leg_m: float
    ) -> tuple[Point, Point]:
        """A race-track `out_m` east of Akrotiri, running across the threat axis.

        Every friendly orbit in this mission is one of these, and they are all
        measured against the same two circles: `BULWARK`'s hundred kilometres and
        the Gammon's briefed ring. `_spawn_sanctuaries` is what enforces the
        first — the AWACS track's far end sits 95 km out and a longer leg would
        push it outside our own missiles — and the comment on each caller states
        the second. Anchoring on the bearing to the S-200 rather than to the
        target keeps both checks one-dimensional.
        """
        axis = scene.akrotiri.position.heading_between_point(scene.sa5_pos)
        p1 = scene.akrotiri.position.point_from_heading(axis, out_m)
        return p1, p1.point_from_heading((axis + 90.0) % 360.0, leg_m)

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[FlyingGroup, tuple[Point, Point]]:
        """E-3A Magic on a long track over Cyprus, 251.000 AM.

        Returned as a group as well as a track because he is the mission's only
        ESM collector — `_add_iads` lists him as the sole `Listener`, so every
        radar-state call the briefing promises is his and goes quiet if he is
        shot down or dragged off station. At 260-275 km from the S-200 and 9,000
        m up he has line of sight to the coastal batteries across open water,
        and none at all to anything behind the range: the Gadfly in the Ghab can
        come up and nobody says a word about it, which is the honest chain and
        also exactly what the Intelligence section admits to.
        """
        p1, p2 = self._station(scene, 30_000.0, 90_000.0)
        track = race_track(p1, p2)
        magic = m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.akrotiri,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=9000,
            speed=740,
            start_type=StartType.Warm,
            frequency=_FREQ_AWACS,
        )
        return magic, (p1, p2)

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco on the band between our Patriot and their Gammon.

        Structural rather than decorative, and the one place in this project
        where that is arithmetic rather than a preference: `Colt` flies a 279 km
        radius with 300 km of it on the deck, which is not a sortie an F-16C with
        two bags does on one tank of gas. He is briefed to take fuel outbound and
        again on the way home, and the ROE's bingo number is written against
        this track being where it says it is.

        55 km east of Akrotiri, so 235 km from the S-200 — 35 km outside the ring
        the player is shown — and comfortably inside `BULWARK`. 750 km/h is
        0.77 of the KC-135's `max_speed` and about 250 KIAS at 6,500 m, which is
        a tanker's speed rather than a fighter's fraction.
        """
        p1, p2 = self._station(scene, 55_000.0, 55_000.0)
        track = race_track(p1, p2)
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.akrotiri,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=6500,
            speed=_TANKER_SPEED_KPH,
            start_type=StartType.Warm,
            frequency=_FREQ_TANKER,
            tacanchannel=_TANKER_TACAN,
        )
        return p1, p2

    def _spawn_tarcap(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C pair on the forward edge of the band, and no further.

        75 km east of Akrotiri puts both ends of the track 215-220 km from the
        S-200, which is fifteen kilometres outside its briefed ring and 88 km
        inside `BULWARK`'s. That is the whole of the sky this pair can hold, and
        it is why the briefing says they cannot come with you: an F-15C that
        follows `Colt` east is inside a 160 km envelope with nothing to hide
        behind, and the ROE would be asking the escort to do the one thing the
        strike is flown at fifty metres to avoid.

        What they are actually for is the other half of the sortie — the alert
        pair that launches off the coastal radar's call has to be met somewhere,
        and this is the line `Colt` is trying to get back across.
        """
        p1, p2 = self._station(scene, 75_000.0, 45_000.0)
        eagle = m.patrol_flight(
            usa,
            "Eagle",
            planes.F_15C,
            airport=scene.akrotiri,
            pos1=p1,
            pos2=p2,
            start_type=StartType.Warm,
            speed=800,
            altitude=8000,
            max_engage_distance=90_000,
            group_size=2,
        )
        set_skill(eagle, Skill.High)
        arm(
            eagle,
            planes.F_15C,
            [
                (1, "AIM_9M_Sidewinder_IR_AAM"),
                (3, "AIM_9M_Sidewinder_IR_AAM"),
                (4, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (5, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (6, "Fuel_tank_610_gal"),
                (7, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (9, "AIM_9M_Sidewinder_IR_AAM"),
                (11, "AIM_9M_Sidewinder_IR_AAM"),
            ],
        )
        apply_threat_reaction(eagle)
        return p1, p2

    def _spawn_strike(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        column: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> FlyingGroup:
        """Chevy: an F/A-18C pair holding on the deck, fragged against the column.

        Spawned **in flight** and late-activated rather than held on the ramp,
        which is the opposite of `idlib_gauntlet`'s Pontiac and for a reason that
        is entirely about this map: Akrotiri is 279 km from the beach, so a pair
        released off the ramp arrives twenty-five minutes after the event that
        released them and the column is long gone. They have been holding at
        150 km east of the field since before the player started engines, which
        is what the briefing says, and 128 km from the crossing is eleven minutes
        — a release the player can watch pay off.

        **They cannot climb either, and that is the interesting part.** The
        Gammon's floor is not a rule about the player, it is a rule about this
        coast: an S-200 at 27 km from the basin will take anything that goes
        above 300 m and can see it. So the frag is a low-level pass with
        retarded cluster munitions rather than the 5,200 m laser-guided delivery
        the same pair would fly anywhere else in this project, and the column's
        own Strela-10 is the price of it. Measured against the elevation raster,
        the whole length of the column's road is masked from the S-200 and from
        the unlocated Gadfly at that height; the ridge does what the altitude
        cannot.
        """
        hold = scene.akrotiri.position.point_from_heading(
            scene.akrotiri.position.heading_between_point(scene.feet_dry), 150_000.0
        )
        chevy = m.flight_group_inflight(
            country=usa,
            name="Chevy",
            aircraft_type=planes.FA_18C_hornet,
            position=hold,
            altitude=int(_SEA_DECK_M),
            # km/h, like every other pydcs speed argument and unlike anything in
            # this helper's signature that says so. `waypoints.set_departure_speeds`
            # cannot save an in-flight spawn — it only rewrites runway waypoints —
            # so a metres-per-second value here would have left the pair holding
            # at 194 km/h, a tenth of the Hornet's ceiling, and stayed silent.
            speed=_SEA_DECK_SPEED_KPH,
            maintask=task.CAS,
            group_size=2,
        )
        chevy.late_activation = True
        rockeye = "BRU_33_with_2_x_Mk_20_Rockeye___490lbs_CBU__247_x_HEAT_Bomblets"
        arm(
            chevy,
            planes.FA_18C_hornet,
            [
                (1, "AIM_9X_Sidewinder_IR_AAM"),
                (2, rockeye),
                (3, "FPU_8A_Fuel_Tank_330_gallons"),
                (4, "AN_ASQ_228_ATFLIR___Targeting_Pod"),
                (6, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (7, "FPU_8A_Fuel_Tank_330_gallons"),
                (8, rockeye),
                (9, "AIM_9X_Sidewinder_IR_AAM"),
            ],
        )
        set_skill(chevy, Skill.High)
        self._route_strike(chevy, scene, column=column, threats=threats)
        return chevy

    def _route_strike(
        self,
        chevy: FlyingGroup,
        scene: _Scene,
        *,
        column: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> None:
        """Hold → the player's own corridor → the column on the road → Akrotiri.

        The corridor is handed over rather than re-planned, and that is a
        deliberate choice against `core/routing.py` rather than an oversight:
        the ingress here is a terrain-following line whose altitudes were
        measured against the elevation raster leg by leg, and a second route
        bent around the same rings by `avoid_threats` would be a different line
        at altitudes nobody checked. What routing is still used for is the water
        — the transit from the hold to the beach genuinely does have to stay out
        of the two coastal envelopes, and there is no terrain out there to do it
        for us.

        The attack point sits four fifths of the way up the column's road,
        which is the one position on it outside the Osa's ring at the plant. The
        `AttackGroup` task is what actually finds the trucks; the waypoint is
        where the pair is pointed while it does.
        """
        aim = self._column_aimpoint(scene)
        for i, pt in enumerate(
            routing.avoid_threats(
                chevy.points[0].position, scene.feet_dry, threats, clearance_m=6_000.0
            )[1:-1],
            start=1,
        ):
            chevy.add_waypoint(
                pt,
                altitude=int(_SEA_DECK_M),
                speed=_SEA_DECK_SPEED_KPH,
                name=f"OVERWATER-{i}",
            )
        for leg, altitude in waypoints.agl_profile(
            scene.ingress,
            scene.overlay.overlay,
            clearance_m=_LEG_CLEARANCE_M,
            ground_floor_m=0.0,
        ):
            chevy.add_waypoint(
                leg.position,
                altitude=altitude,
                speed=_SEA_DECK_SPEED_KPH
                if leg.agl_m <= _SEA_DECK_M
                else _LAND_SPEED_KPH,
                name=leg.name,
            )
        attack = chevy.add_waypoint(
            aim,
            altitude=int(
                waypoints.ground_elevation_m(scene.overlay.overlay, aim) + 250
            ),
            speed=_LAND_SPEED_KPH,
            name="ATTACK",
        )
        attack.tasks.append(
            task.AttackGroup(
                column.id,
                weapon_type=task.WeaponType.Bombs,
                group_attack=True,
                expend=task.Expend.All,
            )
        )
        for leg, altitude in waypoints.agl_profile(
            scene.egress,
            scene.overlay.overlay,
            clearance_m=_LEG_CLEARANCE_M,
            ground_floor_m=0.0,
        ):
            chevy.add_waypoint(
                leg.position, altitude=altitude, speed=_EGRESS_SPEED_KPH, name=leg.name
            )
        chevy.add_runway_waypoint(scene.akrotiri)
        chevy.land_at(scene.akrotiri)
        # The one flight here that gets the throttle stop. A bombed-up pair on a
        # 279 km radius has nothing to gain from burner and everything to lose,
        # and the route above already keeps it out of the rings it knows about.
        apply_threat_reaction(chevy, restrict_afterburner=True)

    @staticmethod
    def _column_aimpoint(scene: _Scene) -> Point:
        """Four fifths of the way up the column's road, outside the Osa's ring.

        Measured rather than eyeballed: the Osa sits 1.8 km south-west of the
        plant with a 10.3 km envelope, and the road runs north-east up the ridge,
        so anywhere below about three quarters of it is inside that ring. This
        point is 11.5 km from the launchers and 2.4 km short of the portal, which
        is also the far end of what the player is cleared to follow.
        """
        start, end = scene.plant, scene.portal
        return Point(
            start.x + 0.8 * (end.x - start.x),
            start.y + 0.8 * (end.y - start.y),
            start._terrain,
        )

    def _spawn_player(
        self, m: Mission, usa: Country, scene: _Scene, *, plan: PlanOverlay
    ) -> tuple[list[FlyingGroup], list[Point]]:
        """Colt: F-16C-50 out of Akrotiri, two JDAM, the deck, and 279 km.

        The bomber's fit is ED's own `AIM-120C*2, AIM-9X*2, GBU-31-3B*2, FUEL*2,
        ECM, TGP` payload station for station, which is what makes the wingtip
        rule right — the AMRAAM go on 1/9 and the Sidewinders on 2/8, not the
        other way round. Slot 2 flies the pure air-to-air fit instead
        (`_FITS`): it is the only friendly cover east of the band `Eagle` is
        stuck in, and the sortie's second half is an alert pair the player's own
        crossing scrambled.

        **Two 2,000 lb penetrators and no laser, on a target you could have
        lased.** That is the threat picture choosing the weapon rather than the
        weapon choosing itself: there is no altitude over this basin from which a
        jet could hold a spot and live, because the Gammon 27 km away takes
        anything above 300 m that it can see. A satellite-aided bomb released
        from the deck is the only delivery this geometry allows, which is also
        why the aimpoints have to be surveyed onto the cartridge before start.

        The jet launches at about 86 % of max gross, which is heavy — and unlike
        the AI flights in this package, that is fine: the weight is a 279 km
        radius with 300 km of it on the deck, `Texaco` is briefed twice, and
        nobody is going to sit behind a DCS climb-out routine in this cockpit.
        """
        sections = player_flight(
            m,
            country=usa,
            name="Colt",
            aircraft_type=planes.F_16C_50,
            airport=scene.akrotiri,
            maintask=task.PinpointStrike,
            start_type=StartType.Warm,
            slots=self.players,
            loadouts=_FITS,
        )
        # Both ends of the deck run are the *briefed* edge of the Gammon's ring,
        # not the real one — same estimate the F10 map paints and the cartridge
        # loads. A wider claim buys a longer run on the deck, which is the right
        # price for a thinner picture.
        gammon, gammon_ring = plan.estimate(scene.sa5_pos, radius=_SA5_RING_M)
        letdown = self._ring_edge(
            scene.akrotiri.position, scene.feet_dry, gammon, gammon_ring
        )
        climb = self._ring_edge(
            scene.akrotiri.position, scene.egress[-1].position, gammon, gammon_ring
        )
        # And the target steerpoint is where the works are *assessed* to be. The
        # plant is a set of buildings the player has to find in the pod; a
        # steerpoint on the hall would hand him a survey the briefing spent three
        # paragraphs saying nobody has.
        target, _ = plan.estimate(scene.plant, radius=_SA8_RING_M)
        ingress = waypoints.agl_profile(
            scene.ingress,
            scene.overlay.overlay,
            clearance_m=_LEG_CLEARANCE_M,
            ground_floor_m=0.0,
        )
        egress = waypoints.agl_profile(
            scene.egress,
            scene.overlay.overlay,
            clearance_m=_LEG_CLEARANCE_M,
            ground_floor_m=0.0,
        )
        for section in sections:
            self._route_colt(
                section,
                scene,
                ingress,
                egress,
                letdown=letdown,
                climb=climb,
                target=target,
            )
        route = [
            scene.akrotiri.position,
            self._tanker_point(scene),
            letdown,
            letdown.midpoint(scene.feet_dry),
            *(leg.position for leg, _ in ingress),
            target,
            *(leg.position for leg, _ in egress),
            climb,
            self._descent_point(scene),
            scene.akrotiri.position,
        ]
        return sections, route

    def _route_colt(
        self,
        player: FlyingGroup,
        scene: _Scene,
        ingress: Sequence[tuple[Leg, float]],
        egress: Sequence[tuple[Leg, float]],
        *,
        letdown: Point,
        climb: Point,
        target: Point,
    ) -> None:
        """Akrotiri → tanker → the deck → the basin → the water → Akrotiri.

        The altitudes, the ring edges and the assessed target are worked out once
        in `_spawn_player` and handed in: each is a read against the elevation
        raster or against the plan's estimate, and two sections deriving them
        separately could fly two different plans under one briefing.
        """
        player.add_runway_waypoint(scene.akrotiri)
        player.add_waypoint(
            self._tanker_point(scene),
            altitude=6500,
            speed=_TANKER_SPEED_KPH,
            name="TEXACO",
        )
        # LETDOWN is where the descent *starts*, not where it ends, and DECK is
        # where it ends. That distinction is the difference between a card that
        # matches the ROE and one that quietly contradicts it: DCS ramps
        # linearly between waypoints, so a 6,500 m point followed by a 60 m point
        # 199 km later has the jet above the Gammon's floor for nine tenths of
        # the leg — which is exactly what the briefing spends a page telling the
        # player not to do. Half way is a 6.8 % descent, which a Viper flies at
        # idle, and it puts the level-off 81 km short of the beach.
        player.add_waypoint(
            letdown, altitude=6500, speed=_TRANSIT_SPEED_KPH, name="LETDOWN"
        )
        player.add_waypoint(
            letdown.midpoint(scene.feet_dry),
            altitude=int(_SEA_DECK_M),
            speed=_SEA_DECK_SPEED_KPH,
            name="DECK",
        )
        for leg, altitude in ingress:
            player.add_waypoint(
                leg.position,
                altitude=altitude,
                speed=_SEA_DECK_SPEED_KPH
                if leg.agl_m <= _SEA_DECK_M
                else _LAND_SPEED_KPH,
                name=leg.name,
            )
        # The run-in altitude is flown off the IP leg above; this steerpoint
        # marks the ground, so it carries the ground's elevation.
        waypoints.add_ground_waypoint(
            player,
            target,
            overlay=scene.overlay.overlay,
            speed=_LAND_SPEED_KPH,
            name="TARGET",
        )
        for leg, altitude in egress:
            player.add_waypoint(
                leg.position, altitude=altitude, speed=_EGRESS_SPEED_KPH, name=leg.name
            )
        # And `CLIMB` is where the climb starts, for the mirror-image reason: at
        # 6,500 m it would have put the recovery above the floor from the moment
        # the jet went feet wet, 197 km inside the ring. At 60 m the whole
        # egress is on the deck exactly as briefed and the climb is the leg
        # after it.
        player.add_waypoint(
            climb, altitude=int(_SEA_DECK_M), speed=_EGRESS_SPEED_KPH, name="CLIMB"
        )
        # A let-down point, and it is a timing fix rather than navigation.
        # `add_runway_waypoint` hard-codes the *approach* gate at 108 kt and
        # `waypoints.set_departure_speeds` deliberately leaves it there — by then
        # the jet is light and that is roughly its real approach speed. What that
        # makes expensive is the leg *into* it: with `CLIMB` 48 NM out, the last
        # 39 NM of this sortie were flown on the card at 108 kt, which is
        # twenty-two minutes of a ninety-minute mission. Coming down 19 NM from
        # the field leaves a 16 NM final instead, the same fix `daryal_run` made
        # with its `MTSKHETA` waypoint.
        player.add_waypoint(
            self._descent_point(scene),
            altitude=2500,
            speed=_EGRESS_SPEED_KPH,
            name="DESCENT",
        )
        player.add_runway_waypoint(scene.akrotiri)
        player.land_at(scene.akrotiri)

    def _tanker_point(self, scene: _Scene) -> Point:
        """The AR point: the near end of `Texaco`'s track, as a steerpoint."""
        return self._station(scene, 55_000.0, 55_000.0)[0]

    @staticmethod
    def _descent_point(scene: _Scene) -> Point:
        """19 NM east of Akrotiri on the recovery line — see `_route_colt`."""
        akrotiri = scene.akrotiri.position
        return akrotiri.point_from_heading(
            akrotiri.heading_between_point(scene.egress[-1].position), 35_000.0
        )

    # -- somewhere to fall back to ------------------------------------------

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        syria: Country,
        scene: _Scene,
        *,
        route: list[Point],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Akrotiri under Patriot, Hama under S-125.

        This is the one mission in the project where a Patriot is the right
        battery rather than the greedy one, and `keep_clear` is what proves it:
        the nearest thing the Syrians need left standing is 279 km away, so a
        100 km envelope cannot rewrite anything. What it buys is that **every**
        friendly station fits inside one umbrella — the AWACS at 95 km, the
        tanker at 78, the CAP at 88, and Paphos at 48 — and the sixteen
        kilometres between its edge and the Gammon's briefed ring is the entire
        answer to "why does the escort stop there".

        Hama gets the red half, and the choice of field is the whole reason the
        alert pair is not at Bassel Al-Assad. A battery on a coastal airfield
        would have reached the briefed crossing, and `build_sanctuary` would have
        refused it — correctly. Behind the range it costs the run nothing at 72
        km and costs a chase everything, which is what a red sanctuary is for.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.akrotiri,
            callsign=_SANCTUARY,
            facing=scene.plant,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[
                scene.plant,
                scene.sa5_pos,
                scene.sa3_north,
                scene.sa3_south,
                scene.ewr_pos,
                scene.portal,
            ],
            alternates=[scene.paphos],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        hama_ad = sanc.build_sanctuary(
            m,
            syria,
            scene.hama,
            callsign="Hama field",
            facing=scene.plant,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Hama",
            keep_clear=[scene.plant, scene.portal, *route],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, hama_ad

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, *countries: Country) -> None:
        """Keep every Syrian and Russian group off the map, planner and datalink.

        `conceal_country` rather than a hand-picked list, which matters more here
        than usual: this mission's enemy order of battle includes nine static
        structures and a late-activated column, and a compound that showed up as
        stock icons would hand the player the aimpoint choice the whole briefing
        is built around asking him to make in the pod.
        """
        conceal_country(*countries)

    def _threat_rings(self, scene: _Scene) -> tuple[ThreatRing, ...]:
        """The rings the friendly package is planned around — on truth, not on the map.

        Three of them, and the two absences are the interesting part.

        **The Gammon is not here.** Its envelope covers the hold, the whole
        transit and the target, so `avoid_threats` would skip it anyway (a ring
        that covers both ends of a leg cannot be detoured around) — but the real
        reason is that routing is the wrong tool for it. The answer to an S-200
        is three hundred metres of altitude, and `core/routing.py` models
        distance. The altitude answer is in `_CORRIDOR`, in the ROE and in
        `_route_strike`, and it is the only one there is.

        **The Gadfly is not here either**, because nothing friendly may be
        planned around a site the briefing admits to having no position for.
        That is exactly the gap `tasking.apply_threat_reaction` covers.
        """
        return (
            ThreatRing(scene.sa3_north, _SA3_RING_M, "S-125 Latakia"),
            ThreatRing(scene.sa3_south, _SA3_RING_M, "S-125 Tartus"),
            ThreatRing(scene.sa8_pos, _SA8_RING_M, "SA-8 Ansariyah"),
        )

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        route: list[Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
        tarcap_track: tuple[Point, Point],
        home: sanc.Sanctuary,
        hama_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan (veteran: wide dashed rings, several kilometres off truth).

        The order is the cartridge's budget rather than a drawing preference.
        `core/dtc.plan_steerpoints` fills the jet's twenty-five navigation slots
        with the flight's own route first and then the plan's points **in draw
        order**, and this route is fourteen points long, so the ten marks below
        fit with one slot spare and would start dropping off the end if anything
        else were added. The sanctuary goes first for the reason it always does:
        the marshal hold is the one mark a pilot might need with a broken jet.

        Two things are deliberately not drawn. The **Gadfly** has no ring because
        nothing in the briefing claims a position for it. And the **guns in the
        seam** have none either — a towed 23 mm section has no envelope worth a
        steerpoint, and the briefing carries the warning in prose instead, which
        is the honest shape for "there is something on that beach and we do not
        know where".

        The Gammon's ring is the drawing that makes the whole plan legible: it
        covers the route from a hundred kilometres east of Akrotiri all the way
        to the beach, so the deck run stops looking like an eccentric choice and
        starts looking like the only line there is.
        """
        home.draw(plan)
        plan.objective(scene.plant, "ANSARIYAH WORKS", radius=5_000.0)
        plan.waypoint_label(scene.feet_dry, "FEET DRY — cross here, on the deck")
        plan.waypoint_label(scene.portal, "Tunnel portal — do not follow past")
        plan.route(route, "Colt ingress")
        briefed = [
            *dtc.briefed(
                plan.threat(
                    scene.sa5_pos,
                    radius=_SA5_RING_M,
                    label="SA-5 — floor 300 m",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_5,
                label="SA-5 Jableh",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.sa3_north,
                    radius=_SA3_RING_M,
                    label="SA-3 Latakia",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_3,
                label="SA-3 Latakia",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.sa3_south,
                    radius=_SA3_RING_M,
                    label="SA-3 Tartus",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_3,
                label="SA-3 Tartus",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.sa8_pos,
                    radius=_SA8_RING_M,
                    label="SA-8 — on the works",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_8,
                label="SA-8 Ansariyah",
            ),
        ]
        plan.threat(
            scene.ewr_pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar
        )
        # The column's Strela-10 drives with it, so it gets a mark and no
        # envelope: a ring drawn where the load-out started is a promise about
        # ground it has left, and it never reaches the cartridge.
        plan.mobile_threat(scene.plant, "Load-out SHORAD", icon=StandardIcon.Mechanized)
        plan.orbit(*tanker_track, "Texaco tanker")
        plan.orbit(*tarcap_track, "Eagle TARCAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        return briefed + hama_ad.draw(plan)

    def _load_cartridge(
        self,
        m: Mission,
        scene: _Scene,
        points: list[dtc.ThreatPoint],
        *,
        plan: PlanOverlay,
    ) -> None:
        """Put the briefed picture in the cockpit, where the map drew it.

        And on the kneeboard's threat block, which is the same list again for
        whoever is reading a card instead of an HSD. Every row is the estimate
        `PlanOverlay.threat` returned, so the difficulty policy is applied once
        and no channel can out-claim another.

        The Gammon's row is the one worth checking on the ground: it prints the
        briefed 160 km rather than the 255 km the jet's own threat table carries
        for an SA-5, because `dtc.ThreatPoint.radius_m` overrides it — and the
        briefed number is the one the whole flight plan was built from.
        """
        dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)
        dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)

    # -- the net ------------------------------------------------------------

    def _add_iads(
        self,
        m: Mission,
        *,
        magic: FlyingGroup,
        red: _RedGround,
        hama_ad: sanc.Sanctuary,
    ) -> None:
        """Wire every radar-guided site into one net.

        The cueing half is what makes the low ingress mechanically real rather
        than decorative. Left alone every DCS battery radiates from mission
        start, so the player's RWR would be full of S-125s before he left
        Cyprus and the seam would be a line on a map with nothing behind it.
        Here only two things are on the air at the start: the Baniyas EWR,
        because searching is what an early-warning radar is for, and the Gammon,
        because the briefing says it has been radiating for weeks and the
        player's RWR on the runway should confirm that. Everything else is dark
        until the net hands it a track inside its own reach.

        `act_as_ew` on the S-200 rather than a `go_live_percent`, which is the
        honest model of a 240 km search radar: it does not wait to be cued by
        anything, it *is* the cue. It also means the invariant `arm_iads` warns
        about is satisfied twice over, which matters on a coast where the EWR is
        a single vehicle a player could reasonably kill.

        The reaction half is present and will mostly never fire, and that is
        fine rather than waste: nothing in this package carries an
        anti-radiation missile, so no site here is ever going to be shot off the
        air. What the dials still buy is the emission discipline that comes with
        them — the coastal conscripts sit on the air in long looks and the
        Russian-crewed batteries work in short ones, which is the difference the
        briefing describes and the difference the RWR shows.

        `Magic` is the only listener, so every emissions call is his ESM watch.
        Over open water he has line of sight to both coastal batteries and to
        the Gammon; he has none at all to anything behind the range, so the
        Gadfly in the Ghab can come up and nobody says a word about it. That is
        the honest chain and it is exactly what the Intelligence section admits
        to.
        """
        sites = [
            # The premise. Up throughout, and the only battery here whose reach
            # is a strategic fact rather than a local one.
            Site(
                red.sa5,
                "the Gammon",
                act_as_ew=True,
                probability=0.9,
                delay_s=(20.0, 55.0),
                shutdown_s=(280.0, 400.0),
                react_range_m=120_000.0,
                net_relay=0.5,
            ),
            Site(
                red.ewr,
                "the Baniyas early-warning radar",
                role="ewr",
                probability=0.75,
                delay_s=(30.0, 90.0),
                shutdown_s=(200.0, 300.0),
                react_range_m=90_000.0,
            ),
            # The coast. Conscript crews on a quiet sector: `Skill.Average`
            # gives them the longest emission looks in `_EMISSION_BY_SKILL`,
            # which is the mechanical version of what the briefing says about
            # them, and they take a long time to react to anything.
            *[
                Site(
                    belt,
                    label,
                    go_live_percent=150,
                    probability=0.65,
                    delay_s=(30.0, 85.0),
                    shutdown_s=(240.0, 360.0),
                    react_range_m=70_000.0,
                    net_relay=0.3,
                )
                for belt, label in (
                    (red.sa3_north, "the Latakia battery"),
                    (red.sa3_south, "the Tartus battery"),
                )
            ],
            # The works' own Osa. Its crew works its own radar, so it is the
            # quickest here to notice anything and the quickest to go quiet.
            Site(
                red.sa8,
                "the Osa on the works",
                go_live_percent=150,
                probability=0.85,
                delay_s=(10.0, 30.0),
                shutdown_s=(220.0, 320.0),
                react_range_m=40_000.0,
                net_relay=0.6,
            ),
            # The Gadfly. Tight and late by design: it is meant to be found by
            # somebody who has already done the one thing he was told not to.
            Site(
                red.unfixed,
                "the unlocated Gadfly",
                go_live_percent=120,
                probability=0.9,
                delay_s=(12.0, 35.0),
                shutdown_s=(240.0, 360.0),
                react_range_m=70_000.0,
                net_relay=0.3,
            ),
            # Hama's own field battery, 72 km behind the range and unlikely ever
            # to cue. It is in the net anyway for the same reason every airfield
            # battery in this project is: leaving it out would make it the one
            # site in Syria that behaves differently from all the others.
            Site(
                hama_ad.groups[0],
                "the Hama field battery",
                go_live_percent=150,
                probability=0.6,
                delay_s=(35.0, 90.0),
                shutdown_s=(240.0, 360.0),
                react_range_m=40_000.0,
                net_relay=0.2,
            ),
        ]
        arm_iads(
            m,
            sites,
            listeners=[Listener(magic, "Magic")],
            voice=self._voice,
            coalition="blue",
            name="Syrian coastal air defence",
            down_call="Magic: {label} has ceased emissions, site is dark.",
            up_call="Magic: {label} is radiating again, expect it hot.",
        )

    # -- triggers -----------------------------------------------------------

    def _add_intro_voice(self, m: Mission) -> None:
        """Magic's picture at mission start."""
        mission_triggers.intro(
            m,
            comment="Magic intro picture",
            voice=self._voice,
            text=(
                "Magic on station. Colt, picture: the Gammon behind Jableh is up "
                "and has been all night. Two coastal batteries, Latakia and "
                "Tartus, both cold. Your crossing is the water between them. "
                "Stay under three hundred metres and they cannot touch you."
            ),
            seconds=25,
        )

    def _add_support_checkins(self, m: Mission) -> None:
        """Texaco on station, on the clock, while the player is still climbing."""
        mission_triggers.checkin(
            m,
            at_seconds=_TANKER_CHECKIN_S,
            comment="Texaco check-in",
            voice=self._voice,
            text=(
                "Texaco is established, two seven zero point zero, TACAN ten X. "
                "Colt, take everything you can carry — it is a long way to the "
                "beach and I will be here when you come back."
            ),
        )

    def _add_coast_crossing_triggers(
        self,
        m: Mission,
        scene: _Scene,
        *,
        colt: Sequence[FlyingGroup],
        migs: Sequence[FlyingGroup],
        column: VehicleGroup,
    ) -> None:
        """The crossing is detected, and three things happen because of it.

        This is the mission's causal spine and it is deliberately not a timer.
        DCS models no earth curvature, so a deck run over open water is seen by
        a coastal radar exactly as the briefing says it will be — and rather than
        pretend otherwise, the mission spends that detection: the load-out rolls,
        the Hama alert pair starts engines, and `Magic` says so. Everything in the
        second half of the sortie is downstream of the player being where he was
        told to be.

        `condition.Or` over the sections rather than a coalition test, for the
        reason `idlib_gauntlet` found the hard way: gated on the coalition, the
        Eagles trip it from their CAP station before the player has taxied.
        `PartOfGroupInZone` ORed is "any part of the player flight", which is
        what "Colt is feet dry" means.
        """
        gate = m.triggers.add_triggerzone(
            position=scene.feet_dry,
            radius=12_000,
            hidden=True,
            name="Coast crossing",
        )
        crossing = triggers.TriggerOnce(comment="Colt detected crossing the coast")
        # `condition.Or()` is a *separator* in pydcs, not a combinator: the
        # trigger's condition list is ANDed unless one is spliced in between, so
        # a six-slot flight gated without them would hold the call until both
        # sections had crossed the beach.
        for index, section in enumerate(colt):
            if index:
                crossing.add_condition(condition.Or())
            crossing.add_condition(condition.PartOfGroupInZone(section.id, gate.id))
        crossing.add_action(action.SetFlag(_FLAG_COAST_CROSSED))
        crossing.add_action(action.ActivateGroup(column.id))
        call = (
            "Magic: the Baniyas radar has you across the beach, Colt. Assume "
            "they are calling it in. Expect the alert pair off Hama and expect "
            "the load-out to start rolling. Stay low and keep going."
        )
        crossing.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=20)
        )
        self._voice.attach_to_coalition(m, crossing, call, coalition="blue")
        m.triggerrules.triggers.append(crossing)

        for i, mig in enumerate(migs):
            trig = scramble_on_trigger(
                m,
                mig,
                condition.FlagIsTrue(_FLAG_COAST_CROSSED),
                comment=f"Hama alert scramble {i + 1}",
            )
            if i:
                continue
            scramble = (
                "Magic: alert pair starting engines at Hama, Fulcrums. They are "
                "seventy kilometres behind the range — you have a few minutes, "
                "not many. Eagle cannot come to you past the ring."
            )
            trig.add_action(
                action.MessageToCoalition(
                    action.Coalition.Blue, m.string(scramble), seconds=15
                )
            )
            self._voice.attach_to_coalition(m, trig, scramble, coalition="blue")

    def _add_strike_release_triggers(
        self, m: Mission, *, plant: _Plant, chevy: FlyingGroup
    ) -> None:
        """Release Chevy when the hall goes down, or on a cut-off if it does not.

        Two ways in and one flag, the shape `idlib_gauntlet` uses: gating the
        Hornets purely on the player's bomb would strand them west of the beach
        for the whole sortie if the run went wrong, and the column would then
        reach the portal unopposed for a reason the player cannot see.
        """
        struck = triggers.TriggerOnce(comment="Casting hall destroyed")
        struck.add_condition(condition.UnitDead(plant.hall.units[0].id))
        struck.add_action(action.SetFlag(_FLAG_PLANT_STRUCK))
        m.triggerrules.triggers.append(struck)

        cutoff = triggers.TriggerOnce(comment="Chevy release cut-off")
        cutoff.add_condition(condition.TimeAfter(seconds=3300))
        cutoff.add_action(action.SetFlag(_FLAG_PLANT_STRUCK))
        m.triggerrules.triggers.append(cutoff)

        release = triggers.TriggerOnce(comment="Chevy released onto the load-out")
        release.add_condition(condition.FlagIsTrue(_FLAG_PLANT_STRUCK))
        release.add_action(action.ActivateGroup(chevy.id))
        call = (
            "Chevy pushing from the hold, feet dry in ten. We are going after the "
            "column on the ridge road, low — nobody gets to climb out here. Colt, "
            "get yourself over the water."
        )
        release.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=15)
        )
        self._voice.attach_to_coalition(m, release, call, coalition="blue")
        m.triggerrules.triggers.append(release)

    def _add_end_triggers(
        self,
        m: Mission,
        scene: _Scene,
        *,
        red: _RedGround,
        colt: Sequence[FlyingGroup],
    ) -> None:
        """Four outcomes that compose, plus the one that ends it.

        Each aimpoint stands on its own rather than being combined into a score,
        which is what lets a pair's two bombs read as a decision the pilot made
        instead of as a percentage he failed to reach. The hall is the frag
        and its call says so; the other two are what else went with it, and they
        are worded as consequences a pilot could recognise rather than as points.

        The failure gate is every section down **and** the hall still standing,
        ANDed — above four coop slots the flight is more than one group, and
        gating on the lead alone would call the sortie lost with jets still on
        the deck heading east.
        """
        mission_triggers.message_to_all(
            m,
            comment="Casting hall down",
            conditions=(condition.UnitDead(red.plant.hall.units[0].id),),
            voice=self._voice,
            text=(
                "Magic: the casting hall is down. That is the mission, Colt — "
                "they are not casting motors at Ansariyah this year. Egress west, "
                "stay on the deck until you are outside the ring."
            ),
            seconds=25,
        )
        mission_triggers.message_to_all(
            m,
            comment="Motor store down",
            conditions=(condition.UnitDead(red.plant.store.units[0].id),),
            voice=self._voice,
            text=(
                "Magic: the motor store went with it. Whatever they had not "
                "loaded this morning is gone."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Oxidiser plant down",
            conditions=(condition.UnitDead(red.plant.oxidiser.units[0].id),),
            voice=self._voice,
            text=(
                "Magic: the oxidiser plant is gone as well. They will be buying "
                "propellant from somebody else for a year."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Load-out destroyed",
            conditions=(condition.GroupDead(red.column.id),),
            voice=self._voice,
            text=(
                "Chevy: the column is finished on the road. Nothing from that "
                "load-out is reaching the tunnel."
            ),
        )
        portal_zone = m.triggers.add_triggerzone(
            position=scene.portal, radius=1_500, hidden=True, name="Tunnel portal"
        )
        mission_triggers.message_to_all(
            m,
            comment="Load-out reached the portal",
            conditions=(condition.PartOfGroupInZone(red.column.id, portal_zone.id),),
            voice=self._voice,
            text=(
                "Magic: the load-out is under the portal. That is out of reach "
                "now — leave it and get out."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Colt lost with the hall standing",
            conditions=(
                *(condition.GroupDead(group.id) for group in colt),
                condition.UnitAlive(red.plant.hall.units[0].id),
            ),
            voice=self._voice,
            text=(
                "Magic: Colt is down and the works are still standing. "
                "Ansariyah keeps casting."
            ),
            seconds=25,
        )

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Cross the eastern Mediterranean below 300 m, coast in north of "
            "Baniyas between the Latakia and Tartus batteries, and destroy the "
            "casting hall at the Ansariyah rocket-motor works. Second bomb is "
            "yours: the motor store while the load-out is still in it, or the "
            "oxidiser plant. Egress west on the deck and climb only outside the "
            "Gammon's ring. RTB Akrotiri; divert Paphos."
        )
        m.set_description_redtask_text(
            "Hold the Ansariyah works and get the load-out to the tunnel portal. "
            "The Gammon behind Jableh denies every usable altitude over the "
            "approach; the coastal batteries hold Latakia and Tartus. MiG-29A "
            "from Hama intercept anything the coastal radar calls across the "
            "beach."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(AnsariyahWorks)


if __name__ == "__main__":
    main()
