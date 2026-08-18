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

from dataclasses import dataclass
from datetime import datetime, timezone

from dcs import action, condition, planes, task, templates, triggers, vehicles
from dcs.country import Country
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup

from dcs_mission_creator.core import air_defense as ad, triggers as mission_triggers
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import arm, mark_clients, offset, set_skill
from dcs_mission_creator.core.placement import load_scene
from dcs_mission_creator.core.tasking import apply_ai_difficulty
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene


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
    overlay: TacticalScene


class AbkhazSweep(MissionBuilder):
    name = "abkhaz_sweep"
    title = "Abkhaz Sweep"
    difficulty = Difficulty.ACE

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

INTELLIGENCE
  The picture is thin. No overhead since yesterday
  morning and nothing airborne up there tonight — what
  follows is assessment, not fact. Build your own
  picture off Magic and the RWR.
  Air : Sochi-Adler flies the aggressor syllabus and has
        been putting up four-ships. Expect that, with the
        long-burn R-27 variant. Gudauta keeps a lighter
        pair that has reinforced every previous
        engagement once we were committed — R-77 shooters.
        Both fields are crewed by their best.
  SAM : A Kub battery is assessed on the coastal ridge
        north of Sukhumi. We have no current fix and it
        moves, so assume the low block is denied
        anywhere over the AO, and assume guns with it.
  EWR : Early-warning radar covers the whole corridor
        from inland. You will be seen from the coast in,
        and both fields will be vectored onto you.

ROE / FRAGS
  - Weapons free on any Russian fighter inside the
    coastal corridor.
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
Expect a Su-27 four-ship out of Sochi-Adler on the merge, then a MiG-29S pair
reinforcing out of Gudauta once you are committed. Stay above 4500 m over the
AO — the Kub battery assessed on the coastal ridge north of Sukhumi denies the
low block.

## Package

| Callsign | Type     | Base    | Role                              |
|----------|----------|---------|-----------------------------------|
| Dodge    | F-16C-50 | Batumi  | Player air-superiority sweep      |
| Magic    | E-3A     | Batumi  | AWACS, 251.000 AM, Black Sea track|

No tanker, no escort, no Weasel wingman — denied support is part of the
ace composition. Carry two wing tanks.

## Intelligence

The picture is thin — no overhead since yesterday morning, nothing airborne
up there tonight. Everything below is assessment. There are no enemy
positions on your map: build the picture off `Magic`, the RWR and the tally.

- **Air (primary):** Sochi-Adler flies the aggressor syllabus and has been
  putting up four-ships of Su-27, carrying the long-burn R-27 variant. Their
  best crews.
- **Air (reinforcement):** Gudauta keeps a lighter MiG-29S pair that has
  reinforced every previous engagement once we were committed — R-77
  shooters.
- **SAM (terminal denial):** a Kub battery is assessed on the coastal ridge
  north of Sukhumi. No current fix, and it relocates, so assume the low block
  is denied anywhere over the AO and assume guns are sited with it. That is
  what forces the fight above 4500 m AGL, where the bandits want it.
- **EWR:** early-warning radar covers the corridor from inland. You are seen
  from the coast in, and both fields get vectored onto you.

## ROE

- Weapons free on any Russian fighter inside the coastal corridor.
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

- **Success:** the corridor is swept clean — no Russian fighter left flying
  between Sukhumi and Gudauta.
- **Failure:** `Dodge` goes down with the corridor still contested.

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

        _sa6, _shilkas, _ewr_su, _ewr_mig = self._spawn_red_ground(m, russia, scene)
        self._spawn_awacs(m, usa, scene)
        su27 = self._spawn_red_su27(m, russia, scene)
        mig29 = self._spawn_red_mig29(m, russia, scene)
        player = self._spawn_player(m, usa, scene)

        self._add_end_triggers(m, su27=su27, mig29=mig29, player=player)
        self._conceal_red(russia)
        self._draw_plan(m, scene)
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """05:30 map-local on 18 July 2026 — dawn, the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 7, 18, 5, 30, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Summer dawn, scattered cumulus 2400 m, light NW wind, 22 C, 24 km vis."""
        Weather(
            name="Summer dawn scattered",
            season_temperature=22.0,
            clouds_base=2400,
            clouds_thickness=600,
            clouds_density=4,
            visibility_distance=24000,
            wind_at_ground=Wind(310, 4),
            wind_at_2000=Wind(305, 6),
            wind_at_8000=Wind(295, 8),
        ).apply(m)

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
        sa6_site = offset(sukhumi.position, east_m=-2_000, north_m=10_000)
        shilka_pos = offset(sa6_site, east_m=300, north_m=300)
        # EWR sites inland of each bandit base.
        ewr_su27 = offset(sochi.position, east_m=12_000, north_m=4_000)
        ewr_mig29 = offset(gudauta.position, east_m=8_000, north_m=8_000)
        # Sweep stations sit offshore (west of the coast) just outside the
        # SA-6 envelope at altitude (player stays above 4500 m).
        push = offset(batumi.position, east_m=-15_000, north_m=40_000)
        station_south = offset(sukhumi.position, east_m=-35_000, north_m=-15_000)
        station_north = offset(sukhumi.position, east_m=-30_000, north_m=25_000)
        egress = offset(batumi.position, east_m=-15_000, north_m=20_000)
        awacs_anchor = offset(batumi.position, east_m=-25_000, north_m=15_000)
        su27_intrusion = offset(sukhumi.position, east_m=-25_000, north_m=20_000)
        mig29_intrusion = offset(sukhumi.position, east_m=-20_000, north_m=35_000)
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
            overlay=load_scene("caucasus"),
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 radar + launchers on coastal ridge, Shilka SHORAD, EWR chain."""
        sa6 = self._spawn_sa6_site(m, russia, scene)
        shilkas = self._spawn_shilkas(m, russia, scene.shilka_pos)
        ewr_su27 = self._spawn_ewr(
            m, russia, scene.ewr_su27, "Box Spring 1", vehicles.AirDefence.X_55G6_EWR
        )
        ewr_mig29 = self._spawn_ewr(
            m, russia, scene.ewr_mig29, "Box Spring 2", vehicles.AirDefence.X_1L13_EWR
        )
        return sa6, shilkas, ewr_su27, ewr_mig29

    def _spawn_sa6_site(self, m: Mission, russia: Country, scene: _Scene):
        """SA-6 site: 1S91 (Snow Drum) + 2x 2P25 launchers in one group.

        The Kub launchers only engage while the 1S91 (`units[0]`) shares their
        group, so radar and TELs must not be split — kill the 1S91 and the
        whole site goes blind. Dispersed out of the template's 30 m huddle: at
        ace difficulty the site is on no map at all, so the player has to find
        it, and a heap that tight is one pass with a CBU once he has.
        """
        sa6 = templates.VehicleTemplate.sa6_site(
            m,
            russia,
            scene.sa6_site,
            heading=180,
            prefix="Snow Drum ",
            skill=Skill.Excellent,
        )
        return ad.disperse_site(
            sa6,
            radius_m=300.0,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )

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
        set_skill(shilkas, Skill.High)
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
        set_skill(ewr, Skill.Excellent)
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
            speed=920,
            altitude=8000,
            max_engage_distance=120_000,
            group_size=4,
        )
        set_skill(ivan, Skill.Excellent)
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
            speed=900,
            altitude=8500,
            max_engage_distance=100_000,
            group_size=2,
        )
        set_skill(boris, Skill.Excellent)
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
            speed=740,
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
        mark_clients(player)
        arm(
            player,
            planes.F_16C_50,
            [
                (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (2, "AIM_9X_Sidewinder_IR_AAM"),
                (3, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (4, "Fuel_tank_370_gal"),
                (6, "Fuel_tank_370_gal"),
                (7, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (8, "AIM_9X_Sidewinder_IR_AAM"),
                (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            ],
        )

        player.add_runway_waypoint(scene.batumi)
        player.add_waypoint(scene.push, altitude=6000, speed=800, name="PUSH")
        player.add_waypoint(
            scene.station_south, altitude=7500, speed=780, name="STATION_SOUTH"
        )
        player.add_waypoint(
            scene.station_north, altitude=7500, speed=780, name="STATION_NORTH"
        )
        player.add_waypoint(scene.egress, altitude=5000, speed=820, name="EGRESS")
        player.add_runway_waypoint(scene.batumi)
        player.land_at(scene.batumi)
        return player

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        Ace: the player is given a vague threat area and nothing else, so a
        stock icon on the SA-6 ridge would undo the whole reveal policy.
        """
        conceal_country(russia)

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
        mission_triggers.message_to_all(
            m,
            comment="Bandits all dead",
            conditions=(
                condition.GroupDead(su27.id),
                condition.GroupDead(mig29.id),
            ),
            voice=self._voice,
            text=(
                "Magic: picture is clean, nothing flying between Sukhumi and "
                "Gudauta. Dodge, return to base, Batumi. Magic is pushing the "
                "track north."
            ),
            seconds=25,
        )

        mission_triggers.message_to_all(
            m,
            comment="Dodge lost",
            conditions=(condition.GroupDead(player.id),),
            voice=self._voice,
            text=(
                "Magic: Dodge is down and the corridor is still theirs. Holding "
                "the southern track. First-light packages are aborting."
            ),
            seconds=25,
        )

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
            "intercept any USAF push up the coast; MiG-29S from Gudauta "
            "reinforce once the Americans are committed. SA-6 on the coastal "
            "ridge north of Sukhumi denies the low block."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(AbkhazSweep)


if __name__ == "__main__":
    main()
