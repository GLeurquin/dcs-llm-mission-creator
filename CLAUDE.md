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
    `self.players` (1–6, validated by the base class) for client-slot counts —
    build the player flight with `mission_kit.player_flight`, never with a raw
    `group_size=self.players` (see below);
  - `def readme(self) -> str` — returns the README.md content (markdown,
    the mission briefing).
- The base class owns everything around that, and **a mission overrides none
  of it**:
  - `build_miz` constructs the `Mission`, calls `_assemble`, holds the friendly
    package until a player is airborne, snaps every flight's take-off/landing
    waypoints to field elevation, assigns datalink identities, makes the output
    directory and saves. All three finishing steps have to happen after the
    last flight exists and before the save — pydcs hard-codes take-off/landing
    altitudes to zero, so a mission that skipped the snap shipped a jet spawned
    underground; it writes no datalink identity at all, so a coop flight came up
    anonymous and blind to itself; and it launches every AI flight at
    `TriggerStart`, so the package the player was briefed to escort was a
    hundred kilometres down the route before he rotated (`core/join_up.py`).
    They are in the base precisely so they cannot be forgotten. Two steps run
    *after* the save for the mirror-image reason: any data cartridge a mission
    armed (`core/dtc.py`) and the kneeboard cards (`core/kneeboard.py`) are
    files inside the `.miz`, and `Mission.save` writes a fixed set of zip
    entries with no hook for another one. The kneeboard has to come last for a
    second reason as well — its route card prints the take-off and landing
    altitudes the snap has just corrected.
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
policy. `.threat()` is the difficulty dial, and what it dials is **precision,
not presence**: full icon + true ring on `recruit`, coarse + offset + "(est.)"
on `trained`, and on `veteran`/`ace` a wider, dashed, unfilled ring further off
truth and labelled "(approx.)". `.objective()` tightens/loosens the same way,
through the same estimate.

The higher labels used to draw **nothing**, and that was worse than drawing an
approximation, for a reason that is invisible from inside `map_draw.py`: a
mission still has to put steerpoints somewhere. With no estimate to build from
they were built from the site's true position. `daryal_run` concealed every
Russian icon, drew its S-300 as a vague area 4 km off — and wrote a `TARGET`
steerpoint **170 m** from the launchers, which the player reads out of the DED
before releasing brakes. Withholding the ring never withheld the position; it
moved the leak to the one channel the reveal policy did not cover. So every
difficulty now draws a ring and hands the estimate back, and every channel
downstream is built from that one imprecise claim. Friendly-plan calls (`route`, `orbit`,
`waypoint_label`, `umbrella`) always draw precisely — and `umbrella` is the one
whose precision is load-bearing rather than incidental: it is the envelope of our
*own* SAMs (`core/sanctuary.py`), coarsening it would model an ignorance nobody
has, and a pilot who is hit and low on fuel cannot use a refuge drawn 6 km off
truth. `.frontline()` is the one *enemy* call
that also draws precisely at every difficulty — a front line is ground both
armies have held for weeks, and the briefing's "cross at the seam" needs
something on the map to point at; the air defence sitting on it still goes
through `.threat()` / `.mobile_threat()` like anything else. Design rules (what
to draw, reveal per label, and that a site may be left off the map on purpose)
live in the `dcs-mission` skill; the underlying pydcs drawing API lives in
PYDCS_REFERENCE.md.

`PlanOverlay` also **remembers what it drew** — `plan.lines()` and
`plan.marks()` hand back every polyline and every labelled point at the position
it was painted. `core/dtc.py` turns those into the Viper's own steerpoints and
GEO lines, so the F10 map and the DTE page are one briefing rather than two
guesses at it, and the reveal policy stays here even though the cartridge is
written elsewhere: there is no truth in either list to out-claim the map with.

`.threat()` also **returns** the `(center, radius)` it drew, and
`.estimate(center, radius=…)` gives the same pair without drawing anything —
for the callers that need the claim *before* `_draw_plan` runs, which is any
mission whose flight plan refers to a site. The estimate is **memoised on the
true position**, so the map ring, the cartridge point, the target steerpoint and
the kneeboard line are one object rather than four guesses; without that the
offset bearing is a fresh random draw per call and the cockpit contradicts the
map. Feed it to `core/dtc.py` via `dtc.briefed` rather than re-deriving it.

A mission that needs the estimate for its geometry builds its `PlanOverlay`
**first**, before the flights, and passes it to `_draw_plan` at the end rather
than constructing a second one — `daryal_run` and `idlib_gauntlet` both do.
The rule that falls out: **every planned point that refers to an enemy site
derives from the estimate, never from the site.** Then nothing the player can
read — F10, DED, HSD, kneeboard — carries a better position than the briefing
admits to, and a steerpoint that happens to land near the truth is luck rather
than a leak.

What stays on truth is `core/routing.py`. Its rings decide whether the route
gets shot at, and the drawn ring is offset on purpose, so planning a package
around the drawing bends it away from empty sky and leaves it exposed where the
launchers are. Routing is the margin that keeps a flight alive; the estimate is
the claim the player is shown. Where the two visibly disagree on the map the
briefing has to be what explains it — `abkhaz_sweep` is the worked example: at
ace its Kub ring is 34 km wide and 6 km off, there is no water between Sukhumi
and Gudauta outside it, so the sweep stations sit inside the drawn ring and the
ROE answers the Kub with 4,500 m of altitude instead of with distance.

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

## Somewhere to fall back to (project-owned)

[`sanctuary`](src/dcs_mission_creator/core/sanctuary.py) gives each side a
**defended** place to run to. Every mission here used to have none: measured
across all six, not one friendly SAM, not one AAA piece, not one blue
air-defence group anywhere. The recovery field was bare ground with a runway on
it, so a MiG that chased a bingo-fuel, out-of-missiles jet home followed it to
the flare and shot it in the overhead — and nothing on the F10 map, in the DED or
on the kneeboard marked a square kilometre where that could not happen.

That is a design defect, not a missing feature, and it breaks two things at once.
**Disengaging stops being a decision**: a player who correctly reads a fight as
lost has no move that changes the odds, so running is a slower death and the only
playable line is to press a losing merge — while every mission's threat layout is
priced on the assumption the player *can* leave (`core/frontline.py` is entirely
about pricing the ways in). And **pursuit costs nothing**: a red interceptor that
follows the player 150 km into friendly airspace should be making the mistake it
really is, and the way to make it one is not to script the MiG, it is to put a
battery where the MiG has to fly.

```python
from dcs_mission_creator.core import sanctuary as sanc

home = sanc.build_sanctuary(
    m, usa, scene.batumi, callsign="BULLDOG",
    facing=scene.ao_center,               # the threat axis
    battery=sanc.HAWK,                    # 45 km, off the jet's own table
    keep_clear=[scene.ao_center, *red_sites],
    alternates=[scene.kobuleti],          # warns if not actually covered
    overlay=scene.overlay.overlay, terrain=self._terrain,
)
red = sanc.build_sanctuary(
    m, russia, scene.sukhumi, callsign="Sukhumi field",
    facing=scene.ao_center, battery=sanc.SA_3,
    enemy=True, label="SA-3 Sukhumi",     # <=20 chars: the kneeboard column
    keep_clear=[scene.ao_center, *friendly_stations],
    skill=Skill.Average, overlay=..., terrain=...,
)
```

Then in `_draw_plan`, **first**, and in `_assemble` before `_conceal_red`:

```python
home.draw(plan)                    # returns [] — our battery is not a threat
return hsd + red.draw(plan)        # returns the pre-planned threat point
```

plus `sanc.remark_all(m, home, red)` and one
`mission_triggers.checkin(..., text=sanc.checkin_text(home, controller="Magic"))`.
The check-in is not decoration: a cyan ring reads as decoration and nobody opens
the F10 map again after push — same argument as `core/jtac`'s `push_at_s`. Each
remark is written to fit `kneeboard.page.COLUMNS` (98) on **one** line, because a
mission with a primary field, a divert and a JTAC carries five of them and a
remark that runs over costs two lines of the block.

**Both sides get one, and what differs is the reveal, not the geometry.** That
split is why this module knows about `map_draw.py` at all:

| | friendly | enemy |
|---|---|---|
| F10 | `PlanOverlay.umbrella` — precise at **every** difficulty | `PlanOverlay.threat` — estimated, per the reveal policy |
| cartridge | the marshal leg / a divert field, as steerpoints | a pre-planned threat point via `dtc.briefed` |
| kneeboard | a REMARKS line naming the cover | the route card's threat block, like any belt |

A battery the player's own side emplaced is not intelligence, so coarsening it
would model an ignorance nobody has — and it would break the one thing the ring
is for, since a pilot who is hit and low on fuel cannot use an envelope drawn
6 km off truth. `PlanOverlay.umbrella` is therefore the second thing on the map
drawn precisely at every difficulty (the first is `frontline`), and it is cyan
rather than red because every red circle on the map means "do not go here" and
this one means the opposite.

**`keep_clear` is the invariant, and `build_sanctuary` raises on it.** An area SAM
is a mission-warping object — a Patriot reaches 100 km, further than four of the
six missions' entire ingress — and an umbrella that touches the AO does not give
anybody a refuge, it deletes the mission: the belts the player was briefed to
work around get shot from the other side of the map by an asset nobody planned
the sortie against. The reach comes off the F-16C's own `THREAT_PTS` table in
`core/dtc.py`, the same rows the cartridge is written from, so nobody re-types a
range. Four things learned from wiring it into all six:

- **The two lists are not the same list, and the helper cannot tell them apart
  for you.** Out of *our* umbrella goes whatever the enemy needs left standing
  (the AO, the belts, the EWRs) and nothing else — a CAP station 45 km up the
  axis and a PUSH point 25 km north of the field are *supposed* to be inside it.
  Out of *theirs* goes every friendly station and the whole ingress corridor.
  Passing the CAP station to the blue list is the first thing that failed.
- **On the red side, the objective is often *on* the enemy field**, and then no
  system fits: `eastern_shield`'s depot is the Kuweires apron, `idlib_gauntlet`'s
  convoy off-loads 4 km from Taftanaz, `daryal_run`'s S-300 is 12 km from Beslan,
  `kodori_strike`'s FOB 9 km from Sukhumi. Put the sanctuary on the field the
  **fighters recover to** instead — that is the one it is for. All four missions
  do, and `build_sanctuary` refuses the other choice rather than shipping it.
- **The front line can be the binding constraint.** `idlib_gauntlet`'s Hatay is
  52 km from the Syrian forward line — closer to the player's own field than to
  his target — so a Hawk there stops 2.5 km short of it and is refused. It gets
  NASAMS (15 km, cover for an approach rather than for a fight) and the real
  recovery umbrella is a Hawk at Incirlik 105 km back. Refusing was right: an
  umbrella touching the front would shoot into the ground battle the mission
  spends 90 km of frontage setting up, and the seam would stop mattering.
- **Airfields are on coasts.** Sochi-Adler's threat axis runs out over the Black
  Sea, so the doctrinal 4.5 km offset put the whole battery in the water and
  `snap_units_clear` could not save it — every cell inside its 250 m search
  radius was water too. `_emplace` walks the offset back *along the same
  bearing* (4.5 → 3 → 1.5 → 0 km) rather than sideways, because sideways would
  silently take the battery off the axis it exists to cover. Batumi and Sochi
  both needed it.

`Battery` is a table entry: name, the `dtc.ThreatSystem` its reach comes from,
how to build it, and the self-cueing SHORAD that goes on the field (Avenger for
NATO, 2S6 for Russian — it comes with the area system because the two are not
independent). `HAWK` / `PATRIOT` / `NASAMS` blue, `SA_2` / `SA_3` / `SA_10` red.
The two pydcs `VehicleTemplate` sites hard-code `mission.country("USA")` and
ignore the country handed to them, so those **refuse** any other country rather
than filing a Turkish battery under the USA.

**A primary field and a divert want opposite things from the cartridge**, and
`divert=True` is that one distinction. The primary field is already the flight's
own take-off and landing waypoint — on the route, on the HSD, in the route card's
first and last rows — so a mark on it restates the route; what it adds is the
**marshal leg**, a race-track abeam the field inside the envelope. A divert has
no waypoint anywhere near it, so its **position** is the whole point, and nobody
diverts in order to orbit. Two consequences that were bugs first:

- **The marshal leg has to fit inside its own envelope.** The un-shrunk 14 km leg
  put both ends 18–19 km from a NASAMS battery's launchers — outside the 15 km
  umbrella, which makes the one drawing whose entire purpose is "hold here and
  nothing can reach you" a lie. It now halves until the far end is inside
  `_MARSHAL_FIT` of the envelope, measured **from the battery**, which is offset
  up the threat axis.
- **A ring takes no navigation steerpoint.** `PlanOverlay.umbrella` records its
  own `"umbrella"` mark kind, which `dtc.plan_steerpoints` does not turn into a
  point: a ring is an *area* and its centre is a battery 4.5 km off the runway,
  which nobody needs a bearing to.

That budget is real and it forced a fix in `core/dtc.py`. `plan_steerpoints`
listed all the marks and then all the orbits, so "a mission that cares which
point survives draws it first" — the documented contract — was **false for
anything that was a line**. `daryal_run` flies twenty-one waypoints and has four
of twenty-five slots left; its marshal leg was drawn before every other point on
the plan and dropped anyway, behind a vague CAP area drawn twenty lines later.
`PlanLine.seq` / `PlanMark.seq` now number both lists from one counter and
`plan_steerpoints` interleaves by it, so draw order is a total order.

**`ansariyah_works` is the one mission where the umbrella is a piece of the
briefing rather than a backstop**, and it is worth reading for what a sanctuary
can be made to do. The AO is 279 km away, so a Patriot's 100 km cannot reach
anything the enemy needs left standing and `keep_clear` passes trivially. What it
buys instead is that *every* friendly station fits inside one envelope — the
AWACS at 95 km, the tanker at 78, the CAP at 88, Paphos at 48 — while the red
S-200's briefed ring reaches to within about 90 km of the field. The sixteen
kilometres between those two circles is the only sky in the mission that is
inside our missiles and outside theirs, it is where the tanker and the escort
hold, and it is the whole answer to "why can the CAP not come with me". A
sanctuary sized to make a *band* rather than a refuge; the briefing points at it
and the player can read both rings off the F10 map.

**On the red side it decided where the enemy fighters live.** The natural alert
field was Bassel Al-Assad, 21 km from the briefed coast crossing — and an S-125
there would have reached the corridor, so `build_sanctuary` refuses it. Moving
the alert commitment to Hama, 72 km behind the range, is better doctrine as well
as legal geometry: coastal fighters parked inside the raid's own axis are the
first thing a raid like this kills. When the check refuses a field, the answer is
usually that the field was wrong.

Design rule as everywhere else in `core/`: absolute world `Point` / pydcs
`Airport` and `Country` in, built groups out. Which field, which system and what
the briefing says are the mission's decisions. Every mission states its callsign
and battery as module constants (`_SANCTUARY`, `_SANCTUARY_BATTERY`) and
interpolates them into both briefing views, so the prose cannot drift from the
reach that was actually emplaced.

**Every mission carries one on each side**, with a `## Fall-back` briefing
section, a "not cleared to pursue over `<enemy field>`" ROE line, and the enemy
field's belt in the HSD cartridge and the kneeboard threat block:

| mission | ours | theirs | note |
|---|---|---|---|
| coastal_cover | `BULLDOG` Hawk, Batumi | SA-3 Sukhumi | Kutaisi is 97 km out — a runway, deliberately not claimed as cover |
| kodori_strike | `CASTLE` Hawk, Kutaisi | SA-3 Gudauta | Senaki (37 km) is inside the envelope, so the existing divert became real |
| daryal_run | `RAMPART` Hawk, Vaziani | SA-3 Mozdok | Soganlug newly blue at 8 km; the ace mission needs this most |
| abkhaz_sweep | `BASTION` Hawk, Batumi | SA-3 Sochi | Kobuleti newly blue at 42 km; the briefing had named Senaki, which the mission never made friendly |
| eastern_shield | `REDOUBT` Hawk, Incirlik + `PICKET` NASAMS, Gaziantep (divert) | SA-3 Bassel | 213 km egress, the longest here — Gaziantep is 85 km from the target |
| idlib_gauntlet | `KEEPER` NASAMS, Hatay + `ANVIL` Hawk, Incirlik (divert) | SA-3 Bassel | front line 52 km off Hatay caps the forward umbrella; the Bassel belt joins the Skynet net |
| ansariyah_works | `BULWARK` Patriot, Akrotiri | SA-3 Hama | the only Patriot here, and the only one where 100 km is the *small* number — see below |
| kuban_forge | `PALISADE` Hawk, Senaki | SA-3 Min Vody | Kutaisi 37 km away comes free inside the envelope; the coastal field walked the battery back 3 km to find land |

Two things worth noting about the red half. It needs no scripting to work: DCS AI
already RTBs on bingo (`tasking.apply_ai_difficulty` sets it), so a defended red
field turns "chase him home" into a priced decision using the same missiles as
everything else. And on a mission with an IADS net the red field battery belongs
**in** it — `idlib_gauntlet` adds Bassel as a `Site`, slowest reactions and
shortest `react_range_m` in the net, because leaving it out would make the
airfield belt the one battery in Syria that stays up under a HARM.

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
waypoints.clear_terrain(route, altitudes, overlay=ov)   # the whole route, legs included
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

`clear_terrain(route, altitudes, *, overlay, clearance_m=150.0, sample_m=50.0)`
is the third case, and on a mountain theater it is the one that bites: an
**en-route** altitude a mission typed by hand. Every pydcs altitude is metres
AMSL, so "800 m through the gorge" is 800 m above the *sea*, and `daryal_run`
shipped a route with two of its three valley waypoints inside a mountainside —
one of them by **2.7 km** — under a briefing that described flying up the Daryal
Gorge. Nothing catches that by eye, because the coordinates were raw map metres
and there is no way to read `Point(-200000, 863000)` and see a mountain. Two
consequences worth carrying to any low route:

- **Write the corridor in degrees**, not DCS metres, when its points are real
  places. `daryal_run`'s `_CORRIDOR` is a table of `(name, lat, lng, height
  above the ground)`, so Pasanauri, the Jvari Pass and Verkhniy Lars can each be
  checked against a map — which is also how its briefing's "fly the Georgian
  Military Road" stopped being a claim the route did not honour.
- **Per-waypoint is only half of it.** DCS ramps linearly between waypoints, so
  two points that each clear their own valley floor still draw a chord through
  the spur the river bends around, which is what the Terek does four times
  between Kobi and Balta. `clear_terrain` samples every leg as well as every
  point and lifts the **cheaper end** of any leg that would hit — cheaper,
  because lifting the lower end of every offending leg flattens a descent into a
  cruise at ridge height, and on this mission the descent *is* the terrain
  masking. It only ever raises, so a mission's own numbers are a floor.

`sample_m` defaults to the elevation raster's 50 m cell because sampling coarser
than the data steps over a one-cell spur. What it deliberately does **not**
cover is the leg into or out of a `add_ground_waypoint` steerpoint: that point
carries the target's *elevation* rather than a commanded altitude (the run-in
altitude belongs on the IP), so the ramp to it reads as terrain penetration in
every mission here and is not one.

`set_departure_speeds(m)` fixes the same class of defect one field over.
`add_runway_waypoint` hard-codes `speed = 200 / 3.6` — **108 kt at 300 m AGL**
— and takes no speed parameter, so the first waypoint after rotation ordered
every flight in every mission to fly slower than it can. Measured on
`idlib_gauntlet`'s Pontiac, an F/A-18C at ~19.6 t (two 330 gal tanks, four
GBU-12 on BRU-33 racks, ATFLIR, three AAMs): holding 300 m at 108 kt needs
**CL 2.8** against a CLmax near 1.8, i.e. 19 % below that jet's stall speed.
The AI's answer to an unflyable command is max alpha and full throttle, which
is exactly what it looks like from the ground — the Hornet rotating, standing
on its tail and lighting both burners all the way to the first en-route point.
The helper writes the flight's **own next en-route speed** there, so no
per-airframe table has to be invented and the mission's existing tuning is
what applies; it only ever raises the value, so it is idempotent and a mission
that sets its own departure speed keeps it. The **approach** runway waypoint
carries the same 108 kt and is left alone on purpose: by then the jet is light
and that is roughly its real approach speed, not a stall command, and DCS runs
its own pattern logic off the landing waypoint. **Missions never call it** —
`MissionBuilder.build_miz` does, right after `snap_base_waypoints`.

## Altitude is a threat parameter, and the numbers are in the game

A mission that says "fly low" is making a claim about what the enemy can do, and
DCS will settle it either way. Three facts decide whether the claim survives
contact, and all three are checkable before anything is built.

**Every SAM's floor, ceiling and minimum range are in the install.** The missile
tables under `<DCS>/CoreMods/tech/TechWeaponPack/Database/Weapons/*.lua` carry
`H_min`, `H_max`, `D_min` and `D_max` per round, and they are the honest source
for a briefing line. The S-200's `H_min = 300.0`, `D_min = 17e3`, `D_max = 240e3`
is what `ansariyah_works` is built on: it reaches most of the way from Syria to
Cyprus and it cannot bring a missile below three hundred metres, so that
mission's hard deck is a number out of the game rather than a number that sounded
right. Grep the table before promising the player anything about an envelope.

**DCS has no earth curvature, so there is no radar horizon over water.** A
wave-top run across 250 km of open sea is *not* hidden from a coastal radar the
way it would be in life — line of sight is terrain only, and there is no terrain
out there. So a mission may promise a **floor** ("they cannot shoot you below
three hundred metres") and may promise **terrain masking** (measurable with
`MapOverlay.line_of_sight`), and may not promise concealment over water. The
useful move is to spend the detection rather than deny it: in `ansariyah_works`
the coastal EWR *does* call the crossing, and that call is what rolls the target
convoy and scrambles the alert pair, so the whole second half of the mission is
caused by the player being exactly where he was briefed to be.

**A transition waypoint carries the altitude it starts at, not the one it ends
at.** DCS ramps linearly between waypoints, so a 6,500 m point followed by a 60 m
point 200 km later puts the jet above the floor for nine tenths of the leg — and
the route card then contradicts the ROE printed two inches under it. A descent or
a climb needs a point at each end: `LETDOWN` at cruise and `DECK` at the deck,
and on the way home `CLIMB` at the **deck**, with the climb on the leg after it.
The same arithmetic decides whether the descent is flyable at all — a 6.8 % idle
descent from 6,500 m needs about 95 km of run, which is what fixes where `DECK`
goes. Where the geometry leaves no room, because the threat ring is wider than
the distance available, say so in the briefing and price it rather than drawing a
profile nobody can fly.

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
altitude on the throttle. Fix the speed first — but do not stop there, because
the speed is only ever half of it. **The DCS AI's own take-off and climb-out
routine is high deck angle and both burners lit until it is established, and
no waypoint reaches that.** Three route-level fixes went in against a report of
`idlib_gauntlet`'s Pontiac climbing out in burner — the km/h units, the Hornet's
cruise fraction, the 108 kt departure waypoint — and all three were real
defects that changed nothing about the symptom.

So `apply_threat_reaction` takes `restrict_afterburner` (default `False`, the
DCS default). It is off by default because for most of the package it is
pointless or harmful — an E-3A, a KC-135 and an A-10C have no afterburner to
restrict, and a CAP or interceptor needs one in a merge. Set it on a **heavy
strike flight**, where burner buys nothing and the fuel matters; Pontiac is the
one flight in the project that carries it. The trade is that the flight cannot
accelerate out of a SAM engagement either, so it belongs on a flight whose
route already bends around the live rings (`core/routing.py`), not as a blanket
setting.

The other half is **weight**, and it is the half a mission actually controls.
Pontiac launched at 19.6 t — **83 % of the Hornet's 23.5 t max gross** — on a
**91 km radius** sortie, carrying full internal fuel *plus* two 330 gal wing
tanks, with a tanker on station. It was the only flight in the project with two
external bags; Uzi flies the same 182 km round trip on one centreline 300 gal
and Eagle on one 610 gal. A jet at 83 % of max gross rotates at a high deck
angle and climbs in burner because that is what the weight demands, not because
a waypoint told it to. One centreline bag instead of the wing pair takes 1,140 kg
and two lumps of drag off it. **Check gross weight against the sortie radius the
same way you check speed against the airframe** — a package tanker is not a
reason to launch heavy, it is the reason you do not have to.

Sanity bound: `FlyingType.max_speed` is km/h too, so check every leg against
it. On a **supersonic fighter** a cruise or orbit speed lands at **0.30–0.40 of
`max_speed`**; under ~0.2 is the unit error, over ~0.40 is afterburner. The
band does *not* transfer to subsonic types — their `max_speed` is barely above
their cruise, so an E-3A at 0.86 and an A-10C at 0.72 are correct, and neither
has an afterburner to worry about anyway.

```
F-15C 2650   F-16C 2120   F/A-18C 1950   MiG-29S 2450   Su-27 2500
A-10C 720    E-3A 860     KC-135 980     MQ-9 400
```

**The ratio is the check, not the number.** Getting the unit right is only half
of it: the same km/h is a different fraction of a different jet's ceiling, and
the fast jets are not interchangeable. `idlib_gauntlet`'s Pontiac kept the 800 /
850 km/h that read as a sane cruise for Uzi's F-16C next to it (0.38 / 0.40 of
2120) and flew its whole sortie in burner, because on an F/A-18C — the slowest
fast jet in the fleet at 1950 — the same numbers are **0.41 / 0.44**, and it is
carrying the heaviest configuration any flight here launches with: two 330 gal
tanks, four GBU-12 on BRU-33 racks, ATFLIR and three AAMs. It now flies
680–750 (0.35–0.38, Mach 0.59–0.67, ~280 KIAS). Weigh the loadout as well as the
airframe — a bombed-up jet sits at the bottom of the band, a clean CAP at the
top.

**`Mission.flight_group_inflight` is the same argument with no one watching it.**
Its `speed` is km/h like every other pydcs speed, and unlike a runway start there
is nothing downstream to catch a wrong one: `waypoints.set_departure_speeds` only
rewrites runway waypoints, so an airborne spawn given metres per second holds
that number for its whole first leg and the build says nothing. `ansariyah_works`
shipped its held Hornet pair at 194 km/h — a tenth of the airframe's ceiling —
from a `/ 3.6` that looked like a unit conversion and was the bug.

**Check the last leg as well as the cruise.** `add_runway_waypoint` writes the
*approach* gate at 108 kt and that is left alone on purpose, but the leg **into**
it is flown at that speed, so a route whose last en-route point is 40 NM out
spends twenty minutes of the sortie there. Put a let-down waypoint about 19 NM
from the field — `daryal_run`'s `MTSKHETA` and `ansariyah_works`' `DESCENT` are
both that fix, and on the second it took the mission from 66 to 57 minutes en
route without changing any other number.

Correct per airframe, **never by a blanket ×1.852** — 400 kt is 740 km/h,
which is *above* the A-10C's never-exceed. Where a bare number would be
ambiguous, name the unit like `idlib_gauntlet`'s `_FAC_SPEED_KPH` does. The
pydcs-side gotcha, including the `strike_flight` / `sead_flight` helpers that
pick `max_speed * 0.8` (Mach 1.4 for a Viper) for themselves, is in
PYDCS_REFERENCE.md §4.2.

A one-off audit across all six missions is a short script — build each mission,
walk every flying group, and print each distinct waypoint speed over
`unit_type.max_speed`. That is what found Pontiac after the unit fix had already
gone in, and it is worth re-running after touching any route. **Include the
sub-300 km/h waypoints in that audit.** The first pass filtered them out as
noise and so walked straight past the `200(0.10)` sitting on all thirty flights
— pydcs's hard-coded departure speed, and a worse bug than the cruise numbers
it was looking for (see `waypoints.set_departure_speeds`). A speed under ~0.2 of
`max_speed` is the signal, not the noise.

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

## Data-cartridge helper (project-owned)

[`dtc`](src/dcs_mission_creator/core/dtc.py) puts the briefed SAM rings in the
cockpit. The F-16C draws a surface-to-air envelope on the HSD (and the HAD) from
its **data cartridge**, not from the RWR: up to fifteen pre-planned threat
points in steerpoints 56–70, each with a position, a three-character code and
the system's range and ceiling. DCS reads them from a `DTC/<name>.dtc` JSON file
inside the `.miz` plus a per-unit `DTC` key naming the cartridges that slot
carries — and pydcs writes neither (`Mission.save` writes a fixed set of zip
entries, `Unit.dict` has no `DTC` field).

Feed it what the F10 plan *drew*, not the site's true position:
`PlanOverlay.threat` returns the `(center, radius)` it painted — coarsened and
offset, further out the harder the mission — and `dtc.briefed` turns that into
zero or one threat point, so the cockpit ring and the map ring are the same
claim and the difficulty policy stays in `map_draw.py`. **This matters more here
than on the map**: a pre-planned threat *is* a steerpoint, so a point on the
site's true position is a set of coordinates the player reads straight out of
the DED, and it would undo a reveal the F10 map had just applied:

```python
hsd = dtc.briefed(
    plan.threat(sa6_pos, radius=12_000.0, label="SA-6", icon=StandardIcon.AirDefense),
    dtc.SA_6,
)
...
dtc.arm_hsd_threats(m, hsd, overlay=scene.overlay.overlay)   # a `_load_cartridge` step
dtc.arm_plan(m, plan, overlay=scene.overlay.overlay)         # in the same step
```

- `arm_hsd_threats(m, points, *, name="THREATS", overlay=None)` — builds one
  cartridge, marks it default + `AutoLoad` (the rings are up before the player
  touches the DTE page) and attaches it to every **player-flown F-16C** unit.
  Empty `points` writes nothing — a mission that briefs no ring at all, or one
  whose only air defence moves; more than fifteen points, or no Viper slot to
  load, raises. Every difficulty produces an estimate, so a hard mission gets a
  cartridge like any other and what changes with difficulty is how far its rings
  sit from the launchers. It also
  `record_briefed`s the points on the mission, because **the cartridge is only
  the Viper's copy of the briefed picture** — `core/kneeboard` prints the
  identical list, with the identical coordinates, for whoever is not flying one.
  A package with no Viper calls `dtc.record_briefed(m, points)` directly.
- `briefed(estimate, system, *, label=None)` — pass the **same `label` the
  `plan.threat` call above it was given**. It never reaches the jet (three
  characters, `system.code`); it is what makes the kneeboard call a belt what the
  map calls it. `ThreatPoint.title()` resolves it, falling back to the jet's own
  table name with its `SAM `/`SPAAA `/`AAA ` dialog prefix trimmed;
  `ThreatPoint.hsd_code()` is the three characters (it used to be `label()`,
  which is why the field could not have that name).
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

### The rest of the F10 plan: steerpoints and GEO lines

The cartridge holds two more tabs, and `arm_plan(m, plan, *, overlay,
name="THREATS")` fills both **off the `PlanOverlay` itself** — the map and the
cockpit then say one thing, because they are read from one place. `PlanOverlay`
records every drawing it makes (`plan.lines()` / `plan.marks()`, `PlanLine` /
`PlanMark`) at the position it drew it, so an estimated site stays estimated in
the DED and a site the mission never drew is not in the list to leak. Both
`arm_*` calls fill their own tab of the *same* cartridge — the jet loads one
default — and either may be made without the other.

- **`NAV_PTS`, steerpoints 1–25.** The flight's own route first, then the plan's
  points **in the mission's own draw order** — marks and orbit midpoints
  interleaved by `PlanLine.seq` / `PlanMark.seq`, since that order is what
  decides who survives an oversubscribed tab. It used to list every mark and then
  every orbit, which made the documented "draw it first to keep it" contract
  false for anything that was a *line*: `daryal_run` flies twenty-one waypoints,
  has four slots left, and dropped the marshal leg `core/sanctuary.py` had drawn
  before every other point on the plan, behind a vague CAP area drawn twenty
  lines later. A `PlanOverlay.umbrella` ring takes no slot at all (its own
  `"umbrella"` kind) — a ring is an area and its centre is a battery 4.5 km off
  the runway. The marks that do qualify: the objective as a `TGT`, the mission's text labels (a seam, an
  off-load point), the air defence that moves, a vague enemy area — and one
  steerpoint per **orbit**, at the midpoint of the race-track, because what a
  pilot wants from a tanker station is a range and a bearing to it. Emplaced
  threats are deliberately absent: they are already the pre-planned threat
  points above, and a second copy costs a navigation slot for nothing.
- **The route's steerpoints carry a `TOS`**, and the plan's marks do not —
  nothing scheduled a tanker station or a seam, and `-1` with `isTOSEnabled`
  clear is the editor's own "no time for this point" state. A route point's time
  is the instant the kneeboard's route card prints in its `ETA L` column, since
  both are `dtc.takeoff_zulu_s` plus the elapsed time
  `kneeboard/flightplan.flight_plan` works out — one schedule, and the card's
  zero-wind caveat covers the DED with it. **The clock is zulu, and the card's
  is local**: the editor's own DTC manager builds every time it computes from
  `start_time - SummerTimeDelta * 3600`, because the jet reads zulu, and pydcs
  carries that offset as `Terrain.utc_offset` (Caucasus +4, Syria +3). Reading
  the mission's local `start_time` straight through would put every steerpoint
  time three or four hours out and still look like a plausible time, so the
  route card prints the take-off in both (`08:40L / 05:40Z`) and labels its own
  column `ETA L`. `FIX_Time` stays off everywhere: it is the switch that makes
  the *speed* derived from the times, and the mission tuned those speeds per
  airframe.
- **`GEO_LINES`, steerpoints 31–55.** Twenty-five vertices shared between
  **four** polylines, so this is the scarce tab and the order matters: front
  lines, then a corridor the flight does not itself fly, then orbit tracks with
  whatever is left. A **front line** is what it is really for — the one piece of
  enemy geometry with a shape, drawn precisely at every difficulty for the
  reasons in the map-drawing section, and carried nowhere else in the cockpit —
  which is why it can never be the line that loses. An orbit takes a line only
  because in most missions there is one going spare, and it adds what its
  steerpoint cannot: which way the pattern runs and how long it is. Line index
  is a colour (the editor's own: L1 white, L2 black, L3 red, L4 green), so enemy
  geometry asks for red and the friendly plan for green. A line over its share
  is **thinned**, both ends kept — losing the end of a front line would move it;
  losing a bend only coarsens it.
- **A `route` line the flight itself flies is dropped.** Missions draw the
  corridor they then fly, and the HSD already joins the steerpoints; what
  survives is a lane somebody else flies, which is worth a line.
- **The route wins every budget fight.** Uploading a steerpoint tab *replaces*
  the flight plan DCS put in the cockpit — `mirror_NAV_PTS` is the "do not
  upload" checkbox and defaults to on for exactly that reason — so a plan that
  would push past twenty-five points loses its own marks, never the pilot's
  navigation. `overlay` is required here rather than optional: every point
  carries the terrain elevation under it, and a route at sea level would be
  worse than the mirrored default it replaces.
- **The route is re-read at write time, not when the mission arms it.**
  `build_miz` snaps take-off and landing altitudes and rewrites the departure
  speed *after* `_assemble` returns, so a tab frozen in `_load_cartridge` would
  print the sea-level take-off and the 108 kt those two steps exist to correct.
  Same reason the kneeboard is written last.
- Two player Viper *flights* raise: there is one steerpoint tab and every Viper
  slot loads it, so two routes will not fit.

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

## The package waits for the player (project-owned)

[`join_up`](src/dcs_mission_creator/core/join_up.py) fixes the timing defect
every mission here shipped with: **the AI launched without the player.** pydcs
and the ME both start an AI flight at `TriggerStart`, and a cold or warm Viper
is eight to twelve minutes of alignment, INS, DTE, checklist and taxi. An A-10
pair at the same field is rolling in ninety seconds and is a hundred kilometres
down the route by the time the player rotates — so `coastal_cover` briefed
"escort `Hawg` to the AO" and handed the player a stern chase he could never
close, and `kodori_strike`'s SEAD had rolled the SA-6 back and gone home before
the strike it was clearing for was airborne. Nothing in the route fixes that:
the AI is flying its plan correctly, it just started without him.

So every friendly flight that departs from a field is set uncontrolled with a
queued `StartCommand`, and one `TriggerOnce` per flight pushes it the moment
**any player slot is above 50 m AGL**. The AI then starts up, taxis and takes
off behind a player who is already turning overhead, which is the join-up the
briefing describes. **Missions never call it** — `MissionBuilder.build_miz`
does, right after `_assemble` returns, for the same reason as
`waypoints.snap_base_waypoints`: a flight added later cannot miss it.

Four exclusions, and each one is a way the sweep would otherwise break a
mission rather than fix it:

- **Anything whose job is a station** (`ON_STATION_TASKS` — AWACS, Refueling,
  CAP). All three are defined by somewhere they have to *be* rather than
  somebody they fly with, and all three have to be there before the package
  needs them. The CAP is the one that had to be measured rather than argued:
  `eastern_shield`'s Eagle needs **21 minutes** to reach its station and
  `idlib_gauntlet`'s **14**, against a player who is over his target at 9 — so
  holding a TARCAP leaves the whole ingress uncovered, which is a worse mission
  than the one this fixes. The task name is the mission author's own
  declaration of which kind of flight it is (`patrol_flight` writes `CAP`, a
  strike or an escort does not), so it is what the split reads. This is the one
  exclusion stated as a name rather than derived.
- **A flight that spawns airborne** (`Mission.flight_group` with
  `airport=None`). `idlib_gauntlet`'s Reaper has been over the road since before
  dawn — that is the mission's whole intelligence claim — and an uncontrolled
  aircraft in the air does not start up, it falls.
- **A flight the mission already holds.** `tasking.scramble_on_trigger` and
  pydcs's own `intercept_flight` both set `uncontrolled` themselves, so
  `idlib_gauntlet`'s reserve strike and `coastal_cover`'s Gudauta pair keep the
  release the mission wrote for them; a second push would race it.
- **The enemy**, and it is derived rather than hard-coded: the sweep holds the
  coalition the client slots are on. Red launching on its own clock is the
  mission's threat model, not an oversight.

`launch_immediately(group)` is the per-flight opt-out for what the task name
cannot express — an asset whose head start *is* the point. Nothing uses it yet.

**Any** player slot, not all of them: in a six-slot coop, waiting for the last
pilot to finish an alignment stalls the mission behind whoever is slowest, and
in single-player the two tests are the same. The other half of that is the
fallback — with no client airborne at all (a server with nobody slotted, a
mission opened to look at) the package would sit on the ramp for the whole
sortie, so a `TimeAfter(FALLBACK_S)` is OR'd in and the flight launches on its
own after fifteen minutes.

What is left is exactly the flight the player was briefed to fly *with*, and
across the eight missions that is three: `coastal_cover` `Hawg` (the A-10 pair
the frag says to escort), `kodori_strike` `Weasel` (the SEAD element clearing
the strike's path) and `eastern_shield` `Hawg`. The rest hold nothing, and each
for a reason the mission already had — `idlib_gauntlet`'s `Pontiac` is a
mission-owned reserve and its Reaper is airborne from the start; `abkhaz_sweep`,
`daryal_run` and `kuban_forge` fly with no friendly AI but an AWACS and a
tanker, which is the composition those missions *are*.

The three briefings that describe those flights all read *better* afterwards,
which is the check worth running on any package this touches. Measured on the
built routes, from each flight's own take-off: `coastal_cover`'s `Hawg` was
attacking the column at what is now the player's T-3 — its run was over before
the player was airborne, under a briefing that says "escort `Hawg` onto the
column" — and now rolls in at about T+9 against the player's T+10 on station.
`kodori_strike`'s "`Weasel` rolls back the SA-6 ahead of you" stops being a
ten-minute head start, which was long enough for the belt to re-radiate before
the strike arrived (`core/iads.py` `shutdown_s` is minutes), and becomes the
two or three minutes its own taxi costs: the HARM lands at about T+11 against
`Dodge`'s T+11 on target. `eastern_shield`'s `Hawg` is briefed to hold west of
the AO until the SEAD is done, so a later launch is the same plan with less
loiter.

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
from dcs_mission_creator.core.iads import Listener, Site, arm_iads

arm_iads(
    m,
    [Site(sa6, "SA-6", go_live_percent=150, probability=0.9,
          delay_s=(14, 40), shutdown_s=(280, 400)),
     Site(sa2, "SA-2", go_live_percent=130, probability=0.7),
     Site(ewr, "EWR", role="ewr"),          # a unit, not a group — see below
     Site(sa10, "SA-10", act_as_ew=True, point_defence=sa15)],
    listeners=[Listener(magic, "Magic")],   # who can hear a radar change state
    voice=self._voice,                      # calls are spoken as well as printed
    down_call="Magic: {label} has ceased emissions, site is dark.",
    up_call="Magic: {label} is radiating again, expect it hot.",
    debug=False,                            # Skynet's own live/dark output
    trace=False,                            # our own decisions; follows `debug`
)
```

**Two switches, one per half of the split below.** `debug` is Skynet's — which
sites it took, what it is tracking, every radar going live and dark — and it
prints **on the player's screen** as well as to `dcs.log`. `trace` is this
project's half: which sites were in a position to see a launch, what each
reaction rolled against, how long each stayed off the air, where a
shoot-and-scoot battery drove, and the reason a site was left out of a reaction
entirely (cold, out of reach, no radar left). It goes to `dcs.log` only, one
line per decision under an `IADS/<net name>` prefix — read a sortie back with
`grep 'IADS/' dcs.log` — and it is drawn from the same rolls whether or not
anyone is reading it, so a traced sortie decides what a quiet one would have.
`trace` follows `debug` unless set, so `debug=True` gives both and
`trace=True, debug=False` gives the quiet, log-only one. `idlib_gauntlet`
currently ships with `debug=True` while its net is being tuned; turn it off
before flying it, or Skynet talks over the mission on screen.

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

**A cold battery is in nobody's reaction, so the shot at a dark site needed its
own answer.** The reaction above is decided at the instant of the launch, and a
HARM in **POS** or **EOM** mode is aimed at a *place* rather than at an emitter —
shoot first, let the round arrive on whatever comes up. That made the pre-emptive
shot a guaranteed kill: the battery came on the air into a missile already
tracking and could never react, whatever its dials said. An observed launch now
leaves the whole net on notice for `alert_window_s` (120 s by default, the order
of a HARM's time of flight), and a site coming up inside that window is *told*
about the shot — its `net_relay` share of `probability`, recognition timed from
the moment it came up, one roll per site per shot. It still comes up, which is
the point: being warned is not being told to hide, and whether the transmitter
dies is again a race between the crew's recognition and the missile's remaining
flight. Only an observed launch arms it, so masking a shot from the whole net
still reaches nobody, and `0.0` switches it off.

Per-site dials are the SEAD difficulty statement: `probability` (does this crew
act on a launch it saw), `delay_s` (recognition lag — **tens of seconds**, the
same order as a HARM's time of flight, because nobody in the site gets a launch
warning: the shot must be seen, called down the net and acted on. Single-digit
seconds darken the radar in the first third of the missile's flight and no HARM
ever connects; at these bands the shooter's range at launch decides the duel),
`shutdown_s` (how long it stays dark — minutes, not Skynet's 180 s cap past
impact, so a HARM buys a real working window; repeat fire extends it),
`react_range_m` (how far down the net the launch travels), `scoot_after_s`
(time on the air that compromises the position), `emission_limit_s` /
`emission_pause_s` (how long a look is, and the quiet between them — see below). Both time bands are
drawn triangularly, so the middle of the band is the common case. A suppressed
site is released to *cold*, not hot — it re-radiates only if there is still
something to shoot at.

**A radar radiates in looks, and how long a look is says who is running it.**
"Radars were also forced to operate for only 20 seconds or less to avoid
destruction by HARMs" — Desert Storm, and again over Yugoslavia in 1999, where
the standing rule was no more than about forty seconds from one position. It is
the discipline rather than the reaction that kept batteries alive: a crew already
off the air when the round arrives never had to out-react anything. So a site
takes a look of `emission_limit_s` and then goes quiet for `emission_pause_s`,
and the look comes off **the group's own DCS `Skill`** when the mission does not
state it (`_EMISSION_BY_SKILL`): 20–35 s at `Excellent`, 30–55 s at `High`,
45–80 s at `Good`, and 90–150 s at `Average`, which is long enough that a
conscript battery effectively has no discipline and dies to the HARM a drilled
crew two ridges away would have been off the air for. Twenty seconds is what the
*best* crews of a real campaign managed, so it is the bottom of the élite band
rather than a number everybody gets. `idlib_gauntlet` needed no new arguments for
any of this — its SA-6, SA-8 and SA-11 are already `Skill.High` and its SA-2 and
S-125 belts `Skill.Average`, which is exactly the split its briefing describes.

Two things keep that from breaking the mission. **A look never refuses an
engagement** — Dani's own radar stayed up the extra twenty seconds to finish the
shot that downed an F-117 — so the clock is held while there are missiles in
flight or a target inside the launchers' envelope, and released the moment that
lapses. The test for that is deliberately *not* Skynet's `isTargetInRange`, which
carries `go_live_percent` in it and at 150 % answers "is this site cued" (true of
everything it can see, so the clock would never run); it is the launchers' own
range, firing altitude and remaining rounds. And **an EWR or any `act_as_ew` site
is exempt** unless a band is given explicitly, because a net whose search
coverage works in bursts has nothing to hand a track to — the same invariant
`arm_iads` already warns about.

**Shoot and scoot is two hops, and the useful one happens before the shot.**
An anti-radiation missile in **POS** or **EOM** mode is aimed at a *coordinate*,
so it flies there whether anything is radiating or not: going dark saves nothing
against the shot a competent player actually takes, and only a **stale
coordinate** does. So a battery that has spent `scoot_after_s` on the air since
it last moved (90 s by default, accumulated across stretches because a cued site
flaps on and off at the go-live cycle) relocates the next time it goes quiet — it
must assume it was fixed while it emitted. That is also the doctrine the vehicle
exists for: a battery that only displaces once a missile is already inbound is
not scooting, it is dodging. The bound is the same as the reactive hop's, so the
briefed ring stays honest while the *aimpoint* inside it goes stale.

The reactive hop is the second one, and it is a **grade on the duel** rather than
an escape. Its numbers come from measuring two real HARM flight times out of a
sortie's `dcs.log` (19.75 km → 27.5 s, ~45 km → 56.5 s) against the SA-6's
recognition band, and doing that is what found two things wrong with the band
itself.

**`delay_s` is the band at the edge of `react_range_m`, not a flat number.** Held
flat, a crew was given the same half-minute to notice a launch twelve kilometres
overhead as one sixty kilometres away, and the arithmetic then said a shot from
inside the missile engagement zone was unanswerable: at 20 km the crew reacted
54 % of the time and moved nothing. That is not how the historical crews worked —
a launch close in is a rocket motor and a smoke trail, and in the Gulf a *bogus*
"Magnum" call was often enough to make operators power down, so the trigger was
suspicion rather than observation. The drawn band therefore tightens towards 45 %
of the stated one as the launch closes, floored at six seconds (somebody has to
look up, decide and reach the switch), and a launch the site could not see itself
keeps the slower reading (× 1.3) — being told takes longer than looking. At 20 km
that is 9–25 s instead of 14–40, and the duel becomes a duel: 100 % react, 87 %
get clear of the aimpoint, median 67 m. Inside 12 km it is still a knife fight,
which is right.

**`JOCKEY_SPEED_MS` is 9.0 m/s (~32 km/h)**, because this is a hasty dash off an
aimpoint rather than a road march and a Kub TELAR is good for 40 km/h
cross-country. At the old 5.5 m/s a crew that reacted at the MEZ edge moved twelve
metres, which made the feature invisible exactly where a player looks for it.

**A battery that can drive, drives** — shoot and scoot, `jockey_m`. Ceasing to
radiate saves the *system*, not the vehicle: a HARM remembers where the emitter
was and keeps flying to that point, which is why Skynet's own dark path also
cuts the group's AI (a DCS multiplayer workaround, not a tactic). So a
self-propelled site displaces a few hundred metres when it goes quiet, and the
jockey hands the AI back to make that possible — an AI-off group does not move.
It does not defeat the missile, it **grades the duel**: the hop starts when the
crew reacts, i.e. `delay_s` after the launch, so a shot from 40 km arrives on
ground the battery has left and one from 15 km arrives before it moved at all.
Leave `jockey_m` as `None` and a table decides (`_MOBILE_TYPES` — SA-6/8/11/15,
HQ-7 and the support trucks that come with them); anything else stays put,
because an S-125 fires from built revetments, an S-300PS is march-ordered in
minutes and a 55G6 needs hours. Two compositions are refused outright, and both
arrive by accident from a pydcs template: **infantry** (a DCS group moves at its
slowest member, so a battery walking at 2 m/s has not displaced — `sa11_site`
ships a rifleman) and an **optically guided launcher** (Strela, Tunguska: with
the AI back on it would keep fighting from a site the mission believes is
suppressed). Every hop is drawn from the site's **start** point, never from the
last one, so repeat fire cannot walk a battery out of the ring `PlanOverlay`
drew and `core/dtc.py` loaded — the `_JOCKEY_M_MAX` ceiling sits well inside the
2 km offset `threat` already applies at `trained`.

**A radar going off or back on the air is only reported if somebody could have
heard it.** That is an ESM observation, so `listeners` names the friendly groups
that could make it — a Rivet Joint track, an AWACS with ESM, a ground collection
site — and a call is made only while one of them is alive, inside its own
`range_m` and in **line of sight of the emitter**. Declare none and the net is
silent, which is the honest default and not a broken one: without a collector,
"the SA-6 has ceased emissions" is the mission reading its own trigger state out
to the player, which is exactly what the briefing rules below forbid. The gate is
live, so it is a real condition rather than a build-time formality — a battery
masked behind a ridge from the AWACS track goes quiet without a word, and shooting
the collector down ends the reporting for the rest of the sortie. `arm_iads` warns
when calls are configured with no listener, since the wording is set in Python and
the silence happens in Lua. The default reach is 250 km: a passive receiver against
a megawatt search radar is horizon-limited rather than power-limited, so terrain
and survival are what actually decide it. The briefing has to say whose picture it
is (`idlib_gauntlet`: "both of those calls are `Magic`'s ESM watch, not a
certainty"), or a call that never comes reads as a bug.

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

**Place a ground controller with
[`placement.observation_post`](src/dcs_mission_creator/core/placement.py)**, not
with a concealment helper. A DCS JTAC lases what its *own* sensor sees, so the
constraint that decides whether the feature works at all is line of sight to the
target's ground, and `place_ambush_on_route` / `infantry_treeline` do not carry
one — in `coastal_cover` the ambush helper picked a spot 830 m from the march
route, 38 m below it and behind a rise, which from the cockpit is
indistinguishable from a wrong laser code. Pass **several points spread along
whatever is being watched**: one point is satisfied by any hollow that can see
one point, and measured on that mission's road it produced a post seeing 3.5 km
of it (six minutes of a convoy) where two points 4 km apart forced one seeing
12 km. The visible stretch is then the mission's strike window, so it is worth
measuring and worth saying out loud on the radio.

**Pass `push_at_s`** (mission seconds, just after the controller's check-in
call) unless there is a reason not to. Time it after whatever call announces the
target, not at check-in — the readout answers off a live unit, so it will
happily read out coordinates for something the controller has not yet said he
can see. It reads out the first target's position
once, unprompted, to whoever is in the cockpit and to anyone slotting in later.
Without it the feature is invisible: DCS keeps reading its own grid, the player
has no reason to go looking in F10 → Other, and the mission looks like it never
had the readout. Say where the entry is in the briefing too, and say that the
stock nine-line is a grid — otherwise the two calls look like a bug.

## Kneeboard helper (project-owned)

[`kneeboard`](src/dcs_mission_creator/core/kneeboard/) writes the cards the
player reads with the jet already moving. All of them **derived from the built
mission**, so none can contradict the route, the package or the fields it came
from:

- **flight plan** — **one line per waypoint**, and one table: its position in
  degrees and decimal minutes, the terrain elevation under it, magnetic track,
  leg distance, altitude, commanded TAS, ETE and ETA. Then the **briefed
  threats**; then the departure and recovery fields with their elevation and this
  flight's own parking slot; then the weather the timings were flown against.
- **comms** — your flight, the package, the controllers, each relevant field's
  ATC bands, and the theater navaids. A frequency that happens to be a channel
  on the player airframe's own default preset table is annotated with it
  (`251.000 AM  R1 CH18`), which is the difference between a card that saves
  time and one that lists numbers.
- **airfield**, and this one is **conditional**: it is written only for a field
  the *theatre ships no chart of*. Position, elevation, runways with any measured
  ILS course, navaids with bearing and range, which flight parks where, and a
  north-up plan view.

**One table per waypoint, not two.** The route card used to print the legs and
then repeat the same points as a `STEERPOINTS` list purely to give their
coordinates — a pilot reading his position off one table and his timing off
another, four inches apart, doing by hand the join the card should have done. The
coordinates moved into the route table and the second one went away. Two columns
paid for the 25 characters that took, and both are recoverable from what is still
on the page: cumulative distance (the sortie block prints the route total) and
true track (the page prints the theatre variation next to the magnetic tracks,
so it is one subtraction). Note the general shape of that trade — **a kneeboard
column earns its place by being unrecoverable in the cockpit**, which is also the
whole argument for the threat block and against, say, a second altitude column.

**The threat block prints the briefed picture, and is not a fourth reveal
channel.** It comes from `dtc.briefed_threats(m)` — the estimates
`PlanOverlay.threat` returned, by way of `dtc.briefed`, which is the same list
the F-16C's cartridge was loaded from. So it repeats the F10 plan rather than
adding to it, and the difficulty policy stays in `map_draw.py`: a harder
mission's block prints rings that are wider and in the wrong place, rather than
no block at all. What it adds is the half of the picture no
cockpit holds — the F-16C is the only module in DCS that draws a pre-planned
threat ring, so for every other airframe the briefed coordinates exist nowhere
but here, and even in the Viper an HSD ring is a shape on a scope with no numbers
to read off it. Each row carries the pre-planned steerpoint it occupies
(`dtc.FIRST_STEERPOINT`, 56 upward), the three-character HSD code, the position
in DDM, the system's published range and ceiling, and bearing/range from
bullseye. Sites briefed under one name — a pair of SA-13s — are numbered apart,
because otherwise the coordinates are the only thing telling them apart in a
radio call.

Two mission-side consequences:

- **`arm_hsd_threats` records the points** (`dtc.record_briefed`), so a Viper
  mission gets the block for free. A package with **no Viper** — a Hornet
  mission, whose cartridge keeps threats on the SA page under a different
  descriptor — calls `dtc.record_briefed(m, points)` itself and gets the card
  without the cartridge.
- **Pass `plan.threat`'s own `label` to `dtc.briefed`** (`dtc.briefed(est,
  dtc.SA_3, label="SA-3 north shoulder")`). It never reaches the jet, which has
  three characters and prints `system.code` in them; it is so the card names a
  belt the way the map and the briefing name it, and a pilot cross-referencing
  one against the other reads one name. Unlabelled, a row falls back to the
  jet's own table name with its dialog-box prefix trimmed (`SA-6 'GAINFUL'`).

**A table that does not fit repeats its column headers and numbers its parts**
(`ROUTE (1 OF 2)` / `ROUTE (2 OF 2)`), because a column of figures with nothing
written over it is a column nobody can read, and a route continued overleaf that
does not say so reads as the whole route. `Page.parts()` is the layout as text,
which is what the tests assert on — comparing pixels would not say which page a
row landed on.

**The airfield card is conditional because ED's coverage is.** DCS puts the
theatre's own aerodrome and approach charts on the same kneeboard
(`Mods/terrains/<Theater>/Kneeboard/`), and where there is one, it is a surveyed
drawing and strictly better than anything derivable here — pydcs has no runway
extent at all (see below), so a generated diagram is a centreline through a
reference point with the aprons plotted round it, competing with a real chart two
pages away. But what ED ships is not "all fields":

| theatre  | shipped charts                                          |
|----------|---------------------------------------------------------|
| Caucasus | 21 fields — ground diagram *and* approach chart for each |
| Syria    | **three**: Akrotiri, Incirlik, Beirut. Nothing else.     |
| Marianas | one theatre map, no field at all                        |

So `idlib_gauntlet`'s player, who starts at **Hatay**, had no page about their own
field. [`kneeboard/charts.py`](src/dcs_mission_creator/core/kneeboard/charts.py)
answers the question by looking — matching the airport name against the chart file
names, since that is all the name a chart has and pydcs carries no ICAO to join on
— and the card is written only where the answer is no. Today that is Hatay and
nothing else across the eight missions. **With no install the answer is unknown and
the card is written**, because the two failure modes are not symmetric: a
redundant page costs a page, a missing one costs the player their field's
elevation, its ATC channel and where their jet is parked.

**Missions call none of it.** `MissionBuilder.build_miz` calls
`kneeboard.publish(m, miz_path, overlay=…, title=…)` after the save, for the
same reason `dtc.write_cartridges` runs there (the pages are files *inside* the
package) and after every other finishing step for a second reason: the route
card reads the take-off and landing altitudes `snap_base_waypoints` has just
corrected. The PNGs also land in `<output>/kneeboard/`, next to the README,
because a card that can only be read inside the game cannot be reviewed.

A mission may add free-text lines to the comms card's REMARKS block, for the
few facts that are real but in no field pydcs writes:

```python
from dcs_mission_creator.core import kneeboard

kneeboard.remark(m, f"Hammer 1-1 lases the column on code {_LASER_CODE}.")
kneeboard.remark(m, "Target coordinates in your own cockpit's format: "
                    "F10 -> Other -> Hammer 1-1.")
```

Remarks are **wrapped** to the page rather than clipped, which they were not
until `coastal_cover` put two long ones on the card and lost the halves that
mattered — the laser code survived, "where to find the readout" ran off the
right edge. Continuation lines are indented so a two-line remark still reads as
one item.

The wrap is a **floor, not a licence**: it guarantees nothing is silently
truncated, and the one-line budget in the sanctuary section above is the ceiling
— nothing on this card should need a second line in the first place. Both halves
matter because they fail differently. Without the floor a remark loses its back
half and nobody can tell; without the ceiling every remark is two lines and the
block is prose. The test for a line that will not fit is not "shorten it" but
"which half of this is a fact the page cannot derive, and which half is
*explaining* the fact" — the first stays on the card, the second is briefing
prose and probably already there. `coastal_cover`'s readout line was 109
characters, of which 35 explained that DCS's own nine-line is a grid; its README
had said so all along, so the card lost that clause and kept the menu path. And
verify on the rendered page rather than by counting characters: the width is a
function of the font, so the card is what knows.

Keep that list short anyway. Everything else on the cards is derived
and should stay that way — a remark is prose, and prose goes stale exactly the way the
hand-typed FREQUENCIES block in every briefing was one edit from being wrong.
That block is what these cards make true: the briefings have always said
"Batumi tower: per kneeboard".

**Four things had to be got right, and each of them is a way the feature would
otherwise be quietly wrong:**

- **pydcs's own `Mission.add_aircraft_kneeboard` is not used.** It writes the
  archive entry as `f'{directory}/{page.name}'` where `directory` already ends
  in `/`, so every page lands at `KNEEBOARD/<type>/IMAGES//<name>.png` — an
  empty path component DCS may or may not resolve. The entries are appended
  here with the arcname spelled out, exactly as `core/dtc.py` appends its
  cartridges. Writing them also fixes the timestamp: pydcs would use
  `zipf.write`, which records the file's mtime and mode into the archive, which
  is the problem `core/recon/publish.py` has to pin mtime and mode on disk to
  work around. An explicit `ZipInfo` needs no pin.
- **DCS has no per-flight kneeboard.** A page goes in a folder named after an
  aircraft *type* and every pilot of that type sees it, so a mission with two
  player flights of different airframes gets both route cards in both folders
  and each card names its flight in the title.
- **Timings are zero-wind, and the card says so.** The mission file's wind
  `dir` is one number with two readings — the direction the wind comes from, or
  the direction it blows to — and DCS's editor labels it only `DIR`. A
  wind-corrected heading printed off the wrong reading is out by twice the drift
  angle and looks authoritative; the wind profile is printed as its own block
  instead. At 400 kt against the winds these missions set, the ETE error is
  under six per cent, which is smaller than that mistake.
- **A magnetic track comes from a per-theater table**
  (`flightplan.VARIATION_DEG_EAST`), printed on the page next to the number so it
  can be checked; a theater the table does not cover prints true tracks only,
  which is why that is a lookup and not a default of zero. DCS models one
  declination per map and pydcs carries it nowhere. Note that a **runway
  designator is not a heading you may convert**: `RunwayApproach.heading` is the
  designator times ten, the number painted on the threshold, which DCS carries
  over from real-world charts — applying a variation to it introduces an error
  rather than removing one.

### Navaids come from the installed game

pydcs knows a beacon *exists* and nothing else: `Airport.beacons` is a list of
`AirportBeacon(id='airfield22_3')` — an id, no type, no frequency, no position —
and `Airport.tacan` is `None` for every Caucasus field, Batumi included, which
has one. [`kneeboard/beacons.py`](src/dcs_mission_creator/core/kneeboard/beacons.py)
reads `Mods/terrains/<Theater>/Beacons.lua` out of the install instead — the same
file DCS's own F10 airdrome panel reads — for the ILS, PRMG, VOR, RSBN, TACAN and
homer frequencies, channels, positions and antenna directions. `core/dcs_install.py`
already locates the install for loadouts, so there is nothing new to configure;
with it absent the navaid block comes out empty and the build still succeeds
(`MapOverlay.places` degrades the same way). Nothing from the install is copied
into a generated mission — only numbers computed from it.

Two details decide whether those numbers are right. **`position` is
`{x, altitude, z}`** — north, up, east — so a pydcs `Point` is `(position[0],
position[2])`; reading it as `(x, y)` puts every navaid on the map's equator. And
the **join to an airport is the beacon id**, which carries the airport's own
number (`airfield22_*` is Batumi, pydcs id 22), so a field's navaids are exact
rather than "whatever is within 5 km" — along a shared approach corridor that
would import the next field's outer homer. Only the beacons with no airfield in
their id (an en-route VOR, a standalone RSBN) are matched by distance.

An ILS installation is also **grouped by callsign** (`IVI` and `IVZ` are the two
ends of Vaziani) rather than through `RunwayApproach.beacons`, which is empty at
several fields — Vaziani and Hatay both come through with none. That pairing is
the only **surveyed runway geometry** available offline: the glideslope sits a few
hundred metres in from its threshold and the localizer beyond the far end, so the
bearing between them is the landing course (printed as `ILS CRS 046T`) and the
segment between them brackets the strip.

Runway *length* is nobody's — DCS keeps it in the terrain binary and the F10 panel
reads it through the game's own API — so on the plan view a field with a full ILS
gets its runway drawn solid between the two antennas, and a field without one gets
a **dashed centreline** on the designator heading through the reference point, with
the legend under the sketch saying which of the two the reader is looking at.
Drawing an invented rectangle would look more authoritative than the data behind
it, which is the one thing a kneeboard must never do. Everything else on the plan
view is a surveyed position — parking slots and the reference point from pydcs,
beacons from the install, the flight's own spawn position from the mission — and a
label with nowhere to go is **dropped**, never overprinted: it is in the navaid
table above the sketch either way, and two labels through each other loses both.
Same rule as `core/recon/landmark.py`.

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
    self._load_cartridge(m, scene, briefed_threats, plan=plan)
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

  **The player flight is `mission_kit.player_flight`, not
  `flight_group_from_airport`.** A DCS plane group holds **four** airframes and
  pydcs does not enforce that, it *clamps* —
  `group_size = min(group_size, aircraft_type.group_size_max)`, no warning — so
  six coop slots in one group silently shipped four and the CLI's own
  `--players 6` was a lie. `player_flight` splits the slots
  (`section_sizes`: 5 is `(3, 2)`, 6 is `(4, 2)`, never a four-ship trailed by a
  lone jet), builds each section from the same field with the same `stores`,
  marks the slots and records the groups as **one flight**. The mission then
  gives each section the same route, which is why every `_spawn_player` here now
  ends in a `_route_<callsign>` helper: the corridor is a search against the
  terrain, and two sections searching separately would fly two plans under one
  briefing.

  Three things read that record rather than counting groups, and each was a way
  a split flight would otherwise be quietly wrong:

  - `core/datalink.py` teams the **sections together**, so a six-slot flight sees
    all of itself on the scope rather than splitting into two nets — which is the
    exact blindness that module exists to fix.
  - `core/dtc.py`'s "two player Viper flights" guard asks *what the groups are*,
    not how many: two sections fly one route, so one steerpoint tab still fits.
  - `MissionBuilder.slot_summary(flight)` writes the README's `**Players:**` line,
    so a briefing that says `Dodge` while the slot list offers `Dodge` and
    `Dodge 2` cannot happen. `readme()` holds no `Mission`, so the naming comes
    off `mission_kit.section_names` — the same table the groups were built from.

  A trigger gated on "the player" needs every section: `GroupDead` ANDed
  (`daryal_run`, `abkhaz_sweep` — the loss call must not fire with jets still up)
  and `PartOfGroupInZone` ORed with `condition.Or()` (`idlib_gauntlet`'s seam
  crossing — pydcs's condition list is ANDed, so listing both would hold the call
  until both had crossed). `mission_kit.sections_of(m, group)` hands back the
  section-mates of any flight, and just that flight for one built any other way.

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
entry. That is not free — six separate things had to be pinned, and all six are
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
- `core/kneeboard/publish.py` sidesteps that trap rather than working around it:
  the pages are appended with an explicit `ZipInfo(date_time=…)` (a fixed 1980
  stamp, as in `core/dtc.py`), so the file on disk can have any mtime it likes.
  What still has to hold is that the *pixels* are a function of the mission —
  hence one font from `core/fonts.py` rather than whatever the host has installed,
  and `Image.save` with no `pnginfo`, since Pillow writes a `tIME` chunk when it
  is handed one. The pages are written as RGB for the reason
  `core/recon/publish.py` documents: DCS renders a single-channel PNG in shades
  of red.

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

## Buildings as objectives

Every mission here but one attacks vehicles. A factory, a depot, a hardened shed
is **statics** — `m.static_group(country, name, dcs.statics.Fortification.X,
position=..., heading=...)` — and three things follow that a vehicle objective
never raises.

**`condition.UnitDead` works on a static.** The mission editor's own
`unitsLister` (`<DCS>/MissionEditor/modules/me_predicates.lua`) enumerates
`Mission.unit_by_id`, which holds statics alongside vehicles, so `c_unit_dead`
resolves them and "destroy the building" is a one-line trigger on
`group.units[0].id`. `GroupDead` is the wrong tool — a static group is not a
group in the scripting sense.

**Spacing is the design.** Aimpoints closer together than one weapon's effect are
not separate objectives, they are one objective with extra steps.
`ansariyah_works` puts its three buildings 400–430 m apart precisely so that two
2,000 lb bombs cannot take all three, which is what turns the second bomb into a
briefed decision — this month's production, or next year's — instead of a lucky
pattern. Surround them with compound that is in *no* trigger (tanks, a crane,
containers) so finding the right roof through the pod is a task.

**`conceal_country` covers statics**, and that matters more here than for
vehicles: an unhidden compound shows every building as an icon on the F10 map and
hands the player the whole aimpoint choice before he starts engines.

## Existing missions

- [coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py) —
  Caucasus, trained, ~60 min: an F-16C out of Batumi flying a sortie that
  **changes task three times**, and the reference for a *layered* frag rather
  than a single one. It opens as escort over an A-10C pair working a Russian
  column on the Inguri valley road, becomes a strike when the load the march is
  actually for — a fuel and ammunition detachment a dozen kilometres behind —
  comes down the same road, and becomes a defensive problem when a pair of
  Mi-24Ps is sent after `Pinpoint 1-1`, the ground party lasing it. Two GBU-12s
  against three trucks, and the party is what makes two enough: lose him and the
  pass is self-designated. Carries a **recon still** (`core/recon`) of the
  column, and it is the mission's whole intelligence picture — every claim in
  the briefing is sourced to one Reaper up since first light, which is also why
  the detachment is not in the frame.

  Read it for three things the other five do not do. **Two objectives, priced
  separately**: the column is `Hawg`'s and the detachment is the player's, and
  the end triggers say which is which rather than merging them into a score.
  **A talk-on that is the mechanism, not decoration** — `core/jtac` +
  `tasking.fac_attack_group` on a ground party placed by
  `placement.observation_post`, whose sight line is measured against the
  elevation raster and whose ~12 km of visible road *is* the strike window (the
  party says so on the radio when it opens and when it closes). And **a
  withheld threat aimed at the deviation**: the SA-8 travelling with the
  detachment has no ring, no cartridge point and no place in any friendly route,
  the briefing names the gap and gives a hard release floor above its ceiling,
  and it is late-activated at the moment the party calls the trucks — because
  emplaced from mission start it sat inside `Hawg`'s briefed 4,000 m run, which
  is the difference between fog of war and a bug wearing its costume.
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
- [daryal_run.py](src/dcs_mission_creator/missions/daryal_run.py) — Caucasus
  ace: F-16C SEAD out of Vaziani against an S-300PS south of Beslan, up the
  Georgian Military Road and the Daryal Gorge. The reference for a **route
  planned against the terrain** rather than against the map's coordinate grid —
  `_CORRIDOR` / `_EGRESS` in degrees, altitudes stated as height above the
  ground and put through `waypoints.clear_terrain`. Every corridor point is
  masked from the battery's search radar (measured against the elevation raster
  with `MapOverlay.line_of_sight`); the masking runs out about 16 km short,
  which is where the gorge ends and the pop-up is.
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
- [ansariyah_works.py](src/dcs_mission_creator/missions/ansariyah_works.py) —
  Syria **veteran**, and the only one at that label: F-16C out of Akrotiri
  against a Syrian rocket-motor plant in a basin on the seaward side of the
  Jebel al-Ansariyah, 279 km east with all of it water. The reference for a
  **low ingress whose floor is a number out of the game's own weapon table**:
  the S-200 behind Jableh has `H_min = 300 m`, `D_min = 17 km`, `D_max = 240 km`
  (`<DCS>/CoreMods/tech/TechWeaponPack/Database/Weapons/misc_sams.lua`), so it
  reaches most of the way to Cyprus and cannot touch anything below three
  hundred metres — and the whole sortie, the player's *and* the AI strike pair's,
  is flown under that one figure. Read it for three more things:
  **statics as objectives** (three aimpoints, two bombs, and a briefed choice
  between this month's motors and next year's oxidiser — see below on
  `c_unit_dead`); **detection spent rather than denied**, since DCS models no
  earth curvature and the mission says so, so the coastal radar *does* call the
  crossing and that call is what rolls the load-out and scrambles the alert
  pair; and a **seam between two coastal S-125s** checked against what the
  batteries actually reach (18.1 and 16.4 km of margin) rather than against the
  wider rings the veteran reveal draws (9.7 and 8.2 km).
- [kuban_forge.py](src/dcs_mission_creator/missions/kuban_forge.py) — Caucasus
  **ace**, ~55 min: F-16C out of Senaki against a Russian solid-motor plant on
  the Kuban north of Karachayevsk, flown up the Abkhaz coastal plain, inland
  through the Kodori and over the **Klukhori Pass**. The nearest thing here to
  `ansariyah_works` — statics as objectives, a plant in terrain that denies an
  approach from altitude — and worth reading for where the two diverge. Three
  things it is the reference for. **A low route whose floor is measured, not
  chosen**: at 250 m AGL this corridor needs twenty-three waypoints to keep
  every straight leg out of the rock and at 600 m it needs eleven, and 600 m is
  still masked from the Buk and both EWRs at every point down to `KARACHAY` —
  the massif does the hiding, not the last three hundred metres, and the
  waypoint budget is what makes that worth knowing. **`core/iads.py` used for
  the cueing alone**: the package carries no HARM, so half that module never
  rolls, and the net is there purely so the belts sit cold until the chain hands
  them a track — without it the RWR is full before the coast and two hundred
  kilometres of valley buy nothing. And **low in, high out**: the egress is a
  climb over the range because the valley bought surprise and the halls spend
  it, which is also the only egress the cartridge can afford (the Marukh, the
  one other pass, costs eleven waypoints on its own).

Every one of the eight also ships kneeboard cards — route and comms — built by the
base class from the mission itself (`core/kneeboard`), plus an airfield page
wherever the theatre ships no chart of the field, which across the eight is
Hatay alone — Akrotiri is one of the three fields ED charts on Syria, so
`ansariyah_works` gets no page for its own base either. `idlib_gauntlet` and
`ansariyah_works` are the two that add `kneeboard.remark` lines: Hammer's laser
code and radio-menu path in the first, the hard deck in the second.

The route card's **threat block** prints whatever the mission briefed
(idlib_gauntlet six, ansariyah_works five, kuban_forge four, kodori_strike
three — its two SA-13s come out `SA-13 1` and `SA-13 2`, abkhaz_sweep one,
daryal_run two). The three ace missions and the veteran one print `(approx.)`
estimates, so those cards are as imprecise as the maps they repeat. `ansariyah_works` is also where the briefed
radius earns its override: its SA-5 row prints the 160 km the flight plan was
built from, not the 255 km the jet's own threat table carries for the system.

All eight missions ([coastal_cover](src/dcs_mission_creator/missions/coastal_cover.py),
[kodori_strike](src/dcs_mission_creator/missions/kodori_strike.py),
[eastern_shield](src/dcs_mission_creator/missions/eastern_shield.py),
[idlib_gauntlet](src/dcs_mission_creator/missions/idlib_gauntlet.py),
[abkhaz_sweep](src/dcs_mission_creator/missions/abkhaz_sweep.py),
[daryal_run](src/dcs_mission_creator/missions/daryal_run.py),
[ansariyah_works](src/dcs_mission_creator/missions/ansariyah_works.py),
[kuban_forge](src/dcs_mission_creator/missions/kuban_forge.py)) paint an
F10 briefing plan via a `_draw_plan` step using `PlanOverlay` — see the
map-drawing helper section above. All eight draw estimated threat rings + NATO
icons; what separates them is how far off the estimate is. The four trained
missions (coastal_cover, kodori_strike, eastern_shield, idlib_gauntlet) draw a
`(est.)` ring 2 km off truth; `ansariyah_works` at veteran draws a dashed
`(approx.)` ring a quarter wider and 4 km off; the three ace missions
(abkhaz_sweep, daryal_run, kuban_forge) draw wider again and 6 km off, plus a
vague `threat_area` for the airborne threat, which is not an emplaced envelope
anybody can ring. `kuban_forge` is the one that draws an *enemy* thing
precisely, and states why: a chemical works has been on the 1:100,000 sheet for
forty years and a team has been looking at it for six days, so the plant is a
`waypoint_label` at its true position while every ring around it is an
assessment — the same argument `PlanOverlay.frontline` makes, applied to the
other kind of fixed geography.

All eight then pass the rings their `_draw_plan` drew to a `_load_cartridge`
step (`core/dtc.py`), so the F-16C player's HSD carries the briefed envelopes as
pre-planned threats. The ace pair used to be the exception and no longer is —
loading nothing there did not withhold the sites, it just left the mission with
no coarsened position to build its own steerpoints from, so it used the true
one.

All eight also run a `_conceal_red` step (`conceal_country`) immediately before
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

- **F-16C wingtips (1/9) carry the AIM-120, not the AIM-9** — every mission had
  that pair the wrong way round. Where the Sidewinders go then depends on the
  *fit*, and the two arrangements are mirror images: in a **SEAD or strike** fit
  stations 3/7 are the HARM rails or the bomb stations, so the AIM-9X move out
  to 2/8; in a **pure air-to-air** fit ED fills outboard-in with AMRAAM on
  1/2/8/9 and puts the AIM-9X on 3/7 (`AIM-120C*4, AIM-9X*2, FUEL*2, ECM`).
  `abkhaz_sweep` flies the second; the four F-16 missions with a HARM or a bomb
  on 3/7 fly the first. Note also that **every** ED two-tank F-16C payload
  carries an ALQ-184 on the centreline (station 5), and this project left that
  station empty in all six missions — `abkhaz_sweep` is the only one fixed so
  far.
- **The F-15C never flies a single wing tank.** Its fuel stations are 2 (left
  wing), 6 (centerline) and 10 (right wing); all eleven ED payloads that carry
  fuel use 6, and the wing pair only ever comes as 2 + 10. A lone `(10, tank)`
  is an asymmetric jet.

Also check the loadout against the *sortie*, not only the airframe: a modern
CAS or LGB tasking wants a targeting pod on the jet that needs one — except the
A-10C, whose TGP is integrated (no ED payload lists an AAQ-28, so don't add
one).

## Force balance: the magazine is the budget

**A mission may not task more kills than the player flight is carrying weapons
for**, and the number of player slots is an argument, so both sides of that
have to be computed rather than typed. `abkhaz_sweep` shipped as the worked
example of getting it wrong: a fixed six bandits — four Su-27 and a MiG-29S
pair, all `Skill.Excellent` — against a win condition of "both flights dead",
whatever `--players` said. A single-slot `Dodge` therefore launched with **six
air-to-air missiles against six of the best crews in the game** and a frag that
required all six to die, with no tanker, no rearm and no wingman. Four slots
faced the same six.

The arithmetic that fixes it is short, and the first two terms are facts about
the airframe rather than judgement calls:

- **Count the stations, don't assume.** An F-16C-50 with two wing tanks has
  exactly six air-to-air stations — 1/2/8/9 and 3/7 — because stations 4 and 6
  *accept no missile at all* (read the `PylonN` tables, and see the loadout
  section above). Six is a ceiling, not a choice: there is no F-16C loadout
  that buys a seventh shot without giving up the fuel a 55-minute unsupported
  sortie needs.
- **Two shots per kill** is the planning factor against `Skill.Excellent`
  fighters. So one player jet is worth ~3 kills, and that is the whole budget.
- **Then pick one of three levers** — all three are legitimate, and
  `abkhaz_sweep` now uses the first and the third:
  1. **Scale the opposition off the player count** (`_plan_bandits`), so the
     number of bandits is derived from the magazine rather than chosen.
  2. **Add friendly AI** — but note this trades away mission character: "no
     tanker, no escort, no wingman" *is* the ace composition here, so adding a
     flight was the wrong lever for this mission.
  3. **Task less than the airspace.** Not every enemy has to be a required
     kill. Make the objective the element that gates the campaign effect (the
     Sochi CAP that pins the AWACS track) and let the rest be a threat to
     survive — a reinforcement the player is explicitly cleared to leave
     flying. The win trigger then names only the tasked groups.

Two things follow for the code. A DCS plane group holds **at most four
airframes**, so a scaled element is a *list* of flights and the win condition
ANDs `GroupDead` over all of them (`_split_flights` also refuses to leave a
lone trailer behind a four-ship, since that would gate a win on one jet). And
the briefing has to state which kill is the frag and which is not — "the
Gudauta section is a threat to beat, not a target list" — or a player who
disengages correctly cannot tell a designed off-ramp from a broken trigger.

The same audit is worth running on any mission whose objective is "destroy the
X": count what the package carries, divide by two, and compare. Nothing but
`abkhaz_sweep` has been checked this way yet.

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
