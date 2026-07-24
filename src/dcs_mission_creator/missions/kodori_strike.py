"""Caucasus 'Kodori Strike' — F-16C mixed package strike on a Russian FOB.

Player flies a USAF F-16C-50 out of Kutaisi as `Dodge`, lead element of a
strike package hitting a Russian forward operating base in the Kodori valley
northeast of Sukhumi-Babushara. `Weasel` (F-16C SEAD) rolls back an SA-6
site placed on a ridge with LOS to the FOB. `Eagle` (F-15C high cover)
holds an overlay-placed CAP station between Kutaisi and Gudauta, ready for
the Russian Su-27 CAP launched on intrusion. `Magic` AWACS and `Texaco`
tanker sit on overlay-placed race-tracks opposite the threat axis.

All ground placements (FOB road snap, SA-6 ridge, SA-13 hilltop overwatch,
55G6 EWR) and the player's ingress corridor come from the `map_overlay`
tactical-scene helpers — not hand-tuned cardinal offsets.

Composition (difficulty: trained):
  - 2x Russian Su-27, Skill.High, R-27/R-77 class, launched on intrusion trigger.
  - Russian FOB: 2x T-72B, 4x BTR-80, 2x KAMAZ supply, 1x ZSU-23-4 Shilka,
    snapped onto a road in the Kodori valley.
  - SA-6 site: 1x Kub 1S91 radar + 2x Kub 2P25 launchers on prominent
    terrain with LOS to the FOB (Skill.High).
  - 2x SA-13 (Strela-10M3) hilltop SHORAD covering the southern approach
    to the FOB.
  - 1x 55G6 EWR on prominent ground in the Russian rear, feeding GCI.
  - USA support: E-3A `Magic` + KC-135 `Texaco` on overlay-placed race-tracks
    behind Kutaisi. F-15C `Eagle` 2-ship on a CAP station forward toward Gudauta.
  - Weather: late-spring clear morning, light W wind, 22 C.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from dcs import action, condition, planes, task, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.placement import (
    FOREST_BUFFER_M as _FOREST_BUFFER_M,
    NO_FOREST as _NO_FOREST,
    find_clear_spot,
    load_scene,
    snap_units_clear,
)
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.map_overlay.placement import Placement
from dcs_mission_creator.map_overlay.scene import TacticalScene


def _offset(
    origin: Point, terrain: Caucasus, *, east_m: float = 0, north_m: float = 0
) -> Point:
    """Return a point offset from `origin` in DCS world meters (east/north)."""
    return Point(origin.x + north_m, origin.y + east_m, terrain)


def _heading_deg(a: Point, b: Point) -> float:
    """Compass heading from a to b (0=N, 90=E). Caucasus uses x=north, y=east."""
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0


def _distance_m(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


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
    """Resolved airports + AO + overlay handle used by every spawn step."""

    kutaisi: Airport
    sukhumi: Airport
    gudauta: Airport
    senaki: Airport
    ao_center: Point
    overlay: TacticalScene


class KodoriStrike(MissionBuilder):
    name = "kodori_strike"
    title = "Kodori Strike"

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""KODORI STRIKE — Caucasus, 20 May 2026, 10:00 local
========================================================
SITUATION
  Russian forces have staged a forward operating base
  in the Kodori valley NE of Sukhumi-Babushara: armour,
  supply trucks, organic AAA. An SA-6 (Kub) site sits on
  a ridge with LOS to the FOB, and SA-13 SHORAD covers
  the southern approach from hilltops. EWR feed out of
  the Russian rear is vectoring Su-27s from Gudauta
  against any package crossing the Inguri.

MISSION (Dodge — F-16C-50, Kutaisi)
  Lead the strike on the FOB. Weasel rolls back the SA-6
  ahead of you. Eagle holds a CAP station between Kutaisi
  and Gudauta and handles the Su-27 intercept. Push along
  the terrain-masked ingress corridor, tank pre-strike if
  needed, work the target box, RTB Kutaisi.

PACKAGE
  Dodge 1 (you): F-16C-50, Kutaisi, hot ramp, strike.
  Weasel 1-2   : F-16C-50 SEAD, Kutaisi, hunting SA-6.
  Eagle 1-2    : F-15C high cover, overlay CAP station.
  Magic        : E-3A AWACS, 251.000 AM, overlay track.
  Texaco       : KC-135, 252.000 AM, TACAN 10Y, overlay track.

THREATS
  Air : 2x Su-27, Skill High, R-27/R-77 class. Russian
        EWR (55G6) vectoring them from prominent ground.
  SAM : 1x SA-6 (Kub) site on a ridge with LOS to the FOB.
        2x SA-13 SHORAD on hilltops covering the southern
        approach. Stay above 4500 m AGL in the target box
        until Weasel calls SA-6 dead.
  AAA : ZSU-23-4 Shilka organic to the FOB.

ROE / FRAGS
  - Cleared to engage Russian aircraft entering the AO.
  - Hold ordnance until Weasel reports SA-6 down or the
    FOB is the only viable target.
  - Bingo fuel: 3000 lb. RTB Kutaisi (divert: Senaki).

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  AO center         : Kodori valley, ~22 km NE Sukhumi.
  PUSH waypoint     : 18 km NW of Kutaisi.
  Ingress           : terrain-masked corridor, overlay-routed
                      to break LOS to the SA-6, EWR, and Gudauta.

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
**Players:** {self.players} coop slot(s)
**Difficulty:** trained
**Expected sortie length:** ~75 minutes

## Situation

Russian forces have staged a forward operating base in the Kodori valley
northeast of Sukhumi-Babushara: armour, supply trucks, organic AAA. An SA-6
(Kub) site sits on a ridge with LOS to the FOB, and SA-13 SHORAD covers the
southern approach from hilltops. EWR coverage out of the Russian rear is
vectoring Su-27s from Gudauta against any USAF package crossing the Inguri.

## Mission

Lead the strike on the FOB as `Dodge` flight. `Weasel` rolls back the SA-6
ahead of the strike, `Eagle` holds a CAP station between Kutaisi and Gudauta
and handles the Russian Su-27 intercept. Push along the terrain-masked
ingress corridor, tank pre-strike if needed, work the target box, RTB
Kutaisi.

## Package

| Callsign  | Type     | Base    | Role                   |
|-----------|----------|---------|------------------------|
| Dodge     | F-16C-50 | Kutaisi | Player strike lead     |
| Weasel 1-2| F-16C-50 | Kutaisi | SEAD on SA-6           |
| Eagle 1-2 | F-15C    | Kutaisi | High cover CAP         |
| Magic     | E-3A     | Kutaisi | AWACS, 251.000 AM      |
| Texaco    | KC-135   | Kutaisi | Tanker, 252.000 AM 10Y |

75-minute sortie is well past F-16C internal endurance, so the tanker is
mandatory — top off pre-strike on the way in, again post-strike if needed.

All ground placements and the player's ingress corridor are picked from the
Caucasus terrain overlay (road snap, ridges, LOS masking) — they shift each
run, so re-brief any visual references off the kneeboard, not memory.

## Threats

- **Air:** 2x Russian Su-27 (Skill High), R-27/R-77 class, launched on
  intrusion trigger and vectored by Russian GCI from a 55G6 EWR on
  prominent ground in the rear.
- **SAM:** 1x SA-6 (Kub) site — 1x 1S91 search/track radar + 2x 2P25
  launchers — on a ridge with LOS to the FOB. 2x SA-13 SHORAD on hilltops
  covering the southern approach. Stay above 4500 m AGL in the target box
  until `Weasel` calls SA-6 dead.
- **AAA:** 1x ZSU-23-4 Shilka organic to the FOB.
- **EWR:** 1x 55G6 on commanding ground in the Russian rear.

## ROE

- Cleared to engage any Russian aircraft entering the AO.
- Hold ordnance until `Weasel` reports SA-6 down or the FOB is the only
  viable target.
- Bingo fuel: 3000 lb. RTB Kutaisi (divert: Senaki-Kolkhi).

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- AO center: ~22 km NE of Sukhumi-Babushara, in the Kodori valley.
- PUSH waypoint: 18 km NW of Kutaisi.
- Ingress: terrain-masked corridor, overlay-routed to break LOS to the
  SA-6, the EWR, and the Gudauta CAP base.
- TANK orbit: overlay-placed standoff track behind Kutaisi.

## Frequencies

- Magic AWACS: 251.000 AM
- Texaco tanker: 252.000 AM, TACAN 10Y
- Kutaisi tower: per kneeboard

## Weather

Late-spring clear morning, light W wind, 22 °C. QNH 760 mmHg. Visibility
80 km. Thin scattered layer at 3000 m, 400 m thick.

## Win / loss conditions

- **Success:** the Russian FOB is destroyed.
- **Failure:** `Weasel` is destroyed while the FOB is still standing
  (SEAD support gone, strike too risky).

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

        fob, sa6, sa6_pos, sa13_positions, ewr_pos = self._spawn_red_ground(
            m, russia, scene
        )
        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        weasel = self._spawn_sead(m, usa, scene, sa6_pos=sa6_pos)
        escort_track = self._spawn_escort(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            threats=(sa6_pos, ewr_pos, scene.gudauta.position, *sa13_positions),
        )

        self._add_end_triggers(m, fob=fob, sa6=sa6, weasel=weasel)
        self._draw_plan(
            m,
            scene,
            fob=fob,
            sa6_pos=sa6_pos,
            sa13_positions=sa13_positions,
            ewr_pos=ewr_pos,
            corridor=corridor,
            escort_track=escort_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
        )
        self._add_briefing(m)

        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """10:00 local on 20 May 2026 (Caucasus is UTC+4)."""
        m.start_time = datetime(2026, 5, 20, 6, 0, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Late-spring clear morning, light W wind, 22 C, 80 km visibility."""
        w = m.weather
        w.season_temperature = 22.0
        w.qnh = 760
        w.wind_at_ground.direction = 270
        w.wind_at_ground.speed = 3
        w.wind_at_2000.direction = 270
        w.wind_at_2000.speed = 6
        w.wind_at_8000.direction = 260
        w.wind_at_8000.speed = 11
        w.clouds_base = 3000
        w.clouds_thickness = 400
        w.clouds_density = 2
        w.visibility_distance = 80000
        w.name = "Late spring clear"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Kutaisi for blue, Sukhumi/Gudauta for red, derive AO + overlay.

        AO seed is the upper Kodori valley NE of Sukhumi. The seed itself often
        lands in dense canopy, biasing every downstream search forest-heavy;
        we snap it to the nearest road-adjacent clearing so the FOB, SA-6 and
        SA-13 placements have a chance to actually land in the open.
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
        ao_seed = _offset(sukhumi.position, t, east_m=22_000, north_m=12_000)
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
        """FOB platoon + SA-6 ridge + SA-13 hilltop overwatch + rear EWR."""
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
        _set_skill(fob, Skill.Average)
        snap_units_clear(scene.overlay.overlay, self._terrain, fob)
        return fob

    def _spawn_red_sa6(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 (Kub) site on a clear ridge with LOS to the FOB; threats from south.

        Threats come from the south (Kutaisi). The SAM sits in a ±90° arc
        toward that axis, on prominent ground with LOS to the FOB, and
        explicitly out of light/dense forest (+ a forest edge buffer) so the
        radar isn't trying to track through canopy. Envelope and prominence
        are relaxed in stages — Kodori is high mountain terrain so the first
        attempt often fails. Final fallback delegates to `find_clear_spot` so we
        never settle on a deep-canopy cell.
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
                    min_distance_to=((ao, 1_500.0),),
                ),
            )
        sa6_types = [
            vehicles.AirDefence.Kub_1S91_str,
            vehicles.AirDefence.Kub_2P25_ln,
            vehicles.AirDefence.Kub_2P25_ln,
        ]
        sa6 = m.vehicle_group_platoon(
            russia,
            "SAM Kodori-6",
            cast(list[type[VehicleType]], sa6_types),
            position=sa6_pos,
            heading=180,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(sa6, Skill.High)
        snap_units_clear(scene.overlay.overlay, self._terrain, sa6)
        return sa6, sa6_pos

    def _spawn_red_shorad(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[Point]:
        """2x SA-13 on clear hilltops covering the southern approach to the FOB.

        Each spot must be on prominent ground with LOS to the approach axis
        and explicitly out of forest (with edge buffer) — Strela-10M3 optics
        and seeker won't see through canopy. Two-pass search; relaxes
        prominence on the second pass. Per-shooter fallback delegates to
        `find_clear_spot` so a missed hilltop pick never drops the launcher into
        canopy.
        """
        t = self._terrain
        approach_anchor = _offset(scene.ao_center, t, east_m=6_000, north_m=-15_000)
        anchors = [approach_anchor, scene.ao_center]
        placed: list[Point] = []
        for i in range(2):
            anchor = anchors[i % len(anchors)]
            for prominence in (40.0, 20.0):
                require = Placement(
                    max_slope_deg=20,
                    not_in=_NO_FOREST,
                    forest_buffer_m=_FOREST_BUFFER_M,
                    not_in_built_up=True,
                    min_relative_height_m=prominence,
                    relative_height_radius_m=2_000.0,
                    line_of_sight_to=(anchor,),
                    min_distance_to=tuple((p, 2_000.0) for p in placed),
                )
                spots = scene.overlay.overlay.find_placement(
                    anchor, radius_m=6_000.0, require=require
                )
                if spots:
                    placed.append(spots[0])
                    break
            else:
                placed.append(
                    find_clear_spot(
                        scene.overlay.overlay,
                        anchor,
                        t,
                        radius_m=6_000.0,
                        require=Placement(
                            max_slope_deg=25,
                            not_in=_NO_FOREST,
                            forest_buffer_m=_FOREST_BUFFER_M,
                            not_in_built_up=True,
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
            _set_skill(grp, Skill.High)
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
        rear_anchor = _offset(scene.sukhumi.position, t, east_m=12_000, north_m=-6_000)
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
        _set_skill(ewr, Skill.High)
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
            speed=460,
            altitude=7500,
            max_engage_distance=90_000,
            group_size=2,
        )
        _set_skill(boris, Skill.High)
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
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.kutaisi,
            position=p1,
            race_distance=int(_distance_m(p1, p2)),
            heading=int(_heading_deg(p1, p2)),
            altitude=8500,
            speed=410,
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
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.kutaisi,
            position=p1,
            race_distance=int(_distance_m(p1, p2)),
            heading=int(_heading_deg(p1, p2)),
            altitude=4500,
            speed=380,
            start_type=StartType.Warm,
            frequency=252,
            tacanchannel="10Y",
        )
        return p1, p2

    def _spawn_sead(self, m: Mission, usa: Country, scene: _Scene, *, sa6_pos: Point):
        """F-16C Weasel 2-ship from Kutaisi, fragged on the placed SA-6 site."""
        weasel = m.sead_flight(
            usa,
            "Weasel",
            planes.F_16C_50,
            target_pos=sa6_pos,
            airport=scene.kutaisi,
            start_type=StartType.Warm,
            max_engage_distance=40_000,
            group_size=2,
        )
        _set_skill(weasel, Skill.High)
        return weasel

    def _spawn_escort(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C 2-ship Eagle on an overlay CAP station forward toward Gudauta."""
        threat_bearing = _heading_deg(scene.kutaisi.position, scene.gudauta.position)
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
            speed=420,
            altitude=7500,
            max_engage_distance=90_000,
            group_size=2,
        )
        _set_skill(eagle, Skill.High)
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
        """
        t = self._terrain
        player = m.flight_group_from_airport(
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.kutaisi,
            maintask=task.CAS,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        _mark_clients(player)
        player.add_runway_waypoint(scene.kutaisi)
        push = _offset(scene.kutaisi.position, t, east_m=-15_000, north_m=12_000)
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=scene.ao_center,
            threats=threats,
            waypoints=3,
            leg_search_radius_m=6_000.0,
        )
        for i, pt in enumerate(corridor):
            if i == 0:
                name = "PUSH"
            elif i == len(corridor) - 1:
                name = "TGT"
            else:
                name = f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=6500, speed=400, name=name)
        egress = _offset(scene.ao_center, t, east_m=20_000, north_m=-15_000)
        player.add_waypoint(egress, altitude=6500, speed=420, name="EGRESS")
        player.add_runway_waypoint(scene.kutaisi)
        player.land_at(scene.kutaisi)
        return [*corridor, egress]

    # -- F10 map briefing ---------------------------------------------------

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        fob,
        sa6_pos: Point,
        sa13_positions: list[Point],
        ewr_pos: Point,
        corridor: list[Point],
        escort_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
    ) -> None:
        """Paint the plan on the F10 map (trained: coarse, estimated threats)."""
        plan = PlanOverlay(m, "trained")
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
        plan.threat(
            sa6_pos, radius=10_000.0, label="SA-6", icon=StandardIcon.AirDefense
        )
        for pos in sa13_positions:
            plan.threat(
                pos, radius=6_000.0, label="SA-13", icon=StandardIcon.AirDefense
            )
        plan.threat(ewr_pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(self, m: Mission, *, fob, sa6, weasel) -> None:
        """Success when FOB dead; failure when Weasel dies while FOB lives."""
        success = triggers.TriggerOnce(comment="Strike successful")
        success.add_condition(condition.GroupDead(fob.id))
        success_call = (
            "Russian FOB destroyed. Mission successful. Dodge, return to base, Kutaisi."
        )
        success.add_action(action.MessageToAll(m.string(success_call), seconds=20))
        self._voice.attach_to_all(m, success, success_call)
        m.triggerrules.triggers.append(success)

        sead_done = triggers.TriggerOnce(comment="SEAD complete")
        sead_done.add_condition(condition.GroupDead(sa6.id))
        sead_call = "Weasel, magnum splash. SA-6 site is cold. Dodge, target box open."
        sead_done.add_action(action.MessageToAll(m.string(sead_call), seconds=15))
        self._voice.attach_to_all(m, sead_done, sead_call)
        m.triggerrules.triggers.append(sead_done)

        failure = triggers.TriggerOnce(comment="Strike failed")
        failure.add_condition(condition.GroupDead(weasel.id))
        failure.add_condition(condition.GroupAlive(fob.id))
        failure_call = (
            "Weasel is down with the FOB still standing. Mission failed. "
            "Dodge, return to base, Kutaisi."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=20))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Lead the strike on the Russian FOB in the Kodori valley. Weasel "
            "rolls back the SA-6 site; Eagle holds a CAP station between "
            "Kutaisi and Gudauta and handles the Su-27 intercept. RTB Kutaisi."
        )
        m.set_description_redtask_text(
            "Hold the FOB in the Kodori valley. SA-6 / SA-13 cover the target "
            "box; Su-27s out of Gudauta scramble on USAF intrusion."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Caucasus 'Kodori Strike' mixed mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/kodori_strike"),
        help="Output directory for the .miz and README.md (default: out/kodori_strike)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Dodge flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = KodoriStrike(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
