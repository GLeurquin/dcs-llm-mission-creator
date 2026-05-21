"""Caucasus 'Daryal Run' — F-16C ace SEAD strike with low valley ingress.

Player flies a USAF F-16C-50 out of Vaziani as `Dodge`. The target is a
Russian S-300PS (SA-10) battery emplaced south of Beslan, covering the
North Caucasus from a ridge that denies any high-altitude approach. The
only viable ingress is a low-level run north up the Daryal Gorge between
Mt Kazbek (5033 m) and the eastern ridge — terrain-masked from the Big
Bird radar until the player pops up for the HARM shot. AWACS `Magic` holds
a southern race-track. No tanker, no escort, no SEAD support.

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

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from dcs import action, condition, planes, task, triggers, vehicles
from dcs.country import Country
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unittype import VehicleType

from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.tts import VoiceSynth


def _offset(
    origin: Point, terrain: Caucasus, *, east_m: float = 0, north_m: float = 0
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
    """Resolved airports + key positions used by every spawn step."""

    vaziani: Airport
    mozdok: Airport
    beslan: Airport
    sa10_site: Point
    sa10_launchers: Point
    shorad: Point
    ewr_pos: Point
    valley_entry: Point
    valley_mid: Point
    valley_exit: Point
    ip: Point
    awacs_anchor: Point
    intrusion_center: Point


class DaryalRun(MissionBuilder):
    name = "daryal_run"
    title = "Daryal Run"

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
  A Russian S-300PS battery has emplaced south of Beslan,
  shutting down the airspace over the North Caucasus from
  a ridge that denies any high-altitude push. Command
  needs the Big Bird and Flap Lid radars off the air
  tonight, before the layer thickens overnight.

  The only viable ingress is low. You will fly the Georgian
  Military Road north, drop into the Daryal Gorge between
  Mt Kazbek and the eastern ridge, and stay masked until
  the pop-up. The valley narrows to roughly 3 km in
  places — pick your line.

MISSION (Dodge — F-16C-50, Vaziani, hot ramp)
  - Push north, descend before the foothills.
  - Ingress the Daryal Gorge below 1000 m AGL.
  - Pop up at the IP south of Beslan, HARM the Big Bird,
    re-attack the Flap Lid and the 54K6 CP. Launchers
    are bonus; the radars are the kill.
  - Egress WEST, then south around the western ridges.
    Do NOT re-cross Daryal — the MiG-29S will be in by then.
  - RTB Vaziani. Divert: Soganlug.

PACKAGE
  Dodge 1 (you) : F-16C-50, Vaziani, hot ramp, SEAD frag.
  Magic         : E-3A AWACS, 251.000 AM, race-track over
                  Georgia. No tanker, no escort, no Weasel
                  wingman. You are alone tonight.

THREATS
  Air : 2x Russian MiG-29S, Skill Excellent, R-77 class.
        Mozdok. Late-activated when blue enters the
        intrusion zone south of Beslan.
  SAM : 1x S-300PS (SA-10) battery, Skill Excellent.
        SR 64H6E (Big Bird), TR 30H6 (Flap Lid), CP 54K6,
        4x 5P85C/D launchers. Engagement envelope ~75 km.
        1x SA-15 Tor terminal SHORAD, Skill Excellent.
  AAA : 2x ZSU-23-4 Shilka at the SAM site.
  EWR : 1x 1L13 on a ridge near Mozdok, Skill Excellent.

ROE / FRAGS
  - Weapons free on the SA-10 cluster and any Russian
    aircraft entering the intrusion zone.
  - Keep below 1000 m AGL inside Daryal Gorge until the
    pop-up — Big Bird sees you the moment you crest.
  - Bingo fuel: 2500 lb. RTB Vaziani via the western
    egress, not back through Daryal.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  PUSH                : 25 km NNW of Vaziani.
  DESCEND             : 60 km N of Vaziani, foothills.
  VALLEY_IN           : Stepantsminda / Kazbegi area.
  VALLEY_OUT          : just south of Vladikavkaz.
  TARGET              : 12 km south of Beslan airfield.

FREQUENCIES
  Magic AWACS   : 251.000 AM
  Vaziani tower : per kneeboard

NOTES
  Sunset ~18:40 local. The valley will be in shadow before
  you reach the IP. Broken layer base 2200 m — do not climb
  through it on the western egress without checking your six.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Daryal Run

**Theater:** Caucasus
**Date / time:** 12 October 2026, 18:15 local (dusk)
**Player aircraft:** F-16C-50 (`Dodge`), Vaziani, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** ace
**Expected sortie length:** ~55 minutes

## Situation

A Russian S-300PS (SA-10) battery has emplaced south of Beslan, shutting
down the North Caucasus airspace from a ridge that denies any high-altitude
push. Command needs the Big Bird and Flap Lid radars off the air tonight,
before the cloud layer thickens overnight.

The only viable ingress is low. `Dodge` flies the Georgian Military Road
north, drops into the **Daryal Gorge** between Mt Kazbek (5033 m) and the
eastern ridge, and stays masked until the pop-up. The valley narrows to
roughly 3 km in places — pick your line.

## Mission

Push north out of Vaziani, descend before the foothills, ingress Daryal
below 1000 m AGL, pop up at the IP south of Beslan, HARM the Big Bird,
re-attack the Flap Lid and the 54K6 CP. Egress west, then south around the
western ridges. Do **not** re-cross Daryal on egress — the MiG-29S CAP
will be airborne by then.

## Package

| Callsign | Type     | Base    | Role                           |
|----------|----------|---------|--------------------------------|
| Dodge    | F-16C-50 | Vaziani | Player SEAD strike (frag SA-10)|
| Magic    | E-3A     | Vaziani | AWACS, 251.000 AM, south of mtns|

No tanker, no escort, no Weasel wingman — denied support is part of the
ace composition. Carry externals.

## Threats

- **SAM (boss):** 1x S-300PS battery (Skill Excellent). SR 64H6E (Big
  Bird), TR 30H6 (Flap Lid), CP 54K6, 4x 5P85C/D launchers. Engagement
  envelope ~75 km against a fast jet at altitude.
- **Terminal SHORAD:** 1x SA-15 Tor (Skill Excellent) adjacent to the
  SA-10 cluster — closes the bubble at low altitude.
- **AAA:** 2x ZSU-23-4 Shilka at the SAM site (Skill High).
- **EWR:** 1x 1L13 on a ridge near Mozdok (Skill Excellent), feeding GCI
  to the MiG-29S.
- **Air:** 2x Russian MiG-29S out of Mozdok (Skill Excellent), R-77 / R-27
  class, late-activated on an intrusion zone just south of Beslan.

## ROE

- Weapons free on the SA-10 cluster and any Russian aircraft entering the
  intrusion zone.
- Stay below 1000 m AGL inside Daryal Gorge until the pop-up — Big Bird
  sees you the moment you crest.
- Bingo fuel: 2500 lb. RTB Vaziani via the western egress, **not** back
  through Daryal.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- PUSH: 25 km NNW of Vaziani
- DESCEND: 60 km N of Vaziani, foothills
- VALLEY_IN: Stepantsminda / Kazbegi area
- VALLEY_OUT: just south of Vladikavkaz
- TARGET: 12 km south of Beslan airfield

## Frequencies

- Magic AWACS: 251.000 AM
- Vaziani tower: per kneeboard

## Weather

Autumn dusk, broken layer base 2200 m, 800 m thick, density 6.
Light north wind 4 m/s ground, 8 m/s at 8000 m. 12 °C, QNH 760 mmHg.
Visibility 30 km (haze). Sunset ~18:40 local — the valley will be in
shadow by the time you reach the IP.

## Difficulty composition

**Ace.** Excellent SA-10 + SA-15 + EWR, Excellent MiG-29S CAP, bandits
2x player flight, R-77 class missiles, AWACS-only support (no tanker, no
escort, no Weasel wingman), dusk with broken layer, valley-only viable
ingress, west-only viable egress. One mistake ends the sortie.

## Win / loss conditions

- **Success:** SA-10 SR (Big Bird) and TR (Flap Lid) are both destroyed.
- **Failure:** `Dodge` flight is dead before the SA-10 radars are.

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

        sa10_radars, _sa10_launchers, _tor, _shilkas, _ewr = self._spawn_red_ground(
            m, russia, scene
        )
        self._spawn_awacs(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        player = self._spawn_player(m, usa, scene)

        self._add_end_triggers(m, sa10_radars=sa10_radars, player=player)
        self._add_briefing(m)

        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """18:15 local on 12 October 2026 (Caucasus is UTC+4)."""
        m.start_time = datetime(2026, 10, 12, 14, 15, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Autumn dusk, broken layer at 2200 m, light N wind, 12 C, 30 km visibility."""
        w = m.weather
        w.season_temperature = 12.0
        w.qnh = 760
        w.wind_at_ground.direction = 0
        w.wind_at_ground.speed = 4
        w.wind_at_2000.direction = 10
        w.wind_at_2000.speed = 6
        w.wind_at_8000.direction = 350
        w.wind_at_8000.speed = 8
        w.clouds_base = 2200
        w.clouds_thickness = 800
        w.clouds_density = 6
        w.visibility_distance = 30000
        w.name = "Autumn dusk"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Vaziani for blue, Mozdok for red, derive valley + target geometry."""
        t = self._terrain
        vaziani = t.airports["Vaziani"]
        mozdok = t.airports["Mozdok"]
        beslan = t.airports["Beslan"]
        vaziani.set_blue()
        mozdok.set_red()
        beslan.set_red()

        # SA-10 cluster sits ~12 km south of Beslan, in the open ground east
        # of Vladikavkaz where the SR has a clear horizon to the south.
        sa10_site = _offset(beslan.position, t, east_m=2_000, north_m=-12_000)
        sa10_launchers = _offset(sa10_site, t, east_m=1_500, north_m=800)
        shorad = _offset(sa10_site, t, east_m=-1_200, north_m=-600)
        ewr_pos = _offset(mozdok.position, t, east_m=-8_000, north_m=-6_000)

        # Valley waypoints: Stepantsminda → mid-gorge → Vladikavkaz south.
        # Caucasus convention: Point(x = north, y = east), Vaziani sits at
        # (-319065, 903148), Beslan at (-148590, 843668).
        valley_entry = Point(-225000, 873000, t)
        valley_mid = Point(-200000, 863000, t)
        valley_exit = Point(-170000, 851000, t)
        ip = Point(-158000, 846000, t)

        awacs_anchor = _offset(vaziani.position, t, east_m=-25_000, north_m=15_000)
        intrusion_center = _offset(beslan.position, t, east_m=0, north_m=-8_000)

        return _Scene(
            vaziani=vaziani,
            mozdok=mozdok,
            beslan=beslan,
            sa10_site=sa10_site,
            sa10_launchers=sa10_launchers,
            shorad=shorad,
            ewr_pos=ewr_pos,
            valley_entry=valley_entry,
            valley_mid=valley_mid,
            valley_exit=valley_exit,
            ip=ip,
            awacs_anchor=awacs_anchor,
            intrusion_center=intrusion_center,
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """SA-10 radars + launchers, SA-15 SHORAD, ZSU-23-4 AAA, 1L13 EWR."""
        sa10_radars = self._spawn_sa10_radars(m, russia, scene.sa10_site)
        sa10_launchers = self._spawn_sa10_launchers(m, russia, scene.sa10_launchers)
        tor = self._spawn_shorad(m, russia, scene.shorad)
        shilkas = self._spawn_shilkas(m, russia, scene.sa10_site)
        ewr = self._spawn_ewr(m, russia, scene.ewr_pos)
        return sa10_radars, sa10_launchers, tor, shilkas, ewr

    def _spawn_sa10_radars(self, m: Mission, russia: Country, pos: Point):
        """64H6E (Big Bird) SR + 30H6 Flap Lid TR + 54K6 CP — kill these to win."""
        radar_types = [
            vehicles.AirDefence.S_300PS_64H6E_sr,
            vehicles.AirDefence.S_300PS_5H63C_30H6_tr,
            vehicles.AirDefence.S_300PS_54K6_cp,
        ]
        radars = m.vehicle_group_platoon(
            russia,
            "SAM Grumble Radars",
            cast(list[type[VehicleType]], radar_types),
            position=pos,
            heading=180,
        )
        _set_skill(radars, Skill.Excellent)
        return radars

    def _spawn_sa10_launchers(self, m: Mission, russia: Country, pos: Point):
        """4x 5P85C/D launchers — bonus targets; radars are the kill."""
        launcher_types = [
            vehicles.AirDefence.S_300PS_5P85C_ln,
            vehicles.AirDefence.S_300PS_5P85C_ln,
            vehicles.AirDefence.S_300PS_5P85D_ln,
            vehicles.AirDefence.S_300PS_5P85D_ln,
        ]
        launchers = m.vehicle_group_platoon(
            russia,
            "SAM Grumble Launchers",
            cast(list[type[VehicleType]], launcher_types),
            position=pos,
            heading=180,
        )
        _set_skill(launchers, Skill.Excellent)
        return launchers

    def _spawn_shorad(self, m: Mission, russia: Country, pos: Point):
        """SA-15 Tor adjacent to the SAM site — terminal SHORAD vs HARM and bombs."""
        tor = m.vehicle_group(
            russia,
            "SAM Tor",
            vehicles.AirDefence.Tor_9A331,
            position=pos,
            heading=180,
        )
        _set_skill(tor, Skill.Excellent)
        return tor

    def _spawn_shilkas(self, m: Mission, russia: Country, site: Point):
        """2x ZSU-23-4 inside the SAM perimeter — close-in AAA pop-up coverage."""
        pos = _offset(site, self._terrain, east_m=-300, north_m=400)
        shilkas = m.vehicle_group(
            russia,
            "AAA Bear-23",
            vehicles.AirDefence.ZSU_23_4_Shilka,
            position=pos,
            heading=180,
            group_size=2,
        )
        _set_skill(shilkas, Skill.High)
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
        _set_skill(ewr, Skill.Excellent)
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
            speed=480,
            altitude=8000,
            max_engage_distance=110_000,
            group_size=2,
        )
        _set_skill(boris, Skill.Excellent)
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
            speed=410,
            start_type=StartType.Warm,
            frequency=251,
        )

    def _spawn_player(self, m: Mission, usa: Country, scene: _Scene):
        """Dodge F-16C-50 from Vaziani, hot ramp; valley ingress + western egress."""
        player = m.flight_group_from_airport(
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.vaziani,
            maintask=task.SEAD,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        _mark_clients(player)

        t = self._terrain
        v = scene.vaziani.position

        player.add_runway_waypoint(scene.vaziani)
        player.add_waypoint(
            _offset(v, t, east_m=-8_000, north_m=25_000),
            altitude=3000,
            speed=380,
            name="PUSH",
        )
        player.add_waypoint(
            _offset(v, t, east_m=-13_000, north_m=60_000),
            altitude=1500,
            speed=360,
            name="DESCEND",
        )
        player.add_waypoint(
            scene.valley_entry, altitude=800, speed=340, name="VALLEY_IN"
        )
        player.add_waypoint(
            scene.valley_mid, altitude=700, speed=340, name="VALLEY_MID"
        )
        player.add_waypoint(
            scene.valley_exit, altitude=800, speed=340, name="VALLEY_OUT"
        )
        player.add_waypoint(scene.ip, altitude=600, speed=360, name="IP")
        player.add_waypoint(scene.sa10_site, altitude=1500, speed=380, name="TARGET")
        # Egress: west, then south around the western ridges. Do NOT re-cross Daryal.
        player.add_waypoint(
            Point(-165000, 815000, t), altitude=1500, speed=400, name="EGRESS_W"
        )
        player.add_waypoint(
            Point(-240000, 830000, t), altitude=4500, speed=420, name="EGRESS_S"
        )
        player.add_runway_waypoint(scene.vaziani)
        player.land_at(scene.vaziani)
        return player

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(self, m: Mission, *, sa10_radars, player) -> None:
        """Success when SA-10 radars dead; failure when Dodge dies first."""
        success = triggers.TriggerOnce(comment="SA-10 radars destroyed")
        success.add_condition(condition.GroupDead(sa10_radars.id))
        success_call = (
            "Big Bird and Flap Lid are off the air. Mission successful. "
            "Dodge, egress west and return to base, Vaziani. Do not re-cross Daryal."
        )
        success.add_action(action.MessageToAll(m.string(success_call), seconds=25))
        self._voice.attach_to_all(m, success, success_call)
        m.triggerrules.triggers.append(success)

        failure = triggers.TriggerOnce(comment="Dodge lost before kill")
        failure.add_condition(condition.GroupDead(player.id))
        failure.add_condition(condition.GroupAlive(sa10_radars.id))
        failure_call = "Dodge is down and the S-300 is still tracking. Mission failed."
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=25))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

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
            "intercept any USAF push entering the intrusion zone."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Caucasus 'Daryal Run' ace SEAD mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/daryal_run"),
        help="Output directory for the .miz and README.md (default: out/daryal_run)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Dodge flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = DaryalRun(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
