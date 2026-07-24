"""Caucasus 'Abkhaz Sweep' — F-16C ace air-superiority sweep.

Player flies a USAF F-16C-50 out of Batumi as `Dodge`. The frag is to sweep
the airspace off the Abkhaz coast between Sukhumi-Babushara and Gudauta
before AWACS `Magic` pushes its track north. Russian aggressor squadrons
operating out of Sochi-Adler and Gudauta are contesting the corridor. A
Russian SA-6 site on a coastal ridge between the two bases denies low
transit through the AO, forcing the player to fight high where the
bandits' R-27ER / R-77 have the reach.

No tanker, no escort, no Weasel — `Dodge` is alone tonight. F-16C internal
fuel with two wing tanks just covers the sortie; manage bingo aggressively.

Composition (difficulty: ace):
  - 4x Russian Su-27, Skill Excellent, R-27ER class, Sochi-Adler,
    intercept on an Abkhaz coastal intrusion zone.
  - 2x Russian MiG-29S, Skill Excellent, R-77 / R-27 class, Gudauta,
    reinforcement on a closer (north-of-Sukhumi) intrusion zone.
  - SA-6: 1x Kub 1S91 (Snow Drum) SR/TR + 2x Kub 2P25 launchers on a
    coastal ridge north of Sukhumi (Skill Excellent). Terminal SHORAD
    denies any push below ~4 km AGL over the AO.
  - 2x ZSU-23-4 Shilka at the SA-6 site (Skill High).
  - 1x 55G6 EWR inland east of Sochi-Adler (Skill Excellent), feeding GCI
    to the Su-27 element.
  - 1x 1L13 EWR north of Gudauta (Skill Excellent), feeding the MiG-29S
    reinforcement.
  - USA support: E-3A `Magic` AWACS only, 251.000 AM, Black Sea race-track
    south of the AO. No tanker, no escort, no SEAD wingman.
  - Weather: summer dawn — light NW wind, scattered cumulus 2400 m,
    24 km visibility, 22 C, QNH 760.
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
from dcs.unitgroup import VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.tasking import apply_ai_difficulty
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

    batumi: Airport
    sochi: Airport
    gudauta: Airport
    sukhumi: Airport
    sa6_site: Point
    shilka_pos: Point
    ewr_su27: Point
    ewr_mig29: Point
    push: Point
    station_south: Point
    station_north: Point
    egress: Point
    awacs_anchor: Point
    su27_intrusion: Point
    mig29_intrusion: Point


class AbkhazSweep(MissionBuilder):
    name = "abkhaz_sweep"
    title = "Abkhaz Sweep"
    difficulty = "ace"

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""ABKHAZ SWEEP — Caucasus, 18 Jul 2026, 05:30 local (dawn)
========================================================
SITUATION
  Russian aggressor squadrons operating from Sochi-Adler
  and Gudauta are contesting the Abkhaz coastal corridor.
  Magic AWACS cannot push its track north until the
  bandit CAP is broken.

  Command needs the corridor clean by sunrise so the
  Magic track can shift north and the strike packages
  tasked for first light can ingress unmolested.

MISSION (Dodge — F-16C-50, Batumi, hot ramp)
  - Push north up the Black Sea coast, take a sweep
    station offshore between Sukhumi and Gudauta.
  - Sanitize the airspace — kill the Su-27 four-ship out
    of Sochi-Adler, then the MiG-29S reinforcement out
    of Gudauta.
  - Stay ABOVE 4500 m over the AO — SA-6 on the coastal
    ridge north of Sukhumi denies the low block.
  - RTB Batumi. Divert: Senaki-Kolkhi.

PACKAGE
  Dodge 1 (you) : F-16C-50, Batumi, hot ramp, CAP frag.
                  Two wing tanks, AIM-120C / AIM-9X.
  Magic         : E-3A AWACS, 251.000 AM, Black Sea
                  race-track. No tanker, no escort, no
                  Weasel wingman. You are alone tonight.

THREATS
  Air : 4x Russian Su-27, Skill Excellent, R-27ER class,
        Sochi-Adler. Late-activated on the Abkhaz coastal
        intrusion zone.
        2x Russian MiG-29S, Skill Excellent, R-77 / R-27
        class, Gudauta. Late-activated on a closer (north
        of Sukhumi) intrusion zone — they roll in once
        Dodge has committed.
  SAM : 1x SA-6 (Kub 1S91 + 2x 2P25 launchers) on the
        coastal ridge north of Sukhumi, Skill Excellent.
        Engagement envelope ~24 km / 14 km altitude.
  AAA : 2x ZSU-23-4 Shilka at the SA-6 site.
  EWR : 1x 55G6 inland east of Sochi-Adler, Skill
        Excellent, vectoring the Su-27 element.
        1x 1L13 north of Gudauta, Skill Excellent,
        vectoring the MiG-29S element.

ROE / FRAGS
  - Weapons free on any Russian-coalition fighter inside
    the intrusion zones.
  - Do NOT descend below 4500 m AGL over the AO — Snow
    Drum will see you the moment you drop into its
    envelope.
  - Bingo fuel: 3500 lb. RTB Batumi direct (divert:
    Senaki-Kolkhi). Do not chase north of Gudauta on
    bingo.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  PUSH                : 40 km north of Batumi, over coast
  STATION_SOUTH       : offshore south of Sukhumi
  STATION_NORTH       : offshore north of Sukhumi
  EGRESS              : south back to Batumi

FREQUENCIES
  Magic AWACS  : 251.000 AM
  Batumi tower : per kneeboard

NOTES
  Sunrise ~05:25 local. Sun comes up over the mountains
  to the east — the Su-27 four-ship will be pushing south
  out of Sochi with the sun behind them. Manage your
  aspect before commit. Scattered cumulus base 2400 m,
  600 m thick — bandits can use the layer to mask their
  intercept geometry.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Abkhaz Sweep

**Theater:** Caucasus
**Date / time:** 18 July 2026, 05:30 local (dawn)
**Player aircraft:** F-16C-50 (`Dodge`), Batumi, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** ace
**Expected sortie length:** ~55 minutes

## Situation

Russian aggressor squadrons operating from Sochi-Adler and Gudauta are
contesting the Abkhaz coastal corridor. `Magic` AWACS cannot push its track
north until the bandit CAP is broken. Command needs the corridor clean by
sunrise so the AWACS track can shift north and the strike packages tasked
for first light can ingress unmolested.

## Mission

Push north out of Batumi up the Black Sea coast as `Dodge`, take a sweep
station offshore between Sukhumi and Gudauta, and sanitize the airspace.
Expect a Su-27 four-ship out of Sochi-Adler on the merge, then a MiG-29S
two-ship reinforcement out of Gudauta once you are committed. Stay above
4500 m over the AO — the SA-6 on the coastal ridge north of Sukhumi denies
the low block.

## Package

| Callsign | Type     | Base    | Role                              |
|----------|----------|---------|-----------------------------------|
| Dodge    | F-16C-50 | Batumi  | Player air-superiority sweep      |
| Magic    | E-3A     | Batumi  | AWACS, 251.000 AM, Black Sea track|

No tanker, no escort, no Weasel wingman — denied support is part of the
ace composition. Carry two wing tanks.

## Threats

- **Air (primary):** 4x Russian Su-27 (Skill Excellent), R-27ER class,
  Sochi-Adler. Late-activated on the Abkhaz coastal intrusion zone.
- **Air (reinforcement):** 2x Russian MiG-29S (Skill Excellent), R-77 / R-27
  class, Gudauta. Late-activated on a closer north-of-Sukhumi intrusion
  zone — they commit once `Dodge` is engaged.
- **SAM (terminal denial):** 1x SA-6 site on the coastal ridge north of
  Sukhumi (Kub 1S91 Snow Drum SR/TR + 2x 2P25 launchers), Skill Excellent.
  Engagement envelope ~24 km / 14 km altitude. Forces the fight above
  4500 m AGL.
- **AAA:** 2x ZSU-23-4 Shilka at the SA-6 site (Skill High).
- **EWR (Su-27 GCI):** 1x 55G6 inland east of Sochi-Adler (Skill Excellent).
- **EWR (MiG-29S GCI):** 1x 1L13 north of Gudauta (Skill Excellent).

## ROE

- Weapons free on any Russian-coalition fighter inside the intrusion zones.
- Do **not** descend below 4500 m AGL over the AO — Snow Drum sees you the
  moment you drop into its envelope.
- Bingo fuel: 3500 lb. RTB Batumi direct (divert: Senaki-Kolkhi). Do not
  chase north of Gudauta on bingo.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- PUSH: 40 km north of Batumi, over the coast
- STATION_SOUTH: offshore south of Sukhumi
- STATION_NORTH: offshore north of Sukhumi
- EGRESS: south back to Batumi

## Frequencies

- Magic AWACS: 251.000 AM
- Batumi tower: per kneeboard

## Weather

Summer dawn. Light NW wind 4 m/s ground, 8 m/s at 8000 m. 22 °C, QNH
760 mmHg. Visibility 24 km, scattered cumulus base 2400 m, 600 m thick.
Sunrise ~05:25 local — the Su-27 element pushes south with the sun
behind them over the eastern mountains.

## Difficulty composition

**Ace.** Excellent Su-27 + MiG-29S, bandits 6 vs player flight (3x for a
2-ship Dodge, 6x for a single-seat), R-77 / R-27ER class, SA-6 terminal
denial over the AO forcing the fight high, EWR-fed GCI on both bandit
flights, AWACS-only support (no tanker, no escort, no Weasel), low sun on
commit. One mistake ends the sortie.

## Win / loss conditions

- **Success:** all 4x Su-27 and all 2x MiG-29S are destroyed.
- **Failure:** `Dodge` flight is dead before the bandits are.

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

        _sa6, _shilkas, _ewr_su, _ewr_mig = self._spawn_red_ground(m, russia, scene)
        self._spawn_awacs(m, usa, scene)
        su27 = self._spawn_red_su27(m, russia, scene)
        mig29 = self._spawn_red_mig29(m, russia, scene)
        player = self._spawn_player(m, usa, scene)

        self._add_end_triggers(m, su27=su27, mig29=mig29, player=player)
        self._draw_plan(m, scene)
        self._add_briefing(m)

        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """05:30 local on 18 July 2026 (Caucasus is UTC+4)."""
        m.start_time = datetime(2026, 7, 18, 1, 30, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Summer dawn, scattered cumulus 2400 m, light NW wind, 22 C, 24 km vis."""
        w = m.weather
        w.season_temperature = 22.0
        w.qnh = 760
        w.wind_at_ground.direction = 310
        w.wind_at_ground.speed = 4
        w.wind_at_2000.direction = 305
        w.wind_at_2000.speed = 6
        w.wind_at_8000.direction = 295
        w.wind_at_8000.speed = 8
        w.clouds_base = 2400
        w.clouds_thickness = 600
        w.clouds_density = 4
        w.visibility_distance = 24000
        w.name = "Summer dawn scattered"

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Batumi for blue, Sochi/Gudauta/Sukhumi for red, derive AO geometry."""
        t = self._terrain
        batumi = t.airports["Batumi"]
        sochi = t.airports["Sochi-Adler"]
        gudauta = t.airports["Gudauta"]
        sukhumi = t.airports["Sukhumi-Babushara"]
        batumi.set_blue()
        sochi.set_red()
        gudauta.set_red()
        sukhumi.set_red()
        # SA-6 on a coastal ridge ~10 km north of Sukhumi, with LOS over the
        # AO offshore. Launchers a few hundred metres south of the radar.
        sa6_site = _offset(sukhumi.position, t, east_m=-2_000, north_m=10_000)
        shilka_pos = _offset(sa6_site, t, east_m=300, north_m=300)
        # EWR sites inland of each bandit base.
        ewr_su27 = _offset(sochi.position, t, east_m=12_000, north_m=4_000)
        ewr_mig29 = _offset(gudauta.position, t, east_m=8_000, north_m=8_000)
        # Sweep stations sit offshore (west of the coast) just outside the
        # SA-6 envelope at altitude (player stays above 4500 m).
        push = _offset(batumi.position, t, east_m=-15_000, north_m=40_000)
        station_south = _offset(sukhumi.position, t, east_m=-35_000, north_m=-15_000)
        station_north = _offset(sukhumi.position, t, east_m=-30_000, north_m=25_000)
        egress = _offset(batumi.position, t, east_m=-15_000, north_m=20_000)
        awacs_anchor = _offset(batumi.position, t, east_m=-25_000, north_m=15_000)
        su27_intrusion = _offset(sukhumi.position, t, east_m=-25_000, north_m=20_000)
        mig29_intrusion = _offset(sukhumi.position, t, east_m=-20_000, north_m=35_000)
        return _Scene(
            batumi=batumi,
            sochi=sochi,
            gudauta=gudauta,
            sukhumi=sukhumi,
            sa6_site=sa6_site,
            shilka_pos=shilka_pos,
            ewr_su27=ewr_su27,
            ewr_mig29=ewr_mig29,
            push=push,
            station_south=station_south,
            station_north=station_north,
            egress=egress,
            awacs_anchor=awacs_anchor,
            su27_intrusion=su27_intrusion,
            mig29_intrusion=mig29_intrusion,
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 radar + launchers on coastal ridge, Shilka SHORAD, EWR chain."""
        sa6 = self._spawn_sa6_site(m, russia, scene.sa6_site)
        shilkas = self._spawn_shilkas(m, russia, scene.shilka_pos)
        ewr_su27 = self._spawn_ewr(
            m, russia, scene.ewr_su27, "Box Spring 1", vehicles.AirDefence.X_55G6_EWR
        )
        ewr_mig29 = self._spawn_ewr(
            m, russia, scene.ewr_mig29, "Box Spring 2", vehicles.AirDefence.X_1L13_EWR
        )
        return sa6, shilkas, ewr_su27, ewr_mig29

    def _spawn_sa6_site(self, m: Mission, russia: Country, pos: Point):
        """SA-6 site: 1S91 (Snow Drum) + 2x 2P25 launchers in one group.

        The Kub launchers only engage while the 1S91 (`units[0]`) shares their
        group, so radar and TELs must not be split — kill the 1S91 and the
        whole site goes blind.
        """
        sa6_types = [
            vehicles.AirDefence.Kub_1S91_str,
            vehicles.AirDefence.Kub_2P25_ln,
            vehicles.AirDefence.Kub_2P25_ln,
        ]
        sa6 = m.vehicle_group_platoon(
            russia,
            "SAM Snow Drum",
            cast(list[type[VehicleType]], sa6_types),
            position=pos,
            heading=180,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(sa6, Skill.Excellent)
        return sa6

    def _spawn_shilkas(self, m: Mission, russia: Country, pos: Point):
        """2x ZSU-23-4 inside the SAM perimeter — terminal AAA coverage."""
        shilkas = m.vehicle_group(
            russia,
            "AAA Snow Drum-23",
            vehicles.AirDefence.ZSU_23_4_Shilka,
            position=pos,
            heading=180,
            group_size=2,
            formation=VehicleGroup.Formation.Scattered,
        )
        _set_skill(shilkas, Skill.High)
        return shilkas

    def _spawn_ewr(self, m: Mission, russia: Country, pos: Point, name: str, ewr_type):
        """Single EWR site feeding GCI to one of the bandit flights."""
        ewr = m.vehicle_group(
            russia,
            f"EWR {name}",
            ewr_type,
            position=pos,
            heading=180,
        )
        _set_skill(ewr, Skill.Excellent)
        return ewr

    def _spawn_red_su27(self, m: Mission, russia: Country, scene: _Scene):
        """4x Su-27 out of Sochi-Adler, intercept on Abkhaz coastal zone."""
        zone = m.triggers.add_triggerzone(
            position=scene.su27_intrusion,
            radius=45_000,
            hidden=True,
            name="Su-27 intrusion",
        )
        ivan = m.intercept_flight(
            russia,
            "Ivan",
            planes.Su_27,
            airport=scene.sochi,
            zone=zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=470,
            altitude=8000,
            max_engage_distance=120_000,
            group_size=4,
        )
        _set_skill(ivan, Skill.Excellent)
        apply_ai_difficulty(ivan, self.difficulty)
        announce = triggers.TriggerOnce(comment="Su-27 launch announcement")
        announce.add_condition(condition.PartOfCoalitionInZone("blue", zone.id))
        su27_call = (
            "Magic, Dodge. Four Sukhoi 27 airborne from Sochi-Adler, "
            "bearing 180, vectoring on the coast."
        )
        announce.add_action(
            action.MessageToCoalition(
                action.Coalition.Blue, m.string(su27_call), seconds=15
            )
        )
        self._voice.attach_to_coalition(m, announce, su27_call, coalition="blue")
        m.triggerrules.triggers.append(announce)
        return ivan

    def _spawn_red_mig29(self, m: Mission, russia: Country, scene: _Scene):
        """2x MiG-29S out of Gudauta, reinforcement on north-of-Sukhumi zone."""
        zone = m.triggers.add_triggerzone(
            position=scene.mig29_intrusion,
            radius=30_000,
            hidden=True,
            name="MiG-29 intrusion",
        )
        boris = m.intercept_flight(
            russia,
            "Boris",
            planes.MiG_29S,
            airport=scene.gudauta,
            zone=zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=490,
            altitude=8500,
            max_engage_distance=100_000,
            group_size=2,
        )
        _set_skill(boris, Skill.Excellent)
        apply_ai_difficulty(boris, self.difficulty)
        announce = triggers.TriggerOnce(comment="MiG-29 launch announcement")
        announce.add_condition(condition.PartOfCoalitionInZone("blue", zone.id))
        mig_call = (
            "Magic, Dodge. Two MiG-29 airborne from Gudauta, "
            "bearing 200, R-77 class, committing south."
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
        """E-3A Magic on a Black Sea race-track south of the AO, 251.000 AM."""
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.batumi,
            position=scene.awacs_anchor,
            race_distance=90_000,
            heading=300,
            altitude=8500,
            speed=410,
            start_type=StartType.Warm,
            frequency=251,
        )

    def _spawn_player(self, m: Mission, usa: Country, scene: _Scene):
        """Dodge F-16C-50 from Batumi, hot ramp; sweep stations offshore."""
        player = m.flight_group_from_airport(
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.batumi,
            maintask=task.CAP,
            start_type=StartType.Warm,
            group_size=self.players,
        )
        _mark_clients(player)

        player.add_runway_waypoint(scene.batumi)
        player.add_waypoint(scene.push, altitude=6000, speed=400, name="PUSH")
        player.add_waypoint(
            scene.station_south, altitude=7500, speed=420, name="STATION_SOUTH"
        )
        player.add_waypoint(
            scene.station_north, altitude=7500, speed=420, name="STATION_NORTH"
        )
        player.add_waypoint(scene.egress, altitude=5000, speed=420, name="EGRESS")
        player.add_runway_waypoint(scene.batumi)
        player.land_at(scene.batumi)
        return player

    # -- F10 map briefing ---------------------------------------------------

    def _draw_plan(self, m: Mission, scene: _Scene) -> None:
        """Paint the plan on the F10 map (ace: friendly plan + a vague threat zone).

        Ace reveals no enemy positions — the player builds the picture off RWR,
        Magic, and the tally. Only the sweep geometry and one coarse threat
        area (the SA-6 ridge / bandit CAP off Sukhumi) are drawn.
        """
        plan = PlanOverlay(m, self.difficulty)
        ao = scene.station_south.midpoint(scene.station_north)
        plan.objective(ao, "Sweep AO", radius=8_000.0)
        plan.route(
            [scene.push, scene.station_south, scene.station_north, scene.egress],
            "Dodge sweep",
        )
        plan.waypoint_label(scene.awacs_anchor, "Magic AWACS")
        plan.threat_area(
            scene.sukhumi.position, 28_000.0, "SA-6 + bandit CAP — vicinity"
        )

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(self, m: Mission, *, su27, mig29, player) -> None:
        """Success when both bandit flights dead; failure when Dodge dies first."""
        success = triggers.TriggerOnce(comment="Bandits all dead")
        success.add_condition(condition.GroupDead(su27.id))
        success.add_condition(condition.GroupDead(mig29.id))
        success_call = (
            "Corridor is clean. Mission successful. Dodge, return to base, Batumi. "
            "Magic is pushing the track north."
        )
        success.add_action(action.MessageToAll(m.string(success_call), seconds=25))
        self._voice.attach_to_all(m, success, success_call)
        m.triggerrules.triggers.append(success)

        failure = triggers.TriggerOnce(comment="Dodge lost")
        failure.add_condition(condition.GroupDead(player.id))
        failure_call = (
            "Dodge is down. Mission failed. Magic, hold the southern track. "
            "Strike packages aborting."
        )
        failure.add_action(action.MessageToAll(m.string(failure_call), seconds=25))
        self._voice.attach_to_all(m, failure, failure_call)
        m.triggerrules.triggers.append(failure)

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Sweep the airspace off the Abkhaz coast between Sukhumi and "
            "Gudauta. Kill the Russian Su-27 four-ship out of Sochi-Adler "
            "and the MiG-29S reinforcement out of Gudauta. Stay above "
            "4500 m AGL over the AO — the SA-6 on the coastal ridge north "
            "of Sukhumi denies the low block. RTB Batumi. No tanker, no "
            "escort."
        )
        m.set_description_redtask_text(
            "Hold the Abkhaz coastal airspace. Su-27 from Sochi-Adler "
            "intercept on the southern intrusion zone; MiG-29S from Gudauta "
            "reinforce once USAF commits. SA-6 on the coastal ridge north "
            "of Sukhumi denies the low block."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Caucasus 'Abkhaz Sweep' ace air-superiority mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/abkhaz_sweep"),
        help="Output directory for the .miz and README.md (default: out/abkhaz_sweep)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of coop client slots in Dodge flight (default: 1)",
    )
    args = parser.parse_args()
    miz, readme = AbkhazSweep(players=args.players).generate(args.output_dir)
    print(f"wrote {miz}")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()
