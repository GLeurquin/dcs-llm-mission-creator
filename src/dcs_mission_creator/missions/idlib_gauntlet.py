"""Syria 'Idlib Gauntlet' — F-16C convoy interdiction through a layered IADS.

Player flies a USAF F-16C-50 out of Hatay (callsign `Uzi`) against a Syrian
resupply column running north-west from Abu al-Duhur toward Taftanaz. The
column carries its own short-range air defence, and the corridor it drives
through is covered by three overlapping Russian-supplied SAM belts. The
difficulty is not the convoy — it is working inside the missile engagement
zones long enough to kill it before it reaches Taftanaz.

The SAM belts react to HARM fire the way real crews do (see
`core/emcon.py`): the launch goes out over the IADS net, the crew takes a few
seconds to react, the fire-control radar drops emissions, the missile goes for
the last known point, and the site comes back up a minute or two later. HARMs
therefore *suppress* far more often than they *kill*, and the player has to use
the dark window rather than expect a free radar kill.

Composition (difficulty: trained):
  - Syrian convoy `Nasr` (11 vehicles): 3x T-72B, 2x BTR-80, 2x Ural-375,
    plus organic SHORAD — 2x SA-13 Strela-10M3, 1x SA-19 Tunguska,
    1x ZSU-23-4 Shilka. Armor Average, SHORAD High.
  - SAM belt 1 (long): SA-2 site at Abu al-Duhur, Skill Average.
  - SAM belt 2 (medium): SA-6 site on high ground over the convoy route,
    Skill High — the priority SEAD target.
  - SAM belt 3 (short, mobile): SA-8 site covering the Taftanaz off-load,
    Skill High.
  - 2x 55G6 EWR feeding the network and the GCI picture.
  - 2x MiG-29S alert-5 at Bassel Al-Assad, scrambled cold off the ramp once
    the convoy starts taking losses.
  - USAF support: E-3A `Magic`, KC-135 `Texaco` (TACAN 10X), F-15C `Eagle`
    TARCAP, MQ-9 `Hammer` FAC(A) lasing the column, F/A-18C `Pontiac` strike
    pair released once the SA-6 is down or the clock runs out.
  - Weather: late-summer Levant haze, light west wind, 30 C.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

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

from dcs_mission_creator.core import air_defense as ad
from dcs_mission_creator.core.emcon import ArmSite, arm_emcon_reaction
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.placement import (
    convoy_spawn,
    find_clear_spot,
    load_scene,
    sam_site_on_ridge,
)
from dcs_mission_creator.core.tasking import (
    apply_ai_difficulty,
    fac_attack_group,
    scramble_on_trigger,
)
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.map_overlay.scene import TacticalScene

# Flag raised when the F/A-18C strike pair is cleared into the AO.
_FLAG_STRIKE_RELEASE = 10
# Frequencies (MHz) and the FAC(A) laser code, quoted in every briefing view.
_FREQ_AWACS = 251
_FREQ_TANKER = 270
_FREQ_FAC = 133
_LASER_CODE = 1688


def _mark_clients(group: FlyingGroup) -> None:
    """Mark every unit in `group` as a coop client slot."""
    for u in group.units:
        u.skill = Skill.Client


@dataclass
class _Scene:
    """Resolved airports + AO geometry + map overlay used by every spawn step."""

    hatay: Airport
    incirlik: Airport
    abu_al_duhur: Airport
    taftanaz: Airport
    bassel: Airport
    convoy_origin: Point
    convoy_destination: Point
    route_mid: Point
    threat_axis_deg: float
    overlay: TacticalScene


class IdlibGauntlet(MissionBuilder):
    name = "idlib_gauntlet"
    title = "Idlib Gauntlet"
    difficulty = "trained"

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Syria()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""IDLIB GAUNTLET — Syria, 12 September 2026, 08:40 local
=====================================================
SITUATION
  A Syrian resupply column left Abu al-Duhur before
  first light and is running north-west toward the
  Taftanaz off-load. It carries the ammunition for the
  next push on the Idlib pocket, and it travels with
  its own short-range air defence.
  The corridor is covered by three overlapping Russian
  -supplied SAM belts. Their crews are trained: they
  drop emissions when they see an anti-radiation shot
  and come back up a minute or two later. Expect to
  suppress, not to sanitize.

MISSION (Uzi — F-16C-50, Hatay)
  Destroy the convoy before it reaches Taftanaz.
  Suppress whatever belt is holding you off the target.
  Combat-ineffective (70% of the column dead) meets
  the frag; the whole column is the full score.

PACKAGE
  Uzi 1 (you): F-16C-50, Hatay, hot ramp. 2x AGM-88C,
        2x CBU-97 SFW, 2x AIM-120C, 2x AIM-9X, HTS pod,
        LITENING, 300 gal centerline.
  Pontiac 1-2: F/A-18C, 4x GBU-12 + ATFLIR, Hatay. Held
        on the ground until the SA-6 is down or 25
        minutes elapse, then released onto the column.
  Eagle 1-2  : F-15C TARCAP west of the corridor.
  Hammer     : MQ-9, {_FREQ_FAC}.000 AM, FAC(A) over the
        corridor, lasing the column, code {_LASER_CODE}.
  Magic      : E-3A AWACS, {_FREQ_AWACS}.000 AM.
  Texaco     : KC-135, {_FREQ_TANKER}.000 AM, TACAN 10X.

THREATS
  SAM : SA-2 belt at Abu al-Duhur (~40 km), SA-6 belt on
        the high ground over the convoy route (~25 km),
        mobile SA-8 belt at the Taftanaz off-load
        (~10 km). 2x 55G6 EWR feeding the network.
  AAA/SHORAD: organic to the column — 2x SA-13, 1x SA-19
        Tunguska, 1x ZSU-23-4. None of it shuts down for
        a HARM; kill it or stay outside 8 km.
  Air : 2x MiG-29S alert-5 at Bassel Al-Assad, scrambled
        once the column starts taking losses.

ROE / FRAGS
  - Cleared to engage the convoy and any air defence
    covering it.
  - Cleared to engage Syrian and Russian aircraft in
    the corridor.
  - HARM suppresses; a dark site is not a dead site.
    Work the window, do not loiter in the MEZ.
  - Tank from Texaco before the push if the SEAD phase
    runs long. Bingo fuel 3500 lb, RTB Hatay.

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  PUSH        : 25 km southeast of Hatay.
  Convoy axis : Abu al-Duhur -> Taftanaz, north-west.
  Off-load    : Taftanaz. Convoy arrival there = failure.

FREQUENCIES
  Magic AWACS   : {_FREQ_AWACS}.000 AM
  Texaco tanker : {_FREQ_TANKER}.000 AM, TACAN 10X
  Hammer FAC(A) : {_FREQ_FAC}.000 AM, laser {_LASER_CODE}
  Hatay tower   : per kneeboard
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Idlib Gauntlet

**Theater:** Syria
**Date / time:** 12 September 2026, 08:40 local
**Player aircraft:** F-16C-50 (`Uzi`), Hatay, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** trained (medium)
**Expected sortie length:** ~60 minutes

## Situation

A Syrian resupply column left Abu al-Duhur before first light and is running
north-west toward the Taftanaz off-load with the ammunition for the next push
on the Idlib pocket. It travels with its own short-range air defence, and the
corridor it drives through is covered by three overlapping Russian-supplied SAM
belts: an SA-2 belt at Abu al-Duhur, an SA-6 belt on the high ground over the
route, and a mobile SA-8 belt at the off-load — all of it tied together by a
pair of 55G6 early-warning radars.

## Mission

`Uzi` flight destroys the column before it reaches Taftanaz. The convoy is the
objective; the SAM belts are the problem, not the target list. Kill what you
must, suppress the rest, and get weapons onto the trucks.

1. **Push and SEAD.** Ingress from Hatay through the terrain-masked corridor.
   The SA-6 on the ridge is what owns the convoy route — put it down or keep
   it down.
2. **Interdict.** Work the column with the CBU-97s — SFW submunitions are what
   kill a dispersed column in two passes. `Hammer` (MQ-9) is overhead on
   {_FREQ_FAC}.000 AM for the talk-on, lasing code {_LASER_CODE} for `Pontiac`'s GBU-12s.
3. **Strike release.** `Pontiac` (2x F/A-18C) launches once the SA-6's Straight
   Flush radar is destroyed, or 25 minutes into the sortie, whichever comes
   first.
4. **DCA.** 2x MiG-29S scramble off the ramp at Bassel Al-Assad once the column
   has lost about a third of its strength. `Eagle` TARCAP is west of the
   corridor; back them up.

## How the SAMs react to HARM

This mission scripts realistic emissions control for every radar-guided site
(SA-2, SA-6, SA-8 and both EWRs). When an anti-radiation missile is fired:

- the launch is passed over the IADS net to every site within ~60 km;
- each crew independently makes the call — most drop emissions, some do not
  (SA-6 and SA-8 crews react ~85–90% of the time, the SA-2 crew ~70%);
- reaction takes **3–8 seconds**, so a HARM fired from close in still kills;
- the site then sits dark for **60–130 seconds** with radars off and weapons
  hold, and `Magic` calls the shutdown on the radio;
- a second HARM while a site is dark makes that crew stay off the air longer;
- when the timer runs out the radar comes back up and `Magic` calls it.

Practical consequence: a HARM shot buys you a working window, not a kill.
Plan the run for the window, and remember the column's own SHORAD (SA-13,
SA-19, ZSU-23-4) never shuts down — it is optical and IR-guided.

## Package

| Callsign    | Type     | Base     | Role                                  |
|-------------|----------|----------|---------------------------------------|
| Uzi 1       | F-16C-50 | Hatay    | Player SEAD / interdiction            |
| Pontiac 1-2 | F/A-18C  | Hatay    | Strike on the column (held until release) |
| Eagle 1-2   | F-15C    | Incirlik | TARCAP west of the corridor           |
| Hammer      | MQ-9     | on station | FAC(A), lases the column, code {_LASER_CODE}  |
| Magic       | E-3A     | Incirlik | AWACS, {_FREQ_AWACS}.000 AM                    |
| Texaco      | KC-135   | Incirlik | Tanker, {_FREQ_TANKER}.000 AM, TACAN 10X        |

Loadouts are spelled out in the mission rather than left to the DCS payload
defaults: `Uzi` carries 2x AGM-88C, 2x CBU-97 (SFW), 2x AIM-120C, 2x AIM-9X,
the AN/ASQ-213 HTS pod, a LITENING pod and a 300 gal centerline; `Pontiac`
carries 4x GBU-12 with ATFLIR — buddy-lase off `Hammer` or self-lase.

## Threats

- **Long belt:** SA-2 site at Abu al-Duhur, ~40 km envelope, Skill Average.
- **Medium belt:** SA-6 site on the high ground over the convoy route, ~25 km
  envelope, Skill High. The 1S91 Straight Flush is the priority kill — the
  launchers cannot engage without it.
- **Short belt:** mobile SA-8 site at the Taftanaz off-load, ~10 km, Skill High.
- **Organic SHORAD:** 2x SA-13 Strela-10M3, 1x SA-19 Tunguska, 1x ZSU-23-4 in
  the column itself. Skill High, and immune to HARM suppression.
- **EWR:** 2x 55G6 feeding the network and GCI.
- **Air:** 2x MiG-29S (Skill High) alert-5 at Bassel Al-Assad, scrambled cold
  off the ramp when the column is ~30% attrited.

## ROE

- Cleared to engage the convoy and every air-defence unit covering it.
- Cleared to engage Syrian and Russian aircraft inside the corridor.
- HARM suppresses; a dark site is not a dead site. Do not loiter in a MEZ
  waiting for a radar that will come back up.
- Tank from `Texaco` before the push if SEAD runs long — F-16C internal fuel
  does not cover a 60-minute sortie plus a MEZ fight.
- Bingo fuel: 3500 lb. RTB Hatay (no divert).

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- PUSH: 25 km southeast of Hatay.
- Convoy axis: Abu al-Duhur → Taftanaz, north-west, ~28 km of road.
- Off-load: Taftanaz. The column reaching it is a mission failure.

## Frequencies

- Magic AWACS: {_FREQ_AWACS}.000 AM
- Texaco tanker: {_FREQ_TANKER}.000 AM, TACAN 10X
- Hammer FAC(A): {_FREQ_FAC}.000 AM, laser code {_LASER_CODE}
- Hatay tower: per kneeboard

## Weather

Late-summer Levant haze: 30 °C, QNH 760 mmHg, light west wind, 25 km
visibility, few clouds at 3000 m.

## Win / loss conditions

- **Primary success:** the column is combat-ineffective (70% of its vehicles
  destroyed) before it reaches Taftanaz.
- **Full success:** the entire column destroyed, MiG-29S scramble defeated.
- **Failure:** the column reaches the Taftanaz off-load.

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
        usa = m.country("USA")
        russia, syria = m.country("Russia"), m.country("Syria")

        convoy = self._spawn_red_convoy(m, syria, scene)
        sa2, sa2_pos = self._spawn_red_sa2_belt(m, russia, scene)
        sa6, sa6_pos = self._spawn_red_sa6_belt(m, russia, scene)
        sa8, sa8_pos = self._spawn_red_sa8_belt(m, russia, scene)
        ewrs, ewr_positions = self._spawn_red_ewr_chain(m, russia, scene)
        migs = self._spawn_red_alert_fighters(m, russia, scene)

        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        tarcap_track = self._spawn_tarcap(m, usa, scene)
        fac_track = self._spawn_fac(m, usa, scene, convoy=convoy)
        pontiac = self._spawn_strike(m, usa, scene, target_unit=convoy.units[0])
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            sead_ip=sa6_pos,
            threats=(sa2_pos, sa6_pos, sa8_pos, *ewr_positions),
        )

        self._draw_plan(
            m,
            scene,
            sa2_pos=sa2_pos,
            sa6_pos=sa6_pos,
            sa8_pos=sa8_pos,
            ewr_positions=ewr_positions,
            corridor=corridor,
            tarcap_track=tarcap_track,
            fac_track=fac_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
        )
        self._add_harm_reaction(m, sa2=sa2, sa6=sa6, sa8=sa8, ewrs=ewrs)
        self._add_intro_voice(m)
        self._add_support_checkins(m)
        self._add_strike_release_triggers(m, sa6=sa6, pontiac=pontiac)
        self._add_scramble_trigger(m, convoy=convoy, migs=migs)
        self._add_end_triggers(m, scene, convoy=convoy, migs=migs)
        self._add_briefing(m)

        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """08:40 map-local on 12 September 2026 — the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 9, 12, 8, 40, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Late-summer Levant haze: 30 C, light west wind, 25 km visibility."""
        w = m.weather
        w.season_temperature = 30.0
        w.qnh = 760
        w.wind_at_ground.direction = 270
        w.wind_at_ground.speed = 3
        w.wind_at_2000.direction = 280
        w.wind_at_2000.speed = 7
        w.wind_at_8000.direction = 290
        w.wind_at_8000.speed = 12
        w.clouds_base = 3000
        w.clouds_thickness = 300
        w.clouds_density = 2
        w.visibility_distance = 25000
        w.name = "Late summer haze"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim the two blue fields, the Syrian fields for red, derive the AO axis.

        Hatay is the forward strip the fighters work from — it has ten fighter
        stands and no heavy parking, so `Magic`, `Texaco` and the `Eagle`
        TARCAP come off Incirlik like they would in reality.

        The convoy axis runs Abu al-Duhur -> Taftanaz; both endpoints are
        snapped onto real roads so the column drives instead of ploughing
        cross-country. Everything downstream (SAM belts, ingress corridor,
        CAP stations) is anchored on that axis.
        """
        t = self._terrain
        hatay = t.airports["Hatay"]
        incirlik = t.airports["Incirlik"]
        abu = t.airports["Abu al-Duhur"]
        taftanaz = t.airports["Taftanaz"]
        bassel = t.airports["Bassel Al-Assad"]
        hatay.set_blue()
        incirlik.set_blue()
        abu.set_red()
        taftanaz.set_red()
        bassel.set_red()

        overlay = load_scene("syria")
        axis = taftanaz.position.heading_between_point(abu.position)
        origin_seed = taftanaz.position.point_from_heading(axis, 28_000)
        dest_seed = taftanaz.position.point_from_heading(axis, 4_000)
        origin = self._on_road(overlay, origin_seed)
        destination = self._on_road(overlay, dest_seed)
        route_mid = origin.midpoint(destination)
        return _Scene(
            hatay=hatay,
            incirlik=incirlik,
            abu_al_duhur=abu,
            taftanaz=taftanaz,
            bassel=bassel,
            convoy_origin=origin,
            convoy_destination=destination,
            route_mid=route_mid,
            threat_axis_deg=route_mid.heading_between_point(hatay.position),
            overlay=overlay,
        )

    def _on_road(self, overlay: TacticalScene, near: Point) -> Point:
        """Snap `near` onto a road, falling back to the raw point if none is close."""
        for radius in (6_000.0, 12_000.0):
            try:
                return convoy_spawn(overlay, near, radius_m=radius)
            except LookupError:
                continue
        return near

    # -- red side: the convoy ------------------------------------------------

    def _spawn_red_convoy(
        self, m: Mission, syria: Country, scene: _Scene
    ) -> VehicleGroup:
        """Syrian resupply column with organic SHORAD, road-bound for Taftanaz.

        Armor and trucks are Average; the SHORAD riding with them is High —
        they are the reason the player cannot simply orbit overhead with a gun.
        The column is one group so `GroupLifeLess` reads as "combat-effective
        fraction remaining", which is the mission's win condition.
        """
        convoy_types = [
            vehicles.Armor.T_72B,
            vehicles.AirDefence.X_2S6_Tunguska,
            vehicles.Armor.BTR_80,
            vehicles.Unarmed.Ural_375,
            vehicles.AirDefence.Strela_10M3,
            vehicles.Unarmed.Ural_375,
            vehicles.Armor.T_72B,
            vehicles.AirDefence.ZSU_23_4_Shilka,
            vehicles.Armor.BTR_80,
            vehicles.AirDefence.Strela_10M3,
            vehicles.Armor.T_72B,
        ]
        heading = int(
            scene.convoy_origin.heading_between_point(scene.convoy_destination)
        )
        convoy = m.vehicle_group_platoon(
            syria,
            "Convoy Nasr",
            cast(list[type[VehicleType]], convoy_types),
            position=scene.convoy_origin,
            heading=heading,
        )
        shorad_types = {
            vehicles.AirDefence.X_2S6_Tunguska.id,
            vehicles.AirDefence.Strela_10M3.id,
            vehicles.AirDefence.ZSU_23_4_Shilka.id,
        }
        for u in convoy.units:
            u.skill = Skill.High if u.type in shorad_types else Skill.Average
        convoy.add_waypoint(
            scene.convoy_destination,
            move_formation=PointAction.OnRoad,
            speed=35,  # km/h — makes the Taftanaz off-load around T+50 unopposed
        )
        apply_ai_difficulty(convoy, self.difficulty)
        return convoy

    # -- red side: the three SAM belts --------------------------------------

    def _spawn_red_sa2_belt(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> tuple[VehicleGroup, Point]:
        """Outer belt: SA-2 at Abu al-Duhur, covering the southern approach."""
        pos = self._ridge_or_offset(
            scene,
            defends=scene.abu_al_duhur.position,
            radius_m=12_000.0,
            prominence=15.0,
        )
        sa2 = ad.build_sa2_site(
            m,
            russia,
            pos,
            heading=int(pos.heading_between_point(scene.hatay.position)),
            launchers=6,
            prefix="Zubr ",
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return sa2, pos

    def _spawn_red_sa6_belt(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> tuple[VehicleGroup, Point]:
        """Middle belt: SA-6 on the high ground owning the convoy route.

        The 1S91 Straight Flush is `units[0]` and stays in the same group as its
        launchers — a Kub TEL cannot engage without its radar in-group. Gate
        objectives on `UnitDead(units[0].id)`, never on splitting the site.
        """
        pos = self._ridge_or_offset(
            scene, defends=scene.route_mid, radius_m=16_000.0, prominence=25.0
        )
        heading = int(pos.heading_between_point(scene.hatay.position))
        sa6 = templates.VehicleTemplate.sa6_site(
            m,
            russia,
            pos,
            heading=heading,
            prefix="Vega ",
            skill=Skill.High,
        )
        # pydcs's template is a two-rail site; a battery that has to cover a
        # 25 km road segment fields four. Same group — the TELs need the 1S91.
        for i, bearing in enumerate((heading + 70, heading + 290)):
            tel = m.vehicle(f"Launcher {i + 3}", vehicles.AirDefence.Kub_2P25_ln)
            tel.position = pos.point_from_heading(bearing, 45)
            tel.heading = heading
            tel.skill = Skill.High
            sa6.add_unit(tel)
        return sa6, pos

    def _spawn_red_sa8_belt(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> tuple[VehicleGroup, Point]:
        """Inner belt: mobile SA-8 ring around the Taftanaz off-load."""
        anchor = scene.convoy_destination.point_from_heading(
            scene.threat_axis_deg, 4_000.0
        )
        pos = find_clear_spot(
            scene.overlay.overlay, anchor, self._terrain, radius_m=3_000.0
        )
        sa8 = ad.build_sa8_site(
            m,
            russia,
            pos,
            heading=int(scene.threat_axis_deg),
            launchers=3,
            prefix="Osa ",
            skill=Skill.High,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return sa8, pos

    def _spawn_red_ewr_chain(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> tuple[list[VehicleGroup], list[Point]]:
        """2x 55G6 EWR on high ground behind the belts, feeding GCI."""
        frontier = [
            scene.route_mid.point_from_heading(
                (scene.threat_axis_deg + 120.0) % 360.0, 22_000.0
            ),
            scene.abu_al_duhur.position.point_from_heading(
                (scene.threat_axis_deg + 200.0) % 360.0, 18_000.0
            ),
        ]
        try:
            positions = scene.overlay.place_ewr_chain(
                frontier_polyline=frontier,
                count=2,
                min_spacing_m=25_000.0,
                min_elevation_m=250.0,
            )
        except LookupError:
            positions = frontier
        groups: list[VehicleGroup] = []
        for i, pos in enumerate(positions):
            grp = m.vehicle_group(
                russia,
                f"EWR Sarab-{i + 1}",
                vehicles.AirDefence.X_55G6_EWR,
                position=pos,
                heading=int(scene.threat_axis_deg),
            )
            ad.set_skill(grp, Skill.High)
            groups.append(grp)
        return groups, list(positions)

    def _ridge_or_offset(
        self, scene: _Scene, *, defends: Point, radius_m: float, prominence: float
    ) -> Point:
        """Prominent, LOS-holding ground covering `defends`, with fallbacks.

        The Idlib plain is flat in places, so the strict prominence pass fails
        often enough that a two-step relaxation (then a plain clear-spot
        offset) is worth having rather than crashing the generator.
        """
        for envelope, prom in (
            (radius_m, prominence),
            (radius_m * 1.5, max(5.0, prominence / 3.0)),
        ):
            try:
                return sam_site_on_ridge(
                    scene.overlay,
                    defends=defends,
                    threat_axis_deg=scene.threat_axis_deg,
                    envelope_radius_m=envelope,
                    min_prominence_m=prom,
                )
            except LookupError:
                continue
        anchor = defends.point_from_heading(scene.threat_axis_deg, radius_m / 2.0)
        return find_clear_spot(
            scene.overlay.overlay, anchor, self._terrain, radius_m=4_000.0
        )

    # -- red side: alert fighters -------------------------------------------

    def _spawn_red_alert_fighters(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> FlyingGroup:
        """2x MiG-29S sitting alert-5 at Bassel Al-Assad, cold until scrambled.

        Cold ramp rather than late activation: the scramble reads as *caused*
        by the strike on the column, and buys the player the few minutes it
        takes them to start, taxi and climb out.
        """
        migs = m.flight_group_from_airport(
            country=russia,
            name="Ivan",
            aircraft_type=planes.MiG_29S,
            airport=scene.bassel,
            maintask=task.CAP,
            start_type=StartType.Cold,
            group_size=2,
        )
        migs.add_runway_waypoint(scene.bassel)
        migs.add_waypoint(scene.route_mid, altitude=7000, speed=850, name="AO")
        migs.add_waypoint(
            scene.convoy_destination, altitude=6000, speed=800, name="CONVOY"
        )
        migs.land_at(scene.bassel)
        self._arm(
            migs,
            planes.MiG_29S,
            [
                (1, "R_73__AA_11_Archer____Infra_Red"),
                (2, "R_77__AA_12_Adder____Active_Rdr"),
                (3, "R_27ER__AA_10_Alamo_C____Semi_Act_Extended_Range"),
                (4, "Fuel_tank_1400L"),
                (5, "R_27ER__AA_10_Alamo_C____Semi_Act_Extended_Range"),
                (6, "R_77__AA_12_Adder____Active_Rdr"),
                (7, "R_73__AA_11_Archer____Infra_Red"),
            ],
        )
        ad.set_skill(migs, Skill.High)
        apply_ai_difficulty(migs, self.difficulty)
        return migs

    # -- loadouts ------------------------------------------------------------

    def _arm(
        self,
        group: FlyingGroup,
        plane_type: type,
        stores: list[tuple[int, str]],
    ) -> None:
        """Load `stores` — `(pylon, weapon attribute)` — as the whole loadout.

        Every flight in this mission spells its loadout out: the package the
        briefing promises (HARM + HTS Weasel, buddy-lase Hornet) is not what
        the DCS task defaults hand out. Stations are cleared first because
        `Mission.flight_group_*` already ran `load_task_default_loadout`, which
        fills pylons from the installed game once `DCS_INSTALL_DIR` is set (see
        `core/dcs_install.py`) — without the clear the defaults survive on
        every station this list skips, e.g. two extra tanks on `Eagle`.

        The `PylonN` classes on each `PlaneType` enumerate what a station
        legally accepts, so these pairs are checked against pydcs rather than
        guessed.
        """
        for unit in group.units:
            unit.pylons.clear()
        for pylon, weapon in stores:
            group.load_pylon(getattr(getattr(plane_type, f"Pylon{pylon}"), weapon))

    # -- blue side -----------------------------------------------------------

    def _spawn_awacs(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """E-3A Magic north-west of the corridor, 251.000 AM, 120 km legs.

        Heavies come off Incirlik — Hatay is a fighter strip with no parking
        for an E-3A — but the track is anchored on Hatay so the picture sits
        between the player and the corridor.
        """
        p1, p2 = scene.overlay.place_awacs_track(
            home_base=scene.hatay.position,
            threat_axis=scene.route_mid,
            standoff_m=90_000.0,
            track_length_m=120_000.0,
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
            frequency=_FREQ_AWACS,
        )
        return p1, p2

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco behind Hatay, TACAN 10X — a 60 min F-16 sortie needs it.

        Off Incirlik with the other heavy, but the track sits just behind the
        forward strip so a tanker pass costs the player minutes, not tens of
        minutes.
        """
        p1, p2 = scene.overlay.place_tanker_track(
            home_base=scene.hatay.position,
            threat_axis=scene.route_mid,
            standoff_m=55_000.0,
            track_length_m=60_000.0,
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
            frequency=_FREQ_TANKER,
            tacanchannel="10X",
        )
        return p1, p2

    def _spawn_tarcap(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """F-15C Eagle 2-ship between the corridor and Bassel Al-Assad.

        Launches from Incirlik — Hatay's ten stands are reserved for the
        player flight and Pontiac — so the TARCAP arrives on station about
        when the player pushes.
        """
        p1, p2 = scene.overlay.place_cap_station(
            defended_asset=scene.route_mid,
            threat_bearing_deg=scene.route_mid.heading_between_point(
                scene.bassel.position
            ),
            forward_distance_m=35_000.0,
            track_length_m=45_000.0,
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
        amraam = "AIM_120C_AMRAAM___Active_Radar_AAM"
        self._arm(
            eagle,
            planes.F_15C,
            [
                (1, "AIM_9M_Sidewinder_IR_AAM"),
                (3, amraam),
                (4, amraam),
                (5, amraam),
                (6, "Fuel_tank_610_gal"),
                (7, amraam),
                (8, amraam),
                (9, amraam),
                (11, "AIM_9M_Sidewinder_IR_AAM"),
            ],
        )
        ad.set_skill(eagle, Skill.High)
        return p1, p2

    def _spawn_fac(
        self, m: Mission, usa: Country, scene: _Scene, *, convoy: VehicleGroup
    ) -> tuple[Point, Point]:
        """MQ-9 Hammer holding west of the corridor, lasing the column.

        Spawns airborne already on station — a Reaper that has been watching
        this road since before dawn is why the column is a known target at all,
        and at 300 km/h it would otherwise still be transiting when the sortie
        ends. Holds on the friendly side of the route at medium altitude,
        outside the SA-8 ring, giving the player a talk-on and a laser spot
        while the belts are still up.
        """
        p1 = scene.route_mid.point_from_heading(scene.threat_axis_deg, 28_000.0)
        p2 = p1.point_from_heading((scene.threat_axis_deg + 90.0) % 360.0, 18_000.0)
        hammer = m.flight_group(
            country=usa,
            name="Hammer",
            aircraft_type=planes.MQ_9_Reaper,
            airport=None,
            position=p1,
            altitude=6000,
            speed=300,
            maintask=task.AFAC,
            group_size=1,
        )
        for i in range(2):
            hammer.add_waypoint(p1, altitude=6000, speed=300, name=f"FAC-{2 * i + 1}")
            hammer.add_waypoint(p2, altitude=6000, speed=300, name=f"FAC-{2 * i + 2}")
        hammer.land_at(scene.hatay)
        ad.set_skill(hammer, Skill.High)
        fac_attack_group(
            hammer,
            convoy,
            designation=task.Designation.Laser,
            frequency=_FREQ_FAC,
            modulation=task.Modulation.AM,
        )
        return p1, p2

    def _spawn_strike(
        self, m: Mission, usa: Country, scene: _Scene, *, target_unit
    ) -> FlyingGroup:
        """F/A-18C Pontiac pair, held on the ground until the SEAD phase pays off.

        Late-activated so the release trigger (SA-6 radar dead, or the 25 minute
        cut-off) is what puts them in the air — sending Hornets into a live
        SA-6 MEZ on take-off would just feed the site kills.
        """
        pontiac = m.strike_flight(
            usa,
            "Pontiac",
            planes.FA_18C_hornet,
            target=target_unit,
            airport=scene.hatay,
            start_type=StartType.Warm,
            group_size=2,
        )
        pontiac.late_activation = True
        lgb = "BRU_33_with_2_x_GBU_12___500lb_Laser_Guided_Bomb"
        tank = "FPU_8A_Fuel_Tank_330_gallons"
        self._arm(
            pontiac,
            planes.FA_18C_hornet,
            [
                (1, "AIM_9X_Sidewinder_IR_AAM"),
                (2, lgb),
                (3, tank),
                (4, "AN_ASQ_228_ATFLIR___Targeting_Pod"),
                (6, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (7, tank),
                (8, lgb),
                (9, "AIM_9X_Sidewinder_IR_AAM"),
            ],
        )
        ad.set_skill(pontiac, Skill.High)
        return pontiac

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        sead_ip: Point,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Uzi F-16C-50 from Hatay, terrain-masked ingress to the SEAD IP."""
        player = m.flight_group_from_airport(
            country=usa,
            name="Uzi",
            aircraft_type=planes.F_16C_50,
            airport=scene.hatay,
            maintask=task.SEAD,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        _mark_clients(player)
        # Wild Weasel + interdiction: two HARMs for the belts, two CBU-97 SFW
        # for the column, HTS to find the emitters, LITENING to find the trucks.
        harm = "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"
        sfw = "CBU_97___10_x_SFW_Cluster_Bomb"
        self._arm(
            player,
            planes.F_16C_50,
            [
                (1, "AIM_9X_Sidewinder_IR_AAM"),
                (2, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (3, harm),
                (4, sfw),
                (5, "Fuel_tank_300_gal"),
                (6, sfw),
                (7, harm),
                (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (9, "AIM_9X_Sidewinder_IR_AAM"),
                (10, "AN_ASQ_213_HTS___HARM_Targeting_System"),
                (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
            ],
        )
        player.add_runway_waypoint(scene.hatay)
        push = scene.hatay.position.point_from_heading(
            scene.hatay.position.heading_between_point(scene.route_mid), 25_000.0
        )
        corridor = scene.overlay.place_ingress_corridor(
            ip=push,
            target=sead_ip,
            threats=threats,
            waypoints=3,
            leg_search_radius_m=8_000.0,
        )
        for i, pt in enumerate(corridor):
            if i == 0:
                name = "PUSH"
            elif i == len(corridor) - 1:
                name = "SEAD IP"
            else:
                name = f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=7000, speed=800, name=name)
        player.add_waypoint(scene.route_mid, altitude=6000, speed=750, name="CONVOY AO")
        player.add_runway_waypoint(scene.hatay)
        player.land_at(scene.hatay)
        return [*corridor, scene.route_mid]

    # -- F10 map briefing ---------------------------------------------------

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        sa2_pos: Point,
        sa6_pos: Point,
        sa8_pos: Point,
        ewr_positions: list[Point],
        corridor: list[Point],
        tarcap_track: tuple[Point, Point],
        fac_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
    ) -> None:
        """Paint the plan on the F10 map (trained: coarse, estimated threats)."""
        plan = PlanOverlay(m, self.difficulty)
        plan.objective(scene.route_mid, "Convoy axis — Taftanaz road", radius=7_000.0)
        plan.route(corridor, "Uzi ingress")
        plan.orbit(*tarcap_track, "Eagle TARCAP")
        plan.orbit(*fac_track, "Hammer FAC(A)")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco tanker")
        plan.waypoint_label(scene.convoy_destination, "Off-load — Taftanaz")
        plan.threat(
            sa2_pos, radius=40_000.0, label="SA-2 belt", icon=StandardIcon.AirDefense
        )
        plan.threat(
            sa6_pos, radius=25_000.0, label="SA-6 belt", icon=StandardIcon.AirDefense
        )
        plan.threat(
            sa8_pos, radius=10_000.0, label="SA-8 belt", icon=StandardIcon.AirDefense
        )
        for pos in ewr_positions:
            plan.threat(pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)
        plan.threat(
            scene.convoy_origin,
            radius=8_000.0,
            label="Convoy SHORAD",
            icon=StandardIcon.Mechanized,
        )

    # -- HARM reaction ------------------------------------------------------

    def _add_harm_reaction(
        self,
        m: Mission,
        *,
        sa2: VehicleGroup,
        sa6: VehicleGroup,
        sa8: VehicleGroup,
        ewrs: list[VehicleGroup],
    ) -> None:
        """Wire realistic emissions control for every radar-guided site.

        The tuned dials are the difficulty statement for the SEAD half of this
        mission: the SA-6 and SA-8 crews are drilled (react ~9 times in 10,
        recognise the launch in 3–7 s, stay dark up to ~2 min), the SA-2 crew
        is a conscript belt that misses the call almost a third of the time,
        and the EWRs — furthest from the shooter, and not the ones being shot
        at — react slowest and come back up soonest.
        """
        sites = [
            ArmSite(
                sa6,
                "SA-6",
                probability=0.9,
                delay_s=(3.0, 7.0),
                shutdown_s=(70.0, 130.0),
            ),
            ArmSite(
                sa8,
                "SA-8",
                probability=0.85,
                delay_s=(2.0, 5.0),
                shutdown_s=(50.0, 90.0),
                react_range_m=40_000.0,
            ),
            ArmSite(
                sa2,
                "SA-2",
                probability=0.7,
                delay_s=(5.0, 11.0),
                shutdown_s=(60.0, 120.0),
            ),
            *[
                ArmSite(
                    ewr,
                    "early-warning radar",
                    probability=0.75,
                    delay_s=(6.0, 14.0),
                    shutdown_s=(45.0, 80.0),
                    react_range_m=90_000.0,
                )
                for ewr in ewrs
            ],
        ]
        arm_emcon_reaction(
            m,
            sites,
            voice=self._voice,
            coalition="blue",
            down_call="Magic: {label} has ceased emissions, site is dark.",
            up_call="Magic: {label} is radiating again, expect it hot.",
        )

    # -- triggers and briefing ----------------------------------------------

    def _add_intro_voice(self, m: Mission) -> None:
        """Mission-start AWACS picture: the column, the belts, the clock."""
        intro = triggers.TriggerStart(comment="Magic mission-start picture")
        call = (
            "Uzi, Magic on station. Syrian column rolling north-west out of "
            "Abu al-Duhur, forty minutes from the Taftanaz off-load. Three SAM "
            "belts on your nose, SA-6 owns the route. Texaco is 270.0, TACAN 10X."
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
            call="Uzi, Texaco established, 270.0, TACAN 10X, ready for receivers.",
        )
        self._add_checkin(
            m,
            seconds=300,
            comment="Hammer FAC(A) check-in",
            call=(
                f"Uzi, Hammer overhead the corridor on {_FREQ_FAC}.0. I have the "
                f"column visual, eleven vehicles, laser code {_LASER_CODE} on call."
            ),
        )
        self._add_checkin(
            m,
            seconds=480,
            comment="SEAD reminder",
            call=(
                "Magic: reminder, Uzi — HARM suppresses those belts, it does not "
                "kill them. Work the dark window."
            ),
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

    def _add_strike_release_triggers(
        self, m: Mission, *, sa6: VehicleGroup, pontiac: FlyingGroup
    ) -> None:
        """Release Pontiac on the SA-6 radar dying, or on the 25 minute cut-off.

        Two ways in, one flag: with EMCON in play the player may only ever
        suppress the SA-6, so gating the Hornets purely on a radar kill could
        strand them on the ramp for the whole sortie.
        """
        radar_down = triggers.TriggerOnce(comment="SA-6 Straight Flush destroyed")
        radar_down.add_condition(condition.UnitDead(sa6.units[0].id))
        radar_down.add_action(action.SetFlag(_FLAG_STRIKE_RELEASE))
        radar_call = (
            "Magic: SA-6 Straight Flush is destroyed. The launchers are blind. "
            "Pontiac, you are cleared to push."
        )
        radar_down.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue, m.string(radar_call), seconds=15
            )
        )
        self._voice.attach_to_coalition(m, radar_down, radar_call, coalition="blue")
        m.triggerrules.triggers.append(radar_down)

        timeout = triggers.TriggerOnce(comment="Strike release cut-off (T+25)")
        timeout.add_condition(condition.TimeAfter(seconds=1500))
        timeout.add_action(action.SetFlag(_FLAG_STRIKE_RELEASE))
        m.triggerrules.triggers.append(timeout)

        release = triggers.TriggerOnce(comment="Pontiac released onto the column")
        release.add_condition(condition.FlagIsTrue(_FLAG_STRIKE_RELEASE))
        release.add_action(action.ActivateGroup(pontiac.id))
        release_call = (
            "Pontiac 1-2 airborne out of Hatay, inbound the column. "
            "Uzi, keep the belts off us."
        )
        release.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue, m.string(release_call), seconds=15
            )
        )
        self._voice.attach_to_coalition(m, release, release_call, coalition="blue")
        m.triggerrules.triggers.append(release)

    def _add_scramble_trigger(
        self, m: Mission, *, convoy: VehicleGroup, migs: FlyingGroup
    ) -> None:
        """Scramble the Bassel alert pair once the column is ~30% attrited."""
        trig = scramble_on_trigger(
            m,
            migs,
            condition.GroupLifeLess(convoy.id, 70),
            comment="Convoy attrited: MiG-29S alert scramble",
        )
        call = (
            "Magic: alert pair scrambling out of Bassel Al-Assad, MiG-29S, "
            "inbound the corridor. Eagle is committing."
        )
        trig.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=15)
        )
        self._voice.attach_to_coalition(m, trig, call, coalition="blue")

    def _add_end_triggers(
        self, m: Mission, scene: _Scene, *, convoy: VehicleGroup, migs: FlyingGroup
    ) -> None:
        """Primary / full success on convoy attrition, failure if it off-loads."""
        primary = triggers.TriggerOnce(comment="Convoy combat-ineffective")
        primary.add_condition(condition.GroupLifeLess(convoy.id, 30))
        primary_call = (
            "Magic: the column is combat-ineffective. Primary objective complete. "
            "Uzi, finish what you can and RTB Hatay."
        )
        primary.add_action(action.MessageToAll(m.string(primary_call), seconds=20))
        self._voice.attach_to_all(m, primary, primary_call)
        m.triggerrules.triggers.append(primary)

        full = triggers.TriggerOnce(comment="Convoy destroyed, air threat cleared")
        full.add_condition(condition.GroupDead(convoy.id))
        full.add_condition(condition.GroupDead(migs.id))
        full_call = (
            "Magic: column destroyed, air threat clear. Full mission success. "
            "Uzi, Pontiac, RTB Hatay. Texaco is on tap."
        )
        full.add_action(action.MessageToAll(m.string(full_call), seconds=25))
        self._voice.attach_to_all(m, full, full_call)
        m.triggerrules.triggers.append(full)

        offload_zone = m.triggers.add_triggerzone(
            position=scene.convoy_destination,
            radius=3_000,
            hidden=True,
            name="Taftanaz off-load",
        )
        failure = triggers.TriggerOnce(comment="Convoy reached the off-load")
        failure.add_condition(condition.PartOfGroupInZone(convoy.id, offload_zone.id))
        failure_call = (
            "Magic: the column made the Taftanaz off-load. Tasking failed. "
            "Uzi, egress west and RTB Hatay."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=20))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Destroy the Syrian resupply column before it reaches the Taftanaz "
            "off-load. Suppress the SA-2, SA-6 and SA-8 belts covering the "
            "corridor — their crews drop emissions when they see a HARM and "
            "come back up a minute later, so work the window. Pontiac is "
            "released once the SA-6 radar is dead or at T+25. RTB Hatay."
        )
        m.set_description_redtask_text(
            "Run the resupply column from Abu al-Duhur to the Taftanaz "
            "off-load. Air defence belts cover the corridor; drop emissions on "
            "anti-radiation fire and re-radiate once the shooter is dry. "
            "Scramble the MiG-29S alert pair when the column takes losses."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Syria 'Idlib Gauntlet' convoy-interdiction mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/idlib_gauntlet"),
        help="Output directory for the .miz and README.md (default: out/idlib_gauntlet)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Uzi flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = IdlibGauntlet(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
