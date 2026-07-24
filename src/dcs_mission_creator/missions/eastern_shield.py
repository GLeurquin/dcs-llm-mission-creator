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

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from dcs import action, condition, planes, task, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.point import PointAction
from dcs.terrain.syria.syria import Syria
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.placement import load_scene, sam_site_on_ridge
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.map_overlay.scene import TacticalScene


def _offset(
    origin: Point, terrain: Syria, *, east_m: float = 0, north_m: float = 0
) -> Point:
    """Return a point offset from `origin` in DCS world meters (east/north)."""
    return Point(origin.x + north_m, origin.y + east_m, terrain)


def _mark_clients(group) -> None:
    """Mark every unit in `group` as a coop client slot."""
    for u in group.units:
        u.skill = Skill.Client


def _set_skill(group, skill: Skill) -> None:
    """Apply `skill` to every unit of `group`."""
    for u in group.units:
        u.skill = skill


@dataclass
class _Scene:
    """Resolved airports + AO geometry + map overlay used by every spawn step."""

    incirlik: Airport
    bassel: Airport
    kuweires: Airport
    sa6_anchor: Point
    depot_anchor: Point
    reserve_origin: Point
    reserve_destination: Point
    overlay: TacticalScene


class EasternShield(MissionBuilder):
    name = "eastern_shield"
    title = "Eastern Shield"

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
  Russian expeditionary forces have stood up a forward
  supply depot at Kuweires, defended by a relocatable
  SA-6 'Gainful' site on the ridge to the north and a
  GCI chain feeding MiG-29S out of Bassel Al-Assad.
  USAF has been tasked to break the depot and degrade
  Russian air defence coverage of the Aleppo corridor.

MISSION (Springfield — F-16C-50, Incirlik)
  Phase 1: Suppress the SA-6 site north of Kuweires.
  Phase 2: Escort Hawg 1-2 onto the depot after the
           SA-6 search radar is down.
  Phase 3: Intercept any Russian fighters that scramble
           out of Bassel Al-Assad in response.
  Phase 4: Disrupt the armored reserve column pushing
           west on the Aleppo road if it gets close.

PACKAGE
  Springfield 1 (you): F-16C-50, Incirlik, hot ramp,
        HARM + AIM-120/9X loadout (planner default).
  Hawg 1-2 : A-10C, Incirlik, strike on depot. Holds
        west of the AO until Springfield calls SAM safe.
  Eagle 1-2: F-15C TARCAP, Incirlik, race-track east
        of the AO covering the strike.
  Magic    : E-3A AWACS, 251.000 AM, Med track north.
  Texaco   : KC-135 tanker, 270.000 AM, TACAN 10X,
        track over the Med west of Latakia.

THREATS
  Air : 2x MiG-29S (High), R-77 class, late-activated
        from Bassel Al-Assad on depot destruction.
        Russian EWR chain (2x 55G6) vectoring them.
  SAM : SA-6 site (1S91 search + 3x 2P25 TEL) on the
        ridge north of Kuweires. 25 km envelope.
        SA-13 SHORAD organic to the depot.
  AAA : 2x ZU-23 emplaced with the SA-6, 2x ZU-23 in
        the depot.
  Land: Russian armored reserve (2x T-72B + 4x BTR-80)
        late-activated on the Aleppo road east of Kuweires.

ROE / FRAGS
  - Cleared to engage SA-6 site and depot ground units.
  - Cleared to engage Russian aircraft entering the AO.
  - Stay above SA-6 MEZ until search radar is destroyed.
  - Tank from Texaco pre-push and post-AO if needed.
  - Bingo fuel: 3500 lb. RTB Incirlik (no divert).

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  SA-6 ridge        : ~8 km north of Kuweires.
  Depot             : Kuweires airfield apron + truck park.
  PUSH waypoint     : 30 km southeast of Incirlik.

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
**Players:** {self.players} coop slot(s)
**Difficulty:** trained
**Expected sortie length:** ~120 minutes (2 hours)

## Situation

Russian expeditionary forces have stood up a forward supply depot at Kuweires
in northern Syria, defended by a relocatable SA-6 'Gainful' site on the ridge
to the north and a GCI chain feeding MiG-29S out of Bassel Al-Assad. USAF has
been tasked to break the depot and degrade Russian air defence coverage of the
Aleppo corridor before the Russian reserve closes the road.

## Mission

`Springfield` flight runs a four-phase package out of Incirlik:

1. **Phase 1 — SEAD.** Suppress the SA-6 site north of Kuweires. The
   1S91 search radar is the priority target — once it dies, the rest of
   the package is cleared to push.
2. **Phase 2 — Strike escort.** `Hawg 1-2` holds west of the AO until
   `Springfield` calls SAM safe, then runs in on the depot. Stay between
   `Hawg` and the threat axis.
3. **Phase 3 — DCA.** 2x Russian MiG-29S launch out of Bassel Al-Assad
   when the depot is destroyed. Sanitize the airspace before they get a
   shot on the strike package.
4. **Phase 4 — Convoy interdict.** A Russian armored reserve activates
   and pushes west on the Aleppo road. `Hawg` re-tasks if it can; otherwise
   `Springfield` rolls in with AGM-65 / strafe on the BTR/T-72 column.

## Package

| Callsign    | Type    | Base     | Role                              |
|-------------|---------|----------|-----------------------------------|
| Springfield | F-16C-50| Incirlik | Player SEAD lead / escort / DCA   |
| Hawg 1-2    | A-10C   | Incirlik | Strike on depot (holds for SEAD)  |
| Eagle 1-2   | F-15C   | Incirlik | TARCAP race-track east of the AO  |
| Magic       | E-3A    | Incirlik | AWACS, 251.000 AM, Med track north|
| Texaco      | KC-135  | Incirlik | Tanker, 270.000 AM, TACAN 10X     |

## Threats

- **SAM (priority):** SA-6 'Gainful' site — 1x 1S91 search radar + 3x 2P25
  TEL + 2x ZU-23 AAA on the ridge north of Kuweires. 25 km engagement zone.
- **SHORAD:** 1x SA-13 organic to the depot, 2x ZU-23 AAA in the depot.
- **Air:** 2x MiG-29S (Skill High), R-77/R-27 class, late-activated from
  Bassel Al-Assad on depot destruction. Russian EWR chain (2x 55G6)
  vectoring them.
- **Land reserve:** 2x T-72B + 4x BTR-80 activating on the Aleppo road
  east of Kuweires, pushing west toward the depot site.

## ROE

- Cleared to engage the SA-6 site, depot ground units, and any Russian
  aircraft entering the AO.
- Stay above the SA-6 MEZ until the 1S91 search radar is destroyed.
- Tank from `Texaco` pre-push and post-AO; F-16C internal fuel does not
  cover a 2-hour sortie without at least one tanker pass.
- Bingo fuel: 3500 lb. RTB Incirlik (no divert).

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- SA-6 ridge: ~8 km north of Kuweires.
- Depot: Kuweires airfield apron and truck park.
- PUSH waypoint: 30 km southeast of Incirlik.

## Frequencies

- Magic AWACS: 251.000 AM
- Texaco tanker: 270.000 AM, TACAN 10X
- Incirlik tower: per kneeboard

## Weather

Late-spring eastern Mediterranean haze, light NW wind, 22 °C. QNH 760 mmHg.
Visibility 40 km (haze layer). Scattered layer at 2800 m, 400 m thick.

## Win / loss conditions

- **Primary success:** SA-6 site destroyed *and* depot destroyed.
- **Full success:** Primary + MiG-29S scramble defeated + reserve convoy
  destroyed before reaching the depot.
- **Failure:** `Hawg` is destroyed before the depot is, or the reserve
  convoy reaches Kuweires.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- top-level orchestration --------------------------------------------

    def build_miz(self, miz_path: Path) -> None:
        """Assemble the mission by calling each step in package order."""
        m = Mission(self._terrain)

        self._set_time(m)
        self._set_weather(m)
        scene = self._setup_airports(m)
        self._scene = scene
        usa, russia = m.country("USA"), m.country("Russia")

        sa6_radar, sa6_tels, ewr_positions = self._spawn_red_air_defence(
            m, russia, scene
        )
        depot, shorad_pos = self._spawn_red_depot(m, russia, scene)
        reserve = self._spawn_red_reserve(m, russia, scene)
        migs = self._spawn_red_intercept(m, russia, scene)

        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        tarcap_track = self._spawn_tarcap(m, usa, scene)
        hog = self._spawn_strike(m, usa, scene, target_unit=depot.units[0])
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            threats=(scene.sa6_anchor, shorad_pos, *ewr_positions),
        )

        self._draw_plan(
            m,
            scene,
            sa6_pos=scene.sa6_anchor,
            shorad_pos=shorad_pos,
            ewr_positions=ewr_positions,
            reserve_origin=scene.reserve_origin,
            corridor=corridor,
            tarcap_track=tarcap_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
        )
        self._add_intro_voice(m)
        self._add_support_checkins(m)
        self._add_layered_triggers(
            m,
            sa6_radar=sa6_radar,
            sa6_tels=sa6_tels,
            depot=depot,
            hog=hog,
            migs=migs,
            reserve=reserve,
        )
        self._add_briefing(m)

        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """09:00 local on 21 May 2026 (Syria is UTC+3)."""
        m.start_time = datetime(2026, 5, 21, 6, 0, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Late-spring east-Med haze, light NW wind, 22 C, 40 km visibility."""
        w = m.weather
        w.season_temperature = 22.0
        w.qnh = 760
        w.wind_at_ground.direction = 310
        w.wind_at_ground.speed = 3
        w.wind_at_2000.direction = 300
        w.wind_at_2000.speed = 6
        w.wind_at_8000.direction = 290
        w.wind_at_8000.speed = 10
        w.clouds_base = 2800
        w.clouds_thickness = 400
        w.clouds_density = 3
        w.visibility_distance = 40000
        w.name = "Spring haze"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Incirlik for blue, Bassel/Kuweires for red, derive AO anchors."""
        t = self._terrain
        incirlik = t.airports["Incirlik"]
        bassel = t.airports["Bassel Al-Assad"]
        kuweires = t.airports["Kuweires"]
        incirlik.set_blue()
        bassel.set_red()
        kuweires.set_red()
        sa6_anchor = _offset(kuweires.position, t, east_m=0, north_m=8_000)
        depot_anchor = _offset(kuweires.position, t, east_m=600, north_m=400)
        reserve_origin = _offset(kuweires.position, t, east_m=22_000, north_m=-2_000)
        reserve_destination = _offset(kuweires.position, t, east_m=2_000, north_m=-500)
        overlay = load_scene("syria")
        return _Scene(
            incirlik=incirlik,
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
        sa6_radar, sa6_tels = self._spawn_sa6_site(m, russia, scene)
        ewr_positions = self._spawn_red_ewr_chain(m, russia, scene)
        return sa6_radar, sa6_tels, ewr_positions

    def _spawn_sa6_site(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 'Gainful' on the ridge north of Kuweires (1S91 + 3x 2P25 + AAA).

        Layered priority target: the 1S91 search radar gates whether Hawg can
        push; the TELs become safe ground kills once it dies. Threat axis
        comes from the northwest (Incirlik bearing).
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
        radar = m.vehicle_group(
            russia,
            "SAM Kobra Search",
            vehicles.AirDefence.Kub_1S91_str,
            position=ridge,
            heading=315,
        )
        _set_skill(radar, Skill.High)
        tel_offset = Point(ridge.x - 200, ridge.y + 300, self._terrain)
        tels = m.vehicle_group(
            russia,
            "SAM Kobra TELs",
            vehicles.AirDefence.Kub_2P25_ln,
            position=tel_offset,
            heading=315,
            group_size=3,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(tels, Skill.High)
        aaa_offset = Point(ridge.x + 250, ridge.y - 150, self._terrain)
        aaa = m.vehicle_group(
            russia,
            "AAA Kobra",
            vehicles.AirDefence.ZU_23_Emplacement,
            position=aaa_offset,
            heading=315,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(aaa, Skill.Average)
        return radar, tels

    def _spawn_red_ewr_chain(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[Point]:
        """2x 55G6 EWR chain north-east of Bassel feeding GCI to Kuweires."""
        frontier = [
            _offset(
                scene.bassel.position, self._terrain, east_m=10_000, north_m=20_000
            ),
            _offset(
                scene.kuweires.position, self._terrain, east_m=-15_000, north_m=10_000
            ),
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
        for i, pos in enumerate(positions):
            grp = m.vehicle_group(
                russia,
                f"EWR Frontier-{i + 1}",
                vehicles.AirDefence.X_55G6_EWR,
                position=pos,
                heading=270,
            )
            _set_skill(grp, Skill.High)
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
        _set_skill(depot, Skill.Average)
        shorad_pos = Point(depot_pos.x + 400, depot_pos.y - 200, self._terrain)
        shorad = m.vehicle_group(
            russia,
            "SHORAD Bear",
            vehicles.AirDefence.Strela_10M3,
            position=shorad_pos,
            heading=315,
        )
        _set_skill(shorad, Skill.High)
        depot_aaa = m.vehicle_group(
            russia,
            "Depot AAA",
            vehicles.AirDefence.ZU_23_Emplacement,
            position=Point(depot_pos.x - 350, depot_pos.y + 200, self._terrain),
            heading=315,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(depot_aaa, Skill.Average)
        return depot, shorad_pos

    def _spawn_red_reserve(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> VehicleGroup:
        """Armored reserve on the Aleppo road, late-activated on depot kill.

        Snap origin and destination to real roads (wider 15 km search than
        the default `place_convoy_route` because the rolling country east
        of Kuweires is sparse), then pre-load an OnRoad push waypoint west
        toward Kuweires. The group is dormant until `_add_layered_triggers`
        flips `ActivateGroup` on depot destruction.
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
        )
        reserve.late_activation = True
        reserve.add_waypoint(
            push_target,
            move_formation=PointAction.OnRoad,
            speed=40,
        )
        _set_skill(reserve, Skill.Average)
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
            speed=440,
            altitude=7500,
            max_engage_distance=90_000,
            group_size=2,
        )
        _set_skill(migs, Skill.High)
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
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.incirlik,
            position=p1,
            race_distance=int(p1.distance_to_point(p2)),
            heading=int(p1.heading_between_point(p2)),
            altitude=9000,
            speed=410,
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
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.incirlik,
            position=p1,
            race_distance=int(p1.distance_to_point(p2)),
            heading=int(p1.heading_between_point(p2)),
            altitude=6500,
            speed=407,
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
            speed=430,
            altitude=8000,
            max_engage_distance=90_000,
            group_size=2,
        )
        _set_skill(eagle, Skill.High)
        return p1, p2

    def _spawn_strike(self, m: Mission, usa: Country, scene: _Scene, *, target_unit):
        """A-10C 2-ship Hawg from Incirlik, fragged on the depot lead vehicle.

        Hawg's onboard route is the pydcs default IP/Attack/Fence-out
        sequence — they will push on take-off. The package design relies on
        the player getting the SAM down before they enter the MEZ; for a
        trained mission we accept the AI's eagerness rather than gating the
        push behind a flag (the player has Magic and Eagle to call SAM safe).
        """
        hog = m.strike_flight(
            usa,
            "Hawg",
            planes.A_10C,
            target=target_unit,
            airport=scene.incirlik,
            start_type=StartType.Warm,
            group_size=2,
        )
        _set_skill(hog, Skill.High)
        return hog

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Springfield F-16C-50 from Incirlik, terrain-masked SEAD ingress."""
        player = m.flight_group_from_airport(
            country=usa,
            name="Springfield",
            aircraft_type=planes.F_16C_50,
            airport=scene.incirlik,
            maintask=task.SEAD,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        _mark_clients(player)
        player.add_runway_waypoint(scene.incirlik)
        push = _offset(
            scene.incirlik.position, self._terrain, east_m=20_000, north_m=-25_000
        )
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=scene.sa6_anchor,
            threats=threats,
            waypoints=4,
            leg_search_radius_m=8_000.0,
        )
        for i, pt in enumerate(corridor):
            if i == 0:
                name = "PUSH"
            elif i == len(corridor) - 1:
                name = "SA6 IP"
            else:
                name = f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=7000, speed=400, name=name)
        player.add_waypoint(scene.depot_anchor, altitude=6500, speed=400, name="DEPOT")
        player.add_runway_waypoint(scene.incirlik)
        player.land_at(scene.incirlik)
        return [*corridor, scene.depot_anchor]

    # -- F10 map briefing ---------------------------------------------------

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        sa6_pos: Point,
        shorad_pos: Point,
        ewr_positions: list[Point],
        reserve_origin: Point,
        corridor: list[Point],
        tarcap_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
    ) -> None:
        """Paint the plan on the F10 map (trained: coarse, estimated threats)."""
        plan = PlanOverlay(m, "trained")
        plan.objective(scene.depot_anchor, "Depot — Kuweires", radius=5_000.0)
        plan.route(corridor, "Springfield ingress")
        plan.orbit(*tarcap_track, "Eagle TARCAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco tanker")
        plan.threat(
            sa6_pos, radius=12_000.0, label="SA-6", icon=StandardIcon.AirDefense
        )
        plan.threat(
            shorad_pos, radius=6_000.0, label="SA-13", icon=StandardIcon.AirDefense
        )
        for pos in ewr_positions:
            plan.threat(pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)
        plan.threat(
            reserve_origin,
            radius=3_000.0,
            label="Reserve (Aleppo rd)",
            icon=StandardIcon.Mechanized,
        )

    # -- triggers and briefing ----------------------------------------------

    def _add_intro_voice(self, m: Mission) -> None:
        """Mission-start AWACS picture + support tap call (TriggerStart).

        Fires the moment the mission loads. Gives the player the picture,
        names the threat, and reminds them of tanker frequency / TACAN so
        they don't have to dig into the kneeboard during taxi.
        """
        intro = triggers.TriggerStart(comment="Magic mission-start picture")
        call = (
            "Springfield, Magic on station. Picture: clean cold east, "
            "MiG-29S parked at Bassel Al-Assad. SA-6 search radar lit "
            "north of Kuweires. Texaco on tap, 270.0, TACAN 10X."
        )
        intro.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=25)
        )
        self._voice.attach_to_coalition(m, intro, call, coalition="blue")
        m.triggerrules.triggers.append(intro)

    def _add_support_checkins(self, m: Mission) -> None:
        """Staged support check-ins across the early sortie (TimeAfter)."""
        self._add_checkin(
            m,
            seconds=180,
            comment="Texaco check-in",
            call="Springfield, Texaco established overhead, 6500 feet, "
            "ready for receivers. 270.0, TACAN 10X.",
        )
        self._add_checkin(
            m,
            seconds=360,
            comment="Eagle TARCAP on station",
            call="Magic, Eagle TARCAP on station east of the AO, fuel state plus one.",
        )
        self._add_checkin(
            m,
            seconds=540,
            comment="Hawg holding",
            call="Magic, Hawg holding west of the AO, ready for SAM safe "
            "call from Springfield.",
        )

    def _add_checkin(
        self, m: Mission, *, seconds: int, comment: str, call: str
    ) -> None:
        """Wire a single TimeAfter coalition voice call."""
        rule = triggers.TriggerOnce(comment=comment)
        rule.add_condition(condition.TimeAfter(seconds=seconds))
        rule.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=15)
        )
        self._voice.attach_to_coalition(m, rule, call, coalition="blue")
        m.triggerrules.triggers.append(rule)

    def _add_layered_triggers(
        self,
        m: Mission,
        *,
        sa6_radar,
        sa6_tels,
        depot,
        hog,
        migs,
        reserve,
    ) -> None:
        """Wire the four-phase objective chain with announce-on-fire messages.

        Phase gates (objective layering):
          1. SA-6 1S91 radar dead → "SAM safe, Hawg cleared to push".
          2. Depot dead → activate MiG-29S scramble + reserve convoy, both
             announce themselves.
          3. Mission success when SA-6 radar + depot + MiGs are all down.
          4. Failure if Hawg dies while depot still up, or reserve reaches
             the depot anchor (handled implicitly: reserve OnRoad waypoint
             ends at Kuweires).
        """
        self._add_sa6_radar_down_trigger(m, sa6_radar=sa6_radar)
        self._add_depot_killed_triggers(m, depot=depot, migs=migs, reserve=reserve)
        self._add_end_triggers(
            m,
            sa6_radar=sa6_radar,
            sa6_tels=sa6_tels,
            depot=depot,
            hog=hog,
            migs=migs,
        )

    def _add_sa6_radar_down_trigger(self, m: Mission, *, sa6_radar) -> None:
        """Announce SAM-safe and effectively clear Hawg's run."""
        rule = triggers.TriggerOnce(comment="SA-6 search radar destroyed")
        rule.add_condition(condition.GroupDead(sa6_radar.id))
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

    def _add_end_triggers(
        self, m: Mission, *, sa6_radar, sa6_tels, depot, hog, migs
    ) -> None:
        """Success when primary + secondary objectives complete; failure on Hawg loss."""
        success = triggers.TriggerOnce(comment="All objectives met")
        success.add_condition(condition.GroupDead(sa6_radar.id))
        success.add_condition(condition.GroupDead(depot.id))
        success.add_condition(condition.GroupDead(migs.id))
        success_call = (
            "Magic: all primary and secondary objectives complete. "
            "Springfield, RTB Incirlik. Texaco available north for fuel."
        )
        success.add_action(action.MessageToAll(m.string(success_call), seconds=25))
        self._voice.attach_to_all(m, success, success_call)
        m.triggerrules.triggers.append(success)

        partial = triggers.TriggerOnce(comment="Primary objectives met")
        partial.add_condition(condition.GroupDead(sa6_radar.id))
        partial.add_condition(condition.GroupDead(depot.id))
        partial.add_condition(condition.GroupDead(sa6_tels.id))
        partial_call = (
            "Magic: primary targets struck. Mop up the MiGs and the reserve, then RTB."
        )
        partial.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue,
                m.string(partial_call),
                seconds=20,
            )
        )
        self._voice.attach_to_coalition(m, partial, partial_call, coalition="blue")
        m.triggerrules.triggers.append(partial)

        failure = triggers.TriggerOnce(comment="Strike package down")
        failure.add_condition(condition.GroupDead(hog.id))
        failure.add_condition(condition.GroupAlive(depot.id))
        failure_call = (
            "Hawg flight is down before the depot was hit. "
            "Mission failed. Springfield, RTB Incirlik."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=20))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

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
            "Push the armored reserve from the Aleppo road on cue."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Syria 'Eastern Shield' SEAD/escort mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/eastern_shield"),
        help="Output directory for the .miz and README.md (default: out/eastern_shield)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Springfield flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = EasternShield(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
