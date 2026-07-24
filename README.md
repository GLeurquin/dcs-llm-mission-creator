# DCS Mission Creator

DCS Mission Creator turns a plain-language mission idea into a ready-to-fly [DCS World](https://www.digitalcombatsimulator.com/) `.miz` file with a matching briefing. It is built to be driven by an AI coding assistant — specifically [Claude](https://claude.com/claude-code) via the bundled [`dcs-mission` skill](.claude/skills/dcs-mission/SKILL.md): you describe the scenario you want ("F-16 CAP over a Russian convoy near Senaki, dawn, broken clouds"), and Claude writes a mission generator script against this project's API, then runs it to produce the `.miz`.

Under the hood it is a [pydcs](https://github.com/pydcs/dcs)-based generator, plus a static **map overlay** that gives the generator spatial awareness — roads, rivers, buildings, elevation, slope, and vegetation — so ground units land in tactically plausible places instead of in lakes or on cliffs.

## Getting started

The project uses [uv](https://docs.astral.sh/uv/) for dependency and environment management. If you don't have it yet, install it first (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates a `.venv/` if one doesn't exist, and installs the exact pinned dependencies (pydcs, the map-overlay build stack, the TTS engine, plus the `ruff` / `ty` dev tools). Run it once after cloning and again whenever the lockfile changes. Prefix project commands with `uv run` (e.g. `uv run dcs-mission-creator list`) so they execute inside that environment without a manual `activate`.

### Generating a mission with Claude

The intended workflow is to open this repo in [Claude Code](https://claude.com/claude-code) and ask for a mission in natural language. The `dcs-mission` skill teaches Claude the design conventions and pydcs quirks (see [SKILL.md](.claude/skills/dcs-mission/SKILL.md)); it will author a new module under [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/), then generate the `.miz`. You can still run and iterate on any generator by hand with the CLI below.

## Two halves of the project

```
┌─────────────────────────┐      ┌──────────────────────────────┐
│ Map overlay (per map)   │      │ Mission generator (per .miz) │
│   build once, query     │ ───▶ │   reads overlay              │
│   millions of times     │      │   writes .miz                │
└─────────────────────────┘      └──────────────────────────────┘
   OSM + SRTM + ESA WorldCover         pydcs + TacticalScene
```

The overlay lives under [src/dcs_mission_creator/resources/overlays/&lt;theater&gt;/](src/dcs_mission_creator/resources/overlays/) and is the only artefact that needs rebuilding when a new theater is added. Mission scripts in [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) consume it through the runtime API in [map_overlay/query.py](src/dcs_mission_creator/map_overlay/query.py).

## Generating a mission

**Build the map overlay first.** Every generator reads the per-theater overlay at runtime (elevation, slope, roads, ...), so the Caucasus layers must exist before `generate` will work — see [Building the overlay](#building-the-overlay) below. Skip this only if the layers are already built under `src/dcs_mission_creator/resources/overlays/caucasus/`.

```bash
uv run dcs-mission-creator list                    # show available missions
uv run dcs-mission-creator generate coastal_cover  # writes <slug>.miz + README.md
```

### Where the files land

Each generator writes two files into one output folder: the `<slug>.miz` (load this in DCS) and a `README.md` mission briefing.

- **Default:** `$DCS_MISSIONS_FOLDER/IAGeneratedMissions/<slug>/`. Set the `DCS_MISSIONS_FOLDER` env var to your DCS `Missions` folder so the `.miz` drops straight where DCS can load it; `generate` errors out if the var is unset and no `--output-dir` is given.
- **Override:** `--output-dir DIR` writes both files to `DIR` instead.

```bash
export DCS_MISSIONS_FOLDER="$HOME/Saved Games/DCS/Missions"
uv run dcs-mission-creator generate coastal_cover
#   → $DCS_MISSIONS_FOLDER/IAGeneratedMissions/coastal_cover/coastal_cover.miz
#   → $DCS_MISSIONS_FOLDER/IAGeneratedMissions/coastal_cover/README.md

uv run dcs-mission-creator generate coastal_cover --output-dir out/coastal_cover
```

Grab the `.miz` from that folder and open it from the DCS mission editor or the mission list in-game.

## Supported maps

**Caucasus** and **Syria** are supported: both build a full overlay (elevation, slope, roads, rivers, buildings, vegetation) and can be targeted by `generate`. Caucasus is the primary theater and carries the worked example mission ([coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py)); it also has a hand-tuned clip to the visible map area in [coords.py](src/dcs_mission_creator/map_overlay/coords.py). Syria builds and queries the same way but falls back to the full pydcs terrain bounds (no hand-tuned visible-map clip yet).

The theater registry in [map_overlay/terrains.py](src/dcs_mission_creator/map_overlay/terrains.py) also recognises the other real-world pydcs maps by slug — `persiangulf`, `sinai`, `normandy`, `thechannel`, `falklands` — and each can build against the full-bounds fallback, but none is exercised yet; expect to add a visible-map clip in `coords.py` for clean coverage.

The synthetic training-range maps (`nevada`, `marianaislands`) are recognised by name but out of scope — they have no real-world OSM / SRTM / WorldCover ground truth to build an overlay from.

See [Out of scope (v1)](#out-of-scope-v1) for the full picture.

## Map overlay — what it stores

| Layer            | Format                | Built from                       | Used for                                            |
|------------------|-----------------------|----------------------------------|-----------------------------------------------------|
| `elevation.zarr` | int16 50 m            | SRTM 1-arc-second                | absolute altitude lookups                           |
| `slope.zarr`     | uint8 0–90° 50 m      | derived (Horn) from elevation    | reject cliffs                                       |
| `vegetation.zarr`| uint8 4-class 50 m    | ESA WorldCover 2021 v200 (10 m)  | dense forest / light forest / water / open         |
| `buildings.zarr` | uint8 0–3 density 50 m| OSM `place=*` + `landuse=*`      | reject in-town placement, find urban outskirts     |
| `roads_dt.zarr`  | uint16 cell-distance  | OSM `highway=motorway..tertiary` | "near road?" proximity queries in O(1)             |
| `rivers_dt.zarr` | uint16 cell-distance  | OSM `waterway=*` + water polygons| "near water?" proximity queries in O(1)            |
| `roads.geojson`  | LineString collection | same OSM roads                   | snap convoy waypoints to drivable polylines        |
| `rivers.geojson` | LineString + Polygon  | same OSM water                   | render and reference                                |
| `manifest.json`  | metadata              | builder pipeline                 | per-theater bounds, cell sizes, OSM class filters   |

All `*.zarr` directories are gitignored — they are regenerable from the same public sources. `roads.geojson`, `rivers.geojson`, and `manifest.json` are committed so missions stay reproducible without a rebuild.

Total Caucasus footprint on disk: ~1.6 GB.

## Building the overlay

The build is fully automated and **never touches DCS**. It uses three public data sources:

- **SRTM 1-arc-second** elevation (AWS Open Data)
- **OSM Overpass API** for roads, waterways, settlements, landuse
- **ESA WorldCover 2021 v200** for vegetation classes

A new theater is added by registering it in [map_overlay/terrains.py](src/dcs_mission_creator/map_overlay/terrains.py) and running the three build subcommands below.

```bash
# 1. Elevation (~5 min, downloads ~30 SRTM tiles)
uv run dcs-mission-creator map-overlay build caucasus --layers elevation

# 2. OSM roads / rivers / buildings (~25 min, ~7 GB of Overpass JSON, ~15 min compute)
uv run dcs-mission-creator map-overlay build caucasus --layers osm

# 3. Forest from ESA WorldCover (~15 min, ~1.4 GB of WorldCover tiles)
uv run dcs-mission-creator map-overlay build caucasus --layers forest
```

`--layers all` runs everything in sequence.

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
```

For multi-cell searches use [`MapOverlay.find_placement`](src/dcs_mission_creator/map_overlay/query.py) with a [`Placement`](src/dcs_mission_creator/map_overlay/placement.py) filter, and `MapOverlay.find_road_spawn` for snapping a waypoint to a drivable road.

The `Placement` dataclass collects all the constraint axes (slope, vegetation, road proximity, prominence, line-of-sight, sector arcs, separation between placements, ...) and ships with sugar classmethods:

```python
Placement.on_hilltop(min_prominence_m=50, max_slope_deg=20, line_of_sight_to=(target,))
Placement.in_valley(max_relative_height_m=-20, near_road_m=300)
Placement.near_treeline(within_m=80, light_forest_ok=True, not_in_built_up=True)
```

## Tactical scenes for mission generators

Mission code rarely calls `find_placement` directly — it calls [`TacticalScene`](src/dcs_mission_creator/map_overlay/scene.py) or the archetype shortcuts in [`missions/_placement.py`](src/dcs_mission_creator/missions/_placement.py) instead:

```python
from dcs_mission_creator.missions._placement import (
    load_scene, convoy_spawn, sam_site_on_ridge, ewr_high_ground,
)

scene = load_scene("caucasus")

convoy = convoy_spawn(scene, ao_center, radius_m=10_000)
sa13   = sam_site_on_ridge(scene, defends=convoy, threat_axis_deg=270, envelope_radius_m=8_000)
ewr    = ewr_high_ground(scene, rear_anchor, min_elevation_m=300, min_prominence_m=60)
```

These return `dcs.mapping.Point` instances that the mission builder threads straight into `vehicle_group(..., position=p)`. DCS handles convoy routing between `PointAction.OnRoad` waypoints itself — the overlay's job is to pick *good* waypoints, not to route between them.

A worked example lives in [missions/coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py): a Russian convoy snapped onto a real road north of Senaki, a two-launcher SA-13 placed on a prominence west of the convoy with verified line-of-sight, and a 55G6 EWR on high ground inland from Sukhumi-Babushara.

## Voice lines (TTS)

Mission scripts speak to the player through in-game audio triggers, not just text messages. The [`core/tts/`](src/dcs_mission_creator/core/tts/) package renders briefings, ATC chatter, AWACS calls, and side-task announcements to WAV and wires them into pydcs `SoundTo*` actions.

```python
from dcs_mission_creator.core.tts import VoiceSynth

tts = VoiceSynth()                                       # default: Piper, en_US-danny-low
tts.attach_to_all(m, rule, "Bullseye 270 for 40, bandits hot.")
tts.attach_to_coalition(m, rule, "Magic, picture clear.", coalition="blue")
tts.attach_to_group(m, rule, "Dodge 1, RTB Batumi.", group_id=player.id)
```

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

## Visualising the overlay

```bash
uv run dcs-mission-creator map-overlay inspect caucasus --out /tmp/caucasus_layers.png
```

Emits a 6-panel composite (one panel per zarr layer) downsampled to ~4000 px on the long edge. Use this for sanity-checking new builds and for tuning OSM class filters in `manifest.json`.

## Adding a new mission

1. Create `src/dcs_mission_creator/missions/<slug>.py` defining one `MissionBuilder` subclass with `name`, `title`, `build_miz`, and `readme`.
2. Add a `main()` at the bottom so `python -m dcs_mission_creator.missions.<slug>` works.
3. The unified CLI auto-discovers the module — `uv run dcs-mission-creator list` will show the new mission immediately, and `generate <slug>` will run it.

See [.claude/skills/dcs-mission/SKILL.md](.claude/skills/dcs-mission/SKILL.md) for the design playbook (faction naming, weather conventions, pydcs API quirks, lint and type-check commands).

## Out of scope (v1)

- Theaters beyond Caucasus and Syria (pipeline is per-map configurable; Persian Gulf / Sinai / others work off the full-bounds fallback, cleaner once a visible-map clip is added to `coords.py`).
- Synthetic theaters (Nevada, Marianas combat range) — no real-world OSM / SRTM / WorldCover ground truth.
- Live in-game F10 screenshot capture — the public-data overlay reaches ~90 % alignment with DCS's own forest/road rendering, and the 100 m placement buffers absorb the remainder.
- Full A\* convoy routing — DCS routes between `OnRoad` waypoints on its own.
