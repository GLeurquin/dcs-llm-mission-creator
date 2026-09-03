"""Caucasus 'Kodori Strike' — F-16C mixed package strike on a Russian FOB.

Player flies a USAF F-16C-50 out of Kutaisi as `Dodge`, lead element of a
strike package hitting a Russian forward operating base on the coastal plain
at the Kodori delta, east-southeast of Sukhumi-Babushara. `Weasel` (F-16C SEAD)
rolls back an SA-6 site on the rising ground inland, with LOS to the FOB.
`Eagle` (F-15C high cover)
holds an overlay-placed CAP station between Kutaisi and Gudauta, ready for
the Russian Su-27 CAP launched on intrusion. `Magic` AWACS and `Texaco`
tanker sit on overlay-placed race-tracks opposite the threat axis.

All ground placements (FOB road snap, SA-6 site inland, SA-13 point defence,
55G6 EWR) and the player's ingress corridor come from the `map_overlay`
tactical-scene helpers — not hand-tuned cardinal offsets.

Composition (difficulty: trained):
  - 2x Russian Su-27, Skill.High, R-27/R-77 class, launched on intrusion trigger.
  - Russian FOB: 2x T-72B, 4x BTR-80, 2x KAMAZ supply, 1x ZSU-23-4 Shilka,
    snapped onto the coast road at the Kodori delta.
  - SA-6 site: 1x Kub 1S91 radar + 2x Kub 2P25 launchers on prominent
    terrain with LOS to the FOB (Skill.High).
  - 2x SA-13 (Strela-10M3) dug in around the FOB, covering the approach
    the package flies in on.
  - 1x 55G6 EWR on prominent ground in the Russian rear, feeding GCI.
  - USA support: E-3A `Magic` + KC-135 `Texaco` on overlay-placed race-tracks
    behind Kutaisi. F-15C `Eagle` 2-ship on a CAP station forward toward Gudauta.
  - Weather: late-spring clear morning, light W wind, 22 C.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence, cast

from dcs import action, condition, planes, task, templates, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    laser,
    loadout,
    routing,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import (
    Assembled,
    MissionBuilder,
)
from dcs_mission_creator.core.mission_kit import (
    arm,
    offset,
    player_flight,
    race_track,
    set_skill,
)
from dcs_mission_creator.core.placement import (
    FOREST_BUFFER_M as _FOREST_BUFFER_M,
    NO_FOREST as _NO_FOREST,
    find_clear_spot,
    load_scene,
    snap_units_clear,
)
from dcs_mission_creator.core.recon import (
    Chrome,
    Frame,
    Mark,
    landmark_marks,
    publish as recon,
)
from dcs_mission_creator.core.routing import ThreatRing
from dcs_mission_creator.core.tasking import (
    apply_ai_difficulty,
    apply_threat_reaction,
)
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.placement import Placement
from dcs_mission_creator.map_overlay.scene import TacticalScene

#: Elevation floor (m) for any air-defence placement in this AO. The threat
#: layout sits on a coastal plain, where a "prominent" cell can be a beach —
#: see `_spawn_red_sa6`. Low enough that the 26-49 m ground the mission actually
#: uses still qualifies.
_DRY_LAND_M = 20.0


@dataclass
class _Scene:
    """Resolved airports + AO + overlay handle used by every spawn step."""

    kutaisi: Airport
    sukhumi: Airport
    gudauta: Airport
    senaki: Airport
    ao_center: Point
    overlay: TacticalScene


# Kutaisi's own air defence, and the reason the mission has any.
#
# The AO is 127 km out and the sortie is 75 minutes; Senaki sits 37 km from
# Kutaisi, so one Hawk battery covers both runways and the whole recovery
# quarter without reaching anything the mission needs alive. That pairing is
# what makes the divert field a real option rather than a second bare strip.
_SANCTUARY = "CASTLE"
_SANCTUARY_BATTERY = sanc.HAWK


#: How `Dodge` splits the frag across its slots (`core/loadout.py`).
#:
#: The frag is a forward operating base — nine vehicles scattered over a
#: clearing, two of them main battle tanks and one of them a Shilka — and the
#: two halves of that are not the same weapon. Slot 1 carries four CBU-105 on
#: The one code the flight's bombs and any spot are on. `laser.set_code` writes
#: nothing on an F-16C — the airframe carries no laser-code property, the pilot
#: dials it on the TGP — so this is a build-time assertion that the number the
#: briefing quotes is the number the jet comes up on. It is what caught
#: `kuban_forge` briefing 1511 at four separate places against a spot on 1688.
_LASER_CODE = laser.DEFAULT_CODE

#: BRU-57: wind-corrected submunitions, which is what kills a dispersed platoon
#: in one pass from above the IR launchers the briefing tells the flight to stay
#: over. Slot 2 carries four GBU-12 on TERs and finds the armour and the gun with
#: the pod, because a submunition pattern is the wrong answer to a T-72 in a
#: revetment and a 500 lb laser bomb is the right one.
#:
#: Those bombs ride a spot, so the flight states its code (`_LASER_CODE`) like
#: every other laser mission here — see `_spawn_player`.
#:
#: This flight used to fly a **pure air-to-air fit** against a win condition of
#: "the FOB is wrecked", which is a mission whose player could not complete it.
#: `Eagle` owns the Su-27 pair (the briefing says so), so the four missiles each
#: of these fits carries are for getting home rather than for the frag.
#:
#: Both are ED payloads station for station, off
#: `<DCS>/CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua`:
#: `AIM-120C*2, AIM-9X*2, CBU-105*4, FUEL*2, ECM, TGP` and
#: `AIM-120C*2, AIM-9X*2, GBU-12*4, FUEL*2, ECM, TGP`.
_FITS = (
    loadout.Loadout(
        role="CBU-105*4",
        carries=(
            "four CBU-105 wind-corrected SFW on BRU-57, LITENING pod, "
            "two AIM-120C, two AIM-9X, ALQ-184, two 370 gal"
        ),
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_9X_Sidewinder_IR_AAM"),
            (3, "BRU_57_with_2_x_CBU_105___10_x_SFW__CBU_with_WCMD"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "BRU_57_with_2_x_CBU_105___10_x_SFW__CBU_with_WCMD"),
            (8, "AIM_9X_Sidewinder_IR_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
        ),
    ),
    loadout.Loadout(
        role="GBU-12*4",
        carries=(
            "four GBU-12 on TERs, LITENING pod, two AIM-120C, two AIM-9X, "
            "ALQ-184, two 370 gal"
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
            (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
        ),
    ),
)


class KodoriStrike(MissionBuilder):
    name = "kodori_strike"
    title = "Kodori Strike"
    difficulty = Difficulty.TRAINED
    terrain = Caucasus

    #: The two coalition task panels. Plain strings: nothing here needs
    #: to compute one, and `blue_task_text` / `red_task_text` are there
    #: for the mission that does.
    blue_task = (
        "Lead the strike on the Russian FOB at the Kodori delta. Weasel "
        "rolls back the SA-6 site; Eagle holds a CAP station between "
        "Kutaisi and Gudauta and handles the Su-27 intercept. RTB Kutaisi."
    )

    red_task = (
        "Hold the FOB at the Kodori delta. SA-6 / SA-13 cover the target "
        "box; Su-27s out of Gudauta launch against any USAF package "
        "crossing the Inguri."
    )

    #: 10:00 map-local on 20 May 2026 — the wall clock DCS shows in-game.
    start_time = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)

    #: Late-spring clear morning, light W wind, 22 C, 80 km visibility.
    weather = Weather(
        name="Late spring clear",
        season_temperature=22.0,
        clouds_base=3000,
        clouds_thickness=400,
        clouds_density=2,
        visibility_distance=80000,
        wind_at_ground=Wind(270, 3),
        wind_at_2000=Wind(270, 6),
        wind_at_8000=Wind(260, 11),
    )

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""KODORI STRIKE — Caucasus, 20 May 2026, 10:00 local
========================================================
SITUATION
  Satellite imagery over three passes this week has
  watched a Russian forward operating base grow on the
  coast road at the Kodori delta, ESE of
  Sukhumi-Babushara: armour, supply trucks, dug-in guns.
  That road is the only supply artery into Abkhazia,
  which is why the base is on it. Yesterday's pass
  caught freshly graded revetments on the rising ground
  inland, and an ELINT cut the same night put an SA-6
  fire-control radar in that area — assess the site is
  now live. SHORAD is reported dug in around the base
  itself. Early-warning radar in the Russian rear hands
  the picture to the Su-27s at Gudauta, who launch
  against anything crossing the Inguri.

MISSION (Dodge — F-16C-50, Kutaisi)
  Lead the strike on the FOB. Weasel rolls back the SA-6
  ahead of you. Eagle holds a CAP station between Kutaisi
  and Gudauta and handles the Su-27 intercept. Push along
  the terrain-masked ingress corridor, tank pre-strike if
  needed, work the target box, RTB Kutaisi.

LOADOUT (the flight splits the target)
{self.loadout_brief("Dodge", _FITS)}
  Slot 1's submunitions are for the platoon in the open;
  slot 2's laser bombs are for the tanks and the Shilka.
  There is no controller on this target: slot 2 lases its
  own, pod and bombs both on {_LASER_CODE}, which is where
  they come up. Nothing to arrange.

PACKAGE
  Dodge        : F-16C-50 pair, Kutaisi, hot ramp, strike.
                 Loadout above.
  Weasel 1-2   : F-16C-50 SEAD, Kutaisi, hunting SA-6.
  Eagle 1-2    : F-15C high cover, overlay CAP station.
  Magic        : E-3A AWACS, 251.000 AM, overlay track.
  Texaco       : KC-135, 252.000 AM, TACAN 10Y, overlay track.

INTELLIGENCE
  Air : Gudauta keeps a Su-27 pair ready, current
        missiles, experienced crews. Early-warning radar
        in the rear will see you and vector them.
  SAM : ELINT places an SA-6 fire-control radar on the
        rising ground about 10 km inland of the base,
        looking straight down onto it — that is Weasel's
        problem. Imagery also shows tracked IR launchers
        dug in around the base. Stay above 4500 m AGL in
        the target box until Weasel calls the SA-6 cold.
  AAA : Guns inside the FOB perimeter, seen on every
        imagery pass this week.
  Base: Gudauta is defended in its own right — an S-125
        battery on the field and guns in the overhead.
        Do not follow the Su-27s home.

ROE / FRAGS
  - Cleared to engage Russian aircraft entering the AO.
  - Hold ordnance until Weasel reports the SA-6 down or
    the FOB is the only viable target.
  - Not cleared to pursue over Gudauta.
  - Bingo fuel: 3000 lb. RTB Kutaisi (divert: Senaki).

FALL-BACK ({_SANCTUARY})
  Kutaisi and Senaki both sit under a
  {_SANCTUARY_BATTERY.name} battery — {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km,
  cyan ring on the map, guns in the overhead of Kutaisi.
  If you are hit, out of ordnance or out of fuel, that
  ring is the answer: get inside it and the fight is
  over. {_SANCTUARY} MARSHAL is a hold abeam Kutaisi,
  on the map and in the DED. Either runway works.

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  AO center         : coast road at the Kodori delta,
                      ~8 km ESE Sukhumi-Babushara.
  PUSH waypoint     : 18 km NW of Kutaisi.
  Ingress           : terrain-masked corridor, routed to keep
                      ridgelines between you and the reported
                      radars and the Gudauta approach.
  Cartridge         : the Gainful, the IR launchers and the
                      Gudauta belt are loaded as pre-planned
                      threats — select PRE on the HSD for the
                      rings. Estimates, same as the map, no
                      better than the cut.
  Imagery           : yesterday's satellite pass over the base
                      is on the briefing screen. Wide-area
                      mosaic, 50 m posts, so the bracket is the
                      target area and not a picture of what is
                      in it. The sea is the black along the
                      bottom; the coast road runs through the
                      bracket.

FREQUENCIES
  Magic AWACS   : 251.000 AM
  Texaco tanker : 252.000 AM, TACAN 10Y
  Kutaisi tower : per kneeboard
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Kodori Strike

**Theater:** Caucasus
**Date / time:** 20 May 2026, 10:00 local
**Player aircraft:** F-16C-50 (`Dodge`), Kutaisi, hot ramp
**Players:** {self.slot_summary("Dodge")}
**Difficulty:** trained — experienced Su-27 pair with GCI, one radar SAM plus
SHORAD dug in over the target, dedicated SEAD element, AWACS and tanker
**Expected sortie length:** ~75 minutes

## Situation

Satellite imagery over three passes this week has watched a Russian forward
operating base grow on the coastal plain at the Kodori delta, east-southeast of
Sukhumi-Babushara: armour, supply trucks, dug-in guns. It sits on the coast
road, which is the only supply artery into Abkhazia and the reason the base is
where it is. Yesterday's pass caught freshly graded revetments on the rising
ground inland, and an ELINT cut the same night put an SA-6 fire-control radar in
that area — the site is assessed live. SHORAD is reported dug in around the base
itself. Early-warning radar in the Russian rear hands the picture to the Su-27s
at Gudauta, who launch against any USAF package crossing the Inguri.

## Mission

Lead the strike on the FOB as `Dodge` flight. `Weasel` rolls back the SA-6
ahead of the strike, `Eagle` holds a CAP station between Kutaisi and Gudauta
and handles the Russian Su-27 intercept. Push along the terrain-masked
ingress corridor, tank pre-strike if needed, work the target box, RTB
Kutaisi.

The base is a scattered platoon with armour in it, and the flight carries two
answers to that rather than one compromise — see the loadout table below.

## Package

| Callsign  | Type     | Base    | Role                   |
|-----------|----------|---------|------------------------|
| Dodge     | F-16C-50 | Kutaisi | Player strike flight   |
| Weasel 1-2| F-16C-50 | Kutaisi | SEAD on SA-6           |
| Eagle 1-2 | F-15C    | Kutaisi | High cover CAP         |
| Magic     | E-3A     | Kutaisi | AWACS, 251.000 AM      |
| Texaco    | KC-135   | Kutaisi | Tanker, 252.000 AM 10Y |

### `Dodge` loadout

{self.loadout_table("Dodge", _FITS)}

Nine vehicles scattered over a clearing, two of them main battle tanks and one
a Shilka, is not one target — so the flight does not carry one weapon. Slot 1
has four CBU-105: wind-corrected submunitions, which is what kills a dispersed
platoon in a single pass from above the IR launchers you have been told to stay
over. Slot 2 has four GBU-12 on TERs and the pod to find the armour and the
gun with, because a submunition pattern is the wrong answer to a T-72 in a
revetment. Four air-to-air missiles a jet, and they are for getting home:
`Eagle` owns the Su-27 fight.

There is no ground controller on this target. Slot 2 self-designates: the pod
and the GBU-12s are both on **{_LASER_CODE}**, which is the code they come up
on, so there is nothing to set and nobody to raise before the first pass.

75-minute sortie is well past F-16C internal endurance, so the tanker is
mandatory — top off pre-strike on the way in, again post-strike if needed.

Terrain, not a template, decides where the FOB, the SAM site and your
corridor sit — they are re-derived every time the mission is generated, so
brief off this sheet rather than off a previous sortie.

## Intelligence

Everything below is imagery and ELINT from the last three days. The SAM and
SHORAD positions are assessed to within a few kilometres, and the map rings
are drawn as estimates for that reason. The base itself is the one thing that
has been watched long enough to be pinned, and the frame below is yesterday's
pass over it.

{self.recon_figure_md()}

- **Air:** Gudauta holds a Su-27 pair ready — current missiles, experienced
  crews — launched against any package that crosses the Inguri and vectored by
  early-warning radar in the Russian rear.
- **SAM:** the ELINT cut puts an SA-6 fire-control radar on the rising ground
  about 10 km inland of the base, with a clean look down onto it. Kill the radar
  and the launchers up there are blind. Imagery also shows tracked IR launchers
  dug in around the base — stay above 4500 m AGL in the target box until
  `Weasel` calls the SA-6 cold.
- **AAA:** guns inside the FOB perimeter, on every imagery pass this week.
- **EWR:** early-warning radar on commanding ground in the Russian rear,
  feeding the Gudauta GCI.
- **Gudauta field defence:** the same ELINT work that found the Gainful puts an
  S-125 battery on the airfield itself, with self-propelled guns in the
  overhead. It reaches 18 km and covers nothing you need — but it is the reason
  a Su-27 that turns for home stops being a target.

## ROE

- Cleared to engage any Russian aircraft entering the AO.
- Hold ordnance until `Weasel` reports SA-6 down or the FOB is the only
  viable target.
- **Not cleared to pursue over Gudauta.** A withdrawing Su-27 is not worth an
  S-125.
- Bingo fuel: 3000 lb. RTB Kutaisi (divert: Senaki-Kolkhi).

## Fall-back

Kutaisi is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10 map, with gun sections in
the overhead. Senaki-Kolkhi is 37 km away and sits
**inside the same envelope**, so the divert is a runway with cover over it
rather than an unmarked strip: take whichever is closer to where you break off.

That ring is where the sortie stops being dangerous. If you are hit, out of
ordnance or below bingo, run for it rather than turning back into a fight you
have already lost. `{_SANCTUARY} MARSHAL` is a hold abeam Kutaisi inside the
envelope, on the map and in the DED, for sorting out a damaged jet or waiting
on the pattern.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- AO center: ~8 km ESE of Sukhumi-Babushara, on the coast road across the
  Kodori delta. The supply road is the reason the base is there.
- PUSH waypoint: 18 km NW of Kutaisi.
- Ingress: terrain-masked corridor, routed to keep ridgelines between you and
  the reported radar positions and the Gudauta approach.
- TANK orbit: standoff track behind Kutaisi.
- Your data cartridge carries the Gainful, the IR launchers and the Gudauta
  field battery as pre-planned threats — select PRE on the HSD (they show on
  the HAD too) for the rings. They are the same estimates as the map, and no
  better than the cut they came from.

## Frequencies

- Magic AWACS: 251.000 AM
- Texaco tanker: 252.000 AM, TACAN 10Y
- Kutaisi tower: per kneeboard
- `{_SANCTUARY}` details and the divert are on the kneeboard comms card.

## Weather

Late-spring clear morning, light W wind, 22 °C. QNH 760 mmHg. Visibility
80 km. Thin scattered layer at 3000 m, 400 m thick.

## Win / loss conditions

- **Success:** the FOB is wrecked — armour, trucks and stores burning in the
  valley.
- **Failure:** `Weasel` is lost with the FOB still standing; without SEAD the
  strike is not worth flying.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- top-level orchestration --------------------------------------------

    def _assemble(self, m: Mission, plan: PlanOverlay) -> Assembled:
        """Assemble the mission by calling each step in package order."""
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        fob, sa6, sa6_pos, sa13_positions, ewr_pos = self._spawn_red_ground(
            m, russia, scene
        )
        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        weasel = self._spawn_sead(m, usa, scene, sa6_pos=sa6_pos, sa6=sa6)
        escort_track = self._spawn_escort(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            threats=(sa6_pos, ewr_pos, scene.gudauta.position, *sa13_positions),
        )

        home, gudauta_ad = self._spawn_sanctuaries(
            m,
            usa,
            russia,
            scene,
            red_sites=(sa6_pos, ewr_pos, *sa13_positions),
            stations=(*awacs_track, *tanker_track, *escort_track, *corridor),
        )

        self._add_end_triggers(m, fob=fob, sa6=sa6, weasel=weasel)
        sanc.announce(m, home, at_seconds=180, voice=self._voice)
        sanc.remark_all(m, home, gudauta_ad)
        # One overlay for every reveal channel: the F10 plan, the cockpit
        # cartridge and the recon still all have to make the same claim, and the
        # difficulty policy that decides how much they claim lives in here.
        briefed_threats = self._draw_plan(
            m,
            scene,
            plan=plan,
            fob=fob,
            sa6_pos=sa6_pos,
            sa13_positions=sa13_positions,
            ewr_pos=ewr_pos,
            corridor=corridor,
            escort_track=escort_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
            home=home,
            gudauta_ad=gudauta_ad,
        )
        self._render_recon(m, scene, plan=plan, fob=fob)
        return Assembled(scene.overlay.overlay, briefed_threats)

    # -- airports ------------------------------------------------------------

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Kutaisi for blue, Sukhumi/Gudauta for red, derive AO + overlay.

        The AO seed is the coastal plain at the Kodori delta, and it has to be:
        this mission wants a road-served FOB *and* prominent ground for the SA-6
        and the SHORAD, and in this overlay those two only coexist where the
        plain runs into the foothills.

        The seed used to be the upper Kodori valley, 22 km northeast — where the
        overlay has no road at all, because its OSM filter keeps only the major
        network and the only major road in Abkhazia is the coastal highway.
        `find_clear_spot` then escalated its radius (documented behaviour, four
        attempts out to `radius_m * 4`) and put the AO **19.3 km** from the seed,
        on this same coastal plain — while every briefing string still read
        "Kodori valley, ~22 km NE". Measured, the old seed had one non-forest
        cell within 10 km and zero road-served cells within 20 km, so the valley
        could not have held the FOB, let alone the four air-defence groups.
        Seeding where the mission can actually be built keeps the snap to 1.4 km
        and makes the briefing's stated position true.
        """
        t = self._terrain
        kutaisi = t.airports["Kutaisi"]
        sukhumi = t.airports["Sukhumi-Babushara"]
        gudauta = t.airports["Gudauta"]
        senaki = t.airports["Senaki-Kolkhi"]
        kutaisi.set_blue()
        senaki.set_blue()  # divert field
        sukhumi.set_red()
        gudauta.set_red()
        ao_seed = offset(sukhumi.position, east_m=9_000, north_m=-2_000)
        overlay = load_scene("caucasus")
        ao_center = find_clear_spot(
            overlay.overlay,
            ao_seed,
            t,
            radius_m=10_000.0,
            require=Placement(
                not_in=_NO_FOREST,
                forest_buffer_m=120.0,
                near_road_m=500.0,
            ),
        )
        return _Scene(kutaisi, sukhumi, gudauta, senaki, ao_center, overlay)

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """FOB platoon + SA-6 inland + SA-13 point defence + rear EWR."""
        fob = self._spawn_red_fob(m, russia, scene)
        sa6, sa6_pos = self._spawn_red_sa6(m, russia, scene)
        sa13_positions = self._spawn_red_shorad(m, russia, scene)
        ewr_pos = self._spawn_red_ewr(m, russia, scene)
        return fob, sa6, sa6_pos, sa13_positions, ewr_pos

    def _spawn_red_fob(self, m: Mission, russia: Country, scene: _Scene):
        """Russian forward operating base — placed in a road-accessible clearing.

        Prefers a flat, road-adjacent cell that is not under canopy (light or
        dense). `find_clear_spot` guarantees a non-forest result by relaxing
        tactical constraints before any forest concession. After the platoon
        is built, each unit is re-snapped off canopy in case Scattered
        formation spread (up to ~unit_count × 20 m) blew past the buffer.
        """
        fob_pos = find_clear_spot(
            scene.overlay.overlay,
            scene.ao_center,
            self._terrain,
            radius_m=8_000.0,
            require=Placement(
                max_slope_deg=15,
                not_in=_NO_FOREST,
                forest_buffer_m=_FOREST_BUFFER_M,
                near_road_m=300.0,
            ),
        )
        fob_types = [
            vehicles.Armor.T_72B,
            vehicles.Armor.T_72B,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Unarmed.KAMAZ_Truck,
            vehicles.Unarmed.KAMAZ_Truck,
            vehicles.AirDefence.ZSU_23_4_Shilka,
        ]
        fob = m.vehicle_group_platoon(
            russia,
            "FOB Kodori",
            cast(list[type[VehicleType]], fob_types),
            position=fob_pos,
            heading=180,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(fob, Skill.Average)
        snap_units_clear(scene.overlay.overlay, self._terrain, fob)
        return fob

    def _spawn_red_sa6(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 (Kub) site on clear rising ground with LOS to the FOB.

        Threats come from the south (Kutaisi). The SAM sits in a ±90° arc
        toward that axis, on prominent ground with LOS to the FOB, and
        explicitly out of light/dense forest (+ a forest edge buffer) so the
        radar isn't trying to track through canopy. Envelope and prominence
        are relaxed in stages — the coastal plain is flat and the rising ground
        is inland, so a tight envelope can come back empty. Final fallback
        delegates to `find_clear_spot` so we never settle on a deep-canopy cell.

        `min_elevation_m` is what keeps the site out of the surf, and it is not
        redundant with the prominence filter — it is the fix for the way that
        filter fails on a coast. `min_relative_height_m` is height above the
        *local mean* over a 2 km radius, and beside the sea that mean is dragged
        below zero (the water is −300 m a few kilometres out), so a beach cell at
        −4 m clears a 20 m prominence test comfortably. Before the floor went in,
        this search put the whole Kub site at −4 m and a launcher at −7 m: under
        water, and passing every other filter.
        """
        ao = scene.ao_center
        attempts = [
            (15_000.0, 20.0),
            (20_000.0, 10.0),
            (25_000.0, 0.0),
        ]
        sa6_pos: Point | None = None
        for envelope, prominence in attempts:
            require = Placement(
                max_slope_deg=10,
                not_in=_NO_FOREST,
                forest_buffer_m=_FOREST_BUFFER_M,
                not_in_built_up=True,
                min_elevation_m=_DRY_LAND_M,
                min_relative_height_m=prominence if prominence > 0 else None,
                relative_height_radius_m=2_000.0,
                in_sector_from=(ao, 90.0, 270.0),
                line_of_sight_to=(ao,),
                max_distance_to=((ao, envelope),),
                min_distance_to=((ao, 1_500.0),),
            )
            spots = scene.overlay.overlay.find_placement(
                ao, radius_m=envelope, require=require
            )
            if spots:
                sa6_pos = spots[0]
                break
        if sa6_pos is None:
            sa6_pos = find_clear_spot(
                scene.overlay.overlay,
                ao,
                self._terrain,
                radius_m=15_000.0,
                require=Placement(
                    max_slope_deg=15,
                    not_in=_NO_FOREST,
                    forest_buffer_m=_FOREST_BUFFER_M,
                    not_in_built_up=True,
                    min_elevation_m=_DRY_LAND_M,
                    min_distance_to=((ao, 1_500.0),),
                ),
            )
        sa6 = templates.VehicleTemplate.sa6_site(
            m, russia, sa6_pos, heading=180, prefix="Kodori ", skill=Skill.High
        )
        # Dispersed, then snapped — the wider the site, the more of it the
        # inland slopes and the treeline can swallow.
        ad.disperse_site(
            sa6,
            radius_m=300.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return sa6, sa6_pos

    def _spawn_red_shorad(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[Point]:
        """2x SA-13 dug in around the FOB, covering the approach to it.

        Each spot must be on prominent ground with LOS out along the approach
        the package flies in on, and explicitly out of forest (with edge buffer)
        — Strela-10M3 optics and seeker won't see through canopy. Two-pass
        search; relaxes prominence on the second pass. Per-shooter fallback
        delegates to `find_clear_spot` so a missed hilltop pick never drops the
        launcher into canopy.

        Sited as point defence **around what they defend**, biased to the side
        the package comes in on, which is the arrangement a coastal supply node
        actually gets. The old code searched around a hard-coded "6 km east,
        15 km south" anchor for hilltops with LOS to it; on a target this close
        to the shore that anchor is 100 m deep in the Black Sea, so both
        prominence passes came back empty and both launchers were placed by the
        canopy fallback — the "hilltops covering the approach" in the briefing
        were two cells picked by a last-resort spiral.

        So: search around the AO, require line of sight to the AO itself (a
        launcher has to see what it is shooting over), and bias the sector
        toward the ingress bearing, which is derived from the blue field rather
        than assumed southerly. High ground is a *preference* — the first pass
        asks for prominence and, on this coast, is expected to fail. The
        elevation floor is doing the same job as in `_spawn_red_sa6`.
        """
        t = self._terrain
        ao = scene.ao_center
        ingress_deg = ao.heading_between_point(scene.kutaisi.position)
        sector = ((ingress_deg - 90.0) % 360.0, (ingress_deg + 90.0) % 360.0)
        placed: list[Point] = []
        for _ in range(2):
            for prominence in (20.0, None):
                require = Placement(
                    max_slope_deg=20,
                    not_in=_NO_FOREST,
                    forest_buffer_m=_FOREST_BUFFER_M,
                    not_in_built_up=True,
                    min_elevation_m=_DRY_LAND_M,
                    min_relative_height_m=prominence,
                    relative_height_radius_m=2_000.0,
                    in_sector_from=(ao, *sector),
                    line_of_sight_to=(ao,),
                    min_distance_to=((ao, 1_000.0),)
                    + tuple((p, 2_000.0) for p in placed),
                )
                spots = scene.overlay.overlay.find_placement(
                    scene.ao_center, radius_m=6_000.0, require=require
                )
                if spots:
                    placed.append(spots[0])
                    break
            else:
                placed.append(
                    find_clear_spot(
                        scene.overlay.overlay,
                        scene.ao_center,
                        t,
                        radius_m=6_000.0,
                        require=Placement(
                            max_slope_deg=25,
                            not_in=_NO_FOREST,
                            forest_buffer_m=_FOREST_BUFFER_M,
                            not_in_built_up=True,
                            min_elevation_m=_DRY_LAND_M,
                            min_distance_to=tuple((p, 2_000.0) for p in placed),
                        ),
                    )
                )
        for i, pos in enumerate(placed):
            grp = m.vehicle_group(
                russia,
                f"SAM Kodori-13-{i + 1}",
                vehicles.AirDefence.Strela_10M3,
                position=pos,
                heading=180,
            )
            set_skill(grp, Skill.High)
            snap_units_clear(scene.overlay.overlay, t, grp)
        return placed

    def _spawn_red_ewr(self, m: Mission, russia: Country, scene: _Scene) -> Point:
        """55G6 EWR on commanding open ground in the Russian rear feeding GCI.

        Explicitly excludes light/dense forest with an edge buffer — the
        55G6's horizon depends on an open antenna footprint. Relaxes
        elevation/prominence in stages; final fallback delegates to
        `find_clear_spot` so the radar never ends up under canopy.
        """
        t = self._terrain
        rear_anchor = offset(scene.sukhumi.position, east_m=12_000, north_m=-6_000)
        ewr_pos: Point | None = None
        for min_elev, min_prom in ((150.0, 40.0), (50.0, 20.0), (0.0, 0.0)):
            require = Placement(
                max_slope_deg=20,
                not_in=_NO_FOREST,
                forest_buffer_m=_FOREST_BUFFER_M,
                not_in_built_up=True,
                min_elevation_m=min_elev if min_elev > 0 else None,
                min_relative_height_m=min_prom if min_prom > 0 else None,
                relative_height_radius_m=3_000.0,
            )
            spots = scene.overlay.overlay.find_placement(
                rear_anchor, radius_m=15_000.0, require=require
            )
            if spots:
                ewr_pos = spots[0]
                break
        if ewr_pos is None:
            ewr_pos = find_clear_spot(
                scene.overlay.overlay,
                rear_anchor,
                t,
                radius_m=15_000.0,
                require=Placement(
                    max_slope_deg=25,
                    not_in=_NO_FOREST,
                    forest_buffer_m=_FOREST_BUFFER_M,
                    not_in_built_up=True,
                ),
            )
        ewr = m.vehicle_group(
            russia,
            "EWR Kodori",
            vehicles.AirDefence.X_55G6_EWR,
            position=ewr_pos,
            heading=270,
        )
        set_skill(ewr, Skill.High)
        snap_units_clear(scene.overlay.overlay, t, ewr)
        return ewr_pos

    def _spawn_red_intercept(self, m: Mission, russia: Country, scene: _Scene):
        """2x Su-27 out of Gudauta, late-activated by blue intrusion zone."""
        intrusion_zone = m.triggers.add_triggerzone(
            position=scene.ao_center,
            radius=40_000,
            hidden=True,
            name="SU27 intrusion",
        )
        boris = m.intercept_flight(
            russia,
            "Boris",
            planes.Su_27,
            airport=scene.gudauta,
            zone=intrusion_zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=920,
            altitude=7500,
            max_engage_distance=90_000,
            group_size=2,
        )
        set_skill(boris, Skill.High)
        apply_ai_difficulty(boris, self.difficulty)
        announce = triggers.TriggerOnce(comment="Su-27 launch announcement")
        announce.add_condition(
            condition.PartOfCoalitionInZone("blue", intrusion_zone.id)
        )
        su27_call = (
            "Russian Sukhoi 27 airborne from Gudauta, vectoring on the strike package."
        )
        announce.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue, m.string(su27_call), seconds=15
            )
        )
        self._voice.attach_to_coalition(m, announce, su27_call, coalition="blue")
        m.triggerrules.triggers.append(announce)
        return boris

    # -- blue side ----------------------------------------------------------

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """E-3A Magic on an overlay-placed track behind Kutaisi, 251.000 AM."""
        p1, p2 = scene.overlay.place_awacs_track(
            home_base=scene.kutaisi.position,
            threat_axis=scene.gudauta.position,
            standoff_m=100_000.0,
            track_length_m=80_000.0,
        )
        track = race_track(p1, p2)
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.kutaisi,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=8500,
            speed=740,
            start_type=StartType.Warm,
            frequency=251,
        )
        return p1, p2

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco on an overlay-placed track behind Kutaisi, 252.000 AM, 10Y."""
        p1, p2 = scene.overlay.place_tanker_track(
            home_base=scene.kutaisi.position,
            threat_axis=scene.gudauta.position,
            standoff_m=70_000.0,
            track_length_m=60_000.0,
        )
        track = race_track(p1, p2)
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.kutaisi,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=4500,
            speed=700,
            start_type=StartType.Warm,
            frequency=252,
            tacanchannel="10Y",
        )
        return p1, p2

    def _spawn_sead(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        sa6_pos: Point,
        sa6: VehicleGroup,
    ):
        """F-16C Weasel 2-ship from Kutaisi, routed onto the placed SA-6 site.

        Built by hand rather than with `Mission.sead_flight`, whose attack
        waypoint pydcs hard-codes to `alt = 0` — a Weasel descending to sea
        level onto a live SA-6 is the opposite of how the shot is taken. The
        pair now runs in high, where a HARM has the energy to reach the site
        from outside its envelope.
        """
        weasel = m.flight_group_from_airport(
            country=usa,
            name="Weasel",
            aircraft_type=planes.F_16C_50,
            airport=scene.kutaisi,
            maintask=task.SEAD,
            start_type=StartType.Warm,
            group_size=2,
        )
        set_skill(weasel, Skill.High)
        arm(
            weasel,
            planes.F_16C_50,
            [
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
        apply_threat_reaction(weasel)
        self._route_sead(weasel, scene, sa6_pos=sa6_pos, sa6=sa6)
        return weasel

    def _route_sead(
        self,
        weasel,
        scene: _Scene,
        *,
        sa6_pos: Point,
        sa6: VehicleGroup,
    ) -> None:
        """Kutaisi → high IP outside the ring → HARM shot → egress → Kutaisi.

        The IP is the whole point of a SEAD route: it sits *outside* the SA-6's
        envelope, so the pair shoots from standoff instead of flying into the
        engagement zone the briefing tells the player to respect. The shot
        waypoint is placed on the ring edge rather than over the site.
        """
        ring = ThreatRing(sa6_pos, 10_000.0, "SA-6")
        weasel.add_runway_waypoint(scene.kutaisi)
        ip = routing.standoff_point(
            sa6_pos,
            toward=scene.kutaisi.position,
            threats=(ring,),
            min_distance_m=28_000.0,
            clearance_m=6_000.0,
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                scene.kutaisi.position, ip, (ring,), clearance_m=5_000.0
            )[1:],
            start=1,
        ):
            weasel.add_waypoint(pt, altitude=7_600, speed=800, name=f"INGRESS-{i}")
        shot = weasel.add_waypoint(ip, altitude=7_600, speed=800, name="HARM")
        shot.tasks.append(
            task.AttackGroup(
                sa6.id,
                weapon_type=task.WeaponType.Missiles,
                group_attack=True,
                expend=task.Expend.Two,
            )
        )
        weasel.add_runway_waypoint(scene.kutaisi)
        weasel.land_at(scene.kutaisi)

    def _spawn_escort(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C 2-ship Eagle on an overlay CAP station forward toward Gudauta."""
        threat_bearing = scene.kutaisi.position.heading_between_point(
            scene.gudauta.position
        )
        p1, p2 = scene.overlay.place_cap_station(
            defended_asset=scene.kutaisi.position,
            threat_bearing_deg=threat_bearing,
            forward_distance_m=50_000.0,
            track_length_m=40_000.0,
        )
        eagle = m.patrol_flight(
            usa,
            "Eagle",
            planes.F_15C,
            airport=scene.kutaisi,
            pos1=p1,
            pos2=p2,
            start_type=StartType.Warm,
            speed=800,
            altitude=7500,
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

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Dodge F-16C-50 from Kutaisi, hot ramp; overlay-routed terrain-masked corridor.

        Route: Kutaisi → PUSH → corridor (terrain-masked legs avoiding LOS to
        SA-6 / EWR / Gudauta / SA-13s) → TGT → EGRESS → Kutaisi.

        The flight splits its ordnance across the target rather than carrying one
        compromise (`_FITS`): area submunitions on slot 1 for the dispersed
        platoon, laser bombs on slot 2 for the armour and the Shilka. Before the
        split this flight launched with no air-to-ground stores at all under a
        win condition of "the FOB is wrecked".
        """
        sections = player_flight(
            m,
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.kutaisi,
            maintask=task.CAS,
            start_type=StartType.Warm,
            slots=self.players,
            loadouts=_FITS,
        )
        # Slot 2's GBU-12s ride a spot, so the code is stated rather than
        # assumed. Nothing lands in the .miz for a Viper; what this buys is that
        # the briefing and the jet cannot say different numbers.
        for section in sections:
            laser.set_code(section, _LASER_CODE)
        push = offset(scene.kutaisi.position, east_m=-15_000, north_m=12_000)
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=scene.ao_center,
            threats=threats,
            waypoints=3,
            leg_search_radius_m=6_000.0,
        )
        egress = offset(scene.ao_center, east_m=20_000, north_m=-15_000)
        for player in sections:
            self._route_dodge(player, scene, corridor, egress)
        return [*corridor, egress]

    def _route_dodge(
        self,
        player: FlyingGroup,
        scene: _Scene,
        corridor: Sequence[Point],
        egress: Point,
    ) -> None:
        """Kutaisi → PUSH → corridor → TGT → EGRESS → Kutaisi.

        One route, flown by every section: the corridor is a terrain-masking
        search against the overlay, so it is placed once and handed to each
        section rather than searched again per group.
        """
        player.add_runway_waypoint(scene.kutaisi)
        for i, pt in enumerate(corridor[:-1]):
            name = "PUSH" if i == 0 else f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=6500, speed=800, name=name)
        # The corridor ends on the FOB itself: a ground target, so its
        # steerpoint sits on the ground rather than at ingress altitude.
        waypoints.add_ground_waypoint(
            player, corridor[-1], overlay=scene.overlay.overlay, speed=800, name="TGT"
        )
        player.add_waypoint(egress, altitude=6500, speed=820, name="EGRESS")
        player.add_runway_waypoint(scene.kutaisi)
        player.land_at(scene.kutaisi)

    # -- somewhere to fall back to ------------------------------------------

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        *,
        red_sites: tuple[Point, ...],
        stations: tuple[Point, ...],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Kutaisi under Hawk, Gudauta under S-125.

        One Hawk at Kutaisi covers Senaki as well, 37 km away, so the divert the
        mission already claimed by calling `senaki.set_blue()` becomes a runway
        with something over it instead of an unmarked strip. The helper checks
        that rather than taking the briefing's word for it — pass Senaki as an
        `alternate` and it warns if the envelope does not actually reach.

        Gudauta gets the red battery rather than Sukhumi, and that is forced
        geometry rather than a preference: the FOB is 9 km from the Sukhumi
        threshold, so **no** system emplaced there clears the target, and
        `build_sanctuary` would refuse. Gudauta is 63 km out, it is where the
        Su-27s recover, and 18 km of S-125 is exactly the price a player should
        pay for chasing one onto its own runway.

        The two `keep_clear` lists are different lists. Out of *our* umbrella
        goes everything the enemy needs left standing — the SA-6, the SA-13s and
        the EWR; the tanker and escort tracks sit close to home on purpose and
        are supposed to be inside it. Out of *theirs* goes every friendly station
        and the whole ingress corridor.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.kutaisi,
            callsign=_SANCTUARY,
            facing=scene.ao_center,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[scene.ao_center, *red_sites],
            alternates=[scene.senaki],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        gudauta_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.gudauta,
            callsign="Gudauta field",
            facing=scene.ao_center,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Gudauta",
            keep_clear=[scene.ao_center, *stations],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, gudauta_ad

    # -- F10 map briefing ---------------------------------------------------

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        fob,
        sa6_pos: Point,
        sa13_positions: list[Point],
        ewr_pos: Point,
        corridor: list[Point],
        escort_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
        home: sanc.Sanctuary,
        gudauta_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan on the F10 map (trained: coarse, estimated threats).

        Returns the estimated air-defense rings as HSD threat points, so the
        Weasel's cockpit shows the same claim as the map. The FOB and the EWR
        stay map-only — neither is a missile envelope to stay outside of.
        """
        # The sanctuary goes on first so its marshal point is the first mark in
        # the cartridge's navigation tab: `core/dtc.py` fills those in draw order
        # after the flight's own route, and the one mark a pilot may need with a
        # broken jet should not lose a budget fight to a tanker track.
        home.draw(plan)
        plan.objective(scene.ao_center, "AO — FOB Kodori", radius=6_000.0)
        plan.route(corridor, "Dodge ingress")
        plan.orbit(*escort_track, "Eagle CAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco tanker")
        plan.threat(
            fob.units[0].position,
            radius=2_500.0,
            label="FOB",
            icon=StandardIcon.Mechanized,
        )
        hsd = dtc.briefed(
            plan.threat(
                sa6_pos, radius=10_000.0, label="SA-6", icon=StandardIcon.AirDefense
            ),
            dtc.SA_6,
            label="SA-6",
        )
        for pos in sa13_positions:
            hsd += dtc.briefed(
                plan.threat(
                    pos, radius=6_000.0, label="SA-13", icon=StandardIcon.AirDefense
                ),
                dtc.SA_13,
                label="SA-13",
            )
        plan.threat(ewr_pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)
        # Gudauta's own belt is a red ring like any other — estimated, and into
        # the cartridge beside the SA-6. It reaches 18 km and the AO is 63 km
        # away, so it costs nothing to fly the strike and everything to follow a
        # Su-27 home.
        return hsd + gudauta_ad.draw(plan)

    # -- the imagery the briefing cites --------------------------------------

    def _render_recon(
        self, m: Mission, scene: _Scene, *, plan: PlanOverlay, fob: VehicleGroup
    ) -> None:
        """Ship the wide-area cut of the base the Situation paragraph already claims.

        "Satellite imagery over three passes this week has watched a Russian
        forward operating base grow" was prose about a product nobody could look
        at. This is the product: the mosaic off yesterday's pass, the one
        collection in this briefing that is imagery rather than an ELINT cut.

        A target graphic, not a moving-target cut — a supply base sits still, so
        there is no track to draw and no route to run across the frame, and it is
        published north-up the way a target graphic is. The coastline does the
        work a grid would: the base is astride the coast road, and both the
        shoreline and the road are in the frame, so a reader can see what the
        bracket is on.

        Only the base is marked, and the two things that are deliberately not:
        the Gainful inland, because it is an ELINT cut and not imagery at all,
        and the IR launchers dug in around the base, which the earlier passes
        found but which are a fifth of a pixel here. Both are already an
        estimated ring on the map and a point in the cartridge; a bracket here
        would be a third, better-looking guess at the same site.

        The registration bias is 200 m rather than the 1.2 km default, for the
        reason `PlanOverlay.detections` documents: this frame is a coastline, a
        road net and villages, so the product is registered against them. At the
        default the bracket would sit a kilometre off the road the base is on, in
        a picture that draws the road.
        """
        aim = plan.detections([scene.ao_center], bias_m=200.0, jitter_m=60.0)
        if not aim:
            return

        frame = Frame(center=scene.ao_center)
        target = Mark(
            x=aim[0].x,
            y=aim[0].y,
            kind="group",
            radius_m=900.0,
            # No vehicle count: at 50 m posts this frame cannot resolve one truck
            # from the next, and the three passes that *did* count them are a
            # different collection. `idlib_gauntlet` labels a count because
            # counting movers is what an MTI product does.
            text="FOB  STORES / VEH PARK",
        )
        # Settlement names, so the frame can be *located* against the F10 map
        # rather than merely believed. Landmarks first, target last, so the
        # bracket wins any overlap the separation rules did not prevent.
        marks = [
            *landmark_marks(scene.overlay.overlay, frame, avoid=[target]),
            target,
        ]
        self._still = recon.sensor_still(
            m,
            frame,
            marks,
            Chrome(
                platform="SAR SATELLITE  X-BAND",
                mode="STRIPMAP  5 LOOK",
                # Yesterday's pass, which is what the briefing cites; a dawn
                # crossing, as a sun-synchronous orbit gives.
                taken_at="0612L  19 MAY 26",
                classification="SECRET // REL FVEY",
                footer="KODORI DELTA  COAST RD",
                caption=(
                    "Yesterday's pass over the Kodori delta. This is the wide-area "
                    "mosaic — 50 m posts — so the bracket is the target area and "
                    "not a picture of what is parked in it; the revetment call came "
                    "off a spot collect on the same pass. Black is water: the sea "
                    "across the bottom and down the left, the delta channel running "
                    "to it. The coast road is the thin dark line through the "
                    "bracket, and the named villages are there to tie the frame to "
                    "your map. Neither the launchers dug in around the base nor the "
                    "graded ground inland resolves at this spacing, which is why "
                    "the rings on your map are estimates."
                ),
            ),
            overlay=scene.overlay.overlay,
            slug=self.name,
            label="fob",
        )

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(self, m: Mission, *, fob, sa6, weasel) -> None:
        """Success when FOB dead; failure when Weasel dies while FOB lives."""
        mission_triggers.message_to_all(
            m,
            comment="Strike successful",
            conditions=(condition.GroupDead(fob.id),),
            voice=self._voice,
            text=(
                "Magic: the FOB is wrecked, armour and stores burning in the "
                "valley. Dodge, return to base, Kutaisi."
            ),
        )

        sead_done = triggers.TriggerOnce(comment="SEAD complete")
        sead_done.add_condition(condition.GroupDead(sa6.id))
        sead_call = "Weasel, magnum splash. SA-6 site is cold. Dodge, target box open."
        sead_done.add_action(action.MessageToAll(m.string(sead_call), seconds=15))
        self._voice.attach_to_all(m, sead_done, sead_call)
        m.triggerrules.triggers.append(sead_done)

        mission_triggers.message_to_all(
            m,
            comment="Strike failed",
            conditions=(
                condition.GroupDead(weasel.id),
                condition.GroupAlive(fob.id),
            ),
            voice=self._voice,
            text=(
                "Magic: Weasel is down and the FOB is still standing. Without SEAD "
                "that valley is closed to us. Dodge, return to base, Kutaisi."
            ),
        )


def main() -> None:
    run_cli(KodoriStrike)


if __name__ == "__main__":
    main()
