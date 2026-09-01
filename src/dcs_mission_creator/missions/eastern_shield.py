"""Syria 'Eastern Shield' — F-16C SEAD/escort with layered objectives.

Player flies a USAF F-16C-50 out of Incirlik (callsign `Springfield`, the
USAF SEAD callsign), suppresses a Russian SA-6 site defending a forward
depot at Kuweires, then escorts A-10C `Hawg` onto the depot, then handles
a 2-ship MiG-29S scramble out of Bassel Al-Assad and an armored reserve
push from the Aleppo road. KC-135 `Texaco` and E-3A `Magic` cover the
sortie end-to-end (F-16C internal fuel is ~35 min; 2h sortie mandates a
tanker).

Composition (difficulty: trained):
  - SA-6 site: 1x Kub 1S91 search radar + 3x Kub 2P25 TEL + 2x ZU-23
    AAA on a ridgeline north of Kuweires. Skill High.
  - Russian forward depot at Kuweires: 3x BTR-80, 3x T-72B, 2x GAZ-66
    trucks, 1x SA-13 SHORAD overwatch, 2x ZU-23 AAA. Skill Average.
  - 2x 55G6 EWR chain along the Syrian frontier feeding GCI.
  - 2x MiG-29S, Skill High, R-77/R-27 class, late-activated from Bassel
    Al-Assad after the depot is destroyed.
  - Russian armored reserve (2x T-72B + 4x BTR-80) on the Aleppo road,
    activates and pushes west once the depot dies.
  - USAF support: E-3A Magic AWACS over the Mediterranean, KC-135 Texaco
    tanker (TACAN 10X, 270.000 AM), F-15C Eagle 2-ship TARCAP,
    A-10C Hawg 2-ship strike package.
  - Weather: late-spring eastern Med haze, light NW wind, 22 C.
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
from dcs.point import PointAction
from dcs.terrain.syria.syria import Syria
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    routing,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    arm,
    offset,
    player_flight,
    race_track,
    set_skill,
)
from dcs_mission_creator.core.placement import load_scene, sam_site_on_ridge
from dcs_mission_creator.core.routing import ThreatRing
from dcs_mission_creator.core.tasking import (
    apply_ai_difficulty,
    apply_threat_reaction,
)
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene


@dataclass
class _Scene:
    """Resolved airports + AO geometry + map overlay used by every spawn step."""

    incirlik: Airport
    gaziantep: Airport
    bassel: Airport
    kuweires: Airport
    sa6_anchor: Point
    depot_anchor: Point
    reserve_origin: Point
    reserve_destination: Point
    overlay: TacticalScene


# Two covered fields, because this is the longest sortie in the project.
#
# The depot is 213 km from Incirlik. A jet that takes a hit over Kuweires is
# fifteen minutes from home on a good engine and may not have one, so `REDOUBT`
# at Incirlik is the recovery umbrella and `PICKET` at Gaziantep — 85 km from the
# target, 128 km nearer than home — is the forward field it can actually reach.
# Gaziantep gets NASAMS rather than a second Hawk for the reason `keep_clear`
# exists: 45 km from there would reach 40 km toward the AO across ground the
# mission needs to stay contested.
_SANCTUARY = "REDOUBT"
_SANCTUARY_BATTERY = sanc.HAWK
_FORWARD_SANCTUARY = "PICKET"
_FORWARD_BATTERY = sanc.NASAMS


class EasternShield(MissionBuilder):
    name = "eastern_shield"
    title = "Eastern Shield"
    difficulty = Difficulty.TRAINED

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Syria()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""EASTERN SHIELD — Syria, 21 May 2026, 09:00 local
==================================================
SITUATION
  Two weeks of imagery show Russian expeditionary forces
  standing up a forward supply depot on the Kuweires
  apron — revetted stores, trucks turning nightly.
  A Rivet Joint track four days ago picked up a Gainful
  fire-control radar on the ridge to the north; the site
  is relocatable, so treat the fix as approximate. The
  same collection has early-warning radars handing a
  picture to MiG-29S at Bassel Al-Assad.
  USAF has been tasked to break the depot and degrade
  Russian air defence coverage of the Aleppo corridor.

MISSION (Springfield — F-16C-50, Incirlik)
  Phase 1: Suppress the SA-6 site north of Kuweires.
  Phase 2: Escort Hawg 1-2 onto the depot once you can
           call the target box SAM-safe.
  Phase 3: Intercept any Russian fighters that scramble
           out of Bassel Al-Assad in response.
  Phase 4: Disrupt the armored reserve if it comes west
           on the Aleppo road to retake the ground.

PACKAGE
  Springfield 1 (you): F-16C-50, Incirlik, hot ramp,
        2x AGM-88C, HTS pod, AIM-120C / AIM-9X,
        two wing tanks.
  Hawg 1-2 : A-10C, Incirlik, strike on depot. Holds
        west of the AO until Springfield calls SAM safe.
  Eagle 1-2: F-15C TARCAP, Incirlik, race-track east
        of the AO covering the strike.
  Magic    : E-3A AWACS, 251.000 AM, Med track north.
  Texaco   : KC-135 tanker, 270.000 AM, TACAN 10X,
        track over the Med west of Latakia.

INTELLIGENCE
  Air : ELINT and the AWACS picture put a MiG-29S pair
        at readiness at Bassel Al-Assad, current
        missiles, experienced crews. They will launch
        when the depot is hit. Early-warning radars
        along the frontier will vector them.
  SAM : Rivet Joint fix, four days old — an SA-6 site on
        the ridge north of Kuweires, reach about 25 km.
        The search radar is what feeds its launchers.
        Depot imagery also shows a tracked IR launcher
        parked inside the wire.
  AAA : Light guns emplaced on the SA-6 ridge and around
        the depot.
  Land: Ground reporting says an armored reserve sits
        east of Kuweires on the Aleppo road, held back
        to retake the site if we hit it.

ROE / FRAGS
  - Cleared to engage the SA-6 site and depot ground units.
  - Cleared to engage Russian aircraft entering the AO.
  - Stay out of the SA-6 engagement zone until you have
    put its search radar down.
  - Tank from Texaco pre-push and post-AO if needed.
  - Bingo fuel: 3500 lb. RTB Incirlik (no divert).

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  SA-6 ridge        : ~8 km north of Kuweires.
  Depot             : Kuweires airfield apron + truck park.
  PUSH waypoint     : 30 km southeast of Incirlik.
  Your cartridge carries the Gainful and IR-launcher
  estimates as pre-planned threats; select PRE on the HSD
  to see the rings. They are as approximate as the fix
  they came from — do not fly the edge of one.

FREQUENCIES
  Magic AWACS    : 251.000 AM
  Texaco tanker  : 270.000 AM, TACAN 10X
  Incirlik tower : per kneeboard
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Eastern Shield

**Theater:** Syria
**Date / time:** 21 May 2026, 09:00 local
**Player aircraft:** F-16C-50 (`Springfield`), Incirlik, hot ramp
**Players:** {self.slot_summary("Springfield")}
**Difficulty:** trained — one radar SAM over the target with SHORAD and guns,
experienced MiG-29S pair with GCI, an armoured counter-push, full support
package (AWACS, tanker, TARCAP)
**Expected sortie length:** ~120 minutes (2 hours)

## Situation

Two weeks of imagery show Russian expeditionary forces standing up a forward
supply depot on the Kuweires apron in northern Syria — revetted stores, trucks
turning nightly. A Rivet Joint track four days ago picked up a Gainful
fire-control radar on the ridge to the north; the site is relocatable, so
treat that fix as approximate. The same collection has early-warning radars
handing a picture to MiG-29S at Bassel Al-Assad. USAF is tasked to break the
depot and degrade Russian air defence coverage of the Aleppo corridor before
the Russian reserve can close the road.

## Mission

`Springfield` flight runs a four-phase package out of Incirlik:

1. **Phase 1 — SEAD.** Suppress the SA-6 site north of Kuweires. Its search
   radar is the priority target — the launchers on that ridge are blind
   without it, and the rest of the package waits on your call.
2. **Phase 2 — Strike escort.** `Hawg 1-2` holds west of the AO until
   `Springfield` calls SAM safe, then runs in on the depot. Stay between
   `Hawg` and the threat axis.
3. **Phase 3 — DCA.** The Bassel Al-Assad pair will launch once the depot is
   hit. Sanitize the airspace before they get a shot on the strike package.
4. **Phase 4 — Convoy interdict.** The armored reserve is expected to come
   west on the Aleppo road to retake the site. `Hawg` re-tasks if it can;
   otherwise `Springfield` rolls in with AGM-65 or guns on the column.

## Package

| Callsign    | Type    | Base     | Role                              |
|-------------|---------|----------|-----------------------------------|
| Springfield | F-16C-50| Incirlik | Player SEAD lead / escort / DCA   |
| Hawg 1-2    | A-10C   | Incirlik | Strike on depot (holds for SEAD)  |
| Eagle 1-2   | F-15C   | Incirlik | TARCAP race-track east of the AO  |
| Magic       | E-3A    | Incirlik | AWACS, 251.000 AM, Med track north|
| Texaco      | KC-135  | Incirlik | Tanker, 270.000 AM, TACAN 10X     |

## Intelligence

The SAM fix is four days old and the site is relocatable, so the ring on your
map is an estimate — expect to find it with the HTS, not with the mark.

- **SAM (priority):** SA-6 'Gainful' on the ridge north of Kuweires, reach
  about 25 km, with light guns emplaced alongside. Its search radar feeds the
  launchers; put that down and the ridge stops shooting.
- **SHORAD:** depot imagery shows a tracked IR launcher parked inside the wire
  and guns around the perimeter.
- **Air:** a MiG-29S pair at readiness at Bassel Al-Assad — current missiles,
  experienced crews — expected to launch once the depot is hit, vectored by
  early-warning radar along the frontier.
- **Land reserve:** ground reporting places an armoured reserve east of
  Kuweires on the Aleppo road, held back to retake the site if we strike it.
- **Bassel Al-Assad field defence:** the same ELINT work that put the MiGs at
  readiness puts an S-125 battery on their airfield, with self-propelled guns in
  the overhead. It is 172 km from the depot and reaches 18 km, so it touches no
  part of the strike — it is the reason a MiG that turns for home stops being a
  target.

## ROE

- Cleared to engage the SA-6 site, depot ground units, and any Russian
  aircraft entering the AO.
- Stay out of the SA-6 engagement zone until its search radar is down.
- Tank from `Texaco` pre-push and post-AO; F-16C internal fuel does not
  cover a 2-hour sortie without at least one tanker pass.
- **Not cleared to pursue over Bassel Al-Assad.** A withdrawing MiG is not
  worth an S-125, and it is 172 km the wrong way.
- Bingo fuel: 3500 lb. RTB Incirlik, or Gaziantep if the jet will not make it —
  see below.

## Fall-back

This is the longest egress in the set: the depot is **213 km** from Incirlik,
which on a good engine is fifteen minutes and on a damaged one is not a number
worth relying on. So there are two covered fields rather than one.

- `{_SANCTUARY}` — a {_SANCTUARY_BATTERY.name} battery at Incirlik reaching {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km,
  drawn as the larger cyan ring, with gun sections in the overhead. Normal recovery. `{_SANCTUARY} MARSHAL` is a hold abeam the field
  inside the envelope, on the map and in the DED.
- `{_FORWARD_SANCTUARY}` — a {_FORWARD_BATTERY.name} battery at Gaziantep, the smaller ring. Its
  {_FORWARD_BATTERY.radius_m / 1000:.0f} km is a bubble over the field and the pattern and nothing further: it is not cover for a fight, it is cover for an approach. What it
  buys is distance — Gaziantep is 85 km from the target against Incirlik's 213.
  There is no marshal point there because nobody diverts in order to hold; the
  field itself is a steerpoint in your cartridge and a label on the map.

If the jet is hurt, turn for the nearer ring and say so on the way.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- SA-6 ridge: ~8 km north of Kuweires.
- Depot: Kuweires airfield apron and truck park.
- PUSH waypoint: 30 km southeast of Incirlik.
- Your data cartridge carries the Gainful and IR-launcher estimates as
  pre-planned threats — select PRE on the HSD (and they show on the HAD) to
  see the rings. They are the same estimates as the map, and no more precise
  than the fix behind them.

## Frequencies

- Magic AWACS: 251.000 AM
- Texaco tanker: 270.000 AM, TACAN 10X
- Incirlik tower: per kneeboard
- `{_SANCTUARY}` and `{_FORWARD_SANCTUARY}` details are on the kneeboard comms card.

## Weather

Late-spring eastern Mediterranean haze, light NW wind, 22 °C. QNH 760 mmHg.
Visibility 40 km (haze layer). Scattered layer at 2800 m, 400 m thick.

## Win / loss conditions

- **Primary success:** the SA-6 site is off the air for good and the depot is
  burning.
- **Full success:** the above, plus the fighter scramble defeated and the
  armoured reserve stopped short of Kuweires.
- **Failure:** `Hawg` is lost with the depot still intact, or the reserve
  reaches Kuweires and digs in.

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
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        sa6, ewr_positions = self._spawn_red_air_defence(m, russia, scene)
        depot, shorad_pos = self._spawn_red_depot(m, russia, scene)
        reserve = self._spawn_red_reserve(m, russia, scene)
        migs = self._spawn_red_intercept(m, russia, scene)

        threats = self._threat_rings(sa6_pos=scene.sa6_anchor, shorad_pos=shorad_pos)
        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        tarcap_track = self._spawn_tarcap(m, usa, scene)
        hog = self._spawn_strike(m, usa, scene, depot=depot, threats=threats)
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            threats=(scene.sa6_anchor, shorad_pos, *ewr_positions),
        )

        home, forward, bassel_ad = self._spawn_sanctuaries(
            m,
            usa,
            russia,
            scene,
            red_sites=(scene.sa6_anchor, shorad_pos, *ewr_positions),
            stations=(*awacs_track, *tanker_track, *tarcap_track, *corridor),
        )
        sanc.remark_all(m, home, forward, bassel_ad)

        self._conceal_red(russia)
        plan = PlanOverlay(m, self.difficulty)
        briefed_threats = self._draw_plan(
            m,
            scene,
            plan=plan,
            sa6_pos=scene.sa6_anchor,
            shorad_pos=shorad_pos,
            ewr_positions=ewr_positions,
            reserve_origin=scene.reserve_origin,
            corridor=corridor,
            tarcap_track=tarcap_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
            home=home,
            forward=forward,
            bassel_ad=bassel_ad,
        )
        self._load_cartridge(m, scene, briefed_threats, plan=plan)
        self._add_intro_voice(m)
        self._add_support_checkins(m, home)
        self._add_layered_triggers(
            m,
            sa6=sa6,
            depot=depot,
            hog=hog,
            migs=migs,
            reserve=reserve,
        )
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """09:00 map-local on 21 May 2026 — the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 5, 21, 9, 0, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Late-spring east-Med haze, light NW wind, 22 C, 40 km visibility."""
        Weather(
            name="Spring haze",
            season_temperature=22.0,
            clouds_base=2800,
            clouds_thickness=400,
            clouds_density=3,
            visibility_distance=40000,
            wind_at_ground=Wind(310, 3),
            wind_at_2000=Wind(300, 6),
            wind_at_8000=Wind(290, 10),
        ).apply(m)

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Incirlik for blue, Bassel/Kuweires for red, derive AO anchors."""
        t = self._terrain
        incirlik = t.airports["Incirlik"]
        # Gaziantep is the forward divert: 85 km from the depot against
        # Incirlik's 213, which on a damaged jet is the difference between a
        # recovery and a bail-out. See `_spawn_sanctuaries`.
        gaziantep = t.airports["Gaziantep"]
        bassel = t.airports["Bassel Al-Assad"]
        kuweires = t.airports["Kuweires"]
        incirlik.set_blue()
        gaziantep.set_blue()
        bassel.set_red()
        kuweires.set_red()
        sa6_anchor = offset(kuweires.position, east_m=0, north_m=8_000)
        depot_anchor = offset(kuweires.position, east_m=600, north_m=400)
        reserve_origin = offset(kuweires.position, east_m=22_000, north_m=-2_000)
        reserve_destination = offset(kuweires.position, east_m=2_000, north_m=-500)
        overlay = load_scene("syria")
        return _Scene(
            incirlik=incirlik,
            gaziantep=gaziantep,
            bassel=bassel,
            kuweires=kuweires,
            sa6_anchor=sa6_anchor,
            depot_anchor=depot_anchor,
            reserve_origin=reserve_origin,
            reserve_destination=reserve_destination,
            overlay=overlay,
        )

    # -- red side: air defences --------------------------------------------

    def _spawn_red_air_defence(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 site on a ridge + 55G6 EWR chain along the frontier."""
        sa6 = self._spawn_sa6_site(m, russia, scene)
        ewr_positions = self._spawn_red_ewr_chain(m, russia, scene)
        return sa6, ewr_positions

    def _spawn_sa6_site(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 'Gainful' on the ridge north of Kuweires (1S91 + 3x 2P25 + AAA).

        The 1S91 (`units[0]`) and its TELs sit in **one** group — a Kub
        launcher only engages when its Straight Flush radar shares the group.
        The 1S91 is still the priority kill: gate objectives on
        `UnitDead(units[0].id)`, not on splitting the site into two groups.
        Threat axis comes from the northwest (Incirlik bearing).
        """
        try:
            ridge = sam_site_on_ridge(
                scene.overlay,
                defends=scene.depot_anchor,
                threat_axis_deg=315.0,
                envelope_radius_m=18_000.0,
                min_prominence_m=15.0,
            )
        except LookupError:
            ridge = sam_site_on_ridge(
                scene.overlay,
                defends=scene.depot_anchor,
                threat_axis_deg=315.0,
                envelope_radius_m=22_000.0,
                min_prominence_m=5.0,
            )
        sa6 = templates.VehicleTemplate.sa6_site(
            m, russia, ridge, heading=315, prefix="Kobra ", skill=Skill.High
        )
        # pydcs's template is a two-rail site; this battery fields three. Same
        # group — a Kub TEL only engages while its 1S91 shares the group.
        tel = m.vehicle("Launcher 3", vehicles.AirDefence.Kub_2P25_ln)
        tel.position = ridge.point_from_heading(315 + 120, 45)
        tel.heading = 315
        tel.skill = Skill.High
        sa6.add_unit(tel)
        # After the third rail, or it stays where the template's neighbours were.
        ad.disperse_site(
            sa6,
            radius_m=300.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        aaa_offset = offset(ridge, east_m=-150, north_m=250)
        aaa = m.vehicle_group(
            russia,
            "AAA Kobra",
            vehicles.AirDefence.ZU_23_Emplacement,
            position=aaa_offset,
            heading=315,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(aaa, Skill.Average)
        return sa6

    def _spawn_red_ewr_chain(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[Point]:
        """2x 55G6 EWR chain north-east of Bassel feeding GCI to Kuweires."""
        frontier = [
            offset(scene.bassel.position, east_m=10_000, north_m=20_000),
            offset(scene.kuweires.position, east_m=-15_000, north_m=10_000),
        ]
        try:
            positions = scene.overlay.place_ewr_chain(
                frontier_polyline=frontier,
                count=2,
                min_spacing_m=30_000.0,
                min_elevation_m=150.0,
            )
        except LookupError:
            positions = frontier
        ad.build_ewr_chain(m, russia, positions, prefix="EWR Frontier")
        return positions

    # -- red side: depot, reserve, intercept --------------------------------

    def _spawn_red_depot(self, m: Mission, russia: Country, scene: _Scene):
        """Forward depot at Kuweires: trucks + armor + SA-13 + ZU-23 AAA.

        The first unit of the depot group is what Hawg is fragged on
        (`strike_flight(target=...)`). Wrapping it as a single platoon lets
        end-of-mission triggers test `GroupDead(depot.id)` instead of chasing
        every parking-spot truck.
        """
        depot_pos = scene.depot_anchor
        depot_types = [
            vehicles.Armor.T_72B,
            vehicles.Armor.T_72B,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Unarmed.GAZ_66,
            vehicles.Unarmed.GAZ_66,
        ]
        depot = m.vehicle_group_platoon(
            russia,
            "Depot Bear",
            cast(list[type[VehicleType]], depot_types),
            position=depot_pos,
            heading=0,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(depot, Skill.Average)
        shorad_pos = Point(depot_pos.x + 400, depot_pos.y - 200, self._terrain)
        shorad = m.vehicle_group(
            russia,
            "SHORAD Bear",
            vehicles.AirDefence.Strela_10M3,
            position=shorad_pos,
            heading=315,
        )
        set_skill(shorad, Skill.High)
        depot_aaa = m.vehicle_group(
            russia,
            "Depot AAA",
            vehicles.AirDefence.ZU_23_Emplacement,
            position=Point(depot_pos.x - 350, depot_pos.y + 200, self._terrain),
            heading=315,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(depot_aaa, Skill.Average)
        return depot, shorad_pos

    def _spawn_red_reserve(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """Armored reserve on the Aleppo road, late-activated on depot kill.

        Snap origin and destination to real roads (wider 15 km search than
        the default `place_convoy_route` because the rolling country east
        of Kuweires is sparse), then pre-load an OnRoad push waypoint west
        toward Kuweires. Spawn waypoint is OnRoad too, or the column would
        path the leg cross-country. The group is dormant until
        `_add_layered_triggers` flips `ActivateGroup` on depot destruction.
        """
        spawn = scene.overlay.overlay.find_road_spawn(
            scene.reserve_origin, radius_m=15_000
        )
        push_target = scene.overlay.overlay.find_road_spawn(
            scene.reserve_destination, radius_m=15_000
        )
        heading = int(spawn.heading_between_point(push_target))
        reserve_types = [
            vehicles.Armor.T_72B,
            vehicles.Armor.T_72B,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
        ]
        reserve = m.vehicle_group_platoon(
            russia,
            "Reserve Bear",
            cast(list[type[VehicleType]], reserve_types),
            position=spawn,
            heading=heading,
            move_formation=PointAction.OnRoad,
        )
        reserve.late_activation = True
        reserve.add_waypoint(
            push_target,
            move_formation=PointAction.OnRoad,
            speed=40,
        )
        set_skill(reserve, Skill.Average)
        return reserve

    def _spawn_red_intercept(self, m: Mission, russia: Country, scene: _Scene):
        """2x MiG-29S out of Bassel Al-Assad, late-activated by intrusion zone.

        Zone is centered on the depot — once blue pushes into the AO the MiGs
        are armed; depot destruction trigger then flips them to active and
        adds the AITaskPush. (pydcs `intercept_flight` builds the AITaskPush
        on the zone trigger automatically; activation is what gates them.)
        """
        intrusion_zone = m.triggers.add_triggerzone(
            position=scene.depot_anchor,
            radius=45_000,
            hidden=True,
            name="MIG intrusion",
        )
        migs = m.intercept_flight(
            russia,
            "Ivan",
            planes.MiG_29S,
            airport=scene.bassel,
            zone=intrusion_zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=900,
            altitude=7500,
            max_engage_distance=90_000,
            group_size=2,
        )
        set_skill(migs, Skill.High)
        apply_ai_difficulty(migs, self.difficulty)
        return migs

    # -- blue side ----------------------------------------------------------

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """E-3A Magic on a Mediterranean track, 251.000 AM, 120 km legs.

        2h sortie demands a long race-track so Magic lives the whole mission;
        pacing model gives ~1.5 km/min station-keeping → 180 km is comfortable.
        """
        p1, p2 = scene.overlay.place_awacs_track(
            home_base=scene.incirlik.position,
            threat_axis=scene.depot_anchor,
            standoff_m=120_000.0,
            track_length_m=180_000.0,
        )
        track = race_track(p1, p2)
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.incirlik,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=9000,
            speed=740,
            start_type=StartType.Warm,
            frequency=251,
        )
        return p1, p2

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco over the Med west of Latakia, TACAN 10X, 270.000 AM."""
        p1, p2 = scene.overlay.place_tanker_track(
            home_base=scene.incirlik.position,
            threat_axis=scene.depot_anchor,
            standoff_m=100_000.0,
            track_length_m=80_000.0,
        )
        track = race_track(p1, p2)
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.incirlik,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=6500,
            speed=750,
            start_type=StartType.Warm,
            frequency=270,
            tacanchannel="10X",
        )
        return p1, p2

    def _spawn_tarcap(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C Eagle 2-ship TARCAP east of the AO, racetrack toward Bassel."""
        threat_bearing = scene.incirlik.position.heading_between_point(
            scene.bassel.position
        )
        p1, p2 = scene.overlay.place_cap_station(
            defended_asset=scene.depot_anchor,
            threat_bearing_deg=threat_bearing,
            forward_distance_m=40_000.0,
            track_length_m=50_000.0,
        )
        eagle = m.patrol_flight(
            usa,
            "Eagle",
            planes.F_15C,
            airport=scene.incirlik,
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
        depot: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ):
        """A-10C 2-ship Hawg from Incirlik, routed onto the depot.

        Built by hand rather than with `Mission.strike_flight`, whose attack
        waypoint pydcs hard-codes to `alt = 0` — the pair descended to sea level
        over the depot's IR launcher and its guns. The run-in is flown above
        that, and the transit bends around the SA-6 rather than crossing it.

        The pair still pushes on take-off; the depot sits under the SA-6
        umbrella, so the player is expected to put the search radar down before
        they arrive. That is the mission, and Magic calls it when it happens.
        """
        hog = m.flight_group_from_airport(
            country=usa,
            name="Hawg",
            aircraft_type=planes.A_10C,
            airport=scene.incirlik,
            maintask=task.CAS,
            start_type=StartType.Warm,
            group_size=2,
        )
        set_skill(hog, Skill.High)
        arm(
            hog,
            planes.A_10C,
            [
                (1, "ALQ_184"),
                (2, "Mk_82___500lb_GP_Bomb_LD"),
                (3, "LAU_117_AGM_65G"),
                (9, "LAU_117_AGM_65G"),
                (10, "Mk_82___500lb_GP_Bomb_LD"),
                (11, "LAU_105_1_AIM_9M_R"),
            ],
        )
        apply_threat_reaction(hog)
        self._route_strike(hog, scene, depot=depot, threats=threats)
        return hog

    def _route_strike(
        self,
        hog,
        scene: _Scene,
        *,
        depot: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> None:
        """Incirlik → IP → run-in on the depot → egress → Incirlik."""
        target = scene.depot_anchor
        hog.add_runway_waypoint(scene.incirlik)
        ip = routing.standoff_point(
            target,
            toward=scene.incirlik.position,
            threats=threats,
            min_distance_m=20_000.0,
            clearance_m=3_000.0,
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                scene.incirlik.position, ip, threats, clearance_m=4_000.0
            )[1:],
            start=1,
        ):
            hog.add_waypoint(pt, altitude=4_600, speed=520, name=f"INGRESS-{i}")
        # Above the SA-13 sitting inside the depot wire, and inside Maverick
        # range of it. pydcs's own attack waypoint would have been at zero.
        attack = hog.add_waypoint(target, altitude=4_000, speed=500, name="ATTACK")
        attack.tasks.append(
            task.AttackGroup(
                depot.id,
                weapon_type=task.WeaponType.Auto,
                group_attack=True,
                expend=task.Expend.All,
            )
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                target, scene.incirlik.position, threats, clearance_m=4_000.0
            )[1:-1],
            start=1,
        ):
            hog.add_waypoint(pt, altitude=4_600, speed=540, name=f"EGRESS-{i}")
        hog.add_runway_waypoint(scene.incirlik)
        hog.land_at(scene.incirlik)

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Springfield F-16C-50 from Incirlik, terrain-masked SEAD ingress."""
        sections = player_flight(
            m,
            country=usa,
            name="Springfield",
            aircraft_type=planes.F_16C_50,
            airport=scene.incirlik,
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
        push = offset(scene.incirlik.position, east_m=20_000, north_m=-25_000)
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=scene.sa6_anchor,
            threats=threats,
            waypoints=4,
            leg_search_radius_m=8_000.0,
        )
        for player in sections:
            self._route_springfield(player, scene, corridor)
        return [*corridor, scene.depot_anchor]

    def _route_springfield(
        self, player: FlyingGroup, scene: _Scene, corridor: Sequence[Point]
    ) -> None:
        """Incirlik → PUSH → corridor → SA6 TGT → DEPOT → Incirlik.

        One route, flown by every section: the corridor is a terrain-masking
        search against the overlay, so it is placed once and handed to each
        section rather than searched again per group.
        """
        ov = scene.overlay.overlay
        player.add_runway_waypoint(scene.incirlik)
        for i, pt in enumerate(corridor[:-1]):
            name = "PUSH" if i == 0 else f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=7000, speed=800, name=name)
        # Both remaining steerpoints mark something on the ground — the SA-6
        # site the corridor ends on, then the depot — so they sit on the
        # terrain; the INGRESS legs above carry the run-in altitude.
        waypoints.add_ground_waypoint(
            player, corridor[-1], overlay=ov, speed=800, name="SA6 TGT"
        )
        waypoints.add_ground_waypoint(
            player, scene.depot_anchor, overlay=ov, speed=800, name="DEPOT"
        )
        player.add_runway_waypoint(scene.incirlik)
        player.land_at(scene.incirlik)

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
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary, sanc.Sanctuary]:
        """Two covered fields on our side, one on theirs.

        This is the only mission in the project with a **forward** sanctuary, and
        the reason is the range: 213 km from Incirlik to the depot is the longest
        egress here by 60 km. `REDOUBT` covers home, and `PICKET` at Gaziantep
        covers a runway 85 km from the target — a jet losing oil pressure over
        Kuweires can reach one of those and not the other.

        The two systems are different because the geometry is. Hawk at Incirlik
        reaches 45 km into empty Turkish airspace; the same battery at Gaziantep
        would reach 40 km toward the AO, across the ground the MiGs transit and
        the strike egresses through, which is exactly the quiet mission rewrite
        `keep_clear` exists to refuse. NASAMS' 15 km covers the field, the
        overhead and the pattern and nothing else, which is all a divert needs.

        Bassel Al-Assad gets the red battery because that is where the MiG-21s
        recover. Kuweires is the field a mission would reach for first and the one
        `build_sanctuary` refuses outright: the depot **is** the Kuweires apron,
        so no envelope emplaced there clears the objective. Bassel is 172 km from
        the target, which makes 18 km of S-125 irrelevant to the strike and
        decisive against a Viper that chases a withdrawing MiG down the coast.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.incirlik,
            callsign=_SANCTUARY,
            facing=scene.depot_anchor,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[scene.depot_anchor, *red_sites],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        forward = sanc.build_sanctuary(
            m,
            usa,
            scene.gaziantep,
            callsign=_FORWARD_SANCTUARY,
            facing=scene.depot_anchor,
            battery=_FORWARD_BATTERY,
            divert=True,
            keep_clear=[scene.depot_anchor, *red_sites],
            point_defence=2,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        bassel_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.bassel,
            callsign="Bassel field",
            facing=scene.depot_anchor,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Bassel",
            keep_clear=[scene.depot_anchor, *stations],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, forward, bassel_ad

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        Includes the reserve waiting on the Aleppo road — an unhidden reserve
        would spoil its own counter-push before it ever rolls.
        """
        conceal_country(russia)

    def _threat_rings(
        self, *, sa6_pos: Point, shorad_pos: Point
    ) -> tuple[ThreatRing, ...]:
        """The two shooting envelopes, for both the drawn plan and AI routing.

        Same radii `_draw_plan` paints, so the briefing and the friendly flight
        plan come from one number. The EWRs are not here — they cannot shoot.
        """
        return (
            ThreatRing(sa6_pos, 12_000.0, "SA-6"),
            ThreatRing(shorad_pos, 6_000.0, "SA-13"),
        )

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        sa6_pos: Point,
        shorad_pos: Point,
        ewr_positions: list[Point],
        reserve_origin: Point,
        corridor: list[Point],
        tarcap_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
        home: sanc.Sanctuary,
        forward: sanc.Sanctuary,
        bassel_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan on the F10 map (trained: coarse, estimated threats).

        Returns the estimated air-defense rings as HSD threat points, so the
        cockpit shows the same claim as the map. The EWRs and the armor reserve
        stay map-only — neither is a missile envelope to stay outside of.
        """
        # The two umbrellas go on first so their marshal legs win the
        # cartridge's navigation budget: `core/dtc.py` fills that tab in draw
        # order after the flight's own route, and on a 213 km egress the forward
        # field is the mark most likely to matter. `PICKET` is drawn before
        # `REDOUBT` for the same reason — it is the one a broken jet reaches.
        forward.draw(plan)
        home.draw(plan)
        plan.objective(scene.depot_anchor, "Depot — Kuweires", radius=5_000.0)
        plan.route(corridor, "Springfield ingress")
        plan.orbit(*tarcap_track, "Eagle TARCAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco tanker")
        hsd = dtc.briefed(
            plan.threat(
                sa6_pos, radius=12_000.0, label="SA-6", icon=StandardIcon.AirDefense
            ),
            dtc.SA_6,
            label="SA-6",
        )
        hsd += dtc.briefed(
            plan.threat(
                shorad_pos, radius=6_000.0, label="SA-13", icon=StandardIcon.AirDefense
            ),
            dtc.SA_13,
            label="SA-13",
        )
        for pos in ewr_positions:
            plan.threat(pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)
        # The reserve is a road column that is not even on the ground yet, so it
        # gets a mark on the road it will come up: a ring would claim reach
        # around a laager it leaves the moment it matters.
        plan.mobile_threat(
            reserve_origin, "Reserve (Aleppo rd)", icon=StandardIcon.Mechanized
        )
        # Bassel's own belt is a red ring like any other — estimated, and into
        # the cartridge beside the Gainful. It reaches 18 km and the depot is
        # 172 km away, so it costs the strike nothing and costs a chase down the
        # coast everything.
        return hsd + bassel_ad.draw(plan)

    def _load_cartridge(
        self,
        m: Mission,
        scene: _Scene,
        points: list[dtc.ThreatPoint],
        *,
        plan: PlanOverlay,
    ) -> None:
        """Load the briefed SAM rings as pre-planned threats on the player's cartridge.

        And onto the kneeboard's threat block, which is where the same estimates
        turn into coordinates a pilot can read.

        The same cartridge carries the rest of the plan the F10 map shows: the
        flight's own route and the plan's marks as steerpoints, its lines as the
        HSD's GEO lines. The map and the cockpit are one briefing, drawn from
        one set of positions.
        """
        dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)
        dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)

    # -- triggers and briefing ----------------------------------------------

    def _add_intro_voice(self, m: Mission) -> None:
        """Mission-start AWACS picture + support tap call (TriggerStart).

        Fires the moment the mission loads. Gives the player the picture,
        names the threat, and reminds them of tanker frequency / TACAN so
        they don't have to dig into the kneeboard during taxi.
        """
        mission_triggers.intro(
            m,
            comment="Magic mission-start picture",
            voice=self._voice,
            text=(
                "Springfield, Magic on station. Picture: clean cold east, "
                "MiG-29S parked at Bassel Al-Assad. SA-6 search radar lit "
                "north of Kuweires. Texaco on tap, 270.0, TACAN 10X."
            ),
        )

    def _add_support_checkins(self, m: Mission, home: sanc.Sanctuary) -> None:
        """Staged support check-ins across the early sortie (TimeAfter).

        The umbrella is read out with them, and it has to be read out at all:
        the cyan ring is easy to take for decoration and nobody opens the F10
        map again after push. Same argument as `core/jtac`'s `push_at_s`.
        """
        mission_triggers.checkin(
            m,
            voice=self._voice,
            at_seconds=120,
            comment="REDOUBT umbrella check-in",
            text=sanc.checkin_text(home, controller="Magic"),
        )
        mission_triggers.checkin(
            m,
            voice=self._voice,
            at_seconds=180,
            comment="Texaco check-in",
            text="Springfield, Texaco established overhead, 6500 feet, "
            "ready for receivers. 270.0, TACAN 10X.",
        )
        mission_triggers.checkin(
            m,
            voice=self._voice,
            at_seconds=360,
            comment="Eagle TARCAP on station",
            text="Magic, Eagle TARCAP on station east of the AO, fuel state plus one.",
        )
        mission_triggers.checkin(
            m,
            voice=self._voice,
            at_seconds=540,
            comment="Hawg holding",
            text="Magic, Hawg holding west of the AO, ready for SAM safe "
            "call from Springfield.",
        )

    def _add_layered_triggers(
        self,
        m: Mission,
        *,
        sa6,
        depot,
        hog,
        migs,
        reserve,
    ) -> None:
        """Wire the four-phase objective chain with announce-on-fire messages.

        Phase gates (objective layering):
          1. SA-6 1S91 radar dead → "SAM safe, Hawg cleared to push". The 1S91
             is `sa6.units[0]`; gate on `UnitDead` so the shot-capable TELs can
             stay in the same group (Kub only fires with its radar in-group).
          2. Depot dead → activate MiG-29S scramble + reserve convoy, both
             announce themselves.
          3. Mission success when SA-6 radar + depot + MiGs are all down.
          4. Failure if Hawg dies while depot still up, or reserve reaches
             the depot anchor (handled implicitly: reserve OnRoad waypoint
             ends at Kuweires).
        """
        self._add_sa6_radar_down_trigger(m, sa6=sa6)
        self._add_depot_killed_triggers(m, depot=depot, migs=migs, reserve=reserve)
        self._add_end_triggers(
            m,
            sa6=sa6,
            depot=depot,
            hog=hog,
            migs=migs,
        )

    def _add_sa6_radar_down_trigger(self, m: Mission, *, sa6) -> None:
        """Announce SAM-safe and effectively clear Hawg's run."""
        rule = triggers.TriggerOnce(comment="SA-6 search radar destroyed")
        rule.add_condition(condition.UnitDead(sa6.units[0].id))
        call = "Magic: SA-6 search radar is down. Hawg, cleared to push on the depot."
        rule.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue,
                m.string(call),
                seconds=15,
            )
        )
        self._voice.attach_to_coalition(m, rule, call, coalition="blue")
        m.triggerrules.triggers.append(rule)

    def _add_depot_killed_triggers(self, m: Mission, *, depot, migs, reserve) -> None:
        """Depot dead → scramble MiGs and push the armored reserve."""
        mig_rule = triggers.TriggerOnce(comment="Depot down: MiGs scramble")
        mig_rule.add_condition(condition.GroupDead(depot.id))
        mig_rule.add_action(action.ActivateGroup(migs.id))
        mig_call = (
            "Magic: 2 contacts airborne out of Bassel Al-Assad, "
            "MiG-29S, vectoring on the AO."
        )
        mig_rule.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue,
                m.string(mig_call),
                seconds=15,
            )
        )
        self._voice.attach_to_coalition(m, mig_rule, mig_call, coalition="blue")
        m.triggerrules.triggers.append(mig_rule)

        reserve_rule = triggers.TriggerOnce(comment="Depot down: reserve activates")
        reserve_rule.add_condition(condition.GroupDead(depot.id))
        reserve_rule.add_action(action.ActivateGroup(reserve.id))
        reserve_call = (
            "Russian armored reserve pushing west on the Aleppo road. "
            "Hawg or Springfield, interdict before they reach Kuweires."
        )
        reserve_rule.add_action(
            action.MessageToAll(
                m.string(reserve_call),
                seconds=15,
            )
        )
        self._voice.attach_to_all(m, reserve_rule, reserve_call)
        m.triggerrules.triggers.append(reserve_rule)

    def _add_end_triggers(self, m: Mission, *, sa6, depot, hog, migs) -> None:
        """Success when primary + secondary objectives complete; failure on Hawg loss."""
        mission_triggers.message_to_all(
            m,
            comment="All objectives met",
            conditions=(
                condition.UnitDead(sa6.units[0].id),
                condition.GroupDead(depot.id),
                condition.GroupDead(migs.id),
            ),
            voice=self._voice,
            text=(
                "Magic: the ridge is quiet, the depot is burning and the sky is "
                "clear. Springfield, RTB Incirlik. Texaco available north for fuel."
            ),
            seconds=25,
        )

        # Gated on the search radar, exactly as the "all objectives" rule above
        # and `_add_sa6_radar_down_trigger` are. It used to ask for GroupDead —
        # every launcher and the command post as well — which is strictly more
        # than the success rule needs, so "primary objectives met" could arrive
        # *after* "all objectives met", or never, with the site already blind.
        mission_triggers.message_to_coalition(
            m,
            comment="Primary objectives met",
            conditions=(
                condition.UnitDead(sa6.units[0].id),
                condition.GroupDead(depot.id),
            ),
            voice=self._voice,
            text=(
                "Magic: the ridge and the depot are down. Deal with the fighters "
                "and that column, then RTB."
            ),
            seconds=20,
        )

        mission_triggers.message_to_all(
            m,
            comment="Strike package down",
            conditions=(
                condition.GroupDead(hog.id),
                condition.GroupAlive(depot.id),
            ),
            voice=self._voice,
            text=(
                "Magic: we have lost Hawg and the depot is untouched. There is "
                "nothing left to run the strike with. Springfield, RTB Incirlik."
            ),
        )

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Suppress the SA-6 north of Kuweires, escort Hawg 1-2 onto the "
            "Russian depot, then intercept the MiG-29S scramble out of "
            "Bassel Al-Assad and disrupt the armored reserve on the Aleppo "
            "road. RTB Incirlik. Tank from Texaco as required."
        )
        m.set_description_redtask_text(
            "Defend the Kuweires depot with the SA-6 ridge and SHORAD. "
            "Scramble MiG-29S from Bassel Al-Assad on depot strike. "
            "Push the armored reserve west from the Aleppo road to retake "
            "the site once it is struck."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(EasternShield)


if __name__ == "__main__":
    main()
