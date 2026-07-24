# Project notes for Claude

This is a pydcs-based DCS mission generator. The `dcs-mission` skill at
[.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md)
drives the high-level design. The notes below are pydcs-specific quirks
verified against the installed source under
[.venv/lib/python3.12/site-packages/dcs/](.venv/lib/python3.12/site-packages/dcs/)
— consult them before guessing API shapes.

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
  is installed.
- Default output for `generate` is the folder
  `$DCS_MISSIONS_FOLDER/IAGeneratedMissions/<slug>/`, which receives both
  `<slug>.miz` and `README.md`. The CLI errors out if `DCS_MISSIONS_FOLDER`
  is unset and no `--output-dir` is given.
- The README content lives on the class (`readme()` method) — it should be
  the mission *briefing* in markdown (situation, mission, package, threats,
  ROE, navigation, frequencies, win/loss conditions, re-generation command),
  not just metadata. In-game briefing (`set_description_text`) is a separate
  plain-text view — both pull from class state so they stay consistent.

## Verified pydcs API (v0.15.0 from the git head)

The SKILL.md hints are mostly right but a few things contradict the installed
source — trust the source.

### Terrain & airports
- `Mission(terrain=Caucasus())` — pass a terrain instance, default is Caucasus.
  Terrain classes (in `dcs.terrain`): `Caucasus`, `PersianGulf`, `Syria`,
  `Nevada`, `Normandy`, `TheChannel`, `Sinai`, `Falklands`, `MarianaIslands`.
  Note: the Marianas class is `MarianaIslands`, not `Marianas`.
- **Airports are dict-indexed, not methods.** Use `m.terrain.airports["Batumi"]`.
  Names use the display form ("Sukhumi-Babushara", "Senaki-Kolkhi",
  "Krasnodar-Center", …).
- `airport.set_blue()` / `set_red()` / `set_neutral()` mutate coalition.
  Caucasus airports default neutral — always set explicitly for any base you
  spawn from.
- Bullseye lives at `terrain.bullseye_blue` / `bullseye_red` as `{"x":…, "y":…}`
  dicts.

### Mission constructor
- `Mission.__init__` pre-populates **all** coalitions/countries on both sides.
  Just call `m.country("USA")` / `m.country("Russia")` — no `add_country`
  needed.
- `m.start_time` is a UTC `datetime`. Caucasus UTC offset is +4, so 10:00 local
  on 15 May 2026 = `datetime(2026, 5, 15, 6, 0, 0, tzinfo=timezone.utc)`.

### Flight helpers (`dcs/mission.py`)
- `flight_group_from_airport(country, name, aircraft_type, airport, maintask=None, start_type=StartType.Cold, group_size=1, parking_slots=None)`.
- `StartType` values are `Cold`, `Warm`, `Runway` — there is **no `Hot`**.
  `Warm` = hot ramp (engines running) and is the default we use for player and
  AI flights unless the user explicitly asks for cold start.
- `awacs_flight(country, name, plane_type, airport, position, race_distance=30_000, heading=90, altitude=4500, speed=550, start_type=Cold, frequency=140)`.
- `refuel_flight(...)` adds a `tacanchannel="10X"` kwarg and uses `task.Refueling`.
- `patrol_flight(country, name, patrol_type, airport, pos1, pos2, …, max_engage_distance=60_000, group_size=2)` — **this is what you use for CAP.**
  Builds two-point race-track with `task.EngageTargets(max_engage_distance, [Targets.All.Air])`. There is no `cap_flight`.
- `escort_flight(country, name, escort_type, airport, group_to_escort, …)`.
- `intercept_flight(country, name, patrol_type, airport, zone, late_activation=True, …)`.
  Auto-creates a `TriggerContinious` with `PartOfCoalitionInZone("blue", zone.id)`
  to push an `AITaskPush` when blue enters. **Needs a TriggerZoneCircular** —
  build one via `m.triggers.add_triggerzone(position, radius, hidden=True, name="…")`.
- `sead_flight(country, name, plane_type, target_pos, airport, …)`.
- `strike_flight(country, name, _type, target: Unit, airport, …)` — `target`
  is a single `Unit` (e.g. `vehicle_group.units[0]`), not a group. The IP / Attack /
  Fence-out waypoints and RTB are added automatically when `airport` is given.

### Ground helpers
- `vehicle_group(country, name, _type, position, heading=0, group_size=N, …)` — single type, multiple units.
- `vehicle_group_platoon(country, name, types: List[VehicleType], position, heading=0, …)` — mixed types in one group. **Use this for a convoy.**
- **Formations.** `vehicle_group*` defaults to `VehicleGroup.Formation.Line`
  (perpendicular row, 20 m spacing) — that straight line reads as scripted
  from the air. Pass `formation=VehicleGroup.Formation.<X>` or call
  `group.formation_<x>(heading, …)` post-spawn. Values in
  `dcs.unitgroup.VehicleGroup.Formation`: `Line`, `Vee`, `Rectangle`, `Star`,
  `Scattered`. `formation_scattered(heading=0, max_radius=None)` accepts a
  custom radius; the others take `distance` (spacing). The formation kwarg
  is a no-op for `group_size=1`. Design rules on which to pick live in the
  skill doc.
- Vehicle catalog is namespaced in `dcs.vehicles`:
  - `vehicles.AirDefence.*` — SAMs, AAA, EWR. Note awkward leading-X names:
    `X_1L13_EWR`, `X_55G6_EWR`, `X_2S6_Tunguska`, `X_5p73_s_125_ln`.
    SHORAD examples: `Strela_10M3` (SA-13), `Osa_9A33_ln` (SA-8),
    `Tor_9A331` (SA-15), `Kub_2P25_ln` (SA-6 TEL), `ZSU_23_4_Shilka`.
  - `vehicles.Armor.*` — `T_72B`, `T_72B3`, `T_55`, `BTR_80`, `BTR_D`, …
  - `vehicles.Artillery`, `Infantry`, `Fortification`, `Unarmed`,
    `MissilesSS`, `Locomotive`, `Carriage`.

### Skills, clients, callsigns
- `dcs.unit.Skill` enum: `Average`, `Good`, `High`, `Excellent`, `Random`,
  `Player`, `Client`. **Use `Client` for coop slots**, never `Player` for
  multi-slot missions (the skill doc and pydcs source agree).
- After `flight_group_from_airport(...)`, iterate `group.units` and set every
  unit's `.skill = Skill.Client` to mark the whole flight as human.
- AI flights: set each `unit.skill` to the difficulty-derived value
  (`Skill.High` for trained, `Excellent` for ace boss threats, etc.).
- pydcs auto-assigns callsigns; pass the flight `name` ("Dodge", "Boris", …)
  via the helper kwarg — the displayed callsign comes from the unit's onboard
  numbering, not the group name. For coop player flights, `Dodge` / `Springfield` /
  `Uzi` for USA/NATO faction; `Boris` / `Ivan` for Russian faction. AI in this
  project: `Magic`, `Hawg`, `Eagle`, `Texaco`.

### Weather (`m.weather` is a `Weather` instance)
- `season_temperature` (°C), `qnh` (mmHg, default 760).
- `wind_at_ground` / `wind_at_2000` / `wind_at_8000` — each a `Wind(direction, speed)` with `.direction` (deg) and `.speed` (m/s) attributes.
- `clouds_base` (m), `clouds_thickness` (m), `clouds_density` (0–10).
  Density 0=clear, 3–4=scattered, 5–7=broken, 8–10=overcast.
- `clouds_preset: Optional[CloudPreset]` — set via `CloudPreset.by_name(...)` if you want a named DCS preset.
- `clouds_iprecptns = Weather.Preceptions.None_ | Rain | Thunderstorm`.
- `visibility_distance` (m), `enable_fog`, `fog_thickness`, `fog_visibility`.

### Triggers & briefing
- Trigger rules go on `m.triggerrules.triggers` (a `Rules` collection).
  Classes in `dcs.triggers`: `TriggerOnce`, `TriggerContinious`, `TriggerStart`,
  `TriggerCondition`. Most mission-end logic uses `TriggerOnce`.
- Conditions in `dcs.condition`: `GroupAlive(group_id)`, `GroupDead(group_id)`,
  `GroupLifeLess(id, percent)`, `UnitAlive`, `UnitDead`, `TimeAfter`,
  `TimeBefore`, `FlagEquals`, `AllOfCoalitionInZone`, …
- Actions in `dcs.action`: `MessageToAll(text, seconds, clearview)`,
  `MessageToCoalition(coalitionlist=Coalition.Blue, …)`, plus
  `Coalition` enum (`Coalition.Blue`/`Red`) lives in `dcs.action`, not
  `dcs.coalition` (that's the Mission's instance class).
- Wrap briefing/message text with `m.string("…")` to register it in the
  mission's translation table before passing to a `MessageTo*`.
- Briefing setters: `set_description_text`, `set_description_bluetask_text`,
  `set_description_redtask_text`, `set_sortie_text`. **There is no
  `mission.duration`** — end the sortie via triggers (success/failure
  messages) or just let bingo fuel resolve it.

### Trigger zones
- `m.triggers.add_triggerzone(position: Point, radius: float, hidden=False, name="zone", color=None)` returns a `TriggerZoneCircular`. Pass to `intercept_flight(zone=...)`.

### Saving
- `m.save(str(path))` writes the `.miz`. Caller must `mkdir(parents=True)`
  beforehand — pydcs does not create the parent dir.
- Sanity-check by reloading: `Mission().load_file(path)` returns a list of
  `StatusMessage`; an empty list means clean.
- Console noise on non-Windows: pydcs prints `"Cannot read registry keys on
  non-Windows OS, returning None"` and `"Couldn't detect any installed DCS
  World version"` on import / save. Harmless; don't try to suppress.

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

## Gotchas (pydcs)

- `cap_flight` does not exist. Use `patrol_flight(patrol_type=...)`.
- Airports are dict-indexed: `m.terrain.airports["Batumi"]`. Display names
  ("Sukhumi-Babushara", "Senaki-Kolkhi", …), no method form.
- `Point(x, y)` uses DCS world meters, not lat/lon. Anchor on
  `airport.position` and add offsets.
- `m.country(name)` takes a **string**: `m.country(countries.USA.name)`,
  not `m.country(countries.USA)`.
- `group_size` caps at 4 for fighter flights; AI flights of 5+ desync
  formations in pydcs output.
- `m.save(path)` does not create parents — `path.parent.mkdir(parents=True,
  exist_ok=True)` first.
- `mission.duration` does not exist. End the sortie via triggers (success /
  failure messages on `GroupDead` / `GroupLifeLess` / `TimeAfter`) or let
  bingo fuel resolve it.
- Don't commit `.miz` outputs — binary, large, gitignored.

## Aircraft and target lookups

When the user names an airframe that's not the F-16C default, grep first:
```bash
DCS=.venv/lib/python3.12/site-packages/dcs
grep -nE "^class <name>" "$DCS/planes.py"        # or helicopters.py
grep -nE "^class <name>" "$DCS/vehicles.py"      # AirDefence/Armor namespaces
```
Don't invent attribute names — pydcs ships ~thousands of classes and the
naming is inconsistent (`F_16C_50` but `MiG_29S`, `X_1L13_EWR` but
`Strela_10M3`).

## Existing missions

- [coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py) —
  Caucasus mix: F-16C escort/CAP from Batumi over an A-10C strike on a
  Russian convoy near Senaki, MiG-29S intercept from Sukhumi-Babushara,
  trained difficulty, ~50 min sortie. Generates to `out/coastal_cover.miz`.

## Running

(rely on the `DCS_MISSIONS_FOLDER` env var being available)

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
