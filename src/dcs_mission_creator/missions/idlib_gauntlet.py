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
the last known point, and the site comes back up several minutes later. HARMs
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

from dcs_mission_creator.core import air_defense as ad, routing, waypoints
from dcs_mission_creator.core.emcon import ArmSite, arm_emcon_reaction
from dcs_mission_creator.core.map_draw import PlanOverlay, conceal_country
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.placement import (
    convoy_spawn,
    find_clear_spot,
    load_scene,
    sam_site_on_ridge,
)
from dcs_mission_creator.core.routing import ThreatRing
from dcs_mission_creator.core.tasking import (
    FacCallsign,
    apply_ai_difficulty,
    apply_threat_reaction,
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
# F-16C stock presets carrying those nets, so the briefing can say "channel N"
# instead of leaving the player to hand-tune and find no JTAC in the menu.
_PRESET_AWACS = "COMM1 CH 18"
_PRESET_TANKER = "COMM1 CH 7"
_PRESET_FAC = "COMM2 CH 10"
# Hammer's station: a race-track abeam the convoy road, long enough to cover the
# whole 25 km of it. 5 km cross-track at 18,000 ft holds the column inside about
# 8 km slant the entire run — a DCS FAC that sits further out never acquires.
_FAC_OFFSET_M = 5_000.0
_FAC_LEG_M = 18_000.0
_FAC_ALT_M = 5_500
_FAC_SPEED_KPH = 300


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
  Overhead imagery before first light caught a Syrian
  resupply column forming at Abu al-Duhur; a Reaper has
  been following it since and it is running north-west
  toward the Taftanaz off-load. Partner-force reporting
  out of the pocket says it carries the ammunition for
  the next push. It travels with its own short-range
  air defence.
  A Rivet Joint track overnight mapped the corridor it
  drives through: three overlapping Russian-supplied
  SAM belts. Those crews are drilled — they drop
  emissions when they see an anti-radiation shot, and
  they stay off the air a while before coming back up.
  Expect to suppress, not to sanitize.

MISSION (Uzi — F-16C-50, Hatay)
  Break the column up before it reaches Taftanaz.
  Suppress whatever belt is holding you off the target.
  Render it combat-ineffective and the ammunition never
  reaches the pocket; the whole column is better.

PACKAGE
  Uzi 1 (you): F-16C-50, Hatay, hot ramp. 2x AGM-88C,
        2x CBU-97 SFW, 2x AIM-120C, 2x AIM-9X, HTS pod,
        LITENING, 300 gal centerline.
  Pontiac 1-2: F/A-18C, 4x GBU-12 + ATFLIR, Hatay. Held
        in reserve, pushing onto the column once the
        SAM threat over the route is suppressed.
  Eagle 1-2  : F-15C TARCAP west of the corridor.
  Hammer     : MQ-9, {_FREQ_FAC}.000 AM, FAC(A) over the
        corridor, lasing the column, code {_LASER_CODE}.
  Magic      : E-3A AWACS, {_FREQ_AWACS}.000 AM.
  Texaco     : KC-135, {_FREQ_TANKER}.000 AM, TACAN 10X.

INTELLIGENCE
  SAM : From the Rivet Joint cut — an SA-2 belt around
        Abu al-Duhur, reach out to about 40 km; an SA-6
        belt on the high ground over the convoy route,
        about 25 km, and it is the one that owns the
        road; a mobile SA-8 at the Taftanaz off-load,
        about 10 km. Early-warning radars behind them
        feed the whole net. The SA-6 crew is the sharpest
        of the three; the SA-2 site is conscripts.
  SHORAD: Reaper imagery shows tracked IR launchers, a
        gun-missile vehicle and a Shilka riding with the
        column. None of that shuts down for a HARM —
        kill it or stay outside 8 km.
  Air : ELINT has the alert pair at Bassel Al-Assad on
        cockpit alert. MiG-29S. They will come once the
        column starts taking losses.

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
  Off-load    : Taftanaz. If the column makes it there,
                we have missed the window.

FREQUENCIES
  Magic AWACS   : {_FREQ_AWACS}.000 AM ({_PRESET_AWACS})
  Texaco tanker : {_FREQ_TANKER}.000 AM, TACAN 10X
                  ({_PRESET_TANKER})
  Hammer FAC(A) : {_FREQ_FAC}.000 AM ({_PRESET_FAC}),
                  laser {_LASER_CODE}
  Hatay tower   : per kneeboard

  Hammer is on the VHF radio, not the UHF one you start
  on. Tune COMM2 before you look for him — the JTAC only
  shows up in the radio menu on the net he is talking on.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Idlib Gauntlet

**Theater:** Syria
**Date / time:** 12 September 2026, 08:40 local
**Player aircraft:** F-16C-50 (`Uzi`), Hatay, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** trained (medium) — three layered SAM belts with drilled
EMCON-capable crews, organic SHORAD on the target, an alert fighter pair,
full support package (AWACS, tanker, TARCAP, FAC(A))
**Expected sortie length:** ~60 minutes

## Situation

Overhead imagery before first light caught a Syrian resupply column forming at
Abu al-Duhur; a Reaper has been following it since and it is running
north-west toward the Taftanaz off-load. Partner-force reporting out of the
pocket says it carries the ammunition for the next push. It travels with its
own short-range air defence.

A Rivet Joint track overnight mapped the corridor it drives through: three
overlapping Russian-supplied SAM belts — an SA-2 belt around Abu al-Duhur, an
SA-6 belt on the high ground over the route, and a mobile SA-8 at the off-load
— tied together by early-warning radars sitting behind them.

## Mission

`Uzi` flight breaks the column up before it reaches Taftanaz. The convoy is the
objective; the SAM belts are the problem, not the target list. Kill what you
must, suppress the rest, and get weapons onto the trucks.

1. **Push and SEAD.** Ingress from Hatay through the terrain-masked corridor.
   The SA-6 on the ridge is what owns the convoy route — put it down or keep
   it down.
2. **Interdict.** Work the column with the CBU-97s — SFW submunitions are what
   kill a dispersed column in two passes. `Hammer` (MQ-9) is overhead on
   {_FREQ_FAC}.000 AM ({_PRESET_FAC}) for the talk-on, lasing code {_LASER_CODE}
   for `Pontiac`'s GBU-12s.
3. **Strike release.** `Pontiac` (2x F/A-18C) is held in reserve at Hatay and
   will run the column once the SAM threat over the route is suppressed.
4. **DCA.** The Bassel Al-Assad alert pair will scramble once the column starts
   taking real losses. `Eagle` TARCAP is west of the corridor; back them up.

## How those crews handle a HARM

Every radar-guided site in the corridor is drilled in emissions control, and
the belts are netted — a launch anywhere on the corridor is called down the
net in seconds. When they see an anti-radiation shot:

- the crew that hears the call usually drops emissions; not all of them do,
  and the SA-2 site is the least disciplined of the three;
- it takes them a few seconds to react, so a shot from close in still kills;
- the site then sits dark with radars off and weapons tight for several
  minutes, and `Magic` calls the shutdown on the radio;
- keep the pressure on with a second shot and that crew stays off the air
  longer;
- when they judge it safe the radar comes back up, and `Magic` calls that too.

Practical consequence: a HARM shot buys you a working window, not a kill.
Plan the run for the window, and remember the column's own launchers and guns
never shut down — they are optical and IR-guided.

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

## Intelligence

Positions below come off last night's Rivet Joint cut and this morning's
Reaper feed — the belts are located to a few kilometres, not to the metre, and
the map rings are marked as estimates for that reason.

- **Long belt:** SA-2 around Abu al-Duhur, reach out to roughly 40 km. Poorly
  drilled crew by the standard of the other two.
- **Medium belt:** SA-6 on the high ground over the convoy route, roughly
  25 km, and the belt that actually owns the road. The Straight Flush radar is
  the priority kill — the launchers are blind without it.
- **Short belt:** mobile SA-8 at the Taftanaz off-load, roughly 10 km. Sharp
  crew, and it will relocate.
- **Organic SHORAD:** the Reaper feed shows tracked IR launchers, a
  gun-missile vehicle and a Shilka in the column itself. Capable crews, and
  none of it can be suppressed with a HARM.
- **EWR:** early-warning radars behind the belts feeding the net and the GCI
  picture.
- **Air:** ELINT puts a MiG-29S pair on cockpit alert at Bassel Al-Assad,
  experienced crews. They will start engines when the column starts taking
  real losses.

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
- Off-load: Taftanaz. If the column reaches it, we have missed the window.

## Frequencies

- Magic AWACS: {_FREQ_AWACS}.000 AM — {_PRESET_AWACS}
- Texaco tanker: {_FREQ_TANKER}.000 AM, TACAN 10X — {_PRESET_TANKER}
- Hammer FAC(A): {_FREQ_FAC}.000 AM — {_PRESET_FAC}, laser code {_LASER_CODE}
- Hatay tower: per kneeboard

`Hammer` works the VHF radio, not the UHF one the jet starts on: put COMM2 on
{_PRESET_FAC} ({_FREQ_FAC}.000 AM) and he appears in the radio menu as
**Hammer 1-1**. Until COMM2 is on his net there is no JTAC entry to select.

## Weather

Late-summer Levant haze: 30 °C, QNH 760 mmHg, light west wind, 25 km
visibility, few clouds at 3000 m.

## Win / loss conditions

- **Primary success:** the column is rendered combat-ineffective short of
  Taftanaz — enough of it wrecked that the ammunition never gets through.
- **Full success:** the whole column destroyed and the alert pair defeated.
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
        belts = self._threat_rings(sa2_pos=sa2_pos, sa6_pos=sa6_pos, sa8_pos=sa8_pos)

        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        tarcap_track = self._spawn_tarcap(m, usa, scene)
        fac_track = self._spawn_fac(m, usa, scene, convoy=convoy)
        pontiac = self._spawn_strike(m, usa, scene, convoy=convoy, threats=belts)
        corridor = self._spawn_player(
            m,
            usa,
            scene,
            sead_ip=sa6_pos,
            threats=(sa2_pos, sa6_pos, sa8_pos, *ewr_positions),
        )

        self._conceal_red(russia, syria)
        self._draw_plan(
            m,
            scene,
            belts=belts,
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
        waypoints.snap_base_waypoints(m, scene.overlay.overlay)

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

        Both waypoints are OnRoad — the spawn one is what makes the column
        actually follow the highway rather than drive the straight line.

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
            move_formation=PointAction.OnRoad,
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
        """MQ-9 Hammer stacked over the corridor, lasing the column.

        Spawns airborne already on station — a Reaper that has been watching
        this road since before dawn is why the column is a known target at all.
        The racetrack runs parallel to the road and only 5 km off it: a DCS FAC
        acquires and lases what its own sensor sees, so a stand-off orbit on the
        far side of the corridor is a FAC that never checks in and never puts a
        spot on anything. `OrbitAction` keeps it there for the whole sortie
        instead of running out of route and going home mid-mission.
        """
        along = scene.convoy_origin.heading_between_point(scene.convoy_destination)
        center = self._fac_station(scene, along)
        p1 = center.point_from_heading((along + 180.0) % 360.0, _FAC_LEG_M / 2)
        p2 = center.point_from_heading(along, _FAC_LEG_M / 2)
        hammer = m.flight_group(
            country=usa,
            name="Hammer",
            aircraft_type=planes.MQ_9_Reaper,
            airport=None,
            position=p1.point_from_heading((along + 180.0) % 360.0, 2_000.0),
            altitude=_FAC_ALT_M,
            speed=_FAC_SPEED_KPH,
            maintask=task.AFAC,
            group_size=1,
        )
        self._task_fac_orbit(hammer, p1, p2)
        ad.set_skill(hammer, Skill.High)
        fac_attack_group(
            hammer,
            convoy,
            designation=task.Designation.Laser,
            frequency=_FREQ_FAC,
            modulation=task.Modulation.AM,
            callsign=FacCallsign.HAMMER,
        )
        return p1, p2

    def _fac_station(self, scene: _Scene, along: float) -> Point:
        """Centre of Hammer's race-track: abeam the road, on the Hatay flank.

        The offset has to be *cross*-track. Hatay sits almost straight down the
        convoy axis from the route mid-point, so stepping off along
        `threat_axis_deg` — the direction every other helper here uses for "the
        friendly side" — slides the orbit along the road instead of beside it
        and leaves the Reaper sitting over the column. Take both abeam points
        and keep the one nearer home.
        """
        left = scene.route_mid.point_from_heading((along + 90.0) % 360.0, _FAC_OFFSET_M)
        right = scene.route_mid.point_from_heading(
            (along - 90.0) % 360.0, _FAC_OFFSET_M
        )
        home = scene.hatay.position
        return min((left, right), key=home.distance_to_point)

    def _task_fac_orbit(self, hammer: FlyingGroup, p1: Point, p2: Point) -> None:
        """Park Hammer on an indefinite race-track, on its own radio, unshootable.

        The orbit sits inside the SA-6 MEZ, which is the only place it can see
        the road from — a Reaper left shootable there is dead in the first two
        minutes and the laser goes with it, so it is set invisible to enemy AI
        the way a scripted overwatch asset normally is. `SetFrequencyCommand`
        moves its own radio onto the FAC net; pydcs otherwise leaves the group
        on the 251.0 default while the FAC task talks on another frequency.
        """
        hammer.points[0].tasks.append(task.SetInvisibleCommand(True))
        wp = hammer.add_waypoint(p1, altitude=_FAC_ALT_M, speed=_FAC_SPEED_KPH)
        wp.name = "FAC-1"
        wp.tasks.append(task.SetFrequencyCommand(_FREQ_FAC, task.Modulation.AM))
        wp.tasks.append(
            task.OrbitAction(
                _FAC_ALT_M, _FAC_SPEED_KPH, task.OrbitAction.OrbitPattern.RaceTrack
            )
        )
        wp2 = hammer.add_waypoint(p2, altitude=_FAC_ALT_M, speed=_FAC_SPEED_KPH)
        wp2.name = "FAC-2"
        hammer.frequency = _FREQ_FAC
        hammer.modulation = task.Modulation.AM.value

    def _spawn_strike(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        convoy: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> FlyingGroup:
        """F/A-18C Pontiac pair, held on the ground until the SEAD phase pays off.

        Late-activated so the release trigger (SA-6 radar dead, or the 25 minute
        cut-off) is what puts them in the air — sending Hornets into a live
        SA-6 MEZ on take-off would just feed the site kills.

        Routed by hand rather than by `Mission.strike_flight`: that helper joins
        Hatay to the column with a straight line and an IP on the reciprocal,
        which walks the pair straight through the SA-2 and SA-6 belts the
        briefing tells the player to work around. Instead the run-in starts from
        a standoff IP on the friendly side of the AO, the transit bends around
        whatever rings are still up (`core/routing.py`), and the egress leaves
        the same way — so the only exposure is the attack itself.
        """
        pontiac = m.flight_group_from_airport(
            country=usa,
            name="Pontiac",
            aircraft_type=planes.FA_18C_hornet,
            airport=scene.hatay,
            maintask=task.CAS,
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
        self._route_strike(pontiac, scene, convoy=convoy, threats=threats)
        self._limit_strike_engagement(pontiac, scene.route_mid)
        return pontiac

    def _limit_strike_engagement(self, pontiac: FlyingGroup, ao: Point) -> None:
        """Keep Pontiac hunting the column, not every vehicle in Idlib.

        pydcs's CAS main task queues an unbounded "engage all ground units"
        enroute task at the spawn point, and an unbounded search is how a
        carefully routed flight ends up over a SAM site anyway: one truck
        detected off the corridor and the pair breaks off toward it. Swapping
        it for a zone-bounded search keeps the route meaningful — anything
        outside the AO is somebody else's target.
        """
        tasks = pontiac.points[0].tasks
        tasks[:] = [t for t in tasks if t.id != task.CASTaskAction.Id]
        tasks.insert(
            0,
            task.EngageTargetsInZone(
                position=ao,
                radius=15_000,
                targets=cast(
                    list[type[task.TargetType]], [task.Targets.All.GroundUnits]
                ),
            ),
        )

    def _route_strike(
        self,
        pontiac: FlyingGroup,
        scene: _Scene,
        *,
        convoy: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> None:
        """Plan Pontiac's ingress, attack and egress around the surviving belts.

        The IP sits on the Hatay side of the column, outside every ring the
        pair can stay out of; transit, run-in and egress all bend around the
        rings rather than crossing them. The column itself sits inside the SA-2
        and SA-6 envelopes — that is the mission — so those two are exposure the
        pair accepts on the run-in, which is exactly what the release trigger
        buys by holding them on the ramp until the SA-6 is down. The SA-8 at the
        off-load is not on the accepted list: nothing suppresses it, so the
        route stays out of it until the bombs are off.

        The attack is an explicit `AttackGroup` on the column — pydcs's Ground
        Attack default is a waypoint over the target and no tasking at all, so
        the Hornets would overfly the SHORAD without dropping.
        """
        target = scene.route_mid
        pontiac.add_runway_waypoint(scene.hatay)
        ip = routing.standoff_point(
            target,
            toward=scene.hatay.position,
            threats=threats,
            min_distance_m=25_000.0,
            clearance_m=4_000.0,
        )
        ingress = routing.avoid_threats(
            scene.hatay.position, ip, threats, clearance_m=5_000.0
        )
        for i, pt in enumerate(ingress[1:], start=1):
            name = "IP" if i == len(ingress) - 1 else f"INGRESS-{i}"
            pontiac.add_waypoint(pt, altitude=6400, speed=800, name=name)

        run_in = routing.avoid_threats(ip, target, threats, clearance_m=3_000.0)
        for i, pt in enumerate(run_in[1:-1], start=1):
            pontiac.add_waypoint(pt, altitude=5800, speed=800, name=f"RUN-IN-{i}")
        # Release from 5,200 m: a GBU-12 reaches the column from there, and it
        # keeps the pair above the Strela / Tunguska / Shilka ceiling riding
        # with it — the SHORAD the SEAD phase never touches.
        attack = pontiac.add_waypoint(target, altitude=5200, speed=750, name="ATTACK")
        attack.tasks.append(
            task.AttackGroup(
                convoy.id,
                weapon_type=task.WeaponType.GuidedBombs,
                group_attack=True,
                expend=task.Expend.All,
            )
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                target, scene.hatay.position, threats, clearance_m=5_000.0
            )[1:-1],
            start=1,
        ):
            pontiac.add_waypoint(pt, altitude=7000, speed=850, name=f"EGRESS-{i}")
        pontiac.add_runway_waypoint(scene.hatay)
        pontiac.land_at(scene.hatay)
        apply_threat_reaction(pontiac)

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        sead_ip: Point,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Uzi F-16C-50 from Hatay, terrain-masked ingress to the SA-6 site."""
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
        ov = scene.overlay.overlay
        for i, pt in enumerate(corridor[:-1]):
            name = "PUSH" if i == 0 else f"INGRESS-{i}"
            player.add_waypoint(pt, altitude=7000, speed=800, name=name)
        # Last corridor point is the SA-6 site itself, and the one after it the
        # convoy road: both are ground targets, so their steerpoints sit on the
        # terrain — the ingress altitude is carried by the INGRESS legs above.
        waypoints.add_ground_waypoint(
            player, corridor[-1], overlay=ov, speed=800, name="SEAD TGT"
        )
        waypoints.add_ground_waypoint(
            player, scene.route_mid, overlay=ov, speed=750, name="CONVOY AO"
        )
        player.add_runway_waypoint(scene.hatay)
        player.land_at(scene.hatay)
        return [*corridor, scene.route_mid]

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, *countries: Country) -> None:
        """Keep every Syrian and Russian group off the map, planner and datalink.

        The belts are an intel problem: the player gets the estimated rings
        `_draw_plan` paints, not a stock icon on every TEL.
        """
        conceal_country(*countries)

    def _threat_rings(
        self, *, sa2_pos: Point, sa6_pos: Point, sa8_pos: Point
    ) -> tuple[ThreatRing, ...]:
        """The three belts as envelopes, for both the drawn plan and AI routing.

        One set of radii, two consumers: what `_draw_plan` paints as the
        estimated ring is exactly what the AI package flies around, so the
        briefing and the friendly flight plan can never disagree. The EWRs are
        not here — they cannot shoot, so nothing needs to route around them.
        """
        return (
            ThreatRing(sa2_pos, 40_000.0, "SA-2 belt"),
            ThreatRing(sa6_pos, 25_000.0, "SA-6 belt"),
            ThreatRing(sa8_pos, 10_000.0, "SA-8 belt"),
        )

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        belts: tuple[ThreatRing, ...],
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
        for belt in belts:
            plan.threat(
                belt.position,
                radius=belt.radius_m,
                label=belt.label,
                icon=StandardIcon.AirDefense,
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
        recognise the launch in 3–7 s), the SA-2 crew is a conscript belt that
        misses the call almost a third of the time, and the EWRs — furthest
        from the shooter, and not the ones being shot at — react slowest and
        come back up soonest. A suppressed site stays dark ~4–6 min, long
        enough that a HARM buys the package a real run at the column instead
        of a one-minute gap.
        """
        sites = [
            ArmSite(
                sa6,
                "SA-6",
                probability=0.9,
                delay_s=(3.0, 7.0),
                shutdown_s=(280.0, 400.0),
            ),
            ArmSite(
                sa8,
                "SA-8",
                probability=0.85,
                delay_s=(2.0, 5.0),
                shutdown_s=(220.0, 320.0),
                react_range_m=40_000.0,
            ),
            ArmSite(
                sa2,
                "SA-2",
                probability=0.7,
                delay_s=(5.0, 11.0),
                shutdown_s=(260.0, 380.0),
            ),
            *[
                ArmSite(
                    ewr,
                    "early-warning radar",
                    probability=0.75,
                    delay_s=(6.0, 14.0),
                    shutdown_s=(200.0, 300.0),
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
                f"Uzi, Hammer overhead the corridor, {_FREQ_FAC}.0 victor, "
                f"{_PRESET_FAC}. I have the column visual, eleven vehicles, "
                f"laser code {_LASER_CODE} on call."
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
            "Magic: that column is finished as a fighting unit, nothing left "
            "worth off-loading. Uzi, work what is left and RTB Hatay."
        )
        primary.add_action(action.MessageToAll(m.string(primary_call), seconds=20))
        self._voice.attach_to_all(m, primary, primary_call)
        m.triggerrules.triggers.append(primary)

        full = triggers.TriggerOnce(comment="Convoy destroyed, air threat cleared")
        full.add_condition(condition.GroupDead(convoy.id))
        full.add_condition(condition.GroupDead(migs.id))
        full_call = (
            "Magic: column destroyed, sky is clear over the corridor. "
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
            "Magic: the column made the Taftanaz off-load, they are unloading "
            "under the revetments. We missed the window. Uzi, egress west and "
            "RTB Hatay."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=20))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Break up the Syrian resupply column before it reaches the Taftanaz "
            "off-load. Suppress the SA-2, SA-6 and SA-8 belts covering the "
            "corridor — their crews drop emissions when they see a HARM and "
            "stay dark for minutes, so work the window. Pontiac is held "
            "in reserve and will run the column once the SAM threat over the "
            "route is suppressed. RTB Hatay."
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
