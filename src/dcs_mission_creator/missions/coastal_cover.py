"""Caucasus 'Coastal Cover' — F-16C swing-role escort over the Inguri valley.

The player flies a USAF F-16C-50 out of Batumi on a sortie that changes shape
three times, and the mission is the seams between the shapes rather than any one
of them:

1. **Escort.** Push the terrain-masked corridor, station over the AO, and keep
   the Russian alert pair off `Hawg` — the A-10C pair fragged against the
   armoured column on the valley road.
2. **Strike.** The column is the escort for what the march is actually for: a
   fuel and ammunition detachment a dozen kilometres back down the same road,
   in the rear of the march. `Hawg` is dry
   on the column by the time it matters, so the two GBU-12s under `Dodge`'s
   wings are the only thing in the package that can stop it — talked on and
   lased by `Pinpoint 1-1`, a tactical air control party in the treeline above
   the road.
3. **Defending the man doing the lasing.** A Russian battalion works out where a
   laser is coming from, and the counter is not another SAM: it is a pair of
   Mi-24Ps sent low up the valley after `Pinpoint`. Losing that race costs the
   player the laser rather than the mission — the pod self-designates — but it
   costs the pass its margin, and it is a low, slow, look-down fight the rest of
   the sortie never asks for.

Two things are deliberately absent from the F10 map, the cartridge and every
friendly flight plan, and both are named as gaps in the briefing rather than
sprung (the rules for that are in the `dcs-mission` skill):

- **the SA-8 travelling with the detachment.** The briefing says a brigade's
  fuel does not move without air defence and that nobody found it, and it gives
  a hard release floor — 6,000 m, above the system's ceiling — so the withheld
  launcher punishes the greedy low pass and not the briefed plan. `Magic` names
  it on ESM when it radiates.
- **the Su-27 pair at Gudauta**, which is `Eagle`'s and is named as `Eagle`'s in
  both briefings and on the radio when it scrambles.

`Dodge` carries four air-to-air missiles and two bombs, so the frag is priced
against that magazine rather than against the airspace: **nothing airborne is a
required kill.** The arithmetic is in `_spawn_red_escalation`.

Composition (difficulty: trained):
  - Russian armoured column `Convoy Bear` (4x BTR-80, 2x T-72B, 1x ZSU-23-4) on
    a snap-on-road route from the Inguri valley to the Senaki junction.
  - Second echelon in the rear of the same march: `POL Bear` (2x ATZ-10
    bowser + 1x Ural-375 — the player's target), a BTR-80 escort, and one
    SA-8 Osa that joins the road when the party calls the trucks.
  - 2x SA-13 (Strela-10M3) on high ground overlooking the road, Skill.High.
  - 2x ZSU-23-4 AAA overwatch on the valley hills.
  - 2x T-72B + 2x BTR-80 counterattack reserve concealed behind the column.
  - 2x 55G6 EWR chain along the Russian frontier for GCI vectoring.
  - 2x MiG-29S `Boris` at Sukhumi-Babushara, Skill.High, launched on intrusion.
  - 2x Su-27 `Sokol` at Gudauta, Skill.Average, cold on the ramp until the
    column comes apart — a threat to survive, not a tasked kill.
  - 2x Mi-24P `Krokodil`, low up the valley after the TACP team, sent once the
    detachment has been seen and lased.
  - An S-125 battery with gun sections over Sukhumi-Babushara, so a MiG that
    turns for home stops being a target.
  - USAF support: E-3A `Magic`, KC-135 `Texaco` (TACAN 10X), F-15C `Eagle` CAP,
    A-10C `Hawg` on the column, `Pinpoint 1-1` TACP lasing the detachment, and
    a Hawk battery (`BULLDOG`) over Batumi to run to.
  - Weather: spring scattered cumulus, light NW wind, 18 C.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from dcs import action, condition, helicopters, planes, task, triggers, vehicles
from dcs.country import Country
from dcs.drawing.icon import StandardIcon
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.point import PointAction
from dcs.terrain.caucasus.caucasus import Caucasus
from dcs.terrain.terrain import Airport
from dcs.triggers import TriggerZoneCircular
from dcs.unit import Skill
from dcs.unitgroup import FlyingGroup, VehicleGroup
from dcs.unittype import VehicleType

from dcs_mission_creator.core import (
    air_defense as ad,
    dtc,
    kneeboard,
    routing,
    sanctuary as sanc,
    triggers as mission_triggers,
    waypoints,
)
from dcs_mission_creator.core.cli import run_cli
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.jtac import CoordTarget, arm_jtac_coords
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.mission_builder import MissionBuilder
from dcs_mission_creator.core.mission_kit import (
    arm,
    mark_clients,
    offset,
    race_track,
    set_skill,
)
from dcs_mission_creator.core.placement import (
    load_scene,
    observation_post,
    sam_site_on_ridge,
)
from dcs_mission_creator.core.recon import (
    Chrome,
    Frame,
    Mark,
    ReconStill,
    landmark_marks,
    publish as recon,
    road_column,
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
from dcs_mission_creator.core.visibility import conceal_country
from dcs_mission_creator.core.weather import Weather, Wind
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import TacticalScene


@dataclass
class _Scene:
    """Resolved airports + AO center + map overlay used by every spawn step."""

    batumi: Airport
    kutaisi: Airport
    sukhumi: Airport
    gudauta: Airport
    senaki: Airport
    ao_center: Point
    #: The road junction north of Senaki both Russian echelons are driving at.
    junction: Point
    overlay: TacticalScene


@dataclass
class _RedGround:
    """The Russian ground order of battle, so spawn order stops being a 7-tuple.

    Two echelons rather than one is the mission's whole structure, and the
    orchestrator has to hand different pieces of it to different steps — the
    column to the A-10s, the fuel detachment to the controller and the player,
    the launcher to nothing at all. Naming them beats positional unpacking.
    """

    convoy: VehicleGroup
    reserve: VehicleGroup | None
    #: The player's target: two bowsers and an ammunition truck.
    pol: VehicleGroup
    pol_escort: VehicleGroup
    #: The SA-8 travelling with the detachment. On no map and in no cartridge.
    osa: VehicleGroup
    sa13_pos: Point
    ewr_positions: list[Point]


# Batumi's own air defence, and the reason the mission has any.
#
# The player launches with six missiles, no tanker and no wingman below four
# slots, 98 km from the AO. Without a covered field the only answer to "three
# bandits, two missiles" is a losing merge, so `BULLDOG` is what makes running
# a move. Hawk rather than Patriot because the AO is 98 km out and a Patriot
# would reach most of the way to it — see `core/sanctuary.py` on `keep_clear`.
_SANCTUARY = "BULLDOG"
_SANCTUARY_BATTERY = sanc.HAWK

# Radios and the laser code. Both briefings quote every one of these, so they
# are constants rather than literals at the call site: a briefing that names a
# frequency the flight is not on is worse than one that names none.
_FREQ_AWACS = 251
_FREQ_TANKER = 260
_FREQ_TACP = 133
_TANKER_TACAN = "10X"
_LASER_CODE = 1688

# The sortie's own clock, in mission seconds, and it is set against the route
# rather than guessed: the kneeboard's own route card puts `Dodge` over the AO
# about eight minutes after take-off (98 km at 800 km/h), and the detachment
# comes into the party's sight line around T+16 (see `_ECHELON_SPEED_KPH`).
#
# So the party checks in as the player arrives on station, and the one
# unprompted coordinate readout comes *after* the sighting call rather than
# before it. `core/jtac.py` would happily read out a live position earlier —
# the trucks exist from mission start — but a controller who passes coordinates
# for something he has not yet announced he can see has the conversation
# backwards.
_TANKER_CHECKIN_S = 240
_TACP_CHECKIN_S = 480
_TACP_READOUT_S = 1_050

# Two flags, and both of them are "something that happened has a consequence":
# the detachment having been *seen*, which is what eventually sends the gunships
# after the man who saw it, and the column coming apart, which is what buys the
# Russians a second fighter pair.
_FLAG_ECHELON_SEEN = 20
_FLAG_ESCALATION = 21
#: How long the Russians take to work out where a laser is coming from.
_HIND_DELAY_S = 240

# The second echelon's clock, and it is the mission's pacing rather than a
# detail. 35 km/h is a road march for loaded bowsers on a valley road; with
# 30 km to run that is the Senaki junction at about T+50, and the stretch of
# road `Pinpoint` can actually see (line of sight, measured — see
# `observation_post`) between roughly T+23 and T+36. The player reaches the AO
# around T+13, which leaves the escort phase whole and the strike window in the
# middle third of the sortie where the pacing model in the `dcs-mission` skill
# puts the on-station block.
#
# The 30 km also buys the spacing the briefing claims: the column starts 22 km
# north of Senaki, so this is a dozen kilometres behind it and stays there —
# which is what keeps the launcher below out of `Hawg`'s run.
_ECHELON_SPEED_KPH = 35
_ECHELON_START_M = 30_000
#: Where on the march the launcher joins it — see `_spawn_red_second_echelon`.
_OSA_ROUTE_FRACTION = 0.30

# Which parts of the march `Pinpoint` is placed to see, as fractions of it.
#
# Two points rather than one, and 4 km apart rather than adjacent, because
# `observation_post` requires line of sight to every point it is given and the
# valley is what decides the rest. Measured against the elevation raster: one
# watch point gave a post that could see 3.5 km of road — about six minutes of a
# 35 km/h convoy, which would make the whole second half of the mission a
# six-minute window the player has no way to know about. This pair forces a post
# on the shoulder that sees 12 km of it, from roughly T+16 to T+36, which is a
# window wide enough to be a plan rather than a coincidence.
_TACP_WATCH_FRACTIONS = (0.45, 0.58)
# Past here the trucks are behind the spur for good. Measured rather than
# guessed: the post sees the march in and out from about 0.30 to 0.78 as the road
# weaves behind spurs, so the usable lasing stretch is intermittent — which is
# what a real sight line does, and what the party's own calls say.
_TACP_SIGHT_LOST_FRACTION = 0.80

# Mi-24P: 250 km/h against a 330 km/h never-exceed. The 0.30–0.40 band in
# CLAUDE.md is for supersonic fighters; a Hind, like the A-10C, cruises close to
# its own ceiling and has no afterburner to get wrong.
_HIND_SPEED_KPH = 250


class CoastalCover(MissionBuilder):
    name = "coastal_cover"
    title = "Coastal Cover"
    difficulty = Difficulty.TRAINED

    def __init__(self, *, players: int = 1) -> None:
        super().__init__(players=players)
        self._terrain = Caucasus()
        self._voice = VoiceSynth()
        #: Set by `_render_recon`; `readme` degrades to no figure without it.
        self._still: ReconStill | None = None

    # -- in-game and README briefings ---------------------------------------

    def _in_game_briefing(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""COASTAL COVER — Caucasus, 15 May 2026, 10:00 local
========================================================
SITUATION
  A Reaper feed at first light watched a Russian
  mechanised column form up in the Inguri valley and
  start south on the valley road toward Senaki. That
  column is the escort. The load is behind it — the same
  feed counted a fuel and ammunition detachment forming
  up in the rear of the march, and that is what puts a
  brigade back on the road once it reaches the Senaki
  junction. USAF A-10s (Hawg 1-2) out of Kutaisi are
  fragged against the column. Nobody is fragged against
  the detachment except you.
  Russian fighters at Sukhumi-Babushara went to alert on
  the same warning — expect them airborne once we commit.

MISSION (Dodge — F-16C-50, Batumi)
  1. ESCORT. Push north along the terrain-masked
     corridor, station over the AO and keep the Russian
     alert pair off Hawg's run.
  2. STRIKE. Two GBU-12 for the fuel detachment when it
     comes down the valley road. Pinpoint 1-1 has eyes on
     the road and will lase it for you.
  3. COVER PINPOINT. He is three vehicles in a treeline
     and he is the reason two bombs are enough.

PACKAGE
  Dodge 1 (you): F-16C-50, Batumi, hot ramp.
                 2x AIM-120C, 2x AIM-9X, 2x GBU-12,
                 targeting pod, two bags.
  Hawg 1-2     : A-10C, Kutaisi, strike on the column.
  Eagle 1-2    : F-15C CAP toward Sukhumi. Eagle owns
                 the air-to-air fight — you have four
                 missiles and a strike to fly.
  Magic        : E-3A AWACS, {_FREQ_AWACS}.000 AM.
  Texaco       : KC-135, {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN},
                 west of Batumi over the water.
  Pinpoint 1-1 : TACP in the valley, {_FREQ_TACP}.000 AM,
                 lases on code {_LASER_CODE}.

INTELLIGENCE
  Air : Sukhumi-Babushara holds a MiG-29S pair on alert,
        current generation missiles. A Rivet Joint track
        overnight fixed early-warning radars along the
        frontier — they will see you coming and vector.
        Gudauta holds a second pair, Su-27, and we assess
        they commit only if the column is coming apart.
        That pair is Eagle's. You are not required to
        kill it and you are cleared to leave it flying.
  SAM : The Reaper feed showed a tracked SHORAD launcher
        moving onto high ground overlooking the road.
        SA-13 class, IR, short reach. Stay above 4000 m
        AGL over the target box and it cannot touch you.
        Sukhumi-Babushara itself is defended — an S-125
        battery on the field and guns in the overhead.
        Do not follow anyone home.
        THE GAP: a detachment carrying a brigade's fuel
        does not travel without air defence, and we never
        found it. No ring on your map because we would be
        drawing a guess. Release the bombs from 6000 m or
        above and nothing that size reaches you; go down
        to look at the trucks and you are inside whatever
        is driving with them.
  AAA : Gun vehicles ride with the column, and the same
        imagery showed dug-in guns on the hills either
        side of the valley road.
  Land: Partner-force reporting puts a small armoured
        reserve laagered in the treeline behind the
        column, held back to push through if the lead
        elements are hit. Unconfirmed.
  Heli: Russian rotary is based up the Inguri. If they
        work out where the laser is coming from, that is
        what they will send, and it will come low.

ROE / FRAGS
  - Hold fire on civilian/neutral contacts.
  - Cleared to engage any Russian aircraft entering the AO.
  - Do not overfly the convoy below 4000 m AGL.
  - LGB release at or above 6000 m, outside 8 km slant.
  - Not cleared to pursue over Sukhumi-Babushara.
  - Bingo fuel: 2500 lb. Texaco first, then RTB Batumi.

FALL-BACK ({_SANCTUARY})
  Batumi is covered by a {_SANCTUARY_BATTERY.name} battery —
  {_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, cyan ring on the map, with guns in
  the overhead. If you are hit, out of missiles or out
  of fuel, that ring is the answer: get inside it and
  the fight is over. {_SANCTUARY} MARSHAL is a hold
  abeam the field, in the DED and on the map. Kutaisi
  is a divert but it is outside the ring.

NAV
  Bullseye (own side): {bx:.0f}, {by:.0f} (DCS world m)
  AO center         : ~18 km north-northeast of Senaki.
  PUSH waypoint     : 25 km north of Batumi (corridor IP).
  ECHELON steerpoint: the stretch of valley road Pinpoint
                      is watching. The detachment's own
                      position is his to pass, not ours —
                      F10 -> Other -> Pinpoint 1-1 reads it
                      out in your cockpit's own format.
                      His nine-line is a military grid, as
                      DCS reads it to every airframe; that
                      menu entry is what the DED will take.
  Cartridge         : the SHORAD estimate and the Sukhumi belt
                      are loaded as pre-planned threats — select
                      PRE on the HSD for the rings. They are
                      where the feed and the signals work last
                      had them, not fixes.
  Imagery           : the Reaper's radar cut of the column is
                      on the briefing screen, shot 25 minutes
                      before push. Wide-area search, 50 m
                      posts, so the brackets are moving-target
                      returns and not pictures of vehicles —
                      read it for how long the column is and
                      which road it is on. The detachment is
                      not in the frame; it had not left the
                      assembly area when this was shot.

FREQUENCIES
  Magic AWACS   : {_FREQ_AWACS}.000 AM
  Texaco tanker : {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}
  Pinpoint 1-1  : {_FREQ_TACP}.000 AM
  Batumi tower  : per kneeboard
"""

    def _recon_figure_md(self) -> str:
        """The radar-still figure block, or nothing if no still was published.

        Empty rather than raising, so `readme()` still works on a builder whose
        `_assemble` has not run and at difficulties that withhold the imagery.
        """
        return "" if self._still is None else self._still.markdown()

    def readme(self) -> str:
        bx, by = self._terrain.bullseye_blue["x"], self._terrain.bullseye_blue["y"]
        return f"""# Coastal Cover

**Theater:** Caucasus
**Date / time:** 15 May 2026, 10:00 local
**Player aircraft:** F-16C-50 (`Dodge`), Batumi, hot ramp
**Players:** {self.players} coop slot(s)
**Difficulty:** trained — experienced MiG-29S pair on alert and a second-line
Su-27 pair behind them, current-generation missiles, GCI vectoring, SHORAD over
the target and one unlocated launcher with the second echelon, rotary threat
against the ground party, AWACS + tanker support, a covered field to run to
**Expected sortie length:** ~60 minutes (of which a good ten are spent waiting
for the second echelon to come down the road)

## Situation

A Reaper feed at first light watched a Russian mechanised column form up in
the Inguri valley and start south on the valley road toward Senaki. That
column is the escort. The load is behind it: the same feed counted a fuel and
ammunition detachment forming up in the rear of the march, and that detachment
is what puts a brigade back on the road once it reaches the junction north of
Senaki.

USAF A-10s (`Hawg 1-2`) out of Kutaisi are fragged against the column. Nobody
is fragged against the detachment except `Dodge`. Russian fighters at
Sukhumi-Babushara went to alert on the same warning — expect them airborne once
the package commits.

## Mission

Three tasks, in the order the sortie will hand them to you.

1. **Escort.** Push north along the terrain-masked ingress corridor, take
   station over the AO, and keep the Russian alert pair off `Hawg`'s run.
2. **Strike.** Two GBU-12 for the fuel detachment when it comes down the
   valley road. `Pinpoint 1-1` — a tactical air control party in the treeline
   above the road — has eyes on it and will lase on code `{_LASER_CODE}`.
3. **Cover `Pinpoint`.** He is three vehicles in a treeline, he is the reason
   two bombs are enough for a moving detachment, and the Russians will work out
   where the laser is coming from.

## Package

| Callsign     | Type     | Base    | Role                              |
|--------------|----------|---------|-----------------------------------|
| Dodge        | F-16C-50 | Batumi  | Player escort + LGB strike        |
| Hawg 1-2     | A-10C    | Kutaisi | Strike on the armoured column     |
| Eagle 1-2    | F-15C    | Batumi  | CAP — owns the air-to-air fight   |
| Magic        | E-3A     | Batumi  | AWACS, {_FREQ_AWACS}.000 AM                 |
| Texaco       | KC-135   | Batumi  | Tanker, {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}      |
| Pinpoint 1-1 | TACP     | ground  | Talk-on + laser, {_FREQ_TACP}.000 AM        |

`Dodge` carries two AIM-120C on the wingtips, two AIM-9X, two GBU-12, a
targeting pod, an ECM pod and two 370 gal bags. Four air-to-air missiles is
two kills at the planning factor, which is why `Eagle` owns the air fight and
why nothing airborne is a required kill in this frag. `Texaco` is on station
because the jet launches heavy and then loiters — the fuel goes on waiting for
the second echelon, not on the transit.

## Intelligence

The ground picture below is one Reaper that has been over the Inguri valley
since first light — the column, the launcher and the hill guns are all off that
feed, and the column is located to the road it is driving on. The hill
positions are good to a kilometre or two, which is why the map rings are marked
as estimates. The air and radar picture is not from the feed at all: that is
overnight signals work, and the reserve is partner-force reporting nobody has
confirmed.

{self._recon_figure_md()}

- **Air:** Sukhumi-Babushara holds a MiG-29S pair on alert, current-generation
  missiles, flown by an experienced crew. They will come once we are committed
  over the valley. Gudauta holds a second pair, Su-27, and the assessment is
  that they commit only once the column is coming apart. **That pair is
  `Eagle`'s.** You are not required to kill it and you are cleared to leave it
  flying.
- **EWR:** A Rivet Joint track overnight fixed early-warning radars along the
  Russian frontier. Assume the pair is vectored onto you from the moment you
  cross the coast.
- **SAM:** The Reaper feed showed a tracked SHORAD launcher moving onto high
  ground overlooking the road — SA-13 class, IR-guided, short reach. Stay
  above 4000 m AGL over the target box and it cannot reach you.
- **The gap:** a detachment carrying a brigade's fuel does not travel without
  air defence, and we never found it. There is **no ring on your map for it,
  because we would be drawing a guess.** What the arithmetic says is that
  nothing in that weight class reaches 6000 m: release at or above that and
  outside 8 km slant and it is not your problem. Go down for a look at the
  trucks and it is.
- **Sukhumi-Babushara field defence:** the same overnight signals work that
  found the frontier radars puts an S-125 battery on the airfield, with
  self-propelled guns in the overhead. Short reach and it covers nothing you
  need — but it is the reason a MiG that turns for home stops being a target.
- **AAA:** Gun vehicles ride with the column, and the same imagery showed
  dug-in guns on the hills either side of the valley road.
- **Land reserve:** Partner-force reporting puts a small armoured reserve
  laagered in the treeline behind the column, held back to push through if the
  lead elements are hit. Unconfirmed.
- **Rotary:** Russian attack helicopters are based up the Inguri. If they work
  out where the laser is coming from, that is what they will send, and it will
  come low up the valley where the radar picture is worst.

## ROE

- Hold fire on civilian / neutral contacts.
- Cleared to engage any Russian aircraft entering the AO.
- Do not overfly the convoy below 4000 m AGL.
- **LGB release at or above 6000 m, outside 8 km slant.** That is the answer to
  the launcher nobody found, and it is a hard number rather than advice.
- **Not cleared to pursue over Sukhumi-Babushara.** A withdrawing MiG is not
  worth an S-125.
- The Su-27 pair is a threat to survive, not a target list. Disengaging from it
  is a correct decision, not a failed one.
- Bingo fuel: 2500 lb. Texaco first, then RTB Batumi (divert: Kutaisi).

## Fall-back

Batumi is covered by a `{_SANCTUARY}` {_SANCTUARY_BATTERY.name} battery —
{_SANCTUARY_BATTERY.radius_m / 1000:.0f} km, drawn as the cyan ring on the F10
map — with gun sections in the overhead. That ring is where the sortie stops
being dangerous: if you are hit, out of missiles or below bingo, run for it
rather than turning back into a fight you have already lost. `{_SANCTUARY}
MARSHAL` is a hold abeam the field inside the envelope, on the map and in the
DED, for sorting out a damaged jet or waiting on the pattern.

Kutaisi is a legal divert but it is 97 km from Batumi and **outside** the
envelope — treat it as a runway, not as cover.

## Navigation

- Bullseye (own side): `{bx:.0f}, {by:.0f}` (DCS world m)
- AO center: ~18 km north-northeast of Senaki.
- PUSH waypoint: 25 km north of Batumi (corridor IP).
- ECHELON steerpoint: the stretch of valley road `Pinpoint` is watching. The
  detachment's own position is his to pass, not ours — **F10 → Other →
  Pinpoint 1-1** reads it out in your own cockpit's format, off a live vehicle,
  so it is current for something that is still driving. DCS's own nine-line
  will read you a military grid whatever you are flying; that entry is what
  gives an F-16 driver something the DED will take.
- Your route is a terrain-masked corridor that keeps ridgelines between you
  and the reported launcher and radar positions for as long as it can.
- Your data cartridge carries the SHORAD estimate and the Sukhumi belt as
  pre-planned threats — select PRE on the HSD for the rings. They mark where
  the feed and the signals work last had them, which is the same claim the map
  makes, not a fix.

## Frequencies

- Magic AWACS: {_FREQ_AWACS}.000 AM
- Texaco tanker: {_FREQ_TANKER}.000 AM, TACAN {_TANKER_TACAN}
- Pinpoint 1-1: {_FREQ_TACP}.000 AM
- Batumi tower: per kneeboard
- `{_SANCTUARY}` details, the laser code and the divert are on the kneeboard
  comms card.

## Weather

Spring scattered cumulus, light NW wind, 18 °C. QNH 760 mmHg. Visibility
80 km. Scattered layer at 2400 m, 600 m thick.

## Win / loss conditions

Layered, and the second layer is the one only `Dodge` can do.

- **Primary:** the Russian column is broken up on the valley road and never
  reaches the Senaki junction.
- **Secondary:** the fuel and ammunition detachment behind it is stopped short
  of the same junction. This is the player's task — `Hawg` is dry on the column
  by the time it matters.
- **Full success:** both, with `Pinpoint` still on the air at the end of it.
- **Failure:** `Hawg` is shot down with the column still rolling, or the
  detachment reaches the junction and off-loads.

Losing `Pinpoint` is a real cost rather than a failure: the talk-on and the
laser go with him, and the pass has to be flown self-designating.

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

        red = self._spawn_red_ground(m, russia, scene)
        threats = self._threat_rings(sa13_pos=red.sa13_pos)
        awacs_track = self._spawn_awacs(m, usa, scene)
        tanker_track = self._spawn_tanker(m, usa, scene)
        hog = self._spawn_strike(m, usa, scene, convoy=red.convoy, threats=threats)
        cap_track = self._spawn_cap(m, usa, scene)
        tacp = self._spawn_tacp(m, usa, scene, target=red.pol)
        boris = self._spawn_red_intercept(m, russia, scene)
        sokol = self._spawn_red_escalation(m, russia, scene)
        hinds = self._spawn_red_hinds(m, russia, scene, target=tacp)
        corridor = self._spawn_player(
            m, usa, scene, threats=(red.sa13_pos, *red.ewr_positions)
        )
        home, sukhumi_ad = self._spawn_sanctuaries(
            m,
            usa,
            russia,
            scene,
            # The second echelon and its launcher go in the blue list as well as
            # the AO: they march to within 4 km of Senaki, and a friendly
            # envelope that reached that far would shoot the mission's whole
            # second half off the road before the player got there. This is the
            # check failing loudly rather than the surprise evaporating quietly.
            red_sites=(
                red.sa13_pos,
                *red.ewr_positions,
                red.pol.units[0].position,
                red.osa.units[0].position,
                scene.junction,
            ),
            stations=(
                *awacs_track,
                *cap_track,
                *tanker_track,
                *corridor,
                tacp.units[0].position,
            ),
        )

        self._add_intro_voice(m)
        self._add_support_checkins(m)
        self._add_sanctuary_checkin(m, home)
        sanc.remark_all(m, home, sukhumi_ad)
        self._add_tacp_readout(m, target=red.pol)
        seen = self._add_echelon_sighting_trigger(m, pol=red.pol, osa=red.osa)
        self._add_sight_line_lost_trigger(m, pol=red.pol)
        self._add_shorad_reveal_trigger(m, zone=seen)
        self._add_hind_trigger(m, hinds=hinds)
        self._add_tacp_loss_trigger(m, tacp=tacp)
        self._add_escalation_trigger(m, convoy=red.convoy, boris=boris, sokol=sokol)
        if red.reserve is not None:
            self._add_reserve_trigger(m, convoy=red.convoy, reserve=red.reserve)
        self._add_end_triggers(m, scene, red=red, hog=hog, tacp=tacp)
        self._conceal_red(russia)
        # One overlay for every reveal channel: the F10 plan, the cockpit
        # cartridge and the recon still all have to make the same claim, and the
        # difficulty policy that decides how much they claim lives in here.
        plan = PlanOverlay(m, self.difficulty)
        briefed_threats = self._draw_plan(
            m,
            scene,
            plan=plan,
            convoy=red.convoy,
            sa13_pos=red.sa13_pos,
            ewr_positions=red.ewr_positions,
            corridor=corridor,
            cap_track=cap_track,
            awacs_track=awacs_track,
            tanker_track=tanker_track,
            tacp=tacp,
            home=home,
            sukhumi_ad=sukhumi_ad,
        )
        self._load_cartridge(m, scene, briefed_threats, plan=plan)
        self._render_recon(m, scene, plan=plan, convoy=red.convoy)
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
        Weather(
            name="Spring scattered",
            season_temperature=18.0,
            clouds_base=2400,
            clouds_thickness=600,
            clouds_density=4,
            visibility_distance=80000,
            wind_at_ground=Wind(300, 4),
            wind_at_2000=Wind(290, 7),
            wind_at_8000=Wind(280, 12),
        ).apply(m)

    def _setup_airports(self, m: Mission) -> _Scene:
        """Claim Batumi/Kutaisi for blue, Sukhumi and Gudauta for red, derive the AO.

        `junction` is the one point both Russian echelons are driving at — the
        road junction on the northern outskirts of Senaki. Convoy destination,
        fuel-detachment destination, the failure zone and the F10 label are all
        that single Point rather than four offsets that have to agree with each
        other.
        """
        t = self._terrain
        batumi = t.airports["Batumi"]
        kutaisi = t.airports["Kutaisi"]
        sukhumi = t.airports["Sukhumi-Babushara"]
        gudauta = t.airports["Gudauta"]
        senaki = t.airports["Senaki-Kolkhi"]
        batumi.set_blue()
        kutaisi.set_blue()
        sukhumi.set_red()
        gudauta.set_red()
        ao_center = offset(senaki.position, east_m=4_000, north_m=18_000)
        junction = offset(senaki.position, east_m=-1_000, north_m=4_000)
        overlay = load_scene("caucasus")
        return _Scene(
            batumi, kutaisi, sukhumi, gudauta, senaki, ao_center, junction, overlay
        )

    # -- red side -----------------------------------------------------------

    def _spawn_red_ground(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> _RedGround:
        """The whole Russian ground picture, in the order the sortie meets it."""
        convoy = self._spawn_red_convoy(m, russia, scene)
        sa13_pos = self._spawn_red_shorad(m, russia, scene.ao_center)
        self._spawn_red_aaa_overwatch(m, russia)
        reserve = self._spawn_red_reserve(m, russia)
        self._reserve = reserve
        pol, pol_escort, osa = self._spawn_red_second_echelon(m, russia, scene)
        ewr_positions = self._spawn_red_ewr_chain(m, russia, scene)
        return _RedGround(
            convoy=convoy,
            reserve=reserve,
            pol=pol,
            pol_escort=pol_escort,
            osa=osa,
            sa13_pos=sa13_pos,
            ewr_positions=ewr_positions,
        )

    def _spawn_red_convoy(self, m: Mission, russia: Country, scene: _Scene):
        """Snap-on-road convoy route from Inguri valley to Senaki outskirts.

        `place_convoy_route` snaps origin and destination to the nearest real
        road; the DCS engine paths the platoon between them. Spawn point is
        the snapped origin; the spawn waypoint *and* the destination waypoint
        are OnRoad, so the column follows the valley road all the way to
        Senaki instead of cutting cross-country.
        """
        origin = offset(scene.senaki.position, east_m=2_000, north_m=22_000)
        route = scene.overlay.place_convoy_route(origin, scene.junction)
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
        ad.build_ewr_chain(m, russia, positions, prefix="EWR Bear")
        return positions

    # -- red side: the second echelon ---------------------------------------

    def _spawn_red_second_echelon(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> tuple[VehicleGroup, VehicleGroup, VehicleGroup]:
        """The load the whole march is for: bowsers, an escort, and an SA-8.

        Three groups rather than one, and the split is most of what the second
        half of this mission is made of:

        - `POL Bear` holds only what the frag names — two ATZ-10 bowsers and an
          ammunition truck — so a two-bomb magazine is measured against three
          soft trucks and not against an escort nobody asked the player to kill.
        - the BTR-80 is its own group, so killing it neither counts toward the
          objective nor spares the trucks.
        - the SA-8 is its own group for two reasons. Inside the objective's
          group `Pinpoint` would lase it along with the bowsers and it would need
          a ring on the plan — and a ring is the one thing this launcher must not
          have (see `_draw_plan`). Being separate is also what lets it be
          **late-activated**, which is the fix for a real defect in the first
          version of this mission: emplaced from mission start it began the
          sortie 10 km north of the AO and drove south through it, so an Osa
          nobody was briefed on was inside `Hawg`'s 4,000 m run on the column at
          about T+17. A withheld threat that breaks the *briefed* plan is a bug
          wearing a fog-of-war costume; this one has to bite the player's own low
          pass and nothing else. It comes on the road at
          `_OSA_ROUTE_FRACTION` — where the trucks are when `Pinpoint` calls
          them, about T+16 — so it exists only from the moment the player is told
          the detachment exists, and by then `Hawg` has made its run and is
          egressing. It still ends up within reach of the AO later, on the road,
          which is correct: an Osa escorting fuel would shoot at an A-10 it could
          see. What it can no longer do is be there before anybody was told.

        The trucks and their escort roll from mission start rather than being
        activated on approach: a late-activated group is a group that does not
        exist when the FAC task and the coordinate readout bind to it, and the
        reveal activation would have bought is bought instead by
        `_add_echelon_sighting_trigger`. The detachment is concealed like every
        other Russian group here, so what the player knows about it is what
        `Pinpoint` tells him — which is also the honest answer to how anybody
        knows.
        """
        origin = offset(scene.junction, east_m=5_000, north_m=_ECHELON_START_M)
        route = scene.overlay.place_convoy_route(origin, scene.junction)
        self._pol_route = route
        spawn = route.waypoints[0]
        heading = int(spawn.heading_between_point(route.waypoints[-1]))

        pol = self._road_march(
            m,
            russia,
            "POL Bear",
            [
                vehicles.Unarmed.ATZ_10,
                vehicles.Unarmed.ATZ_10,
                vehicles.Unarmed.Ural_375,
            ],
            spawn,
            heading,
        )
        escort = self._road_march(
            m,
            russia,
            "POL Escort Bear",
            [vehicles.Armor.BTR_80],
            self._behind_on_road(spawn, heading, 500.0),
            heading,
        )
        osa = self._road_march(
            m,
            russia,
            "SHORAD Bear-8",
            [vehicles.AirDefence.Osa_9A33_ln],
            self._road_at_fraction(_OSA_ROUTE_FRACTION),
            heading,
        )
        osa.late_activation = True
        set_skill(pol, Skill.Average)
        set_skill(escort, Skill.Average)
        set_skill(osa, Skill.High)
        return pol, escort, osa

    def _road_march(
        self,
        m: Mission,
        russia: Country,
        name: str,
        types: list[type[VehicleType]],
        spawn: Point,
        heading: int,
    ) -> VehicleGroup:
        """One group marching down the valley road to the Senaki junction.

        Spawn waypoint *and* destination waypoint are `OnRoad`, the same shape
        `_spawn_red_convoy` uses — off-road either end and the DCS engine cuts
        the corner across a field the briefing says is a road.
        """
        grp = m.vehicle_group_platoon(
            russia,
            name,
            types,
            position=spawn,
            heading=heading,
            move_formation=PointAction.OnRoad,
        )
        grp.add_waypoint(
            self._pol_route.waypoints[-1],
            move_formation=PointAction.OnRoad,
            speed=_ECHELON_SPEED_KPH,
        )
        return grp

    def _echelon_overwatch(self) -> Point:
        """The stretch of valley road the sortie's second half happens on.

        One point, four consumers: the party watches it, the player gets a
        steerpoint on it, the sighting trigger's zone sits on it and the F10 plan
        is drawn around it. Snapped to the road, because the midpoint of a
        straight line between two road-snapped endpoints is not itself on the
        road — this valley bends — and both the elevation the steerpoint carries
        and the line of sight the party needs are properties of the road, not of
        the average of its ends.
        """
        route = self._pol_route
        return self._on_road(route.waypoints[0].midpoint(route.waypoints[-1]))

    def _road_at_fraction(self, fraction: float) -> Point:
        """A road point `fraction` of the way down the detachment's march.

        Straight-line interpolation between the two road-snapped ends and then
        snapped back to the road, which is the same approximation
        `place_ambush_on_route` makes and is honest about: the overlay knows where
        the roads are but not how the DCS engine will path between two points on
        them, so a fraction of the *march* is not exactly a fraction of the
        driving. Close enough for "roughly where the trucks will be when somebody
        calls them", which is all any caller wants it for.
        """
        a, b = self._pol_route.waypoints[0], self._pol_route.waypoints[-1]
        return self._on_road(
            Point(
                a.x + fraction * (b.x - a.x),
                a.y + fraction * (b.y - a.y),
                a._terrain,
            )
        )

    def _behind_on_road(self, spawn: Point, heading: int, distance_m: float) -> Point:
        """A road point `distance_m` back up the detachment's own axis.

        Snapped to the road rather than offset in a straight line, so the escort
        and the launcher start *on* the march route instead of in the field beside
        whichever bend the column happened to spawn on. The first waypoint is
        `OnRoad` either way, so the fallback costs a hundred metres of grass.
        """
        return self._on_road(
            spawn.point_from_heading((heading + 180.0) % 360.0, distance_m)
        )

    def _on_road(self, near: Point) -> Point:
        """`near` snapped to the nearest road, or `near` itself if there is none.

        The overlay's road sidecar keeps only the major network, so a snap can
        legitimately come up dry on a valley track — returning the unsnapped
        point is the honest failure here rather than a raise, since every caller
        either drives `OnRoad` from it or only wants the elevation under it.
        """
        try:
            return self._scene.overlay.overlay.find_road_spawn(near, radius_m=3_000)
        except LookupError:
            return near

    def _spawn_red_hinds(
        self, m: Mission, russia: Country, scene: _Scene, *, target: VehicleGroup
    ) -> FlyingGroup:
        """2x Mi-24P up the valley after the man doing the lasing.

        This is the answer to a player who solves the strike problem *correctly*.
        `Pinpoint`'s laser is what makes two GBU-12s enough against a moving
        detachment, and a Russian battalion works out where a laser is coming
        from the same way anyone would — so the counter is not another SAM, it is
        a pair of gunships hunting a three-vehicle team in a treeline.

        What that buys the sortie is a threat in a part of the sky nothing else
        here uses: low, slow, in the ground clutter, inside the valley the player
        has spent the whole mission staying above. And losing the race costs the
        laser rather than the mission — the F-16C can self-designate with the
        pod, at the price of flying the pass itself.

        Spawned in the air, and the docstring should say why rather than pretend:
        a pair like this comes off a forward FARP up the Inguri, the mission does
        not model one, and what it does model is that they are not there at push
        and are *called* as they come.

        The spawn is 22 km up the valley from the party rather than deep in the
        range, and that is a timing decision. At 250 km/h every ten kilometres is
        two and a half minutes; the first attempt put them 52 km out at 2,300 m
        in the high Caucasus, which arrived a quarter of an hour after the flag —
        i.e. after the strike it is supposed to complicate. Five minutes plus the
        four `_HIND_DELAY_S` buys is a threat during the pass.

        `alt_type = "RADIO"` is the detail that makes the route flyable. Every
        other altitude in pydcs is metres AMSL, so 80 m AMSL up a valley whose
        floor is at 300 m is a route into the hillside; the spawn point has no
        preceding waypoint to ramp from, so that one is AMSL off the elevation
        raster instead.
        """
        ov = scene.overlay.overlay
        start = offset(self._pol_route.waypoints[0], east_m=2_000, north_m=14_000)
        hinds = m.flight_group(
            country=russia,
            name="Krokodil",
            aircraft_type=helicopters.Mi_24P,
            airport=None,
            position=start,
            altitude=int(waypoints.ground_elevation_m(ov, start) + 300.0),
            speed=_HIND_SPEED_KPH,
            maintask=task.CAS,
            group_size=2,
        )
        hinds.late_activation = True
        hunt_at = target.units[0].position
        for name, pos in (
            ("VALLEY", start.midpoint(hunt_at)),
            ("HUNT", hunt_at),
        ):
            wp = hinds.add_waypoint(pos, altitude=80, speed=_HIND_SPEED_KPH, name=name)
            wp.alt_type = "RADIO"
        hinds.points[-1].tasks.append(
            task.AttackGroup(
                target.id,
                weapon_type=task.WeaponType.Auto,
                group_attack=True,
                expend=task.Expend.All,
            )
        )
        hinds.land_at(scene.gudauta)
        set_skill(hinds, Skill.Average)
        apply_ai_difficulty(hinds, self.difficulty)
        return hinds

    def _spawn_red_escalation(
        self, m: Mission, russia: Country, scene: _Scene
    ) -> FlyingGroup:
        """A Su-27 pair at Gudauta, cold — and deliberately not a tasked kill.

        `Dodge` launches with four air-to-air missiles, two AMRAAM on the wingtips
        and two AIM-9X, because the other stations carry the fuel, the pod and the
        two bombs this sortie is now built around. Four missiles is two kills at
        the planning factor and `Boris` is already two aircraft, so a third and
        fourth bandit cannot be a frag: `Eagle` is what commits on this pair, no
        win condition names it, and both briefings say so in as many words. The
        alternative is six bandits against four missiles, which is the arithmetic
        `abkhaz_sweep` shipped with — see CLAUDE.md on the magazine being the
        budget.

        Cold on the ramp rather than late-activated, so the scramble is *caused*
        by the column coming apart and the player is paid the four or five minutes
        it takes a second-line pair to start, taxi and climb out. `Skill.Average`
        for the same reason: Sukhumi has already sent its alert crew.
        """
        sokol = m.flight_group_from_airport(
            country=russia,
            name="Sokol",
            aircraft_type=planes.Su_27,
            airport=scene.gudauta,
            maintask=task.CAP,
            start_type=StartType.Cold,
            group_size=2,
        )
        sokol.add_runway_waypoint(scene.gudauta)
        sokol.add_waypoint(scene.ao_center, altitude=7_000, speed=850, name="AO")
        sokol.add_waypoint(scene.junction, altitude=6_000, speed=800, name="VALLEY")
        sokol.land_at(scene.gudauta)
        set_skill(sokol, Skill.Average)
        apply_ai_difficulty(sokol, self.difficulty)
        return sokol

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
            speed=900,
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
        """E-3A Magic on a coastal race-track west of Batumi, 251.000 AM.

        Hand-placed for the same reason `_spawn_tanker` is, and this one was
        wrong before: `place_awacs_track` anchors the track on the far side of
        the field from the threat axis, and the far side of Batumi from
        Sukhumi-Babushara is inland Turkey — the orbit it returned sat over
        2,400 m of mountain while every briefing in this mission called it a
        Black Sea track. Offshore and running up the coast keeps the standoff,
        keeps the radar horizon over the water the MiGs cross, and makes the
        drawn orbit the thing the prose says it is.

        The numbers are bounded at both ends and neither bound is arbitrary. Far
        enough out that the northern end stays 80 km from Sukhumi-Babushara —
        under that, `_spawn_sanctuaries` refuses the build, because the S-125 on
        that field is in the red `keep_clear` list against exactly this kind of
        drift (a first attempt at this track put the orbit 29 km off the battery
        and the check caught it). Close enough in that a 8,500 m orbit still
        holds the valley and the coastal approach the MiGs use.
        """
        p1 = offset(scene.batumi.position, east_m=-80_000, north_m=-10_000)
        p2 = offset(p1, east_m=0, north_m=70_000)
        track = race_track(p1, p2)
        m.awacs_flight(
            usa,
            "Magic",
            plane_type=planes.E_3A,
            airport=scene.batumi,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=8500,
            speed=740,
            start_type=StartType.Warm,
            frequency=251,
        )
        return p1, p2

    def _spawn_strike(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        convoy: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ):
        """A-10C 2-ship Hawg from Kutaisi, routed onto the convoy.

        Built by hand rather than with `Mission.strike_flight`, which drops an
        attack waypoint pydcs hard-codes to `alt = 0`: the pair descended to sea
        level directly over the column's SA-13 and its gun vehicles, which is
        the one place an A-10 must not be. The run-in is flown at a height the
        Mavericks reach from and the IR SHORAD does not.
        """
        hog = m.flight_group_from_airport(
            country=usa,
            name="Hawg",
            aircraft_type=planes.A_10C,
            airport=scene.kutaisi,
            maintask=task.CAS,
            start_type=StartType.Warm,
            group_size=2,
        )
        set_skill(hog, Skill.High)
        arm(
            hog,
            planes.A_10C,
            [
                (1, "ALQ_184"),
                (2, "Mk_82___500lb_GP_Bomb_LD"),
                (3, "LAU_117_AGM_65G"),
                (9, "LAU_117_AGM_65G"),
                (10, "Mk_82___500lb_GP_Bomb_LD"),
                (11, "LAU_105_1_AIM_9M_R"),
            ],
        )
        apply_threat_reaction(hog)
        self._route_strike(hog, scene, convoy=convoy, threats=threats)
        return hog

    def _route_strike(
        self,
        hog,
        scene: _Scene,
        *,
        convoy: VehicleGroup,
        threats: tuple[ThreatRing, ...],
    ) -> None:
        """Kutaisi → IP → run-in on the column → egress → Kutaisi.

        The column is the target and its SHORAD travels with it, so that ring
        covers the target and `avoid_threats` rightly leaves it alone — the
        exposure on the run-in is the mission. What routing buys here is the
        transit: the EWR-covered ground and the SA-13's hilltop are bent around
        on the way in and out instead of flown over.
        """
        target = scene.ao_center
        hog.add_runway_waypoint(scene.kutaisi)
        ip = routing.standoff_point(
            target,
            toward=scene.kutaisi.position,
            threats=threats,
            min_distance_m=18_000.0,
            clearance_m=3_000.0,
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                scene.kutaisi.position, ip, threats, clearance_m=4_000.0
            )[1:],
            start=1,
        ):
            hog.add_waypoint(pt, altitude=4_600, speed=520, name=f"INGRESS-{i}")
        # 4,000 m keeps the pair inside Maverick range of the column and above
        # the Strela-10 and the gun vehicles riding with it. pydcs's own attack
        # waypoint would have put this at zero.
        attack = hog.add_waypoint(target, altitude=4_000, speed=500, name="ATTACK")
        attack.tasks.append(
            task.AttackGroup(
                convoy.id,
                weapon_type=task.WeaponType.Auto,
                group_attack=True,
                expend=task.Expend.All,
            )
        )
        for i, pt in enumerate(
            routing.avoid_threats(
                target, scene.kutaisi.position, threats, clearance_m=4_000.0
            )[1:-1],
            start=1,
        ):
            hog.add_waypoint(pt, altitude=4_600, speed=540, name=f"EGRESS-{i}")
        hog.add_runway_waypoint(scene.kutaisi)
        hog.land_at(scene.kutaisi)

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
            speed=800,
            altitude=7500,
            max_engage_distance=80_000,
            group_size=2,
        )
        set_skill(eagle, Skill.High)
        arm(
            eagle,
            planes.F_15C,
            [
                (1, "AIM_9M_Sidewinder_IR_AAM"),
                (3, "AIM_9M_Sidewinder_IR_AAM"),
                (4, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (5, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (6, "Fuel_tank_610_gal"),
                (7, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (8, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (9, "AIM_9M_Sidewinder_IR_AAM"),
                (11, "AIM_9M_Sidewinder_IR_AAM"),
            ],
        )
        apply_threat_reaction(eagle)
        return p1, p2

    def _spawn_tanker(
        self, m: Mission, usa: Country, scene: _Scene
    ) -> tuple[Point, Point]:
        """KC-135 Texaco on a coastal track west of Batumi, TACAN 10X.

        The escort-only version of this mission had no tanker and did not need
        one: fifty minutes on internal fuel and two bags. `Dodge` now launches
        with two GBU-12s and a pod on top of that and then *waits* — eight
        minutes to the AO, a MiG fight and somebody else's strike, and a target
        that is not in the party's sight line until about T+16 and may not be
        killed until T+36. It is the loiter rather than the transit that puts the
        sortie past where "F-16C internal plus ten minutes" is a plan rather than
        a hope.

        Hand-placed rather than taken from `place_tanker_track`, and the reason
        is geometry the helper cannot know: it anchors a track on the far side of
        the field from the threat axis, and the far side of Batumi from the
        Inguri valley is the Turkish border ridge. West of the field is water,
        60 km of it, and that is where a tanker belongs — close enough that a
        pass costs the player minutes rather than the sortie.
        """
        p1 = offset(scene.batumi.position, east_m=-45_000, north_m=-10_000)
        p2 = offset(p1, east_m=0, north_m=50_000)
        track = race_track(p1, p2)
        m.refuel_flight(
            usa,
            "Texaco",
            plane_type=planes.KC_135,
            airport=scene.batumi,
            position=track.position,
            race_distance=track.race_distance,
            heading=track.heading,
            altitude=6_500,
            speed=750,
            start_type=StartType.Warm,
            frequency=_FREQ_TANKER,
            tacanchannel=_TANKER_TACAN,
        )
        return p1, p2

    def _spawn_tacp(
        self, m: Mission, usa: Country, scene: _Scene, *, target: VehicleGroup
    ) -> VehicleGroup:
        """Pinpoint 1-1: three vehicles in the treeline above the valley road.

        This is what makes two bombs enough. A moving detachment on a road is a
        hard LGB problem for a single jet with no wingman to buddy-lase, and the
        honest fix is not a bigger magazine — it is a controller on the ground
        who can see the road, talk the pilot on and hold a spot on a truck.
        `core/tasking.fac_attack_group` does the lasing and the talk-on;
        `_add_tacp_readout` adds the one thing DCS gets wrong for this airframe,
        which is reading a military grid to a cockpit that takes degrees and
        decimal minutes.

        `FacCallsign.PINPOINT` rather than the group name: a DCS FAC answers to
        its index in the game's own callname table, so a group called `Pinpoint`
        left on the default index checks in as *Axeman 1-1* and every line of the
        briefing is then wrong (see `core/tasking.FacCallsign`).

        `observation_post` rather than `place_ambush_on_route`, and the difference
        is the whole feature: the ambush helper picks concealment near a road and
        says nothing about sight lines, and the spot it chose here sat 830 m from
        the march route, 38 m *below* it and behind a rise — a controller who
        checks in, talks the player on and never lases, which from the cockpit
        looks like a mission bug. Line of sight to the road is a hard filter, and
        it is asked for at two points 4 km apart rather than at one; see
        `_TACP_WATCH_FRACTIONS` for what that difference is worth here.

        Deliberately **not** `SetInvisibleCommand`. An airborne FAC over a MEZ
        gets that treatment because the alternative is losing the laser in the
        first two minutes to something the player cannot fight; a ground party in
        a treeline is a different bargain — it is survivable, it is findable, and
        making it killable is what gives `_spawn_red_hinds` something to hunt and
        the player something to defend. It sits near the road because a JTAC
        lases what its own sensor sees, not what the mission tells it about.
        """
        pos = observation_post(
            scene.overlay,
            [self._road_at_fraction(f) for f in _TACP_WATCH_FRACTIONS],
        )
        tacp = m.vehicle_group_platoon(
            usa,
            "Pinpoint",
            cast(
                list[type[VehicleType]],
                [
                    vehicles.Unarmed.Hummer,
                    vehicles.Infantry.JTAC,
                    vehicles.Infantry.Soldier_M4,
                ],
            ),
            position=pos,
            heading=int(pos.heading_between_point(scene.junction)),
        )
        set_skill(tacp, Skill.Average)
        fac_attack_group(
            tacp,
            target,
            designation=task.Designation.Laser,
            frequency=_FREQ_TACP,
            modulation=task.Modulation.AM,
            callsign=FacCallsign.PINPOINT,
        )
        return tacp

    def _spawn_player(
        self,
        m: Mission,
        usa: Country,
        scene: _Scene,
        *,
        threats: tuple[Point, ...],
    ) -> list[Point]:
        """Dodge F-16C-50 from Batumi, hot ramp: escort out, strike back.

        The loadout is the mission statement, and it is ED's own
        `AIM-120C*2, AIM-9X*2, GBU-12*2, FUEL*2, ECM, TGP` fit read off
        `<DCS>/CoreMods/aircraft/F-16C/UnitPayloads/F-16C_50.lua` rather than
        assembled from memory — AMRAAM on the wingtips, Sidewinders on 2/8
        because 3/7 are carrying the bombs, tanks on 4/6, ALQ-184 on the
        centreline and the pod on 11 (see the loadout rule in CLAUDE.md, and note
        that "legal in pydcs" and "a fit somebody flies" are different tests).

        What it costs is four air-to-air missiles instead of six, and that is the
        number the whole force balance is priced against: two kills at the
        planning factor, which is `Boris` and nothing else. Every other airborne
        threat in the mission is explicitly somebody else's or explicitly
        declinable (`_spawn_red_escalation`). What it buys is that the second
        half of the sortie exists at all.

        Two bags rather than one, unlike `idlib_gauntlet`'s Pontiac: this is a
        98 km radius with a hold over the AO through someone else's strike before
        its own, and the jet is still only ~15.5 t against a 19.2 t max gross —
        the weight that put Pontiac in burner was 83 % of max, not two tanks.
        `Texaco` is there for the hold, not for the transit.
        """
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
                (3, "GBU_12___500lb_Laser_Guided_Bomb"),
                (4, "Fuel_tank_370_gal"),
                (5, "ALQ_184_Long"),
                (6, "Fuel_tank_370_gal"),
                (7, "GBU_12___500lb_Laser_Guided_Bomb"),
                (8, "AIM_9X_Sidewinder_IR_AAM"),
                (9, "AIM_120C_AMRAAM___Active_Radar_AAM"),
                (11, "AN_AAQ_28_LITENING___Targeting_Pod_"),
            ],
        )
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
            player.add_waypoint(pt, altitude=6500, speed=800, name=name)
        # The strike half of the sortie gets a steerpoint, and it goes on the
        # *road* rather than on the detachment. The vehicles are driving, their
        # position is Pinpoint's to pass (`_add_tacp_readout`), and what a
        # cartridge can honestly carry is the stretch of road he is watching.
        # `add_ground_waypoint` puts the terrain elevation under it: left at the
        # route altitude the steerpoint would tell the pod and the CCRP page that
        # the valley floor is at 6,500 m.
        waypoints.add_ground_waypoint(
            player,
            self._echelon_overwatch(),
            overlay=scene.overlay.overlay,
            speed=750,
            name="ECHELON",
        )
        player.add_runway_waypoint(scene.batumi)
        player.land_at(scene.batumi)
        return list(corridor)

    # -- somewhere to fall back to ------------------------------------------

    def _spawn_sanctuaries(
        self,
        m: Mission,
        usa: Country,
        russia: Country,
        scene: _Scene,
        *,
        red_sites: tuple[Point, ...],
        stations: tuple[Point, ...],
    ) -> tuple[sanc.Sanctuary, sanc.Sanctuary]:
        """A covered field at each end: Batumi under Hawk, Sukhumi under S-125.

        The system choice on both sides is fixed by `keep_clear` rather than
        chosen. Batumi's battery sits 4.5 km up the axis toward Senaki, so a
        Patriot's 100 km would cover the convoy road, the Shilka on the hill and
        the MiGs' whole approach, and there would be no mission left; Hawk stops
        49 km short of the AO and still covers the field, the overhead and the
        pattern. Sukhumi gets 18 km rather than an S-75's 43 because `Magic`'s
        track sits 55 km off it and the mission needs that AWACS alive.

        **The two `keep_clear` lists are not the same list, and that is the
        distinction the helper cannot make for a mission.** What has to stay out
        of *our* umbrella is anything the enemy needs left standing — the AO, the
        SHORAD and the EWR chain — and nothing else: `Eagle`'s CAP station is
        45 km up the axis and the corridor's PUSH point 25 km north of the field,
        both comfortably inside the ring, and both are supposed to be. What has
        to stay out of *theirs* is every friendly station and the whole ingress
        corridor. Passing the overlay-placed tracks rather than trusting the
        arithmetic is the point: the overlay moves a track when its terrain
        search comes up dry.

        Kutaisi is the briefed divert but it is 97 km from Batumi, well outside
        the umbrella, so it is deliberately **not** an alternate here — the
        helper would warn and the briefing would be claiming cover that is not
        there.
        """
        home = sanc.build_sanctuary(
            m,
            usa,
            scene.batumi,
            callsign=_SANCTUARY,
            facing=scene.ao_center,
            battery=_SANCTUARY_BATTERY,
            keep_clear=[scene.ao_center, *red_sites],
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        sukhumi_ad = sanc.build_sanctuary(
            m,
            russia,
            scene.sukhumi,
            callsign="Sukhumi field",
            facing=scene.ao_center,
            battery=sanc.SA_3,
            enemy=True,
            label="SA-3 Sukhumi",
            keep_clear=[scene.ao_center, *stations],
            skill=Skill.Average,
            overlay=scene.overlay.overlay,
            terrain=self._terrain,
        )
        return home, sukhumi_ad

    def _add_sanctuary_checkin(self, m: Mission, home: sanc.Sanctuary) -> None:
        """Tell the player the umbrella is there, once, on the climb-out.

        Without this the feature is invisible: a cyan ring on the F10 map is
        easy to read as decoration, and nobody opens the map again after push.
        Same argument as `core/jtac`'s `push_at_s`.
        """
        mission_triggers.checkin(
            m,
            at_seconds=150,
            comment="BULLDOG umbrella check-in",
            voice=self._voice,
            text=sanc.checkin_text(home, controller="Magic"),
        )

    # -- F10 map briefing ---------------------------------------------------

    def _conceal_red(self, russia: Country) -> None:
        """Keep every Russian group off the F10 map, the planner and the datalink.

        The player's picture of the enemy is the briefing plus what
        `_draw_plan` chooses to show — never a stock unit icon.
        """
        conceal_country(russia)

    def _threat_rings(self, *, sa13_pos: Point) -> tuple[ThreatRing, ...]:
        """The convoy's SHORAD as an envelope, for the plan and the AI route.

        One radius, two consumers: what `_draw_plan` paints as the estimated
        ring is what the strike pair flies around, so the briefing and the
        friendly flight plan cannot disagree. The EWRs are not here — they
        cannot shoot, so nothing needs to route around them.
        """
        return (ThreatRing(sa13_pos, 8_000.0, "SA-13"),)

    def _draw_plan(
        self,
        m: Mission,
        scene: _Scene,
        *,
        plan: PlanOverlay,
        convoy,
        sa13_pos: Point,
        ewr_positions: list[Point],
        corridor: list[Point],
        cap_track: tuple[Point, Point],
        awacs_track: tuple[Point, Point],
        tanker_track: tuple[Point, Point],
        tacp: VehicleGroup,
        home: sanc.Sanctuary,
        sukhumi_ad: sanc.Sanctuary,
    ) -> list[dtc.ThreatPoint]:
        """Paint the plan on the F10 map (trained: coarse, estimated threats).

        Returns the estimated air-defense rings as HSD threat points, so the
        escort's cockpit shows the same claim as the map. The column and the
        EWRs stay map-only — neither is a missile envelope to stay outside of.

        **What is not here is the point.** The SA-8 travelling with the fuel
        detachment gets no ring, no icon and no cartridge point, because the
        briefing says nobody found it; drawing an estimate would be inventing the
        fix the Intelligence section admits it does not have, and `core/dtc.py`
        would then load coordinates for it into the DED. The friendly package is
        not planned around it either — `_threat_rings` does not know about it — so
        the option that covers it is `apply_threat_reaction` and the thing that
        covers the *player* is the briefed release floor.
        """
        # The sanctuary goes on first so its marshal point is the first mark in
        # the cartridge's navigation tab: `core/dtc.py` fills those in draw order
        # after the flight's own route, and the one mark a pilot may need with a
        # broken jet should not lose a budget fight to a tanker track.
        home.draw(plan)
        plan.objective(scene.ao_center, "AO — convoy axis", radius=6_000.0)
        plan.route(corridor, "Dodge ingress")
        # The second half of the sortie is drawn before the support orbits, and
        # the order is load-bearing: `core/dtc.py` fills the cartridge's
        # navigation tab in *draw* order across marks and orbits alike, so the
        # last thing drawn is the first thing to lose a budget fight. Today this
        # mission uses 18 of its 25 steerpoints and nothing is dropped — but if a
        # longer route ever tightens that, the party and the junction the whole
        # strike is about should outlive a tanker track's midpoint.
        #
        # Both are drawn precisely because both are friendly or public geography:
        # the party's own position is ours to know, and a road junction on the
        # edge of Senaki is on every map either side owns. The junction is
        # labelled rather than ringed because what matters about it is that two
        # Russian echelons are driving at it, which is a fact about them.
        plan.waypoint_label(
            tacp.units[0].position, f"PINPOINT 1-1 — TACP, laser {_LASER_CODE}"
        )
        plan.waypoint_label(scene.junction, "SENAKI JCT — both echelons' objective")
        plan.orbit(*cap_track, "Eagle CAP")
        plan.orbit(*awacs_track, "Magic AWACS")
        plan.orbit(*tanker_track, "Texaco AAR")
        # The column and the Shilka riding in it are on the road, so they get a
        # mark and no envelope — a ring at the spawn point would claim reach
        # over ground the column has already driven off.
        plan.mobile_threat(
            convoy.units[0].position, "Convoy", icon=StandardIcon.Mechanized
        )
        hsd = dtc.briefed(
            plan.threat(
                sa13_pos, radius=8_000.0, label="SA-13", icon=StandardIcon.AirDefense
            ),
            dtc.SA_13,
            label="SA-13",
        )
        for pos in ewr_positions:
            plan.threat(pos, radius=4_000.0, label="EWR", icon=StandardIcon.SearchRadar)
        # Sukhumi's own belt is a red ring like any other — estimated, and into
        # the cartridge beside the SHORAD. It is 145 km from Batumi and reaches
        # 18 km, so it costs nothing to fly the sortie and everything to chase a
        # MiG onto its own runway.
        return hsd + sukhumi_ad.draw(plan)

    def _load_cartridge(
        self,
        m: Mission,
        scene: _Scene,
        points: list[dtc.ThreatPoint],
        *,
        plan: PlanOverlay,
    ) -> None:
        """Load the briefed SHORAD as a pre-planned threat on the escort's cartridge.

        The same point is recorded for the kneeboard's threat block, so the Hog
        driver — who has no cartridge to load — reads the coordinates the Viper
        sees as a ring.

        The same cartridge carries the rest of the plan the F10 map shows: the
        flight's own route and the plan's marks as steerpoints, its lines as the
        HSD's GEO lines. The map and the cockpit are one briefing, drawn from
        one set of positions.
        """
        dtc.arm_hsd_threats(m, points, overlay=scene.overlay.overlay)
        dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)

    # -- the imagery the briefing cites --------------------------------------

    def _render_recon(
        self, m: Mission, scene: _Scene, *, plan: PlanOverlay, convoy: VehicleGroup
    ) -> None:
        """Ship the radar cut of the column the Situation paragraph already claims.

        Every intelligence line in this briefing is sourced to one Reaper that
        has been over the Inguri valley since first light — the column forming
        up, the launcher going onto high ground, the guns on the hills. This is
        the one thing that feed can actually be shown doing: a wide-area cut of
        the column on the valley road, stamped shortly before push, so the
        picture is the column where the mission puts it rather than where it was
        five hours ago.

        `plan.detections` is the reveal gate, so the still cannot out-claim the
        F10 map, and at `veteran`/`ace` it returns nothing and no frame is
        published. Only the column is marked. The launcher on the high ground
        and the dug-in guns are a ring and an icon on the map instead: at 50 m
        posts a gun pit is a fifth of a pixel, and bracketing one here would be
        a third, better-looking estimate of a site the map and the cartridge
        already place — three claims about one launcher, disagreeing.

        Returns are laid along the real road by `road_column` rather than read
        off the group: pydcs stacks a platoon abeam its heading and DCS only
        strings it out along the road once the mission runs, so the build-time
        positions are a dash at right angles to the road the briefing names.

        The 1.2 km registration bias `detections` defaults to is cut to 200 m
        because this frame paints roads and braided river to register against —
        see that method on why the number is calibration and not a difficulty
        concession. Measured here, the default put the column 1.0–1.2 km from
        any road in a picture that draws the roads, which contradicted the
        still's own footer.
        """
        route = self._convoy_route
        column = road_column(
            scene.overlay.overlay,
            route.waypoints[0],
            route.waypoints[-1],
            len(convoy.units),
        )
        returns = plan.detections(column, bias_m=200.0, jitter_m=60.0)
        if not returns:
            return

        # `road_column` walks away from the spawn point, so the last return is
        # the lead vehicle. Track is measured off the column itself rather than
        # off the route's end-to-end bearing: the valley road bends, and the
        # tick has to point the way the picture shows the road running.
        tail, lead = returns[0], returns[-1]
        axis = tail.heading_between_point(lead)
        # Centred on the column rather than on the route: the frame is 25.6 km
        # wide against an 18 km march, so centring the route would put the column
        # itself out at the edge. A quarter turn off the column axis lays the road
        # across the long dimension.
        frame = Frame.along_axis(tail, lead, heading_offset_deg=-90.0)
        column_marks = [Mark(x=p.x, y=p.y) for p in returns]
        column_marks.append(
            Mark(
                x=tail.midpoint(lead).x,
                y=tail.midpoint(lead).y,
                kind="group",
                radius_m=max(tail.distance_to_point(lead) * 0.6, 700.0),
                track_deg=axis,
                text=f"{len(returns)} DET  TRK {axis:03.0f}  40 KM/H",
            )
        )
        # Settlement names, so the reader can find the stretch of road this is.
        # The column's symbology is passed as `avoid` and drawn last: it is what
        # the frame is about, and nothing may print into it.
        marks = [
            *landmark_marks(scene.overlay.overlay, frame, avoid=column_marks),
            *column_marks,
        ]
        self._still = recon.sensor_still(
            m,
            frame,
            marks,
            Chrome(
                platform="MQ-9 / AN-APY-8 LYNX II",
                mode="WAS-MTI  5 LOOK",
                # 25 minutes before the mission clock (`_set_time`) — the last
                # cut off a feed that has been up since first light, which is
                # why the column is still where the picture has it.
                taken_at="0935L  15 MAY 26",
                classification="SECRET // REL FVEY",
                footer=f"{len(returns)} DET  INGURI VALLEY RD",
                caption=(
                    "The Reaper's radar cut of the valley road, 25 minutes before "
                    "push. The base is a 50 m radar mosaic and the brackets are "
                    "moving-target returns, not imagery — count them for how long "
                    "the column is and which road it is on, not for what is in it. "
                    "The gun vehicle riding with the column is one of these "
                    "returns and cannot be told from the rest. Named villages are "
                    "on the frame to tie it to your map. The fuel detachment "
                    "is not in this cut — it had not left the assembly area "
                    "when the frame was shot."
                ),
            ),
            overlay=scene.overlay.overlay,
            slug=self.name,
            label="convoy",
        )

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

    def _add_intro_voice(self, m: Mission) -> None:
        """Magic's mission-start picture: both echelons, the clock, the tanker.

        The mission's whole shape is that the column is not the point, and a
        player who does not hear that early plans a fifty-minute escort and then
        has ten minutes to solve a strike. This says it once, at load, in the
        order the sortie will hand it over.
        """
        mission_triggers.intro(
            m,
            comment="Magic mission-start picture",
            voice=self._voice,
            text=(
                "Dodge, Magic on station. Russian column rolling south down the "
                "Inguri valley road, Hawg is fragged against it. The load is "
                "behind it — fuel and ammunition, and that is yours. Pinpoint is "
                "in the valley watching the road and will call it. Texaco is "
                f"{_FREQ_TANKER} point zero, TACAN {_TANKER_TACAN}, west of the field."
            ),
        )

    def _add_support_checkins(self, m: Mission) -> None:
        """Texaco and Pinpoint check in on the clock, in the order they matter."""
        mission_triggers.checkin(
            m,
            at_seconds=_TANKER_CHECKIN_S,
            comment="Texaco on station",
            voice=self._voice,
            text=(
                "Dodge, Texaco is on station west of Batumi, "
                f"TACAN {_TANKER_TACAN}. Come to us before you go home, not after."
            ),
        )
        mission_triggers.checkin(
            m,
            at_seconds=_TACP_CHECKIN_S,
            comment="Pinpoint 1-1 check-in",
            voice=self._voice,
            text=(
                "Dodge, Pinpoint one-one. We are in the treeline above the valley "
                "road with eyes on it. The lead column has gone past us. When the "
                f"fuel comes down I will lase it for you on {_LASER_CODE}. My "
                f"net is {_FREQ_TACP} point zero."
            ),
        )

    def _add_tacp_readout(self, m: Mission, *, target: VehicleGroup) -> None:
        """Let Pinpoint pass the detachment's position in the units the Viper takes.

        DCS reads a four-digit military grid to every airframe from its own
        `NATO.lua`, and an F-16's DED cannot take one — so as it ships, the
        controller's talk-on is a kneeboard conversion before it is a steerpoint.
        The request on his menu answers in the asking cockpit's format, off a live
        vehicle, which matters here because the detachment is driving: a mark good
        at his check-in is stale by the time the escort phase is over.

        The volunteered readout is timed to land *after* the sighting call rather
        than at check-in (`_TACP_READOUT_S`) — early would be a controller
        reading out coordinates for something he has not announced he can see.
        """
        arm_jtac_coords(
            m,
            [
                CoordTarget(
                    target,
                    label="Pinpoint 1-1",
                    what="the fuel and ammunition detachment",
                    laser_code=_LASER_CODE,
                )
            ],
            menu_title="Pinpoint 1-1",
            push_at_s=_TACP_READOUT_S,
        )
        # The two facts about the controller a derived card cannot carry: the
        # laser code (pydcs writes it nowhere) and where the readout lives in the
        # radio menu.
        kneeboard.remark(
            m, f"Pinpoint 1-1 lases the fuel detachment on code {_LASER_CODE}."
        )
        # One line, under the card's column width, so it does not wrap: what a
        # remark is for is the fact the page cannot derive — where the readout
        # lives — and *why* it exists (DCS reads a grid to every airframe) is
        # briefing prose, which both briefings carry. A remark that spends a
        # second line explaining itself is prose on the wrong page.
        kneeboard.remark(
            m,
            "Target coordinates in your cockpit's format: F10 -> Other -> Pinpoint 1-1.",
        )

    def _add_echelon_sighting_trigger(
        self, m: Mission, *, pol: VehicleGroup, osa: VehicleGroup
    ) -> TriggerZoneCircular:
        """Pinpoint calls the detachment as it comes into his view, and sets a flag.

        The detachment has been driving since mission start (see
        `_spawn_red_second_echelon`), so what this trigger models is not its
        departure but the moment somebody sees it — which is the only way the
        player could know, given the group is concealed like everything else
        Russian here. The zone sits on the stretch of road the party is
        overlooking, so the call is true when it fires rather than scheduled.

        The flag is the interesting half: being *seen* is what eventually sends
        the gunships after the man who saw it (`_add_hind_trigger`). The player's
        second task and the threat to it come off one event.

        It also brings the SA-8 onto the road with the trucks — see
        `_spawn_red_second_echelon` for why that launcher is not on the map from
        the start. The player is told about the detachment and the detachment's
        air defence appears in the same instant, which is the only version of
        this where the surprise cannot have already wrecked somebody else's
        briefed plan.
        """
        seen = m.triggers.add_triggerzone(
            position=self._echelon_overwatch(),
            radius=6_000,
            hidden=True,
            name="Echelon in view",
        )
        rule = mission_triggers.message_to_coalition(
            m,
            comment="Pinpoint sights the fuel detachment",
            conditions=(condition.PartOfGroupInZone(pol.id, seen.id),),
            voice=self._voice,
            text=(
                "Dodge, Pinpoint one-one. I have your target — two fuel bowsers "
                "and an ammunition truck on the valley road, one armoured vehicle "
                "with them, making about thirty-five. I am on them with the "
                "laser. They will be in and out of my sight line for the next "
                "quarter of an hour as the road weaves, and then they are behind "
                "the spur for good — coordinates are on my net whenever you want "
                "them."
            ),
            seconds=20,
        )
        rule.add_action(action.SetFlag(_FLAG_ECHELON_SEEN))
        rule.add_action(action.ActivateGroup(osa.id))
        return seen

    def _add_sight_line_lost_trigger(self, m: Mission, *, pol: VehicleGroup) -> None:
        """Say when the party's laser runs out, because the terrain decides it.

        `Pinpoint` sees 12 km of the march and no more (`_TACP_WATCH_FRACTIONS`),
        so a player who spends the window on the MiGs arrives to a controller who
        can talk but not lase. Without a call for that, the pod suddenly being the
        only option reads as the laser code being wrong or the JTAC being broken.
        With one, it is the road bending — which is what it is.
        """
        masked = m.triggers.add_triggerzone(
            position=self._road_at_fraction(_TACP_SIGHT_LOST_FRACTION),
            radius=2_000,
            hidden=True,
            name="Echelon behind the spur",
        )
        mission_triggers.message_to_coalition(
            m,
            comment="Pinpoint loses the sight line",
            conditions=(condition.PartOfGroupInZone(pol.id, masked.id),),
            voice=self._voice,
            text=(
                "Dodge, Pinpoint one-one. They are behind the spur — I have lost "
                "the sight line and the spot is off. I can still give you "
                "coordinates but the last few kilometres to the junction are your "
                "pod, not my laser."
            ),
            seconds=20,
        )

    def _add_shorad_reveal_trigger(
        self, m: Mission, *, zone: TriggerZoneCircular
    ) -> None:
        """Magic's ESM call when the launcher travelling with the detachment lights up.

        This is the moment that makes the withheld SA-8 an intelligence gap
        rather than an ambush. The briefing already says a fuel detachment does
        not travel unescorted and that nobody found what is escorting it; the RWR
        will say the rest. What this adds is the name, from the one asset that
        could plausibly hear it, at the point the player is closing on the road.

        The zone is the road, not the launcher: a moving group cannot anchor a
        trigger zone, and the honest condition is "somebody blue is close enough
        for that radar to be worth switching on" rather than a range to a vehicle
        the player is not supposed to have a position for.
        """
        mission_triggers.message_to_coalition(
            m,
            comment="SA-8 with the detachment called on ESM",
            conditions=(
                condition.FlagIsTrue(_FLAG_ECHELON_SEEN),
                condition.PartOfCoalitionInZone("blue", zone.id),
            ),
            voice=self._voice,
            text=(
                "Dodge, Magic. New emitter with that detachment — Land Roll, "
                "SA-8, and it is the one we could not find this morning. Short "
                "reach and a low ceiling. Keep the release at six thousand and "
                "you are over the top of it; go down to look and you are not."
            ),
            seconds=20,
        )

    def _add_hind_trigger(self, m: Mission, *, hinds: FlyingGroup) -> None:
        """Send the gunships once the detachment has been seen, plus a few minutes.

        `TimeSinceFlag` rather than a second zone, because what causes this is not
        where the player is — it is that a laser has been on Russian vehicles and
        somebody has had time to work out roughly where it is coming from. Four
        minutes is the whole margin the player gets, and the call is deliberately
        the vague one an AWACS can actually make about two helicopters in a
        valley: movers, low, no better than that.
        """
        rule = mission_triggers.message_to_coalition(
            m,
            comment="Mi-24P pair sent after Pinpoint",
            conditions=(condition.TimeSinceFlag(_FLAG_ECHELON_SEEN, _HIND_DELAY_S),),
            voice=self._voice,
            text=(
                "Dodge, Magic. Two movers low in the Inguri valley, rotary, "
                "tracking south toward Pinpoint's position. That is all we have "
                "on them — they are in the clutter. If they get to him you lose "
                "the laser."
            ),
            seconds=20,
        )
        rule.add_action(action.ActivateGroup(hinds.id))

    def _add_tacp_loss_trigger(self, m: Mission, *, tacp: VehicleGroup) -> None:
        """Say plainly what losing the party costs, because it is not the mission.

        Without this the player watches a friendly icon disappear and has no way
        to know whether the frag is still live. It is: the pod self-designates,
        the bombs still work, and the pass is the pilot's own. Saying so is the
        difference between a cost and a bug.
        """
        mission_triggers.message_to_coalition(
            m,
            comment="Pinpoint 1-1 lost",
            conditions=(condition.GroupDead(tacp.id),),
            voice=self._voice,
            text=(
                "Dodge, Magic. Pinpoint is off the air — we have lost the party in "
                "the valley. No talk-on and no spot from here on. The detachment "
                "is still yours: self-designate with the pod and fly the pass "
                "yourself."
            ),
            seconds=20,
        )

    def _add_escalation_trigger(
        self,
        m: Mission,
        *,
        convoy: VehicleGroup,
        boris: FlyingGroup,
        sokol: FlyingGroup,
    ) -> None:
        """Scramble Gudauta's second pair once the first is gone or the column is.

        Two ways in, one flag, for the same reason `idlib_gauntlet` releases its
        strike that way: either of these can happen first, and gating on one of
        them alone leaves a fighter pair on the ramp for a whole sortie in the
        half of the runs where the other one is what happened.

        The call says out loud that this pair is not the player's — the frag is
        priced on four missiles (`_spawn_red_escalation`), so a player who reads
        "two more Flankers" as a new target list is being set up to fail by the
        arithmetic rather than by the enemy.
        """
        for comment, cond in (
            ("Alert pair destroyed", condition.GroupDead(boris.id)),
            ("Column coming apart", condition.GroupLifeLess(convoy.id, 40)),
        ):
            rule = triggers.TriggerOnce(comment=f"Escalation: {comment}")
            rule.add_condition(cond)
            rule.add_action(action.SetFlag(_FLAG_ESCALATION))
            m.triggerrules.triggers.append(rule)

        trig = scramble_on_trigger(
            m,
            sokol,
            condition.FlagIsTrue(_FLAG_ESCALATION),
            comment="Gudauta Su-27 pair scramble",
        )
        call = (
            "Dodge, Magic. Second pair starting engines at Gudauta, Su-27. Eagle "
            "is committing on them — they are Eagle's, not yours. Finish the "
            "detachment and go home; you are cleared to leave them flying."
        )
        trig.add_action(
            action.MessageToCoalition(action.Coalition.Blue, m.string(call), seconds=20)
        )
        self._voice.attach_to_coalition(m, trig, call, coalition="blue")

    def _add_end_triggers(
        self,
        m: Mission,
        scene: _Scene,
        *,
        red: _RedGround,
        hog: FlyingGroup,
        tacp: VehicleGroup,
    ) -> None:
        """Layered resolution: the column, the detachment, both, and the two ways out.

        Two objectives means the sortie can end four ways rather than two, and
        each of them has to be *said*, because a player who has broken the column
        and is orbiting with two bombs left needs to know that the mission is not
        over and a player who has bombed the detachment needs to know that it is.

        No condition here names an air group. `Boris`, `Sokol` and the gunships
        are threats to survive, and a win gated on killing them would be a win
        gated on more missiles than the jet carries — see
        `_spawn_red_escalation`.
        """
        mission_triggers.message_to_all(
            m,
            comment="Column broken on the road",
            conditions=(condition.GroupLifeLess(red.convoy.id, 30),),
            voice=self._voice,
            text=(
                "Magic: that column is finished as a fighting unit, nothing left "
                "on the road worth calling a march. The fuel behind it is still "
                "coming, Dodge — that one is yours."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Fuel detachment stopped",
            conditions=(condition.GroupLifeLess(red.pol.id, 40),),
            voice=self._voice,
            text=(
                "Magic: the bowsers are burning on the valley road. Whatever is "
                "left of that column is walking pace from here on — there is no "
                "fuel behind it."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Both echelons stopped",
            conditions=(
                condition.GroupLifeLess(red.convoy.id, 30),
                condition.GroupLifeLess(red.pol.id, 40),
            ),
            voice=self._voice,
            text=(
                "Magic: column wrecked, fuel destroyed, nothing is reaching "
                "Senaki today. Good work, Dodge. Texaco is on tap on the way "
                "home — RTB Batumi."
            ),
            seconds=25,
        )

        junction = m.triggers.add_triggerzone(
            position=scene.junction,
            radius=3_000,
            hidden=True,
            name="Senaki junction off-load",
        )
        mission_triggers.message_to_all(
            m,
            comment="Fuel detachment reached the junction",
            conditions=(condition.PartOfGroupInZone(red.pol.id, junction.id),),
            voice=self._voice,
            text=(
                "Magic: the fuel made the junction north of Senaki and they are "
                "off-loading under cover. We missed that window. Dodge, work what "
                "is left on the road and RTB Batumi."
            ),
        )
        mission_triggers.message_to_all(
            m,
            comment="Hawg lost with the column rolling",
            conditions=(
                condition.GroupDead(hog.id),
                condition.GroupAlive(red.convoy.id),
            ),
            voice=self._voice,
            text=(
                "Magic: we have lost Hawg and the column is still rolling south. "
                "The fuel behind it is still your frag, Dodge — nothing else here "
                "can reach it."
            ),
        )
        # Not an outcome, a hand-off: the party being alive at the end is what
        # separates full success from a sortie that cost more than it should.
        mission_triggers.message_to_all(
            m,
            comment="Package complete with Pinpoint intact",
            conditions=(
                condition.GroupLifeLess(red.pol.id, 40),
                condition.GroupAlive(tacp.id),
            ),
            voice=self._voice,
            text=(
                "Pinpoint one-one: good hits, we are still here and still in the "
                "treeline. Thanks for keeping them off us, Dodge."
            ),
        )

    def _add_briefing(self, m: Mission) -> None:
        """Wire the in-game description, side tasks, and sortie name."""
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(
            "Escort Hawg 1-2 onto the Russian column north of Senaki, then stop "
            "the fuel and ammunition detachment behind it before it reaches the "
            "Senaki junction — two GBU-12 and Pinpoint 1-1 on the laser are what "
            "you have for that, and Pinpoint has to survive to give it to you. "
            "Eagle owns the air-to-air fight: the Sukhumi alert pair is a threat "
            "to beat and the Gudauta pair behind it is not your target list. "
            "The detachment travels with air defence nobody located, so keep the "
            "release at or above 6000 m. Not cleared to pursue over "
            "Sukhumi-Babushara. RTB Batumi."
        )
        m.set_description_redtask_text(
            "Push the column through the Inguri valley to the Senaki junction, "
            "and get the fuel and ammunition detachment behind it through as "
            "well — the column is worth nothing at the junction without it. "
            "MiG-29S to intercept the USAF package; commit the Gudauta pair only "
            "once the column is coming apart. Find the ground party doing the "
            "lasing and send the gunships after it."
        )
        m.set_sortie_text(self.title)


def main() -> None:
    run_cli(CoastalCover)


if __name__ == "__main__":
    main()
