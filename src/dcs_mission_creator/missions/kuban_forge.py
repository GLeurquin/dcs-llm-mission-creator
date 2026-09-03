"""Caucasus 'Kuban Forge' — F-16C ace strike on a rocket-motor plant, low ingress.

Player flies a USAF F-16C-50 out of Senaki-Kolkhi as `Colt`. The target is a
Soviet-era chemical works on the Kuban north of Karachayevsk, re-tooled to cast
solid rocket motors for the batteries the rest of this theater keeps running
into. The plant sits in a valley, which is the point: it cannot be seen from
altitude, and the airspace over it belongs to a Buk battery on the plain.

The sortie carries **no anti-radiation weapon**. That is the design statement:
the SAM belts here cannot be shot, only avoided, so the terrain is the SEAD —
north-west across the Kolkheti lowland and the Abkhaz coastal plain, inland up
the Kodori valley system, over the **Klukhori Pass** onto the northern slope,
down the Teberda and out into the Kuban. Measured against the elevation raster,
the whole corridor is masked from the Buk and from both early-warning radars
until `KARACHAY`, where the Teberda turns toward the Kuban 34 km short of the
battery (`_CORRIDOR`, `waypoints.agl_profile`). Egress inverts the profile: the
valley bought surprise and the halls spend it, so the way home is a hard climb
south-west over the range and feet wet past the Abkhaz coast.

Three objectives, priced separately (`_add_end_triggers`):

  1. the two casting halls at the works — the frag, and what four GBU-12s are
     aboard for;
  2. the night's shipment, which leaves the loading yard the moment the field
     hears the raid coming and drives north up the Kuban. `Ferret`, a recon
     team on the ridge, calls it and lases it; catching it costs bombs the
     halls may still need, and the road it is on runs into the one battery
     nobody could fix;
  3. getting home. The alert section at Mineralnye Vody launches when the halls
     go up, and the climb out of the valley is flown inside the Buk's ring.

Composition (difficulty: ace):
  - SA-11 Buk site on the plain 14 km north of the works (Skill Excellent),
    the reason there is no approach above the ridges.
  - S-125 battery on the works itself (Skill High) + 2x 2S6 Tunguska and
    2x ZSU-23-4 inside the wire (Skill High / Average).
  - 2x 55G6 EWR — one on the plain feeding GCI, one in the western foothills
    watching the egress.
  - 2x Igla MANPADS teams in the Teberda valley (Skill Average) — the price of
    being predictable on the low route.
  - 1x SA-6 battery on the plain north of the Kuban bend, **on no map and in no
    cartridge**: the briefing names the gap, `Magic` calls the emitter when the
    player goes north, and it only bites somebody who chases the shipment past
    the bend.
  - A Russian MiG-29S alert section at Mineralnye Vody — a pair up to two coop
    slots, a four-ship above that — cold on the ramp and scrambled when the
    first casting hall goes up. Deliberately **not** a tasked kill.
  - Every radar-guided site is in one Skynet net (`core/iads.py`): nothing
    radiates until the early-warning chain hands it a track, which is what
    makes the valley run worth flying.
  - USA support: E-3A `Magic` (251.000 AM) and KC-135 `Texaco` (253.000 AM,
    TACAN 12X), both south of the watershed. No escort and no SEAD element —
    the flight covers itself, which is what its second fit is for.
  - Weather: October first light, broken layer based at 4500 m — six hundred
    metres over the Klukhori crossing, and the egress climbs through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence, cast

from dcs import action, condition, planes, statics, task, templates, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.point import PointAction
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, StaticGroup, VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    kneeboard,
    laser,
    loadout,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.iads import Listener, Site, arm_iads
from dcs_mission_creator.core.jtac import CoordTarget, arm_jtac_coords
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MIN_PLAYERS, MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    offset,
    player_flight,
    race_track,
    set_skill,
)
from dcs_mission_creator.core.placement import (
    convoy_spawn,
    ewr_high_ground,
    find_clear_spot,
    load_scene,
    manpads_in_valley,
    observation_post,
    sam_site_on_ridge,
)
from dcs_mission_creator.core.tasking import (
    FacCallsign,
    apply_ai_difficulty,
    fac_attack_group,
    scramble_on_trigger,
)
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.waypoints import Leg
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.placement import Placement, Vegetation
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene

#: What the briefing claims each emplaced system reaches, before the ace reveal
#: coarsens it. These are the DCS envelopes rather than the brochure figures —
#: the Buk's 35 km is what the game models, while `dtc.SA_11` carries the 50 km
#: the jet's own threat table prints, and the ring the player is shown has to be
#: the one that will actually shoot at him.
_SA11_RING_M = 35_000.0
_SA3_RING_M = 18_000.0
_SA19_RING_M = 8_000.0

#: Commanded true airspeeds are **km/h** — the unit every pydcs speed argument
#: takes and none of them names — and they live in `_CORRIDOR` / `_EGRESS`
#: beside the altitude they are flown at, because a speed is half of a profile
#: and splitting the two across the file is how they drift apart. On an
#: F-16C-50 (`max_speed` 2120 km/h) a cruise sits at 0.30-0.40 of that: 780 on
#: the lowland transit and the climb home (0.37), 680 in the valleys (0.32,
#: which is also what a 500 m run between three-kilometre walls is worth), and
#: the run-in below. This jet belongs at the bottom of the band — four GBU-12
#: on two TERs, two bags, a pod and a jammer is the heaviest air-to-ground fit
#: in the project after `idlib_gauntlet`'s Hornet.
_ATTACK_SPEED_KPH = 640.0

#: How far above the ground the whole flown route has to stay, at a waypoint and
#: along the straight leg between two of them. `core/waypoints.py` enforces it.
_LEG_CLEARANCE_M = 150.0

#: Radios, codes and the numbers a real card would carry.
_FREQ_AWACS = 251
_FREQ_TANKER = 253
_TANKER_TACAN = "12X"
_FREQ_RECON = 133
#: The mission's one laser code. It was 1511, which nothing in the package was
#: ever on: the ME's FAC task carries no code field, so `Ferret` lases DCS's own
#: 1688 whatever a briefing says, and the Viper's four GBU-12s come up on 1688
#: too. `core/laser.py` owns that and refuses anything else.
_LASER_CODE = laser.DEFAULT_CODE

#: Mission-clock moments. `Ferret` has been on that ridge for six days, so a
#: scheduled check-in is what he would really make; the coordinate readout is
#: pushed after it, because a controller who reads out a position for something
#: he has not yet said he can see is a controller nobody believes.
_TANKER_CHECKIN_S = 200
_SANCTUARY_CHECKIN_S = 300
_RECON_CHECKIN_S = 1_200
_RECON_READOUT_S = 1_320

#: The ingress corridor: north-west across the Kolkheti lowland and the Abkhaz
#: coastal plain, inland up the Kodori valley system, over the **Klukhori Pass**
#: and down the Teberda into the Kuban. `(name, lat, lng, height above the
#: ground, commanded TAS in km/h)` — degrees rather than DCS metres because
#: every one of these is a real place, and a coordinate you can put on a map is
#: a coordinate somebody can check. `daryal_run` learned that the expensive way:
#: a route written in raw map metres shipped with two valley waypoints inside a
#: mountainside, one of them by 2.7 km, and nothing about reading
#: `Point(-200000, 863000)` says "mountain".
#:
#: **600 m over the valley floor, not 200**, and the number is measured rather
#: than chosen. At 250 m AGL this corridor needs twenty-three waypoints to keep
#: every straight leg out of the rock — the Klukhori saddle is 3,000 m with
#: 3,400 m walls a kilometre either side, and a jet cannot thread that in the
#: three or four points a cartridge can spare. At 600 m it needs eleven, and
#: measured against the elevation raster it is still masked from the Buk and
#: from both early-warning radars at every point down to `KARACHAY` — where the
#: Teberda turns toward the Kuban, 34 km short of the battery. The masking here
#: is the massif, not the last three hundred metres.
_CORRIDOR = (
    ("PUSH", 42.42, 41.90, 300.0, 780.0),  # climb-out over the Kolkheti lowland
    ("OCHAMCHIRA", 42.78, 41.62, 300.0, 780.0),  # the Abkhaz coastal plain
    ("KODORI", 43.008, 41.567, 600.0, 680.0),  # inland, into the valley system
    ("KLUKHOR", 43.235, 41.744, 900.0, 680.0),  # the watershed, 3,000 m
    ("GONACHKHIR", 43.290, 41.756, 600.0, 680.0),  # the gorge on the north side
    ("DOMBAY", 43.320, 41.692, 600.0, 680.0),
    ("AZGEK", 43.348, 41.678, 600.0, 680.0),  # into the Teberda
    ("TEBERDA", 43.410, 41.725, 500.0, 680.0),
    ("NIZHNYAYA", 43.506, 41.759, 350.0, 680.0),
    ("KARACHAY", 43.606, 41.866, 300.0, 680.0),  # the masking runs out here
    ("IP", 43.691, 41.898, 300.0, 680.0),  # the Teberda mouth. Pop point
)

#: Egress: climb hard to the south-west, over the range, feet wet past the
#: Abkhaz coast and home. **Low in, high out**, and that is a statement rather
#: than a shortcut. The valley bought surprise, and surprise is spent the moment
#: the halls go up: going back down the Teberda at five hundred metres with an
#: alert section inbound and every belt on the plain awake is worse than being
#: high, fast and outside the Buk's ring in two and a half minutes. It is also
#: the only egress the cartridge can afford — the Marukh, the one other pass the
#: Kodori reaches, is a valley so tortuous it costs eleven waypoints on its own,
#: which is the whole navigation tab.
_EGRESS = (
    ("EGRESS_SW", 43.60, 41.70, 3_300.0, 800.0),  # climbing out of the Kuban
    ("RANGE", 43.15, 41.45, 3_900.0, 800.0),  # over the watershed, ~5,600 m
    ("FEET_WET", 42.70, 41.30, 3_000.0, 800.0),  # past the Abkhaz coast
    ("LETDOWN", 42.40, 41.90, 1_300.0, 780.0),
)

#: Where the works go, and where the shipment is driven. Both are anchors for a
#: raster search rather than final positions — see `_setup_airports`.
_WORKS_ANCHOR = (43.86, 41.90)
_SHIPMENT_DESTINATION = (44.05, 41.97)
_UNFIXED_SAM_ANCHOR = (44.14, 41.99)
_EWR_PLAIN_ANCHOR = (44.00, 41.98)
_EWR_WEST_ANCHOR = (43.93, 41.58)

#: Cells a building may not be put on. The Kuban runs 200 m west of the works,
#: so this is not hypothetical: the first hand-written oxidiser farm had two of
#: its three tanks in the river.
_NO_BUILD = (Vegetation.WATER, Vegetation.DENSE_FOREST)

#: The works, as a plot plan in metres east/north of the site centre. A compact
#: complex on purpose — about 400 m across — because four GBU-12s against a
#: two-kilometre industrial sprawl is not a strike, it is a survey.
_PLANT_LAYOUT = {
    "hall_a": (0.0, 0.0),
    "hall_b": (0.0, 220.0),
    "oxidiser_1": (190.0, -110.0),
    "oxidiser_2": (190.0, -30.0),
    "oxidiser_3": (190.0, 50.0),
    "stores": (160.0, -190.0),
    "boiler": (-90.0, 350.0),
}
_PLANT_HEADING = 350

#: What stands on each plot: the key into `_PLANT_LAYOUT`, what the group is
#: called, and the static type. The two casting halls are the frag and are named
#: as such — `_add_end_triggers` gates the success call on them by unit id, and
#: a name a player can read in the debrief is worth having.
_PLANT_BUILDINGS = (
    ("hall_a", "casting hall A", statics.Fortification.Workshop_A),
    ("hall_b", "casting hall B", statics.Fortification.Workshop_A),
    ("oxidiser_1", "oxidiser tank 1", statics.Fortification.Chemical_tank_A),
    ("oxidiser_2", "oxidiser tank 2", statics.Fortification.Chemical_tank_A),
    ("oxidiser_3", "oxidiser tank 3", statics.Fortification.Chemical_tank_A),
    ("stores", "finished stores", statics.Warehouse.Warehouse),
    ("boiler", "boiler house", statics.Fortification.Boiler_house_A),
)

#: Trigger geometry. The Teberda zone is where `Ferret` calls the yard and the
#: shipment starts rolling; the northern zone is where `Magic` names the gap in
#: the picture. Both are checked against `Ferret`'s own position — a
#: `PartOfCoalitionInZone` counts *any* blue unit, and a recon team sitting
#: inside the zone would fire the trigger before the player had left the ramp.
_TEBERDA_ZONE = (43.46, 41.76)
_TEBERDA_ZONE_R = 18_000
_NORTH_ZONE = (44.03, 41.96)
_NORTH_ZONE_R = 10_000

#: How many jets Mineralnye Vody keeps on alert. Two constraints meet here and
#: the smaller one wins. The **magazine**: the fit below leaves four air-to-air
#: rails and two shots per kill is the planning factor against `Skill.Excellent`
#: crews, so one player jet is worth about two kills — which makes a pair the
#: whole budget for a single-slot sortie. And **doctrine**: a field scrambles
#: what it has sitting on alert, which is a section, not a regiment. So this
#: goes to four and stops, rather than tracking `--players` upward into a sky
#: nobody could clear. It does not need to track it, because the MiGs are
#: deliberately **not** a tasked kill: the frag is the works, these are what the
#: egress has to survive, and the briefing says so in as many words.
_ALERT_SECTION = (2, 4)

#: Senaki's own air defence. `Colt` goes 181 km with no escort and no Weasel, and the one thing that makes turning for home a plan rather than a
#: slower loss is that it ends somewhere. Kutaisi is 37 km away and inside the
#: same envelope, so a jet that cannot fly a normal approach has two runways.
_SANCTUARY = "PALISADE"
_SANCTUARY_BATTERY = sanc.HAWK


@dataclass(frozen=True)
class _Plant:
    """The works, as the trigger layer sees it: two halls and everything else.

    The halls are what the frag is written against, so they are named rather
    than indexed — `_add_end_triggers` gates the success call on both of them
    and nothing else at the site counts.
    """

    hall_a: StaticGroup
    hall_b: StaticGroup
    others: tuple[StaticGroup, ...]


@dataclass
class _Scene:
    """Resolved airports + key positions used by every spawn step."""

    senaki: Airport
    kutaisi: Airport
    mineralnye_vody: Airport
    works: Point
    sa11_pos: Point
    sa3_pos: Point
    shorad_pos: Point
    ewr_positions: tuple[Point, ...]
    manpads_positions: tuple[Point, ...]
    unfixed_pos: Point
    shipment_origin: Point
    shipment_destination: Point
    shipment_watch: tuple[Point, ...]
    recon_post: Point
    ingress: tuple[Leg, ...]
    egress: tuple[Leg, ...]
    overlay: TacticalScene

    @property
    def ip(self) -> Point:
        """The pop point: the last corridor point, where the Teberda opens."""
        return self.ingress[-1].position


#: How `Colt` splits the frag across its slots (`core/loadout.py`).
#:
#: **Four bombs stay four bombs**, and that is the point of the split rather
#: than an accident of it. Two casting halls, a shipment that rolls when the
#: field hears the raid, and one magazine to spend across both is the decision
#: this mission is built on — so the second jet does not carry a fifth bomb. It
#: carries the air-to-air fit, because the other half of the sortie is a climb
#: out of the Teberda inside the Buk's ring with a MiG-29S alert section
#: launching off the halls going up, and the briefing has always said there is
#: no escort coming.
#:
#: A four-slot flight puts a second bomber up and the decision softens; a pair
#: flies the sortie as written, with somebody covering the egress.
#:
#: Both are ED payloads station for station, off
#: `<DCS>/CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua`
#: (`AIM-120C*2, AIM-9X*2, GBU-12*4, FUEL*2, ECM, TGP` and
#: `AIM-120C*4, AIM-9X*2, FUEL*2, ECM, TGP`) with one deliberate substitution:
#: the pod is the AN/AAQ-33 rather than the LITENING, on both jets, because the
#: talk-on with `Ferret` happens at 600 m over a valley floor and this mission
#: picked the better sensor for it.
_FITS = (
    loadout.Loadout(
        role="GBU-12*4",
        carries=(
            f"four GBU-12 on TERs coded {_LASER_CODE}, AN/AAQ-33 pod, "
            "two AIM-120C, two AIM-9X, ALQ-184, two 370 gal"
        ),
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_9X_Sidewinder_IR_AAM"),
            (3, "TER_9A_with_2_x_GBU_12___500lb_Laser_Guided_Bomb"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "TER_9A_with_2_x_GBU_12___500lb_Laser_Guided_Bomb_"),
            (8, "AIM_9X_Sidewinder_IR_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (11, "AN_AAQ_33___Advanced_Targeting_Pod"),
        ),
    ),
    loadout.Loadout(
        role="AIM-120C*4",
        carries=(
            "four AIM-120C, two AIM-9X, AN/AAQ-33 pod, ALQ-184, two 370 gal — "
            "the cover for the climb out of the valley"
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
            (11, "AN_AAQ_33___Advanced_Targeting_Pod"),
        ),
    ),
)


class KubanForge(MissionBuilder):
    name = "kuban_forge"
    title = "Kuban Forge"
    difficulty = Difficulty.ACE

    #: 06:50 map-local on 18 October 2026 — twilight at the saddle.
    #:
    #: Sunrise is about 07:05 there in the third week of October, and the
    #: sortie is fifty minutes to the target — so the crossing is flown in
    #: first light and the run-in in daylight, which is what a targeting pod
    #: and a laser actually want.
    start_time = datetime(2026, 10, 18, 6, 50, 0, tzinfo=timezone.utc)

    #: October first light: broken layer at 4500 m, light N wind, 6 C, 25 km.
    weather = Weather(
        name="October first light",
        season_temperature=6.0,
        clouds_base=4500,
        clouds_thickness=1200,
        clouds_density=6,
        visibility_distance=25000,
        wind_at_ground=Wind(0, 4),
        wind_at_2000=Wind(10, 6),
        wind_at_8000=Wind(350, 9),
    )

    def __init__(self, *, players: int = MIN_PLAYERS) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""KUBAN FORGE — Caucasus, 18 Oct 2026, 06:50 local
==================================================
SITUATION
  The chemical works on the Kuban north of
  Karachayevsk have been casting solid rocket
  motors since the spring. Every battery this
  theater has run into for six months has been
  fed from that valley. Two casting halls do the
  work; everything else on the site can be
  rebuilt in a fortnight.

  The works are in a valley because that is where
  they were built to be. There is no approach to
  them above the ridges: a Buk battery on the
  plain north of the site owns that airspace out
  to about 35 km, and you carry nothing that will
  argue with it. What you have instead is the
  ground. North-west over the Kolkheti flats and
  the Abkhaz coast, inland up the Kodori, over
  the Klukhori Pass and down the Teberda. Six
  hundred metres over the valley floor is enough
  — it is the massif doing the work, not the last
  three hundred feet. Nothing on the far side
  sees you until the Teberda turns toward the
  Kuban, about 34 km short of the battery.

MISSION (Colt — F-16C-50, Senaki, hot ramp)
  - Take Texaco before you turn north-west. You
    want full internal fuel at the coast, not at
    the ramp.
  - Feet dry over the Abkhaz plain low and quick.
    Everything from Ochamchira on is theirs.
  - Inland up the Kodori and over Klukhori at
    3,000 m of rock — you cross at about 3,900,
    six hundred under the layer. Hold 600 over
    the floor and do not crest a ridge to save a
    turn.
  - Down the Teberda. You will be seen somewhere
    around Karachayevsk; from there it is two and
    a half minutes to the pop, so be ready before
    you are detected, not after.
  - Pop at IP. Kill BOTH casting halls. Two long
    sheds in line, north-south, in the middle of
    the yard and west of the tank farm. Ferret
    talks you on.
  - The night's shipment is loaded and will roll
    the moment they hear you coming. If you have
    bombs left, it is worth more than the tank
    farm is. Ferret lases it, code {_LASER_CODE} — the
    same code your four GBU-12s are on.
  - Egress is a CLIMB, south-west, hard. You will
    be inside the Gadfly's ring for about two and
    a half minutes and there is no low way out of
    that valley once the halls are burning. Over
    the range, feet wet, home.
  - Do NOT go north past the Kuban bend.
  - RTB Senaki. Divert: Kutaisi.

LOADOUT (four bombs stay four bombs)
{self.loadout_brief("Colt", _FITS)}
  No HARM anywhere — there is no Weasel answer to this
  one. Slot 2 carries no bomb either: it is the cover for
  the climb out of the valley.

PACKAGE
  Colt         : F-16C-50 pair, Senaki, hot ramp. Loadout
                 above.
  Magic        : E-3A AWACS, {_FREQ_AWACS}.000 AM, over
                 western Georgia. He is also the
                 only receiver we have pointed at
                 those radars.
  Texaco       : KC-135, {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN},
                 north of Kutaisi.
  Ferret       : recon team on the ridge above the
                 works, {_FREQ_RECON}.000. Six days in place.
  No escort. No SEAD. The flight covers itself.

INTELLIGENCE
  Ferret has had eyes on the works since the 12th,
  so the site itself is not in question — that is
  a survey, and his coordinate readout is on your
  radio menu in your own cockpit's format.
  The air defence is the part we are guessing at.
  Overhead has been thin for a week and what we
  have is an ELINT cut, so every red ring on your
  map is drawn wide, dashed and marked approximate.
  SAM : Buk-class battery on the plain north of
        the works, assessed 35 km, their best crew.
        S-125 on the site itself, roughly 18 km,
        and self-propelled gun/missile SHORAD plus
        guns inside the wire. Assume all of it is
        cold until something hands it a track.
  EWR : two search radars — one on the plain, one
        in the western foothills. The western one
        looks straight down your egress.
  Air : Mineralnye Vody holds a MiG-29S alert
        section, R-77 shooters, experienced. They
        will launch when the halls go up. You are
        not fragged against them.
  GAP : the picture north of the Kuban bend is
        thin. A Gainful-class emitter came up out
        there ten days ago and we never got a fix,
        so there is no ring on your map for it —
        we would be drawing a guess. It touches
        nothing you have been asked to fly. It
        will absolutely touch you if you chase the
        shipment past the bend.
  Base: Mineralnye Vody is defended in its own
        right — an S-125 on the field. It is 104 km
        from the works and reaches 18. It costs
        your run nothing and a chase everything.

ROE / FRAGS
  - Weapons free on the works, the shipment, and
    any Russian aircraft that comes up against you
    north of the watershed.
  - Both casting halls, or the sortie was a raid
    on a tank farm.
  - 600 m over the floor from the Kodori to the
    Teberda. Cresting a ridge hands them the
    whole plan.
  - Not cleared to pursue over Mineralnye Vody.
  - Not cleared north of the Kuban bend.
  - Bingo fuel: 3000 lb. RTB Senaki over the top.

FALL-BACK ({_SANCTUARY})
  Senaki and Kutaisi both sit under a
  {_SANCTUARY_BATTERY.name} battery — {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km,
  cyan ring on the map, guns in the Senaki
  overhead. You are 181 km out with nobody behind
  you; if this has gone wrong, the ring is the
  plan. {_SANCTUARY} MARSHAL is a hold abeam
  Senaki, on the map and in the DED. Either
  runway takes you.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  PUSH                : climb-out over the Kolkheti.
  OCHAMCHIRA          : the Abkhaz coastal plain.
  KODORI              : inland, into the valleys.
  KLUKHOR             : the watershed, 3,000 m.
  GONACHKHIR / DOMBAY : down the northern side.
  AZGEK / TEBERDA     : into the Teberda valley.
  NIZHNYAYA / KARACHAY: the masking runs out here.
  IP                  : the Teberda mouth. Pop.
  TARGET              : the works. Surveyed, not
                        assessed — this one is real.
  EGRESS_SW           : the climb out of the Kuban.
  RANGE               : over the watershed, high.
  FEET_WET            : past the Abkhaz coast.
  LETDOWN             : let-down for Senaki.

FREQUENCIES
  Magic AWACS   : {_FREQ_AWACS}.000 AM
  Texaco tanker : {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}
  Ferret        : {_FREQ_RECON}.000, laser {_LASER_CODE}
                  (your GBU-12s are on the same code)
  Senaki tower  : per kneeboard

NOTES
  Sunrise 07:05 local — you cross the saddle in
  twilight and you are over the target in early
  light. Broken layer based 4500 m. That is six
  hundred metres over the Klukhori, so the pass
  is flown under a ceiling; on the way out you
  climb straight through it. Fly the card.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Kuban Forge

**Theater:** Caucasus
**Date / time:** 18 October 2026, 06:50 local (first light)
**Player aircraft:** F-16C-50 (`Colt`), Senaki-Kolkhi, hot ramp
**Players:** {self.slot_summary("Colt")}
**Difficulty:** ace
**Expected sortie length:** ~55 minutes

## Situation

The chemical works on the Kuban north of Karachayevsk have spent the year
casting solid rocket motors. Every surface-to-air battery this theater has run
into for six months has been fed out of that valley, and the works are in a
valley because that is where somebody decided to build them: there is no
approach to the site above the ridgelines. A Buk battery on the plain to the
north owns that airspace out to roughly 35 kilometres, and this sortie carries
nothing that will argue with it.

**Two casting halls do the work.** Everything else on the site — the tank farm,
the stores, the boiler house — can be rebuilt in a fortnight. The halls cannot.

## Mission

Take `Texaco` before turning north-west, so that the low run starts on full
internal fuel rather than on what is left after the transit. Then fly the
ground:

1. **North-west over the Kolkheti flats and the Abkhaz coastal plain**, low and
   quick. Everything from Ochamchira on is theirs.
2. **Inland up the Kodori** valley system and over the **Klukhori Pass** — three
   thousand metres of rock, flown at six hundred over the floor.
3. **Down the Teberda.** You will be picked up somewhere around Karachayevsk;
   from there it is about two and a half minutes to the pop, so be ready before
   you are detected rather than after.
4. **Pop at `IP`**, where the Teberda opens into the Kuban.
5. **Both casting halls.** Two long sheds in line, north-south, in the middle
   of the yard and west of the tank farm. `Ferret` talks you on. Everything
   else on that site — the tank farm, the stores, the boiler house — is a
   fortnight's work to replace and is not what you came for.
6. **Egress is a climb.** South-west, hard, over the range and feet wet past the
   Abkhaz coast. There is no low way out of that valley once the halls are
   burning, and the Gadfly's ring costs about two and a half minutes on the way
   through it.

The route is checked leg by leg against the elevation raster and the whole of
it — every waypoint and every ramp between two of them — clears the ground. The
kneeboard prints altitudes; the heights quoted in the briefing are above the
valley floor, which is always a smaller number. Fly the card.

## Package

| Callsign | Type     | Base            | Role                                   |
|----------|----------|-----------------|----------------------------------------|
| Colt     | F-16C-50 | Senaki-Kolkhi   | Player strike — the casting halls      |
| Magic    | E-3A     | Senaki-Kolkhi   | AWACS, {_FREQ_AWACS}.000 AM, western Georgia    |
| Texaco   | KC-135   | Kutaisi         | Tanker, {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}          |
| Ferret   | recon    | on the ridge    | Talk-on and laser, {_FREQ_RECON}.000, code {_LASER_CODE} |

No escort and no SEAD element. **And no HARM** — that is the shape of this
sortie rather than an oversight. The belts here cannot be shot; they can only be
avoided, so the terrain is the SEAD and the GBU-12s are all for the target. They
are coded `{_LASER_CODE}`, which is where the pod comes up and where `Ferret`
lases, so his spot and the seekers need no arranging.

### `Colt` loadout

{self.loadout_table("Colt", _FITS)}

**Four bombs stay four bombs**, and that is the point of the split rather than
an accident of it. Two casting halls, a shipment that rolls the moment the field
hears the raid, and one magazine to spend across both is the decision this
mission is built on — so slot 2 does not carry a fifth bomb. It carries the
air-to-air fit, because the other half of the sortie is a climb out of the
Teberda inside the Buk's ring with an alert section launching off the halls
going up, and nothing else is coming. Four slots put a second bomber up and the
decision softens.

## Objectives

Three, and they are priced separately.

- **The casting halls.** The frag. Two aimpoints, four bombs, and no second
  pass worth taking.
- **The shipment.** The night's load is on transporters in the yard and will
  roll the moment the field hears the raid coming, north up the Kuban.
  `Ferret` calls it and lases it on code {_LASER_CODE}, which is your bombs' own
  code, and his coordinate readout — in *your* cockpit's format, off a live
  vehicle — is on the radio menu under **F10 → Other → Ferret**. Catching it is
  worth more than the tank farm is. It also costs bombs the halls may still
  need, which is the decision.
- **Getting out.** The alert section launches when the halls go up, and the
  climb out of that valley is flown inside the Buk's ring.

## Intelligence

`Ferret` has been on that ridge since the 12th, so the works themselves are not
in question — that is a survey rather than an assessment, and it is why the
`TARGET` steerpoint is where the target is. A chemical plant has been on the
1:100,000 sheet for forty years; nobody needs an overhead pass to find it.

**The air defence is the part we are guessing at.** Overhead has been thin for a
week and what we have is an ELINT cut, so every red ring on the F10 map and on
your HSD is drawn wide, dashed and labelled `(approx.)` — out by kilometres, and
meant to be read that way.

- **SAM (boss):** a Buk-class battery on the plain north of the works, assessed
  at about 35 km against a jet at altitude. That ring is the whole reason for
  this routing: it covers every approach above the ridges, which leaves the
  valleys.
- **Site defence:** an S-125 battery on the works, roughly 18 km, plus
  self-propelled gun/missile SHORAD and guns inside the wire. The SHORAD is
  what the pop-up actually has to survive.
- **EWR:** two search radars — one on the plain feeding the fighter control,
  one in the western foothills that looks straight down the egress. Expect to
  be seen on the way out even though you were not on the way in.
- **Emission discipline:** these crews do not sit on the air. Assume every
  battery is cold until the early-warning chain hands it a track, and assume it
  is quick once it has one. A quiet RWR on the ingress is the plan working, not
  the threat being absent.
- **Air:** a MiG-29S alert section at Mineralnye Vody, R-77 shooters,
  experienced crews. They launch when the halls go up. **They are a threat to
  beat, not a target list** — nothing about this sortie requires them dead.
- **Where the picture is thin:** a Gainful-class emitter came up on the plain
  north of the Kuban bend ten days ago and we never got a fix on it. There is
  no ring on your map because we would be drawing a guess. It reaches nothing
  you have been asked to fly — and it covers the last third of the road the
  shipment is driving up. `Magic` will call it if you go there.
- **Mineralnye Vody field defence:** the same ELINT work puts an S-125 on the
  field. It is 104 km from the works and reaches 18, so it touches no part of
  the run. It is the reason a withdrawing MiG stops being a target.

## ROE

- Weapons free on the works, the shipment, and any Russian aircraft that comes
  up against you north of the watershed.
- **Both** casting halls. One hall and a tank farm is a raid, not a strike.
- Six hundred metres over the valley floor from the Kodori to the Teberda —
  the massif is what hides you, and cresting a ridge to save a turn hands them
  the whole plan.
- **Not cleared to pursue over Mineralnye Vody.**
- **Not cleared north of the Kuban bend**, whatever is driving up it.
- Bingo fuel: 3000 lb. RTB Senaki over the top, feet wet past the coast.

## Fall-back

Senaki is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10 map, with gun sections in
the overhead. Kutaisi is 37 km away and **inside the same envelope**, so a jet
that cannot fly a normal approach has two runways under one battery.

`{_SANCTUARY} MARSHAL` is a hold abeam Senaki inside the envelope, on the map and
in the DED, for a damaged jet waiting on the pattern. It matters here because
there is nobody else out there: no escort to trade with, one other jet in the
flight, and 190 km of egress with an alert section behind it. Crossing that
ring is what ends the sortie.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- `PUSH` — climb-out over the Kolkheti lowland
- `OCHAMCHIRA` — the Abkhaz coastal plain. Enemy ground from here on
- `KODORI` — inland, into the valley system
- `KLUKHOR` — the watershed, 3,000 m. The high point of the route
- `GONACHKHIR`, `DOMBAY` — the gorge on the northern side
- `AZGEK`, `TEBERDA` — into the Teberda valley
- `NIZHNYAYA`, `KARACHAY` — down the Teberda. The masking runs out here
- `IP` — the Teberda mouth. Pop here
- `TARGET` — the works. Surveyed, not assessed
- `EGRESS_SW` — the climb out of the Kuban
- `RANGE` — over the watershed, high and fast
- `FEET_WET` — past the Abkhaz coast
- `LETDOWN` — let-down for Senaki

## Frequencies

- Magic AWACS: {_FREQ_AWACS}.000 AM
- Texaco tanker: {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}
- Ferret: {_FREQ_RECON}.000, laser code {_LASER_CODE}
- Senaki tower: per kneeboard
- `{_SANCTUARY}` details and the Kutaisi divert are on the kneeboard comms card.

## Weather

October first light. Broken layer based 4500 m, 1200 m thick, density 6. It
clears the Klukhori crossing by six hundred metres, and the egress climbs
straight through it — climbing out of trouble on this sortie means going IMC
over five-thousand-metre rock. Light north wind, 4 m/s ground, 9 m/s at
8000 m. 6 °C, visibility 25 km in autumn haze. Sunrise about 07:05 local.

## Difficulty composition

**Ace.** Excellent Buk crew and High site defence, all of it emission-
disciplined and cold until cued; two EWRs; an unlocated Gainful north of the
objective; Excellent MiG-29S alert section scaled off the player count;
**no anti-radiation weapon and no SEAD support**, so the belts can only be
avoided; AWACS and tanker only, both behind the watershed; a 160 km low ingress
over a 3,000 m pass, 190 km of enemy ground each way, and an egress flown inside
the Buk's ring because there is no low way out. One mistake ends the sortie.

## Win / loss conditions

- **Primary success:** both casting halls are down — the works stop producing.
- **Secondary:** the night's shipment never leaves the valley.
- **Failure:** `Colt` goes down with the halls still standing.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- top-level orchestration --------------------------------------------

    def _assemble(self, m: Mission) -> MapOverlay:
        """Assemble the mission by calling each step in package order."""
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        plant = self._spawn_red_plant(m, russia, scene)
        shipment = self._spawn_red_shipment(m, russia, scene)
        sa11 = self._spawn_red_sa11(m, russia, scene)
        sa3 = self._spawn_red_plant_sam(m, russia, scene)
        shorad = self._spawn_red_plant_shorad(m, russia, scene)
        _aaa = self._spawn_red_plant_aaa(m, russia, scene)
        ewrs = self._spawn_red_ewr(m, russia, scene)
        _manpads = self._spawn_red_valley_manpads(m, russia, scene)
        unfixed = self._spawn_red_unfixed_sam(m, russia, scene)

        magic, awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        ferret = self._spawn_recon_team(m, usa, scene, shipment=shipment)
        colt, route = self._spawn_player(m, usa, scene)
        migs = self._spawn_red_alert_fighters(m, russia, scene)

        home, minvody_ad = self._spawn_sanctuaries(
            m,
            usa,
            russia,
            scene,
            route=route,
            stations=(*awacs_track, *tanker_track, scene.recon_post),
        )
        sanc.remark_all(m, home, minvody_ad)

        self._add_iads(
            m,
            magic=magic,
            sa11=sa11,
            sa3=sa3,
            shorad=shorad,
            ewrs=ewrs,
            unfixed=unfixed,
            minvody_ad=minvody_ad,
        )
        self._add_intro_voice(m)
        self._add_support_checkins(m, home)
        self._add_recon_readout(m, shipment=shipment)
        self._arm_recon_laser(m, ferret=ferret, shipment=shipment)
        self._add_valley_trigger(m, shipment=shipment)
        self._add_north_warning_trigger(m)
        self._add_recon_loss_trigger(m, ferret=ferret)
        self._add_scramble_triggers(m, plant=plant, migs=migs)
        self._add_end_triggers(m, plant=plant, shipment=shipment, colt=colt)

        self._conceal_red(russia)
        # One overlay for every reveal channel: the F10 plan, the cockpit
        # cartridge and the kneeboard's threat block all make the same claim,
        # and the difficulty policy that decides how much they claim lives in it.
        plan = PlanOverlay(m, self.difficulty)
        briefed_threats = self._draw_plan(
            m,
            scene,
            plan=plan,
            route=route,
            shipment=shipment,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
            home=home,
            minvody_ad=minvody_ad,
        )
        self._load_cartridge(m, scene, briefed_threats, plan=plan)
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- airports ------------------------------------------------------------

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim the Georgian fields for blue, derive the works and the threats.

        Every enemy position here is a raster search rather than a typed
        coordinate, because all of them have a constraint that the map decides:
        the works want flat, unbuilt ground beside the Kuban road; the Buk wants
        prominence and line of sight to what it defends; the early-warning
        radars want high ground; the MANPADS want the valley floor the route
        actually flies down. The corridor is the exception and is written out
        in degrees — see `_CORRIDOR`.
        """
        t = self._terrain
        senaki = t.airports["Senaki-Kolkhi"]
        kutaisi = t.airports["Kutaisi"]
        mineralnye_vody = t.airports["Mineralnye Vody"]
        senaki.set_blue()
        kutaisi.set_blue()
        mineralnye_vody.set_red()

        scene = load_scene("caucasus")
        ov = scene.overlay
        works = find_clear_spot(
            ov,
            self.at(*_WORKS_ANCHOR),
            t,
            radius_m=1_500,
            require=Placement(
                near_road_m=400,
                max_slope_deg=8,
                not_in=_NO_BUILD,
                not_in_built_up=True,
            ),
        )
        # The Buk goes on the plain *north* of the works rather than between the
        # works and the player: what it is for is denying altitude everywhere,
        # and the one place its own line of sight fails is the valley floor the
        # route comes up. Threat axis north, so it sits on the open ground where
        # it can see the site and the whole Kuban bend.
        sa11_pos = sam_site_on_ridge(
            scene, works, threat_axis_deg=0, envelope_radius_m=20_000
        )
        # The site defence is emplaced off the plot rather than on it: an S-125
        # fires from built revetments and no crew puts them where a stick meant
        # for the halls lands. Both positions live here rather than inside their
        # spawn helpers because `_draw_plan` rings them, and a ring drawn on the
        # works while the battery is a kilometre and a half east would be the
        # map contradicting the ground for no reason.
        sa3_pos = find_clear_spot(
            ov,
            offset(works, east_m=1_500, north_m=-400),
            t,
            radius_m=1_200,
            require=Placement(max_slope_deg=12, not_in=_NO_BUILD, not_in_built_up=True),
        )
        shorad_pos = find_clear_spot(
            ov,
            offset(works, east_m=-350, north_m=-500),
            t,
            radius_m=800,
            require=Placement(max_slope_deg=15, not_in=_NO_BUILD),
        )
        ewr_positions = (
            ewr_high_ground(
                scene,
                self.at(*_EWR_PLAIN_ANCHOR),
                radius_m=15_000,
                min_elevation_m=600,
                min_prominence_m=40,
            ),
            ewr_high_ground(
                scene,
                self.at(*_EWR_WEST_ANCHOR),
                radius_m=15_000,
                min_elevation_m=700,
                min_prominence_m=40,
            ),
        )
        manpads_positions = (
            manpads_in_valley(scene, self.at(43.50, 41.77), radius_m=6_000),
            manpads_in_valley(scene, self.at(43.66, 41.885), radius_m=6_000),
        )
        unfixed_pos = find_clear_spot(
            ov, self.at(*_UNFIXED_SAM_ANCHOR), t, radius_m=6_000
        )

        shipment_origin = convoy_spawn(
            scene, offset(works, north_m=700), radius_m=4_000
        )
        shipment_destination = convoy_spawn(
            scene, self.at(*_SHIPMENT_DESTINATION), radius_m=8_000
        )
        # Two points on the road eight kilometres apart, plus the works. One
        # watch point is satisfied by any hollow that can see one watch point;
        # asking for a spread is a different request, and it is the one a real
        # party would make — this post sees about half of the twenty-one
        # kilometres the shipment has to drive, and that stretch is the window.
        shipment_watch = (
            works,
            shipment_origin,
            convoy_spawn(
                scene,
                Point(
                    shipment_origin.x
                    + (shipment_destination.x - shipment_origin.x) * 0.45,
                    shipment_origin.y
                    + (shipment_destination.y - shipment_origin.y) * 0.45,
                    t,
                ),
                radius_m=4_000,
            ),
        )
        recon_post = observation_post(
            scene, shipment_watch, radius_m=11_000, max_standoff_m=9_000
        )

        return _Scene(
            senaki=senaki,
            kutaisi=kutaisi,
            mineralnye_vody=mineralnye_vody,
            works=works,
            sa11_pos=sa11_pos,
            sa3_pos=sa3_pos,
            shorad_pos=shorad_pos,
            ewr_positions=ewr_positions,
            manpads_positions=manpads_positions,
            unfixed_pos=unfixed_pos,
            shipment_origin=shipment_origin,
            shipment_destination=shipment_destination,
            shipment_watch=shipment_watch,
            recon_post=recon_post,
            ingress=tuple(
                Leg(name, self.at(lat, lng), agl, speed)
                for name, lat, lng, agl, speed in _CORRIDOR
            ),
            egress=tuple(
                Leg(name, self.at(lat, lng), agl, speed)
                for name, lat, lng, agl, speed in _EGRESS
            ),
            overlay=scene,
        )

    # -- red side: the target -----------------------------------------------

    def _spawn_red_plant(self, m: Mission, russia: Country, scene: _Scene) -> _Plant:
        """The works: two casting halls, an oxidiser farm, stores and a boiler house.

        Statics rather than vehicles, because this is a factory and a factory
        that is modelled as a truck park does not read as one from a targeting
        pod. Each building is its own group so the two that matter can be named
        and gated on individually — `_add_end_triggers` asks for both halls dead
        and nothing else at the site counts.

        The plot plan is deliberately compact, about 500 m across, and the
        halls' own spacing is the number that matters: 220 m is inside one
        run-in and well outside one GBU-12's effect. Closer and the two
        aimpoints collapse into one lucky pattern; further and the sortie needs
        two passes over a site with a Tunguska in the wire. The whole ordnance
        argument this mission is built on — two bombs for the halls, two for
        whatever else the sortie turns out to be worth — only exists in that
        band.

        Everything else on the plot is in **no** trigger: the oxidiser farm, the
        stores and the boiler house are there so that finding the right roof
        through the pod is a task rather than a formality, and so that a player
        who levels the tank farm has spent bombs rather than won.
        """
        built = {
            key: m.static_group(
                russia,
                f"Works — {title}",
                kind,
                position=self._plot(scene, offset(scene.works, east_m=e, north_m=n)),
                heading=_PLANT_HEADING,
            )
            for key, title, kind in _PLANT_BUILDINGS
            for e, n in (_PLANT_LAYOUT[key],)
        }
        return _Plant(
            hall_a=built["hall_a"],
            hall_b=built["hall_b"],
            others=tuple(v for k, v in built.items() if not k.startswith("hall")),
        )

    def _plot(self, scene: _Scene, position: Point) -> Point:
        """A building plot: the laid-out position, nudged off water or canopy.

        The Kuban runs two hundred metres west of the site, so this is not
        hypothetical — the first hand-written oxidiser farm put two of its three
        tanks in the river. The search radius is small on purpose: a plot that
        has to move more than a couple of hundred metres is no longer part of
        the same works.
        """
        ov = scene.overlay.overlay
        if ov.vegetation_at(position) in _NO_BUILD:
            return find_clear_spot(ov, position, self._terrain, radius_m=250)
        return position

    def _spawn_red_shipment(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """The night's load, parked in the yard and late-activated when the raid comes.

        Late activation is what keeps this honest rather than scripted. The
        column is not a second target the mission spawns out of nowhere: it is
        loaded and waiting at mission start in the same yard `Ferret` has been
        watching for six days, and it starts rolling when the field hears the
        player coming down the Teberda (`_add_valley_trigger`). By the time the
        works are on fire it is eight or ten kilometres up the road, which is
        the point — chasing it costs bombs the halls may still need, and the
        road it is on runs into the one battery nobody could fix.

        Road-bound, and both waypoints say so: pydcs writes waypoint 0 as
        `OffRoad` and a ground waypoint's action governs the leg *leaving* it,
        so `OnRoad` on the destination alone changes nothing about how the
        column drives there.
        """
        column_types = [
            vehicles.Armor.BTR_80,
            vehicles.Unarmed.KAMAZ_Truck,
            vehicles.Unarmed.KAMAZ_Truck,
            vehicles.AirDefence.Strela_10M3,
            vehicles.Unarmed.KAMAZ_Truck,
            vehicles.Armor.BTR_80,
        ]
        heading = int(
            scene.shipment_origin.heading_between_point(scene.shipment_destination)
        )
        column = m.vehicle_group_platoon(
            russia,
            "Shipment Kuznitsa",
            cast(list[type[VehicleType]], column_types),
            position=scene.shipment_origin,
            heading=heading,
            move_formation=PointAction.OnRoad,
        )
        for unit in column.units:
            unit.skill = (
                Skill.High
                if unit.type == vehicles.AirDefence.Strela_10M3.id
                else Skill.Average
            )
        column.add_waypoint(
            scene.shipment_destination,
            move_formation=PointAction.OnRoad,
            speed=40,  # km/h — a loaded road march, and about 30 min of window
        )
        column.late_activation = True
        apply_ai_difficulty(column, self.difficulty)
        return column

    # -- red side: air defence ----------------------------------------------

    def _spawn_red_sa11(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """The Buk on the plain: the reason there is no approach above the ridges.

        pydcs's own template, because it gets the thing that matters right — the
        Snow Drift search radar, the Fire Dome TELARs and the command post all in
        **one** group. Split the radar out and the launchers get no fire control
        and never shoot, which is the single most common way a SAM site in a
        generated mission turns out to be scenery.

        Dispersed afterwards. The template parks the whole battery inside a
        hundred metres, which makes one stick of two bombs the answer to a
        system this mission is built around never being answerable at all. At
        400 m the radar sits out in front of its own fan and the site has to be
        worked rather than deleted — and the footprint still fits well inside
        the 6 km offset the ace reveal puts on the drawn ring, so dispersing it
        cannot make the briefing wrong.
        """
        sa11 = templates.VehicleTemplate.sa11_site(
            m,
            russia,
            scene.sa11_pos,
            heading=180,
            prefix="Gadfly ",
            skill=Skill.Excellent,
        )
        return ad.disperse_site(
            sa11,
            radius_m=400.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_red_plant_sam(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """S-125 on the works — the battery that actually shoots at the run-in.

        Emplaced a kilometre and a half east of the plot so the site is not
        underneath its own target: an S-125 fires from built revetments and the
        crew would not have put them where a stick meant for the halls lands.
        High rather than Excellent — the Buk crew is the good one here, and a
        site every dial set to maximum reads as a fingerprint rather than as a
        threat layout.
        """
        return ad.build_sa3_site(
            m,
            russia,
            scene.sa3_pos,
            heading=190,
            launchers=4,
            prefix="Goa ",
            skill=Skill.High,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_red_plant_shorad(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """2x 2S6 Tunguska inside the wire — what the pop-up has to survive.

        This is the system the whole low route runs into at the end of it. The
        player arrives at 250 m over the valley floor with no anti-radiation
        weapon and no support beyond the flight itself, and a self-cueing
        gun/missile vehicle at 8 km is the price of that plan. It gets a ring on the map like anything else
        emplaced: it is parked at a fixed installation and it has no waypoints.
        """
        return ad.build_sa19_site(
            m,
            russia,
            scene.shorad_pos,
            heading=180,
            launchers=2,
            prefix="Grison ",
            skill=Skill.High,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_red_plant_aaa(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """2x ZSU-23-4 in the works perimeter — close-in cover for the second pass.

        Average crews. Guns are not what kills the player on the first run; they
        are what makes a re-attack on the same heading a bad idea, which is the
        whole argument for getting both halls in one pass.
        """
        pos = find_clear_spot(
            scene.overlay.overlay,
            offset(scene.works, east_m=450, north_m=350),
            self._terrain,
            radius_m=800,
            require=Placement(max_slope_deg=15, not_in=_NO_BUILD),
        )
        aaa = m.vehicle_group(
            russia,
            "AAA Kuznitsa",
            vehicles.AirDefence.ZSU_23_4_Shilka,
            position=pos,
            heading=180,
            group_size=2,
        )
        set_skill(aaa, Skill.Average)
        return aaa

    def _spawn_red_ewr(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[VehicleGroup]:
        """Two 55G6 search radars: one on the plain, one over the western foothills.

        They are the net's eyes and the only things in it that radiate from the
        start, which is what makes the rest of the layout work — every battery
        below sits cold until one of these hands it a track. Their siting is the
        mission's geometry stated as hardware: the plain radar looks north and
        east, away from the Teberda, so the ingress is quiet; the western one
        looks straight down the foothill run the egress uses, so the way out is
        seen even though the way in was not.
        """
        return ad.build_ewr_chain(
            m, russia, list(scene.ewr_positions), prefix="EWR Kuban", skill=Skill.High
        )

    def _spawn_red_valley_manpads(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[VehicleGroup]:
        """Igla teams on the Teberda valley floor — the price of being predictable.

        Two sections, deliberately small and deliberately without a radar: the
        low route is not free, but what it costs is a shoulder-launched missile
        the player can out-fly rather than an envelope they have to plan around.
        They are drawn on no map — a MANPADS team is not an emplaced envelope
        anybody rings, and the briefing carries the reach instead.
        """
        teams = []
        for i, pos in enumerate(scene.manpads_positions, start=1):
            team = m.vehicle_group(
                russia,
                f"MANPADS Teberda-{i}",
                vehicles.AirDefence.SA_18_Igla_S_manpad,
                position=pos,
                heading=180,
                group_size=2,
            )
            set_skill(team, Skill.Average)
            teams.append(team)
        return teams

    def _spawn_red_unfixed_sam(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """The Gainful nobody fixed: on no map, in no cartridge, north of the bend.

        The one withheld site, and it is withheld under the three conditions
        that make an intelligence gap different from a cheat. The briefing
        **names the hole** and sources it — an emitter that came up ten days ago
        and was never located, so there is no ring because a ring would be a
        guess. It **cannot touch the briefed plan**: 36 km from the works, 43 km
        from the IP and 43 km from the first egress point, against a system that
        reaches 25, so every point the player was told to fly has better than
        fifteen kilometres of margin. And it is aimed squarely at the
        **deviation** — it covers the last third of the road the shipment drives
        up, so it only ever bites somebody who chases the column past the Kuban
        bend after being told twice not to.

        `Magic` names it when the player crosses north (`_add_north_warning_
        trigger`), which is what makes it read as this morning's intelligence
        having a hole rather than as the mission spawning something. Nothing on
        the friendly side is planned around it: no ring, no cartridge point, no
        bend in a route.
        """
        sa6 = templates.VehicleTemplate.sa6_site(
            m,
            russia,
            scene.unfixed_pos,
            heading=180,
            prefix="Gainful ",
            skill=Skill.High,
        )
        return ad.disperse_site(
            sa6,
            radius_m=300.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

    def _spawn_red_alert_fighters(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[FlyingGroup]:
        """MiG-29S alert section(s) at Mineralnye Vody, cold until the halls go up.

        Hand-built rather than `Mission.intercept_flight`, and the reason is a
        trap worth naming: that helper wires its own `PartOfCoalitionInZone`
        trigger, and a coalition-in-zone condition counts **any** blue unit —
        `Ferret` sits on a ridge seven kilometres from the works, so any zone
        big enough to be the alert trip wire has a friendly recon team already
        standing inside it and the pair scrambles before the player leaves the
        ramp. Gating on the first casting hall instead is both bug-free and
        better: the field reacts to the attack, which is what a real one does.

        Sized by `_ALERT_SECTION`: a pair for one or two slots, a four-ship
        above that, and no further. The magazine sets the floor — four rails and
        two shots per kill against crews this good makes a pair the whole budget
        for a single jet — and doctrine sets the ceiling, because a field
        scrambles the section it has on alert rather than everything on the
        ramp. Neither number needs to chase `--players` upward, because these
        are not a tasked kill: the frag is the works, this is what the egress
        has to survive, and the briefing says so rather than leaving the player
        to guess whether a disengagement is an off-ramp or a broken trigger.
        """
        sizes = (_ALERT_SECTION[0] if self.players <= 2 else _ALERT_SECTION[1],)
        flights: list[FlyingGroup] = []
        for i, size in enumerate(sizes, start=1):
            name = "Rubin" if i == 1 else f"Rubin {i}"
            mig = m.flight_group_from_airport(
                russia,
                name,
                planes.MiG_29S,
                scene.mineralnye_vody,
                maintask=task.CAP,
                start_type=StartType.Cold,
                group_size=size,
            )
            mig.add_runway_waypoint(scene.mineralnye_vody)
            mig.points[0].tasks[0] = task.EngageTargets(120_000, [task.Targets.All.Air])
            hunt = mig.add_waypoint(scene.works, 7_000, 900)
            hunt.tasks.append(
                task.OrbitAction(7_000, 900, task.OrbitAction.OrbitPattern.RaceTrack)
            )
            mig.add_waypoint(scene.ip, 7_000, 900)
            mig.add_runway_waypoint(scene.mineralnye_vody)
            mig.land_at(scene.mineralnye_vody)
            set_skill(mig, Skill.Excellent)
            apply_ai_difficulty(mig, self.difficulty)
            flights.append(mig)
        return flights

    # -- blue side ----------------------------------------------------------

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[FlyingGroup, tuple[Point, Point]]:
        """E-3A Magic on a race-track over western Georgia, 251.000 AM.

        Hand-placed rather than taken from `place_awacs_track`, which anchors a
        track on the far side of the field from the threat axis — and the far
        side of Senaki from the Kuban is the Black Sea, 60 km further from a
        watershed the E-3A already cannot see over. North-east of the field puts
        the orbit behind the Georgian ridges but as close to them as the standoff
        allows, which is what the ESM half of his job needs: he is the only
        receiver in this package pointed at those radars, and every emissions
        call in the mission is conditional on him being alive, on station and in
        line of sight of the emitter.
        """
        p1 = offset(scene.senaki.position, east_m=20_000, north_m=25_000)
        p2 = offset(p1, east_m=60_000, north_m=0)
        track = race_track(p1, p2)
        magic = m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.senaki,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=9_000,
            speed=740,
            start_type=StartType.Warm,
            frequency=_FREQ_AWACS,
        )
        return magic, (p1, p2)

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco north of Kutaisi, TACAN 12X — the pre-crossing top-up.

        Two bags make the planned sortie comfortable on internal fuel, so this
        is honestly a margin rather than a necessity, and the briefing says so
        in those words. What it buys is the *unplanned* half: twenty-five
        minutes of low-level burn between the coast and the target, a climb-out
        on the far side that costs thirteen thousand feet in eighteen miles, and
        an alert section behind a jet that may have to run rather than route.
        Crossing with full internal fuel is what makes any of that survivable,
        and it costs four minutes before the enemy coast rather than forty after
        it.

        Stationed on the Georgian side and well short of the watershed on
        purpose: a tanker the player has to come back over a 3,000 m pass to
        reach is a tanker that does not exist.
        """
        p1 = offset(scene.kutaisi.position, east_m=-10_000, north_m=20_000)
        p2 = offset(p1, east_m=45_000, north_m=0)
        track = race_track(p1, p2)
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.kutaisi,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=6_500,
            speed=750,
            start_type=StartType.Warm,
            frequency=_FREQ_TANKER,
            tacanchannel=_TANKER_TACAN,
        )
        return p1, p2

    def _spawn_recon_team(
        self, m: Mission, usa: Country, scene: _Scene, *, shipment: VehicleGroup
    ) -> VehicleGroup:
        """Ferret: a three-vehicle recon team on the ridge over the Kuban road.

        `observation_post` rather than a concealment helper, and the difference
        is the whole feature: a DCS ground controller lases what its **own**
        sensor sees, so line of sight is a hard filter here rather than a
        preference. It is asked for at three points — the works, the loading
        yard, and a point most of the way up the road the shipment drives —
        which is a different request from asking at one, and it is what turns a
        post that can see one hollow into a post that watches about half of the
        twenty-one kilometres the column has to cover.

        That visible stretch **is** the window on the second objective, and the
        team is what makes four bombs enough for two jobs: a moving column on a
        road is a hard laser problem for a single jet with nobody to buddy-lase,
        and the honest answer is a controller on the ground rather than a bigger
        magazine.

        `FacCallsign.AXEMAN` is left alone deliberately — DCS answers a FAC by
        its index in the game's own callname table, and this team is called
        `Ferret` in the briefing, so `fac_attack_group` is given the callsign
        rather than the group name doing the work. Not `SetInvisibleCommand`
        either: the team is killable, and losing it costs the laser, which is a
        consequence the player can see coming and defend against.
        """
        ferret = m.vehicle_group_platoon(
            usa,
            "Ferret",
            cast(
                list[type[VehicleType]],
                [
                    vehicles.Unarmed.Hummer,
                    vehicles.Unarmed.Hummer,
                    vehicles.Infantry.Soldier_M4,
                ],
            ),
            position=scene.recon_post,
            heading=int(scene.recon_post.heading_between_point(scene.works)),
        )
        set_skill(ferret, Skill.High)
        fac_attack_group(
            ferret,
            shipment,
            designation=task.Designation.Laser,
            frequency=_FREQ_RECON,
            modulation=task.Modulation.AM,
            callsign=FacCallsign.FERRET,
        )
        return ferret

    def _spawn_player(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[list[FlyingGroup], list[Point]]:
        """Colt F-16C-50 out of Senaki, hot ramp: low up the valleys, high home.

        The ED-shipped strike fit for this tasking on slot 1, station for
                station: AMRAAM on the wingtips (1/9, which is where an F-16C carries
                them — the AIM-9X goes outboard to 2/8 when 3/7 are the weapon
                stations), four GBU-12 on two TERs, two 370 gal bags, the ALQ-184 on the
                centreline that every two-tank ED payload carries, and the pod on 11.
                Slot 2 flies the air-to-air fit (`_FITS`), which is what keeps the
                magazine at four bombs however many people show up: the decision this
                mission is built on is spending four across two halls and a shipment.

                **No HARM and no HTS**, and that is the mission rather than an omission.
                There is no anti-radiation answer to the layout here — the belts are
                avoided or they are not dealt with — so every station that could have
                carried a Weasel load carries a bomb instead, and the terrain does the
                job the pod would have done.

                The gross weight is about three quarters of max, which is where a jet
                with this radius should sit: an F-16 launched at eighty-plus per cent
                rotates steeply and climbs in afterburner because the weight demands it,
                and no waypoint speed fixes that.
        """
        sections = player_flight(
            m,
            country=usa,
            name="Colt",
            aircraft_type=planes.F_16C_50,
            airport=scene.senaki,
            maintask=task.PinpointStrike,
            start_type=StartType.Warm,
            slots=self.players,
            loadouts=_FITS,
        )
        # Four GBU-12s and `Ferret`'s spot on one code. The Viper carries no
        # laser-code field, so this writes nothing into the .miz and instead
        # refuses a code the jet would not come up on — which is what the
        # briefed 1511 was, on a mission whose only guidance is that laser.
        for section in sections:
            laser.set_code(section, _LASER_CODE)
        overlay = scene.overlay.overlay
        ingress = waypoints.agl_profile(
            scene.ingress, overlay, clearance_m=_LEG_CLEARANCE_M
        )
        egress = waypoints.agl_profile(
            scene.egress, overlay, clearance_m=_LEG_CLEARANCE_M
        )
        for player in sections:
            self._route_colt(player, scene, ingress, egress)
        route = [
            *(leg.position for leg, _ in ingress),
            scene.works,
            *(leg.position for leg, _ in egress),
        ]
        return sections, route

    def _route_colt(
        self,
        player: FlyingGroup,
        scene: _Scene,
        ingress: Sequence[tuple[Leg, float]],
        egress: Sequence[tuple[Leg, float]],
    ) -> None:
        """Senaki → the Kodori → Klukhori → TARGET → over the range → Senaki.

        The altitudes are worked out once in `_spawn_player` and handed in: they
        are reads against the elevation raster, and two sections deriving them
        separately could fly two different profiles under one briefing.

        `TARGET` is a ground waypoint, so it carries the works' own elevation
        rather than the altitude the flight happens to cross it at — that number
        is the steerpoint elevation the CCRP solution, the HUD and the DED all
        read. The pop is flown off the `IP` leg above it, which is a point in the
        air and stays there.
        """
        player.add_runway_waypoint(scene.senaki)
        for leg, altitude in ingress:
            player.add_waypoint(
                leg.position,
                altitude=altitude,
                speed=leg.speed_kph,
                name=leg.name,
            )
        waypoints.add_ground_waypoint(
            player,
            scene.works,
            overlay=scene.overlay.overlay,
            speed=_ATTACK_SPEED_KPH,
            name="TARGET",
        )
        for leg, altitude in egress:
            player.add_waypoint(
                leg.position,
                altitude=altitude,
                speed=leg.speed_kph,
                name=leg.name,
            )
        player.add_runway_waypoint(scene.senaki)
        player.land_at(scene.senaki)

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        *,
        route: list[Point],
        stations: Sequence[Point],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Senaki under Hawk, Mineralnye Vody under S-125.

        Kutaisi is 37 km from Senaki and therefore inside the same 45 km
        envelope, so the divert this briefing names is a defended field rather
        than a runway with a promise attached — which is the distinction
        `coastal_cover` had to make about Kutaisi in the other direction.

        The red half goes on **Mineralnye Vody**, not on anything nearer the
        objective, because the field a sanctuary is for is the one the fighters
        recover to. It is 104 km from the works against an 18 km system, so it
        costs the strike nothing and costs a chase everything — which is what
        turns "follow the MiG home" from a free decision into a priced one, with
        no scripting at all: the DCS AI already goes home on bingo.

        `keep_clear` on our side is the whole Russian order of battle, which at
        180 km is never in question — it is passed anyway so that a future
        change to the AO fails loudly instead of quietly switching the mission
        off. On theirs it is the works, every flown point of the route, and both
        support tracks.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.senaki,
            callsign=_SANCTUARY,
            facing=scene.works,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[
                scene.works,
                scene.sa11_pos,
                scene.sa3_pos,
                scene.unfixed_pos,
                *scene.ewr_positions,
            ],
            alternates=[scene.kutaisi],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        minvody_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.mineralnye_vody,
            callsign="Mineralnye Vody field",
            facing=scene.works,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Min Vody",
            keep_clear=[scene.works, *route, *stations],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, minvody_ad

    # -- the integrated air-defence net -------------------------------------

    def _add_iads(
        self,
        m: Mission,
        *,
        magic: FlyingGroup,
        sa11: VehicleGroup,
        sa3: VehicleGroup,
        shorad: VehicleGroup,
        ewrs: list[VehicleGroup],
        unfixed: VehicleGroup,
        minvody_ad: sanc.Sanctuary,
    ) -> None:
        """Wire every radar-guided site into one net — for the cueing, not the duel.

        This is the first mission here to use `core/iads.py` without a HARM in
        the package, and that is worth being explicit about: half the module is
        inert here. Nobody is going to shoot at these radars, so the reaction
        dials — recognition delay, shutdown window, shoot-and-scoot — will never
        be rolled against anything. What the net is for is the **other** half,
        emission discipline, and without it this mission does not work at all.

        Left to DCS, every battery radiates from mission start. The player's RWR
        would be full before the coast, the whole point of two hundred
        kilometres of valley would be invisible, and a low route that buys
        nothing is just a slow one. With the net, only the two early-warning
        radars are on the air; each battery sits cold until the chain hands it a
        track inside its own reach. So the ingress is quiet because it is
        *working*, the first strobe is the Buk at the IP, and the site defence
        comes up as the player commits — which is the shape the briefing
        promises.

        `go_live_percent` is over 100 everywhere for the usual reason: a DCS site
        needs something like half a minute from cold to a shot, so a battery that
        waits until the target is inside its launch envelope comes up and
        watches. The Buk is the exception in the other direction — 170 % of a
        50 km table range is most of the Kuban bend, and it is meant to be up and
        looking before anything reaches the valley mouth.

        The consequence the briefing depends on: killing an EWR does **not**
        switch the belts off. A battery cut off from the net goes autonomous,
        which with `"ai"` means it searches on its own and radiates continuously
        from then on. That is a trade rather than a win, and on this sortie it is
        a bad one — there is nothing aboard that profits from an emitter being
        findable.

        `Magic` is the only listener, so every emissions call in the mission is
        his ESM watch: a battery behind a ridge from his track, or anything at
        all once he is gone, goes quiet without a word.
        """
        sites = [
            Site(
                sa11,
                "SA-11",
                go_live_percent=170,
                probability=0.9,
                delay_s=(14.0, 40.0),
                shutdown_s=(280.0, 400.0),
                net_relay=0.7,
            ),
            Site(
                sa3,
                "SA-3",
                go_live_percent=140,
                probability=0.75,
                delay_s=(20.0, 55.0),
                shutdown_s=(240.0, 380.0),
                net_relay=0.6,
            ),
            Site(
                shorad,
                "SA-19",
                go_live_percent=130,
                probability=0.8,
                delay_s=(12.0, 30.0),
                shutdown_s=(120.0, 220.0),
                net_relay=0.4,
            ),
            # The unlocated Gainful is the one site that must not announce itself
            # early: it is only honest as a surprise if it stays off the air
            # until somebody is well north of the bend, which is exactly what a
            # tight go-live and no relay buy.
            Site(
                unfixed,
                "SA-6",
                go_live_percent=115,
                probability=0.85,
                delay_s=(16.0, 45.0),
                shutdown_s=(240.0, 360.0),
                net_relay=0.2,
            ),
            Site(
                minvody_ad.groups[0],
                minvody_ad.threat_label,
                go_live_percent=140,
                probability=0.6,
                delay_s=(25.0, 70.0),
                shutdown_s=(200.0, 320.0),
                net_relay=0.3,
            ),
            *(
                Site(ewr, f"EWR {i}", role="ewr", act_as_ew=True)
                for i, ewr in enumerate(ewrs, start=1)
            ),
        ]
        arm_iads(
            m,
            sites,
            listeners=[Listener(magic, "Magic")],
            voice=self._voice,
            name="Kuban IADS",
            down_call="Magic: {label} has ceased emissions, site is dark.",
            up_call="Magic: {label} is radiating again, expect it hot.",
        )

    # -- triggers -----------------------------------------------------------

    def _add_intro_voice(self, m: Mission) -> None:
        """Magic's mission-start picture: the frag, the route and what is not on it.

        Said once, at load, in the order the sortie hands it over. The two halls
        are the frag and everything else at the works is scenery, so a player who
        does not hear that plans a four-bomb attack on a tank farm; and the
        shipment is the thing that will change under them in flight, so the fact
        that it exists has to be in the picture before it moves.
        """
        mission_triggers.intro(
            m,
            comment="Magic mission-start picture",
            voice=self._voice,
            text=(
                "Colt, Magic on station. Target is the motor works on the Kuban, "
                "and it is the two casting halls, nothing else on that site. "
                "Take Texaco before the coast, then fly the valleys — nothing "
                "up there can see you until the Teberda opens. Ferret has been "
                "on that ridge six days and will talk you on."
            ),
        )

    def _add_support_checkins(self, m: Mission, home: sanc.Sanctuary) -> None:
        """Texaco, PALISADE and Ferret, on the clock across the climb-out.

        The sanctuary check-in is not decoration: a cyan ring on the F10 map
        reads as decoration, and nobody opens the map again after the coast.
        Ferret's is a scheduled report rather than a reaction — he has been in
        place for six days, so a check-in on the hour is what he would really
        make, and it is what gives the coordinate readout below something to
        follow rather than precede.
        """
        mission_triggers.checkin(
            m,
            at_seconds=_TANKER_CHECKIN_S,
            comment="Texaco check-in",
            voice=self._voice,
            text=(
                f"Texaco on station, {_FREQ_TANKER} point zero, TACAN "
                f"{_TANKER_TACAN[:-1]} X-ray, north of Kutaisi. Colt, take your "
                "gas before you turn north-west. You will not see me again."
            ),
        )
        sanc.announce(m, home, at_seconds=_SANCTUARY_CHECKIN_S, voice=self._voice)
        mission_triggers.checkin(
            m,
            at_seconds=_RECON_CHECKIN_S,
            comment="Ferret check-in",
            voice=self._voice,
            text=(
                f"Ferret one-one on {_FREQ_RECON} point zero, eyes on the works. "
                "Both halls are running and there is a load on transporters in "
                f"the yard. Laser code {_LASER_CODE} when you want it."
            ),
        )

    def _add_recon_readout(self, m: Mission, *, shipment: VehicleGroup) -> None:
        """Let Ferret pass the shipment's position in the units the Viper takes.

        DCS reads a four-digit military grid to every airframe out of its own
        `NATO.lua`, and an F-16's DED cannot take one — so as it ships, the
        controller's talk-on is a kneeboard conversion before it is a steerpoint.
        The request on his menu answers in the asking cockpit's format, read off
        a **live vehicle**, which is the whole reason it is worth having here:
        the column is driving, so a position good at his check-in is stale by the
        time the halls are down.

        The volunteered readout is timed just after the check-in rather than at
        it — a controller reading out coordinates for something he has not yet
        said he can see is a controller nobody believes.
        """
        arm_jtac_coords(
            m,
            [
                CoordTarget(
                    shipment,
                    label="Ferret 1-1",
                    what="the motor shipment",
                    laser_code=_LASER_CODE,
                )
            ],
            menu_title="Ferret 1-1",
            push_at_s=_RECON_READOUT_S,
        )
        # The two facts about the controller that a derived card cannot carry:
        # the laser code (pydcs writes it nowhere) and where the readout lives
        # in the radio menu.
        kneeboard.remark(
            m,
            f"Ferret 1-1 lases on {_LASER_CODE}; your four GBU-12s are coded the same.",
        )
        kneeboard.remark(m, "Shipment coordinates: F10 -> Other -> Ferret 1-1.")

    def _arm_recon_laser(
        self, m: Mission, *, ferret: VehicleGroup, shipment: VehicleGroup
    ) -> None:
        """Keep Ferret's spot on the transporters without Colt calling him.

        `fac_attack_group` gives the talk-on, and as DCS ships it that same
        conversation is the only thing holding the laser: the spot lives inside
        a radio exchange the player has to be in range and in line of sight of.
        This mission is built so he is neither for almost all of it — two
        hundred kilometres of valley at 600 m with the massif between him and
        everything — and what he comes out of the Teberda into is a yard
        emptying onto a road. Checking in first would spend the pass.

        So the team works the way six days on a ridge implies: the spot is on
        the transporters whenever it can see them, and the talk-on confirms a
        laser that is already burning. `lead_correction` because the column is
        driving by then, and `core/laser.py`'s default reach because Ferret sits
        about seven kilometres from the yard — the trucks are inside it in the
        yard and they drive out of it up the Kuban, which is the same clock the
        briefing already gives the player.
        """
        laser.arm_autolase(
            m,
            [
                laser.LaserSpot(
                    ferret,
                    shipment,
                    code=_LASER_CODE,
                    label="Ferret 1-1",
                    lead_correction=True,
                )
            ],
        )

    def _add_valley_trigger(self, m: Mission, *, shipment: VehicleGroup) -> None:
        """The Teberda crossing: Ferret calls the yard emptying, and it does.

        This is the mission's second objective arriving, and it arrives *caused*
        rather than scheduled. The column has been sitting in that yard since
        mission start; what starts it is the field hearing a jet come down the
        Teberda, which is about ten minutes before the player is over the works.
        By then it is eight or ten kilometres up the road and the decision is
        real: two bombs are the halls, and whatever is left is the shipment or
        nothing.

        The zone is checked against Ferret's own position — a
        `PartOfCoalitionInZone` counts any blue unit, and a recon team standing
        inside it would empty the yard before the player had started engines.
        Ferret is 53 km from this one.
        """
        zone = m.triggers.add_triggerzone(
            position=self.at(*_TEBERDA_ZONE),
            radius=_TEBERDA_ZONE_R,
            hidden=True,
            name="Teberda ingress",
        )
        rule = triggers.TriggerOnce(comment="Shipment rolls")
        rule.add_condition(condition.PartOfCoalitionInZone("blue", zone.id))
        rule.add_action(action.ActivateGroup(shipment.id))
        call = (
            "Ferret one-one: they have heard you. The transporters are out of "
            "the yard and turning north up the Kuban road. Halls first, Colt — "
            "I will hold a spot on the column when you are ready."
        )
        rule.add_action(action.MessageToAll(m.string(call), seconds=20))
        self._voice.attach_to_all(m, rule, call)
        m.triggerrules.triggers.append(rule)

    def _add_north_warning_trigger(self, m: Mission) -> None:
        """Magic names the gap in the picture the moment somebody flies into it.

        The withheld Gainful is only fair with this call attached. Everything
        else about it is set up in the briefing — an emitter that came up ten
        days ago, never located, deliberately not drawn because a ring would be
        a guess — but a gap the player is never reminded of at the moment it
        starts to matter is indistinguishable from an ambush. This is the
        moment, and it is `Magic`'s ESM watch making it, which is the same
        collector every other emissions call in this mission comes from.

        The zone sits 13 km clear of Ferret's post, so no friendly ground unit
        is standing inside it at mission start.
        """
        zone = m.triggers.add_triggerzone(
            position=self.at(*_NORTH_ZONE),
            radius=_NORTH_ZONE_R,
            hidden=True,
            name="North of the bend",
        )
        mission_triggers.message_to_coalition(
            m,
            comment="Unfixed SA-6 warning",
            conditions=(condition.PartOfCoalitionInZone("blue", zone.id),),
            voice=self._voice,
            text=(
                "Magic: Colt, that is the Gainful we never fixed — strong "
                "emitter north of the bend and no fix on it. You are inside it. "
                "Come south now, whatever is on that road."
            ),
            seconds=20,
        )

    def _add_recon_loss_trigger(self, m: Mission, *, ferret: VehicleGroup) -> None:
        """Losing Ferret costs the laser, and the player is told so.

        The team is deliberately killable — it sits near a road inside an
        airfield's worth of air defence, and the SHORAD in the column can reach
        it. That is the bargain: it is what makes his contribution feel earned,
        and the mission has to say out loud when it is gone, or a laser that
        stops working reads as a bug rather than as a casualty.
        """
        mission_triggers.message_to_all(
            m,
            comment="Ferret lost",
            conditions=(condition.GroupDead(ferret.id),),
            voice=self._voice,
            text=(
                "Magic: we have lost Ferret one-one. No spot on that column, "
                "Colt — anything you put on it now is your own."
            ),
        )

    def _add_scramble_triggers(
        self, m: Mission, *, plant: _Plant, migs: list[FlyingGroup]
    ) -> None:
        """The alert section launches when the first casting hall goes up.

        Gated on the hall rather than on a zone, for the reason in
        `_spawn_red_alert_fighters`: a coalition-in-zone trip wire big enough to
        be an alert trigger already has Ferret standing inside it. Gating on the
        attack is also what a field really does — nobody scrambles for a radar
        contact in a valley they cannot see into, and everybody scrambles for a
        works on fire.
        """
        hall = plant.hall_a.units[0].id
        for mig in migs:
            scramble_on_trigger(
                m,
                mig,
                condition.UnitDead(hall),
                comment=f"scramble {mig.name}",
            )
        mission_triggers.message_to_coalition(
            m,
            comment="Alert pair scrambling",
            conditions=(condition.UnitDead(hall),),
            voice=self._voice,
            text=(
                "Magic: Colt, Mineralnye Vody is scrambling their alert section. "
                "They will be over the Kuban in ten. Finish what you are doing "
                "and climb out south-west, not back down the Teberda."
            ),
            seconds=20,
        )

    def _add_end_triggers(
        self,
        m: Mission,
        *,
        plant: _Plant,
        shipment: VehicleGroup,
        colt: Sequence[FlyingGroup],
    ) -> None:
        """Three outcomes, priced separately — the halls, the shipment, the jet.

        The frag is the pair of casting halls and nothing else at the works, so
        the success call asks for exactly those two and stays silent for a
        player who levels the tank farm. The shipment gets its own call because
        it is its own objective: it is worth more than the tank farm and it
        costs bombs the halls may still need, and merging the two into one score
        would hide the decision the mission is built around.

        "Colt is down" is every section down, ANDed. Above four coop slots the
        flight is more than one DCS group, and gating the failure call on the
        lead alone would sound the mission over with jets still in the valley.
        """
        halls = (plant.hall_a.units[0].id, plant.hall_b.units[0].id)
        mission_triggers.message_to_all(
            m,
            comment="Casting halls destroyed",
            conditions=tuple(condition.UnitDead(unit) for unit in halls),
            voice=self._voice,
            text=(
                "Magic: both casting halls are down. That works is finished, "
                "Colt — nothing comes out of that valley now. Climb out "
                "south-west and go home over the top."
            ),
            seconds=25,
        )
        mission_triggers.message_to_all(
            m,
            comment="Shipment destroyed",
            conditions=(condition.GroupLifeLess(shipment.id, 50),),
            voice=self._voice,
            text=(
                "Ferret one-one: the column is wrecked on the road. Whatever "
                "they cast this month is burning with it. Good work, Colt."
            ),
            seconds=20,
        )
        mission_triggers.message_to_all(
            m,
            comment="Colt lost with the halls standing",
            conditions=(
                *(condition.GroupDead(group.id) for group in colt),
                condition.UnitAlive(halls[0]),
            ),
            voice=self._voice,
            text=(
                "Magic: Colt is down and those halls are still standing. The "
                "Kuban keeps casting."
            ),
            seconds=25,
        )

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        By country rather than by hand, so the late-activated shipment and the
        works' own buildings cannot be forgotten — an unhidden column sitting on
        the planner map would give away the whole second half of the sortie
        before the player started engines.
        """
        conceal_country(russia)

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        route: list[Point],
        shipment: VehicleGroup,
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
        home: sanc.Sanctuary,
        minvody_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan, and be precise about exactly one enemy thing: the works.

        Every ring here is drawn several kilometres off truth, wider than the
        system reaches, dashed and labelled "(approx.)", which is what the ace
        reveal does and what the Intelligence section claims to have — a week of
        thin overhead and an ELINT cut.

        **The works are the deliberate exception, and they are marked with a
        `waypoint_label` rather than an `objective` ring for that reason.** A
        chemical plant is not intelligence: it has been on the 1:100,000 sheet
        for forty years, it does not move, and a team has been looking at it for
        six days. Drawing it as a twelve-kilometre dashed "vicinity" would model
        an ignorance nobody has, and it would then contradict the two channels
        that cannot be coarsened anyway — the `TARGET` steerpoint has to be *on*
        the works or the run-in is a search, and Ferret's readout answers off a
        live position. This is the same argument `PlanOverlay.frontline` makes
        about a front line, applied to the other kind of fixed geography.

        Draw order is a total order in the cartridge's navigation tab and this
        route uses twenty of its twenty-five slots, so the sequence below is a
        budget: the marshal leg first (a damaged jet's hold should not lose to
        an AWACS anchor), then the target, then the recon post, then the things
        that are pleasant to have.

        The shipment's escort SHORAD is a `mobile_threat` — icon and label, no
        ring, nothing returned. It rides on a column that will have driven ten
        kilometres by the time anyone reaches it, and a ring drawn at its start
        point reads as "clear" everywhere it no longer covers.

        Returns the emplaced systems as HSD threat points. The early-warning
        radars are not among them: a search radar has no envelope to fly around,
        and the fifteen pre-planned slots are better spent on things that shoot.
        """
        home.draw(plan)
        plan.waypoint_label(scene.works, "TARGET — Kuban Forge (works)")
        plan.waypoint_label(scene.recon_post, "Ferret 1-1 — recon post")
        plan.route(route, "Colt — valley ingress, high egress")
        briefed = [
            *dtc.briefed(
                plan.threat(
                    scene.sa11_pos,
                    radius=_SA11_RING_M,
                    label="SA-11",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_11,
                label="SA-11 Kuban",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.sa3_pos,
                    radius=_SA3_RING_M,
                    label="SA-3 (works)",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_3,
                label="SA-3 works",
            ),
            *dtc.briefed(
                plan.threat(
                    scene.shorad_pos,
                    radius=_SA19_RING_M,
                    label="SA-19 (works)",
                    icon=StandardIcon.AirDefense,
                ),
                dtc.SA_19,
                label="SA-19 works",
            ),
        ]
        for i, pos in enumerate(scene.ewr_positions, start=1):
            plan.threat(
                pos,
                radius=3_000.0,
                label=f"EWR {i}",
                icon=StandardIcon.SearchRadar,
            )
        plan.mobile_threat(
            scene.shipment_origin,
            "Shipment escort SHORAD",
            icon=StandardIcon.Mechanized,
        )
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco AAR")
        plan.threat_area(
            scene.mineralnye_vody.position, 45_000.0, "MiG-29S alert — vicinity"
        )
        briefed += minvody_ad.draw(plan)
        return briefed

    def _load_cartridge(
        self,
        m: Mission,
        scene: _Scene,
        points: list[dtc.ThreatPoint],
        *,
        plan: PlanOverlay,
    ) -> None:
        """Put the assessed envelopes on Colt's HSD, where the F10 map drew them.

        This mission carries no HARM, so the pre-planned threats are not a
        targeting aid — they are the only place the briefed rings exist once the
        player's head is in the pit, and on a route flown at 250 m in a valley
        that is where it stays. Same claim as the map, wrong by the same
        kilometres, which is the point.

        The same cartridge carries the rest of the plan: the flight's route and
        the plan's marks as steerpoints, its lines as GEO lines. Twenty of the
        twenty-five navigation slots are the route, and the route wins — a plan
        that overflows loses its own marks, never the pilot's navigation.
        """
        dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)
        dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Fly the Abkhaz coast, the Kodori and the Klukhori Pass low, "
            "destroy BOTH casting halls at the motor works on the Kuban north "
            "of Karachayevsk, and take the night's shipment with whatever is "
            "left. Egress is a climb south-west over the range. Do not go "
            "north of the Kuban bend."
        )
        m.set_description_redtask_text(
            "Hold the motor works on the Kuban. The Buk battery on the plain "
            "denies the airspace above the valley; the alert section at "
            "Mineralnye Vody launches if the works are hit."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(KubanForge)


if __name__ == "__main__":
    main()
