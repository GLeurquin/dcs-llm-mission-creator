# DCS Mission Creator

DCS Mission Creator turns a plain-language mission idea into a ready-to-fly [DCS World](https://www.digitalcombatsimulator.com/) `.miz` file with a matching briefing, kneeboard cards, and a data cartridge. It is built to be driven by an AI coding assistant — specifically [Claude](https://claude.com/claude-code) via the bundled [`dcs-mission` skill](.claude/skills/dcs-mission/SKILL.md): you describe the scenario you want ("F-16 CAP over a Russian convoy near Senaki, dawn, broken clouds"), and Claude writes a mission generator script against this project's API, then runs it to produce the `.miz`.

Under the hood it is a [pydcs](https://github.com/pydcs/dcs)-based generator, plus two things pydcs has no notion of:

- a static **map overlay** that gives the generator spatial awareness — roads, rivers, buildings, elevation, slope, vegetation, settlements — so ground units land in tactically plausible places instead of in lakes or on cliffs;
- a **mission toolkit** ([`core/`](src/dcs_mission_creator/core/)) that carries the things the mission format supports but pydcs does not write: an integrated air-defence net that goes dark under HARM fire, threat-aware AI routing, F10 plan drawing scaled to difficulty, HSD threat rings, kneeboard cards, spoken radio calls, and a recon still on the briefing screen.

## Contents

- [Quick start](#quick-start) — zero to a `.miz` in four steps
- [Generating missions with Claude](#generating-missions-with-claude) — the intended workflow
- [What a generated mission ships](#what-a-generated-mission-ships)
- [Bundled mission examples](#bundled-mission-examples)
- [Supported maps](#supported-maps)
- [How it works](#how-it-works) — architecture
- [Reference: the mission toolkit](#reference-the-mission-toolkit) — what `core/` adds on top of pydcs
- [Reference: the map overlay](#reference-the-map-overlay) — storage, building, querying
- [Voice lines (TTS)](#voice-lines-tts)
- [Adding a new mission](#adding-a-new-mission)
- [Development](#development) — lint, type-check, tests
- [Out of scope (v1)](#out-of-scope-v1)

## Quick start

From a fresh clone to a loadable `.miz`:

### 1. Install `uv` and sync dependencies

The project uses [uv](https://docs.astral.sh/uv/) for dependency and environment management. If you don't have it yet, install it first, then sync:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if uv is already installed
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates a `.venv/` if one doesn't exist, and installs the exact pinned dependencies (pydcs, the map-overlay build stack, the TTS engine, plus the `ruff` / `ty` / `pytest` dev tools). Run it once after cloning and again whenever the lockfile changes. Prefix project commands with `uv run` (e.g. `uv run dcs-mission-creator list`) so they execute inside that environment without a manual `activate`.

### 2. Build the map overlay (one-time, per theater)

Every generator reads a per-theater **map overlay** at runtime (elevation, slope, roads, ...), so the layers must exist before `generate` will work. The overlay is gitignored, so a fresh clone has to build it once. For Caucasus:

```bash
uv run dcs-mission-creator map-overlay build caucasus --layers all
```

This downloads public elevation / OSM / land-cover data and takes ~45 min the first time (it caches and crash-resumes — see [Building the overlay](#building-the-overlay)). You only redo this per theater, not per mission.

### 3. Generate a mission

```bash
uv run dcs-mission-creator list                    # show available missions
uv run dcs-mission-creator generate coastal_cover  # writes <slug>.miz + README.md
uv run dcs-mission-creator generate                # no slug → every mission
```

Each generator writes the `.miz`, the markdown briefing and the kneeboard cards into one output folder — see [What a generated mission ships](#what-a-generated-mission-ships).

- **Default output:** `$DCS_MISSIONS_FOLDER/IAGeneratedMissions/<slug>/`. Set the `DCS_MISSIONS_FOLDER` env var to your DCS `Missions` folder so the `.miz` drops straight where DCS can load it; `generate` errors out if the var is unset and no `--output-dir` is given.
- **Override:** `--output-dir DIR` writes everything to `DIR` instead.
- **All at once:** omitting the mission slug generates every discovered mission, each into its own `<slug>/` folder; with `--output-dir DIR` that folder becomes `DIR/<slug>/`. A mission that fails is logged and the rest still run (exit code 1 at the end).
- **Loadouts:** set `DCS_INSTALL_DIR` to your DCS World folder. pydcs reads stock payloads from the installed game and otherwise finds it only via the Windows registry, so off Windows (WSL included) an unset var means the DCS task defaults come back empty. The same install is where the kneeboard's navaid frequencies and the theater's shipped charts are read from. Windows spellings are accepted and mapped to `/mnt/<drive>` under WSL.

```bash
export DCS_MISSIONS_FOLDER="$HOME/Saved Games/DCS/Missions"
export DCS_INSTALL_DIR="/mnt/e/Games/DCS World OpenBeta"   # WSL path to the game
uv run dcs-mission-creator generate coastal_cover
#   → $DCS_MISSIONS_FOLDER/IAGeneratedMissions/coastal_cover/coastal_cover.miz
#   → $DCS_MISSIONS_FOLDER/IAGeneratedMissions/coastal_cover/README.md
#   → .../coastal_cover/kneeboard/*.png

uv run dcs-mission-creator generate coastal_cover --output-dir out/coastal_cover
```

Add `--players N` (1–4) to scale the player flight into a coop mission.

### 4. Fly it

Grab the `<slug>.miz` from the output folder and open it from the DCS mission editor or the mission list in-game. Read the generated `README.md` for the full briefing.

## Generating missions with Claude

The intended workflow is to open this repo in [Claude Code](https://claude.com/claude-code) and ask for a mission in natural language. Three documents teach it the job:

| Doc | Holds |
|-----|-------|
| [.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md) | design intent — what package to build, difficulty policy, pacing, briefing / voice / F10 conventions |
| [.claude/skills/dcs-mission/PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md) | the pydcs API as verified against the installed source, and every gotcha in it |
| [CLAUDE.md](CLAUDE.md) | this project's conventions — package layout, the `MissionBuilder` contract, every `core/` helper, lint and type-check |

Claude authors a new module under [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/), then generates the `.miz` for you. The [bundled examples](#bundled-mission-examples) below were all produced this way — read them as templates. You can still run and iterate on any generator by hand with the CLI from the [Quick start](#quick-start).

## What a generated mission ships

`generate <slug>` writes one folder, and the `.miz` inside it carries more than a set of units:

| Artefact | Where | What it is |
|----------|-------|------------|
| `<slug>.miz` | output folder | the mission — load this in DCS |
| `README.md` | output folder | the full markdown briefing (situation, package, intelligence, ROE, navigation, win/loss) |
| `kneeboard/*.png` | output folder | the kneeboard cards, also appended inside the `.miz`, so they can be read outside the game |
| recon still `.png` | output folder | only where the mission publishes one; the same image is the briefing-screen slide and is embedded in the README |
| flight plan / comms cards | inside the `.miz` | route as numbers, package frequencies, ATC bands, navaids — all derived from the built mission |
| airfield card | inside the `.miz` | only for a field the theatre ships no chart of |
| `DTC/*.dtc` | inside the `.miz` | F-16C data cartridge — the briefed SAM rings, pre-loaded on the HSD |
| voice lines | inside the `.miz` | every radio call rendered to WAV, matching the on-screen text word for word |
| F10 plan drawings | inside the `.miz` | the sortie drawn on the map, revealing exactly as much of the enemy as the difficulty allows |

## Bundled mission examples

Worked missions ship under [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) — read them as templates for what a generator does with the overlay, flight packages, threats, triggers, and TTS. All are F-16C-50 sorties built for 2–6 coop slots (`--players`), with the flight's loadout split across them; every ground placement comes from the `map_overlay` tactical-scene helpers, not hand-tuned offsets. Each module's own docstring is that mission's full brief — this table is only the index.

| Slug | Theater | Difficulty | Sortie |
|------|---------|-----------|--------|
| [`coastal_cover`](src/dcs_mission_creator/missions/coastal_cover.py) | Caucasus | trained | CAP + escort of an A-10C `Hawg` strike on a Russian armoured convoy north of Senaki; handle a 2-ship MiG-29S intercept from Sukhumi-Babushara. AWACS only, no tanker. Carries a recon still of the column on the valley road. |
| [`kodori_strike`](src/dcs_mission_creator/missions/kodori_strike.py) | Caucasus | trained | Lead a mixed package (`Weasel` SEAD, `Eagle` F-15C CAP, `Magic` AWACS, `Texaco` tanker) onto a Russian FOB astride the coast road at the Kodori delta; SA-6 rollback inland, Su-27 CAP launches on intrusion. |
| [`idlib_gauntlet`](src/dcs_mission_creator/missions/idlib_gauntlet.py) | Syria | trained | Interdict a Syrian resupply column out of Hatay through three overlapping SAM belts that sit dark, cue off the EWR chain and go quiet under HARM fire. A 90 km front line prices every flank and funnels the ingress into the SA-6's sector; a JTAC lases the column and reads coordinates in your own cockpit's format. The reference mission for SEAD, front lines, and recon stills. |
| [`eastern_shield`](src/dcs_mission_creator/missions/eastern_shield.py) | Syria | trained | SEAD an SA-6 defending the Kuweires depot, escort A-10C `Hawg` onto it, then a MiG-29S scramble + armoured reserve push. Full support: `Magic` AWACS, `Texaco` tanker, `Eagle` F-15C TARCAP. |
| [`ansariyah_works`](src/dcs_mission_creator/missions/ansariyah_works.py) | Syria | veteran | Deep strike out of Akrotiri on a Syrian rocket-motor plant behind the Jebel al-Ansariyah — 279 km east, 250 km of it water, flown at fifty metres under an S-200 that reaches most of the way to Cyprus and cannot shoot below three hundred. Three buildings, two bombs, one briefed choice. |
| [`abkhaz_sweep`](src/dcs_mission_creator/missions/abkhaz_sweep.py) | Caucasus | ace | Air-superiority sweep off the Abkhaz coast vs. Su-27 + MiG-29S aggressors from Sochi-Adler / Gudauta, under an SA-6 that forces a high fight. No support. |
| [`daryal_run`](src/dcs_mission_creator/missions/daryal_run.py) | Caucasus | ace | SEAD strike on an S-300PS (SA-10) south of Beslan; low-level ingress up the Daryal Gorge, terrain-masked HARM pop-up. AWACS only, no escort/tanker. |
| [`kuban_forge`](src/dcs_mission_creator/missions/kuban_forge.py) | Caucasus | ace | Strike out of Senaki on a motor works on the Kuban, flown up the Abkhaz plain and the Kodori and over the Klukhori Pass. No anti-radiation weapon: the terrain is the SEAD, and the corridor is masked from the Buk until 34 km short. Low in, high out. |

Difficulty is a reveal policy as much as a threat count: a `trained` mission draws its threat rings about 2 km off truth, `veteran` a quarter wider and 4 km off, `ace` wider again and 6 km off. Every mission loads its briefed rings into the F-16C's data cartridge and prints them on the kneeboard, so what changes with difficulty is how far the drawn ring sits from the launchers — never whether the player gets one.

## Supported maps

**Caucasus** and **Syria** are supported: both build a full overlay (elevation, slope, roads, rivers, buildings, vegetation, settlements) and can be targeted by `generate`. Caucasus is the primary theater and carries most of the example missions; it also has a hand-tuned clip to the visible map area in [coords.py](src/dcs_mission_creator/map_overlay/coords.py). Syria builds and queries the same way but falls back to the full pydcs terrain bounds (no hand-tuned visible-map clip yet).

The theater registry in [map_overlay/terrains.py](src/dcs_mission_creator/map_overlay/terrains.py) also recognises the other real-world pydcs maps by slug — `persiangulf`, `sinai`, `normandy`, `thechannel`, `falklands` — and each can build against the full-bounds fallback, but none is exercised yet; expect to add a visible-map clip in `coords.py` for clean coverage.

The synthetic training-range maps (`nevada`, `marianaislands`) are recognised by name but out of scope — they have no real-world OSM / SRTM / WorldCover ground truth to build an overlay from.

## How it works

```
┌─────────────────────────┐     ┌──────────────────────────────┐     ┌──────────────────────────────┐
│ Map overlay (per map)   │     │ Mission generator (per .miz) │     │ Finishing steps (base class) │
│   build once, query     │ ──▶ │   _assemble() builds units,  │ ──▶ │   waypoint snap, tail        │
│   millions of times     │     │   flights, triggers, the F10 │     │   numbers, datalink, save,   │
│                         │     │   plan and the briefing      │     │   then cartridge + kneeboard │
└─────────────────────────┘     └──────────────────────────────┘     └──────────────────────────────┘
   OSM + SRTM + WorldCover         pydcs + TacticalScene + core/        files that live inside the .miz
```

The overlay lives under [src/dcs_mission_creator/resources/overlays/&lt;theater&gt;/](src/dcs_mission_creator/resources/overlays/) and is the only artefact that needs rebuilding when a new theater is added. Mission scripts in [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) consume it through the runtime API in [map_overlay/query.py](src/dcs_mission_creator/map_overlay/query.py).

A mission module is deliberately thin: it defines `name` / `title` / `difficulty`, an `_assemble(m)` that builds the scenario, and a `readme()`. [`MissionBuilder`](src/dcs_mission_creator/core/mission_builder.py) owns everything a mission must not be able to forget — snapping take-off and landing waypoints onto the terrain (pydcs hard-codes them to zero, which spawns jets underground), fixing pydcs's 108 kt departure speed, assigning datalink identities, seeding the RNG, and then, *after* the save, appending the files that live inside the archive: the data cartridge and the kneeboard cards.

---

# Reference: the mission toolkit

[`core/`](src/dcs_mission_creator/core/) is the half of the project that is not spatial. Each module holds one thing the mission format supports and pydcs does not write, or one piece of project policy that would otherwise be copy-pasted into every mission. Full contracts, gotchas and the reasoning behind each are in [CLAUDE.md](CLAUDE.md).

| Module | What it adds |
|--------|--------------|
| [`mission_builder.py`](src/dcs_mission_creator/core/mission_builder.py) | the base class and the finishing steps above; reproducible builds |
| [`iads.py`](src/dcs_mission_creator/core/iads.py) | an integrated air-defence net (vendored [Skynet-IADS](https://github.com/walder/Skynet-IADS) plus a first-party MIST shim): batteries start dark, cue off the early-warning chain, emit in disciplined looks sized by crew skill, go quiet on a HARM launch *they could observe*, and shoot-and-scoot off a stale aimpoint |
| [`air_defense.py`](src/dcs_mission_creator/core/air_defense.py) | whole SAM sites (search radar + fire control + CP + launchers) from one call, laid out dispersed and jittered rather than in a 60 m clump |
| [`frontline.py`](src/dcs_mission_creator/core/frontline.py) | the geometry of a front — shoulders, sectors, and the seam that makes one bearing the way in |
| [`routing.py`](src/dcs_mission_creator/core/routing.py) | AI routes bent *around* the SAM rings the player is briefed on, plus standoff IPs |
| [`tasking.py`](src/dcs_mission_creator/core/tasking.py) | the AI verbs that carry policy: enemy ROE by difficulty, friendly threat reaction, JTAC lasing, scramble-on-trigger |
| [`map_draw.py`](src/dcs_mission_creator/core/map_draw.py) + [`visibility.py`](src/dcs_mission_creator/core/visibility.py) | the F10 plan, revealed as far as the difficulty allows — and every enemy group hidden from the map, planner and datalink so the plan and briefing are the only intel |
| [`dtc.py`](src/dcs_mission_creator/core/dtc.py) | the F-16C data cartridge: the rings the plan drew, pre-loaded on the HSD |
| [`kneeboard/`](src/dcs_mission_creator/core/kneeboard/) | flight-plan, comms and (where the theatre ships no chart) airfield cards, all derived from the built mission; navaids read out of the installed game |
| [`recon/`](src/dcs_mission_creator/core/recon/) | a wide-area radar still of the target, rendered from the overlay rasters, on the briefing screen and in the README |
| [`jtac.py`](src/dcs_mission_creator/core/jtac.py) | a JTAC that reads coordinates in the *requesting* cockpit's format, instead of DCS's one-size-fits-all 4-digit grid |
| [`datalink.py`](src/dcs_mission_creator/core/datalink.py) | track numbers, voice callsigns and per-unit network tables, so a coop flight sees itself |
| [`waypoints.py`](src/dcs_mission_creator/core/waypoints.py) | terrain elevation under the waypoints that need it, and a flyable departure speed |
| [`tts/`](src/dcs_mission_creator/core/tts/) | every radio call spoken as well as printed — see [Voice lines](#voice-lines-tts) |
| [`placement.py`](src/dcs_mission_creator/core/placement.py) | archetype placements (convoy spawn, SAM on a ridge, EWR on high ground) over the overlay |
| [`mission_kit.py`](src/dcs_mission_creator/core/mission_kit.py), [`weather.py`](src/dcs_mission_creator/core/weather.py), [`triggers.py`](src/dcs_mission_creator/core/triggers.py), [`lua/`](src/dcs_mission_creator/core/lua/), [`cli.py`](src/dcs_mission_creator/core/cli.py) | the scaffolding: loadouts and offsets, weather as a record, voice-plus-text radio calls, mission Lua as real files, and the per-module `main()` |

---

# Reference: the map overlay

Everything below is internals — you do **not** need it to generate a mission (the [Quick start](#quick-start) covers that). Read on if you're building an overlay for a new theater, debugging placement, or extending the pipeline.

## What it stores

| Layer            | Format                | Built from                       | Used for                                            |
|------------------|-----------------------|----------------------------------|-----------------------------------------------------|
| `elevation.zarr` | int16 50 m            | SRTM 1-arc-second                | absolute altitude lookups, line of sight, waypoint elevations |
| `slope.zarr`     | uint8 0–90° 50 m      | derived (Horn) from elevation    | reject cliffs                                       |
| `vegetation.zarr`| uint8 4-class 50 m    | ESA WorldCover 2021 v200 (10 m)  | dense forest / light forest / water / open          |
| `buildings.zarr` | uint8 0–3 density 50 m| OSM `place=*` + `landuse=*`      | reject in-town placement, find urban outskirts      |
| `roads_dt.zarr`  | uint16 cell-distance  | OSM `highway=motorway/trunk/primary` | "near road?" proximity queries in O(1)          |
| `rivers_dt.zarr` | uint16 cell-distance  | OSM `waterway=river` + water polygons | "near water?" proximity queries in O(1)        |
| `roads.geojson`  | LineString collection | same OSM roads                   | snap convoy waypoints to drivable polylines         |
| `rivers.geojson` | LineString + Polygon  | same OSM water                   | render and reference                                |
| `places.geojson` | Point collection      | OSM `place=*` (the classes `buildings.zarr` was rasterized from) | settlement labels on recon stills |
| `manifest.json`  | metadata              | builder pipeline                 | per-theater bounds, cell sizes, OSM class filters   |

Only the *major* road network is kept (`motorway`, `trunk`, `primary`) and only rivers over 5 km with water polygons over 10,000 m² — which is a real constraint on placement, not a detail: in Abkhazia the only major road is the coastal highway, so a `near_road_m` filter is unsatisfiable in the mountains. `kodori_strike`'s `_setup_airports` is the worked example of a placement whose constraints could not be met where the briefing pointed.

The whole overlay directory is gitignored — every layer is regenerable from the same public sources, and none of it is in the wheel. Reproducibility comes from the sampling seed carried on `MapOverlay` instead, so two builds against the same layers place units identically.

Measured footprint on disk: ~540 MB for the clipped Caucasus overlay, ~650 MB for Syria (the unclipped full-bounds Caucasus build is ~1.8 GB).

## Building the overlay

The build is fully automated and **never touches DCS**. It uses three public data sources:

- **SRTM 1-arc-second** elevation (AWS Open Data)
- **OSM Overpass API** for roads, waterways, settlements, landuse
- **ESA WorldCover 2021 v200** for vegetation classes

A new theater is added by registering it in [map_overlay/terrains.py](src/dcs_mission_creator/map_overlay/terrains.py) and running the build subcommands below.

```bash
# 1. Elevation (~5 min, downloads ~30 SRTM tiles)
uv run dcs-mission-creator map-overlay build caucasus --layers elevation

# 2. OSM roads / rivers / buildings (~25 min, ~7 GB of Overpass JSON, ~15 min compute)
uv run dcs-mission-creator map-overlay build caucasus --layers osm

# 3. Forest from ESA WorldCover (~15 min, ~1.4 GB of WorldCover tiles)
uv run dcs-mission-creator map-overlay build caucasus --layers forest

# 4. Settlement sidecar for recon-still labels (fast, ~100 KB per tile)
uv run dcs-mission-creator map-overlay build caucasus --layers places
```

`--layers all` runs all four in sequence. The `places` step is deliberately independent of the rasters — it is one `node["place"]` Overpass query per tile, not a re-parse of the 19 GB of tiles the OSM step reads — so it can be added to an overlay that is already built. An overlay without it still loads: `MapOverlay.places` returns `[]` with one warning, and a recon still comes out with no labels rather than failing the build.

### Crash-resume

Each builder writes its in-progress state to a `_progress/` directory under [src/dcs_mission_creator/resources/_build_cache/&lt;theater&gt;/](src/dcs_mission_creator/resources/_build_cache/) so a kernel OOM, Ctrl-C, or network failure does not waste the work already done. Re-running the same command picks up at the last completed tile / pipeline stage.

| Builder            | Checkpointed state                                                                  |
|--------------------|-------------------------------------------------------------------------------------|
| `builder_osm.py`   | memmap masks (roads/rivers/buildings) + processed.jsonl + seen_ways.jsonl + per-layer sidecar JSONL + stage.txt |
| `builder_forest.py`| memmap dst raster + processed.jsonl + stage.txt                                     |
| `builder_elevation.py`| per-tile SRTM `.hgt` cache + dst raster `.npy` cache                              |

The `_progress/` directory is removed automatically on a clean success.

### Memory profile

Each builder is sized to fit comfortably below ~8 GB resident on a 15 GB VM:

- OSM absorb stage streams one Overpass tile at a time (peak ~2 GB: three full-map uint8 masks + the current tile's parsed JSON).
- OSM distance-transform stage uses **chunked EDT** in 2000-row vertical strips with a 1500-row halo. Peak per-strip float64 scratch is ~1.4 GB instead of the 5.3 GB a whole-map EDT would need.
- Forest reproject loops one ESA tile at a time into a memmap-backed destination raster.
- Elevation reproject is the same per-tile streaming pattern.

The Overpass API tile JSONs and SRTM `.hgt` tiles cache to `_build_cache/<theater>/`, so re-running a build after a config change skips the network entirely.

### Visualising a build

```bash
uv run dcs-mission-creator map-overlay inspect caucasus --out /tmp/caucasus_layers.png
```

Emits a 6-panel composite (one panel per zarr layer) downsampled to ~4000 px on the long edge. Use this for sanity-checking new builds and for tuning OSM class filters in `manifest.json`.

**Caucasus:**

![Caucasus overlay layers](images/caucasus_layers.png)

**Syria:**

![Syria overlay layers](images/syria_layers.png)

A single point can also be interrogated straight from the CLI, which is the fastest way to find out why a placement filter is unsatisfiable:

```bash
uv run dcs-mission-creator map-overlay query caucasus --point -291014,617414 --layer road_distance
```

## Querying the overlay at runtime

Missions open the overlay once and query it with point or window APIs:

```python
from dcs_mission_creator.map_overlay.query import MapOverlay

ov = MapOverlay.load("caucasus")            # mmap-backed; no I/O yet

ov.elevation_at(point)                      # int meters AMSL
ov.slope_at(point)                          # degrees 0–90
ov.vegetation_at(point)                     # Vegetation enum
ov.distance_to_road_m(point)                # meters, O(1) lookup
ov.is_built_up(point)                       # bool
ov.line_of_sight(a, b, eye_a_m=2, eye_b_m=2)# Bresenham over elevation
ov.local_prominence_m(point, radius_m=2000) # cell elev − local mean
ov.places(center, radius_m=12_000)          # settlements, for recon-still labels
```

For multi-cell searches use [`MapOverlay.find_placement`](src/dcs_mission_creator/map_overlay/query.py) with a [`Placement`](src/dcs_mission_creator/map_overlay/placement.py) filter, and `MapOverlay.find_road_spawn` for snapping a waypoint to a drivable road.

The `Placement` dataclass collects all the constraint axes (slope, vegetation, road proximity, prominence, line-of-sight, sector arcs, separation between placements, ...) and ships with sugar classmethods:

```python
Placement.on_hilltop(min_prominence_m=50, max_slope_deg=20, line_of_sight_to=(target,))
Placement.in_valley(max_relative_height_m=-20, near_road_m=300)
Placement.near_treeline(within_m=80, light_forest_ok=True, not_in_built_up=True)
Placement.coastal(within_m=500)
Placement.urban_outskirts(within_m=300)
```

## Tactical scenes for mission generators

Mission code rarely calls `find_placement` directly — it calls [`TacticalScene`](src/dcs_mission_creator/map_overlay/scene.py) or the archetype shortcuts in [`core/placement.py`](src/dcs_mission_creator/core/placement.py) instead:

```python
from dcs_mission_creator.core.placement import (
    load_scene, convoy_spawn, sam_site_on_ridge, ewr_high_ground,
)

scene = load_scene("caucasus")

convoy = convoy_spawn(scene, ao_center, radius_m=10_000)
sa13   = sam_site_on_ridge(scene, defends=convoy, threat_axis_deg=270, envelope_radius_m=8_000)
ewr    = ewr_high_ground(scene, rear_anchor, min_elevation_m=300, min_prominence_m=60)
```

`TacticalScene` itself carries the composite scenes a package is built around — convoy routes and ambushes on them, SAM belts defending a point, EWR chains, carrier groups and surface action groups, FARPs and CSAR sites, artillery firebases, bridge chokepoints, airfield ring defences, front lines, and the friendly geometry (tanker and AWACS tracks, CAP stations, ingress corridors).

All of these return `dcs.mapping.Point` instances that the mission builder threads straight into `vehicle_group(..., position=p)`. DCS handles convoy routing between `PointAction.OnRoad` waypoints itself — the overlay's job is to pick *good* waypoints, not to route between them.

A worked example lives in [missions/coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py): a Russian convoy snapped onto a real road north of Senaki, a two-launcher SA-13 placed on a prominence west of the convoy with verified line-of-sight, and a 55G6 EWR on high ground inland from Sukhumi-Babushara.

# Voice lines (TTS)

Mission scripts speak to the player through in-game audio triggers, not just text messages. The [`core/tts/`](src/dcs_mission_creator/core/tts/) package renders briefings, ATC chatter, AWACS calls, and side-task announcements to WAV and wires them into pydcs `SoundTo*` actions.

```python
from dcs_mission_creator.core.tts import VoiceSynth

tts = VoiceSynth()                                       # default: Piper, en_US-danny-low
tts.attach_to_all(m, rule, "Bullseye 270 for 40, bandits hot.")
tts.attach_to_coalition(m, rule, "Magic, picture clear.", coalition="blue")
tts.attach_to_group(m, rule, "Dodge 1, RTB Batumi.", group_id=player.id)
```

In practice a mission goes through [`core/triggers.py`](src/dcs_mission_creator/core/triggers.py) rather than calling both halves by hand: those helpers take one `text` and use it for the on-screen message *and* the TTS render, so the spoken and printed call cannot drift apart. Audio played from mission Lua instead of a trigger action uses `VoiceSynth.register`, which returns the in-`.miz` file name `trigger.action.outSound*` expects.

Three components:

| Piece                                                                  | Role                                                                                   |
|------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| [`VoiceBackend`](src/dcs_mission_creator/core/tts/backend.py)          | Protocol — two methods (`fingerprint()`, `render_to_file()`). Plug in any engine.      |
| [`PiperBackend`](src/dcs_mission_creator/core/tts/piper.py)            | Default. Wraps [piper1-gpl](https://github.com/OHF-voice/piper1-gpl) (neural ONNX, CPU, sub-realtime). Voices auto-download from HuggingFace into `cache/voice/models/`. |
| [`VoiceSynth`](src/dcs_mission_creator/core/tts/synth.py)              | Backend-agnostic facade. Hashes `backend.fingerprint() + text` into a cache key, renders on miss, registers the WAV via `mission.map_resource.add_resource_file`, appends a `SoundToAll` / `SoundToCoalition` / `SoundToGroup` action to the trigger rule. |

**Render cache.** WAVs live at `cache/voice/<sha256[:16]>.wav`. The fingerprint includes voice name + length/noise scales, so changing voice or rate invalidates entries without colliding. Re-running a generator after first build is offline and instant.

**Swapping engines.** Implement `VoiceBackend` for Coqui, Kokoro, ElevenLabs, Azure, etc., then `VoiceSynth(backend=MyBackend(...))`. Default voice override:

```python
from dcs_mission_creator.core.tts import PiperBackend, VoiceSynth

tts = VoiceSynth(backend=PiperBackend(voice="en_GB-alan-medium", length_scale=1.05))
```

# Adding a new mission

1. Create `src/dcs_mission_creator/missions/<slug>.py` defining **one** concrete `MissionBuilder` subclass with:
   - `name` (the filesystem slug, matching the filename), `title` (display name), and `difficulty` (the `Difficulty` enum — it drives both the F10 reveal and the enemy ROE);
   - `_assemble(self, m: Mission) -> MapOverlay` — build the whole mission into `m` and return the overlay its positions came from, so the base class can snap waypoints onto the terrain;
   - `readme(self) -> str` — the markdown briefing.
2. Add a `main()` at the bottom (`run_cli(TheBuilder)` from [core/cli.py](src/dcs_mission_creator/core/cli.py)) so `python -m dcs_mission_creator.missions.<slug>` works.
3. The unified CLI auto-discovers the module — `uv run dcs-mission-creator list` will show the new mission immediately, and `generate <slug>` will run it.

Keep `_assemble` an orchestrator: one small named method per block of the mission (one flight, one ground cluster, one trigger group), each with a docstring stating the design intent. [coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py) is the shortest example of the shape; [idlib_gauntlet.py](src/dcs_mission_creator/missions/idlib_gauntlet.py) the fullest.

See [CLAUDE.md](CLAUDE.md) for the project conventions (the `MissionBuilder` contract, every `core/` helper, briefing style, faction naming) and [.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md) for the design playbook.

# Development

```bash
uv run ruff check src/ tests/          # lint
uv run ruff format --check src/ tests/ # formatting check (use `format` to apply)
uv run ty check src/                   # static type check (astral ty)
uv run pytest -m "not slow"            # what CI and pre-commit run
uv run pytest                          # adds the overlay-dependent smoke test
```

The test suite runs **without a DCS installation and without the built overlay**, because CI has neither; anything that needs the overlay is marked `slow` and skips itself when it is absent. Two suites are worth knowing about: [tests/test_iads.py](tests/test_iads.py) asserts every Skynet symbol the generated Lua touches exists in the pinned build, and [tests/test_iads_runtime.py](tests/test_iads_runtime.py) *runs* the whole IADS stack under an embedded Lua against a stub of the DCS scripting environment (skipped when the optional `lupa` dev dependency is missing).

The same hooks are wired for [`prek`](https://github.com/j178/prek), a Rust drop-in for `pre-commit`:

```bash
uv tool install prek   # once
prek install           # wire .git/hooks/pre-commit
prek run --all-files   # manual full run
```

# Out of scope (v1)

- Theaters beyond Caucasus and Syria (pipeline is per-map configurable; Persian Gulf / Sinai / others work off the full-bounds fallback, cleaner once a visible-map clip is added to `coords.py`).
- Synthetic theaters (Nevada, Marianas combat range) — no real-world OSM / SRTM / WorldCover ground truth.
- Live in-game F10 screenshot capture — the public-data overlay reaches ~90 % alignment with DCS's own forest/road rendering, and the 100 m placement buffers absorb the remainder.
- Full A\* convoy routing — DCS routes between `OnRoad` waypoints on its own.
