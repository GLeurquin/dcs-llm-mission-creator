# Project notes for Claude

This is a pydcs-based DCS mission generator. Three docs, one job each:

- [.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md)
  — **design intent**: what package to build, difficulty policy, pacing,
  briefing / voice / F10 conventions.
- [.claude/skills/dcs-mission/PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md)
  — **the pydcs API**: terrain, flight / ground helpers, tasks, triggers,
  weather, F10 drawings, coordinates, save, and every gotcha — signatures
  verified against the installed source under
  [.venv/lib/python3.12/site-packages/dcs/](.venv/lib/python3.12/site-packages/dcs/).
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
  - class attributes `name: str` (filesystem slug, matches the filename) and
    `title: str` (display name);
  - `def build_miz(self, miz_path: Path) -> None` — writes the `.miz`. Use
    `self.players` (1–4, validated by the base class) for client-slot counts;
  - `def readme(self) -> str` — returns the README.md content (markdown,
    the mission briefing).
- The base class provides
  `generate(output_dir: Path) -> tuple[Path, Path]` which creates
  `output_dir`, calls `build_miz(output_dir / f"{name}.miz")`, then writes
  `output_dir / "README.md"`. **Do not override `generate`.**
- Each module also exposes an argparse `main()` so the script remains
  runnable via `python -m`. Pattern at the bottom of the file:

  ```python
  def main() -> None:
      parser = argparse.ArgumentParser(description="…")
      parser.add_argument("--output-dir", type=Path,
                          default=Path("out") / "<slug>",
                          help="Output directory for the .miz and README.md")
      parser.add_argument("--players", type=int, default=1, choices=[1, 2, 3, 4])
      args = parser.parse_args()
      miz, readme = <ClassName>(players=args.players).generate(args.output_dir)
      print(f"wrote {miz}")
      print(f"wrote {readme}")
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

Missions call this in a `_draw_plan` step near the end of `build_miz` (after
triggers, before `_add_briefing`). Spawn helpers that own friendly geometry
(the ingress corridor, AWACS/tanker/CAP tracks) return their `Point`s so
`_draw_plan` can annotate them.

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
`(m, country, position, heading, *, launchers=…, prefix="", skill=Skill.Average,
overlay=None, terrain=None) -> VehicleGroup`. Like the other core helpers:
absolute world `Point` in, raw pydcs `Country` in (no faction abstraction), a
built group out. `set_skill(group, skill)` is exported too (replaces the
per-mission `_set_skill`). Get the `position` from the `core/placement.py`
helpers (`sam_site_on_ridge`, etc.) as usual. Design rules (what threat where,
per difficulty) live in the `dcs-mission` skill; unit-type catalog and the
pydcs `VehicleTemplate` split in PYDCS_REFERENCE.md §5.

## AI-tasking helpers (project-owned)

[`tasking`](src/dcs_mission_creator/core/tasking.py) holds the three AI-verbs
that carry real project policy — not 1:1 pydcs passthroughs (carrier/nav
beacons stay raw pydcs; see PYDCS_REFERENCE.md §6.5):

```python
from dcs_mission_creator.core import tasking

tasking.apply_ai_difficulty(cap, self._difficulty)   # ROE/react/radar/ECM/bingo
tasking.fac_attack_group(jtac, convoy, frequency=133) # JTAC lases target group
tasking.scramble_on_trigger(m, reserve,               # cold-ramp alert-5
                            condition.PartOfCoalitionInZone("blue", zone.id))
```

- `apply_ai_difficulty(group, difficulty)` — maps the mission's recruit→ace
  label onto `OptROE`/`OptReactOnThreat`/`OptRadarUsing`/`OptECMUsing`/
  `OptRTBOnBingoFuel`/`OptRestrictAfterburner`. A behaviour dial distinct from
  raw `Skill`. Takes `Difficulty` or a str label; reuses the `map_draw.py`
  enum.
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
    [ArmSite(sa6, "SA-6", probability=0.9, delay_s=(3, 7), shutdown_s=(70, 130)),
     ArmSite(sa2, "SA-2", probability=0.7), *ewr_groups],   # bare groups OK
    voice=self._voice,                       # calls are spoken as well as printed
    down_call="Magic: {label} has ceased emissions, site is dark.",
    up_call="Magic: {label} is radiating again, expect it hot.",
)
```

Per-site dials are the SEAD difficulty statement: `probability` (does this crew
catch the launch at all), `delay_s` (recognition lag — a close-in HARM still
kills), `shutdown_s` (how long it stays dark; repeat fire extends it),
`react_range_m` (how far down the net the launch travels). Only list
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

`build_miz` is the orchestrator, not the implementation. Each block of the
mission gets its own method whose name says what the block produces and whose
docstring states the design intent in one line. Pattern (see
[coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py)):

```python
def build_miz(self, miz_path: Path) -> None:
    """Assemble the mission by calling each step in package order."""
    m = Mission(self._terrain)
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
    self._add_briefing(m)

    miz_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(miz_path))
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
uv run ruff check src/                 # lint
uv run ruff format --check src/        # formatting check (use `format` to apply)
uv run ty check src/                   # static type check (astral ty)
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

The hook fails the commit if `ruff check`, `ruff format`, or `ty check`
reports anything. Fix the diagnostic, re-stage, commit again.
