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
[CLAUDE.md](../../../CLAUDE.md). Where this file names a rule and CLAUDE.md
names the call that enforces it, both are needed and neither restates the other.

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
5. **Coop player count** — 2–6 client slots (`MissionBuilder.MIN_PLAYERS` to
   `MAX_PLAYERS`). Default: 2. See *Coop slots*.
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
trigger messages.

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

A "realistic" mission is more than spawning aircraft. Hit these before done.
Where a bullet names a mechanism, CLAUDE.md has the call and its arguments —
what is here is the *decision*.

- **Time + threats match.** Night CAS → IR weapons + NVG airframes. High
  noon strike → LGB self-lase windows.
- **Weather plausible for theater + season.** Persian Gulf July: 40 °C, haze,
  light NW. Caucasus January: −5 °C, low overcast, snow.
- **Airbases coalition-correct.** Don't spawn USA flights from a
  Russian-coalition field — call `set_blue()` / `set_red()` explicitly.
- **Package composition makes sense.** A strike needs escort + SEAD + tanker
  + AWACS, not a lone F-16 with bombs. Even small missions get AWACS or GCI.
- **Loadouts match the task, and the flight splits them.** No Mavericks on
  SA-10 (use HARM); no F-15Cs with bombs. And no jet carrying both halves of a
  two-part frag badly — see *The flight splits its loadout*.
- **Threats layered, not stacked.** One SA-6 + a few Shilkas reads harder
  than five SA-10s on one hill.
- **The objective cannot be reached from any bearing.** See *Front lines and
  territory control*.
- **No air defence in the canopy or the water.** Positions off the placement
  helpers, `overlay` + `terrain` into the site builders, `snap_units_clear`
  after any scattered formation.
- **Build whole SAM sites, not lone launchers.** A real site is search radar
  + track/fire-control radar + command post + launchers. Use the site builders
  (CLAUDE.md, *Air-defense builder*) — not a bare TEL that can't track.
- **Radar + launchers must share one `VehicleGroup`.** In DCS a launcher only
  engages if its tracking/fire-control radar is in the **same** group — split
  the 1S91 (SA-6) or 30H6/64H6E (SA-10) into a separate group from the TELs and
  the launchers get no fire-control and **never shoot**. The site builders keep
  everything in one group; if you hand-build, use one
  `vehicle_group_platoon([radar, launcher, launcher, …])`. Exception:
  self-contained SHORAD (SA-8/13/15/19, each TELAR has its own tracker) is fine
  as one type. To make "kill the radar = suppressed" a win condition **without**
  splitting the group, gate on `condition.UnitDead(radar_unit.id)`, not
  `GroupDead`.
- **Waypoint altitudes match what the waypoint marks**, and **waypoint speeds
  are a profile the airframe can hold.** See *Waypoints that mark the ground*
  and *Waypoint speeds*.
- **JTAC for CAS.** A CAS or CAP-over-ground mission gets a ground or airborne
  FAC that lases targets and talks the player on
  (`tasking.fac_attack_group`). Brief its frequency + designation, and arm
  `jtac.arm_jtac_coords` with it — the stock controller reads a military grid to
  every airframe, so without it a Viper or Hornet driver is given a kneeboard
  conversion before they are given a steerpoint.
- **Laser codes match, and the briefing states the bombs'.** See *Laser codes*.
- **Briefing exists** — `set_description_text`, `*_bluetask_text`,
  `*_redtask_text` with bullseye, AO coords, ROE, callsigns, bingo fuel — and
  **reads like intel, not like the trigger list** (*Briefing craft*).
- **Enemy groups hidden on the map**, and the plan drawn back at the difficulty
  the briefing claims. See *What the player can see*.
- **Frequencies + nav aids** in the briefing — AWACS/tanker freqs + TACAN;
  for carrier ops, the boat's TACAN + ICLS and a recovery tanker overhead. The
  kneeboard cards are automatic and derived, so "per kneeboard" is true and
  nothing needs listing twice; add `kneeboard.remark(m, …)` only for a fact in
  no field pydcs writes (a laser code, where a radio request sits in F10).
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
flight straight into the air. If the scenario fits, announce the scramble by
voice (below).

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
  `--players`.** Numeric balance is the one dial with an arithmetic ceiling —
  the flight can only carry so many missiles, and past that the mission is not
  ace, it is arithmetically unwinnable. Sum it with
  `MissionBuilder.air_to_air_shots(_FITS)`, never by multiplying a constant by
  the slot count. Three legitimate fixes: derive the enemy count from the
  magazine, add friendly AI (but "no escort, no tanker" may *be* the
  composition), or **task less than the airspace** — make the objective the
  element that gates the campaign effect and let the rest be a threat to survive
  rather than a required kill. The arithmetic, the levers and the trigger
  consequences are CLAUDE.md's *Force balance: the magazine is the budget*.
- **Make the label bite.** Ace means night/IMC + no support *actually
  applied*. Recruit means real support + clear weather, not just lowered
  skill.
- **Set the AI's teeth, not just its aim.** `Skill` governs marksmanship;
  ROE and reaction-to-threat govern *behaviour*. Recruit enemies hold fire
  and don't defend; ace enemies are weapons-free, evade, and run ECM. Apply
  per group with `tasking.apply_ai_difficulty(group, difficulty)` — a distinct
  dial from raw skill.
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
matching `VoiceSynth.attach_to_*` call, with the **same string** passed to both
(`core/triggers.py` is the wrapper that makes drift impossible — CLAUDE.md).

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

Geometry comes from `core/frontline.py` (`plan_frontline`, CLAUDE.md). What goes
on it is force composition, so the mission decides — but the shape of the
decision is always the same three prices:

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
counterattack reserve, retreating armor — is road-bound. A column that drives
the straight line through fields, rivers and treelines reads as scripted, bogs
down, and hands the player a target that never behaves like a convoy.

So: **every waypoint of a moving ground group gets
`move_formation=PointAction.OnRoad`, including the spawn waypoint.** The spawn
one is the easy miss — pydcs creates waypoint 0 with `OffRoad` and a ground
waypoint's action governs the leg *leaving* it, so `OnRoad` on the destination
alone changes nothing about how the column drives there. Pass it to
`vehicle_group*` at spawn **and** to each `add_waypoint`.

Two things follow from it:

- Snap origin and destination onto real road with `place_convoy_route` /
  `convoy_spawn` (`core/placement.py`) — OnRoad pathing off a seed point in the
  middle of a field just drives to whatever road the engine finds first.
- Speed is road speed: 30–45 km/h for a mixed column. Faster and the column
  strings out and beats the player to the objective.

Static ground — SAM sites, FOB garrisons, depots, AAA on a hilltop — has no
waypoints at all and is unaffected.

## Waypoints that mark the ground sit on the ground

A waypoint altitude is not decoration: in the cockpit it **is** the steerpoint
elevation the CCRP/CCIP solution, the HUD and the DED read. So a steerpoint that
marks something on the surface must carry that surface's elevation, not the
altitude the flight happens to cross it at. Two kinds are on the ground by
definition: **ground-target steerpoints** (the convoy, the depot, the FOB, the
SAM site, the JTAC's target box) and **base waypoints** (take-off and landing).

The design half is where the *other* altitudes go. The run-in altitude belongs
on the **preceding IP / ingress waypoint**, which is a point in the air and stays
there: PUSH and INGRESS legs at 6000–7000 m, IP at the pop altitude, then the
target steerpoint on the deck. Same for the CAP station or the AWACS orbit —
those are air points, leave them at altitude.

**Client routes only** for ground-target steerpoints. An AI flight actually flies
the altitudes on its route, so a deck-level turning point flies it into the
terrain; give AI a sane crossing altitude and let its attack task handle the
target. Base waypoints are safe for everyone, and are snapped by the base class,
so they need no call at all.

While you are checking altitudes, check the *en-route* ones against the terrain
too: a "valley run" waypoint at 800 m over ground that is 2600 m high is inside
the mountain. `overlay.elevation_at(point)` is the check;
`waypoints.clear_terrain` is the one that checks the legs as well (CLAUDE.md).

## Altitude is a threat parameter, and the numbers are in the game

A mission that says "fly low" is making a claim about what the enemy can do, and
DCS will settle it either way. Three facts decide whether the claim survives
contact, and all three are checkable before anything is built.

**Every SAM's floor, ceiling and minimum range are in the install.** The missile
tables under `<DCS>/CoreMods/tech/TechWeaponPack/Database/Weapons/*.lua` carry
`H_min`, `H_max`, `D_min` and `D_max` per round, and they are the honest source
for a briefing line. The S-200's `H_min = 300.0`, `D_min = 17e3`,
`D_max = 240e3` is what `ansariyah_works` is built on: it reaches most of the way
from Syria to Cyprus and it cannot bring a missile below three hundred metres, so
that mission's hard deck is a number out of the game rather than a number that
sounded right. **Grep the table before promising the player anything about an
envelope.**

**DCS has no earth curvature, so there is no radar horizon over water.** A
wave-top run across 250 km of open sea is *not* hidden from a coastal radar the
way it would be in life — line of sight is terrain only, and there is no terrain
out there. So a mission may promise a **floor** and may promise **terrain
masking** (measurable against the elevation raster), and may not promise
concealment over water. The useful move is to spend the detection rather than
deny it: in `ansariyah_works` the coastal EWR *does* call the crossing, and that
call is what rolls the target convoy and scrambles the alert pair, so the whole
second half of the mission is caused by the player being exactly where he was
briefed to be.

**A transition waypoint carries the altitude it starts at, not the one it ends
at.** DCS ramps linearly between waypoints, so a 6,500 m point followed by a 60 m
point 200 km later puts the jet above the floor for nine tenths of the leg — and
the route card then contradicts the ROE printed two inches under it. A descent or
a climb needs a point at each end: `LETDOWN` at cruise and `DECK` at the deck,
and on the way home `CLIMB` at the **deck**, with the climb on the leg after it.
The same arithmetic decides whether the descent is flyable at all — a 6.8 % idle
descent from 6,500 m needs about 95 km of run, which is what fixes where `DECK`
goes. Where the geometry leaves no room, say so in the briefing and price it
rather than drawing a profile nobody can fly.

## Waypoint speeds are a flight profile

Every pydcs speed argument is **km/h true airspeed**; the unit rule, the
0.30–0.40 sanity band and the four places a wrong number hides are CLAUDE.md's
*Every speed is km/h true airspeed*. Design-side, the number is a profile
statement and each leg wants its own:

- **Transit / ingress** — a jet cruises around Mach 0.7–0.8: 800 km/h at
  6000–8000 m. This is also the climb speed, so it is the one to get right.
- **CAP or sweep station** — same order, a little slower for endurance
  (780–800 km/h). An orbit's `OrbitAction` speed must match the speed on its own
  waypoints or the flight fights itself.
- **AWACS / tanker orbit** — 740–750 km/h, ≈250–290 KIAS at FL215–FL295.
- **Interceptor scramble** — faster than the package it is chasing: 900–920 km/h.
- **Low-level run** — 630–700 km/h; the airframe is IAS-limited down there, not
  Mach-limited.
- **Attack leg** — a touch below the ingress speed, not above it.
- **A-10C and other slow movers** — 500–540 km/h. Its never-exceed is 720 km/h,
  so it does not scale with the fast jets.

Two failure modes to check for, both silent. **Too slow**: a fighter ordered
150 KIAS at FL260 is behind the drag curve, so the AI holds altitude on the
throttle and flies the sortie in afterburner, arriving late and out of fuel; a
heavy simply never reaches station. **Too fast**: above roughly 0.85 of
`max_speed` the flight is in burner by definition and will not make its planned
time on station.

**Speed is not the only reason an AI flight burns**, and the other one is a
design decision rather than a number: **weight**. The DCS AI takes off and climbs
at a high deck angle in afterburner as a matter of routine, and no waypoint
reaches that — but a jet at 80 %+ of max gross rotates steeply and climbs in
burner because the weight demands it. Check gross against the sortie radius the
way you check speed against the airframe: `idlib_gauntlet`'s Pontiac flew a 91 km
radius carrying full internal fuel *and* two 330 gal wing tanks, with a tanker on
station — a package tanker is the reason you do not launch heavy, not permission
to.

## Laser codes: one number, and the briefing says it

A mission that puts a laser-guided weapon on the jet makes two claims — the
controller's spot is on code N, and the bombs will track it. **Both are 1688**
and neither is negotiable in the mission file (why, and which four airframes are
the exception, is CLAUDE.md *Laser codes*). Use `laser.DEFAULT_CODE` and call
`laser.set_code(flight, code)` on **every** flight carrying a laser-guided
weapon, the AI strike pair included.

The design half is what the briefing has to say. **Say the number out loud, on
the bombs' side as well as the spot's.** "`Pinpoint 1-1` lases on 1688" tells the
player half of what he needs; the other half — that his own GBU-12s and pod are
on the same code, so there is nothing to arrange — belongs in the same sentence,
in `readme()`, in the in-game briefing and in the `kneeboard.remark`. A flight
carrying nothing on a laser is worth one clause too, where the mission's
controller is lasing for somebody else: `idlib_gauntlet`'s `Uzi` drops
self-guided SFW and `Hammer`'s spot is `Pontiac`'s.

**And say the spot is already up.** Where a *player* takes an AI controller's
laser, `laser.arm_autolase` holds it on the target from mission start rather than
waiting on a check-in the low route was built to deny — but a player who has been
taught that a JTAC needs a radio conversation will spend his one pass having one
unless the briefing says otherwise. Say that the net is there for the talk-on and
the coordinates, not as a precondition for the laser.

## What the player can see (F10 map, planner, datalink)

The player's picture of the enemy comes from **the briefing and the drawn
plan** — never from the map's own unit icons. So: hide every enemy group, then
deliberately draw back exactly as much as the briefing claims.

### Hide the enemy everywhere

Every enemy group — aircraft, vehicles, ships, statics, EWR, SAM sites,
reserves — has all three map channels turned off, and **the base class does it
for the whole enemy coalition**, so there is nothing to remember and no
late-activated reserve to forget. What matters design-side:

- Hiding is cosmetic. The group still spawns, radiates, moves and shoots.
- It covers late-activated and scrambled groups too — an unhidden reserve sits
  on the planner map spoiling its own reaction before it ever launches.
- Friendly groups stay visible — a flight really does see its own package.
  On veteran / ace you may also pull friendly AI off the planner so the
  player flies the briefed plan rather than reading it off the map.

### Draw the plan

Draw the **plan** on the F10 map so the player reads the sortie at a glance, not
just from prose. Annotations complement the briefing text and voice check-ins —
same intent, different channel. The `PlanOverlay` wrapper and its API are
CLAUDE.md's; what is here is what to draw.

**Always draw (every difficulty)** — the friendly plan, which is never a secret:

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
nothing at all, and that turned out to leak *more*, not less: a mission still has
to put steerpoints somewhere, and with no estimate to draw from it takes the
site's true position. So draw the ring, make it visibly an assessment, and build
every other channel from that same estimate. The rule to hold onto:

> **Every planned point that refers to an enemy site derives from the estimate,
> never from the site.** Map ring, cartridge point, target steerpoint, IP,
> kneeboard row. Then nothing the player can read carries a better position than
> the briefing admits to, and a steerpoint that lands near the truth is luck
> rather than a leak.

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
  mark from the true unit. Say the same thing in the prose: a briefing that
  claims a fix while the map draws a dashed guess reads as the map being broken.
- **Only emplaced systems get a ring.** A threat ring says the envelope *is
  there*. Air defence that moves — a convoy's organic SHORAD, a launcher on a
  road march, a mobile reserve — has driven out of any ring by the time the
  player arrives, and the ring is then worse than nothing: everywhere it no
  longer covers reads as clear. Mark those with an icon and a label
  (`PlanOverlay.mobile_threat`, no circle) and let the prose carry the reach.
  Rule of thumb: a group with waypoints does not get a ring.
- **Colour convention.** Enemy in red, friendly plan in blue/cyan, notes in
  white — fills low-alpha, outlines opaque.
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

A ring the player can only see on the F10 map is a ring they lose the moment they
are heads-down in the jet. An F-16C reads pre-planned threats off its data
cartridge and draws them on the HSD and the HAD, and the cartridge also carries
the flight's route and the plan's marks as **steerpoints** and the plan's lines
as **GEO lines**, read straight off the `PlanOverlay` (the mechanics and the tab
budgets are CLAUDE.md's *Data-cartridge helper*). The rules are the ones above,
unchanged, because it is the *same* claim in a second channel:

- Load what the map drew — the coarsened, offset estimate — not the site's true
  position. It matters more here than on the map: a pre-planned threat **is** a
  steerpoint, so a point on the truth is coordinates the player reads out of the
  DED, and it would undo the reveal the F10 map had just applied.
- Only systems the briefing names, and only ones with an envelope: an EWR, a
  convoy or an armor reserve is not a ring. The jet holds fifteen points, and
  they are better spent on belts than on every MANPADS in the AO.
- **A pre-planned point is a static claim, so mobile air defence never gets
  one.** A cartridge ring cannot be updated in flight: load the convoy's 2S6 and
  the player flies a picture that was wrong from the first road mile, and is
  most wrong exactly where the column has driven to. A moving SHORAD threat
  belongs in the prose and in the AWACS calls, and on the map as a mark with no
  envelope.
- **A front line is what the GEO lines are for**, and it goes first: it is the
  one piece of enemy geometry with a shape nothing else in the cockpit carries —
  the same argument that makes it the one red drawing painted precisely at every
  difficulty. **Do not draw the flight's own corridor twice**; the steerpoints
  already trace it, so a route line earns a GEO line only when somebody else
  flies it.

### Recon stills — imagery the briefing already claims

A briefing that cites "this morning's Reaper feed" and then shows nothing is
describing intel the player cannot look at. `core/recon` renders that feed as a
wide-area radar frame and puts it on the briefing screen and in the README. Add
one when the prose already claims imagery of the target; do not add one to
justify new intel.

- **It is a radar product, not a photograph.** The terrain data is 50 m posts, so
  a frame tight enough to show trucks would be ~95 % invented. The movers are
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
- **The overlay has to be able to carry the subject.** It knows roads, buildings
  and water; it does not know aprons, so a frame centred on one renders farmland
  with a bracket in the middle of it. Measure before promising a still.
- **Say where it is.** The in-game briefing is plain text, so it can only point
  at the picture ("Hammer's radar cut is on the briefing screen"); the README
  embeds it. Without that line the player may never look.

## Player airframe

Default `dcs.planes.F_16C_50` — most flexible (CAP, CAS, SEAD, strike). Safe
when mission type is also unspecified.

| Module          | pydcs attr                  | Good for                   | Max per group |
|-----------------|-----------------------------|----------------------------|---------------|
| F-16C Block 50  | `planes.F_16C_50`           | CAP, CAS, SEAD, strike     | 4             |
| F/A-18C Hornet  | `planes.FA_18C_hornet`      | CAP, CAS, anti-ship, strike| 4             |
| AH-64D Apache   | `helicopters.AH_64D_BLK_II` | CAS, anti-armor            | 4             |

Four is pydcs's `group_size_max`, not the slot ceiling: more than four slots is
more than one group, so always build the player flight with
`mission_kit.player_flight`, which splits the sections for you.

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

- Player count is **minimum two** (`MissionBuilder.MIN_PLAYERS`), and above four
  it is more than one group — always `mission_kit.player_flight`, never a raw
  `group_size`.
- **The opposition scales with it, and so does what is tasked.** A fixed enemy
  count means the mission is balanced for at most one value of `--players`;
  derive both from `self.players` and check the result against the flight's
  magazine (see *Difficulty → Guidelines*).
- **The slots carry different loadouts.** Declare the flight's fit table as a
  module constant and pass it as `loadouts=`; see below.
- **All** units in the flight get `Skill.Client`. Empty Client slots appear as
  unoccupied seats in MP UI; DCS leaves them parked if nobody joins.
- Default `start_type=StartType.Warm` (hot ramp). Cold only when the user asks
  for a startup-procedure rep. There is no `StartType.Hot`.
- One player flight per group. Two distinct human flights (F-16s + A-10s) = two
  groups, each with its own clients.
- Callsigns: `Dodge` / `Springfield` / `Uzi` for USA-NATO; `Boris` / `Ivan` for
  Russian. Don't reuse AI callsigns (`Magic`, `Hawg`, `Eagle`, `Texaco`).
- Two-seat modules (F-14B, AH-64D): pydcs still spawns one unit per
  `group_size`. The second seat is occupied via a separate MP slot in-game, not
  via pydcs.

## Buildings as objectives, and the spacing that makes them one

A factory, a depot, a hardened shed is `m.static_group(...)` rather than
vehicles, and two things follow that a vehicle objective never raises.

**Spacing is the design.** Aimpoints closer together than one weapon's effect
are not separate objectives, they are one objective with extra steps.
`ansariyah_works` puts its three buildings 400–430 m apart precisely so two
2,000 lb bombs cannot take all three, which turns the second bomb into a briefed
decision — this month's production, or next year's — instead of a lucky pattern.

**Surround them with compound that is in no trigger** (tanks, a crane,
containers), so finding the right roof through the pod is a task rather than a
formality. The mechanics — which condition resolves a static, and why the
compound must be concealed — are in CLAUDE.md.

## The flight splits its loadout

**Two coop slots is the floor, and the two jets do not carry the same thing.**
The mechanics are in CLAUDE.md (`core/loadout.py`, `loadouts=` on
`player_flight`, the three views, the cycling rule, and the station arithmetic
that makes the split necessary rather than decorative); what belongs here is how
to *choose* it.

**Ask what the mission already contains, and give the second jet the half the
first one had to drop.** Four patterns cover everything in this project:

| pattern | when | example |
|---|---|---|
| **shooter + killer** | the frag is a radar site | HARM + HTS on one, CBU-105 on the other — a battery that goes dark is still a battery |
| **two weapons, one target set** | the target is not homogeneous | submunitions for a scattered platoon, laser bombs for the armour in it |
| **strike + escort** | the mission's other half is air | one bomber, one jet with six air-to-air missiles and no ordnance for the ground |
| **one magazine, split** | both jets are on the same job | a pure sweep: every shot a radar shot on one, two given up for AIM-9X on the other |

Rules that decide which one:

- **Measure the claim that the CAP will cover it.** "Escort" is only the right
  second fit if the friendly CAP genuinely cannot be there. Fly the routes and
  look: `eastern_shield`'s Eagle needs 21 minutes to reach its station against a
  player over the target at 9, so the flight is its own cover and the second fit
  is air-to-air. If a TARCAP is on station before the push, spend the second jet
  on the ground instead.
- **Never re-price a scarcity the mission was designed around.** If the sortie's
  tension is "two bombs, three aimpoints", the wingman carries **no bomb at all**
  — otherwise a second pilot showing up deletes the decision the mission is
  about. Then say in the briefing what a pair still has to choose and what four
  slots change.
- **Both fits should be ED payloads, station for station.** Grep
  `<DCS>/CoreMods/aircraft/<module>/UnitPayloads/*.lua` for the weapon and read
  the stations off the payload that carries it. "Legal in pydcs" and "a fit
  somebody flies" are different tests, and `core/loadout_check` is the mechanical
  half of it.
- **Name the role after the weapon**, not the doctrine word: `HARM/HTS`,
  `GBU-12*4`, `AIM-120C*6`. It is a dozen characters on a kneeboard card, and it
  has to tell a pilot what his wingman can do.
- **Count the whole flight's magazine** before setting the bandit count. A split
  flight is *not* six shots a jet — the jet with the bombs has four.
- **More slots repeat the pair.** The table cycles, so four slots are two
  elements each carrying the same split. Declare a third fit only when the
  mission has a genuine third job at that size.

The briefing must say **which slot** carries the frag-critical stores, because a
player picking a jet on the slot-selection screen is choosing a role without
knowing it otherwise.

## After generating

1. Run `uv run dcs-mission-creator generate <slug>` (or
   `uv run python -m dcs_mission_creator.missions.<slug>`).
2. Confirm both `.miz` and `README.md` were written; report the output folder.
   If the mission publishes a recon still, confirm the PNG landed beside them and
   that the README's relative link resolves — and *look at the image*, which is
   the only way to tell whether it reads as a sensor product.
3. **Don't** "validate" by opening the file — no DCS install on this box.
   For sanity, re-load via `Mission().load_file(path)` if the user wants it.
4. List the knobs the user can tweak. Only the slot count is a flag —
   `uv run dcs-mission-creator generate <slug> --players 4` (2–6). Theater,
   airframe, difficulty, length, time, package and threats are edited in the
   mission module itself, so say which constant or class attribute to change.
