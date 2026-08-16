# Project notes for Claude

This is a pydcs-based DCS mission generator. Three docs, one job each:

- [.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md)
  — **design intent**: what package to build, difficulty policy, pacing,
  briefing / voice / F10 conventions.
- [.claude/skills/dcs-mission/PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md)
  — **the pydcs API**: terrain, flight / ground helpers, tasks, triggers,
  weather, F10 drawings, coordinates, save, and every gotcha — signatures
  verified against the installed source under
  [.venv/lib/python3.14/site-packages/dcs/](.venv/lib/python3.14/site-packages/dcs/).
  Consult it before guessing any API shape.
- **This file** — **project conventions**: package layout, the
  `MissionBuilder` contract, the project-owned `VoiceSynth` / `PlanOverlay`
  helpers, script structure, faction naming, running, and lint / type-check.

## Package layout

- Mission scripts go in
  [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) as
  `<scenario_slug>.py`. Each module defines **one** concrete subclass of
  `MissionBuilder` (from
  [core/mission_builder.py](src/dcs_mission_creator/core/mission_builder.py)) with:
  - class attributes `name: str` (filesystem slug, matches the filename),
    `title: str` (display name), and `difficulty: Difficulty` (the enum from
    [core/difficulty.py](src/dcs_mission_creator/core/difficulty.py), **not**
    a string) — it drives both the F10 reveal and the enemy ROE;
  - `def _assemble(self, m: Mission) -> MapOverlay` — builds the whole mission
    into `m` and returns the overlay its positions came from. Use
    `self.players` (1–4, validated by the base class) for client-slot counts;
  - `def readme(self) -> str` — returns the README.md content (markdown,
    the mission briefing).
- The base class owns everything around that, and **a mission overrides none
  of it**:
  - `build_miz` constructs the `Mission`, calls `_assemble`, snaps every
    flight's take-off/landing waypoints to field elevation, makes the output
    directory and saves. Snapping has to happen after the last flight exists
    and before the save — pydcs hard-codes those altitudes to zero, so a
    mission that skipped it shipped a jet spawned underground. It is in the
    base precisely so it cannot be forgotten.
  - `generate(output_dir) -> tuple[Path, Path]` seeds the RNG, then writes
    `<name>.miz` and `README.md`.
- Each module also exposes a one-line `main()` so the script stays runnable
  via `python -m`. The argparse lives in
  [core/cli.py](src/dcs_mission_creator/core/cli.py), which takes the slug and
  title off the class, so the two cannot drift:

  ```python
  def main() -> None:
      run_cli(CoastalCover)


  if __name__ == "__main__":
      main()
  ```
- `out/` is gitignored; `*.miz` is also gitignored.
- [src/dcs_mission_creator/__main__.py](src/dcs_mission_creator/__main__.py)
  auto-discovers every public submodule of `missions/`, finds its
  `MissionBuilder` subclass, and exposes it as a `generate <name>` subcommand
  (plus `list`). The `pyproject.toml` `dcs-mission-creator` entry-point targets
  `__main__:main`, so both `uv run dcs-mission-creator generate <slug>` and
  `uv run python -m dcs_mission_creator generate <slug>` work once the project
  is installed. The slug is optional: `generate` with no name builds **every**
  discovered mission (each into its own `<slug>/` folder), logging and skipping
  past any that raise, then exiting 1 if any failed.
- Default output for `generate` is the folder
  `$DCS_MISSIONS_FOLDER/IAGeneratedMissions/<slug>/`, which receives both
  `<slug>.miz` and `README.md`. The CLI errors out if `DCS_MISSIONS_FOLDER`
  is unset and no `--output-dir` is given.
- The README content lives on the class (`readme()` method) — it should be
  the mission *briefing* in markdown (situation, mission, package, threats,
  ROE, navigation, frequencies, win/loss conditions, re-generation command),
  not just metadata. In-game briefing (`set_description_text`) is a separate
  plain-text view — both pull from class state so they stay consistent.

## Voice-over helper (project-owned)

[`VoiceSynth`](src/dcs_mission_creator/core/tts/synth.py) renders TTS audio
(Piper by default), caches WAVs to `cache/voice/`, registers them on
`mission.map_resource`, and appends the right `SoundTo*` action to a trigger
rule. Every mission instantiates one in `__init__` (`self._voice =
VoiceSynth()`). Methods (text matches the on-screen `MessageTo*` body
word-for-word):

```python
self._voice.attach_to_all(m, rule, text)
self._voice.attach_to_coalition(m, rule, text, coalition="blue")   # or "red"
self._voice.attach_to_group(m, rule, text, group_id=group.id)
```

Cache key combines `backend.fingerprint()` + text, so swapping voice or
backend invalidates without collision. Renders are deterministic per
backend; commit the cache only if you want reproducible CI builds (we
don't).

For audio played from **mission Lua** rather than a trigger action, use
`self._voice.register(m, text) -> str`: it renders, adds the WAV to the
mission, and returns the in-`.miz` file name that
`trigger.action.outSound*(…, "<name>.wav")` expects (the `SoundTo*` actions
take a resource key instead — that is what `attach_to_*` wires up).

## Map-drawing helper (project-owned)

[`PlanOverlay`](src/dcs_mission_creator/core/map_draw.py) wraps the pydcs F10
drawing API (see the `F10 map drawings` section of
[PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md)) and
paints the *plan* on the blue layer so the player reads the sortie at a
glance. It owns two things the raw pydcs `Layer` does not: **faction-correct
placement** (always the blue `StandardLayer`) and **difficulty-scaled enemy
reveal**. Construct it per mission with the mission's difficulty label:

```python
plan = PlanOverlay(m, "trained")           # or Difficulty.TRAINED
plan.objective(scene.ao_center, "AO — convoy axis", radius=6_000.0)
plan.route(corridor, "Dodge ingress")      # list[Point] of the flown route
plan.orbit(p1, p2, "Eagle CAP")            # a friendly race-track leg
plan.waypoint_label(pos, "Magic AWACS")
plan.threat(sa13_pos, radius=8_000.0, label="SA-13", icon=StandardIcon.AirDefense)
plan.threat_area(center, 28_000.0, "SA-6 + bandit CAP — vicinity")
```

Pass **absolute** world `Point`s — `PlanOverlay` does the layer selection,
colour choice (enemy red / friendly cyan / objective amber), the
anchor-relative offset math the point-list drawings need, and the difficulty
policy. `.threat()` is the difficulty dial: full icon + true ring on
`recruit`, coarse + offset + "(est.)" on `trained`, **no-op** on
`veteran`/`ace` (use `.threat_area()` for a vague zone there). `.objective()`
tightens/loosens the same way. Friendly-plan calls (`route`, `orbit`,
`waypoint_label`) always draw precisely. Design rules (what to draw, reveal
per label) live in the `dcs-mission` skill; the underlying pydcs drawing API
lives in PYDCS_REFERENCE.md.

Missions call this in a `_draw_plan` step near the end of `_assemble`, before
`_add_briefing`. Whether it runs before or after the trigger steps does not
matter — drawing reads no trigger state, and the two most complex missions
(`eastern_shield`, `idlib_gauntlet`) draw first. Spawn helpers that own friendly geometry
(the ingress corridor, AWACS/tanker/CAP tracks) return their `Point`s so
`_draw_plan` can annotate them.

[`core/visibility.py`](src/dcs_mission_creator/core/visibility.py) owns the
other half of that policy — **what the map does not show**. Enemy groups never
appear as stock unit icons; the player's picture is the briefing plus what
`PlanOverlay` deliberately draws:

```python
from dcs_mission_creator.core.visibility import conceal, conceal_country

conceal_country(russia, syria)   # every group those countries own
conceal(convoy, sa6, reserve)    # or a hand-picked list; None entries skipped
```

Both set `hidden` / `hidden_on_planner` / `hidden_on_mfd` (F10 map, briefing
mission-planner map, datalink) — cosmetic only, the group still spawns,
radiates and shoots. They live outside `map_draw.py` because they never touch
a drawing. Missions call `conceal_country` from a `_conceal_red` step placed
right before `_draw_plan`; prefer it over `conceal` so a
late-activated reserve or a newly added EWR cannot be forgotten. Design rules
live in the `dcs-mission` skill; the raw pydcs attributes in
PYDCS_REFERENCE.md §5.

## Air-defense builder (project-owned)

[`air_defense`](src/dcs_mission_creator/core/air_defense.py) builds a **whole
SAM site** — search radar + track/fire-control radar + command post +
launchers, wired into one `VehicleGroup` — from a single call, so spawn
helpers stop copy-pasting component type lists and offset math. It fills only
the sites pydcs's `dcs.templates.VehicleTemplate` lacks; for
`sa6/sa10/sa11/sa15` + `patriot/hawk` call `VehicleTemplate` directly.

```python
from dcs_mission_creator.core import air_defense as ad

sa3 = ad.build_sa3_site(m, russia, ridge, heading=270, launchers=4,
                        skill=Skill.High)
# rough terrain: pass the overlay + terrain to snap units off canopy/water
sa5 = ad.build_sa5_site(m, russia, hill, heading=180,
                        overlay=scene.overlay.overlay, terrain=self._terrain)
```

Builders: `build_sa2/sa3/sa5/sa8/sa13/sa15/sa19_site`,
`build_nasams/irist/roland/rapier/hq7_site`
`(m, country, position, heading, *, launchers=None, prefix="", skill=Skill.Average,
overlay=None, terrain=None) -> VehicleGroup` — the shared `SiteBuilder`
signature; `launchers=None` means "whatever this system normally fields". Each
site is a `_SiteSpec` table entry (leader, components, launcher type) assembled
by one engine, so **adding a system is a table entry, not a new function**.
Pass `overlay` *and* `terrain` together or neither — one alone used to skip
snapping in silence and now warns. Like the other core helpers:
absolute world `Point` in, raw pydcs `Country` in (no faction abstraction), a
built group out. `set_skill(group, skill)` is exported too (replaces the
per-mission `_set_skill`). Get the `position` from the `core/placement.py`
helpers (`sam_site_on_ridge`, etc.) as usual. Design rules (what threat where,
per difficulty) live in the `dcs-mission` skill; unit-type catalog and the
pydcs `VehicleTemplate` split in PYDCS_REFERENCE.md §5.

## Waypoint-altitude helper (project-owned)

[`waypoints`](src/dcs_mission_creator/core/waypoints.py) puts on the terrain
the waypoints that belong there. pydcs carries no height map: `land_at()` and
the take-off point of an airfield start are hard-coded to `alt = 0` (buried
under any field above sea level), and a steerpoint dropped on a ground target
keeps the route's ingress altitude. Those altitudes are the steerpoint
elevations the jet's CCRP/CCIP, HUD and DED read, so both get corrected from
the overlay's elevation raster:

```python
from dcs_mission_creator.core import waypoints

ov = scene.overlay.overlay                     # the MapOverlay behind TacticalScene
waypoints.add_ground_waypoint(player, scene.route_mid, overlay=ov,
                              speed=750, name="CONVOY AO")
waypoints.snap_base_waypoints(m, ov)           # every flight's take-off + landing
waypoints.ground_elevation_m(ov, point)        # raw lookup, 0.0 outside the overlay
```

`snap_base_waypoints` walks every flying group in the mission, so it cannot
miss a flight added later. **Missions no longer call it** —
`MissionBuilder.build_miz` runs it after `_assemble` returns and before the
save, which is why `_assemble` returns the overlay.
`add_ground_waypoint` is for **client** routes only: an
AI flight flies its route altitudes, so a deck-level turning point flies it
into the terrain. Missions with no other overlay need (`daryal_run`,
`abkhaz_sweep`) carry a `load_scene(<theater>)` handle in their `_Scene` for
this. Design rules (which waypoints, where the run-in altitude goes) live in
the `dcs-mission` skill; the pydcs waypoint API and the gotcha in
PYDCS_REFERENCE.md §4.3.

## Threat-aware routing helper (project-owned)

[`routing`](src/dcs_mission_creator/core/routing.py) plans AI routes *around*
the SAM belts. pydcs plans them straight through: `Mission.strike_flight` joins
base → IP → target → base on one line, with an attack waypoint pydcs hard-codes
to `alt = 0`, so a strike pair flies the missile engagement zone the briefing
told the *player* to avoid and then dives at the SHORAD. Build the route by
hand instead:

```python
from dcs_mission_creator.core.routing import ThreatRing, avoid_threats, standoff_point

belts = (ThreatRing(sa2_pos, 40_000.0, "SA-2 belt"),      # same radii `_draw_plan` paints
         ThreatRing(sa6_pos, 25_000.0, "SA-6 belt"),
         ThreatRing(sa8_pos, 10_000.0, "SA-8 belt"))
ip = standoff_point(target, toward=scene.hatay.position, threats=belts)
for pt in avoid_threats(scene.hatay.position, ip, belts)[1:]:
    flight.add_waypoint(pt, altitude=6400, speed=800)
```

- `avoid_threats(start, target, threats, *, clearance_m=…)` — bends each leg
  around the ring it cuts deepest until nothing enters a ring. Rings covering
  `start` or `target` are **skipped** (you cannot detour out of the envelope
  your target sits in) — that is why the IP comes from `standoff_point` first,
  so the unavoidable exposure is a short run-in, not the whole ingress.
- `standoff_point(target, *, toward, threats, …)` — IP / hold point outside
  every ring, searched outward from the target and swinging round the flank
  when the direct side is covered.
- `ThreatRing(position, radius_m, label)` — build these **once** per mission
  (an `_threat_rings` step) and feed both `_draw_plan` and the routing calls,
  so the ring the player is briefed on is the ring the friendly package flies
  around. Radar-only sites (EWR) are not rings — nothing needs to avoid them.

Geometry only: no groups, tasks or waypoints. The behaviour half —
`tasking.apply_threat_reaction` — is a separate call and every routed flight
wants both. Design rules (how much exposure a difficulty accepts) live in the
`dcs-mission` skill.

## AI-tasking helpers (project-owned)

[`tasking`](src/dcs_mission_creator/core/tasking.py) holds the AI-verbs
that carry real project policy — not 1:1 pydcs passthroughs (carrier/nav
beacons stay raw pydcs; see PYDCS_REFERENCE.md §6.5):

```python
from dcs_mission_creator.core import tasking

tasking.apply_ai_difficulty(cap, self._difficulty)   # ROE/react/radar/ECM/bingo
tasking.apply_threat_reaction(pontiac)               # friendly: bypass/CM/ECM
tasking.fac_attack_group(jtac, convoy, frequency=133) # JTAC lases target group
tasking.scramble_on_trigger(m, reserve,               # cold-ramp alert-5
                            condition.PartOfCoalitionInZone("blue", zone.id))
```

- `apply_ai_difficulty(group, difficulty)` — maps the mission's recruit→ace
  label onto `OptROE`/`OptReactOnThreat`/`OptRadarUsing`/`OptECMUsing`/
  `OptRTBOnBingoFuel`/`OptRestrictAfterburner`. A behaviour dial distinct from
  raw `Skill`. Takes `Difficulty` or a str label; reuses the `map_draw.py`
  enum. It is the **enemy** dial — don't reach for it on the friendly package.
- `apply_threat_reaction(flight, *, reaction=ByPassAndEscape, …)` — the
  friendly counterpart: `OptReactOnThreat` (fly around a threat zone rather
  than through it), `OptChaffFlareUsing` inside a SAM WEZ, `OptECMUsing` on
  lock, RTB on bingo. Every AI flight in a blue package gets it; escalate to
  `AllowAbortMission` for one that should turn around rather than press a live
  belt. Pair it with `core/routing.py` — the option only covers the site the
  planner did not know about.
- `fac_attack_group(fac_group, target_group, *, designation=Laser,
  frequency=…)` — turns a ground JTAC or airborne FAC(A) into a controller
  that lases `target_group`; derives both the id **and** name pydcs needs
  (mismatch = silent no-op).
- `scramble_on_trigger(m, group, *conditions)` — sets the flight uncontrolled
  with a queued `StartCommand` and pushes it on the condition(s); generalizes
  pydcs `FlyingGroup.delay_start` (time-only) to any condition.

## SAM EMCON / HARM-reaction helper (project-owned)

[`emcon`](src/dcs_mission_creator/core/emcon.py) gives radar sites the one
behaviour DCS does not model: reacting to an anti-radiation shot. Left alone, a
site radiates while the HARM rides the beam in, so every SEAD shot is a free
kill. `arm_emcon_reaction` writes one mission-start `DoScript` whose Lua hooks
`S_EVENT_SHOT`, recognises ARM weapons, and cycles the listed sites through
`ALARM_STATE GREEN` (radars off, weapons hold) and back to `RED`:

```python
from dcs_mission_creator.core.emcon import ArmSite, arm_emcon_reaction

arm_emcon_reaction(
    m,
    [ArmSite(sa6, "SA-6", probability=0.9, delay_s=(3, 7), shutdown_s=(280, 400)),
     ArmSite(sa2, "SA-2", probability=0.7), *ewr_groups],   # bare groups OK
    voice=self._voice,                       # calls are spoken as well as printed
    down_call="Magic: {label} has ceased emissions, site is dark.",
    up_call="Magic: {label} is radiating again, expect it hot.",
)
```

Per-site dials are the SEAD difficulty statement: `probability` (does this crew
catch the launch at all), `delay_s` (recognition lag — a close-in HARM still
kills), `shutdown_s` (how long it stays dark — minutes, not seconds, so a HARM buys a
real working window; repeat fire extends it), `react_range_m` (how far down the
net the launch travels). Radio calls are queued and played
`announce_spacing_s` apart, so a shot that darkens a whole belt still gets one
call per site instead of only the first. Only list
**radar-guided** sites — SA-13/MANPADS have nothing to shut down, and listing a
mixed convoy would make the whole column hold fire on every HARM shot. Design
rules (what reveal, what difficulty) live in the `dcs-mission` skill; the pydcs
`DoScript` mechanics are in PYDCS_REFERENCE.md §7.

## Mission Lua lives in `.lua` files

Never embed mission-script Lua as a Python string literal. Every `DoScript`
payload is a real file under
[core/lua/](src/dcs_mission_creator/core/lua/) (e.g. `emcon.lua`), loaded via
that package's loader:

```python
from dcs_mission_creator.core import lua

script = lua.render("emcon.lua", SITES=rows, SIDE="coalition.side.BLUE",
                    COOLDOWN="20.0")
rule.add_action(lua.InlineDoScript(script))
```

Attach it with `lua.InlineDoScript`, **never** pydcs's `action.DoScript`: that
one parks the Lua in the l10n dictionary and emits
`a_do_script(getValueDictByKey("DictKey_…"))`, but DCS does not resolve
dictionary keys inside the scripting sandbox — it hands the key back and
compiles *that* as Lua, so the trigger dies at mission start with
`[string "DictKey_Translation_N"]:1: '=' expected near '<eof>'`.
`InlineDoScript` writes the source into the action's own `text` field, the way
stock ED missions do.

`lua.render(name, **subs)` replaces `__KEY__` placeholders with already-formatted
Lua source and raises if a substitution names a placeholder the file lacks or if
any `__PLACEHOLDER__` survives (a leftover would be a Lua syntax error at mission
start). It does no quoting — build literals on the Python side (see `_lua_str` in
[core/emcon.py](src/dcs_mission_creator/core/emcon.py)). `lua.source(name)`
returns the raw text. New `.lua` files are picked up automatically; the
`[tool.setuptools.package-data]` entry ships them in the wheel.

## Script structure: small named functions

`_assemble` is the orchestrator, not the implementation. Each block of the
mission gets its own method whose name says what the block produces and whose
docstring states the design intent in one line. Pattern (see
[coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py)):

```python
def _assemble(self, m: Mission) -> MapOverlay:
    """Assemble the mission by calling each step in package order."""
    self._set_time(m)
    self._set_weather(m)
    scene = self._setup_airports(m)
    usa, russia = m.country("USA"), m.country("Russia")

    convoy, _sa13, _ewr = self._spawn_red_ground(m, russia, scene)
    self._spawn_awacs(m, usa, scene)
    hog = self._spawn_strike(m, usa, scene, target_unit=convoy.units[4])
    self._spawn_cap(m, usa, scene)
    self._spawn_red_intercept(m, russia, scene)
    self._spawn_player(m, usa, scene)

    self._add_end_triggers(m, convoy=convoy, hog=hog)
    self._conceal_red(russia)
    self._draw_plan(m, scene, ...)
    self._add_briefing(m)

    return scene.overlay.overlay   # the base snaps base waypoints, then saves
```

Rules:
- Each helper does **one thing** (one flight, one ground cluster, one
  trigger group, one briefing block). If a helper grows past ~25 lines,
  split it further (e.g. `_spawn_red_ground` → `_spawn_red_convoy` /
  `_spawn_red_shorad` / `_spawn_red_ewr`).
- The docstring is the design intent ("F-15C 2-ship Eagle on a race-track
  between Batumi and the AO"), not a restatement of the code.
- Bundle resolved airports + AO center into a `_Scene` dataclass so spawn
  helpers take `scene` instead of five positional airport args. Type the
  fields as `Airport` (`dcs.terrain.terrain.Airport`) so ty can verify
  downstream calls like `player.land_at(scene.batumi)`.
- Return the groups the orchestrator still needs (e.g. `convoy`, `hog` for
  end-of-mission triggers); name throwaways with a leading underscore.

## Mission scaffolding helpers (project-owned)

Four small modules hold what every mission used to carry its own copy of. None
of them holds policy: force composition, timings and text stay in the mission.

- [`core/mission_kit.py`](src/dcs_mission_creator/core/mission_kit.py) —
  `offset(origin, *, east_m, north_m)` (DCS `x` is north and `y` is east; this
  is why call sites never say so), `mark_clients(group)`, and a re-export of
  `set_skill`. Import these rather than redefining them.
- [`core/weather.py`](src/dcs_mission_creator/core/weather.py) — state the
  weather as a record instead of fourteen assignments:

  ```python
  Weather(
      name="Spring scattered", season_temperature=18.0,
      clouds_base=2400, clouds_thickness=600, clouds_density=4,
      visibility_distance=80_000,
      wind_at_ground=Wind(300, 4), wind_at_2000=Wind(290, 7),
      wind_at_8000=Wind(280, 12),
  ).apply(m)
  ```
- [`core/triggers.py`](src/dcs_mission_creator/core/triggers.py) — the
  voice-plus-text radio call. **Use these instead of hand-rolling the rule**:
  they take one `text` and use it for both the on-screen `MessageTo*` and the
  TTS render, so the two cannot drift out of sync, which is a convention this
  project otherwise enforces only by eye.

  ```python
  from dcs_mission_creator.core import triggers as mission_triggers

  mission_triggers.message_to_all(m, comment="Strike successful", voice=self._voice,
                                  conditions=(condition.GroupDead(convoy.id),),
                                  text="Magic: the column is wrecked...")
  mission_triggers.message_to_coalition(m, ...)   # one coalition only
  mission_triggers.checkin(m, at_seconds=180, ...)  # support check-in on the clock
  mission_triggers.intro(m, ...)                    # TriggerStart mission picture
  ```
- [`core/cli.py`](src/dcs_mission_creator/core/cli.py) — `run_cli(TheBuilder)`,
  the whole body of a mission's `main()`.

## Reproducibility

Building the same mission twice produces the same `.miz`, byte for byte. That
is not free — four separate things had to be pinned, and all four are easy to
undo by accident:

- `MapOverlay` carries the sampling `seed` (default 0). `find_placement` takes
  no per-call seed; build the overlay with a different one to resample.
- `MissionBuilder.generate` seeds the stdlib `random` from the mission slug.
- `MissionBuilder._pin_runway_waypoint_distance` — pydcs declares
  `add_runway_waypoint(..., distance=random.randrange(6000, 8000, 100))`, a
  **default argument**, so the value is drawn once when `dcs.unitgroup` is
  imported, before any seeding can run. It moved every flight's take-off point.
- `MissionBuilder._pin_onboard_numbers` — pydcs picks tail numbers with
  `set.pop()` over a set of strings, which follows string hashing.

If a change makes generation non-deterministic, the smoke test catches it.

## Tests

`tests/` runs without a DCS installation **and** without the built map overlay,
because CI has neither. That is a hard constraint on anything committed here:

- Default selection (`pytest -m "not slow"`) is what CI and pre-commit run.
- `@pytest.mark.slow` is for anything needing the overlay — currently only
  `tests/test_mission_smoke.py`, which skips itself when the overlay is absent.
- A `Mission(Caucasus())` is cheap and needs neither, so core helpers that take
  a mission (`air_defense`, `triggers`, `weather`) are tested normally.
- **Do not assert on mission content.** The smoke test checks that every
  mission builds a readable `.miz` and builds reproducibly, not what is in it;
  freezing composition would make every balance tweak look like a regression.

## Briefings read as intel, not as the mission file

`readme()`, `_in_game_briefing()`, the side-task texts and every trigger
message are written by a squadron intel officer, not by the person who wrote
the triggers. Two rules carry most of it (full guidance, with a don't/do
table, in the `dcs-mission` skill):

- **No trigger logic in prose.** No thresholds, percentages, countdowns, flag
  or zone vocabulary — "Pontiac is held in reserve and will run the column
  once the SAM threat is suppressed", never "released when the SA-6 radar dies
  or at T+25". Win conditions read as outcomes ("render the column
  combat-ineffective"), not as scores ("70% destroyed"). The exception is the
  README's `**Difficulty:**` metadata line, which does state the composition.
- **Enemy claims carry a source and an age** ("a Rivet Joint track overnight
  fixed two emitters", "this morning's Reaper feed", "unconfirmed
  partner-force reporting"). The source justifies how precise the claim is,
  and `_draw_plan` must not draw more precision than the prose claims.

## Faction naming in narrative

Briefings (`readme()` and `_in_game_briefing()`) name **factions** (USA,
USAF, Russia, Russian, Ukraine, ...), never `red` / `blue`. Coalition terms
remain only at the pydcs API layer (`airport.set_blue()`,
`set_description_bluetask_text`, `Coalition.Blue`) and in code comments.

| Layer                  | Wording                                  |
|------------------------|------------------------------------------|
| pydcs API calls        | `set_blue` / `set_red` / `Coalition.Blue`|
| Code comments          | "blue side", "red side" OK               |
| `readme()` markdown    | "Russian convoy", "USAF A-10s"           |
| `_in_game_briefing`    | "Russian MiG-29S", "USAF AWACS"          |
| Side-task text         | Name the faction, never "red/blue"       |

## Existing missions

- [coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py) —
  Caucasus mix: F-16C escort/CAP from Batumi over an A-10C strike on a
  Russian convoy near Senaki, MiG-29S intercept from Sukhumi-Babushara,
  trained difficulty, ~50 min sortie. Generates to `out/coastal_cover.miz`.
- [idlib_gauntlet.py](src/dcs_mission_creator/missions/idlib_gauntlet.py) —
  Syria: F-16C out of Hatay against a Syrian resupply column with organic
  SHORAD, run through three SAM belts (SA-2 / SA-6 / SA-8 + EWR) that go dark
  on HARM fire and re-radiate (`core/emcon.py`). Trained difficulty, ~60 min.
  The reference for missions that want realistic SEAD.

All six missions ([coastal_cover](src/dcs_mission_creator/missions/coastal_cover.py),
[kodori_strike](src/dcs_mission_creator/missions/kodori_strike.py),
[eastern_shield](src/dcs_mission_creator/missions/eastern_shield.py),
[idlib_gauntlet](src/dcs_mission_creator/missions/idlib_gauntlet.py),
[abkhaz_sweep](src/dcs_mission_creator/missions/abkhaz_sweep.py),
[daryal_run](src/dcs_mission_creator/missions/daryal_run.py)) paint an F10
briefing plan via a `_draw_plan` step using `PlanOverlay` — see the
map-drawing helper section above. The four trained missions (coastal_cover,
kodori_strike, eastern_shield, idlib_gauntlet) draw estimated threat rings +
NATO icons; the two ace missions (abkhaz_sweep, daryal_run) draw only the
friendly plan plus a single vague threat zone (enemy positions withheld).

All six also run a `_conceal_red` step (`conceal_country`) immediately before
`_draw_plan`, so no enemy group shows up as a unit icon on the F10 map, the
briefing mission-planner map, or the datalink — the drawn plan and the
briefing are the player's only intel.

## DCS installation (loadouts)

pydcs reads stock payloads out of the **installed game**, and finds it only
through the Windows registry — under WSL that fails, so
`load_task_default_loadout()` silently leaves every pylon empty.
[`core/dcs_install.py`](src/dcs_mission_creator/core/dcs_install.py) fixes that
from the `DCS_INSTALL_DIR` env var; `MissionBuilder.__init__` calls
`dcs_install.configure()` before any flight exists (pydcs caches its payload
directories on first use), so missions need to do nothing.

```bash
export DCS_INSTALL_DIR="/mnt/e/Games/DCS World OpenBeta"   # 'E:\Games\…' also accepted
# optional: only if the Saved Games folder isn't auto-found
export DCS_SAVED_GAMES_DIR="/mnt/c/Users/<user>/Saved Games/DCS.openbeta"
```

Unset, the helper logs a warning and every generated flight flies clean.
Liveries stay at the DCS default even when set — pydcs's livery scanner splits
paths on `\` and cannot be used off Windows (see the docstring).

## Running

(rely on the `DCS_MISSIONS_FOLDER` and `DCS_INSTALL_DIR` env vars being
available)

```bash
uv run python -m dcs_mission_creator.missions.coastal_cover --players 1

# or via the unified CLI (auto-discovers every mission module):
uv run dcs-mission-creator list
uv run dcs-mission-creator generate coastal_cover --players 1
```

## Lint and type-check

Always run both after editing Python under `src/` (and fix what they flag
before reporting a task done):

```bash
uv run ruff check src/ tests/          # lint
uv run ruff format --check src/ tests/ # formatting check (use `format` to apply)
uv run ty check src/                   # static type check (astral ty)
uv run pytest -m "not slow"            # what CI and pre-commit run
uv run pytest                          # adds the overlay-dependent smoke test
```

`ruff` and `ty` are pinned in `pyproject.toml`'s `[dependency-groups].dev` and
installed via `uv sync`. `ty` is pre-1.0; tolerate occasional false positives
but do not silence errors blindly — prefer fixing the annotation. For
pydcs-induced invariance complaints on `List[Type[…]]` arguments, cast with
`cast(list[type[VehicleType]], [...])` rather than redesigning the call site.

### Pre-commit (prek)

`.pre-commit-config.yaml` wires the same hooks. Use Astral's
[`prek`](https://github.com/j178/prek) — a Rust drop-in for `pre-commit`:

```bash
uv tool install prek          # once
prek install                  # wire .git/hooks/pre-commit
prek run --all-files          # manual full run
```

The hook fails the commit if `ruff check`, `ruff format`, `ty check` or the
fast test selection reports anything. `.github/workflows/ci.yml` runs the same
four on every push. Fix the diagnostic, re-stage, commit again.
