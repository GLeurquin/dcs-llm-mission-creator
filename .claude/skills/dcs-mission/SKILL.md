---
name: dcs-mission
description: >
  Generate realistic DCS World missions with pydcs. Covers theater + weather + time
  selection, coalition setup, flight package design (CAP, strike, SEAD, AWACS, tanker),
  ground threats, waypoint tasking, briefings, and `.miz` packaging.
  Use when the user says "create a DCS mission", "generate a .miz", "build a pydcs
  mission", "DCS scenario", or invokes /dcs-mission. Auto-triggers when the user asks
  for a mission file under this project.
---

Turn vague mission requests ("Caucasus CAP at dawn", "SEAD against an SA-6 near
Bandar Abbas") into a runnable pydcs script that builds a `.miz`.

**This file is design intent only.** The pydcs API (terrain classes, flight
helpers, StartType, Skill enum, formation API, F10 drawings, gotchas) lives
in [PYDCS_REFERENCE.md](PYDCS_REFERENCE.md) — consult it before guessing API
shapes. Project conventions (package layout, the `VoiceSynth` / `PlanOverlay`
helpers, script structure, lint / type-check) live in
[CLAUDE.md](../../../CLAUDE.md).

## When to invoke

- User asks for a DCS mission, `.miz` file, training scenario, or pydcs script.
- User wants to add a new scenario to this repo.

Skip if the user is asking about DCS Lua, the in-game ME, or a non-pydcs tool.

## The eight scenario knobs

Confirm each. If the user didn't specify, **pick a sensible default and state
it in one line** — ask for tweaks afterwards.

1. **Theater** — terrain instance passed to `Mission(terrain=...)`. Default:
   `Caucasus`. Full class list in PYDCS_REFERENCE.md.
2. **Mission type** — mix / CAP / strike / SEAD / CAS / anti-ship / escort /
   training. Default: mix.
3. **Player faction** — name the side by faction (USA, USAF, Russia, Ukraine,
   Germany, …), never by `red` / `blue`. Default: USA. See *Faction naming*.
4. **Player airframe** — default `dcs.planes.F_16C_50` (flexible across CAP /
   CAS / SEAD / strike). See *Player airframe*.
5. **Coop player count** — 1–4 client slots. Default: 1. See *Coop slots*.
6. **Difficulty** — recruit / trained / veteran / ace. Default: trained. See
   *Difficulty*.
7. **Time + weather** — default mid-morning, clear, light wind. Difficulty,
   theater, and season can override.
8. **Mission length** — default 45–60 min total flight time. See *Pacing*.

## Faction naming

Briefings and in-game text name **factions** (USA, USAF, Russia, Ukrainian,
…), never `red` / `blue`. The words `blue` / `red` exist only in pydcs API
calls (`airport.set_blue()`, `Coalition.Blue`, `bluetask_text`) and code
comments — never in `readme()`, `set_description_text`, side-task bodies, or
trigger messages. Full mapping in CLAUDE.md.

## Briefing craft — intel, not implementation

The briefing is an intel product written by a squadron intel officer the
morning of the sortie. It is **not** a description of how the mission file
works. Everything the player reads or hears — `readme()`, `set_description_text`,
`*_bluetask_text`, trigger messages, voice calls — obeys this.

### Never publish the trigger logic

Timers, flag conditions, percentages and unit counts are implementation. State
the *intent*; let the trigger enforce the exact number silently.

| Don't write                                            | Write                                                             |
|--------------------------------------------------------|-------------------------------------------------------------------|
| "Pontiac releases when the SA-6 Flush radar dies or at T+25, whichever comes first" | "Pontiac is held in reserve west of the border and will run the column once the SAM threat is suppressed" |
| "70% of the column destroyed counts as a win"          | "Render the column combat-ineffective"                            |
| "Trigger: 4 vehicles remaining → success message"      | "Enough of the column stops rolling that the resupply fails"      |
| "MiG-29s late-activate 12 minutes into the sortie"     | "Expect a fighter reaction out of Sukhumi once you are committed"  |
| "The SA-6 crew reacts to HARM launches 85% of the time"| "That crew is disciplined — expect them to go dark under fire"     |
| "Entering the 8 km zone scrambles the alert pair"      | "Push close to the field and they will scramble the alert pair"    |

Rules:

- No exact figures for **thresholds** — percentages, unit counts, flags, zone
  radii, countdown timers — and none of the mission-file vocabulary
  ("trigger", "flag", "zone", "late-activated", "script", "condition",
  "spawns").
- Numbers a real briefing *would* carry stay: frequencies, TACAN channels,
  laser codes, bullseye, altitudes, headings, distances, fuel states, time on
  target, expected sortie length.
- Ordering is fine, schedule is not: "once the SAM threat is suppressed",
  "after you are established overhead", "on your call".
- Win/loss reads as an outcome the pilot could recognise from the cockpit
  ("the column is no longer combat-effective", "the bridge is down"), never as
  a scoring formula. Same for the failure case.
- In-flight text and voice follow the same rule: "Pontiac pushing, threat is
  suppressed" — not "release condition met".
- The difficulty-composition line (*Difficulty*, below) is the one deliberate
  exception: it lives in the README's own metadata block, not in the narrative
  briefing.

### Attribute the intel

Every claim about the enemy names a source and an age. It is what makes a
briefing read real, and it is the honest way to express confidence: the source
justifies how precise (or vague) the claim is — and the F10 drawing must match
that precision.

- Collectors: "an RC-135 Rivet Joint track last night fixed two SA-6-class
  emitters", "this morning's satellite pass", "a Reaper feed at 0430", "ELINT
  out of Incirlik", "yesterday's E-3 picture".
- Human / allied sources: "a partner-force report", "the ground unit in
  contact", "a source in the town", "a defecting crewman".
- **In-flight calls need a collector too, live.** "SA-6 radar emissions detected
  south of the AO" is a claim somebody had to be in a position to make — an ESM
  platform on station with line of sight, not the mission narrating its own
  triggers. Put that asset in the package, say in the briefing whose picture it
  is, and accept that the call goes missing when it is masked or shot down.
  `iads.arm_iads(listeners=…)` enforces exactly this for radar on/off calls; a
  hand-rolled trigger has to be judged by eye.
- Age and confidence carry the vagueness: "eighteen hours old",
  "unconfirmed", "last observed", "assessed", "we have no fix on it".
- Match source → precision → map. An emitter fix gives a ring labelled
  "(est.)"; imagery gives a position icon; a rumour gives an area. **If the
  prose has no source for something, don't draw it.**
- Difficulty is the collection budget: recruit gets a fresh multi-source
  picture, ace gets one stale line ("nothing since yesterday — build the
  picture yourself").

## Realism checklist

A "realistic" mission is more than spawning aircraft. Hit these before done:

- **Time + threats match.** Night CAS → IR weapons + NVG airframes. High
  noon strike → LGB self-lase windows.
- **Weather plausible for theater + season.** Persian Gulf July: 40 °C, haze,
  light NW. Caucasus January: −5 °C, low overcast, snow.
- **Airbases coalition-correct.** Don't spawn USA flights from a
  Russian-coalition field — call `set_blue()` / `set_red()` explicitly.
- **Package composition makes sense.** A strike needs escort + SEAD + tanker
  + AWACS, not a lone F-16 with bombs. Even small missions get AWACS or GCI.
- **Loadouts match the task.** No Mavericks on SA-10 (use HARM); no F-15Cs
  with bombs.
- **Threats layered, not stacked.** One SA-6 + a few Shilkas reads harder
  than five SA-10s on one hill.
- **The objective cannot be reached from any bearing.** Contested ground gets a
  front line across the approach and rear-area coverage behind it, or the player
  arcs wide and every threat you placed watches from behind. See *Front lines
  and territory control*.
- **No air defence in the canopy or the water.** Positions off the placement
  helpers, `overlay` + `terrain` into the site builders, `snap_units_clear`
  after any scattered formation.
- **Build whole SAM sites, not lone launchers.** A real site is search radar
  + track/fire-control radar + command post + launchers. Use the site
  builders — pydcs `VehicleTemplate` for SA-6/10/11/15 + Patriot/Hawk, project
  `air_defense.build_*` for SA-2/3/5/8/13/19, NASAMS, IRIS-T, Roland, Rapier,
  HQ-7 (CLAUDE.md) — not a bare TEL that can't track.
- **Radar + launchers must share one `VehicleGroup`.** In DCS a launcher
  only engages if its tracking/fire-control radar is in the **same** group —
  split the 1S91 (SA-6) or 30H6/64H6E (SA-10) into a separate group from the
  TELs and the launchers get no fire-control and **never shoot**. The site
  builders keep everything in one group; if you hand-build, use one
  `vehicle_group_platoon([radar, launcher, launcher, …])`. Exception:
  self-contained SHORAD (SA-8/13/15/19, each TELAR has its own tracker) is
  fine as one type. To make "kill the radar = suppressed" a win condition
  **without** splitting the group, gate on `condition.UnitDead(radar_unit.id)`
  (the radar is `group.units[0]`), not `GroupDead`.
- **Waypoint altitudes match what the waypoint marks.** Ground-target
  steerpoints and base (take-off / landing) waypoints sit on the terrain, not
  at ingress altitude and not at pydcs's default 0; en-route waypoints clear
  the terrain under them. See *Waypoints that mark the ground*.
- **Waypoint speeds are km/h and the airframe can hold them.** Every pydcs
  speed argument is km/h TAS; a knots-shaped number commands about half the
  intended speed and puts the AI package in permanent afterburner. Check the
  departure waypoint too — pydcs commands 108 kt there, below a loaded jet's
  stall speed. See *Waypoint speeds are a flight profile*.
- **JTAC for CAS.** A CAS or CAP-over-ground mission gets a ground or
  airborne FAC that lases targets and talks the player on
  (`tasking.fac_attack_group`). Brief its frequency + designation. Arm
  `jtac.arm_jtac_coords` with it: the stock controller reads a military grid to
  every airframe, so without it the coordinates a Viper or Hornet driver is
  given are a kneeboard conversion before they are a steerpoint.
- **Briefing exists.** `set_description_text`, `*_bluetask_text`,
  `*_redtask_text` with bullseye, AO coords, ROE, callsigns, bingo fuel.
- **Briefing reads like intel, not like the trigger list.** Sourced enemy
  claims, intent instead of timers/percentages — see *Briefing craft*.
- **Enemy groups hidden on the map.** Red never shows up as a unit icon on
  F10, the mission planner, or the datalink — the player works off the
  briefing and the drawn plan. See *What the player can see*.
- **Frequencies + nav aids** in the briefing — AWACS/tanker freqs + TACAN;
  for carrier ops, the boat's TACAN + ICLS and a recovery tanker overhead.
- **Kneeboard cards are automatic.** `MissionBuilder.build_miz` publishes a route
  card and a comms card (`core/kneeboard`), both derived from the mission — so
  "per kneeboard" in a briefing is now true, and nothing needs listing twice. Add
  `kneeboard.remark(m, …)` only for a fact that is in no field pydcs writes: a
  laser code, or where a radio request sits in the F10 menu. An airfield page is
  added automatically for a field the theatre ships no chart of (Caucasus charts
  all 21 of its fields, Syria charts three), so don't write one by hand either
  way. Cards say nothing about the enemy: that stays with the F10 plan and the
  cartridge, which carry the difficulty policy.
- **AI skill varied.** Don't set everything Excellent. Mix High / Average
  for ground; Excellent only for boss threats.
- **Tell a story.** Mission context, scene-setting intro, dynamic reactions
  to player actions via text + voice. Not a target list.
- **No targets in deep forests.**

## Mission pacing

Default total flight time `L` = **45–60 min** unless the user specifies.
Range 20 min ("quick rep") to 180 min ("deep strike") reachable by scaling
the same model.

### Phase budget

Subtract fixed overhead from `L`, split rest 1:2:1.

| Phase                | Budget                                  |
|----------------------|-----------------------------------------|
| `T_start` (startup)  | ~2 min hot ramp (default), ~7 min cold  |
| `T_rtb` (recovery)   | ~7 min approach + landing               |
| Useful `U`           | `L − T_start − T_rtb`                   |
| Transit out          | `0.25 × U`                              |
| On-station           | `0.50 × U`                              |
| Transit back         | `0.25 × U`                              |

Hot ramp (`StartType.Warm`) is default. Only escalate to `Cold` (~7 min) if
the user asks for a startup-procedure rep. For `L ≥ 120 min`, plan a
mid-mission tanker pass.

### Distance cap

Jets cruise ~14 km/min (450 kn GS); helicopters ~3 km/min (140 kn).

```
max_ao_distance_m = (0.25 × U) × cruise_speed_m_per_min
```

| `L`  | Transit out | Max AO (jet) |
|------|-------------|--------------|
| 30   | 5.25 min    | ~75 km       |
| 50   | 10.25 min   | ~145 km      |
| 60   | 12.75 min   | ~180 km      |
| 90   | 20.25 min   | ~285 km      |
| 120  | 27.75 min   | ~390 km      |

Cold start trims ~5 min from every row. Push past caps only with extra fuel
(tanker, externals, closer divert).

### Fuel

Internal endurance (hot, combat power): F-16C / F-18C ~35 min, F-15C /
M2000C ~50 min, F-14B ~70 min, A-10C ~90 min, helos ~90–120 min. If
`L > endurance − 10 min`, **spawn a tanker** even if the difficulty drops
support — and announce it in the briefing.

### Threat staging

Spread enemy contact across the on-station window. Multiple waves, spaced
~14 km of standoff per extra minute before merge. For deterministic timing,
fall back to `triggerrules` activation.

Reinforcement waves can **scramble cold off the ramp** (alert-5) instead of
appearing airborne: `tasking.scramble_on_trigger(m, flight, <condition>)`
keeps the flight shut down until the player trips a zone/flag, so the reaction
reads as *caused*, not scripted. Contrast `late_activation`, which pops a
flight straight into the air. If the scenario fits, announce the scramble by voice (below).

### Patrol legs

Size race-track legs so the flight makes **3–5 passes during on-station**:

```
leg_length_m ≈ (on_station_min × cruise_speed_m_per_min) / (2 × laps_target)
```

Jets, 25 min on-station, 4 laps → `(25 × 14_000) / 8 ≈ 44 km`. Longer
missions get longer legs or more stations, not 15-minute laps.

### Support on-station

`awacs_flight` / `refuel_flight` default tracks (120 km / 80 km) cover
60–90 min. For longer missions:

```
race_distance_m = max(60_000, L × 1_500)
```

### Mission end

No `mission.duration` in pydcs. Length is emergent (distances + fuel +
threats). Let it end naturally — bandits down + bingo fuel → RTB. Always
print a success/failure message so the player knows the sortie resolved —
phrased as an outcome ("the column is combat-ineffective, RTB"), never as a
score.

## Difficulty

Difficulty is a **judgement call**, not a recipe. The label
(recruit / trained / veteran / ace) is a feel; you reach it by combining
several dials. Two missions at the same label should feel different — varied
texture beats a fingerprint.

### Intent per label

| Label    | Intent                                                                   |
|----------|--------------------------------------------------------------------------|
| recruit  | Forgiving. Pilot leaves with kills + successful RTB.                     |
| trained  | Squadron default. Demands competence; punishes carelessness.             |
| veteran  | Hard. Package coordination, fuel, SA all required.                       |
| ace      | Brutal. One mistake ends the sortie. Currency-builds only.               |

### Dials

| Dial               | Range                                                          |
|--------------------|----------------------------------------------------------------|
| Enemy AI skill     | Average / Good / High / Excellent (mix freely)                 |
| AI ROE / reaction  | weapon-hold + no-reaction → weapons-free + evade + ECM (recruit→ace) |
| Numeric balance    | bandits outnumbered → 3× player flight — **capped by the player's magazine**, see below |
| Enemy missile gen  | gen-3 (R-27 / AIM-7) vs gen-4 (R-77 / AIM-120)                 |
| SAM threats        | none → MANPADS → SHORAD (SA-8/13/15/19, HQ-7) → SA-2/3/6 → SA-10/11 / Patriot |
| EWR / GCI          | none → EWR → EWR + GCI vectoring                               |
| Support package    | AWACS + tanker + escort → AWACS only → none → datalink-denied  |
| Weather / vis      | clear → scattered → broken → overcast/IMC → night              |
| Fuel margin        | comfortable → tight → fuel-critical                            |
| Ingress / egress   | clean → threats on route → divert field required               |
| Player loadout     | armament gen, missile / bomb types                              |
| AI wingmen         | count, skill, behaviour                                        |

### Guidelines

- **Match airframe + mission type.** Veteran F-15C CAP leans on bandit
  count + missile gen + GCI, not SAMs. Veteran SEAD inverts that.
- **Vary across runs.** Same label, different texture each time.
- **Honour overrides.** "Trained but no AWACS" → keep support none, pull
  other dials down so overall feel stays trained.
- **Sanity-check worst case.** 2-player flight vs 8 Excellent Flankers +
  SA-10 + IMC + no support is unplayable, not ace. Back off the least
  thematic dial.
- **Count the magazine before you set the bandit count, and scale both off
  `--players`.** Numeric balance is the one dial with an arithmetic ceiling:
  count the air-to-air stations the player's loadout actually leaves free
  (an F-16C with two wing tanks has six, and stations 4/6 take no missile at
  all), allow **two shots per kill** against `Skill.Excellent` crews, and that
  is the whole kill budget — ~3 per jet. Over it the mission is not ace, it is
  arithmetically unwinnable, which `abkhaz_sweep` was: six Excellent bandits
  and a kill-them-all frag against one jet's six missiles, at every slot count.
  Three legitimate fixes — derive the enemy count from the player count, add
  friendly AI (but note that "no escort, no tanker" may *be* the composition),
  or **task less than the airspace**: make the objective the element that gates
  the campaign effect and let the rest be a threat to survive rather than a
  required kill. The worked example, with the arithmetic and the trigger
  consequences, is the "Force balance: the magazine is the budget" section of
  CLAUDE.md.
- **Make the label bite.** Ace means night/IMC + no support *actually
  applied*. Recruit means real support + clear weather, not just lowered
  skill.
- **Set the AI's teeth, not just its aim.** `Skill` governs marksmanship;
  ROE and reaction-to-threat govern *behaviour*. Recruit enemies hold fire
  and don't defend; ace enemies are weapons-free, evade, and run ECM. Apply
  per group with `tasking.apply_ai_difficulty(group, difficulty)` (CLAUDE.md)
  — a distinct dial from raw skill.
- **Document composition in the briefing** so the user sees what you built:
  > Difficulty: veteran. Excellent Flankers, bandits 1.5× player flight,
  > AIM-120/R-77, AWACS only, broken cloud layer.

Player flight skill is **always** `Skill.Client`, never difficulty-derived.
AI wingmen on the player's flight match difficulty (Average on recruit,
Excellent on ace).

## Trigger announcements (text + voice)

**Every gameplay-affecting trigger announces itself with both an on-screen
message AND synthesized voice-over.** Silent triggers that change the world
(`ActivateGroup`, `AITaskPush`, `AITaskSet`, `Destroy`, gating `FlagSet`,
success/failure) read as bugs or cheating. Text alone is missable in the
cockpit; the WAV catches attention.

Rule: every such trigger gets a `MessageToAll` / `MessageToCoalition` AND a
matching `VoiceSynth.attach_to_*` call. Pass the **same string** to both so
on-screen and audio match word-for-word. `VoiceSynth` API + wiring in
CLAUDE.md (project-owned helper).

### When voice is required

- Mission start (`TriggerStart`) — AWACS/Magic check-in with the picture.
- Support check-ins (tanker on station, TARCAP up, strike holding for
  SAM-safe call) — `TimeAfter` triggers staged across early sortie.
- Objective gates — SAM down, depot down, MiGs scrambling, reserve pushing.
- Success and failure.

### What the message should say

Radio-style. Callsign first. No narration prose.

| Trigger                         | Example                                                   |
|---------------------------------|-----------------------------------------------------------|
| Reinforcement push              | "Russian armor reserve activated, pushing toward Senaki." |
| Bandit late-activation          | "MiG-29S airborne out of Sukhumi-Babushara, bearing 270." |
| SAM activation / radar lit      | "SA-6 radar emissions detected south of the AO."          |
| Friendly support inbound        | "Hawg 1-2 checking in, IP in 8 minutes."                  |
| Objective change                | "New tasking: convoy reached Senaki — withdraw to Batumi."|
| Success / failure               | "Strike package destroyed the convoy. RTB Batumi."        |

Guidelines:

- `m.string("…")` wraps the text so it lands in the translation table.
- Faction names, never `red` / `blue`.
- Radio phrasing, never trigger phrasing — no thresholds, timers or flags in
  the call ("Pontiac pushing, threat is suppressed", not "release condition
  met at 70%"). See *Briefing craft*.
- One line, ≤ 15 s on screen.
- `MessageToCoalition` when side-specific (friendly AWACS → blue only).
- Silent triggers OK for bookkeeping (flag plumbing, internal state) — the
  rule is about triggers with player-visible *effects*.

## Front lines and territory control

A target that can be attacked from any bearing has no mission design around it.
Ring the objective with belts and the player simply arcs 40 km wide, comes in
from the quarter nobody covered, and every threat the mission was built around
watches from behind. The fix is two rules, and they are the default for any
mission set over contested ground — not a special scenario:

1. **Put a front line across the approach.** Ground forces are dug in facing
   each other somewhere between the player's field and the objective, and the
   air defence strung along it is what prices each way in.
2. **Defend in depth behind it.** The far side of the line and the airspace
   around the objective are *held* ground. Rear-area batteries mean skipping the
   corridor buys a different envelope, not an empty sky.

### The line

Geometry comes from `core/frontline.py` (`plan_frontline`, see CLAUDE.md), which
takes what the line stands in front of (`defends`, the AO), the side the threat
comes from (`facing`, the player's field), how far short of the AO it sits
(`standoff_m`), its frontage (`span_m`), how far the wings sweep back toward the
player (`bow_m`), and the frontage left without a position on it (`seam_width_m`).
It returns the trace, the sector positions, the two shoulders and the seam.

What goes on it is force composition, so the mission decides — but the shape of
the decision is always the same three prices:

| Where | What | What it costs the player |
|-------|------|--------------------------|
| Shoulders (the tips) | An area SAM battery each — S-125, SA-6, Buk | Flanking at any altitude |
| Sectors (the frontage) | Dug-in armour + guns + MANPADS | Crossing anywhere low |
| Seam (the middle) | Nothing of its own | The crossing the briefing points at |

Sizing rules that matter more than the exact numbers:

- **The seam has to be honestly flyable.** Check the briefed corridor against
  each shoulder's *real* DCS reach, not the briefed ring, and leave ≥ 10 km. A
  player who complies and dies read a briefing that lied.
- **The seam should lead into the mission's actual problem**, not into safety —
  in `idlib_gauntlet` it is the SA-6's sector, which is what makes SEAD phase
  one rather than an option.
- **Make the frontage worth not flying round.** Span is the detour: 90 km of
  line is a ~100 km arc plus the turn back in, which is real minutes and real
  fuel on a jet that already needs the tanker. Bow the wings so the flanks are
  the long way in as well as the far way round.
- **The line is not the target.** Two HARMs will not open a shoulder battery,
  so say so in the ROE and leave the strongpoints to the ground war. A line the
  player is expected to fight through is a wall, and a wall is not a plan.
- **Check every friendly orbit against the new rings.** A TARCAP or tanker
  track that was fine before the line existed may now sit inside a shoulder's
  envelope; measure it and re-station. A CAP belongs on the friendly side of the
  line, covering the crossing.

### Depth (territory control)

Behind the line, and around the objective, ask of every quadrant: *whose
envelope is this?* If a quarter of the reachable airspace answers "nobody's",
that is where the player will go and none of the design applies to them.

- One rear-area battery per sector, level with the objective on each beam, is
  usually enough — plus the belts that cover the objective itself.
- Their envelopes should overlap the flanking arcs, not the briefed corridor:
  the same ≥ 10 km margin check as the shoulders.
- Depth coverage is also what makes the corridor read as a *choice*. The
  briefing can then say what a real one says: this is the cheapest way in, not
  the only one.
- Rear areas are where a **radar-only EWR** belongs as well; it cannot shoot,
  so it is not a ring, but it is what feeds GCI.

### Placement hygiene

Air defence in canopy or water neither sees nor shoots, and a front line of
Shilkas parked under trees reads as decoration:

- Positions come from the placement helpers, which exclude forest and water
  (`plan_frontline` snaps every position it returns when handed
  `overlay` + `terrain`).
- Pass `overlay` **and** `terrain` to the `air_defense.build_*_site` builders
  so the launcher ring is snapped too — clearing the centre is not enough.
- A scattered platoon spreads further than any placement buffer, so call
  `snap_units_clear` on it after pydcs has applied the formation. Anything built
  from a raw pydcs `VehicleTemplate` needs the same treatment.

### Drawing it

The **trace is drawn precisely at every difficulty**, unlike everything else
red: two armies have been sitting on it for weeks and the player's side holds
the other half of it, so withholding it models an ignorance nobody has — and
"cross at the seam" needs something on the map to point at. Use
`PlanOverlay.frontline(trace, label)`; label the seam as friendly plan.

The air defence *on* the line follows the ordinary reveal policy (`threat` /
`mobile_threat`), and per-position rings for every ZU-23 pit are clutter: plot
the line, and let one label carry what the frontage is worth ("guns and MANPADS
below 10,000").

## Ground formations

`vehicle_group*` defaults to `Formation.Line` — straight row, 20 m spacing.
That's the giveaway "spawned by a script" look. Pick by purpose:

| Formation   | Use for                                                           |
|-------------|-------------------------------------------------------------------|
| `Line`      | Convoy column on a road (`OnRoad` snaps spacing once moving).     |
| `Vee`       | Advancing armor, hasty defence.                                   |
| `Rectangle` | Motor-pool / staging area, parade ground.                         |
| `Star`      | Dense cluster around a centre (rare).                             |
| `Scattered` | **Default for static defences** — SAM sites, AAA on a hilltop, FOB garrisons, dispersed infantry. `max_radius=40–120 m`. |

Single-unit groups (`group_size=1`) ignore the kwarg — spread via separate
positions instead. Formation enum + spawn / post-spawn API in
PYDCS_REFERENCE.md.

## Convoys always follow roads

Any ground group that **moves between two places** — resupply column,
counterattack reserve, retreating armor — is road-bound. A column that
drives the straight line through fields, rivers and treelines reads as
scripted, bogs down, and hands the player a target that never behaves like
a convoy.

So: **every waypoint of a moving ground group gets
`move_formation=PointAction.OnRoad`, including the spawn waypoint.** The
spawn one is the easy miss — pydcs creates waypoint 0 with `OffRoad` and a
ground waypoint's action governs the leg *leaving* it, so `OnRoad` on the
destination alone changes nothing about how the column drives there. Pass
`move_formation=PointAction.OnRoad` to `vehicle_group*` at spawn **and** to
each `add_waypoint`.

Two things follow from it:
- Snap origin and destination onto real road with
  `place_convoy_route` / `convoy_spawn` (see `core/placement.py`) — OnRoad
  pathing off a seed point in the middle of a field just drives to whatever
  road the engine finds first.
- Speed is road speed: 30–45 km/h for a mixed column. Faster and the column
  strings out and beats the player to the objective.

Static ground — SAM sites, FOB garrisons, depots, AAA on a hilltop — has no
waypoints at all and is unaffected.

## Waypoints that mark the ground sit on the ground

A waypoint altitude is not decoration: in the cockpit it **is** the steerpoint
elevation the CCRP/CCIP solution, the HUD and the DED read. So a steerpoint
that marks something on the surface must carry that surface's elevation, not
the altitude the flight happens to cross it at.

Two kinds of waypoint are on the ground by definition:

- **Ground-target steerpoints** — the convoy, the depot, the FOB, the SAM
  site, the JTAC's target box. Altitude = terrain elevation there.
- **Base waypoints** — the take-off point and the landing point. pydcs writes
  both as `alt = 0` (it has no height map), which buries them under any field
  above sea level — Vaziani sits at 464 m, and the jet then aligns to a
  steerpoint half a kilometre underground.

The run-in altitude belongs on the **preceding IP / ingress waypoint**, which
is a point in the air and stays there: PUSH and INGRESS legs at 6000–7000 m,
IP at the pop altitude, then the target steerpoint on the deck. Same for the
CAP station or the AWACS orbit — those are air points, leave them at altitude.

Mechanically (helpers in CLAUDE.md, API in PYDCS_REFERENCE.md §4.3):

```python
waypoints.add_ground_waypoint(player, scene.route_mid, overlay=ov,
                              speed=750, name="CONVOY AO")
waypoints.snap_base_waypoints(m, ov)   # once, last step before m.save(...)
```

`snap_base_waypoints` walks every flight in the mission, so a flight added
later cannot miss it — call it in `build_miz` right after `_add_briefing`.

**Client routes only** for ground-target steerpoints. An AI flight actually
flies the altitudes on its route, so a deck-level turning point flies it into
the terrain; give AI a sane crossing altitude and let its attack task handle
the target. Base waypoints are safe for everyone — take-off and landing are
ground events already.

While you are checking altitudes, check the *en-route* ones against the
terrain too: a "valley run" waypoint at 800 m over ground that is 2600 m high
is inside the mountain. `overlay.elevation_at(point)` is the check.

## Waypoint speeds are a flight profile

Every pydcs speed argument is **km/h true airspeed** (the unit rule and the
sanity bound are in CLAUDE.md; the API detail in PYDCS_REFERENCE.md §4.2).
Design-side, the number is a profile statement and each leg wants its own:

- **Transit / ingress** — a jet cruises around Mach 0.7–0.8: 800 km/h at
  6000–8000 m. This is also the climb speed, so it is the one to get right.
- **CAP or sweep station** — same order, a little slower for endurance
  (780–800 km/h). An orbit's `OrbitAction` speed must match the speed on its
  own waypoints or the flight fights itself.
- **AWACS / tanker orbit** — 740–750 km/h, ≈250–290 KIAS at FL215–FL295.
- **Interceptor scramble** — faster than the package it is chasing:
  900–920 km/h.
- **Low-level run** — 630–700 km/h; the airframe is IAS-limited down there,
  not Mach-limited.
- **Attack leg** — a touch below the ingress speed, not above it.
- **A-10C and other slow movers** — 500–540 km/h. Its never-exceed is
  720 km/h, so it does not scale with the fast jets.

The **departure** waypoint is a third case and the easiest to miss, because
pydcs writes it for you: `add_runway_waypoint` commands 108 kt at 300 m AGL and
has no speed argument. That is below the stall speed of anything with a combat
load, so the flight leaves the runway at max alpha in full afterburner. The
project overwrites it with the flight's own climb-out speed for every mission
(`waypoints.set_departure_speeds`, called from `build_miz`) — but if you are
reading a route and it looks wrong off the deck, that is why.

Two failure modes to check for, both silent:

- **Too slow.** A fighter ordered 150 KIAS at FL260 is far below best-climb
  speed and behind the drag curve; the AI holds altitude on the throttle and
  flies the sortie in afterburner, arriving late and out of fuel. A heavy
  (E-3A, KC-135) simply cannot make the lift and never reaches station.
- **Too fast.** Above roughly 0.85 of `max_speed` the flight is in burner by
  definition and will not make its planned time on station.

**Speed is not the only reason an AI flight burns.** The DCS AI takes off and
climbs at a high deck angle in afterburner as a matter of routine, and no
waypoint reaches that. What a mission does control is **weight**: check gross
against the sortie radius the way you check speed against the airframe. A jet at
80 %+ of max gross rotates steeply and climbs in burner because the weight
demands it. `idlib_gauntlet`'s Pontiac flew a 91 km radius carrying full
internal fuel *and* two 330 gal wing tanks, with a tanker on station — a package
tanker is the reason you do not launch heavy, not permission to. Where burner
genuinely buys nothing — a bombed-up strike pair — set
`tasking.apply_threat_reaction(..., restrict_afterburner=True)`, but never on a
CAP or interceptor, which needs it in a merge, and only on a flight whose route
already avoids the live rings.

Sanity-check every leg against `FlyingType.max_speed` (km/h). On a supersonic
fighter a cruise or orbit speed is **0.30–0.40** of it; above ~0.40 the jet is
holding the profile in afterburner. Subsonic types are exempt — an E-3A cruises
at 0.86 of its `max_speed` and has no afterburner to reach for.

**Check the ratio per airframe, not the number.** The fast jets are not
interchangeable: 800 km/h is 0.38 on an F-16C (2120) and 0.41 on an F/A-18C
(1950), so a package that gives the whole flight "800" puts only the Hornet in
burner. Weigh the loadout too — a jet with two bags and four LGBs belongs at the
bottom of the band, a clean CAP at the top.

## What the player can see (F10 map, planner, datalink)

The player's picture of the enemy comes from **the briefing and the drawn
plan** — never from the map's own unit icons. So: hide every enemy group, then
deliberately draw back exactly as much as the briefing claims.

### Hide the enemy everywhere

Every red group — aircraft, vehicles, ships, statics, EWR, SAM sites,
reserves — gets all three map channels turned off. Do it in one
`_conceal_red` step near the end of `build_miz` (after the spawns, before
`_draw_plan`), using the project helper so nothing is missed as the mission
grows:

```python
from dcs_mission_creator.core.visibility import conceal_country

def _conceal_red(self, russia: Country) -> None:
    """Keep every Russian group off the F10 map, the planner and the datalink."""
    conceal_country(russia)          # or conceal_country(russia, syria)
```

- `conceal_country(*countries)` sweeps every group the country owns;
  `conceal(*groups)` does the same for a hand-picked list (and skips `None`).
  Both set `hidden` / `hidden_on_planner` / `hidden_on_mfd` — see CLAUDE.md
  for the helper, PYDCS_REFERENCE.md §5 for the raw attributes.
- Hiding is cosmetic. The group still spawns, radiates, moves and shoots.
- It covers **late-activated and scrambled groups too** — an unhidden reserve
  sits on the planner map spoiling the reaction before it ever launches.
  That's the reason to sweep by country rather than by hand.
- Friendly groups stay visible — a flight really does see its own package.
  On veteran / ace you may also pull friendly AI off the planner so the
  player flies the briefed plan rather than reading it off the map.

### Draw the plan

Draw the **plan** on the F10 map so the player reads the sortie at a glance,
not just from prose. Annotations complement the briefing text and voice
check-ins — same intent, different channel. Put them on the `Blue` layer
(player-facing side); never annotate the enemy's own layer. Drawing API
(`m.drawings`, layers, `Rgba`, `StandardIcon`, `add_*`) in PYDCS_REFERENCE.md;
the project-owned `PlanOverlay` wrapper is in CLAUDE.md.

**Always draw (every difficulty)** — the friendly plan, which is never a
secret:

- AO / target area — a circle or labelled box around the objective.
- Ingress / egress arrows or a route polyline from the departure field.
- Key friendly geometry — CAP race-track, tanker track, AWACS orbit,
  IP / push points — as lines + a text label each.
- Bullseye / reference-point label if the briefing calls bearings off it.

**Enemy positions scale with difficulty** — this is the point of the whole
section. With the units themselves hidden, every enemy mark on the map is one
you chose to draw. Lower difficulty = more the player is *shown*; higher =
more they must *find*. It mirrors the intel a real flight would brief with,
and it is a difficulty dial in its own right (SA on the enemy picture).

| Label    | Enemy reveal drawn on the F10 map                                                    |
|----------|--------------------------------------------------------------------------------------|
| recruit  | Exact icons at true positions. `StandardIcon.AirDefense` / `SearchRadar` on each SAM/EWR, threat rings at true envelope radius, convoy/target marked precisely. Label them plainly ("SA-6"). |
| trained  | Real threat rings and icons, but coarser — cluster nearby units into one ring, place the icon at the cluster centroid not the exact TEL. Label with type ("SA-6 site"). |
| veteran  | Rings still, but they are assessments: drawn dashed and unfilled, ~4 km off truth, radius inflated, labelled "(approx.)". Airborne threats as a **threat area**, not a fix. |
| ace      | The same, further out — ~6 km off, wider again. The player is told the airspace is denied and not where the launchers are: the ring cannot be threaded, so the battery still has to be found on the RWR, the HTS and the tally. |

**What scales is precision, not presence.** The higher labels used to draw
nothing at all, and that turned out to leak *more*, not less. A mission still
has to put steerpoints somewhere, and with no estimate to draw from it takes the
site's true position: `daryal_run` concealed every icon, drew a vague area 4 km
off, and wrote a `TARGET` steerpoint 170 m from the S-300, which the player
reads off the DED on the ramp. So draw the ring, make it visibly an assessment,
and build every other channel from that same estimate. The rule to hold onto:

> **Every planned point that refers to an enemy site derives from the estimate,
> never from the site.** Map ring, cartridge point, target steerpoint, IP,
> kneeboard row. Then nothing the player can read carries a better position than
> the briefing admits to, and a steerpoint that lands near the truth is luck
> rather than a leak.

`PlanOverlay.estimate(center, radius=…)` returns that claim without drawing, and
memoises it on the true position, so a mission that needs it while laying its
route builds the overlay **first** and passes it to `_draw_plan` at the end.
`core/routing.py` is the deliberate exception: its rings keep the flight alive
rather than telling the player anything, so they use the true position — bending
a package around a ring known to be kilometres off would route it away from
empty sky.

Rules:

- **Never reveal more than the briefing text claims.** If prose says
  "estimated SA-6, location unconfirmed", the map gets a vague area, not a
  pinpoint icon — the two channels must agree. Each enemy mark should trace
  back to a sourced line in the briefing (*Attribute the intel*): the emitter
  fix draws a ring, the imagery draws an icon, the rumour draws an area.
- **Mark estimates as estimates.** `PlanOverlay` does this for you — "(est.)"
  on trained, "(approx.)" and a dashed unfilled ring above it — and offsets the
  mark from the true unit so a precise ring can't be reverse-engineered. Say the
  same thing in the prose: a briefing that claims a fix while the map draws a
  dashed guess reads as the map being broken.
- **Only emplaced systems get a ring.** A threat ring says the envelope *is
  there*. Air defence that moves — a convoy's organic SHORAD, a launcher on a
  road march, a mobile reserve — has driven out of any ring by the time the
  player arrives, and the ring is then worse than nothing: everywhere it no
  longer covers reads as clear. Mark those with an icon and a label
  (`PlanOverlay.mobile_threat`, no circle) and let the prose carry the reach
  ("the column's SHORAD holds anything inside 8 km at risk"). Rule of thumb: a
  group with waypoints does not get a ring.
- **Colour convention.** Enemy in red (`Rgba(255,0,0,·)`), friendly plan in
  blue/cyan, notes/neutral in white — fills low-alpha, outlines opaque.
- **Don't clutter.** A handful of purposeful marks beats a busy map; if it
  wouldn't appear on a real kneeboard, leave it off.
- **Honour overrides.** "Recruit but I want to find them myself" → pull the
  reveal down without touching other dials.

### Not every SAM has to be on the map

The table above is a ceiling, not a checklist. Intelligence is *incomplete* in
real life, and a threat the player was never shown is one of the few honest ways
a mission can still surprise someone who read the briefing carefully. So
deliberately leaving a site off the map is allowed and encouraged — under
conditions, because the difference between an intelligence gap and a cheat is
entirely in the setup:

- **Say where the picture is thin.** The briefing must not imply completeness.
  Name the hole with a source, the way an intel officer would: "a Gadfly-class
  emitter came up on the net overnight and we never got a fix — no ring on your
  map because we would be drawing a guess". The player then knows they are
  flying with a gap, which is a different feeling from being ambushed.
- **It must not threaten the briefed plan.** Measure the withheld site against
  the corridor, the IP, the orbits and the target run — with margin. A surprise
  that punishes the player for doing exactly what they were told is a bug
  wearing a fog-of-war costume. Aim it at the *deviation*: the wide flank, the
  greedy second pass, the loiter over the target.
- **Give them the moment.** An AWACS ESM call on a trigger ("new emitter, no
  fix, north of the corridor") or the RWR chirp is what makes it read as the
  morning's intel having a hole rather than as the mission spawning something.
  Without any moment, a withheld site is just an unexplained death.
- **Never plan the friendly package around it.** No `ThreatRing`, no cartridge
  point, no bend in an AI route — the planner has no position either. That is
  precisely the case `tasking.apply_threat_reaction` exists for.
- **Withhold one thing, not the mission.** The objective's own defences and the
  belts the sortie is built around stay briefed; what goes unmarked is the extra
  battery, the relocated launcher, the reserve. One or two per mission.

### Put the same plan in the cockpit

A ring the player can only see on the F10 map is a ring they lose the moment
they are heads-down in the jet. An F-16C reads pre-planned threats off its data
cartridge and draws them on the HSD and the HAD, so a briefed belt belongs
there too — `core/dtc.py`, wired from the `_load_cartridge` step (see
CLAUDE.md). The rest of what the map shows goes with it: the cartridge also
carries the flight's route and the plan's marks as **steerpoints**, and the
plan's lines as the HSD's **GEO lines**, read straight off the `PlanOverlay` so
neither channel can drift from the other.

The rules are the ones above, unchanged, because it is the *same* claim in a
second channel:

- Load what the map drew — the coarsened, offset estimate — not the site's true
  position. `PlanOverlay.threat` hands its estimate back for exactly this. It
  matters more here than on the map: a pre-planned threat **is** a steerpoint,
  so a point on the truth is coordinates the player reads out of the DED, and it
  would undo the reveal the F10 map had just applied. Every difficulty produces
  an estimate, so a hard mission gets a cartridge like any other — its rings are
  simply wider and further from the launchers.
- Only systems the briefing names, and only ones with an envelope: an EWR, a
  convoy or an armor reserve is not a ring. The jet holds fifteen points, and
  they are better spent on belts than on every MANPADS in the AO.
- **A pre-planned point is a static claim, so mobile air defence never gets
  one.** A cartridge ring cannot be updated in flight: load the convoy's 2S6
  and the player flies a picture that was wrong from the first road mile, and
  is most wrong exactly where the column has driven to. Sites only; a moving
  SHORAD threat belongs in the prose and in the AWACS calls, and on the map as
  a mark with no envelope.

For the other two tabs, one design rule matters and the rest is budget:

- **A front line is what the GEO lines are for.** The jet draws four polylines
  from twenty-five shared vertices, and a front line is the one piece of enemy
  geometry with a shape that nothing else in the cockpit carries — the same
  argument that makes it the one red drawing painted precisely at every
  difficulty. It goes first and can never be the line that loses; an orbit takes
  a line only after it, and only because most missions have one to spare. Every
  race-track becomes a steerpoint at its midpoint regardless, since a range and
  a bearing to the station is what a pilot actually asks for.
- **Do not draw the flight's own corridor twice.** The steerpoints already
  trace it. A route line earns a GEO line only when somebody else flies it.

### Recon stills — imagery the briefing already claims

A briefing that cites "this morning's Reaper feed" and then shows nothing is
describing intel the player cannot look at. `core/recon` renders that feed as a
wide-area radar frame and puts it on the briefing screen and in the README. Add
one when the prose already claims imagery of the target; do not add one to
justify new intel.

- **It is a radar product, not a photograph.** The terrain data is 50 m posts, so
  a frame tight enough to show trucks would be ~95 % invented. The frame is
  25 km across and a vehicle is a fifth of a pixel — which is why the movers are
  drawn as brackets *over* the image rather than as shapes *in* it.
- **The caption states the resolution.** "The base is a 50 m radar mosaic and the
  brackets are moving-target returns, not imagery — count them for the size of
  the column, not for what is in it." That sentence is what stops the picture
  over-claiming, and it is the same discipline as marking a ring "(est.)".
- **The sensor and the frame time are part of the claim**, so they carry the same
  source-and-age rule as any other intel line, and the timestamp must be
  consistent with the mission clock rather than a nice-sounding number.
- **Difficulty governs it like everything else.** Positions come from
  `PlanOverlay.detections` and nothing else; at `veteran`/`ace` that returns
  nothing and the mission publishes no still. A frame of empty ground is not a
  substitute — silence is.
- **Say where it is.** The in-game briefing is plain text, so it can only point
  at the picture ("Hammer's radar cut is on the briefing screen"); the README
  embeds it. Without that line the player may never look.

## Player airframe

Default `dcs.planes.F_16C_50` — most flexible (CAP, CAS, SEAD, strike). Safe
when mission type is also unspecified.

| Module          | pydcs attr                  | Good for                   | Coop slots |
|-----------------|-----------------------------|----------------------------|------------|
| F-16C Block 50  | `planes.F_16C_50`           | CAP, CAS, SEAD, strike     | up to 4    |
| F/A-18C Hornet  | `planes.FA_18C_hornet`      | CAP, CAS, anti-ship, strike| up to 4    |
| AH-64D Apache   | `helicopters.AH_64D_BLK_II` | CAS, anti-armor            | up to 4    |

Role-compatibility:

- CAS with F-15C / M2000C → swap to F-16C and state the swap.
- CAP with A-10C → swap to F-16C or F-15C.
- Faction must match airframe nation. A Russian-coalition flight in an
  F-16C is wrong — use Su-27 / MiG-29S / Su-25T. Inconsistent pair? Follow
  the faction (it's the louder signal); state the conflict.
- Helos under `dcs.helicopters.*`, not `planes.*`. Same
  `flight_group_from_airport(aircraft_type=...)` call.
- Unknown airframe? Grep `dcs/planes.py` or `dcs/helicopters.py`. Don't
  invent attributes like `planes.F_35A`.

## Coop player slots

DCS coop = multiple humans sharing a flight. Mark slots flyable by setting
each unit's `skill = Skill.Client` (PYDCS_REFERENCE.md covers the API).

Rules:

- `group_size` on the player flight = requested player count (1–4).
- **The opposition scales with it, and so does what is tasked.** A fixed
  enemy count means the mission is balanced for at most one value of
  `--players`; derive both from `self.players` and check the result against
  the flight's magazine (see *Difficulty → Guidelines*).
- **All** units in the flight get `Skill.Client`. Empty Client slots appear
  as unoccupied seats in MP UI; DCS leaves them parked if nobody joins.
- Default `start_type=StartType.Warm` (hot ramp). Cold only when the user
  asks for a startup-procedure rep. There is no `StartType.Hot`.
- One player flight per group. Two distinct human flights (F-16s + A-10s) =
  two groups, each with its own clients.
- Callsigns: `Dodge` / `Springfield` / `Uzi` for USA-NATO; `Boris` / `Ivan`
  for Russian. Don't reuse AI callsigns (`Magic`, `Hawg`, `Eagle`, `Texaco`).
- Two-seat modules (F-14B, AH-64D): pydcs still spawns one unit per
  `group_size`. The second seat is occupied via a separate MP slot in-game,
  not via pydcs.

## After generating

1. Run `uv run dcs-mission-creator generate <slug>` (or
   `uv run python -m dcs_mission_creator.missions.<slug>`).
2. Confirm both `.miz` and `README.md` were written; report the output folder.
   If the mission publishes a recon still, confirm the PNG landed beside them and
   that the README's relative link resolves — and *look at the image*, which is
   the only way to tell whether it reads as a sensor product.
3. **Don't** "validate" by opening the file — no DCS install on this box.
   For sanity, re-load via `Mission().load_file(path)` if the user wants it.
4. List the knobs the user can tweak (theater, airframe, players, difficulty,
   length, time, package, threats) and show the exact CLI re-invocation,
   e.g. `--players 2 --difficulty veteran --airframe FA_18C_hornet --length-minutes 60`.
