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
- **Briefing exists.** `set_description_text`, `*_bluetask_text`,
  `*_redtask_text` with bullseye, AO coords, ROE, callsigns, bingo fuel.
- **Frequencies + TACAN** for AWACS and tankers in the briefing.
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
print a success/failure message so the player knows the sortie resolved.

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
| Numeric balance    | bandits outnumbered → 3× player flight                         |
| Enemy missile gen  | gen-3 (R-27 / AIM-7) vs gen-4 (R-77 / AIM-120)                 |
| SAM threats        | none → MANPADS → SHORAD → SA-6 → SA-10                         |
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
- **Make the label bite.** Ace means night/IMC + no support *actually
  applied*. Recruit means real support + clear weather, not just lowered
  skill.
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
- One line, ≤ 15 s on screen.
- `MessageToCoalition` when side-specific (friendly AWACS → blue only).
- Silent triggers OK for bookkeeping (flag plumbing, internal state) — the
  rule is about triggers with player-visible *effects*.

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

## F10 map briefing drawings

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
section. Lower difficulty = more the player is *shown*; higher = more they
must *find*. It mirrors the intel a real flight would brief with, and it is
a difficulty dial in its own right (SA on the enemy picture).

| Label    | Enemy reveal on the F10 map                                                          |
|----------|--------------------------------------------------------------------------------------|
| recruit  | Exact icons at true positions. `StandardIcon.AirDefense` / `SearchRadar` on each SAM/EWR, threat rings at true envelope radius, convoy/target marked precisely. Label them plainly ("SA-6"). |
| trained  | Real threat rings and icons, but coarser — cluster nearby units into one ring, place the icon at the cluster centroid not the exact TEL. Label with type ("SA-6 site"). |
| veteran  | A vague **threat area** only — one large low-alpha polygon / oblong ("SAM threat — vicinity Senaki"), no per-unit icons, no true radius. Position offset a few km from truth. Air threat as a bearing arrow from the enemy field, not a fix. |
| ace      | Little or nothing. AO + friendly plan only. Enemy shown as an unlabelled search box at best ("threats expected"), or omitted entirely — the player builds the picture from RWR, AWACS calls, and the tally. |

Rules:

- **Never reveal more than the briefing text claims.** If prose says
  "estimated SA-6, location unconfirmed", the map gets a vague area, not a
  pinpoint icon — the two channels must agree.
- **Mark estimates as estimates.** On trained+ append "(est.)" to labels
  and offset the mark from the true unit so a precise ring can't be
  reverse-engineered.
- **Colour convention.** Enemy in red (`Rgba(255,0,0,·)`), friendly plan in
  blue/cyan, notes/neutral in white — fills low-alpha, outlines opaque.
- **Don't clutter.** A handful of purposeful marks beats a busy map; if it
  wouldn't appear on a real kneeboard, leave it off.
- **Honour overrides.** "Recruit but I want to find them myself" → pull the
  reveal down without touching other dials.

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
3. **Don't** "validate" by opening the file — no DCS install on this box.
   For sanity, re-load via `Mission().load_file(path)` if the user wants it.
4. List the knobs the user can tweak (theater, airframe, players, difficulty,
   length, time, package, threats) and show the exact CLI re-invocation,
   e.g. `--players 2 --difficulty veteran --airframe FA_18C_hornet --length-minutes 60`.
