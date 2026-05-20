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

This skill turns vague mission requests ("a Caucasus CAP at dawn", "SEAD package
against an SA-6 site near Bandar Abbas") into a runnable pydcs script that produces
a `.miz` the user can open in the DCS Mission Editor.

## When to invoke

- User asks for a DCS mission, `.miz` file, training scenario, or pydcs script.
- User to add a new scenario to this repo.

Skip if the user is asking about DCS Lua, the in-game ME, or a non-pydcs tool.

## Before writing code

Confirm the eight scenario knobs. If the user didn't specify, **pick sensible
defaults and state them in one line** — and ask the user for tweaks if they want:

1. **Theater** — Caucasus, Persian Gulf, Syria, Marianas. Other maps are not available to the players.
   (method names are lowercase: `m.terrain.caucasus()`, `m.terrain.persiangulf()`, ...) (default: Caucasus).
2. **Mission type** — mix / CAP / strike / SEAD / CAS / anti-ship / escort / training. (default: mix)
3. **Player coalition** — blue (default) or red.
4. **Player airframe** — default: `dcs.planes.F_16C_50`. See
   [Player airframe knob](#player-airframe-knob) for the supported map and
   role-compatibility rules.
5. **Coop player count** — 1–4 (default: 1). Number of client slots in the player
   flight. See [Coop player slots](#coop-player-slots).
6. **Difficulty** — `recruit` / `trained` / `veteran` / `ace` (default: `trained`).
   Drives enemy AI skill, threat density, support package, and weather.
   See [Difficulty knob](#difficulty-knob).
7. **Time + weather** — defaults to mid-morning, clear, light wind unless the user
   asks for night, IMC, or a specific season. Difficulty can override this.
8. **Mission length** — defaults to **45–60 minutes** of total flight time
   (transit + on-station + RTB). See [Mission pacing](#mission-pacing) for how
   this constrains distances, fuel, and threat staging.

## Project conventions

- Mission scripts go in `src/dcs_mission_creator/missions/<scenario_slug>.py`.
- Each script exposes `def build(output: Path) -> None` that constructs and saves the
  mission. This keeps scripts importable and testable.
- Generated `.miz` files land in `out/` at the repo root (gitignored). Never commit
  `.miz` files.
- Run with `uv run python -m dcs_mission_creator.missions.<scenario_slug>
  --output out/<scenario_slug>.miz`.
- If the `missions` package or `out/` dir doesn't exist yet, create them and add
  `out/` to [.gitignore](.gitignore).

## Realism checklist

A "realistic" mission is more than spawning aircraft. Hit these before declaring done:

- **Time of day matches the threat picture.** Night CAS implies IR-guided weapons
  and NVG-equipped airframes; high noon strike implies LGB self-lase windows.
- **Weather is plausible for the theater + season.** Persian Gulf in July: 40°C,
  haze, light NW wind. Caucasus in January: -5°C, low overcast, snow on the deck.
  Set `season_temperature`, cloud base/thickness, and wind at all three altitudes.
- **Airbases are friendly to the coalition.** Don't spawn blue flights from a
  red-coalition airfield — call `airport.set_blue()` / `set_red()` explicitly.
- **Package composition makes sense.** A strike needs escort + SEAD + tanker +
  AWACS, not a lone F-16 with bombs. Even small missions get an AWACS or GCI.
- **Loadouts match the task.** Don't send Mavericks against an SA-10 — use HARMs.
  Don't send a four-ship of F-15Cs with bombs.
- **Threats are layered, not stacked.** One SA-6 with a search radar + a few
  shilkas is harder than five SA-10s on one hill. Players should be able to
  reason about the threat picture.
- **Briefing text exists.** Use `set_description_text`,
  `set_description_bluetask_text`, `set_description_redtask_text`. Include
  bullseye, AO coordinates, ROE, package callsigns, and bingo fuel.
- **Frequencies and TACAN.** AWACS and tankers get a frequency + TACAN channel
  in the briefing so the player can actually use them.
- **AI skill is varied.** Don't set everything to Excellent — mix High / Average
  for ground units, Excellent only for the boss threat.
- **Create a scenario** Give a mission context, a scenario and a purpose. Use in-game text to set the scene, and react to player actions with text and actions.

## Mission pacing

Default total flight time = **45–60 minutes** unless the user specifies
otherwise. Anything between roughly 20 min ("quick rep") and 180 min ("deep
strike") should be reachable by scaling the same pacing model — the rules
below are length-parametric, not tied to the default.

### Phase budget (proportional model)

Subtract fixed overhead from the total `L` minutes, then split the rest 1:2:1
(transit out : on-station : transit back).

| Phase                     | Budget                                       |
|---------------------------|----------------------------------------------|
| Startup + taxi (`T_start`)| ~2 min hot ramp (default), ~7 min cold start |
| RTB + recovery (`T_rtb`)  | ~7 min approach + landing buffer             |
| Useful budget             | `U = L − T_start − T_rtb`                    |
| Transit out               | `0.25 × U`                                   |
| On-station / target work  | `0.50 × U`                                   |
| Transit back              | `0.25 × U`                                   |

The default hot-ramp start (`StartType.Warm`) keeps `T_start` low so the
on-station window stays usable for short missions. Only escalate to
`StartType.Cold` (~7 min) if the user is explicitly running a startup-procedure
rep, and re-check that the on-station window doesn't collapse. For very long
missions (`L ≥ 120 min`) plan at least one mid-mission tanker pass into the
on-station phase.

### Distance cap (derived from transit budget)

Jet transit averages ~14 km/min (450 kn ground speed). Helicopters ~3 km/min
(140 kn). The AO / patrol box / target should sit within:

```
max_ao_distance_m = transit_out_minutes × cruise_speed_m_per_min
                  = (0.25 × U) × cruise_speed_m_per_min
```

Worked examples (jets, default hot ramp, `T_start=2`, `T_rtb=7`):

| `L`     | `U`     | Transit out | Max AO distance |
|---------|---------|-------------|-----------------|
| 30 min  | 21 min  | 5.25 min    | ~75 km          |
| 50 min  | 41 min  | 10.25 min   | ~145 km         |
| 60 min  | 51 min  | 12.75 min   | ~180 km         |
| 90 min  | 81 min  | 20.25 min   | ~285 km         |
| 120 min | 111 min | 27.75 min   | ~390 km         |

Cold start (`T_start=7`) trims ~5 minutes from every row — re-derive if the
user opts in.

Push past these caps only if you're also adjusting fuel (tanker, externals,
or a closer divert field).

### Fuel

Compare round-trip + on-station time against the airframe's combat endurance.
Rough internal-fuel figures (hot, combat power):

| Airframe         | Internal endurance |
|------------------|--------------------|
| F-16C, F/A-18C   | ~35 min            |
| F-15C, Mirage 2000C | ~50 min         |
| F-14B            | ~70 min            |
| A-10C            | ~90 min            |
| Most helicopters | ~90–120 min        |

If `L > internal_endurance − 10 min margin`, **spawn a tanker** (override the
difficulty-driven support drop) and call it out in the briefing. Don't rely
on the player to know they're under-fuelled.

### Threat staging

Spread enemy contact across the on-station window so the flight doesn't burn
through everything in the first five minutes. Aim for more than one wave depending
on length.

Space waves evenly across on-station time and use distance to phase them
naturally: wave `i` starts at `position` offset roughly proportional to the
delay you want before contact. With AI cruise around 14 km/min, an additional
14 km of standoff buys ~1 min more before merge. For deterministic timing,
fall back to `mission.triggerrules` activation.

### Patrol legs

Size race-track legs so the flight makes **3–5 passes during the on-station
window**, regardless of mission length:

```
leg_length_m ≈ (on_station_minutes × cruise_speed_m_per_min) / (2 × laps_target)
```

For jets at 14 km/min over a 25-min on-station, targeting 4 laps:
`(25 × 14_000) / (2 × 4) ≈ 44 km`. Longer missions get longer legs (or more
patrol stations), not laps that drag on for 15 minutes each.

### Support on-station times

`awacs_flight` and `refuel_flight` use `race_distance` (in meters) to size
their racetrack. The default `120_000 m` AWACS / `80_000 m` tanker tracks
cover roughly 60–90 min on station each — fine for default-length missions.
For longer missions, **explicitly scale** `race_distance` so the support
asset lives the full sortie:

```
race_distance_m = max(60_000, length_minutes × 1_500)   # ~1.5 km/min station-keeping
```

### Mission-end behaviour

There is **no `mission.duration` field in pydcs.** Length is an emergent
property of distances, fuel, threat staging, and (optionally) triggers.

Let it end naturally — when bandits are killed and the flight is bingo,
   players will RTB. This is what most missions should do.
   It should print a message to let the player know the mission ends (either success or failure depending on objectives)

## Player airframe knob

Default to `dcs.planes.F_16C_50` whenever the user doesn't name an airframe.
It's the most flexible module — handles CAP, CAS, SEAD, and strike — so it's
the safest fit when the mission type is also unspecified.

Other modules available to the players:

| Module          | pydcs attr                  | Good for                  | Coop slots |
|-----------------|-----------------------------|---------------------------|-----------:|
| F-16C Block 50  | `planes.F_16C_50`           | CAP, CAS, SEAD, strike    | up to 4    |
| F/A-18C Hornet  | `planes.FA_18C_hornet`      | CAP, CAS, anti-ship, strike| up to 4   |
| AH-64D Apache   | `helicopters.AH_64D_BLK_II` | CAS, anti-armor           | up to 4 (2-seat) |

Role-compatibility rules — enforce these before writing the script:

- If the user asks for **CAS** with an F-15C or Mirage 2000C, swap to F-16C and
  state the swap in one line ("F-15C is air-to-air only; using F-16C for CAS").
- If the user asks for **CAP** with an A-10C, same — swap to F-16C or F-15C.
- Coalition must match the airframe's nation: red coalition with F-16C is wrong;
  use Su-27 / MiG-29S / Su-25T instead. If the user gives an inconsistent pair,
  state the conflict and follow the *coalition* (it's the louder signal).
- Helicopter airframes use `dcs.helicopters.*`, not `dcs.planes.*`. The
  `flight_group_from_airport` call works the same way; pass it under
  `aircraft_type`.
- If the user names an airframe that isn't in this table, look it up in
  [dcs/planes.py](https://github.com/pydcs/dcs/blob/master/dcs/planes.py) or
  [dcs/helicopters.py](https://github.com/pydcs/dcs/blob/master/dcs/helicopters.py)
  before guessing — don't invent attribute names like `planes.F_35A`.

## Coop player slots

DCS "coop" = multiple human players sharing a flight. In pydcs, a slot becomes
human-flyable by setting that unit's `skill` to `Skill.Client` (preferred over
`Player`, which is reserved for the single-player mission editor's "host" slot).

Rules:

- `group_size` on the player flight equals the requested player count (1–4).
- Iterate `group.units` and set **every** unit to `Skill.Client` for a pure coop
  flight. Empty Client slots show up as unoccupied seats in the multiplayer UI
  and DCS will leave them parked if nobody joins.
- Default `start_type=StartType.Warm` for the player flight. In pydcs terms,
  `Warm` = "ramp hot" (engines running, ready to taxi) — what the DCS community
  calls a *hot start*. **There is no `StartType.Hot`** in pydcs; do not write
  it. Use `StartType.Cold` only when the user explicitly asks for a cold-and-dark
  start (training reps focused on startup procedures), and `StartType.Runway`
  for runway-ready starts. Airborne starts are not a `StartType` option — set
  the flight up with `flight_group_inflight` instead.
- One player flight per group. If the user asks for two distinct human flights
  (e.g., two F-16s + two A-10s), build two groups, each with its own clients.
- Callsign convention: `Dodge` / `Springfield` / `Uzi` for blue, `Boris` /
  `Ivan` for red. Avoid reusing AI flight callsigns.

```python
from dcs.unit import Skill

player = m.flight_group_from_airport(
    country=usa, name="Dodge",
    aircraft_type=dcs.planes.F_16C_50,
    airport=batumi, maintask=task.CAS,
    start_type=StartType.Warm,         # hot ramp; use Cold only if user asks
    group_size=player_count,           # 1..4
)
for u in player.units:
    u.skill = Skill.Client
```

Aircraft-type sanity:

- Single-seat fighters (F-16C, F/A-18C, Mirage 2000C, MiG-29, Su-27): 1 slot per
  unit. `group_size=4` → 4 coop slots.
- Two-seat modules (F-14B, AH-64D, JF-17 ... wait, JF-17 is single-seat):
  pydcs still spawns one unit per `group_size`. The second seat (RIO/CPG) is
  occupied by joining a different multiplayer slot in-game, not via pydcs.
- Helicopters: same rule; coop pairs are usually two separate units, not
  pilot+gunner in one airframe.

## Difficulty knob

Difficulty is a **judgement call**, not a recipe. The user picks a label
(`recruit` / `trained` / `veteran` / `ace`) and you build a scenario that
*feels* like that label by combining several dials — enemy AI skill, threat
count and type, support package, weather, egress complexity.

Two missions with the same label should be allowed to feel different — one
heavy on SAMs with clear weather, another light on SAMs with night IMC —
because varied texture beats a single fingerprint.

### What each label should feel like

| Label     | Intent                                                                                                                        |
|-----------|-------------------------------------------------------------------------------------------------------------------------------|
| recruit   | Forgiving. Mistakes are recoverable. Onboarding a new pilot — they should leave the sortie with kills and a successful RTB.    |
| trained   | Squadron-default. Demands competence; punishes carelessness. A good pilot wins comfortably; a bad one loses the jet.            |
| veteran   | Hard. Demands package coordination, fuel management, situational awareness. Wins are earned, not given.                        |
| ace       | Brutal. One mistake ends the sortie. Reserve for currency-builds where the player explicitly wants to be tested.               |

### Dials you can move

When composing, reach for any subset of these — you don't need to touch every
one, and the right mix depends on the mission type and theater.

| Dial                  | Range you can pick from                                                                              |
|-----------------------|------------------------------------------------------------------------------------------------------|
| Enemy AI skill        | `Skill.Average`, `Skill.Good`, `Skill.High`, `Skill.Excellent`. Different ennemies can have different skills.                                       |
| Numeric balance       | Bandits outnumbered, parity, bandits 1.5×, 2×, 3× player flight                                      |
| Enemy missile gen     | gen-3 (R-27 / AIM-7) vs gen-4 (R-77 / AIM-120)                                                       |
| SAM threats           | none → MANPADS/Shilka → short-range radar (SA-13/8) → medium (SA-6) → long-range (SA-10)             |
| EWR / GCI coverage    | none → single EWR → EWR + GCI vectoring                                                              |
| Player support        | AWACS + tanker + escort → AWACS + tanker → AWACS only → none → denied (no datalink)                  |
| Weather / visibility  | clear day → scattered → broken → overcast/IMC → night (stacks with weather)                          |
| Time pressure         | comfortable fuel margin → tight → fuel-critical                                                      |
| Egress complexity     | clean RTB → threats along egress → divert field required                                             |
| Ingress complexity    | clean ingress → threats along ingress → divert field required                                        |
| Player loadout        | armement generation, type of missiles, bombs, etc...                                                 |
| AI wingmens           | number, skill, behavior                                                                              |

### Guidelines, not rules

- **Match the airframe and mission type.** A `veteran` CAP with an F-15C
  leans on bandit count, missile gen, and GCI — not SAMs. A `veteran` SEAD
  inverts that. Don't drop SAMs on a fighter-sweep mission just to hit a
  difficulty label.
- **Vary across runs.** If you previously built a `veteran` with Excellent
  bandits + SA-6 + AWACS only, the next one can be High bandits + IMC night
  + no support. Same label, different texture. Avoid emitting the same
  composition twice in a row.
- **Honour user overrides.** If the user says "trained but no AWACS", keep
  support at none and pull other dials downward (lower AI skill, fewer
  bandits) so the overall feel stays trained.
- **Sanity-check the worst case.** A 2-player flight against 8 Excellent
  Flankers + SA-10 + IMC + no support is not `ace` — it's unplayable. If you
  catch yourself stacking every dial to maximum, back off the least
  thematic one.
- **Make the label bite.** For `ace`, the night/IMC and no-support choices
  should actually be applied, not just labeled. For `recruit`, give the
  player a real support package and clear weather — don't just lower AI
  skill and call it done.

### Apply in code

**Document the composition in the mission briefing** so the user can see what
you actually built without reading the script:

```
Difficulty: veteran. Composition: Excellent Flankers, bandits 1.5x player
flight, AIM-120/R-77 class, AWACS only, broken cloud layer.
```

### Caveats

- The player flight is **never** set to a difficulty-derived skill — it's
  always `Skill.Client`. Difficulty applies to enemies and AI wingmen only.
- AI wingmen on the player's flight (when `player_count < group_size`, which
  is uncommon) should match difficulty: `Average` on recruit so they don't
  outshoot the player, `Excellent` on ace.


## After generating

1. Run the script (`uv run python -m dcs_mission_creator.missions.<slug>`).
2. Confirm the `.miz` was written and report its path.
3. **Do not** try to "validate" by opening the file — there's no DCS install on
   this box. Sanity-check by re-loading with `Mission().load_file(path)` if the
   user wants verification.
4. Mention which knobs the user can tweak (theater, airframe, players,
   difficulty, length, time, package, threats) so they can iterate. Show the
   exact CLI re-invocation, e.g.
   `--players 2 --difficulty veteran --airframe FA_18C_hornet --length-minutes 60`.

## Gotchas

- `cap_flight` does not exist in pydcs. Use `patrol_flight` with `patrol_type`.
- Terrain methods are **lowercase** (`m.terrain.batumi()`, not `Batumi()`).
- `Point(x, y)` uses DCS world coords (meters), not lat/lon. Anchor on
  `airport.position` and add offsets.
- `m.country(name)` takes a *string* — pass `countries.USA.name`, not `USA`.
- `group_size` caps at 4 for fighters; AI flights of 5+ desync formations.
- Saving requires the parent directory to exist — `mkdir(parents=True)` first.
- Don't commit `.miz` outputs; they're binary and large.
