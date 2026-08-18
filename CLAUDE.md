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
    flight's take-off/landing waypoints to field elevation, assigns datalink
    identities, makes the output directory and saves. Both finishing steps have
    to happen after the last flight exists and before the save — pydcs
    hard-codes take-off/landing altitudes to zero, so a mission that skipped
    the snap shipped a jet spawned underground, and it writes no datalink
    identity at all, so a coop flight came up anonymous and blind to itself.
    They are in the base precisely so they cannot be forgotten. One step runs
    *after* the save for the mirror-image reason: any data cartridge a mission
    armed (`core/dtc.py`) is a file inside the `.miz`, and `Mission.save` writes
    a fixed set of zip entries with no hook for another one.
  - `_permit_crash_recovery` forces `permitCrash` on (the ME's "PERMIT CRASH
    RCVR", `m.forced_options`) for every mission, so a player who crashes
    lands back on the slot-selection screen and can take another jet instead
    of dropping straight to the debriefing. Nothing else is forced — the rest
    of the gameplay options stay whatever the player set.
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
plan.mobile_threat(convoy_pos, "Convoy SHORAD", icon=StandardIcon.Mechanized)
plan.threat_area(center, 28_000.0, "SA-6 + bandit CAP — vicinity")
plan.frontline(front.trace, "FRONT LINE — guns and MANPADS below 10,000")
```

Pass **absolute** world `Point`s — `PlanOverlay` does the layer selection,
colour choice (enemy red / friendly cyan / objective amber), the
anchor-relative offset math the point-list drawings need, and the difficulty
policy. `.threat()` is the difficulty dial: full icon + true ring on
`recruit`, coarse + offset + "(est.)" on `trained`, **no-op** on
`veteran`/`ace` (use `.threat_area()` for a vague zone there). `.objective()`
tightens/loosens the same way. Friendly-plan calls (`route`, `orbit`,
`waypoint_label`) always draw precisely. `.frontline()` is the one *enemy* call
that also draws precisely at every difficulty — a front line is ground both
armies have held for weeks, and the briefing's "cross at the seam" needs
something on the map to point at; the air defence sitting on it still goes
through `.threat()` / `.mobile_threat()` like anything else. Design rules (what
to draw, reveal per label, and that a site may be left off the map on purpose)
live in the `dcs-mission` skill; the underlying pydcs drawing API lives in
PYDCS_REFERENCE.md.

`.threat()` also **returns** the `(center, radius)` it drew — the coarsened
estimate on `trained`, `None` where the difficulty withholds the site. Feed that
to `core/dtc.py` (via `dtc.briefed`) rather than re-deriving it: the offset
bearing is a fresh random draw per call, so a second guess at "the trained
estimate" lands somewhere else and the cockpit would contradict the map.

**A ring is only for something emplaced.** `.threat()` claims the envelope *is
there*; air defence that drives — a convoy's organic SHORAD, a road-marching
launcher, a late-activated reserve — has left any ring drawn at its start
point, and the ring then reads as "clear" everywhere it no longer covers. Those
get `.mobile_threat(center, label, icon=…)`: same difficulty policy, icon and
label only, no circle, and **no return value**, so a moving system cannot end
up frozen into a pre-planned cartridge point. Ground truth for the call: a group
with waypoints is mobile.

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

**Sites are laid out dispersed, and that is two families of distance rather
than one number.** A prepared, fixed site is genuinely compact — an S-75 or
S-125 fires from built revetments 60–100 m from its fire-control radar, and
spreading those would be less realistic, not more — so what disperses there is
the **search radar and command post**, pushed 250–400 m off the position. A
self-propelled system has no revetments at all: its TELARs deploy across 300–400
m, and that dispersion is what keeps them alive. Every placement is also wobbled
(`_JITTER_DEG` / `_JITTER_FRAC`), because a perfectly even ring at a constant
radius reads as generated through a TGP, and because a site with a real gap in
its fan means the axis a stick of bombs is laid down actually matters. The
launcher ring is offset half a step so no launcher shares a bearing with a
radar.

`disperse_site(group, *, radius_m, overlay=None, terrain=None)` applies the same
treatment to a group this module did not build — in practice a pydcs
`VehicleTemplate` site, which is the worst heap in the project: `sa6_site` parks
both launchers **30 m** from the radar, and `sa10_site`/`sa11_site` put
everything inside 100 m, so one CBU or a stick of two Mk-82s takes the battery
and the player never has to find the individual vehicles. It *inflates* the
template's own layout (each unit keeps its bearing, distances scaled so the
outermost sits at `radius_m`, then jittered), so the radar stays out in front of
its fan and — what missions depend on — **unit order is untouched**, keeping
`units[0]` the radar for every `unit_of_type` objective. Call it *after* any
extra units the mission adds to the group, or those stay where the mission put
them. All five missions that use a pydcs template now do (SA-6 → 300 m, SA-11 →
400 m, SA-10 → 500 m).

Two bounds hold it honest. The footprint stays well inside the 2 km offset
`PlanOverlay.threat` already applies to an estimated ring at `trained`, so
dispersing a site does not make the drawn ring or the HSD cartridge wrong. And
`snap_units_clear` now refuses to place two units of a group within
`MIN_UNIT_SEPARATION_M`, or to move one further than its search radius: with
65 m sites neither mattered, but at 300 m several units land in the same treeline
and `find_placement` **samples** cells rather than sorting them, so they were
handed the same one — a wooded coastal ridge turned abkhaz_sweep's SA-6 into a
1.3 km smear with two vehicles inside each other. A unit left in the canopy is
the smaller lie.

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

## Every speed is km/h true airspeed

**Write speeds in km/h and check them against the airframe.** Every pydcs
speed argument — `add_waypoint`, `OrbitAction`, `awacs_flight`,
`refuel_flight`, `patrol_flight`, `intercept_flight`,
`flight_group_inflight`, and `waypoints.add_ground_waypoint` on top of them —
is **km/h true airspeed**, stored as `speed / 3.6` m/s. None of them says so
in its signature and none of them validates the number, so a knots-shaped
value is accepted in silence and commands roughly **54 % of the intended
speed**.

All six missions shipped that way. Every `patrol_flight` / `awacs_flight` /
`refuel_flight` / `intercept_flight` call held a knots-shaped number (380–490)
while the hand-built `add_waypoint` routes in the same files held km/h-shaped
ones (750–850) — so the repo disagreed with itself, and the AI package was
ordered to hold **137–167 KIAS at FL210–FL295**:

| flight | was | commanded | should be |
|---|---|---|---|
| E-3A orbit @9000 m | 410 | 221 kt TAS / **137 KIAS** | 740 |
| KC-135 track @6500 m | 407 | 220 kt TAS / **157 KIAS** | 750 |
| F-15C CAP @8000 m | 430 | 232 kt TAS / **152 KIAS** | 800 |
| MiG-29S intercept @7500 m | 440 | 238 kt TAS / 160 KIAS | 900 |
| A-10C ingress @4600 m | 400 | 216 kt TAS / 171 KIAS | 520 |

The symptom is **the friendly package flying its whole sortie in
afterburner**: at those speeds a fighter is far below best-climb speed and
deep on the back side of the drag curve, and the AI holds the commanded
altitude on the throttle. Nothing caps it, either — `OptRestrictAfterburner`
is set in exactly one place (`tasking.apply_ai_difficulty`, the *enemy* dial,
and only at `recruit`), while `apply_threat_reaction` — what every blue AI
flight gets — never touches the throttle, and the DCS default is unrestricted.
Leave it that way: the airframes that should not be burning (E-3A, KC-135,
A-10C) have no afterburner to restrict, and the ones that do (CAP, SEAD) need
it in a merge. Fix the speed, not the option.

Sanity bound: `FlyingType.max_speed` is km/h too, so a cruise or orbit speed
lands around **0.3–0.4 of `max_speed`** and anything under ~0.2 is the unit
error:

```
F-15C 2650   F-16C 2120   A-10C 720   E-3A 860   KC-135 980   MQ-9 400
```

Correct per airframe, **never by a blanket ×1.852** — 400 kt is 740 km/h,
which is *above* the A-10C's never-exceed. Where a bare number would be
ambiguous, name the unit like `idlib_gauntlet`'s `_FAC_SPEED_KPH` does. The
pydcs-side gotcha, including the `strike_flight` / `sead_flight` helpers that
pick `max_speed * 0.8` (Mach 1.4 for a Viper) for themselves, is in
PYDCS_REFERENCE.md §4.2.

## Datalink-identity helper (project-owned)

[`datalink`](src/dcs_mission_creator/core/datalink.py) makes a coop flight show
up on its own scopes. Two things the ME writes for every aircraft, and pydcs
writes for none, decide that:

- **`AddPropAircraft`** carries the identity — the track number (`STN_L16` on
  Link 16, `SADL_TN` on the A-10's SADL) and the callsign the datalink displays
  it under (`VoiceCallsignLabel` + `VoiceCallsignNumber`). pydcs seeds those
  keys from the type's `property_defaults`, where all three are `None`, and
  never fills them: the whole package spawns anonymous, and identical.
- **`datalinks`** is the per-unit network table the four modules with a
  Datalink dialog (F-16C, F/A-18C, A-10C II, AH-64D) read their team members
  out of. pydcs has no field for it, so the F-16's MIDS comes up with an empty
  flight and no other player's PPLI symbol ever appears on the HSD, the FCR
  page or the HUD.

`assign_datalink_identities(m)` fills both, mission-wide and in group-id order
(deterministic, so the `.miz` stays byte-identical): a unique track number per
aircraft in per-flight blocks (`00101`, `00102`, …, `00201`), the ME's own
two-letter tag plus flight digits off the pydcs callsign (`Springfield11` →
`SD` + `11`), and each flight listed as its own team members. **Missions never
call it** — `MissionBuilder.build_miz` does, right after `snap_base_waypoints`.

Adding a module is a `_NETS` table entry — its dialog defaults and whether its
team members carry a TDOA flag, both read off
`<DCS>/CoreMods/aircraft/<module>/Datalinks/*.lua`. The AH-64D is deliberately
absent: its IDM has a different shape (`TN_IDM_LB` / `OwnshipCallSign`) and no
mission here flies one. Writing the table at all needs
[`core/unit_extras.py`](src/dcs_mission_creator/core/unit_extras.py) — a
one-wrapper patch of `FlyingUnit.dict`, the same kind of targeted patch as
`MissionBuilder._pin_onboard_numbers`, shared with `core/dtc.py` because two
mission-file unit keys have no pydcs field. Register with
`emit_unit_key("datalinks", "datalinks")`; **do not** write a second wrapper —
two wrappers each guarding on their own marker re-wrap the chain on every build.

## HSD threat-ring helper (project-owned)

[`dtc`](src/dcs_mission_creator/core/dtc.py) puts the briefed SAM rings in the
cockpit. The F-16C draws a surface-to-air envelope on the HSD (and the HAD) from
its **data cartridge**, not from the RWR: up to fifteen pre-planned threat
points in steerpoints 56–70, each with a position, a three-character code and
the system's range and ceiling. DCS reads them from a `DTC/<name>.dtc` JSON file
inside the `.miz` plus a per-unit `DTC` key naming the cartridges that slot
carries — and pydcs writes neither (`Mission.save` writes a fixed set of zip
entries, `Unit.dict` has no `DTC` field).

Feed it what the F10 plan *drew*, not the site's true position:
`PlanOverlay.threat` now returns the `(center, radius)` it painted — coarsened
and offset at `trained`, `None` at `veteran`/`ace` — and `dtc.briefed` turns
that into zero or one threat point, so the cockpit ring and the map ring are the
same claim and the difficulty policy stays in `map_draw.py`:

```python
hsd = dtc.briefed(
    plan.threat(sa6_pos, radius=12_000.0, label="SA-6", icon=StandardIcon.AirDefense),
    dtc.SA_6,
)
...
dtc.arm_hsd_threats(m, hsd, overlay=scene.overlay.overlay)   # a `_load_hsd_threats` step
```

- `arm_hsd_threats(m, points, *, name="THREATS", overlay=None)` — builds one
  cartridge, marks it default + `AutoLoad` (the rings are up before the player
  touches the DTE page) and attaches it to every **player-flown F-16C** unit.
  Empty `points` writes nothing, which is exactly how an ace mission comes out;
  more than fifteen points, or no Viper slot to load, raises.
- `ThreatSystem` constants (`dtc.SA_2`, `dtc.SA_6`, `dtc.SA_19`, `dtc.HAWK`, …)
  are the jet's own `THREAT_PTS_defs` rows — `def_num`, exact name, code, range,
  ceiling. Adding a system is a table entry.
- The gotcha that makes it work at all: every `mirror_*` flag is the editor's
  **"Do not upload tab data"** checkbox and defaults to *on*, so
  `mirror_THREAT_PTS` has to be `False` or the jet takes the cartridge and then
  declines to read the tab. The other tabs stay mirrored on purpose — uploading
  an empty steerpoint or comms tab would wipe the mission's own route and radio
  presets.
- **Missions never write the file** — `MissionBuilder.build_miz` appends it
  after `m.save(...)`, since the cartridge is a file *inside* the package.

Only list **emplaced** systems the briefing names: a ring the player was never
briefed on is intel the mission did not claim to have, and a pre-planned point
is a static claim, so anything that drives is excluded on principle — a convoy's
organic SHORAD, a launcher on a road march, a mobile reserve. Draw those with
`PlanOverlay.mobile_threat` (no envelope, nothing returned), and let the
briefing say the axis is SHORAD country instead of pretending to a fix. EWRs are
not rings either (a search radar has no envelope), and neither are ground-force
markers. The F/A-18C has a cartridge too, but its threats are `MEZ_THRTS` on the
SA page — a second table, not a parameter — and no other module in DCS draws a
pre-planned ring.

## Recon-still helper (project-owned)

[`recon`](src/dcs_mission_creator/core/recon/) renders the imagery a briefing
claims. `idlib_gauntlet`'s Intelligence section always said the picture came off
"this morning's Reaper feed" and that the feed counted the SHORAD in the column —
prose describing imagery nobody could look at. This produces the imagery, from
the overlay rasters plus the positions of the groups the mission spawned, and
ships it twice: as a `pictureFileNameB` slide inside the `.miz` (the briefing
screen) and as a PNG beside the README, which embeds it.

```python
from dcs_mission_creator.core.recon import Chrome, Frame, Mark, road_column
from dcs_mission_creator.core.recon import publish as recon

column = road_column(overlay, scene.convoy_origin, scene.convoy_destination, 11)
returns = plan.detections(column)          # the reveal gate; [] at veteran/ace
if returns:
    self._still = recon.sensor_still(
        m,
        Frame.along_axis(returns[0], returns[-1], heading_offset_deg=-90.0),
        [Mark(x=p.x, y=p.y) for p in returns] + [group_mark],
        Chrome(platform="MQ-9 / AN-APY-8 LYNX II", mode="WAS-MTI  5 LOOK",
               taken_at="0540L  12 SEP 26", caption="..."),
        overlay=scene.overlay.overlay, slug=self.name, label="convoy",
    )
```

**It is a radar product, not a photograph, and that is forced by the data.**
Measured at the Idlib route midpoint, a 6 km frame holds *no roads, no water and
no trees*, with elevation spanning 271–319 m in whole metres across 49 distinct
values — ~95 % of an EO/IR frame there would be invention, and hillshading
1 m-quantised elevation at ~1.2° slope gives contour terracing, not terrain. The
honest floor is **two output pixels per 50 m post**, so the frame is
25.6 × 19.2 km at 1024 × 768 (25 m/px), rotated so the route runs across it. At
that scale a vehicle is a fifth of a pixel, which is *why* detections are
symbology drawn after the sensor chain rather than hot blobs in it: an open
bracket over a coarse base claims exactly what the product can support. Full
argument, with the numbers, in
[render.py](src/dcs_mission_creator/core/recon/render.py)'s docstring.

Two rules govern what may appear:

- **Statistics may be modelled; features may not.** Open ground carries a
  correlated roughness field (a few dB over field-sized patches) because holding
  sigma-zero constant made 90 % of the frame uniform white noise, which reads as
  *broken* rather than as *coarse*. That claims "this ground has roughness
  variation at about this scale", which is true; it does not claim a hedge or a
  parcel boundary anywhere. Never draw a feature the overlay does not know about.
- **`PlanOverlay.detections` is the only source of positions.** A still is a
  third reveal channel beside the F10 plan and the HSD cartridge, so it answers
  to the same difficulty policy; `detections` returns `[]` at `veteran`/`ace` and
  the mission then publishes no frame. Its `trained` error is **one registration
  bias shared by the whole cluster** plus small per-return jitter — offsetting
  each vehicle independently scatters an eleven-vehicle column over four
  kilometres and it stops reading as a column, and a shared bias is what a real
  product's error actually looks like. `bias_m` itself is **calibration, not
  policy**: the 1.2 km default is for a frame with no landmark in it (Idlib's
  ground holds no road, no water and no tree), and a frame that paints the road
  net has to cut it to a couple of hundred metres — a product is registered
  against what it can see. The default over `coastal_cover`'s valley put the
  column 1.0–1.2 km off any road in a picture that draws the roads.

**A mission gets a still only if the overlay can carry its subject**, which is a
question about the rasters and has to be measured, not assumed. `coastal_cover`
(the column on the valley road) and `kodori_strike` (the base on the coast road)
both carry one; `eastern_shield` cannot. Its depot is the Kuweires apron, and the
overlay has no aeroway layer — 4 building cells within 1.5 km of the field and
the nearest road 2.2 km away — so the frame renders farmland with a bracket in
the middle of it and the bracket names an apron the picture does not show. Give
the overlay runways and it is the next candidate.

The two `ace` missions need no judgement call: `detections` returns `[]` there, so
`abkhaz_sweep` and `daryal_run` publish nothing by policy, and neither briefing
cites imagery in the first place.

What made `kodori_strike` publishable was fixing the mission, not the renderer —
see its `_setup_airports`. It briefed a FOB in the Kodori valley "~22 km NE of
Sukhumi-Babushara" while building it 19.3 km from that seed on the coastal plain,
because the overlay's OSM filter keeps only the major road network and the only
major road in Abkhazia is the coastal highway, so `near_road_m` was unsatisfiable
in the mountains and `find_clear_spot` escalated out of the region. A still would
have put that contradiction on the briefing screen, which is a good argument for
publishing one: **imagery is a consistency check on a mission's own geography.**

**Landmarks are what make a still usable.** A frame of speckled ground with a
bracket on it is convincing and useless: nothing in it ties to the map the player
is planning on. `landmark_marks(overlay, frame, avoid=<the marks you are about to
draw>)` labels the settlements — a dot and an upper-case name, drawn in a register
deliberately unlike the sensor's own symbology, so a place cannot be read as
something the radar found. Three rules, all enforced in code:

- **Only what the raster drew.** The names come from a `places.geojson` sidecar
  holding just the OSM place classes `buildings.zarr` was rasterized from, so a
  label always sits on a built-up return the picture actually paints (measured:
  urban coverage 0.35 under every label against a 0.014–0.028 frame background).
  This is the rule the reverted `eastern_shield` label broke.
- **Collision is tested on the ink**, via `render.mark_extent`, which returns the
  pixel box a mark covers — symbol *and* text. A radius round the target's centre
  is not enough: a group label like `7 DET  TRK 222  40 KM/H` is 190 px, i.e.
  4.7 km of ground at 25 m/px, so a village 3 km clear of the bracket still had
  its name printed straight through the target's.
- **Not a reveal channel.** A settlement is on every map both sides have, so this
  does not go through `detections`; ranking is class then distance to the frame
  centre (by name it produced six labels beginning with A), and it is a sort plus
  a greedy walk, never a sample, because the render cache keys on the marks.

The sidecar is its own build layer, cheap and independent of the rasters, so it can
be added to an overlay that is already built:

```bash
uv run dcs-mission-creator map-overlay build caucasus --layers places
```

It is a `node["place"]` Overpass query per tile — ~100 KB each, cached beside the
main tiles, resumable — **not** a re-parse of those tiles, which are 100 files and
19 GB for Caucasus. `MapOverlay.places` returns `[]` with one warning when the
sidecar is absent, so an older overlay yields a still with no labels instead of a
failed build. Names come from `name:en` and must be ASCII: the native Abkhaz and
Georgian names are the more accurate label, but DejaVu Sans Mono has no glyph for
several Abkhaz letters and they render as tofu, which reads as a broken product.

Also load the column with `road_column`, not from `group.units[*].position`:
pydcs `vehicle_group_platoon` uses `Formation.Line`, which stacks units 20 m apart
**abeam** the heading, and DCS only strings them along the road once the mission
runs — the spawn positions are a 200 m dash at right angles to the road the
briefing names.

The cache mirrors `VoiceSynth`: `cache/recon/<slug>-<label>-<hash8>.png`, keyed on
the **sampled scene** rather than the query, so rebuilding the overlay invalidates
it. The renderer is seeded from that key alone (never stdlib `random`, whose stream
depends on how many draws the mission already made), and the file's mtime and mode
are pinned because `zipf.write` records both into the archive. Missions never copy
the file — `MissionBuilder.build_miz` does.

## Front-line helper (project-owned)

[`frontline`](src/dcs_mission_creator/core/frontline.py) is the geometry of a
front: the reason a mission's target cannot be attacked from an arbitrary
bearing. Without one, the player arcs wide of every belt and comes in from the
quarter nobody covered, and the whole threat layout watches from behind.

```python
from dcs_mission_creator.core.frontline import plan_frontline

front = plan_frontline(
    defends=scene.route_mid,          # what the line stands in front of
    facing=scene.hatay.position,      # the side the threat comes from
    standoff_m=26_000.0, span_m=90_000.0, bow_m=12_000.0,
    sectors_per_side=2, seam_width_m=30_000.0,
    overlay=scene.overlay.overlay, terrain=self._terrain,   # both or neither
)
front.trace       # the drawn polyline, flank to flank, seam included
front.shoulders   # the two tips — where the area SAMs go
front.sectors     # the dug-in positions between them, seam excluded
front.seam        # the crossing the briefing points at
front.facing_deg  # heading a site on the line wants
```

Geometry only — no groups, no tasks, exactly like `core/routing.py`. Force
composition (an S-125 battery per shoulder, armour + guns + MANPADS per sector)
is mission policy. `span_m` is the detour a flanker pays and `bow_m` sweeps the
wings toward `facing` so the flanks are the long way in as well as the far way
round; `seam_width_m` is the frontage with no position on it. Feed the shoulder
envelopes into `_threat_rings` so the drawn plan, the cartridge and the AI routes
are one claim, and draw the trace with `PlanOverlay.frontline` — the trace is
public knowledge, so it is the one red thing drawn precisely at **every**
difficulty. Design rules (how to price each way in, depth coverage behind the
line, the seam leading into the mission's real problem) live in the
`dcs-mission` skill; `TacticalScene.place_frontline` is the different question
(a FLOT meandering between two known anchors).

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
  lock, RTB on bingo. Every AI flight in a blue package gets it (except AWACS and Tanker because `ByPassAndEscape` on an orbiting tanker pulls it off station); escalate to
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

## Integrated air-defence helper (project-owned)

[`iads`](src/dcs_mission_creator/core/iads.py) gives radar sites the two
behaviours DCS does not model. Left alone, every SAM radiates from mission start
— the player's RWR is full before anyone has detected him — and then keeps
radiating while a HARM rides the beam in, so every SEAD shot is a free kill.
`arm_iads` fixes both by shipping walder's
[Skynet-IADS](https://github.com/walder/Skynet-IADS) inside the `.miz` and
driving it, while keeping this project's own model of what a crew knows about an
anti-radiation launch:

```python
from dcs_mission_creator.core.iads import Site, arm_iads

arm_iads(
    m,
    [Site(sa6, "SA-6", go_live_percent=150, probability=0.9,
          delay_s=(14, 40), shutdown_s=(280, 400)),
     Site(sa2, "SA-2", go_live_percent=130, probability=0.7),
     Site(ewr, "EWR", role="ewr"),          # a unit, not a group — see below
     Site(sa10, "SA-10", act_as_ew=True, point_defence=sa15)],
    voice=self._voice,                      # calls are spoken as well as printed
    down_call="Magic: {label} has ceased emissions, site is dark.",
    up_call="Magic: {label} is radiating again, expect it hot.",
    debug=False,                            # Skynet's own live/dark log output
)
```

It adds **three** mission-start triggers the first time it is called, in order:
`core/lua/mist_shim.lua`, `core/lua/vendor/skynet-iads.lua` (both as
`a_do_script_file` resources — 117 KB is not inline material), then the generated
setup as an `InlineDoScript`. A second call reuses the loaded framework.

**The framework owns when a site radiates.** It knows each system's real
envelopes, analyses every launcher and radar against them individually, cues
sites off whichever radars are live, tracks ammunition, and degrades the net when
links or power go. Per site the mission states `go_live_percent` — a fraction of
the system's *own* reach, so an SA-8 and an S-200 are configured identically
without anyone looking up either envelope; go **over 100** or a battery comes up
and watches rather than shoots, since a DCS site needs ~30 s from cold. Plus
`engagement_zone` (`"kill"` / `"search"`), `act_as_ew` for a radar that stays up
throughout, `autonomous`, and `point_defence`.

Two things to get right or the feature silently does nothing:
- `role="ewr"` registers a **unit**, `role="sam"` a **group** — different classes
  inside the framework. An `"ewr"` group must hold exactly one unit (the helper
  raises rather than guess which one is the radar).
- **Something must radiate**: an all-cued net has nothing to hand tracks down.
  The helper warns when no site is `role="ewr"` or `act_as_ew`.

The consequence that is easy to get backwards, and that briefings depend on:
killing the early-warning chain does **not** switch the belts off. A battery with
no live parent radar goes *autonomous*, and with `autonomous="ai"` (the default,
and doctrine) that means it searches on its own — radiating continuously from
then on. A real trade for the player, not a win: every belt becomes an emitter
that can be found and shot, but nothing is dark any more either.
`autonomous="dark"` shuts it down instead — do not do that to a whole net, or two
HARMs on the search radars end the SAM threat.

**We own the reaction to being shot at**, and Skynet's own HARM detection is
switched off at setup. It identifies the *missile* in flight (>800 kt, few
flight-path changes) and darkens radars ahead of its track, which hands a crew
knowledge of a passive weapon they cannot have. Here a launch only reaches sites
that could observe it: line of sight to the launch point earns the site's own
`probability`; a masked launch earns `probability * net_relay`, and only if some
*other* radiating site in reach did see it. Mask the launch from the whole net
and nobody reacts — a lofted shot from behind a ridge is a real tactic.

Per-site dials are the SEAD difficulty statement: `probability` (does this crew
act on a launch it saw), `delay_s` (recognition lag — **tens of seconds**, the
same order as a HARM's time of flight, because nobody in the site gets a launch
warning: the shot must be seen, called down the net and acted on. Single-digit
seconds darken the radar in the first third of the missile's flight and no HARM
ever connects; at these bands the shooter's range at launch decides the duel),
`shutdown_s` (how long it stays dark — minutes, not Skynet's 180 s cap past
impact, so a HARM buys a real working window; repeat fire extends it),
`react_range_m` (how far down the net the launch travels). Both time bands are
drawn triangularly, so the middle of the band is the common case. A suppressed
site is released to *cold*, not hot — it re-radiates only if there is still
something to shoot at.

Radio calls are queued and played `announce_spacing_s` apart, so a shot that
darkens a whole belt gets one call per site instead of only the first. A site
coming up for the **first** time says nothing (`hot_call` defaults to `None`):
the player's RWR is that call, and announcing it would give away a battery the
briefing deliberately left off the map. One coming *back* after being shot off
the air uses `up_call`, since that is news; one going quiet because the package
left is silent, because `down_call` means "SEAD worked" and must not be borrowed.
A site whose **radars** are dead is destroyed rather than suppressed and drops
out silently — the gate is a live radar unit, because DCS keeps the group alive
while one launcher stands, and gating on the group had a site the player had just
killed report that it was going dark and then that it was radiating again.

Only list **radar-guided** sites — SA-13/MANPADS have nothing to shut down, and
listing a mixed convoy would make the whole column hold fire on every HARM shot.
Design rules (what reveal, what difficulty) live in the `dcs-mission` skill; the
pydcs `DoScript` mechanics are in PYDCS_REFERENCE.md §7.

### The vendored framework

`core/lua/vendor/` holds Skynet 3.3.0 verbatim (Apache-2.0) plus its licence and
a README covering provenance and how to update. **There is no MIST**, although
Skynet documents it as a prerequisite: MIST is GPL-3.0, and putting a copyleft
library inside every generated mission is not a decision this project makes on a
user's behalf. Skynet calls exactly thirteen MIST functions — a scheduler pair,
seven conversions and vector helpers, `mist.random`, `mist.getHeading`, and two
name-lookup tables only the `*ByPrefix` registration paths use (this project
registers by exact name) — so `core/lua/mist_shim.lua` is a first-party
implementation of that surface.

Two tests keep the seams honest, and both are the reason a version bump is safe:
- `tests/test_iads.py` asserts every Skynet method, constant and field the
  generated setup touches exists in the pinned build, and that the shim covers
  every `mist.*` the build calls.
- `tests/test_iads_runtime.py` **runs** the whole stack — shim, framework,
  generated setup — under an embedded Lua (`lupa`, a dev dependency; the test
  skips without it) against `tests/lua/dcs_env.lua`, a stub of the DCS scripting
  environment, and drives the clock: batteries start dark, come up when the jet
  is inside the cue range, go dark on an observed HARM after the recognition
  delay, stay dark for the window, come back, ignore a launch masked from the
  whole net, and go autonomous when the chain dies. Writing it caught four real
  things, including that "killing the EWRs blinds the belts" is false.

If a future Skynet build is dropped in, run both. The stub is shaped the way DCS
actually behaves because the framework leans on the details — a group's metatable
must be `Group` itself, a live site stays live only because its own radar still
holds the contact, and `S_EVENT_DEAD` is what tells a battery its parent radar is
gone.

## JTAC coordinate-readout helper (project-owned)

[`jtac`](src/dcs_mission_creator/core/jtac.py) fixes the one thing about a DCS
JTAC that no mission-editor setting reaches: the coordinate system. The stock
9-line and target call both go through `MGRS:make(point, 4)` in the game's own
`Scripts/Speech/NATO.lua`, so **every** airframe is read a 4-digit military
grid — right for an A-10's CDU or an Apache's TSD, useless in an F-16 whose DED
takes degrees and decimal minutes and cannot enter a grid at all.
`arm_jtac_coords` adds a radio request that answers in the format of the
airframe that asked:

```python
from dcs_mission_creator.core.jtac import CoordTarget, arm_jtac_coords

arm_jtac_coords(
    m,
    [CoordTarget(convoy, label="Hammer 1-1", what="the resupply column",
                 laser_code=1688)],
    menu_title="Hammer 1-1",          # name it after the controller's callsign
)
```

The format comes from the requesting *player group's* aircraft type via
`COCKPIT_COORD_FORMAT` (only the grid cockpits are listed — A-10A/C/C II,
AH-64D, OH-58D; everything else falls through to `default_format=DDM`), so one
JTAC reads a grid to a Hog and degrees-and-minutes to a Viper in the same
mission, and a player who swaps slots gets the new cockpit's format. Override
per mission with `formats={planes.FA_18C_hornet.id: CoordFormat.MGRS}`.

Two design consequences worth keeping: the position is read off a live unit on
each request, so it is current for a column that is still driving (a briefing
line would be stale by the run-in), and the reply is **text only** — the numbers
are computed in the mission while `VoiceSynth` renders its audio ahead of time.
This does not replace `tasking.fac_attack_group`: that is what makes the
controller acquire, lase and talk. Arm both.

**Pass `push_at_s`** (mission seconds, just after the controller's check-in
call) unless there is a reason not to. It reads out the first target's position
once, unprompted, to whoever is in the cockpit and to anyone slotting in later.
Without it the feature is invisible: DCS keeps reading its own grid, the player
has no reason to go looking in F10 → Other, and the mission looks like it never
had the readout. Say where the entry is in the briefing too, and say that the
stock nine-line is a grid — otherwise the two calls look like a bug.

## Mission Lua lives in `.lua` files

Never embed mission-script Lua as a Python string literal. Every `DoScript`
payload is a real file under
[core/lua/](src/dcs_mission_creator/core/lua/) (e.g. `iads.lua`), loaded via
that package's loader:

```python
from dcs_mission_creator.core import lua

script = lua.render("iads.lua", SITES=rows, SIDE="coalition.side.BLUE",
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
start). It does no quoting itself — build string literals with `lua.quote(text)`,
which also renders `None` as `nil` (an empty string would still pass a Lua truth
test, so an absent radio call or laser code would print as a blank one).
`lua.source(name)` returns the raw text. New `.lua` files are picked up automatically; the
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
    briefed_threats = self._draw_plan(m, scene, ...)
    self._load_hsd_threats(m, scene, briefed_threats)
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
  is why call sites never say so), `mark_clients(group)`, `arm(...)` (see the
  loadout rule below), `race_track(p1, p2)` for the orbit arguments
  `awacs_flight` / `refuel_flight` want, `unit_of_type(group, type)`, and a
  re-export of `set_skill`. Import these rather than redefining them.

  Use `unit_of_type` — never `group.units[0]` — when an objective means "kill
  the radar". pydcs's own `VehicleTemplate.Russia.sa10_site` puts a paratrooper
  at index 1, so an index-based win condition silently becomes "kill one
  infantryman"; `unit_of_type` raises instead.
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

Building the same mission twice produces the same `.miz` **contents**, entry for
entry. That is not free — five separate things had to be pinned, and all five are
easy to undo by accident:

- `MapOverlay` carries the sampling `seed` (default 0). `find_placement` takes
  no per-call seed; build the overlay with a different one to resample.
- `MissionBuilder.generate` seeds the stdlib `random` from the mission slug.
- `MissionBuilder._pin_runway_waypoint_distance` — pydcs declares
  `add_runway_waypoint(..., distance=random.randrange(6000, 8000, 100))`, a
  **default argument**, so the value is drawn once when `dcs.unitgroup` is
  imported, before any seeding can run. It moved every flight's take-off point.
- `MissionBuilder._pin_onboard_numbers` — pydcs picks tail numbers with
  `set.pop()` over a set of strings, which follows string hashing.
- `core/recon/publish.py` pins the rendered PNG's **mtime and mode**
  (`os.utime` / `os.chmod`). pydcs stores a resource as a *path* and writes it at
  save time with `zipf.write`, which records `st_mtime`, and `ZipInfo.from_file`
  puts the file mode in `external_attr` — so a re-render, or a different umask,
  changes the archive even when every pixel is identical. `core/dtc.py` solves the
  same problem with an explicit `ZipInfo(date_time=...)`, which the `MapResource`
  path gives no hook for. Note the voice cache has the same exposure and no pin:
  it gets away with it only because a warm `cache/voice/` leaves the WAV mtimes
  alone.

**The archive itself is not byte-identical, and never has been.** `Mission.save`
writes `mission`, `options`, `warehouses` and the two `l10n/DEFAULT` files with
`zipfile.writestr`, which stamps each entry with the *current time*; two builds
more than two seconds apart therefore differ in those five headers while every
byte of content matches. Verified against `abkhaz_sweep`, which carries no recon
still: two CLI builds give two different hashes. So compare entry contents, not
file hashes — which is what `test_generation_is_reproducible` now does. It used
to compare whole files and passed only while both builds landed in the same
two-second DOS-timestamp bucket: true for two warm 0.08 s builds, false as soon
as the first build paid a cold cost, which made a real property look flaky and a
clock-dependent assertion look like evidence of byte-identity.

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
  Carries a **recon still** (`core/recon`) of the column on the valley road,
  which is the whole intelligence picture here: every claim in its briefing is
  sourced to one Reaper that has been up since first light.
- [kodori_strike.py](src/dcs_mission_creator/missions/kodori_strike.py) —
  Caucasus: F-16C strike out of Kutaisi on a Russian FOB astride the coast road
  at the Kodori delta, with an F-16C SEAD element against the SA-6 on the rising
  ground inland and Su-27s out of Gudauta. Trained, ~75 min. Also carries a
  **recon still** — the coastline in it is what makes it the most legible of the
  three. Read its `_setup_airports` before moving any AO on this map: it is the
  worked example of a placement whose constraints were unsatisfiable where the
  briefing pointed, and of the two ways a coast breaks a placement filter (no
  roads inland of the highway; `min_relative_height_m` reading a beach as high
  ground because the sea drags the local mean below zero).
- [idlib_gauntlet.py](src/dcs_mission_creator/missions/idlib_gauntlet.py) —
  Syria: F-16C out of Hatay against a Syrian resupply column with organic
  SHORAD, run through three SAM belts (SA-2 / SA-6 / SA-8 + EWR) that go dark
  on HARM fire and re-radiate (`core/iads.py`). Trained difficulty, ~60 min.
  The reference for missions that want realistic SEAD — and for the other two
  things that keep a sortie on its plan: a 90 km **front line** across the
  ingress axis (`core/frontline.py`) whose S-125 shoulders price the flanks and
  whose seam funnels the player into the SA-6's sector, plus rear-area batteries
  behind it so the far side of the line is held ground. One of those, the
  northern SA-11, is deliberately on no map and no cartridge: the briefing calls
  it an emitter nobody fixed, `Magic` names it when the player crosses the seam,
  and it only bites someone who flanks. It is also the reference for the **recon
  still** (`core/recon`): a wide-area radar frame of the resupply column on the
  briefing screen and in the README, which is the imagery its Intelligence
  section was already citing.

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

Those same four then pass the rings their `_draw_plan` drew to a
`_load_hsd_threats` step (`core/dtc.py`), so the F-16C player's HSD carries the
briefed envelopes as pre-planned threats. The ace pair needs no exception:
`PlanOverlay.threat` returns nothing there, so there is nothing to load.

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

Unset, the helper logs a warning and the DCS task defaults come back empty.
Liveries stay at the DCS default even when set — pydcs's livery scanner splits
paths on `\` and cannot be used off Windows (see the docstring).

**Arm every blue flight explicitly — never rely on the task default.** It is
sourced from the installed game, so it is empty without `DCS_INSTALL_DIR` and
is whatever DCS happens to frag with it. Either way it is not what the briefing
promised, and five of the six missions once shipped with entirely empty pylons
while their briefings named specific stores. Use
[`mission_kit.arm`](src/dcs_mission_creator/core/mission_kit.py), which clears
the stations first so no default survives on a station the list skips:

```python
arm(player, planes.F_16C_50, [
    (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
    (2, "AIM_9X_Sidewinder_IR_AAM"),
    (3, "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"),
    (4, "Fuel_tank_370_gal"),
    (10, "AN_ASQ_213_HTS___HARM_Targeting_System"),
])
```

Write the list at the spawn site, not in a shared catalogue — a loadout is
force composition, and one mission's package should not constrain another's.
The `PylonN` classes on each `PlaneType` enumerate what a station legally
takes, so read the names off pydcs rather than guessing; a wrong one is an
`AttributeError` at build time instead of a silent empty rail.

**Legal is not realistic.** pydcs only checks that the station accepts the
store, so a plausible-looking list can still be one no squadron would fly. The
authority is the game's own payload tables, not memory — `<DCS>/CoreMods/
aircraft/<module>/UnitPayloads/*.lua` and `<DCS>/MissionEditor/data/scripts/
UnitPayloads/*.lua` list every ED-shipped loadout as (CLSID, station) pairs, so
grep the store and see which stations it actually sits on. Two errors this
caught, both legal in pydcs:

- **F-16C wingtips (1/9) carry the AIM-120, not the AIM-9** — the Sidewinders go
  on stations 2/8. Every mission had the pair the wrong way round.
- **The F-15C never flies a single wing tank.** Its fuel stations are 2 (left
  wing), 6 (centerline) and 10 (right wing); all eleven ED payloads that carry
  fuel use 6, and the wing pair only ever comes as 2 + 10. A lone `(10, tank)`
  is an asymmetric jet.

Also check the loadout against the *sortie*, not only the airframe: a modern
CAS or LGB tasking wants a targeting pod on the jet that needs one — except the
A-10C, whose TGP is integrated (no ED payload lists an AAQ-28, so don't add
one).

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
