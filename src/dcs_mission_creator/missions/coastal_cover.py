"""Caucasus 'Coastal Cover' — F-16C mix mission (CAP + strike escort).

Player flies a USAF F-16C-50 out of Batumi, escorts an AI A-10C strike on a
Russian armoured convoy north of Senaki, and is expected to handle a 2-ship
Russian MiG-29S intercept out of Sukhumi-Babushara. AWACS Magic provides EWR;
no tanker (50-minute sortie is inside F-16C internal fuel + 10 min margin).

Composition (difficulty: trained):
  - 2x Russian MiG-29S, Skill.High, R-77/R-27 class, launched on intrusion trigger.
  - Russian armoured convoy: 4x BTR-80, 2x T-72B, 1x ZSU-23-4 Shilka, on a
    snap-on-road route from the Inguri valley toward Senaki outskirts.
  - 2x ZSU-23-4 AAA overwatch on hilltops covering the convoy axis.
  - 2x T-72B + 2x BTR-80 counterattack reserve, concealed behind the convoy.
  - 1x SA-13 (Strela-10M3) covering the convoy from a hilltop.
  - 2x 55G6 EWR chain along Russian frontier for layered GCI vectoring.
  - USA support: E-3A 'Magic' on an overlay-placed Black Sea track.
  - Weather: spring scattered cumulus, light NW wind, 18 C.
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
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import mark_clients, offset, set_skill
from dcs_mission_creator.core.placement import (
    load_scene,
    sam_site_on_ridge,
)
from dcs_mission_creator.core.tasking import apply_ai_difficulty
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene


@dataclass
class _Scene:
    """Resolved airports + AO center + map overlay used by every spawn step."""

    batumi: Airport
    kutaisi: Airport
    sukhumi: Airport
    senaki: Airport
    ao_center: Point
    overlay: TacticalScene


class CoastalCover(MissionBuilder):
    name = "coastal_cover"
    title = "Coastal Cover"
    difficulty = Difficulty.TRAINED

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""COASTAL COVER — Caucasus, 15 May 2026, 10:00 local
========================================================
SITUATION
  A Reaper feed at first light watched a Russian
  mechanised column form up in the Inguri valley and
  start south on the valley road toward Senaki. USAF
  A-10s (Hawg 1-2) out of Kutaisi are fragged against it.
  Russian fighters at Sukhumi-Babushara went to alert on
  the same warning — expect them airborne once we commit.

MISSION (Dodge — F-16C-50, Batumi)
  Push north along the terrain-masked ingress corridor,
  station over the AO, sanitize the airspace ahead of
  Hawg's run, and engage Russian fighters before they
  get a shot on the strike.

PACKAGE
  Dodge 1 (you): F-16C-50, Batumi, hot ramp.
  Hawg 1-2     : A-10C, Kutaisi, strike on the column.
  Eagle 1-2    : F-15C CAP, race-track toward Sukhumi.
  Magic        : E-3A AWACS, 251.000 AM, Black Sea track.

INTELLIGENCE
  Air : Sukhumi-Babushara holds a MiG-29S pair on alert,
        current generation missiles. A Rivet Joint track
        overnight fixed early-warning radars along the
        frontier — they will see you coming and vector.
  SAM : The Reaper feed showed a tracked SHORAD launcher
        moving onto high ground overlooking the road.
        SA-13 class, IR, short reach. Stay above 4000 m
        AGL over the target box and it cannot touch you.
  AAA : Gun vehicles ride with the column, and the same
        imagery showed dug-in guns on the hills either
        side of the valley road.
  Land: Partner-force reporting puts a small armoured
        reserve laagered in the treeline behind the
        column, held back to push through if the lead
        elements are hit. Unconfirmed.

ROE / FRAGS
  - Hold fire on civilian/neutral contacts.
  - Cleared to engage any Russian aircraft entering the AO.
  - Do not overfly the convoy below 4000 m AGL.
  - Bingo fuel: 2500 lb. RTB Batumi (divert: Kutaisi).

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  AO center         : ~18 km north-northeast of Senaki.
  PUSH waypoint     : 25 km north of Batumi (corridor IP).

FREQUENCIES
  Magic AWACS   : 251.000 AM
  Batumi tower  : per kneeboard
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Coastal Cover

**Theater:** Caucasus
**Date / time:** 15 May 2026, 10:00 local
**Player aircraft:** F-16C-50 (`Dodge`), Batumi, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** trained — experienced MiG-29S pair, current-generation
missiles, GCI vectoring, SHORAD over the target, AWACS support, no tanker
**Expected sortie length:** ~50 minutes

## Situation

A Reaper feed at first light watched a Russian mechanised column form up in
the Inguri valley and start south on the valley road toward Senaki. USAF
A-10s (`Hawg 1-2`) out of Kutaisi are fragged against it. Russian fighters at
Sukhumi-Babushara went to alert on the same warning — expect them airborne
once the package commits.

## Mission

Push north as `Dodge` flight along a terrain-masked ingress corridor, take
station over the AO, sanitize the airspace ahead of `Hawg`'s run, and engage
Russian fighters before they get a shot on the strike.

## Package

| Callsign | Type     | Base    | Role                         |
|----------|----------|---------|------------------------------|
| Dodge    | F-16C-50 | Batumi  | Player CAP / escort          |
| Hawg 1-2 | A-10C    | Kutaisi | Strike on convoy lead        |
| Eagle 1-2| F-15C    | Batumi  | High cover CAP (overlay)     |
| Magic    | E-3A     | Batumi  | AWACS, Black Sea track       |

No tanker — F-16C internal fuel covers the sortie with a ~10 min margin.

## Intelligence

- **Air:** Sukhumi-Babushara holds a MiG-29S pair on alert, current-generation
  missiles, flown by an experienced crew. They will come once we are committed
  over the valley.
- **EWR:** A Rivet Joint track overnight fixed early-warning radars along the
  Russian frontier. Assume the pair is vectored onto you from the moment you
  cross the coast.
- **SAM:** The Reaper feed showed a tracked SHORAD launcher moving onto high
  ground overlooking the road — SA-13 class, IR-guided, short reach. Stay
  above 4000 m AGL over the target box and it cannot reach you.
- **AAA:** Gun vehicles ride with the column, and the same imagery showed
  dug-in guns on the hills either side of the valley road.
- **Land reserve:** Partner-force reporting puts a small armoured reserve
  laagered in the treeline behind the column, held back to push through if the
  lead elements are hit. Unconfirmed.

## ROE

- Hold fire on civilian / neutral contacts.
- Cleared to engage any Russian aircraft entering the AO.
- Do not overfly the convoy below 4000 m AGL.
- Bingo fuel: 2500 lb. RTB Batumi (divert: Kutaisi).

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- AO center: ~18 km north-northeast of Senaki.
- PUSH waypoint: 25 km north of Batumi (corridor IP).
- Your route is a terrain-masked corridor that keeps ridgelines between you
  and the reported launcher and radar positions for as long as it can.

## Frequencies

- Magic AWACS: 251.000 AM
- Batumi tower: per kneeboard

## Weather

Spring scattered cumulus, light NW wind, 18 °C. QNH 760 mmHg. Visibility
80 km. Scattered layer at 2400 m, 600 m thick.

## Win / loss conditions

- **Success:** the Russian column is broken up on the valley road and never
  reaches Senaki.
- **Failure:** `Hawg` is shot down with the column still rolling.

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
        self._scene = scene
        usa, russia = m.country("USA"), m.country("Russia")

        convoy, sa13_pos, ewr_positions = self._spawn_red_ground(m, russia, scene)
        awacs_track = self._spawn_awacs(m, usa, scene)
        hog = self._spawn_strike(m, usa, scene, target_unit=convoy.units[4])
        cap_track = self._spawn_cap(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        corridor = self._spawn_player(m, usa, scene, threats=(sa13_pos, *ewr_positions))

        self._add_end_triggers(m, convoy=convoy, hog=hog)
        if self._reserve is not None:
            self._add_reserve_trigger(m, convoy=convoy, reserve=self._reserve)
        self._conceal_red(russia)
        self._draw_plan(
            m,
            scene,
            convoy=convoy,
            sa13_pos=sa13_pos,
            ewr_positions=ewr_positions,
            corridor=corridor,
            cap_track=cap_track,
            awacs_track=awacs_track,
        )
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """10:00 map-local on 15 May 2026 — the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Spring scattered cumulus, light NW wind, 18 C, 80 km visibility."""
        w = m.weather
        w.season_temperature = 18.0
        w.qnh = 760
        w.wind_at_ground.direction = 300
        w.wind_at_ground.speed = 4
        w.wind_at_2000.direction = 290
        w.wind_at_2000.speed = 7
        w.wind_at_8000.direction = 280
        w.wind_at_8000.speed = 12
        w.clouds_base = 2400
        w.clouds_thickness = 600
        w.clouds_density = 4
        w.visibility_distance = 80000
        w.name = "Spring scattered"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Batumi/Kutaisi for blue, Sukhumi for red, derive AO center."""
        t = self._terrain
        batumi = t.airports["Batumi"]
        kutaisi = t.airports["Kutaisi"]
        sukhumi = t.airports["Sukhumi-Babushara"]
        senaki = t.airports["Senaki-Kolkhi"]
        batumi.set_blue()
        kutaisi.set_blue()
        sukhumi.set_red()
        ao_center = offset(senaki.position, east_m=4_000, north_m=18_000)
        overlay = load_scene("caucasus")
        return _Scene(batumi, kutaisi, sukhumi, senaki, ao_center, overlay)

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """Convoy on road + SA-13 SHORAD + AAA overwatch + reserve + EWR chain."""
        convoy = self._spawn_red_convoy(m, russia, scene)
        sa13_pos = self._spawn_red_shorad(m, russia, scene.ao_center)
        self._spawn_red_aaa_overwatch(m, russia)
        self._reserve = self._spawn_red_reserve(m, russia)
        ewr_positions = self._spawn_red_ewr_chain(m, russia, scene)
        return convoy, sa13_pos, ewr_positions

    def _spawn_red_convoy(self, m: Mission, russia: Country, scene: _Scene):
        """Snap-on-road convoy route from Inguri valley to Senaki outskirts.

        `place_convoy_route` snaps origin and destination to the nearest real
        road; the DCS engine paths the platoon between them. Spawn point is
        the snapped origin; the spawn waypoint *and* the destination waypoint
        are OnRoad, so the column follows the valley road all the way to
        Senaki instead of cutting cross-country.
        """
        origin = offset(scene.senaki.position, east_m=2_000, north_m=22_000)
        destination = offset(scene.senaki.position, east_m=-1_000, north_m=4_000)
        route = scene.overlay.place_convoy_route(origin, destination)
        self._convoy_route = route
        spawn = route.waypoints[0]
        heading = int(spawn.heading_between_point(route.waypoints[-1]))
        convoy_types = [
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
            vehicles.Armor.T_72B,
            vehicles.Armor.T_72B,
            vehicles.AirDefence.ZSU_23_4_Shilka,
        ]
        convoy = m.vehicle_group_platoon(
            russia,
            "Convoy Bear",
            cast(list[type[VehicleType]], convoy_types),
            position=spawn,
            heading=heading,
            move_formation=PointAction.OnRoad,
        )
        convoy.add_waypoint(
            route.waypoints[-1],
            move_formation=PointAction.OnRoad,
            speed=40,
        )
        set_skill(convoy, Skill.Average)
        return convoy

    def _spawn_red_shorad(self, m: Mission, russia: Country, ao_center: Point) -> Point:
        """2x Strela-10M3 (SA-13) on prominent terrain with LOS to the convoy.

        Threat comes from the west. Returns the placement so the player's
        ingress corridor can avoid LOS to it.
        """
        ridge = sam_site_on_ridge(
            self._scene.overlay,
            defends=ao_center,
            threat_axis_deg=270.0,
            envelope_radius_m=8_000.0,
            min_prominence_m=20.0,
        )
        sa13 = m.vehicle_group(
            russia,
            "SAM Bear-13",
            vehicles.AirDefence.Strela_10M3,
            position=ridge,
            heading=270,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        set_skill(sa13, Skill.High)
        return ridge

    def _spawn_red_aaa_overwatch(self, m: Mission, russia: Country) -> None:
        """2x ZSU-23-4 on hilltops with LOS to the convoy axis."""
        spots = self._scene.overlay.place_aaa_overwatch(
            defended_axis=list(self._convoy_route.waypoints), count=2
        )
        for i, pos in enumerate(spots):
            grp = m.vehicle_group(
                russia,
                f"AAA Bear-{i + 1}",
                vehicles.AirDefence.ZSU_23_4_Shilka,
                position=pos,
                heading=270,
            )
            set_skill(grp, Skill.High)

    def _spawn_red_reserve(self, m: Mission, russia: Country):
        """Counterattack armor (2x T-72B + 2x BTR-80) concealed behind the convoy.

        Late-activated: spawns hidden, dormant. An OnRoad push waypoint toward
        the convoy destination is pre-loaded; the reserve advances only after
        `_add_reserve_trigger` fires `ActivateGroup` (convoy < 50% strength).
        """
        route = self._convoy_route
        flot = route.waypoints[0]
        rear_bearing = route.waypoints[-1].heading_between_point(route.waypoints[0])
        try:
            pos = self._scene.overlay.place_counterattack_reserve(
                flot_point=flot,
                rear_bearing_deg=rear_bearing,
                rear_distance_m=12_000.0,
                search_radius_m=5_000.0,
            )
        except LookupError:
            return None
        reserve_types = [
            vehicles.Armor.T_72B,
            vehicles.Armor.T_72B,
            vehicles.Armor.BTR_80,
            vehicles.Armor.BTR_80,
        ]
        reserve = m.vehicle_group_platoon(
            russia,
            "Reserve Bear",
            cast(list[type[VehicleType]], reserve_types),
            position=pos,
            heading=int((rear_bearing + 180.0) % 360.0),
            move_formation=PointAction.OnRoad,
        )
        reserve.late_activation = True
        push_target = self._scene.overlay.overlay.find_road_spawn(
            route.waypoints[-1], radius_m=4_000
        )
        reserve.add_waypoint(
            push_target,
            move_formation=PointAction.OnRoad,
            speed=35,
        )
        set_skill(reserve, Skill.Average)
        return reserve

    def _spawn_red_ewr_chain(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[Point]:
        """2x 55G6 EWR chain along the Russian frontier (Sukhumi → inland)."""
        frontier = [
            scene.sukhumi.position,
            offset(scene.sukhumi.position, east_m=15_000, north_m=30_000),
        ]
        try:
            positions = scene.overlay.place_ewr_chain(
                frontier_polyline=frontier,
                count=2,
                min_spacing_m=25_000.0,
                min_elevation_m=200.0,
            )
        except LookupError:
            anchor = offset(scene.sukhumi.position, east_m=8_000, north_m=10_000)
            positions = [anchor]
        for i, pos in enumerate(positions):
            grp = m.vehicle_group(
                russia,
                f"EWR Bear-{i + 1}",
                vehicles.AirDefence.X_55G6_EWR,
                position=pos,
                heading=270,
            )
            set_skill(grp, Skill.High)
        return positions

    def _spawn_red_intercept(self, m: Mission, russia: Country, scene: _Scene):
        """2x MiG-29S out of Sukhumi, late-activated by blue intrusion zone."""
        intrusion_zone = m.triggers.add_triggerzone(
            position=scene.ao_center,
            radius=35_000,
            hidden=True,
            name="MIG intrusion",
        )
        boris = m.intercept_flight(
            russia,
            "Boris",
            planes.MiG_29S,
            airport=scene.sukhumi,
            zone=intrusion_zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=450,
            altitude=7000,
            max_engage_distance=90_000,
            group_size=2,
        )
        set_skill(boris, Skill.High)
        apply_ai_difficulty(boris, self.difficulty)
        announce = triggers.TriggerOnce(comment="MiG launch announcement")
        announce.add_condition(
            condition.PartOfCoalitionInZone("blue", intrusion_zone.id)
        )
        mig_call = (
            "Russian MiG-29S airborne from Sukhumi-Babushara, vectoring on the AO."
        )
        announce.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue,
                m.string(mig_call),
                seconds=15,
            )
        )
        self._voice.attach_to_coalition(m, announce, mig_call, coalition="blue")
        m.triggerrules.triggers.append(announce)
        return boris

    # -- blue side ----------------------------------------------------------

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """E-3A Magic on an overlay-placed Black Sea race-track, 251.000 AM."""
        p1, p2 = scene.overlay.place_awacs_track(
            home_base=scene.batumi.position,
            threat_axis=scene.sukhumi.position,
            standoff_m=90_000.0,
            track_length_m=80_000.0,
        )
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.batumi,
            position=p1,
            race_distance=int(p1.distance_to_point(p2)),
            heading=int(p1.heading_between_point(p2)),
            altitude=8500,
            speed=410,
            start_type=StartType.Warm,
            frequency=251,
        )
        return p1, p2

    def _spawn_strike(self, m: Mission, usa: Country, scene: _Scene, *, target_unit):
        """A-10C 2-ship Hawg from Kutaisi, fragged on the convoy."""
        hog = m.strike_flight(
            usa,
            "Hawg",
            planes.A_10C,
            target=target_unit,
            airport=scene.kutaisi,
            start_type=StartType.Warm,
            group_size=2,
        )
        set_skill(hog, Skill.High)
        return hog

    def _spawn_cap(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C 2-ship Eagle on an overlay-placed race-track toward Sukhumi."""
        threat_bearing = scene.batumi.position.heading_between_point(
            scene.sukhumi.position
        )
        p1, p2 = scene.overlay.place_cap_station(
            defended_asset=scene.batumi.position,
            threat_bearing_deg=threat_bearing,
            forward_distance_m=45_000.0,
            track_length_m=40_000.0,
        )
        eagle = m.patrol_flight(
            usa,
            "Eagle",
            planes.F_15C,
            airport=scene.batumi,
            pos1=p1,
            pos2=p2,
            start_type=StartType.Warm,
            speed=420,
            altitude=7500,
            max_engage_distance=80_000,
            group_size=2,
        )
        set_skill(eagle, Skill.High)
        return p1, p2

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Dodge F-16C-50 from Batumi, hot ramp; terrain-masked ingress corridor."""
        player = m.flight_group_from_airport(
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.batumi,
            maintask=task.CAP,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        mark_clients(player)
        player.add_runway_waypoint(scene.batumi)
        push = offset(scene.batumi.position, east_m=5_000, north_m=25_000)
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=scene.ao_center,
            threats=threats,
            waypoints=3,
            leg_search_radius_m=6_000.0,
        )
        for i, pt in enumerate(corridor):
            name = (
                "PUSH"
                if i == 0
                else ("STATION" if i == len(corridor) - 1 else f"INGRESS-{i}")
            )
            player.add_waypoint(pt, altitude=6500, speed=380, name=name)
        player.add_runway_waypoint(scene.batumi)
        player.land_at(scene.batumi)
        return list(corridor)

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        The player's picture of the enemy is the briefing plus what
        `_draw_plan` chooses to show — never a stock unit icon.
        """
        conceal_country(russia)

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        convoy,
        sa13_pos: Point,
        ewr_positions: list[Point],
        corridor: list[Point],
        cap_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
    ) -> None:
        """Paint the plan on the F10 map (trained: coarse, estimated threats)."""
        plan = PlanOverlay(m, self.difficulty)
        plan.objective(scene.ao_center, "AO — convoy axis", radius=6_000.0)
        plan.route(corridor, "Dodge ingress")
        plan.orbit(*cap_track, "Eagle CAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.threat(
            convoy.units[0].position,
            radius=2_500.0,
            label="Convoy",
            icon=StandardIcon.Mechanized,
        )
        plan.threat(
            sa13_pos, radius=8_000.0, label="SA-13", icon=StandardIcon.AirDefense
        )
        for pos in ewr_positions:
            plan.threat(pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)

    # -- triggers and briefing ----------------------------------------------

    def _add_reserve_trigger(self, m: Mission, *, convoy, reserve) -> None:
        """Activate the late-activated reserve when convoy strength drops < 50%.

        On activation the reserve unmasks from the treeline and executes its
        pre-loaded OnRoad waypoint toward the convoy destination.
        """
        rule = triggers.TriggerOnce(comment="Reserve counterattack")
        rule.add_condition(condition.GroupLifeLess(convoy.id, 50))
        rule.add_action(action.ActivateGroup(reserve.id))
        reserve_call = (
            "Magic: Russian armor is breaking out of the treeline behind the "
            "column, pushing south toward Senaki."
        )
        rule.add_action(action.MessageToAll(m.string(reserve_call), seconds=15))
        self._voice.attach_to_all(m, rule, reserve_call)
        m.triggerrules.triggers.append(rule)

    def _add_end_triggers(self, m: Mission, *, convoy, hog) -> None:
        """Success when convoy dead; failure when Hawg dies while convoy lives."""
        success = triggers.TriggerOnce(comment="Strike successful")
        success.add_condition(condition.GroupDead(convoy.id))
        success_call = (
            "Magic: the column is wrecked and off the road, nothing is moving "
            "toward Senaki. Dodge, RTB Batumi."
        )
        success.add_action(action.MessageToAll(m.string(success_call), seconds=20))
        self._voice.attach_to_all(m, success, success_call)
        m.triggerrules.triggers.append(success)

        failure = triggers.TriggerOnce(comment="Strike failed")
        failure.add_condition(condition.GroupDead(hog.id))
        failure.add_condition(condition.GroupAlive(convoy.id))
        failure_call = (
            "Magic: we have lost Hawg and the column is still rolling south. "
            "Nothing more we can do here. Dodge, RTB Batumi."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=20))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Escort Hawg 1-2 onto the Russian convoy north of Senaki and "
            "defeat the MiG-29S intercept out of Sukhumi-Babushara. "
            "RTB Batumi."
        )
        m.set_description_redtask_text(
            "Push the convoy through the Inguri valley toward Senaki. "
            "MiG-29S to intercept any USAF strike package."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Caucasus 'Coastal Cover' mix mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/coastal_cover"),
        help="Output directory for the .miz and README.md (default: out/coastal_cover)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Dodge flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = CoastalCover(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
