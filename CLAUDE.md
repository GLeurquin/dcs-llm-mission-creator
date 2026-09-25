# Project notes for Claude

A pydcs-based DCS mission generator.

## Where a fact lives

A paragraph goes where its **question** is asked, not where its topic fits.

| Home | Answers | Test |
|---|---|---|
| [README.md](README.md) | *What is this project, and what ships with it?* | Is the reader a human who has just arrived? |
| [PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md) | *What does this pydcs call do?* | Would it still be true if this repo had no missions in it? |
| [SKILL.md](.claude/skills/dcs-mission/SKILL.md) | *What should this mission contain?* | Would changing it change what the mission contains? |
| **This file** | *How do I call this repo's code correctly?* | Does it give me a name, an argument, or an ordering constraint in `src/`? |
| the module docstring | *Why is this code the way it is?* | Is it evidence for a decision already made? |

Rules: **one home per fact**; a document may point at another but not restate
it. The one exception is a *value you have to type* — repeat the value, never
the argument. Pointers run outward from this file, never in a cycle.

> If deleting a sentence would make an agent write **wrong code**, it belongs
> here. If deleting it would only make the agent **unable to argue with the
> design**, it belongs in the module docstring.
>
> A number is *contract* if it is an argument or a limit. A number is
> *evidence* if it is a measurement.

**Read the module docstring before changing the module** — that is where the
reasoning went.

## The base class already does these — never call them from a mission

| Step | Module |
|---|---|
| time and weather applied | class attributes, see *Package layout* |
| the F10 `PlanOverlay` constructed and handed to `_assemble` | `core/map_draw.py` |
| enemy coalition concealed | `core/visibility.py` |
| briefed rings into the cartridge, plan into the steerpoint tab | `core/dtc.py` |
| the briefing panels and sortie name written | — |
| the loadout-split kneeboard remark | `core/loadout.py` |
| the AI package held until a player is airborne | `core/join_up.py` |
| take-off / landing altitudes snapped to the field | `core/waypoints.py` |
| the 108 kt departure gate corrected | `core/waypoints.py` |
| datalink track numbers and team members assigned | `core/datalink.py` |
| every AI flight's radio tuned to the frequency it was briefed on | `core/radio.py` |
| the cartridge file, the kneeboard cards and the recon still written into the `.miz` | `core/dtc.py`, `core/kneeboard`, `core/recon` |

**The base owning a step does not mean a mission cannot change it.**
`_finish_briefing` is a normal method: override it and call `super()` for the
parts you still want. What the base guarantees is that nothing is *forgotten*.

# The contract

## Package layout

Mission scripts go in
[src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) as
`<scenario_slug>.py`, one concrete subclass of `MissionBuilder`
([core/mission_builder.py](src/dcs_mission_creator/core/mission_builder.py))
per module.

**What a mission declares:**

```python
class CoastalCover(MissionBuilder):
    name = "coastal_cover"                 # filesystem slug, matches the filename
    title = "Coastal Cover"                # display name
    difficulty = Difficulty.TRAINED        # the enum, not a string
    terrain = Caucasus                     # the pydcs class, not an instance
    blue_task = "Escort Hawg 1-2 onto ..."  # the two coalition task panels
    red_task = "Push the column through ..."
    start_time = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
    weather = Weather(name="Spring scattered", ...)
```

`difficulty` drives both the F10 reveal and the enemy ROE. `start_time` is
map-local: pydcs serialises hour and minute verbatim and DCS reads the field as
map-local, so `tzinfo` is inert — write the local time you want. **None of these
has a default.** Each is read through a method (`start_time_for(m)`,
`weather_for(m)`, `blue_task_text()`, `red_task_text()`) so a mission that needs
to *compute* one overrides that instead.

**What a mission implements** — three abstract methods and nothing else:

- `_assemble(self, m: Mission, plan: PlanOverlay) -> Assembled` — build the
  whole mission into `m`. Return `Assembled(overlay, briefed_threats)`.
- `_in_game_briefing(self) -> str` — the in-game description panel, plain text.
- `readme(self) -> str` — the README.md content: the mission *briefing* in
  markdown (situation, mission, package, threats, ROE, navigation, frequencies,
  win/loss conditions, re-generation command), not metadata.

Use `self.players` for client-slot counts — `MIN_PLAYERS` to `MAX_PLAYERS`, 2–6,
validated by the base. Build the player flight with `mission_kit.player_flight`,
never a raw `group_size=self.players`.

**What the base owns** is the table above, plus `_permit_crash_recovery`, which
forces `permitCrash` on (`m.forced_options`) so a player who crashes lands back
on slot selection. Nothing else is forced. `generate(output_dir)` seeds the RNG,
then writes `<name>.miz` and `README.md`.

Three of the base's steps run *after* the save: the data cartridge, the
kneeboard cards and the recon still are files **inside** the `.miz`, and
`Mission.save` writes a fixed set of zip entries with no hook for another one.
The kneeboard is last because its route card prints the take-off and landing
altitudes the snap has just corrected.

Each module exposes a one-line `main()`. The argparse lives in
[core/cli.py](src/dcs_mission_creator/core/cli.py) and takes the slug and title
off the class:

```python
def main() -> None:
    run_cli(CoastalCover)


if __name__ == "__main__":
    main()
```

[`__main__.py`](src/dcs_mission_creator/__main__.py) auto-discovers every public
submodule of `missions/` and exposes it as a `generate <name>` subcommand (plus
`list`, `audit`, `survey`, `route`, `map-overlay`). The slug is optional:
`generate` with no name builds **every** mission into its own `<slug>/` folder,
logging past any that raise and exiting 1 if any failed. Default output is
`$DCS_MISSIONS_FOLDER/IAGeneratedMissions/<slug>/`; the CLI errors out if that
is unset and no `--output-dir` is given. `out/` and `*.miz` are gitignored.

## Script structure: small named functions

`_assemble` is the orchestrator, not the implementation. Pattern (see
[coastal_cover.py](src/dcs_mission_creator/missions/coastal_cover.py)):

```python
def _assemble(self, m: Mission, plan: PlanOverlay) -> Assembled:
    """Assemble the mission by calling each step in package order."""
    scene = self._setup_airports(m)
    usa, russia = m.country("USA"), m.country("Russia")

    convoy, _sa13, _ewr = self._spawn_red_ground(m, russia, scene)
    self._spawn_awacs(m, usa, scene)
    hog = self._spawn_strike(m, usa, scene, target_unit=convoy.units[4])
    self._spawn_cap(m, usa, scene)
    self._spawn_red_intercept(m, russia, scene)
    self._spawn_player(m, usa, scene)

    self._add_end_triggers(m, convoy=convoy, hog=hog)
    briefed_threats = self._draw_plan(m, scene, plan=plan, ...)
    return Assembled(scene.overlay.overlay, briefed_threats)
```

Rules:

- Each helper does **one thing** (one flight, one ground cluster, one trigger
  group, one briefing block). Past ~25 lines, split it further (e.g.
  `_spawn_red_ground` -> `_spawn_red_convoy` / `_spawn_red_shorad` /
  `_spawn_red_ewr`).
- The docstring is the design intent, not a restatement of the code.
- Bundle resolved airports + AO center into a `_Scene` dataclass. Type the
  fields as `Airport` (`dcs.terrain.terrain.Airport`) so ty can verify calls
  like `player.land_at(scene.batumi)`.
- Return the groups the orchestrator still needs; name throwaways with a
  leading underscore.
- **A helper whose body has no per-mission payload should not exist.** The test
  is "would a second mission write this differently" — if not, it is mechanism
  and belongs in `core/`.

## Mission scaffolding helpers (project-owned)

None of these holds policy: force composition, timings and text stay in the
mission.

- [`core/mission_kit.py`](src/dcs_mission_creator/core/mission_kit.py) —
  `offset(origin, *, east_m, north_m)` (DCS `x` is north and `y` is east),
  `mark_clients(group)`, `arm(...)`, `race_track(p1, p2)`,
  `unit_of_type(group, type)`, `set_skill(group, skill)`, and the walks every
  core module needs — `flying_groups(m)`, `flying_groups_by_side(m)`,
  `is_client(group)`, `player_groups(m)`. Import these rather than redefining.

  Use `unit_of_type` — never `group.units[0]` — when an objective means "kill
  the radar". pydcs's `VehicleTemplate.Russia.sa10_site` puts a paratrooper at
  index 1, so an index-based win condition silently becomes "kill one
  infantryman"; `unit_of_type` raises instead.

  **The player flight is `mission_kit.player_flight`, not
  `flight_group_from_airport`.** A DCS plane group holds **four** airframes and
  pydcs *clamps* — `group_size = min(group_size, aircraft_type.group_size_max)`,
  no warning. `player_flight` splits the slots (`section_sizes`: 5 is `(3, 2)`,
  6 is `(4, 2)`, never a four-ship trailed by a lone jet), builds each section
  from the same field, arms each **slot** from the flight's own fit table
  (`loadouts=`), marks the slots and records the groups as **one flight**. The
  mission then gives each section the same route — which is why every
  `_spawn_player` ends in a `_route_<callsign>` helper.

  Four things read that record rather than counting groups: `core/datalink.py`
  teams the sections together, `core/dtc.py`'s "two player Viper flights" guard,
  `MissionBuilder.slot_summary` writes the README's `**Players:**` line off
  `mission_kit.section_names` (reachable without a `Mission`), and `mission_kit.slot_names(flight, slots)` — the
  `"<group> Pilot #<n>"` string DCS puts on the slot-selection screen. A five-
  or six-slot flight restarts its pilot numbers at the second section.

  A trigger gated on "the player" needs every section:
  `mission_kit.sections_of(m, group)` hands back the section-mates of any
  flight. AND the `GroupDead`s; OR `PartOfGroupInZone` with `condition.Or()`,
  since pydcs's condition list is ANDed.

- [`core/weather.py`](src/dcs_mission_creator/core/weather.py) — the weather as
  one frozen record. A mission declares it as the `weather` class attribute and
  the base applies it; the record is public so `weather_for(m)` can build a
  different one.
- [`core/triggers.py`](src/dcs_mission_creator/core/triggers.py) — the
  voice-plus-text radio call. **Use these instead of hand-rolling the rule**:
  they take one `text` and use it for both the on-screen `MessageTo*` and the
  TTS render, so the two cannot drift.

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

## Mission Lua lives in `.lua` files

Never embed mission-script Lua as a Python string literal. Every `DoScript`
payload is a real file under
[core/lua/](src/dcs_mission_creator/core/lua/) (e.g. `iads.lua`):

```python
from dcs_mission_creator.core import lua

script = lua.render("iads.lua", SITES=rows, SIDE="coalition.side.BLUE",
                    COOLDOWN="20.0")
rule.add_action(lua.InlineDoScript(script))
```

Attach it with `lua.InlineDoScript`, **never** pydcs's `action.DoScript` —
which parks the Lua in the l10n dictionary and produces a trigger that dies at
mission start (PYDCS_REFERENCE.md §7.1).

`lua.render(name, **subs)` replaces `__KEY__` placeholders with already-formatted
Lua source and raises if a substitution names a placeholder the file lacks or if
any `__PLACEHOLDER__` survives. It does no quoting — build string literals with
`lua.quote(text)`, which renders `None` as `nil` (an empty string would still
pass a Lua truth test). `lua.source(name)` returns the raw text. New `.lua` files
are picked up automatically; `[tool.setuptools.package-data]` ships them.

## Briefings, factions and statics

How a briefing reads, how a narrative names a side and how far apart to space
building aimpoints are **design policy** and live in the `dcs-mission` skill.
What is convention and therefore stays here:

- **Faction names in prose, coalition names in code.** `readme()`,
  `_in_game_briefing()` and every trigger message say USA, USAF, Russia,
  Russian, Syrian. `set_blue()` / `Coalition.Blue` /
  `set_description_bluetask_text` are API names and keep theirs.
- **`_draw_plan` must not draw more precision than the prose claims.**
- **A building objective is `m.static_group(...)`, and `condition.UnitDead`
  resolves it.** The editor's `unitsLister` enumerates `Mission.unit_by_id`,
  which holds statics alongside vehicles, so "destroy the building" is a
  one-line trigger on `group.units[0].id`. `GroupDead` is the wrong tool — a
  static group is not a group in the scripting sense. `conceal_country` covers
  statics, which matters more here than for vehicles.
- **A building is flown to exactly, one steerpoint per building, and the call is
  `waypoints.add_target_waypoint(flight, static_group, ...)`.** It takes the
  built `StaticGroup` rather than a `Point`, so a `PlanOverlay` estimate cannot
  reach it. The reveal policy coarsens what an enemy *system* is assessed to
  reach, and a building reaches nothing. Label it on the F10 map with
  `plan.waypoint_label` at the same position and under the same name — never
  `plan.objective`, which loosens with difficulty — and never one point over the
  compound. `core/audit.py`'s `target waypoint` check enforces the other end:
  every static a trigger condition names has to have a client steerpoint within
  `audit.TARGET_WAYPOINT_M`.

# Helpers, in the order you call them

## Air-defense builder (project-owned)

[`air_defense`](src/dcs_mission_creator/core/air_defense.py) builds a **whole
SAM site** — search radar + track/fire-control radar + command post +
launchers, wired into one `VehicleGroup` — from a single call. It fills only the
sites pydcs's `dcs.templates.VehicleTemplate` lacks; for `sa6/sa10/sa11/sa15` +
`patriot/hawk` call `VehicleTemplate` directly.

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
overlay=None, terrain=None) -> VehicleGroup` — one shared `SiteBuilder`
signature; `launchers=None` means "whatever this system normally fields". Each
site is a `_SiteSpec` table entry, so **adding a system is a table entry, not a
new function**. Pass `overlay` *and* `terrain` together or neither — one alone
warns. Absolute world `Point` in, raw pydcs `Country` in, a built group out.
Positions come from `core/placement.py` (`sam_site_on_ridge`, etc.). Design
rules are in the `dcs-mission` skill; the unit-type catalog in
PYDCS_REFERENCE.md §5.

**Sites are laid out dispersed**, with the search radar and command post pushed
off the position and every placement wobbled, so the axis a stick of bombs is
laid down matters.

`disperse_site(group, *, radius_m, overlay=None, terrain=None)` applies the same
treatment to a group this module did not build — in practice a pydcs
`VehicleTemplate` site. It **inflates** the template's own layout: each unit
keeps its bearing, distances are scaled so the outermost sits at `radius_m`,
then jittered — so **unit order is untouched**, keeping `units[0]` the radar for
every `unit_of_type` objective. Call it *after* any extra units the mission adds
to the group. Every mission that uses a pydcs template does (SA-6 → 300 m,
SA-11 → 400 m, SA-10 → 500 m).

## Somewhere to fall back to (project-owned)

[`sanctuary`](src/dcs_mission_creator/core/sanctuary.py) gives each side a
**defended** place to run to. **Every mission carries one on each side**, with a
`## Fall-back` briefing section, a "not cleared to pursue over `<enemy field>`"
ROE line, and the enemy field's belt in the HSD cartridge and the kneeboard
threat block.

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

Then in `_draw_plan`, **first**:

```python
home.draw(plan)                    # returns [] — our battery is not a threat
return hsd + red.draw(plan)        # returns the pre-planned threat point
```

plus `sanc.remark_all(m, home, red)` and one
`mission_triggers.checkin(..., text=sanc.checkin_text(home, controller="Magic"))`.

**Both sides get one, and what differs is the reveal, not the geometry:**

| | friendly | enemy |
|---|---|---|
| F10 | `PlanOverlay.umbrella` — precise at **every** difficulty | `PlanOverlay.threat` — estimated, per the reveal policy |
| cartridge | the marshal leg / a divert field, as steerpoints | a pre-planned threat point via `dtc.briefed` |
| kneeboard | a REMARKS line naming the cover | the route card's threat block, like any belt |

A battery the player's own side emplaced is not intelligence, so the umbrella is
drawn precisely at every difficulty, and cyan rather than red.

**`keep_clear` is the invariant, and `build_sanctuary` raises on it.** An
umbrella that touches the AO deletes the mission. The reach comes off the
F-16C's own `THREAT_PTS` table in `core/dtc.py`. **The two lists are not the
same list.** Out of *our* umbrella goes whatever the enemy needs left standing —
the AO, the belts, the EWRs — and nothing else. Out of *theirs* goes every
friendly station and the whole ingress corridor. The three ways it refuses are
in `core/sanctuary.py`'s docstring.

`Battery` is a table entry: name, the `dtc.ThreatSystem` its reach comes from,
how to build it, and the self-cueing SHORAD that goes on the field (Avenger for
NATO, 2S6 for Russian). `HAWK` / `PATRIOT` / `NASAMS` blue, `SA_2` / `SA_3` /
`SA_10` red. The two pydcs `VehicleTemplate` sites hard-code
`mission.country("USA")` and ignore the country handed to them, so those
**refuse** any other country.

**`divert=True` is the one distinction between a primary field and a divert.**
The primary field is already the flight's take-off and landing waypoint, so what
a mark adds is the **marshal leg**, a race-track abeam the field inside the
envelope. A divert has no waypoint near it, so its **position** is the point.
Two consequences:

- **The marshal leg has to fit inside its own envelope.** It halves until the
  far end is inside `_MARSHAL_FIT` of the envelope, measured **from the
  battery**.
- **A ring takes no navigation steerpoint.** `PlanOverlay.umbrella` records its
  own `"umbrella"` mark kind, which `dtc.plan_steerpoints` does not turn into a
  point.

Absolute world `Point` / pydcs `Airport` and `Country` in, built groups out.
Every mission states its callsign and battery as module constants
(`_SANCTUARY`, `_SANCTUARY_BATTERY`) and interpolates them into both briefing
views.

The red half needs no scripting — DCS AI already RTBs on bingo
(`tasking.apply_ai_difficulty` sets it). On a mission with an IADS net its
battery belongs **in** the net (`idlib_gauntlet` adds Bassel as a `Site`), or
the airfield belt is the one battery that stays up under a HARM.

## Front-line helper (project-owned)

[`frontline`](src/dcs_mission_creator/core/frontline.py) is the geometry of a
front: the reason a mission's target cannot be attacked from an arbitrary
bearing.

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

Geometry only — no groups, no tasks, like `core/routing.py`. Force composition
is mission policy. `span_m` is the detour a flanker pays; `bow_m` sweeps the
wings toward `facing`; `seam_width_m` is the frontage with no position on it.
Feed the shoulder envelopes into `_threat_rings` so the drawn plan, the
cartridge and the AI routes are one claim, and draw the trace with
`PlanOverlay.frontline` — the one red thing drawn precisely at **every**
difficulty. `TacticalScene.place_frontline` is the different question (a FLOT
meandering between two known anchors).

## AI-tasking helpers (project-owned)

[`tasking`](src/dcs_mission_creator/core/tasking.py) holds the AI verbs that
carry real project policy (carrier/nav beacons stay raw pydcs; PYDCS_REFERENCE.md §6.5):

```python
from dcs_mission_creator.core import tasking

tasking.apply_ai_difficulty(cap, self._difficulty)   # ROE/react/radar/ECM/bingo
tasking.apply_threat_reaction(pontiac)               # friendly: bypass/CM/ECM
tasking.fac_attack_group(jtac, convoy, frequency=133) # JTAC lases target group
tasking.scramble_on_trigger(m, reserve,               # cold-ramp alert-5
                            condition.PartOfCoalitionInZone("blue", zone.id))
```

- `apply_ai_difficulty(group, difficulty)` — maps recruit→ace onto
  `OptROE`/`OptReactOnThreat`/`OptRadarUsing`/`OptECMUsing`/
  `OptRTBOnBingoFuel`/`OptRestrictAfterburner`. A behaviour dial distinct from
  raw `Skill`. Takes `Difficulty` or a str label. It is the **enemy** dial.
- `apply_threat_reaction(flight, *, reaction=ByPassAndEscape, …)` — the friendly
  counterpart: `OptReactOnThreat`, `OptChaffFlareUsing`, `OptECMUsing`, RTB on
  bingo. Every AI flight in a blue package gets it **except AWACS and Tanker**,
  because `ByPassAndEscape` on an orbiting tanker pulls it off station; escalate
  to `AllowAbortMission` for one that should turn around. Pair it with
  `core/routing.py`.
- `fac_attack_group(fac_group, target_group, *, designation=Laser, frequency=…)`
  — derives both the id **and** name pydcs needs (mismatch = silent no-op).
- `scramble_on_trigger(m, group, *conditions)` — sets the flight uncontrolled
  with a queued `StartCommand` and pushes it on the condition(s); generalizes
  `FlyingGroup.delay_start` (time-only) to any condition.

## Threat-aware routing helper (project-owned)

[`routing`](src/dcs_mission_creator/core/routing.py) plans AI routes *around*
the SAM belts. pydcs plans them straight through: `Mission.strike_flight` joins
base → IP → target → base on one line, with an attack waypoint hard-coded to
`alt = 0`. Build the route by hand instead:

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
  around the ring it cuts deepest. Rings covering `start` or `target` are
  **skipped**, which is why the IP comes from `standoff_point` first.
- `standoff_point(target, *, toward, threats, …)` — IP / hold point outside every
  ring, searched outward and swinging round the flank when the direct side is
  covered.
- `ThreatRing(position, radius_m, label)` — build these **once** per mission (an
  `_threat_rings` step) and feed both `_draw_plan` and the routing calls.
  Radar-only sites (EWR) are not rings.

Geometry only, and the rings use the **true** position rather than the drawn
estimate. The behaviour half — `tasking.apply_threat_reaction` — is a separate
call and every routed flight wants both.

## Waypoint-altitude helper (project-owned)

[`waypoints`](src/dcs_mission_creator/core/waypoints.py) puts on the terrain the
waypoints that belong there. pydcs carries no height map: `land_at()` and the
take-off point of an airfield start are hard-coded to `alt = 0`, and a
steerpoint dropped on a ground target keeps the route's ingress altitude. Those
altitudes are the steerpoint elevations the jet's CCRP/CCIP, HUD and DED read:

```python
from dcs_mission_creator.core import waypoints

ov = scene.overlay.overlay                     # the MapOverlay behind TacticalScene
waypoints.add_ground_waypoint(player, scene.route_mid, overlay=ov,
                              speed=750, name="CONVOY AO")
waypoints.snap_base_waypoints(m, ov)           # every flight's take-off + landing
waypoints.ground_elevation_m(ov, point)        # raw lookup, 0.0 outside the overlay
waypoints.clear_terrain(route, altitudes, overlay=ov)   # the whole route, legs included
```

`snap_base_waypoints` walks every flying group. **Missions no longer call it** —
`MissionBuilder.build_miz` runs it after `_assemble` returns and before the save,
which is why `_assemble` returns the overlay. `add_ground_waypoint` is for
**client** routes only: an AI flight flies its route altitudes. Missions with no
other overlay need (`daryal_run`, `abkhaz_sweep`) carry a `load_scene(<theater>)`
handle in their `_Scene` for this.

`clear_terrain(route, altitudes, *, overlay, clearance_m=CLEARANCE_M,
sample_m=50.0)` is the third case: an **en-route** altitude a mission typed by
hand. It only ever raises, and it checks the straight legs as well as the points.
`Leg` + `agl_profile(legs, overlay, *, clearance_m, ground_floor_m=None)` is the
form a low route wants — a `(name, position, agl_m)` table in degrees.
`ground_floor_m=0.0` is for a route with water under it. `sample_m` defaults to
the raster's 50 m cell. It deliberately does **not** cover the leg into or out of
an `add_ground_waypoint` steerpoint: that point carries the target's *elevation*
rather than a commanded altitude.

`set_departure_speeds(m)` fixes the same class of defect one field over.
`add_runway_waypoint` hard-codes 108 kt at 300 m AGL and takes no speed
parameter, which is below a loaded jet's stall speed (PYDCS_REFERENCE.md §4.3);
the AI's answer is max alpha and full throttle. The helper writes the flight's
**own next en-route speed** there; it only ever raises the value, so it is
idempotent. The **approach** runway waypoint carries the same 108 kt and is left
alone on purpose. **Missions never call it** — `build_miz` does, right after
`snap_base_waypoints`.

## Every speed is km/h true airspeed

**Write speeds in km/h and check them against the airframe.** Every pydcs speed
argument — `add_waypoint`, `OrbitAction`, `awacs_flight`, `refuel_flight`,
`patrol_flight`, `intercept_flight`, `flight_group_inflight`, and
`waypoints.add_ground_waypoint` — is **km/h true airspeed**, stored as
`speed / 3.6` m/s. None says so and none validates, so a knots-shaped value
commands roughly **54 %** of the intended speed. The failure it produces is the
friendly package flying its whole sortie in afterburner.

**The ratio is the check, not the number.** `FlyingType.max_speed` is km/h too
(per-airframe table in PYDCS_REFERENCE.md §4.2). On a **supersonic fighter** a
cruise or orbit speed lands at **0.30–0.40 of `max_speed`**; under ~0.2 is the
unit error, over ~0.40 is afterburner — the two bounds `core/audit` checks. The
band does *not* transfer to subsonic types: an E-3A at 0.86 and an A-10C at 0.72
are correct. Correct per airframe, **never by a blanket ×1.852** — 400 kt is
740 km/h, which is *above* the A-10C's never-exceed. Where a bare number would
be ambiguous, name the unit like `idlib_gauntlet`'s `_FAC_SPEED_KPH` does.

Four places the check has to reach:

- **The departure gate**, pydcs's hard-coded 200 km/h (0.10 of `max_speed`) on
  every flight — corrected by `waypoints.set_departure_speeds`. A speed under
  ~0.2 of `max_speed` is the signal, not the noise.
- **`Mission.flight_group_inflight`**, which has nothing downstream to catch it:
  `set_departure_speeds` only rewrites runway waypoints.
- **The last leg as well as the cruise.** The *approach* gate is left at 108 kt
  on purpose, but the leg **into** it is flown at that speed. Put a let-down
  waypoint about 19 NM from the field (`daryal_run`'s `MTSKHETA`,
  `ansariyah_works`' `DESCENT`).
- **Weight, which no waypoint reaches.** So `apply_threat_reaction` takes
  `restrict_afterburner` (default `False`, the DCS default): set it on a **heavy
  strike flight**, never on a CAP or interceptor that needs it in a merge, and
  only on a flight whose route already bends around the live rings.

`core/audit.py` checks every commanded speed against the airframe's own
`max_speed`. Re-run `dcs-mission-creator audit` after touching any route; the
pydcs-side gotcha, including the `strike_flight` / `sead_flight` helpers that
pick `max_speed * 0.8`, is in PYDCS_REFERENCE.md §4.2.

## The package waits for the player (project-owned)

[`join_up`](src/dcs_mission_creator/core/join_up.py) fixes the timing defect
every mission here shipped with: **the AI launched without the player.** A cold
or warm Viper is eight to twelve minutes of startup and taxi while an A-10 pair
is rolling in ninety seconds.

So every friendly flight that departs from a field is set uncontrolled with a
queued `StartCommand`, and one `TriggerOnce` per flight pushes it the moment
**any player slot is above 50 m AGL**. **Missions never call it** — `build_miz`
does, right after `_assemble` returns.

Four exclusions:

- **Anything whose job is a station** (`ON_STATION_TASKS` — AWACS, Refueling,
  CAP). The task name is the mission author's own declaration of which kind of
  flight it is (`patrol_flight` writes `CAP`), so it is what the split reads.
  This is the one exclusion stated as a name rather than derived.
- **A flight that spawns airborne** (`Mission.flight_group` with `airport=None`)
  — an uncontrolled aircraft in the air does not start up, it falls.
- **A flight the mission already holds.** `tasking.scramble_on_trigger` and
  pydcs's `intercept_flight` both set `uncontrolled` themselves.
- **The enemy**, derived rather than hard-coded: the sweep holds the coalition
  the client slots are on.

`launch_immediately(group)` is the per-flight opt-out. **Any** player slot
rather than all of them, with a `TimeAfter(FALLBACK_S)` OR'd in so a server with
nobody slotted launches after fifteen minutes.

`player_airborne(m)` hands back that same release condition list for a mission
that keys something else off it (`roki_shepherd` activates its intruders on
it). Call it after the player flight exists.

## Air-policing helper (project-owned)

[`intercept`](src/dcs_mission_creator/core/intercept.py) is for an enemy
aircraft nobody is cleared to fire on yet: intercept it, shadow it, escort it out
of a zone, and let it maybe turn hostile. It runs as
`core/lua/intercept.lua`:

```python
from dcs_mission_creator.core.intercept import Calls, Track, arm_intercepts

flags = arm_intercepts(
    m,
    [Track(group=zombie, label="Zombie 1", orbit=gori, exit=north_of_roki,
           home=beslan_airport, altitude_m=7500, speed_kph=800,
           hostile_probability=0.35, hostile_after_s=(45, 240),
           deadline_s=1500, calls=Calls(intercepted="Magic: {label} ...", ...))],
    zone=zone_points,          # absolute Points, >= 3, the police zone
    defenders=[eagle],         # friendly AI committed on a hostile, and only on it
    controller=magic,          # whose radio the calls go out on; dead = silent
    voice=self._voice,
)
# flags.done / flags.foul / flags.unchallenged -> condition.FlagIsTrue(...)
```

- **Call it after every group it names exists.** It appends weapons hold to each
  track's and defender's spawn waypoint and **strips the defenders' own engage
  tasks** (`CAPTaskAction`, `EngageTargets`, …) — a defender committed on
  `OPEN_FIRE` with its own CAP task would still shoot a compliant track.
- **`orbit` inside the zone, `exit` outside it** — it raises otherwise, since a
  track whose way home starts inside the zone never resolves.
- **Keep every track's route and orbit out of friendly SAM reach.** A hit on a
  track that has not turned — by anyone on the players' side — sets `foul`.
  `build_sanctuary(keep_clear=...)` is where to enforce it.
- **The per-track calls are Lua-side** (`Calls`, `{label}` templates, spoken
  and printed with one string). The mission's outcome calls go on the three
  flags through `core/triggers.py`. Flags are numbered from `flag_base`
  (default 900).
- The hostile roll happens **in the air**, at each intercept, not at build time.
  `trace=True` (default) logs each decision under `INTERCEPT/<label>`.

## Integrated air-defence helper (project-owned)

[`iads`](src/dcs_mission_creator/core/iads.py) gives radar sites the two
behaviours DCS does not model — a net that decides when to radiate, and a crew
that reacts to being shot at. It ships walder's
[Skynet-IADS](https://github.com/walder/Skynet-IADS) inside the `.miz`:

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

It adds **three** mission-start triggers the first time it is called, in order:
`core/lua/mist_shim.lua`, `core/lua/vendor/skynet-iads.lua` (both as
`a_do_script_file` resources), then the generated setup as an `InlineDoScript`.
A second call reuses the loaded framework.

**Two switches, one per half of the design.** `debug` is Skynet's, and it prints
**on the player's screen** as well as to `dcs.log`. `trace` is ours: `dcs.log`
only, one line per decision under an `IADS/<net name>` prefix
(`grep 'IADS/' dcs.log`). `trace` follows `debug` unless set, so a shipped
mission wants `trace=True, debug=False`.

Per site the mission states `go_live_percent` — a fraction of the system's *own*
reach; go **over 100** or a battery comes up and watches rather than shoots,
since a DCS site needs ~30 s from cold. Plus `engagement_zone`
(`"kill"` / `"search"`), `act_as_ew`, `autonomous`, `point_defence`.

| dial | what it is | band |
|---|---|---|
| `probability` | does this crew act on a launch it saw | |
| `delay_s` | recognition lag — nobody in the site gets a launch warning | **tens of seconds**, the order of a HARM's time of flight |
| `shutdown_s` | how long it stays dark | **minutes**; repeat fire extends it |
| `react_range_m` | how far down the net the launch travels | |
| `scoot_after_s` | time on the air that compromises the position | |
| `emission_limit_s` / `emission_pause_s` | how long a look is, and the quiet between | defaults off the group's DCS `Skill` (`_EMISSION_BY_SKILL`) |

Both time bands are drawn triangularly, and a suppressed site is released to
*cold*, not hot.

Four rules that decide whether the feature works at all:

- **`role="ewr"` registers a unit, `role="sam"` a group** — different classes
  inside the framework. An `"ewr"` group must hold exactly one unit.
- **Something must radiate.** The helper warns when no site is `role="ewr"` or
  `act_as_ew`.
- **Only radar-guided sites.** SA-13 and MANPADS have nothing to shut down, and
  listing a mixed convoy would make the whole column hold fire on every HARM
  shot.
- **`listeners` gates every radio call**, live: alive, in range, in line of
  sight. Declare none and the net is silent; `arm_iads` warns when calls are
  configured with no listener. Calls are queued `announce_spacing_s` apart.

Two behaviours briefings depend on. **Killing the early-warning chain does not
switch the belts off** — a battery with no live parent goes *autonomous*, and
with `autonomous="ai"` (the default, and doctrine) it searches on its own,
radiating continuously. `autonomous="dark"` shuts it down instead, which done to
a whole net makes two HARMs end the SAM threat. And **a look never refuses an
engagement** — the emission clock is held while there are missiles in flight or
a target inside the launchers' envelope, and an EWR or `act_as_ew` site is
exempt unless a band is given explicitly.

`alert_window_s` (120 s) is the answer to the shot at a *dark* site: an observed
launch leaves the net on notice, and a site coming up inside that window is told
about the shot at its `net_relay` share of `probability`. Only an observed launch
arms it, and `0.0` switches it off. `jockey_m=None` lets a table
(`_MOBILE_TYPES`) decide who relocates; **infantry** and **optically guided
launchers** are refused, and every hop is drawn from the site's **start** point
so repeat fire cannot walk a battery out of the ring `PlanOverlay` drew.

## JTAC coordinate-readout helper (project-owned)

[`jtac`](src/dcs_mission_creator/core/jtac.py) fixes the one thing about a DCS
JTAC no mission-editor setting reaches: the coordinate system. The stock 9-line
and target call both go through `MGRS:make(point, 4)` in the game's own
`Scripts/Speech/NATO.lua`, so **every** airframe is read a 4-digit military grid
— useless in an F-16 whose DED takes degrees and decimal minutes:

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
AH-64D, OH-58D; everything else falls through to `default_format=DDM`), so a
player who swaps slots gets the new cockpit's format. Override per mission with
`formats={planes.FA_18C_hornet.id: CoordFormat.MGRS}`. The position is read off a
live unit on each request; the reply is **text only**. This does not replace
`tasking.fac_attack_group` — arm both.

**Place a ground controller with
[`placement.observation_post`](src/dcs_mission_creator/core/placement.py)**, not
with a concealment helper: a DCS JTAC lases what its *own* sensor sees, and
`place_ambush_on_route` / `infantry_treeline` carry no line-of-sight constraint.
Pass **several points spread along whatever is being watched**. The visible
stretch is then the mission's strike window.

**Pass `push_at_s`** (mission seconds, just after the call that announces the
target — not at check-in) unless there is a reason not to. Without it the feature
is invisible. Say where the entry is in the briefing too, and say that the stock
nine-line is a grid.

## Laser codes (project-owned)

[`laser`](src/dcs_mission_creator/core/laser.py) owns the one number a briefing
must not get wrong: **an AI JTAC lases 1688 and nothing else**, and **most
cockpits come up on 1688 too**, because the F-16C, F/A-18C and A-10C carry no
laser-code property at all. Which four airframes *do* expose one is
PYDCS_REFERENCE.md §6.1.

The rule: **one code per mission, and unless every laser weapon in it belongs to
an airframe whose code the mission can write, that code is `DEFAULT_CODE`.**

```python
_LASER_CODE = laser.DEFAULT_CODE      # the mission's one code
laser.set_code(section, _LASER_CODE)  # after `arm`, per flight with a laser weapon
laser.laser_guided_stores(flight)     # which loaded stores ride a spot
```

`set_code` writes the properties where they exist and **refuses** any other code
where they do not; `core/jtac.py` refuses a `CoordTarget.laser_code` that is not
`laser.AI_JTAC_CODE`.

**Then the briefing says the number for the bombs, not only for the spot.** Both
briefing views carry it, and so does the `kneeboard.remark` — pydcs writes the
code into no field.

### The spot has to be up before the jet is there

As DCS ships it, the AI controller's spot lives inside the check-in and the
talk-on, over a set the player has to be tuned to, in range of and in line of
sight of. Every low route in this project denies exactly that.

`laser.arm_autolase` puts the spot on the target from mission start, held on the
nearest vehicle the designator can **see** (`land.isVisible`) inside its own
reach, moved as the target drives, and gone when the team is dead. It reads
nothing about the player. The stock task stays.

```python
laser.arm_autolase(m, [laser.LaserSpot(
    tacp, detachment, code=_LASER_CODE, label="Pinpoint 1-1",
    lead_correction=True,          # the target is driving and there is one pass
)])
```

An IR pointer goes up on the same point by default. `lead_correction` is CTLD's
`laseSpotCorrections` — off by default; both missions where the player takes an
AI spot (`coastal_cover`, `kuban_forge`) carry it. `trace=True` writes each
decision to `dcs.log` under `LASER/<label>`. Why the method is reimplemented
rather than vendored is `core/lua/vendor/README.md`.

Arm this where a **player** takes the controller's laser. `idlib_gauntlet`
deliberately does not: there the laser is for `Pontiac`, an AI Hornet.

It says nothing on the radio, on purpose — every call here goes through
`core/triggers.py`. When the laser comes and goes with the terrain, the mission
says so with its own triggers on the geometry that decides it. And the briefing
has to say the spot is already up, or the feature is invisible.

## Voice-over helper (project-owned)

[`VoiceSynth`](src/dcs_mission_creator/core/tts/synth.py) renders TTS audio
(Piper by default), caches WAVs to `cache/voice/`, registers them on
`mission.map_resource`, and appends the right `SoundTo*` action to a trigger
rule. Every mission instantiates one in `__init__` (`self._voice =
VoiceSynth()`). Text matches the on-screen `MessageTo*` body word-for-word:

```python
self._voice.attach_to_all(m, rule, text)
self._voice.attach_to_coalition(m, rule, text, coalition="blue")   # or "red"
self._voice.attach_to_group(m, rule, text, group_id=group.id)
```

Cache key combines `backend.fingerprint()` + text. Renders are deterministic per
backend; commit the cache only if you want reproducible CI builds (we don't).

For audio played from **mission Lua** rather than a trigger action, use
`self._voice.register(m, text) -> str`: it returns the in-`.miz` file name that
`trigger.action.outSound*(…, "<name>.wav")` expects.

## Map-drawing helper (project-owned)

[`PlanOverlay`](src/dcs_mission_creator/core/map_draw.py) wraps the pydcs F10
drawing API and paints the *plan* on the blue layer. It owns two things the raw
pydcs `Layer` does not: **faction-correct placement** (always the blue
`StandardLayer`) and **difficulty-scaled enemy reveal**.

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
plan.boundary(zone_points, "Police zone")  # our own declared airspace, closed
```

Pass **absolute** world `Point`s — `PlanOverlay` does the layer selection, colour
choice (enemy red / friendly cyan / objective amber), the anchor-relative offset
math and the difficulty policy.

**`.threat()` is the difficulty dial, and what it dials is precision, not
presence**: full icon + true ring on `recruit`, coarse + offset + "(est.)" on
`trained`, wider/dashed/unfilled and further off truth with "(approx.)" on
`veteran`/`ace`. `.objective()` tightens and loosens the same way. **Every
difficulty draws a ring** and hands the estimate back.

Friendly-plan calls (`route`, `orbit`, `waypoint_label`, `umbrella`, `boundary`)
always draw precisely; a `boundary` becomes a GEO line ranked with a front line. Two of those precisions are load-bearing: `umbrella` is our own SAMs
and a pilot who is hit and low on fuel cannot use a refuge drawn 6 km off truth;
`.frontline()` is the one *enemy* call drawn precisely at every difficulty. The
air defence sitting on it still goes through `.threat()` / `.mobile_threat()`.

`.threat()` **returns** the `(center, radius)` it drew, and
`.estimate(center, radius=…)` gives the same pair without drawing. The estimate
is **memoised on the true position**, so the map ring, the cartridge point, the
target steerpoint and the kneeboard line are one object rather than four
guesses. Feed it to `core/dtc.py` via `dtc.briefed`. The rule:

> **Every planned point that refers to an enemy site derives from the estimate,
> never from the site.**

The deliberate exception is `core/routing.py`, whose rings keep a flight alive
rather than telling the player anything. Where the two visibly disagree on the
map, the briefing has to be what explains it.

**A ring is only for something emplaced**: `.threat()` returns its estimate and
`.mobile_threat()` **returns nothing**, so a system that drives cannot end up
frozen into a pre-planned cartridge point. Ground truth: a group with waypoints
is mobile.

`PlanOverlay` also **remembers what it drew** — `plan.lines()` and `plan.marks()`
hand back every polyline and every labelled point at the position it was painted,
which is what `core/dtc.py` turns into steerpoints and GEO lines.

Missions call this in a `_draw_plan` step near the end of `_assemble` and hand
what it returns back as `Assembled.briefed_threats`. Spawn helpers that own
friendly geometry return their `Point`s so `_draw_plan` can annotate them.

[`core/visibility.py`](src/dcs_mission_creator/core/visibility.py) owns the other
half — **what the map does not show**:

```python
from dcs_mission_creator.core.visibility import conceal, conceal_country

conceal_country(russia, syria)   # every group those countries own
conceal(convoy, sa6, reserve)    # or a hand-picked list; None entries skipped
```

Both set `hidden` / `hidden_on_planner` / `hidden_on_mfd` — cosmetic only, the
group still spawns, radiates and shoots. **Missions never call either** — the
base sweeps the whole enemy coalition (`conceal_coalition`, driven off the side
the client slots are *not* on). A mission that wants something left visible on
purpose overrides `_finish_briefing`. The raw pydcs attributes are in
PYDCS_REFERENCE.md §5.

## Data-cartridge helper (project-owned)

[`dtc`](src/dcs_mission_creator/core/dtc.py) puts the briefed plan in the
cockpit. The F-16C draws surface-to-air envelopes on the HSD and HAD from its
**data cartridge**, not from the RWR: up to fifteen pre-planned threat points in
steerpoints 56–70, each with a position, a three-character code and the system's
range and ceiling. DCS reads them from a `DTC/<name>.dtc` JSON file inside the
`.miz` plus a per-unit `DTC` key — and pydcs writes neither.

Feed it what the F10 plan *drew*, not the site's true position:

```python
hsd = dtc.briefed(
    plan.threat(sa6_pos, radius=12_000.0, label="SA-6", icon=StandardIcon.AirDefense),
    dtc.SA_6,
)
# ...then hand `hsd` back as `Assembled.briefed_threats`. The base loads it.
```

A pre-planned threat *is* a steerpoint, so a point on the true position is
coordinates the player reads straight out of the DED.

- `arm_hsd_threats(m, points, *, name="THREATS", overlay=None)` — builds one
  cartridge, marks it default + `AutoLoad` and attaches it to every
  **player-flown F-16C** unit. Empty `points` writes nothing; more than fifteen
  points, or no Viper slot to load, raises. It also `record_briefed`s the points,
  because **the cartridge is only the Viper's copy of the briefed picture** —
  `core/kneeboard` prints the identical list for whoever is not flying one. A
  package with no Viper calls `dtc.record_briefed(m, points)` directly.
- `briefed(estimate, system, *, label=None)` — pass the **same `label` the
  `plan.threat` call above it was given**. It never reaches the jet (three
  characters, `system.code`); it is what makes the kneeboard call a belt what the
  map calls it. `ThreatPoint.title()` resolves it, falling back to the jet's own
  table name with its `SAM `/`SPAAA `/`AAA ` dialog prefix trimmed;
  `ThreatPoint.hsd_code()` is the three characters.
- `ThreatSystem` constants (`dtc.SA_2`, `dtc.SA_6`, `dtc.SA_19`, `dtc.HAWK`, …)
  are the jet's own `THREAT_PTS_defs` rows — `def_num`, exact name, code, range,
  ceiling. Adding a system is a table entry.
- Every `mirror_*` flag is the editor's **"Do not upload tab data"** checkbox and
  defaults to *on*, so `mirror_THREAT_PTS` has to be `False` or the jet takes the
  cartridge and then declines to read the tab. The other tabs stay mirrored on
  purpose.
- **Missions never write the file** — `build_miz` calls `dtc.write_cartridges`
  after `m.save(...)`.

Only **emplaced** systems the briefing names reach the cartridge. EWRs are not
rings either, and neither are ground-force markers. The F/A-18C has a cartridge
too, but its threats are `MEZ_THRTS` on the SA page.

### The rest of the F10 plan: steerpoints and GEO lines

`arm_plan(m, plan, *, overlay, name="THREATS")` fills the two other tabs **off
the `PlanOverlay` itself**. Both `arm_*` calls fill their own tab of the *same*
cartridge and either may be made without the other.

- **`NAV_PTS`, steerpoints 1–25.** The flight's own route first, then the plan's
  marks **in the mission's own draw order**: the objective as a `TGT`, the text
  labels, the air defence that moves, a vague enemy area, and one steerpoint per
  **orbit** at the midpoint of the race-track. Emplaced threats are deliberately
  absent. An `umbrella` ring takes no slot at all.
- **Draw order is a total order.** `PlanLine.seq` / `PlanMark.seq` number both
  lists from one counter and `plan_steerpoints` interleaves by it.
- **The route's steerpoints carry a `TOS`**, and the plan's marks do not. **The
  clock is zulu and the card's is local**; the offset is `Terrain.utc_offset`.
  `FIX_Time` stays off everywhere: it makes the *speed* derived from the times.
- **`GEO_LINES`, steerpoints 31–55.** Twenty-five vertices shared between
  **four** polylines, so this is the scarce tab and the order matters: front
  lines, then a corridor the flight does not itself fly, then orbit tracks with
  whatever is left. Line index is a colour — enemy geometry asks for red, the
  friendly plan for green. A line over its share is thinned with both ends kept.
  **A `route` line the flight itself flies is dropped**.
- **The route wins every budget fight.** Uploading a steerpoint tab *replaces*
  the flight plan DCS put in the cockpit. `overlay` is required rather than
  optional: every point carries the terrain elevation under it.
- **The route is re-read at write time**, not when the mission arms it, because
  `build_miz` snaps altitudes and rewrites the departure speed after `_assemble`
  returns.
- Two player Viper *flights* raise: there is one steerpoint tab.

## Datalink-identity helper (project-owned)

[`datalink`](src/dcs_mission_creator/core/datalink.py) makes a coop flight show
up on its own scopes. Two things the ME writes for every aircraft and pydcs
writes for none decide that:

- **`AddPropAircraft`** carries the identity — the track number (`STN_L16` on
  Link 16, `SADL_TN` on the A-10's SADL) and the callsign the datalink displays
  it under (`VoiceCallsignLabel` + `VoiceCallsignNumber`). pydcs seeds those keys
  from the type's `property_defaults`, where all three are `None`.
- **`datalinks`** is the per-unit network table the four modules with a Datalink
  dialog (F-16C, F/A-18C, A-10C II, AH-64D) read their team members out of.
  pydcs has no field for it.

`assign_datalink_identities(m)` fills both, mission-wide and in group-id order
(deterministic): a unique track number per aircraft in per-flight blocks
(`00101`, `00102`, …, `00201`), the ME's own two-letter tag plus flight digits
off the pydcs callsign (`Springfield11` → `SD` + `11`), and each flight listed as
its own team members. **Missions never call it** — `build_miz` does, right after
`snap_base_waypoints`.

Adding a module is a `_NETS` table entry — its dialog defaults and whether its
team members carry a TDOA flag, both read off
`<DCS>/CoreMods/aircraft/<module>/Datalinks/*.lua`. The AH-64D is deliberately
absent: its IDM has a different shape (`TN_IDM_LB` / `OwnshipCallSign`). Writing
the table needs
[`core/unit_extras.py`](src/dcs_mission_creator/core/unit_extras.py) — a
one-wrapper patch of `FlyingUnit.dict`, shared with `core/dtc.py`. Register with
`emit_unit_key("datalinks", "datalinks")`; **do not** write a second wrapper.

## Radio-frequency helper (project-owned)

[`radio`](src/dcs_mission_creator/core/radio.py) puts a flight's frequency in the
field DCS binds a radio to. `Mission.awacs_flight` and `Mission.refuel_flight`
take a `frequency=` argument and spend it on a `SetFrequency` **waypoint task**,
leaving the group's own `frequency` field on the 251 MHz that
`MovingGroup.__init__` gives every group. The group field is what a player's
radio has to match to raise an AI controller at all.

`tune_working_frequencies(m)` mirrors the intent back into the field,
mission-wide. **Missions never call it** — `build_miz` does, after
`assign_datalink_identities` and before the save. `working_frequency(group)` is
the single resolution both it and the comms card read, most specific source
first — a FAC task's own params, then a `SetFrequencyCommand`, then nothing.

Two things it deliberately leaves alone:

- **A group holding a client slot.** `FlyingGroup.set_frequency` also sets
  `radioSet`, DCS's "this mission overrides the cockpit preset table" flag, and
  the comms card annotates frequencies with their preset channel on the
  assumption nothing overrides it.
- **Ground groups.** A `VehicleGroup` writes no frequency unless `communication`
  is set, and a ground JTAC's radio is the frequency inside its own FAC task
  params, which the card already prints.

The **player flight's own** group field stays at pydcs's 251 — that is the
flight's intra-flight frequency.

## Recon-still helper (project-owned)

[`recon`](src/dcs_mission_creator/core/recon/) renders the imagery a briefing
claims, from the overlay rasters plus the positions of the groups the mission
spawned, and ships it twice: as a `pictureFileNameB` slide inside the `.miz` and
as a PNG beside the README.

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

**It is a radar product, not a photograph, and that is forced by the data.** A
vehicle is a fifth of a pixel at the honest scale, so detections are **symbology
drawn after the sensor chain**, never hot blobs in it. Two rules:

- **Statistics may be modelled; features may not.** Open ground carries a
  correlated roughness field; never draw a feature the overlay does not know
  about.
- **`PlanOverlay.detections` is the only source of positions.** It returns `[]`
  at `veteran`/`ace` and the mission then publishes no frame. Its `trained`
  error is **one registration bias shared by the whole cluster** plus small
  per-return jitter. `bias_m` is **calibration, not policy**: the 1.2 km default
  is for a frame with no landmark in it, and a frame that paints the road net has
  to cut it to a couple of hundred metres.

**A mission gets a still only if the overlay can carry its subject**, which has
to be measured — the overlay has no aeroway layer. `coastal_cover` and
`kodori_strike` carry one; `eastern_shield` cannot.

**Landmarks are what make a still usable.** `landmark_marks(overlay, frame,
avoid=<the marks you are about to draw>)` labels the settlements in a register
deliberately unlike the sensor's own symbology. Three rules, all enforced in
code: only what the raster drew (names from a `places.geojson` sidecar holding
just the OSM place classes `buildings.zarr` was rasterized from); collision is
tested on the **ink**, via `render.mark_extent`; and it is **not a reveal
channel**, so ranking is class then distance to the frame centre.

The sidecar is its own build layer:

```bash
uv run dcs-mission-creator map-overlay build caucasus --layers places
```

It is a `node["place"]` Overpass query per tile — cached beside the main tiles,
resumable — **not** a re-parse of those tiles. `MapOverlay.places` returns `[]`
with one warning when the sidecar is absent. Names come from `name:en` and must
be ASCII: DejaVu Sans Mono has no glyph for several Abkhaz letters.

Also load the column with `road_column`, not from `group.units[*].position`:
pydcs `vehicle_group_platoon` uses `Formation.Line`, which stacks units 20 m
apart **abeam** the heading.

The cache mirrors `VoiceSynth`: `cache/recon/<slug>-<label>-<hash8>.png`, keyed
on the **sampled scene**. The renderer is seeded from that key alone (never
stdlib `random`), and the file's mtime and mode are pinned because `zipf.write`
records both. Missions never copy the file — `build_miz` does.

## Kneeboard helper (project-owned)

[`kneeboard`](src/dcs_mission_creator/core/kneeboard/) writes the cards the
player reads with the jet already moving, all **derived from the built mission**:

- **flight plan** — **one line per waypoint**, and one table: position in degrees
  and decimal minutes, terrain elevation, magnetic track, leg distance, altitude,
  commanded TAS, ETE and ETA. Then the **briefed threats**; then the departure
  and recovery fields with their elevation and this flight's own parking slot;
  then the weather the timings were flown against.
- **comms** — your flight, the package, the controllers, each relevant field's
  ATC bands, and the theater navaids. A frequency that happens to be a channel on
  the player airframe's own default preset table is annotated with it
  (`251.000 AM  R1 CH18`) — the table is `FlyingType.panel_radio`.
- **airfield**, **conditional**: written only for a field the theatre ships no
  chart of. Position, elevation, runways with any measured ILS course, navaids
  with bearing and range, which flight parks where, and a north-up plan view.

**The threat block prints the briefed picture rather than adding to it** — it
comes from `dtc.briefed_threats(m)`. Each row carries the pre-planned steerpoint
it occupies (`dtc.FIRST_STEERPOINT`, 56 upward), the three-character HSD code,
the position in DDM, the system's published range and ceiling, and
bearing/range from bullseye.

Two mission-side consequences:

- **`arm_hsd_threats` records the points**, so a Viper mission gets the block for
  free. A package with **no Viper** calls `dtc.record_briefed(m, points)` itself.
- **Pass `plan.threat`'s own `label` to `dtc.briefed`.** Unlabelled, a row falls
  back to the jet's own table name with its dialog-box prefix trimmed.

**A table that does not fit repeats its column headers and numbers its parts**
(`ROUTE (1 OF 2)` / `ROUTE (2 OF 2)`). `Page.parts()` is the layout as text,
which is what the tests assert on.

**The airfield card is conditional because ED's coverage is.** Where the theatre
ships its own chart (`Mods/terrains/<Theater>/Kneeboard/`) that is strictly
better, so
[`kneeboard/charts.py`](src/dcs_mission_creator/core/kneeboard/charts.py)
matches the airport name against the chart file names (pydcs carries no ICAO to
join on). **With no install the answer is unknown and the card is written.**

**Missions call none of it.** `build_miz` calls `kneeboard.publish(m, miz_path,
overlay=…, title=…)` after the save and after every other finishing step, because
the route card reads the altitudes `snap_base_waypoints` has just corrected. The
PNGs also land in `<output>/kneeboard/`, next to the README.

A mission may add free-text lines to the comms card's REMARKS block:

```python
from dcs_mission_creator.core import kneeboard

kneeboard.remark(m, f"Hammer 1-1 lases the column on code {_LASER_CODE}.")
kneeboard.remark(m, "Target coordinates in your own cockpit's format: "
                    "F10 -> Other -> Hammer 1-1.")
```

Remarks are **wrapped** rather than clipped, and continuation lines are indented.
But one line of `kneeboard.page.COLUMNS` (98) is the ceiling: a remark that needs
a second line should have been shorter. Keep the list short — everything else on
the cards is derived.

**Three things about the mechanism:**

- **pydcs's own `Mission.add_aircraft_kneeboard` is not used** — it writes an
  archive entry with an empty path component (PYDCS_REFERENCE.md §8). The pages
  are appended with the arcname spelled out and an explicit `ZipInfo`.
- **DCS has no per-flight kneeboard.** A page goes in a folder named after an
  aircraft *type* and every pilot of that type sees it, so a mission with two
  player flights of different airframes gets both route cards in both folders and
  each card names its flight in the title.
- **Timings are zero-wind and tracks are magnetic off a per-theater table**
  (`flightplan.VARIATION_DEG_EAST`), both printed on the page. *Why the wind is
  not corrected for, and why a runway designator is not a heading you may
  convert, are in `core/kneeboard/flightplan.py`.*

### Navaids come from the installed game

pydcs knows a beacon *exists* and nothing else: `Airport.beacons` is a list of
`AirportBeacon(id='airfield22_3')` and `Airport.tacan` is `None` for every
Caucasus field, Batumi included.
[`kneeboard/beacons.py`](src/dcs_mission_creator/core/kneeboard/beacons.py)
reads `Mods/terrains/<Theater>/Beacons.lua` out of the install instead.
`core/dcs_install.py` already locates the install; with it absent the navaid
block comes out empty and the build still succeeds. Nothing from the install is
copied into a generated mission — only numbers computed from it.

A label with nowhere to go on the plan view is **dropped**, never overprinted.
Same rule as `core/recon/landmark.py`.

# Loadouts

## DCS installation (loadouts)

pydcs reads stock payloads out of the **installed game**, and finds it only
through the Windows registry — under WSL that fails, so
`load_task_default_loadout()` silently leaves every pylon empty.
[`core/dcs_install.py`](src/dcs_mission_creator/core/dcs_install.py) fixes that
from the `DCS_INSTALL_DIR` env var; `MissionBuilder.__init__` calls
`dcs_install.configure()` before any flight exists (pydcs caches its payload
directories on first use).

```bash
export DCS_INSTALL_DIR="/mnt/e/Games/DCS World OpenBeta"   # 'E:\Games\…' also accepted
# optional: only if the Saved Games folder isn't auto-found
export DCS_SAVED_GAMES_DIR="/mnt/c/Users/<user>/Saved Games/DCS.openbeta"
```

Unset, the helper logs a warning and the DCS task defaults come back empty.
Liveries stay at the DCS default even when set — pydcs's livery scanner splits
paths on `\` and cannot be used off Windows.

**Arm every blue flight explicitly — never rely on the task default.** Use
[`mission_kit.arm`](src/dcs_mission_creator/core/mission_kit.py), which clears
the stations first:

```python
arm(player, planes.F_16C_50, [
    (1, "AIM_120C_AMRAAM___Active_Radar_AAM"),
    (2, "AIM_9X_Sidewinder_IR_AAM"),
    (3, "AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_"),
    (4, "Fuel_tank_370_gal"),
    (10, "AN_ASQ_213_HTS___HARM_Targeting_System"),
])
```

Write the list at the spawn site, not in a shared catalogue. The `PylonN` classes
on each `PlaneType` enumerate what a station legally takes, so read the names off
pydcs rather than guessing; a wrong one is an `AttributeError` at build time.

**Legal is not realistic.** Three station facts, all legal in pydcs and all
wrong:

- **F-16C wingtips (1/9) carry the AIM-120, not the AIM-9.** In a **SEAD or
  strike** fit 3/7 are the HARM rails or the bomb stations, so the AIM-9X move
  out to 2/8; in a **pure air-to-air** fit ED fills outboard-in with AMRAAM on
  1/2/8/9 and puts the AIM-9X on 3/7.
- **Every ED two-tank F-16C payload carries an ALQ-184 on the centreline**
  (station 5).
- **The F-15C never flies a single wing tank.** Its fuel stations are 2, 6 and
  10; every ED payload that carries fuel uses 6, and the wing pair only ever
  comes as 2 + 10.

Also check the loadout against the *sortie*: a modern CAS or LGB tasking wants a
targeting pod on the jet that needs one — except the A-10C, whose TGP is
integrated (no ED payload lists an AAQ-28, so don't add one).

## Loadouts are split across the flight (project-owned)

**Every mission is built for at least two coop slots**
(`MissionBuilder.MIN_PLAYERS`), and the two slots do not carry the same jet.
[`loadout`](src/dcs_mission_creator/core/loadout.py) is the table that says who
carries what.

The reason is arithmetic. An F-16C-50 has eleven stations, and with the bags on
4/6, the ALQ-184 on 5 and the pods on 10/11, what is left is 1/2/3/7/8/9 — of
which **1/2 and 8/9 take a missile and nothing else**. So *three* stations decide
the sortie.

```python
from dcs_mission_creator.core import loadout

_FITS = (
    loadout.Loadout(
        role="HARM/HTS",                       # short — the kneeboard prints it
        carries="two AGM-88C, HTS pod, four AIM-120C, ALQ-184, two 370 gal",
        stores=((1, _AMRAAM), ..., (10, _HTS), (11, _TGP)),
    ),
    loadout.Loadout(
        role="CBU-97*4",
        carries="four CBU-97 SFW on TERs, LITENING pod, ...",
        stores=(...),
    ),
)

sections = player_flight(..., slots=self.players, loadouts=_FITS)
```

`player_flight` **cycles the table over the slots in order**, across sections
rather than restarting per section. Two slots are the complementary pair the
mission was written for; four are two elements each carrying the same split. Slot
1 always gets the first fit, so the briefing can name who is carrying what
without holding the built mission.

**The fits are written per unit.** `FlyingGroup.load_pylon` writes the same store
to every airframe in the group; `loadout.arm_unit` is the per-slot half, and it
clears the stations first. `mission_kit.arm` is still the right call for an **AI**
flight.

**Three views, one table.** The fits are a module constant so `readme()` — which
holds no `Mission` — can render them too:

- `self.loadout_table(flight, _FITS)` — the README's markdown table, one row per
  slot, with the slot column holding the `"<group> Pilot #<n>"` string.
- `self.loadout_brief(flight, _FITS)` — the same table as plain text for
  `set_description_text`, wrapped narrow.
- the **kneeboard remark**, written by `MissionBuilder._remark_loadouts` for
  every mission: `Dodge fits: #1/#3 HARM/HTS; #2/#4 CBU-105*4`. Slots are grouped
  by fit rather than listed one per slot, because a six-slot flight listed
  individually runs past the card's 98 columns.

`role` is a dozen characters, named after the weapon that decides the job. **How
to choose the split** is SKILL.md's *The flight splits its loadout*.

`loadout.air_to_air_shots` counts the missiles off the loaded stores, and
`MissionBuilder.air_to_air_shots(_FITS)` sums them over the assignment.

## Legal is not realistic (project-owned)

[`loadout_check`](src/dcs_mission_creator/core/loadout_check.py) reads ED's own
payload tables out of the installed game and answers "which stations does the
game actually hang this store on". pydcs only checks that a station *accepts* a
store.

```python
for note in loadout_check.check(planes.F_16C_50, fit.stores):
    print(note)   # station 11: Sniper ATP — no shipped payload carries it;
                  # the game ships AN/AAQ-28 LITENING here
```

**A note names what the game ships on that station instead**, which is what
separates a swapped pair from a sub-variant substitution. With no
`DCS_INSTALL_DIR` every check returns empty, and the audit says so out loud.

## Force balance: the magazine is the budget

**A mission may not task more kills than the player flight is carrying weapons
for**, and the number of player slots is an argument, so both sides have to be
computed rather than typed.

- **Count the stations, don't assume.** An F-16C-50 with two wing tanks has
  exactly six air-to-air stations — 1/2/8/9 and 3/7 — because stations 4 and 6
  *accept no missile at all*.
- **Two shots per kill** is the planning factor against `Skill.Excellent`
  fighters. So one player jet is worth ~3 kills, and the flight's budget is the
  sum over its slots — **not** three times the slot count once the fits differ: a
  Viper carrying HARMs or bombs on 3/7 is worth two. Count it with
  `MissionBuilder.air_to_air_shots(_FITS)`.
- **Then pick one of three levers**, all legitimate:
  1. **Scale the opposition off the magazine** (`abkhaz_sweep._plan_bandits`).
  2. **Add friendly AI** — but this trades away mission character: "no tanker, no
     escort, nothing else airborne" *is* the ace composition in some missions.
     The player's own wingman is not this lever; he is already priced in.
  3. **Task less than the airspace.** Make the objective the element that gates
     the campaign effect and let the rest be a threat to survive. The win trigger
     then names only the tasked groups.

Two things follow for the code. A DCS plane group holds **at most four
airframes**, so a scaled element is a *list* of flights and the win condition ANDs
`GroupDead` over all of them (`_split_flights` also refuses to leave a lone
trailer behind a four-ship). And the briefing has to state which kill is the frag
and which is not.

The air-to-air half is mechanical (`air_to_air_shots`); the **air-to-ground half
still has to be counted by hand**.

# Tools, in the order you run them

| # | Ask | Tool |
|---|-----|------|
| 1 | Is this **layout** legal — does anything reach anything it should not? | `survey` |
| 2 | Can an aeroplane **fly** the line between those places? | `route` |
| 3 | — write the mission — | |
| 4 | Is the **built** mission internally consistent? | `audit` |

**Survey before you route, route before you write, audit before you say it is
done.**

## Siting a mission (project-owned)

[`survey`](src/dcs_mission_creator/core/survey.py) answers **where does
everything go, and does that layout hold together?**

```bash
uv run dcs-mission-creator survey syria \
  --point "TARGET@35.1640,36.0860" --point "FEET DRY@35.2100,35.9300" \
  --site "S-125 Tartus@34.9000,35.9200:18000" \
  --site "SA-8 works@35.1550,36.0760:10300" --defends "SA-8 works" \
  --agl 150 --difficulty veteran
```

```python
from dcs_mission_creator.core import survey

for spot in survey.spots(overlay, anchor, 20_000.0, require, count=8):
    print(spot.row())          # pasteable degrees + elevation, slope, road, name
print(survey.report(survey.reaches(overlay, points, sites, agl_m=150.0)))
survey.covered(rows)           # the list worth a non-zero exit code
```

- **A margin is measured against what a system reaches, not against the ring the
  map draws.** The report prints both, off `map_draw.reveal_policy`.
- **The objective's own defences are declared (`--defends`), not inferred.**
  Geometry cannot tell the Osa emplaced on the works from the belt that reaches
  it by accident.
- **`spots` ranks and describes; `find_placement` samples and returns bare
  points.** Both are reproducible (the sampling is seeded from the overlay).

## Planning a low route (project-owned)

[`route_plan`](src/dcs_mission_creator/core/route_plan.py) answers the three
questions a mountain corridor has to answer before a line of the mission is
written.

```bash
uv run dcs-mission-creator route caucasus \
  --via 42.42,41.90 --via 42.90,41.55 --via 43.24,41.745 --via 43.86,41.90 \
  --threat "SA-11@43.984,41.884" --threat "EWR@43.991,41.970" --speed 680
```

```python
from dcs_mission_creator.core import route_plan

route = route_plan.plan_corridor(overlay, [senaki, kodori, klukhori, works])
print(route.table(("PUSH", "OCHAMCHIRA", "KLUKHOR", "TARGET"), speed_kph=680))
for look in route_plan.sighting(overlay, route.points, [(sa11, "SA-11")],
                                altitudes_m=route.altitude_m):
    print(look.summary())          # "first seen at point 13, 33 km out"
route_plan.nav_headroom(len(route.points))    # marks left in the DTC
```

**`--via` is the steering and the rest is measurement.** Between the anchors a
least-cost search over the elevation raster (cost rises as the cube of the
ground) finds the valley, then the trace is thinned to the fewest waypoints whose
*straight legs* still clear, by asking `waypoints.leg_violation` which leg goes
deepest into rock. The output is a paste-ready `_CORRIDOR` table in degrees.

- **The real question is "how low for a waypoint budget I can afford".** `--agl`
  is the dial; raising the mountain band is what buys back waypoints.
- **`sighting` is the half that cannot be argued.** Line of sight against the
  raster. It answers only about terrain — there is no earth curvature, so a
  mission may promise masking and may not promise a horizon.
- **`nav_headroom` is the question nobody thinks to ask until the cartridge is
  full.** The route wins every budget fight in `core/dtc.py`.

## Auditing a built mission (project-owned)

[`audit`](src/dcs_mission_creator/core/audit.py) checks the mechanical half of
the rules in this file. **Every check exists because the project shipped the
mistake it looks for.**

```bash
uv run dcs-mission-creator audit kuban_forge     # or omit the slug for all of them
```

It runs against `MissionBuilder.assemble` — the mission exactly as it would be
written, finishing steps and all, minus the save. Exit code is non-zero if
anything came back at `error`, so it works as a gate.

What it checks: commanded speeds against each airframe's own `max_speed`, the
departure gate `set_departure_speeds` is supposed to have corrected, take-off and
landing points sitting on the field, **client** routes against the terrain at
every waypoint and along every leg, the cartridge's navigation headroom, enemy
groups left visible on the F10 map, flights with empty pylons, stores on stations
the game itself does not use, the player flight's air-to-air magazine against the
number of enemy aircraft, and every building objective for a client steerpoint
standing on it. That last one needs no declaration from the mission: a static
named by a trigger condition *is* an objective.

**Findings, not failures.** Several are heuristics about design and a mission is
allowed to be deliberate about any of them — the check names the deviation, the
mission answers it in prose. The deliberate cases should be the only ones left.

# Repo hygiene

## Reproducibility

Building the same mission twice produces the same `.miz` **contents**, entry for
entry. Six things had to be pinned:

- `MapOverlay` carries the sampling `seed` (default 0). `find_placement` takes no
  per-call seed; build the overlay with a different one to resample.
- `MissionBuilder.generate` seeds the stdlib `random` from the mission slug.
- `MissionBuilder._pin_runway_waypoint_distance` — pydcs declares
  `add_runway_waypoint(..., distance=random.randrange(6000, 8000, 100))`, a
  **default argument**, so the value is drawn once when `dcs.unitgroup` is
  imported, before any seeding can run.
- `MissionBuilder._pin_onboard_numbers` — pydcs picks tail numbers with
  `set.pop()` over a set of strings, which follows string hashing.
- `core/recon/publish.py` pins the rendered PNG's **mtime and mode**, because
  pydcs stores a resource as a *path* and writes it at save time with
  `zipf.write`. (The voice cache has the same exposure and no pin.)
- `core/kneeboard/publish.py` sidesteps that trap instead: the pages are appended
  with an explicit `ZipInfo(date_time=…)` (a fixed 1980 stamp, as in
  `core/dtc.py`). What still has to hold is that the *pixels* are a function of
  the mission — hence one font from `core/fonts.py` and `Image.save` with no
  `pnginfo`, since Pillow writes a `tIME` chunk when handed one. The pages are
  RGB because DCS renders a single-channel PNG in shades of red.

**The archive itself is not byte-identical, and never has been.** `Mission.save`
writes five entries with `zipfile.writestr`, which stamps each with the *current
time*. Compare entry contents, not file hashes — which is what
`test_generation_is_reproducible` does.

## Tests

`tests/` runs without a DCS installation **and** without the built map overlay,
because CI has neither:

- Default selection (`pytest -m "not slow"`) is what CI and pre-commit run.
- `@pytest.mark.slow` is for anything needing the overlay — currently only
  `tests/test_mission_smoke.py`, which skips itself when the overlay is absent.
- A `Mission(Caucasus())` is cheap and needs neither, so core helpers that take a
  mission (`air_defense`, `triggers`, `weather`) are tested normally.
- **Do not assert on mission content.** The smoke test checks that every mission
  builds a readable `.miz` and builds reproducibly, not what is in it.
- The three tool modules are worth copying from: `test_route_plan.py` stands up a
  synthetic elevation array with the **real** row/column transform on it;
  `test_audit.py` builds two-point missions by hand; `test_loadout_check.py`
  parses a fixture payload block instead of the install.

## Running

(rely on the `DCS_MISSIONS_FOLDER` and `DCS_INSTALL_DIR` env vars being
available)

```bash
uv run python -m dcs_mission_creator.missions.coastal_cover --players 2

# or via the unified CLI (auto-discovers every mission module):
uv run dcs-mission-creator list
uv run dcs-mission-creator generate coastal_cover --players 2

# build without saving and report what looks wrong (seconds, not a minute)
uv run dcs-mission-creator audit coastal_cover

# check a layout before anything is written around it (exit 1 = something reaches
# something it should not)
uv run dcs-mission-creator survey syria --point "TARGET@35.164,36.086" \
  --site "S-125 Tartus@34.90,35.92:18000"

# plan a corridor the terrain allows, before writing any of it
uv run dcs-mission-creator route caucasus --via 42.42,41.90 --via 43.86,41.90
```

## Lint and type-check

Always run all four after editing Python under `src/`:

```bash
uv run ruff check src/ tests/          # lint
uv run ruff format --check src/ tests/ # formatting check (use `format` to apply)
uv run ty check src/                   # static type check (astral ty)
uv run pytest -m "not slow"            # what CI and pre-commit run
uv run pytest                          # adds the overlay-dependent smoke test
```

`ruff` and `ty` are pinned in `pyproject.toml`'s `[dependency-groups].dev` and
installed via `uv sync`. `ty` is pre-1.0; tolerate occasional false positives but
do not silence errors blindly. For pydcs-induced invariance complaints on
`List[Type[…]]` arguments, cast with `cast(list[type[VehicleType]], [...])`.

### Pre-commit (prek)

`.pre-commit-config.yaml` wires the same hooks. Use Astral's
[`prek`](https://github.com/j178/prek):

```bash
uv tool install prek          # once
prek install                  # wire .git/hooks/pre-commit
prek run --all-files          # manual full run
```

The hook fails the commit if `ruff check`, `ruff format`, `ty check` or the fast
test selection reports anything. `.github/workflows/ci.yml` runs the same four.

## Existing missions

They are in [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/),
and each module's own docstring is that mission's brief. Read the docstring
rather than a summary of it; `README.md` has the one-line index.

The one thing they all do the same way: **every mission draws its plan with
`PlanOverlay`, hands the rings back as `Assembled.briefed_threats`, and lets the
base conceal, load and brief them.** What differs is only how far the estimate
sits from truth, and that is `difficulty` — a `trained` ring is 2 km off,
`veteran` a quarter wider and 4 km off, `ace` wider again and 6 km off. Nothing
is withheld entirely at any label.

Two missions deviate on purpose — `kuban_forge` draws an enemy thing precisely,
`ansariyah_works` overrides a briefed radius — and each says why in its own
`_draw_plan`. Read those before copying either.
