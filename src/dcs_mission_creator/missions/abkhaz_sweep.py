"""Caucasus 'Abkhaz Sweep' — F-16C ace air-superiority sweep.

Player flies a USAF F-16C-50 out of Batumi as `Dodge`. The frag is to sweep
the airspace off the Abkhaz coast between Sukhumi-Babushara and Gudauta
before AWACS `Magic` pushes its track north. Russian aggressor squadrons
operating out of Sochi-Adler and Gudauta are contesting the corridor. A
Russian SA-6 site on a coastal ridge between the two bases denies low
transit through the AO, forcing the player to fight high where the
bandits' R-27ER / R-77 have the reach.

No tanker, no escort, no Weasel — `Dodge` is a pair and nothing else.
F-16C internal fuel with two wing tanks just covers the sortie; manage bingo
aggressively.

The bandit count scales with the number of player slots, and it is scaled off
the **magazine** rather than off taste — see `_plan_bandits`. What is tasked
scales with it too: the frag is the Sochi element, and the Gudauta
reinforcement is a threat to beat rather than a target list, so the mission
never asks for more kills than the flight is carrying missiles for.

This mission is the reason that rule is written down. It shipped with a fixed
six bandits — four Su-27 and a MiG-29S pair, all `Skill.Excellent` — against a
win condition of "both flights dead", whatever `--players` said. A single-slot
`Dodge` therefore launched with six air-to-air missiles against six of the best
crews in the game, with no tanker and no rearm, and four slots faced the same
six. At two shots per kill that is not an ace mission, it is an arithmetically
unwinnable one.

Composition (difficulty: ace, sized per player slot):
  - 2x Russian Su-27 per player jet (one four-ship, plus elements as slots
    are added), Skill Excellent, R-27ER class, Sochi-Adler, intercept on an
    Abkhaz coastal intrusion zone. **This element is the tasked kill.**
  - Russian MiG-29S reinforcement out of Gudauta — a section, one airframe
    per player jet and never fewer than two — Skill Excellent, R-77 / R-27
    class, on a closer (north-of-Sukhumi) intrusion zone. Not a tasked kill.
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
from typing import Sequence

from dcs import condition, planes, task, templates, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.triggers import TriggerZoneCircular
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, VehicleGroup

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    loadout,
    sanctuary as sanc,
    triggers as mission_triggers,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    Assembled,
    MissionBuilder,
)
from dcs_mission_creator.core.mission_kit import (
    offset,
    player_flight,
    section_sizes,
    set_skill,
)
from dcs_mission_creator.core.placement import load_scene
from dcs_mission_creator.core.routing import ThreatRing, avoid_threats
from dcs_mission_creator.core.tasking import apply_ai_difficulty
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.scene import TacticalScene

# -- force balance ------------------------------------------------------------
#
# The bandit count is derived from the player flight's magazine, not chosen by
# taste. An F-16C-50 with two wing tanks has exactly six air-to-air stations:
# 1/2/8/9 and 3/7. Stations 4 and 6 are the fuel and accept no missile at all
# (checked against the `PylonN` tables and against the game's own
# `CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua`), so six is a ceiling and
# not a choice — there is no loadout that buys more shots without giving up the
# range this sortie needs.
#
# Against Skill.Excellent Su-27 / MiG-29S the planning factor is two shots per
# kill, so one player jet is worth three kills. Only two of them are ever
# *tasked*: the Sochi CAP element. The third pays for the Gudauta
# reinforcement, which the frag treats as a threat to survive rather than as a
# target list — which is why `_add_end_triggers` wins on the CAP element alone.
#: The ceiling, not the fit: six is what an F-16C-50 with two bags *can* carry,
#: and both of `_FITS` do. What the opposition is actually priced against is
#: `MissionBuilder.air_to_air_shots(_FITS)`, read off the loaded rails, so a
#: change to the fits moves the bandits with it. This constant survives only to
#: size the spoken-number table below against `MAX_PLAYERS`.
_MISSILES_PER_JET = 6
_SHOTS_PER_KILL = 2

#: Bandit counts as the briefing says them out loud rather than as digits. The
#: table is derived rather than typed because it has to keep up with the slot
#: cap: at the ceiling every slot buys three kills, so a six-slot Dodge can face
#: eighteen, and the eight this stopped at was sized for a four-slot ceiling.
#: Raising `MAX_PLAYERS` past the words below now fails at import, not two
#: thirds of the way through a build.
_NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
)
_SPOKEN = {
    n: _NUMBER_WORDS[n]
    for n in range(1, MAX_PLAYERS * _MISSILES_PER_JET // _SHOTS_PER_KILL + 1)
}


@dataclass(frozen=True)
class _BanditPlan:
    """How many bandits go up, and in what flights, for a given player count."""

    su27: tuple[int, ...]
    mig29: tuple[int, ...]

    @property
    def su27_total(self) -> int:
        return sum(self.su27)

    @property
    def mig29_total(self) -> int:
        return sum(self.mig29)

    @property
    def total(self) -> int:
        return self.su27_total + self.mig29_total


def _plan_bandits(shots: int) -> _BanditPlan:
    """Size both bandit elements off what the player flight is actually carrying.

    `shots` is the flight's whole air-to-air magazine, counted off the loaded
    stores (`MissionBuilder.air_to_air_shots`) rather than off a per-jet
    constant — the flight splits its fit now, and the number that prices the
    opposition has to follow the rails rather than a comment. At two shots per
    kill against Excellent crews that is the kill budget; **two thirds of it is
    tasked** and the rest pays for the reinforcement, floored at a pair because
    Gudauta putting up a single ship is not a section.

    For a two-slot `Dodge` that is four Su-27 tasked and a MiG-29S pair behind
    them; for four slots it is eight tasked and four behind.
    """
    kills = shots // _SHOTS_PER_KILL
    tasked = kills - kills // 3
    return _BanditPlan(
        su27=section_sizes(tasked),
        mig29=section_sizes(max(2, kills - tasked)),
    )


#: The Kub's briefed reach, before the difficulty coarsens it. One number, and
#: every channel downstream of `PlanOverlay.estimate` shares the single claim it
#: turns into: the ring `_draw_plan` paints, the pre-planned threat on the HSD,
#: the envelope the sweep stations are sited outside of and the route bends
#: around.
_SA6_RING_M = 25_000.0


@dataclass
class _Scene:
    """Resolved airports + key positions used by every spawn step."""

    batumi: Airport
    kobuleti: Airport
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
    threats: tuple[ThreatRing, ...]
    overlay: TacticalScene


# Batumi's own air defence, and why it does not break the ace composition.
#
# "No tanker, no escort, nothing but the flight" *is* this mission's statement —
# see the force-balance note in CLAUDE.md — so nothing here may be a friendly
# asset in the fight. `BASTION` is not: it is 149 km behind the sweep stations,
# it cannot reach anything, and it does not fire a shot unless a bandit follows
# a jet with an empty rail 150 km home. What it changes is that breaking off is
# a plan. Kobuleti at 42 km comes free inside the same envelope, which matters
# for a jet that has taken a hit and wants the nearest runway.
_SANCTUARY = "BASTION"
_SANCTUARY_BATTERY = sanc.HAWK


#: How `Dodge` splits its magazine across the flight (`core/loadout.py`).
#:
#: A pure air-to-air sweep is the one tasking where both jets are on the same
#: job, so the split here is about *weapons* rather than about roles. Slot 1
#: takes six AMRAAM: everything it can lock, it can shoot, which is what a sweep
#: pushed into a GCI-fed CAP wants on the first pass. Slot 2 gives up two of
#: those for AIM-9X, because a Su-27 carrying R-27ER will not be beaten at range
#: every time and the flight cannot afford to arrive at a merge with nothing but
#: an AMRAAM minimum range. Six shots either way — the ceiling, since stations 4
#: and 6 are the bags and take no missile — which is what `_plan_bandits` prices
#: the opposition against.
#:
#: Both are ED payloads station for station, off
#: `<DCS>/CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua`:
#: `AIM-120C*6, FUEL*2, ECM` and `AIM-120C*4, AIM-9X*2, FUEL*2, ECM`. Note the
#: outboard-in ordering of a pure A/A fit — AMRAAM on 1/2/8/9 and the Sidewinders
#: on 3/7 — which is the reverse of the SEAD fits elsewhere in this project,
#: where 3/7 are the HARM rails and the AIM-9X move out to 2/8.
_FITS = (
    loadout.Loadout(
        role="AIM-120C*6",
        carries="six AIM-120C, ALQ-184, two 370 gal — every shot is a radar shot",
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (3, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
        ),
    ),
    loadout.Loadout(
        role="AIM-120C*4 + 9X",
        carries=(
            "four AIM-120C, two AIM-9X, ALQ-184, two 370 gal — the flight's "
            "answer to a merge"
        ),
        stores=(
            (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (2, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (3, "AIM_9X_Sidewinder_IR_AAM"),
            (4, "Fuel_tank_370_gal"),
            (5, "ALQ_184_Long"),
            (6, "Fuel_tank_370_gal"),
            (7, "AIM_9X_Sidewinder_IR_AAM"),
            (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
            (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
        ),
    ),
)


class AbkhazSweep(MissionBuilder):
    name = "abkhaz_sweep"
    title = "Abkhaz Sweep"
    difficulty = Difficulty.ACE
    terrain = Caucasus

    #: The two coalition task panels. Plain strings: nothing here needs
    #: to compute one, and `blue_task_text` / `red_task_text` are there
    #: for the mission that does.
    blue_task = (
        "Sweep the airspace off the Abkhaz coast between Sukhumi and "
        "Gudauta. Break the Russian Su-27 element out of Sochi-Adler — "
        "that element is the frag. The MiG-29S reinforcement out of "
        "Gudauta is a threat to beat, not a required kill. Stay above "
        "4500 m AGL over the AO — the SA-6 on the coastal ridge north "
        "of Sukhumi denies the low block. RTB Batumi. No tanker, no "
        "escort."
    )

    red_task = (
        "Hold the Abkhaz coastal airspace. Su-27 from Sochi-Adler "
        "intercept any USAF push up the coast; MiG-29S from Gudauta "
        "reinforce once the Americans are committed. SA-6 on the coastal "
        "ridge north of Sukhumi denies the low block."
    )

    #: 05:30 map-local on 18 July 2026 — dawn, the wall clock DCS shows in-
    #: game.
    start_time = datetime(2026, 7, 18, 5, 30, 0, tzinfo=timezone.utc)

    #: Summer dawn, scattered cumulus 2400 m, light NW wind, 22 C, 24 km vis.
    weather = Weather(
        name="Summer dawn scattered",
        season_temperature=22.0,
        clouds_base=2400,
        clouds_thickness=600,
        clouds_density=4,
        visibility_distance=24000,
        wind_at_ground=Wind(310, 4),
        wind_at_2000=Wind(305, 6),
        wind_at_8000=Wind(295, 8),
    )

    def __init__(self, *, players: int = MIN_PLAYERS) -> None:
        super().__init__(players=players)
        self._bandits = _plan_bandits(self.air_to_air_shots(_FITS))

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        su = _SPOKEN[self._bandits.su27_total]
        mig = _SPOKEN[self._bandits.mig29_total]
        shots = self.air_to_air_shots(_FITS)
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
  - Break the Sochi-Adler element. That element is the
    frag: {su} Su-27. The MiG-29S section Gudauta sends
    once you are committed is a threat to beat, not a
    target list — nobody expects one load of missiles to
    clear the whole corridor.
  - Stay ABOVE 4500 m over the AO — SA-6 on the coastal
    ridge north of Sukhumi denies the low block.
  - RTB Batumi. Divert: Senaki-Kolkhi.

WEAPONS
  Four AIM-120C, two AIM-9X, ALQ-184 on the centreline —
  {shots} shots for the flight, and nothing to rearm from.
  Against crews this good plan two per bandit: that pays
  for the Sochi element and leaves you something for the
  section out of Gudauta. Come home on a dry magazine,
  there is nobody to hand the fight to.

LOADOUT (one magazine, split two ways)
{self.loadout_brief("Dodge", _FITS)}
  Six shots either way. Slot 1 can shoot everything it
  locks; slot 2 has the answer to a merge.

PACKAGE
  Dodge         : F-16C-50 pair, Batumi, hot ramp, CAP
                  frag. Loadout above.
  Magic         : E-3A AWACS, 251.000 AM, Black Sea
                  race-track. No tanker, no escort, no
                  Weasel. The flight is the package.

INTELLIGENCE
  The picture is thin. No overhead since yesterday
  morning and nothing airborne up there tonight — what
  follows is assessment, not fact. Build your own
  picture off Magic and the RWR.
  Air : Sochi-Adler flies the aggressor syllabus. Magic's
        read for tonight is {su} airframes up on the
        first push, carrying the long-burn R-27 variant.
        Gudauta keeps a lighter section, assessed at {mig}
        airframes, that has reinforced every previous
        engagement once we were committed — R-77 shooters.
        Both fields are crewed by their best.
  SAM : A Kub battery is assessed on the coastal ridge
        north of Sukhumi. We have no current fix and it
        moves. The ring on your map is that assessment,
        not a fix — it is drawn wide and it is in the
        wrong place by some kilometres. Assume the low
        block is denied anywhere over the AO, and assume
        guns with it.
  EWR : Early-warning radar covers the whole corridor
        from inland. Both sites are marked approximately.
        You will be seen from the coast in, and both
        fields will be vectored onto you.
  Base: Sochi is defended in its own right — an S-125
        battery on the field, guns in the overhead. It
        reaches 79 km short of your northern station. Do
        not follow the Flankers home to find out.

ROE / FRAGS
  - Weapons free on any Russian fighter inside the
    coastal corridor.
  - The Sochi element is the frag. With it down the
    corridor is ours and Magic moves — you are cleared
    home with the Gudauta section still flying.
  - Do NOT descend below 4500 m AGL over the AO — Snow
    Drum will see you the moment you drop into its
    envelope.
  - Not cleared to pursue over Sochi-Adler.
  - Bingo fuel: 3500 lb. RTB Batumi direct (divert:
    Kobuleti). Do not chase north of Gudauta on bingo or
    on an empty magazine.

FALL-BACK ({_SANCTUARY})
  Batumi and Kobuleti both sit under a
  {_SANCTUARY_BATTERY.name} battery — {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km,
  cyan ring on the map, guns in the overhead of Batumi.
  You have six missiles and nobody with you: the moment
  the magazine or the fuel says the fight is over, that
  ring is where it ends. Cross it and nothing follows.
  {_SANCTUARY} MARSHAL is a hold abeam Batumi, on the
  map and in the DED. Either runway takes you.

NAV
  Bullseye (own side) : {bx:.0f}, {by:.0f} (DCS world m)
  PUSH                : 40 km north of Batumi, over coast
  STATION_SOUTH       : offshore south of Sukhumi
  STATION_NORTH       : offshore north of Sukhumi
  EGRESS              : south back to Batumi
  BASTION MARSHAL     : hold abeam Batumi, inside the ring

FREQUENCIES
  Magic AWACS  : 251.000 AM
  Batumi tower : per kneeboard

NOTES
  Sunrise ~05:25 local. Sun comes up over the mountains
  to the east — the Sochi element will be pushing south
  with the sun behind them. Manage your
  aspect before commit. Scattered cumulus base 2400 m,
  600 m thick — bandits can use the layer to mask their
  intercept geometry.
"""

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        su_total = self._bandits.su27_total
        mig_total = self._bandits.mig29_total
        shots = self.air_to_air_shots(_FITS)
        slot_s = "" if self.players == 1 else "s"
        return f"""# Abkhaz Sweep

**Theater:** Caucasus
**Date / time:** 18 July 2026, 05:30 local (dawn)
**Player aircraft:** F-16C-50 (`Dodge`), Batumi, hot ramp
**Players:** {self.slot_summary("Dodge")}
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
station offshore between Sukhumi and Gudauta, and break the Sochi-Adler
element — {su_total} Su-27, and that element is the frag. A MiG-29S section
out of Gudauta reinforces once you are committed; it is a threat to beat
rather than a target list, and the corridor counts as opened without it.
Stay above
4500 m over the AO — the Kub battery assessed on the coastal ridge north of
Sukhumi denies the low block.

## Package

| Callsign | Type     | Base    | Role                              |
|----------|----------|---------|-----------------------------------|
| Dodge    | F-16C-50 | Batumi  | Player air-superiority sweep      |
| Magic    | E-3A     | Batumi  | AWACS, 251.000 AM, Black Sea track|

No tanker, no escort, no Weasel — denied support is part of the ace
composition, and the flight is the whole package. **{shots} shots between you**,
and no way to rearm. That magazine is what the frag is sized against; see
*Difficulty composition* below.

### `Dodge` loadout

{self.loadout_table("Dodge", _FITS)}

A pure sweep is the one tasking where both jets are on the same job, so the
split is about weapons rather than roles. Slot 1 can shoot everything it locks,
which is what a sweep pushed into a GCI-fed CAP wants on the first pass. Slot 2
gives two of those up for AIM-9X, because a Su-27 carrying R-27ER will not be
beaten at range every time and the flight cannot afford to arrive at a merge
holding nothing but an AMRAAM minimum range. Six shots either way — stations 4
and 6 are the bags and take no missile, so six is the ceiling and not a
choice.

## Intelligence

The picture is thin — no overhead since yesterday morning, nothing airborne
up there tonight. Everything below is assessment, and the marks on your map
are drawn to match: every enemy ring is wide, dashed and labelled
`(approx.)`, because that is the confidence behind it. None of them is a fix.
Treat them as areas to stay out of, not as coordinates — build the real
picture off `Magic`, the RWR and the tally.

- **Air (primary):** Sochi-Adler flies the aggressor syllabus. `Magic`'s
  read for tonight is {su_total} Su-27 up on the first push, carrying the
  long-burn R-27 variant. Their best crews. This element is the tasked kill.
- **Air (reinforcement):** Gudauta keeps a lighter section, assessed at
  {mig_total} MiG-29S, that has reinforced every previous engagement once we
  were committed — R-77 shooters. Beating them is survival, not the frag.
- **SAM (terminal denial):** a Kub battery is assessed on the coastal ridge
  north of Sukhumi. No current fix, and it relocates. The ring on your map and
  the pre-planned threat on your HSD are both that assessment — same claim,
  two displays, and neither is where the launchers are standing. Assume the
  low block is denied anywhere over the AO and assume guns are sited with it.
  That is what forces the fight above 4500 m AGL, where the bandits want it.
- **EWR:** early-warning radar covers the corridor from inland, one site
  behind each field. Both are marked approximately; a search radar has no
  envelope, so neither carries a ring you could fly around. You are seen from
  the coast in, and both fields get vectored onto you.
- **Sochi-Adler field defence:** an S-125 battery on the airfield, with
  self-propelled guns in the overhead, assessed at the same confidence as
  everything else here. It reaches 18 km and your northern station is 79 km
  from it, so it touches no part of the sweep — it is the reason a Flanker that
  turns for home stops being a target.

## ROE

- Weapons free on any Russian fighter inside the coastal corridor.
- The Sochi element is the frag. With it down the corridor is ours and `Magic`
  moves — you are cleared home with the Gudauta section still flying.
- Do **not** descend below 4500 m AGL over the AO — Snow Drum sees you the
  moment you drop into its envelope.
- **Not cleared to pursue over Sochi-Adler.** A withdrawing Flanker is not
  worth an S-125, and on this magazine it is not worth the missiles either.
- Bingo fuel: 3500 lb. RTB Batumi direct (divert: Kobuleti). Do not chase
  north of Gudauta on bingo or on an empty magazine.

## Fall-back

Batumi is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery reaching
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10 map, with gun sections in
the overhead. Kobuleti is 42 km up the coast and **inside the same envelope** —
take whichever runway is closer to where you break off.

This is the counterpart to the magazine arithmetic above. You launch with six
air-to-air missiles each against crews flown at their best, with no tanker and
no escort, and the frag is deliberately smaller than the airspace: the
Gudauta section is a threat to beat, not a target list. Disengaging is therefore
a legitimate line rather than a failure — and it only means anything because the
ring is there for it to end at. `{_SANCTUARY} MARSHAL` is a hold abeam Batumi
inside the envelope, on the map and in the DED.

The battery sits 149 km behind your southern station. It is not support and it
will not help you in the fight; it is where the fight stops.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- PUSH: 40 km north of Batumi, over the coast
- STATION_SOUTH: offshore south of Sukhumi
- STATION_NORTH: offshore north of Sukhumi
- EGRESS: south back to Batumi
- `BASTION MARSHAL`: hold abeam Batumi, inside the umbrella

## Frequencies

- Magic AWACS: 251.000 AM
- Batumi tower: per kneeboard
- `{_SANCTUARY}` details and the Kobuleti divert are on the kneeboard comms card.

## Weather

Summer dawn. Light NW wind 4 m/s ground, 8 m/s at 8000 m. 22 °C, QNH
760 mmHg. Visibility 24 km, scattered cumulus base 2400 m, 600 m thick.
Sunrise ~05:25 local — the Su-27 element pushes south with the sun
behind them over the eastern mountains.

## Difficulty composition

**Ace.** Skill Excellent Su-27 and MiG-29S, R-77 / R-27ER class, EWR-fed GCI
on both bandit elements, SA-6 terminal denial over the AO forcing the fight
high, AWACS-only support (no tanker, no escort, no Weasel), low sun on commit.
One mistake ends the sortie.

The opposition is sized off the magazine rather than off taste, and off the
missiles actually loaded rather than off a number in a comment. An F-16C-50
with two wing tanks has six air-to-air stations — 4 and 6 are the fuel and take
no missile — so this flight launches with {shots} shots, and against Excellent
crews the planning factor is two shots per kill. Two thirds of that budget is
tasked. At {self.players} slot{slot_s} that is **{su_total} Su-27** out of
Sochi-Adler as the tasked kill, plus **{mig_total} MiG-29S** out of Gudauta as a
reinforcement you are never required to shoot down.

## Win / loss conditions

- **Success:** the Sochi-Adler element is destroyed — the corridor is open and
  `Magic` pushes its track north. Taking the Gudauta section down as well is a
  clean sweep and is called out, but it is not required.
- **Failure:** `Dodge` goes down with the corridor still contested.

## Re-generate

```bash
uv run dcs-mission-creator generate {self.name} --players {self.players}
```
"""

    # -- top-level orchestration --------------------------------------------

    def _assemble(self, m: Mission, plan: PlanOverlay) -> Assembled:
        """Assemble the mission by calling each step in package order."""
        scene = self._setup_airports(m)
        usa, russia = m.country("USA"), m.country("Russia")

        _sa6, _shilkas, _ewr_su, _ewr_mig = self._spawn_red_ground(m, russia, scene)
        self._spawn_awacs(m, usa, scene)
        su27s = self._spawn_red_su27(m, russia, scene)
        mig29s = self._spawn_red_mig29(m, russia, scene)
        dodge, route = self._spawn_player(m, usa, scene, threats=scene.threats)

        home, sochi_ad = self._spawn_sanctuaries(m, usa, russia, scene, route=route)

        self._add_end_triggers(m, su27s=su27s, mig29s=mig29s, dodge=dodge)
        sanc.announce(m, home, at_seconds=180, voice=self._voice)
        sanc.remark_all(m, home, sochi_ad)
        briefed_threats = self._draw_plan(
            m, scene, plan=plan, route=route, home=home, sochi_ad=sochi_ad
        )
        return Assembled(scene.overlay.overlay, briefed_threats)

    # -- airports ------------------------------------------------------------

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Batumi for blue, Sochi/Gudauta/Sukhumi for red, derive AO geometry."""
        t = self._terrain
        batumi = t.airports["Batumi"]
        # Kobuleti is 42 km up the coast and inside the same missile umbrella —
        # a second runway for a jet coming back hit, at no cost in cover.
        kobuleti = t.airports["Kobuleti"]
        sochi = t.airports["Sochi-Adler"]
        gudauta = t.airports["Gudauta"]
        sukhumi = t.airports["Sukhumi-Babushara"]
        batumi.set_blue()
        kobuleti.set_blue()
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
        threats = self._threat_rings(sa6_pos=sa6_site)
        # Sweep stations sit offshore, west of the coast. They are inside the
        # ring `_draw_plan` paints — at ace that estimate is 34 km wide and 6 km
        # off truth, and there is no water between Sukhumi and Gudauta outside
        # it — and that is the mission working as briefed rather than a
        # contradiction: the Kub's whole footprint covers the AO, which is why
        # the ROE answers it with 4500 m of altitude instead of with distance.
        push = offset(batumi.position, east_m=-15_000, north_m=40_000)
        station_south = offset(sukhumi.position, east_m=-35_000, north_m=-15_000)
        station_north = offset(sukhumi.position, east_m=-30_000, north_m=25_000)
        egress = offset(batumi.position, east_m=-15_000, north_m=20_000)
        awacs_anchor = offset(batumi.position, east_m=-25_000, north_m=15_000)
        su27_intrusion = offset(sukhumi.position, east_m=-25_000, north_m=20_000)
        mig29_intrusion = offset(sukhumi.position, east_m=-20_000, north_m=35_000)
        return _Scene(
            batumi=batumi,
            kobuleti=kobuleti,
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
            threats=threats,
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

    def _spawn_red_su27(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[FlyingGroup]:
        """The Sochi-Adler CAP — two Su-27 per player jet, and the tasked kill.

        Sized by `_plan_bandits`, split into DCS-legal flights, all cued off one
        coastal intrusion zone so the whole element commits together whatever
        the slot count.
        """
        zone = m.triggers.add_triggerzone(
            position=scene.su27_intrusion,
            radius=45_000,
            hidden=True,
            name="Su-27 intrusion",
        )
        flights = [
            self._spawn_bandit_flight(
                m,
                russia,
                name="Ivan" if i == 0 else f"Ivan {i + 1}",
                plane_type=planes.Su_27,
                airport=scene.sochi,
                zone=zone,
                size=size,
                speed=920,
                altitude=8000,
                max_engage_distance=120_000,
            )
            for i, size in enumerate(self._bandits.su27)
        ]
        self._announce_bandits(
            m,
            zone,
            comment="Su-27 launch announcement",
            text=(
                f"Magic, Dodge. {_SPOKEN[self._bandits.su27_total].capitalize()} "
                "Sukhoi 27 airborne from Sochi-Adler, bearing 180, vectoring "
                "on the coast."
            ),
        )
        return flights

    def _spawn_red_mig29(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> list[FlyingGroup]:
        """The Gudauta reinforcement — a section, and never a required kill.

        One airframe per player jet, floored at a pair. It is what the third
        kill in each jet's magazine pays for, so it exists to be survived; the
        win condition in `_add_end_triggers` does not name it.
        """
        zone = m.triggers.add_triggerzone(
            position=scene.mig29_intrusion,
            radius=30_000,
            hidden=True,
            name="MiG-29 intrusion",
        )
        flights = [
            self._spawn_bandit_flight(
                m,
                russia,
                name="Boris" if i == 0 else f"Boris {i + 1}",
                plane_type=planes.MiG_29S,
                airport=scene.gudauta,
                zone=zone,
                size=size,
                speed=900,
                altitude=8500,
                max_engage_distance=100_000,
            )
            for i, size in enumerate(self._bandits.mig29)
        ]
        self._announce_bandits(
            m,
            zone,
            comment="MiG-29 launch announcement",
            text=(
                f"Magic, Dodge. {_SPOKEN[self._bandits.mig29_total].capitalize()} "
                "MiG-29 airborne from Gudauta, bearing 200, R-77 class, "
                "committing south."
            ),
        )
        return flights

    def _spawn_bandit_flight(
        self,
        m: Mission,
        russia: Country,
        *,
        name: str,
        plane_type: type[planes.PlaneType],
        airport: Airport,
        zone: TriggerZoneCircular,
        size: int,
        speed: int,
        altitude: int,
        max_engage_distance: int,
    ) -> FlyingGroup:
        """One GCI-cued bandit flight at ace skill and ace behaviour."""
        flight = m.intercept_flight(
            russia,
            name,
            plane_type,
            airport=airport,
            zone=zone,
            late_activation=True,
            start_type=StartType.Warm,
            speed=speed,
            altitude=altitude,
            max_engage_distance=max_engage_distance,
            group_size=size,
        )
        set_skill(flight, Skill.Excellent)
        apply_ai_difficulty(flight, self.difficulty)
        return flight

    def _announce_bandits(self, m: Mission, zone, *, comment: str, text: str) -> None:
        """Magic calls the scramble once, when the element is cued on the player."""
        mission_triggers.message_to_coalition(
            m,
            comment=comment,
            conditions=(condition.PartOfCoalitionInZone("blue", zone.id),),
            voice=self._voice,
            text=text,
            coalition="blue",
            seconds=15,
        )

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

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[ThreatRing, ...],
    ):
        """Dodge F-16C-50 from Batumi, hot ramp; sweep stations offshore.

        The magazine is the mission here, and `_FITS` splits it: everything a
        radar shot on slot 1, two of those given up for AIM-9X on slot 2. Six
        shots either way, which is the ceiling and what `_plan_bandits` sizes the
        opposition against.
        """
        sections = player_flight(
            m,
            country=usa,
            name="Dodge",
            aircraft_type=planes.F_16C_50,
            airport=scene.batumi,
            maintask=task.CAP,
            start_type=StartType.Warm,
            slots=self.players,
            loadouts=_FITS,
        )

        # Every section flies the one plan: `_route_sweep` is pure geometry
        # against the same rings, so the routes it writes are identical, and the
        # lead's is the one the map and the cartridge are drawn from.
        routes = []
        for player in sections:
            player.add_runway_waypoint(scene.batumi)
            routes.append(self._route_sweep(player, scene, threats=threats))
            player.add_runway_waypoint(scene.batumi)
            player.land_at(scene.batumi)
        return sections, routes[0]

    def _route_sweep(
        self,
        player,
        scene: _Scene,
        *,
        threats: tuple[ThreatRing, ...],
    ) -> list[Point]:
        """PUSH → both sweep stations → EGRESS, bent clear of the assessed Kub.

        The stations were already sited outside the envelope, but the run home
        was not: straight from the northern station to the coast-out point the
        leg passed 18.6 km abeam the ridge, well inside a Kub's reach, so the
        one line on the plan flown with the fight over and the fuel state low
        was the one line inside the ring the briefing warns about. Routing it
        puts the bend on the map, the kneeboard and the flight plan at once.

        Returns the flown points so `_draw_plan` paints the route that exists
        rather than the four anchors it was planned from.
        """
        legs = (
            (scene.push, "PUSH", 6000, 800),
            (scene.station_south, "STATION_SOUTH", 7500, 780),
            (scene.station_north, "STATION_NORTH", 7500, 780),
            (scene.egress, "EGRESS", 5000, 820),
        )
        first, name, altitude, speed = legs[0]
        player.add_waypoint(first, altitude=altitude, speed=speed, name=name)
        route = [first]
        for (start, *_), (end, name, altitude, speed) in zip(legs, legs[1:]):
            bends = avoid_threats(start, end, threats, clearance_m=6_000.0)[1:-1]
            for i, pt in enumerate(bends, start=1):
                player.add_waypoint(
                    pt, altitude=altitude, speed=speed, name=f"{name}-{i}"
                )
                route.append(pt)
            player.add_waypoint(end, altitude=altitude, speed=speed, name=name)
            route.append(end)
        return route

    # -- F10 map briefing ---------------------------------------------------

    def _threat_rings(self, *, sa6_pos: Point) -> tuple[ThreatRing, ...]:
        """The Kub's real envelope at its real position, for the flight plan.

        Deliberately truth rather than `plan.estimate`: this is what decides
        whether the route gets shot at, and the drawn ring is offset by design,
        so planning around the drawing would bend the sweep away from empty sea
        and leave it exposed where the launchers are. The estimate's job is to
        tell the player something; this one's is to keep the plan flyable.

        Only the assessed Kub is here. The EWRs cannot shoot, so nothing needs
        to route around them; the Shilkas ride inside this ring and add nothing
        to it; and the bandit CAP is airborne — a squadron's race-track is not
        an envelope you can plan a detour around.

        It is what makes the briefing's own claims true. The egress leg used to
        pass 18.6 km abeam the ridge, well inside a Kub, on the one line flown
        with the fight over and the fuel low.
        """
        return (ThreatRing(sa6_pos, _SA6_RING_M, "SA-6"),)

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        *,
        route: list[Point],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Batumi under Hawk, Sochi under S-125.

        The blue half is the answer to this mission's own magazine arithmetic.
        `Dodge` launches with six air-to-air missiles against bandits scaled off
        the slot count and is explicitly cleared to leave the Gudauta section
        flying — but "disengage" only means something if there is somewhere the
        disengagement ends. `BASTION` is that somewhere, and it stays outside the
        fight by 149 km, so it buys the player an exit without giving him a
        friendly asset to fight behind.

        Sochi gets the red battery because that is where the tasked Su-27 element
        recovers, and following one home with an empty jet is the mistake this
        prices. Gudauta is the field a mission would reach for second and the one
        `build_sanctuary` refuses: the northern sweep station is 18 km off its
        threshold, so any envelope there covers the airspace the player is
        fragged to hold.

        The two `keep_clear` lists differ. Out of *ours* goes the red order of
        battle — the Kub, the Shilka and both EWRs. Out of *theirs* goes every
        flown point of the sweep, both stations included, which is what stops an
        S-75 being chosen here by mistake: at 43 km it would reach the northern
        station and quietly turn a fighter sweep into a SAM problem.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.batumi,
            callsign=_SANCTUARY,
            facing=scene.station_north,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[
                scene.sa6_site,
                scene.shilka_pos,
                scene.ewr_su27,
                scene.ewr_mig29,
            ],
            alternates=[scene.kobuleti],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        sochi_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.sochi,
            callsign="Sochi field",
            facing=scene.station_north,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Sochi",
            keep_clear=[
                scene.station_north,
                scene.station_south,
                scene.awacs_anchor,
                *route,
            ],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, sochi_ad

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        route: list[Point],
        home: sanc.Sanctuary,
        sochi_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan on the F10 map (ace: the ground picture, badly located).

        Ace does not withhold the sites any more, it withholds the *fix*: every
        ring here is drawn several kilometres off truth and wider than the
        system really reaches, dashed rather than solid, and labelled
        "(approx.)". That is what the Intelligence section already claims to
        have — a battery assessed on a ridge with no current fix — and it is a
        picture the player still cannot fly a tight route through.

        The bandit CAP stays a `threat_area`: it is airborne, and a race-track
        two fighter squadrons fly is not an envelope anybody emplaced.

        Returns the rings as HSD threat points, so the cockpit carries the same
        imprecise claim as the map.

        Takes the overlay rather than building one so that any other step
        needing this mission's claim about a site — a steerpoint, a cartridge
        point — gets the same memoised estimate rather than a second guess.
        """
        # The sanctuary goes on first so its marshal leg wins the cartridge's
        # navigation budget: `core/dtc.py` fills that tab in draw order after the
        # flight's own route, and on a sortie flown with six missiles and no
        # support the hold behind the umbrella is the point a pilot is most
        # likely to need.
        home.draw(plan)
        ao = scene.station_south.midpoint(scene.station_north)
        plan.objective(ao, "Sweep AO", radius=8_000.0)
        plan.route(route, "Dodge sweep")
        plan.waypoint_label(scene.awacs_anchor, "Magic AWACS")
        briefed = dtc.briefed(
            plan.threat(
                scene.sa6_site,
                radius=_SA6_RING_M,
                label="SA-6",
                icon=StandardIcon.AirDefense,
            ),
            dtc.SA_6,
            label="SA-6",
        )
        # The EWRs are on the map for the same reason the briefing names them —
        # the player is seen from the coast in and should know from where — but
        # a search radar has no envelope, so neither one reaches the cartridge.
        for pos, name in (
            (scene.ewr_su27, "EWR (Sochi)"),
            (scene.ewr_mig29, "EWR (Gudauta)"),
        ):
            plan.threat(pos, radius=4_000.0, label=name, icon=StandardIcon.SearchRadar)
        plan.threat_area(scene.sukhumi.position, 28_000.0, "Bandit CAP — vicinity")
        # Sochi's own belt is a red ring like any other, drawn at ace confidence
        # — approximate, and into the cartridge beside the Kub. It reaches 18 km
        # and the stations are 79 km away, so it costs the sweep nothing and
        # costs a chase everything.
        briefed += sochi_ad.draw(plan)
        return briefed

    # -- triggers and briefing ----------------------------------------------

    def _add_end_triggers(
        self,
        m: Mission,
        *,
        su27s: list[FlyingGroup],
        mig29s: list[FlyingGroup],
        dodge: Sequence[FlyingGroup],
    ) -> None:
        """Success on the Sochi element; failure when Dodge dies first.

        The frag is deliberately not "every bandit dead". A single-slot Dodge
        carries six missiles and no rearm, and asking it to clear the whole
        corridor is asking for kills the jet is not carrying — so success is
        the element that actually blocks the AWACS track, and the Gudauta
        section is scored as a bonus in a second call rather than as a
        requirement.

        "Dodge is down" is every section of it down: above four coop slots the
        flight is more than one DCS group, and gating the loss call on the lead
        section alone would call the corridor lost with jets still in it.
        """
        mission_triggers.message_to_all(
            m,
            comment="Sochi element broken",
            conditions=tuple(condition.GroupDead(g.id) for g in su27s),
            voice=self._voice,
            text=(
                "Magic: Sochi's element is off the board. That is the corridor "
                "open — Magic is pushing the track north. Dodge, disengage "
                "south when you are ready, Batumi."
            ),
            seconds=25,
        )

        mission_triggers.message_to_all(
            m,
            comment="Corridor swept clean",
            conditions=tuple(condition.GroupDead(g.id) for g in su27s + mig29s),
            voice=self._voice,
            text=(
                "Magic: Gudauta's section is down as well. Nothing is flying "
                "between Sukhumi and Gudauta. That is a clean sweep, Dodge."
            ),
            seconds=25,
        )

        mission_triggers.message_to_all(
            m,
            comment="Dodge lost",
            conditions=tuple(condition.GroupDead(group.id) for group in dodge),
            voice=self._voice,
            text=(
                "Magic: Dodge is down and the corridor is still theirs. Holding "
                "the southern track. First-light packages are aborting."
            ),
            seconds=25,
        )


def main() -> None:
    run_cli(AbkhazSweep)


if __name__ == "__main__":
    main()
