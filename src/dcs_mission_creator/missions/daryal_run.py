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

from dataclasses import dataclass
from datetime import datetime, timezone

from dcs import action, condition, planes, task, templates, triggers, vehicles
from dcs.country import Country
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.unit import Skill

from dcs_mission_creator.core import triggers as mission_triggers, waypoints
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    arm,
    mark_clients,
    offset,
    set_skill,
    unit_of_type,
)
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

    vaziani: Airport
    mozdok: Airport
    beslan: Airport
    sa10_site: Point
    shorad: Point
    ewr_pos: Point
    valley_entry: Point
    valley_mid: Point
    valley_exit: Point
    ip: Point
    awacs_anchor: Point
    intrusion_center: Point
    overlay: TacticalScene


class DaryalRun(MissionBuilder):
    name = "daryal_run"
    title = "Daryal Run"
    difficulty = Difficulty.ACE

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
  ELINT has been reading a Big Bird and a Flap Lid south
  of Beslan for three days: a Russian S-300PS battery has
  emplaced on a ridge there and shut down the airspace
  over the North Caucasus to any high-altitude push. The
  bearings cross to within a few kilometres — good enough
  for a target area, not for a pinpoint. Command wants
  those radars off the air tonight, before the layer
  thickens overnight.

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

INTELLIGENCE
  No overhead of the site — cloud for two days. What we
  have is the ELINT cut and pattern-of-life, so treat
  every position below as approximate.
  SAM : S-300PS battery. Search radar, tracking radar,
        command post and launchers, reach out to about
        75 km at altitude. Their best crew — assume they
        are alert and assume terminal SHORAD is sited
        with them to close the low block, guns as well.
  Air : Mozdok holds a MiG-29S pair, R-77 shooters,
        experienced. They will launch once you are
        detected south of Beslan.
  EWR : Early-warning radar on a ridge near Mozdok feeds
        the fighters their picture.

ROE / FRAGS
  - Weapons free on the SA-10 cluster and any Russian
    aircraft that comes up against you north of
    the border.
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

ELINT has been reading a Big Bird and a Flap Lid south of Beslan for three
days: a Russian S-300PS (SA-10) battery has emplaced on a ridge there and
shut the North Caucasus airspace to any high-altitude push. The bearings
cross to within a few kilometres — good enough for a target area, not for a
pinpoint. Command wants those radars off the air tonight, before the cloud
layer thickens overnight.

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

## Intelligence

No overhead of the site — cloud for two days. What we have is the ELINT cut
and pattern-of-life, so every position below is approximate and your map
carries a target area rather than icons. Find the radars with the HTS.

- **SAM (boss):** the S-300PS battery — search radar, tracking radar, command
  post and launchers — reaching out to roughly 75 km against a fast jet at
  altitude. Their best crew; assume they are alert.
- **Terminal SHORAD:** assume a point-defence system sited with the battery
  to close the low block, and guns with it.
- **EWR:** early-warning radar on a ridge near Mozdok feeding the fighters.
- **Air:** a MiG-29S pair at Mozdok, R-77 shooters, experienced crews. They
  will launch once you are detected south of Beslan.

## ROE

- Weapons free on the SA-10 cluster and any Russian aircraft that comes up
  against you north of the border.
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

- **Success:** the battery's search and tracking radars are both off the air
  for good — the North Caucasus is open again.
- **Failure:** `Dodge` goes down with the battery still tracking.

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

        sa10, _tor, _shilkas, _ewr = self._spawn_red_ground(m, russia, scene)
        self._spawn_awacs(m, usa, scene)
        self._spawn_red_intercept(m, russia, scene)
        player, route = self._spawn_player(m, usa, scene)

        self._add_end_triggers(m, sa10=sa10, player=player)
        self._conceal_red(russia)
        self._draw_plan(m, scene, route=route)
        self._add_briefing(m)
        return scene.overlay.overlay

    # -- time, weather, airports --------------------------------------------

    def _set_time(self, m: Mission) -> None:
        """18:15 map-local on 12 October 2026 — dusk, the wall clock DCS shows in-game.

        pydcs serialises the hour/minute verbatim and DCS reads the field as
        map-local, so `tzinfo` is inert: write the local time you want.
        """
        m.start_time = datetime(2026, 10, 12, 18, 15, 0, tzinfo=timezone.utc)

    def _set_weather(self, m: Mission) -> None:
        """Autumn dusk, broken layer at 2200 m, light N wind, 12 C, 30 km visibility."""
        Weather(
            name="Autumn dusk",
            season_temperature=12.0,
            clouds_base=2200,
            clouds_thickness=800,
            clouds_density=6,
            visibility_distance=30000,
            wind_at_ground=Wind(0, 4),
            wind_at_2000=Wind(10, 6),
            wind_at_8000=Wind(350, 8),
        ).apply(m)

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
        sa10_site = offset(beslan.position, east_m=2_000, north_m=-12_000)
        shorad = offset(sa10_site, east_m=-1_200, north_m=-600)
        ewr_pos = offset(mozdok.position, east_m=-8_000, north_m=-6_000)

        # Valley waypoints: Stepantsminda → mid-gorge → Vladikavkaz south.
        # Caucasus convention: Point(x = north, y = east), Vaziani sits at
        # (-319065, 903148), Beslan at (-148590, 843668).
        valley_entry = Point(-225000, 873000, t)
        valley_mid = Point(-200000, 863000, t)
        valley_exit = Point(-170000, 851000, t)
        ip = Point(-158000, 846000, t)

        awacs_anchor = offset(vaziani.position, east_m=-25_000, north_m=15_000)
        intrusion_center = offset(beslan.position, east_m=0, north_m=-8_000)

        return _Scene(
            vaziani=vaziani,
            mozdok=mozdok,
            beslan=beslan,
            sa10_site=sa10_site,
            shorad=shorad,
            ewr_pos=ewr_pos,
            valley_entry=valley_entry,
            valley_mid=valley_mid,
            valley_exit=valley_exit,
            ip=ip,
            awacs_anchor=awacs_anchor,
            intrusion_center=intrusion_center,
            overlay=load_scene("caucasus"),
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(self, m: Mission, russia: Country, scene: _Scene):
        """SA-10 radars + launchers, SA-15 SHORAD, ZSU-23-4 AAA, 1L13 EWR."""
        sa10 = self._spawn_sa10_site(m, russia, scene.sa10_site)
        tor = self._spawn_shorad(m, russia, scene.shorad)
        shilkas = self._spawn_shilkas(m, russia, scene.sa10_site)
        ewr = self._spawn_ewr(m, russia, scene.ewr_pos)
        return sa10, tor, shilkas, ewr

    def _spawn_sa10_site(self, m: Mission, russia: Country, pos: Point):
        """Full SA-10 site from the pydcs template: SR + TR + CP + launchers.

        Radar and launchers must share the group — an S-300 launcher only
        engages while its track radar is in-group, which the template already
        gets right. A fourth launcher is added because a site guarding the one
        pass through the ridge fields more than three rails.

        Do **not** index into `units` to find a radar here: the template puts a
        paratrooper at index 1. `_add_end_triggers` looks both radars up by
        type through `unit_of_type`.
        """
        # The template registers the group with Russia itself — it takes no
        # country argument, unlike `sa6_site`. Adding it again duplicates the
        # whole site.
        sa10 = templates.VehicleTemplate.Russia.sa10_site(
            m, pos, 180, prefix="Grumble ", skill=Skill.Excellent
        )
        launcher = m.vehicle("Launcher 4", vehicles.AirDefence.S_300PS_5P85D_ln)
        launcher.position = pos.point_from_heading(180 + 90, 50)
        launcher.heading = 180
        launcher.skill = Skill.Excellent
        sa10.add_unit(launcher)
        return sa10

    def _spawn_shorad(self, m: Mission, russia: Country, pos: Point):
        """SA-15 Tor adjacent to the SAM site — terminal SHORAD vs HARM and bombs."""
        tor = m.vehicle_group(
            russia,
            "SAM Tor",
            vehicles.AirDefence.Tor_9A331,
            position=pos,
            heading=180,
        )
        set_skill(tor, Skill.Excellent)
        return tor

    def _spawn_shilkas(self, m: Mission, russia: Country, site: Point):
        """2x ZSU-23-4 inside the SAM perimeter — close-in AAA pop-up coverage."""
        pos = offset(site, east_m=-300, north_m=400)
        shilkas = m.vehicle_group(
            russia,
            "AAA Bear-23",
            vehicles.AirDefence.ZSU_23_4_Shilka,
            position=pos,
            heading=180,
            group_size=2,
        )
        set_skill(shilkas, Skill.High)
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
        set_skill(ewr, Skill.Excellent)
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
        set_skill(boris, Skill.Excellent)
        apply_ai_difficulty(boris, self.difficulty)
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
        mark_clients(player)
        arm(
            player,
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

        t = self._terrain
        v = scene.vaziani.position

        push = offset(v, east_m=-8_000, north_m=25_000)
        descend = offset(v, east_m=-13_000, north_m=60_000)
        egress_w = Point(-165000, 815000, t)
        egress_s = Point(-240000, 830000, t)

        player.add_runway_waypoint(scene.vaziani)
        player.add_waypoint(push, altitude=3000, speed=380, name="PUSH")
        player.add_waypoint(descend, altitude=1500, speed=360, name="DESCEND")
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
        # The target steerpoint marks the SA-10 site on the ground; the pop
        # altitude is flown off the IP leg above, not written into the target.
        waypoints.add_ground_waypoint(
            player,
            scene.sa10_site,
            overlay=scene.overlay.overlay,
            speed=380,
            name="TARGET",
        )
        # Egress: west, then south around the western ridges. Do NOT re-cross Daryal.
        player.add_waypoint(egress_w, altitude=1500, speed=400, name="EGRESS_W")
        player.add_waypoint(egress_s, altitude=4500, speed=420, name="EGRESS_S")
        player.add_runway_waypoint(scene.vaziani)
        player.land_at(scene.vaziani)
        route = [
            push,
            descend,
            scene.valley_entry,
            scene.valley_mid,
            scene.valley_exit,
            scene.ip,
            scene.sa10_site,
            egress_w,
            egress_s,
        ]
        return player, route

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        Ace: the battery is a target area on the map, not a set of icons —
        the player finds the radars with the HTS and the RWR.
        """
        conceal_country(russia)

    def _draw_plan(self, m: Mission, scene: _Scene, *, route: list[Point]) -> None:
        """Paint the plan on the F10 map (ace: friendly plan + a vague threat zone).

        Ace reveals no enemy positions. The low Daryal ingress is the whole
        point of the plan, so the route is drawn precisely; the SA-10 shows
        only as a vague target area and the MiG CAP as a coarse zone.
        """
        plan = PlanOverlay(m, self.difficulty)
        plan.objective(scene.sa10_site, "TARGET — SA-10", radius=8_000.0)
        plan.route(route, "Dodge ingress (Daryal)")
        plan.waypoint_label(scene.awacs_anchor, "Magic AWACS")
        plan.threat_area(scene.intrusion_center, 30_000.0, "MiG-29S CAP — vicinity")

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(self, m: Mission, *, sa10, player) -> None:
        """Success when both SA-10 radars dead; failure when Dodge dies first.

        Both radars are found by type, not by index: the site comes from
        pydcs's template, whose `units[1]` is a paratrooper. Gating on the two
        radars lets the shot-capable launchers stay in the same group, which an
        S-300 launcher needs in order to fire at all.
        """
        big_bird = unit_of_type(sa10, vehicles.AirDefence.S_300PS_64H6E_sr).id
        flap_lid = unit_of_type(sa10, vehicles.AirDefence.S_300PS_40B6M_tr).id
        mission_triggers.message_to_all(
            m,
            comment="SA-10 radars destroyed",
            conditions=(
                condition.UnitDead(big_bird),
                condition.UnitDead(flap_lid),
            ),
            voice=self._voice,
            text=(
                "Magic: Big Bird and Flap Lid are off the air, that battery is "
                "blind. Dodge, egress west and return to base, Vaziani. Do not "
                "re-cross Daryal."
            ),
            seconds=25,
        )

        mission_triggers.message_to_all(
            m,
            comment="Dodge lost before kill",
            conditions=(
                condition.GroupDead(player.id),
                condition.UnitAlive(flap_lid),
            ),
            voice=self._voice,
            text=(
                "Magic: Dodge is down and that battery is still radiating. "
                "The North Caucasus stays closed to us tonight."
            ),
            seconds=25,
        )

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
            "intercept any USAF push that comes north up the gorge."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(DaryalRun)


if __name__ == "__main__":
    main()
