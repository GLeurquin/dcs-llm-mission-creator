# Project notes for Claude

A pydcs-based DCS mission generator.

## Where a fact lives

Four homes, and a paragraph goes where its **question** is asked. The rule used
to partition by topic, which is why it was not followed: the laser code is a
design decision, a pydcs fact and a project convention at once, so all three
documents could honestly claim it and all three wrote it out.

| Home | Answers | Test |
|---|---|---|
| [README.md](README.md) | *What is this project, and what ships with it?* | Is the reader a human who has just arrived? |
| [PYDCS_REFERENCE.md](.claude/skills/dcs-mission/PYDCS_REFERENCE.md) | *What does this pydcs call do?* | Would it still be true if this repo had no missions in it? |
| [SKILL.md](.claude/skills/dcs-mission/SKILL.md) | *What should this mission contain?* | Would changing it change what the mission contains? |
| **This file** | *How do I call this repo's code correctly?* | Does it give me a name, an argument, or an ordering constraint in `src/`? |
| the module docstring | *Why is this code the way it is?* | Is it evidence for a decision already made? |

The last one is where most of the mass belongs, and three rules keep it there:

- **One home per fact, one exception.** A document may point at another; it may
  not restate it. The exception is a *value you have to type* — repeat the
  value, never the argument. So SKILL.md says "the code is 1688" and points here
  for why `laser.set_code` refuses anything else.
- **Pointers run outward from this file, never in a cycle.** This file may point
  at either skill document; those two may point back only for the *name* of a
  helper, never for a rule they own.
- **A measurement may appear twice only as evidence for two different rules.**

And the line that decides the hard cases:

> If deleting a sentence would make an agent write **wrong code**, it belongs
> here. If deleting it would only make the agent **unable to argue with the
> design**, it belongs in the module docstring.
>
> A number is *contract* if it is an argument or a limit — 98 kneeboard columns,
> fifteen threat points, twenty-five GEO vertices, `MIN_PLAYERS`. A number is
> *evidence* if it is a measurement — 170 m, 21 minutes, 19.6 t, 9.0 m/s.

Every "(project-owned)" section below is therefore short on purpose: what to
call, what the arguments mean, who calls it, and one pointer to the module whose
docstring carries the argument for it.

## The base class already does these — never call them from a mission

Six of the defect classes this project has shipped were "a mission called
something `MissionBuilder.build_miz` already calls", so the list is here rather
than spread across the sections that describe each one:

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

# The contract

## Package layout

Mission scripts go in
[src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/) as
`<scenario_slug>.py`, one concrete subclass of `MissionBuilder`
([core/mission_builder.py](src/dcs_mission_creator/core/mission_builder.py))
per module.

**What a mission declares.** Data first, because most of it is:

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
map-local: pydcs serialises the hour and minute verbatim and DCS reads the field
as map-local, so `tzinfo` is inert — write the local time you want. None of
these has a default: what time a sortie flies and what the weather is are
decisions, and a mission that forgets one should fail rather than quietly ship
mid-morning clear. Each is read through a method (`start_time_for(m)`,
`weather_for(m)`, `blue_task_text()`, `red_task_text()`) so a mission that needs
to *compute* one overrides that instead.

**What a mission implements** — three abstract methods and nothing else:

- `_assemble(self, m: Mission, plan: PlanOverlay) -> Assembled` — build the
  whole mission into `m`. `plan` is the F10 overlay, constructed by the base so
  a mission whose route geometry reads `plan.estimate` does not have to think
  about ordering. Return `Assembled(overlay, briefed_threats)`: the overlay the
  positions came from, and whatever `_draw_plan` produced.
- `_in_game_briefing(self) -> str` — the in-game description panel, plain text.
- `readme(self) -> str` — the README.md content: the mission *briefing* in
  markdown (situation, mission, package, threats, ROE, navigation, frequencies,
  win/loss conditions, re-generation command), not metadata. Both briefing views
  read class state so they cannot drift apart.

Use `self.players` for client-slot counts — `MIN_PLAYERS` to `MAX_PLAYERS`, 2–6,
validated by the base. Build the player flight with `mission_kit.player_flight`,
never with a raw `group_size=self.players`, and the flight splits its loadout
across the slots (see *Loadouts*).

**What the base owns** is the table above, plus `_permit_crash_recovery`, which
forces `permitCrash` on (the ME's "PERMIT CRASH RCVR", `m.forced_options`) for
every mission so a player who crashes lands back on slot selection instead of in
the debriefing. Nothing else is forced. `generate(output_dir)` seeds the RNG,
then writes `<name>.miz` and `README.md`.

Three of the base's steps run *after* the save, and for the mirror-image reason:
a data cartridge, the kneeboard cards and the recon still are files **inside**
the `.miz`, and `Mission.save` writes a fixed set of zip entries with no hook for
another one. The kneeboard is last for a second reason — its route card prints
the take-off and landing altitudes the snap has just corrected.

**The base owning a step does not mean a mission cannot change it.**
`_finish_briefing` is a normal method: a mission that wants a group left visible
on purpose, or a briefing written some other way, overrides it and calls
`super()` for the parts it still wants. What the base guarantees is that nothing
is *forgotten*.

Each module also exposes a one-line `main()` so the script stays runnable via
`python -m`. The argparse lives in
[core/cli.py](src/dcs_mission_creator/core/cli.py) and takes the slug and title
off the class, so the two cannot drift:

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

`_assemble` is the orchestrator, not the implementation. Each block of the
mission gets its own method whose name says what the block produces and whose
docstring states the design intent in one line. Pattern (see
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

    # The base conceals the enemy, loads the cartridge, writes the briefing
    # panels, then snaps waypoints and saves.
    return Assembled(scene.overlay.overlay, briefed_threats)
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
- **A helper whose body has no per-mission payload should not exist.** Eight
  missions each carried a `_load_cartridge` that was two fixed calls under a
  ten-line docstring, and a `_conceal_red` that was one. Both are the base's
  now. The test is not "is this a block" but "would a second mission write this
  differently" — if not, it is mechanism and belongs in `core/`.

## Mission scaffolding helpers (project-owned)

Four small modules hold what every mission used to carry its own copy of. None
of them holds policy: force composition, timings and text stay in the mission.

- [`core/mission_kit.py`](src/dcs_mission_creator/core/mission_kit.py) —
  `offset(origin, *, east_m, north_m)` (DCS `x` is north and `y` is east; this
  is why call sites never say so), `mark_clients(group)`, `arm(...)` (see the
  loadout rule below), `race_track(p1, p2)` for the orbit arguments
  `awacs_flight` / `refuel_flight` want, `unit_of_type(group, type)`,
  `set_skill(group, skill)`, and the three walks every core module needs —
  `flying_groups(m)`, `flying_groups_by_side(m)`, `is_client(group)` and
  `player_groups(m)`. Import these rather than redefining them; six modules had
  written the group walk out by hand and the copies disagreed about whether an
  empty group counts.

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
  lone jet), builds each section from the same field, arms each **slot** from
  the flight's own fit table (`loadouts=`, see the loadout-split section below),
  marks the slots and records the groups as **one flight**. The mission then
  gives each section the same route, which is why every `_spawn_player` here now
  ends in a `_route_<callsign>` helper: the corridor is a search against the
  terrain, and two sections searching separately would fly two plans under one
  briefing.

  Four things read that record rather than counting groups, and each was a way
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
  - `mission_kit.slot_names(flight, slots)` is the same trick one level down: it
    is the `"<group> Pilot #<n>"` string DCS puts on the slot-selection screen,
    which is what the loadout table's Slot column prints. A five- or six-slot
    flight restarts its pilot numbers at the second section, so "slot 5" and
    "Pilot #1" are both true of the same jet and only one of them is clickable.

  A trigger gated on "the player" needs every section: `GroupDead` ANDed
  (`daryal_run`, `abkhaz_sweep` — the loss call must not fire with jets still up)
  and `PartOfGroupInZone` ORed with `condition.Or()` (`idlib_gauntlet`'s seam
  crossing — pydcs's condition list is ANDed, so listing both would hold the call
  until both had crossed). `mission_kit.sections_of(m, group)` hands back the
  section-mates of any flight, and just that flight for one built any other way.

- [`core/weather.py`](src/dcs_mission_creator/core/weather.py) — the weather as
  one frozen record instead of fourteen assignments. A mission declares it as
  the `weather` class attribute (*Package layout*) and the base applies it; the
  record is public so `weather_for(m)` can build a different one.
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

Attach it with `lua.InlineDoScript`, **never** pydcs's `action.DoScript` —
which parks the Lua in the l10n dictionary and produces a trigger that dies at
mission start (PYDCS_REFERENCE.md §7.1 has the mechanism and the exact error).
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

## Briefings, factions and statics — where the rules are

Three things that used to be stated here are **design policy**, and they live in
the `dcs-mission` skill: how a briefing reads (no trigger logic in prose; every
enemy claim carries a source and an age), how a narrative names a side, and how
far apart to space building aimpoints. What is convention rather than design,
and therefore stays:

- **Faction names in prose, coalition names in code.** `readme()`,
  `_in_game_briefing()` and every trigger message say USA, USAF, Russia,
  Russian, Syrian. `set_blue()` / `Coalition.Blue` /
  `set_description_bluetask_text` are API names and keep theirs; "blue side" is
  fine in a code comment.
- **`_draw_plan` must not draw more precision than the prose claims.** The
  reveal policy and the briefing are one statement — if the text says an emitter
  was fixed overnight by a Rivet Joint track, that is what the ring is allowed
  to be worth.
- **A building objective is `m.static_group(...)`, and `condition.UnitDead`
  resolves it.** The editor's own `unitsLister` enumerates `Mission.unit_by_id`,
  which holds statics alongside vehicles, so "destroy the building" is a
  one-line trigger on `group.units[0].id`. `GroupDead` is the wrong tool — a
  static group is not a group in the scripting sense. `conceal_country` covers
  statics, which matters more here than for vehicles: an unhidden compound shows
  every building as an icon and hands the player the whole aimpoint choice
  before he starts engines.

# Helpers, in the order you call them

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
built group out. `set_skill` lives in `core/mission_kit.py` — it takes any
group, not just a site. Get the `position` from the `core/placement.py`
helpers (`sam_site_on_ridge`, etc.) as usual. Design rules (what threat where,
per difficulty) live in the `dcs-mission` skill; unit-type catalog and the
pydcs `VehicleTemplate` split in PYDCS_REFERENCE.md §5.

**Sites are laid out dispersed**, with the search radar and command post pushed
off the position and every placement wobbled, so a site has a real gap in its fan
and the axis a stick of bombs is laid down matters.

`disperse_site(group, *, radius_m, overlay=None, terrain=None)` applies the same
treatment to a group this module did not build — in practice a pydcs
`VehicleTemplate` site, which parks its launchers within a bomb's effect of the
radar. It **inflates** the template's own layout: each unit keeps its bearing,
distances are scaled so the outermost sits at `radius_m`, then jittered. So the
radar stays out in front of its fan, and — what missions depend on — **unit order
is untouched**, keeping `units[0]` the radar for every `unit_of_type` objective.
Call it *after* any extra units the mission adds to the group, or those stay
where the mission put them. Every mission that uses a pydcs template does (SA-6 →
300 m, SA-11 → 400 m, SA-10 → 500 m).

*Why those distances, and the two bounds that keep dispersion from making the
briefed ring wrong, are in `core/air_defense.py`'s docstring.*

## Somewhere to fall back to (project-owned)

[`sanctuary`](src/dcs_mission_creator/core/sanctuary.py) gives each side a
**defended** place to run to. Every mission here used to have none: measured
across all of them, not one friendly SAM, not one AAA piece, not one blue
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

Then in `_draw_plan`, **first**:

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

**`keep_clear` is the invariant, and `build_sanctuary` raises on it.** An area
SAM is a mission-warping object — a Patriot reaches 100 km, which on the
shortest sorties here is the whole ingress — and an umbrella that touches the AO
does not give anybody a refuge, it deletes the mission: the belts the player was
briefed to work around get shot from the other side of the map by an asset
nobody planned the sortie against. The reach comes off the F-16C's own
`THREAT_PTS` table in `core/dtc.py`, the same rows the cartridge is written
from, so nobody re-types a range.

**The two lists are not the same list, and the helper cannot tell them apart for
you.** Out of *our* umbrella goes whatever the enemy needs left standing — the
AO, the belts, the EWRs — and nothing else: a CAP station 45 km up the axis and
a PUSH point 25 km north of the field are *supposed* to be inside it. Out of
*theirs* goes every friendly station and the whole ingress corridor.

When the check refuses a field, the answer is usually that the field was wrong.
The three ways it refuses, and what each is telling you, are in
`core/sanctuary.py`'s docstring with the worked cases.

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

Design rule as everywhere else in `core/`: absolute world `Point` / pydcs
`Airport` and `Country` in, built groups out. Which field, which system and what
the briefing says are the mission's decisions. Every mission states its callsign
and battery as module constants (`_SANCTUARY`, `_SANCTUARY_BATTERY`) and
interpolates them into both briefing views, so the prose cannot drift from the
reach that was actually emplaced.

**Every mission carries one on each side**, with a `## Fall-back` briefing
section, a "not cleared to pursue over `<enemy field>`" ROE line, and the enemy
field's belt in the HSD cartridge and the kneeboard threat block. Which callsign
and which battery is in each mission's own module constants; the field a
sanctuary is *put on* is the interesting decision, and `core/sanctuary.py`'s
docstring has the cases where the obvious field was the wrong one.

Two things worth noting about the red half. It needs no scripting to work: DCS AI
already RTBs on bingo (`tasking.apply_ai_difficulty` sets it), so a defended red
field turns "chase him home" into a priced decision using the same missiles as
everything else. And on a mission with an IADS net the red field battery belongs
**in** it — `idlib_gauntlet` adds Bassel as a `Site`, slowest reactions and
shortest `react_range_m` in the net, because leaving it out would make the
airfield belt the one battery in Syria that stays up under a HARM.

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

`clear_terrain(route, altitudes, *, overlay, clearance_m=CLEARANCE_M,
sample_m=50.0)` is the third case: an **en-route** altitude a mission typed by
hand. It only ever raises, so a mission's own numbers are a floor, and it checks
the straight legs as well as the points. `Leg` + `agl_profile(legs, overlay, *,
clearance_m, ground_floor_m=None)` is the form a low route actually wants — a
`(name, position, agl_m)` table in degrees, converted against the raster.
`ground_floor_m=0.0` is for a route with water under it, where the raster holds
depth below datum. *Why an AMSL altitude typed by hand is the dangerous one is in
that module's docstring.*

`sample_m` defaults to the elevation raster's 50 m cell because sampling coarser
than the data steps over a one-cell spur. What it deliberately does **not**
cover is the leg into or out of a `add_ground_waypoint` steerpoint: that point
carries the target's *elevation* rather than a commanded altitude (the run-in
altitude belongs on the IP), so the ramp to it reads as terrain penetration in
every mission here and is not one.

`set_departure_speeds(m)` fixes the same class of defect one field over.
`add_runway_waypoint` hard-codes 108 kt at 300 m AGL and takes no speed
parameter, which is below a loaded jet's stall speed (the arithmetic is in
PYDCS_REFERENCE.md §4.3). The AI's answer to an unflyable command is max alpha
and full throttle, which from the ground is a jet standing on its tail in
afterburner all the way to the first en-route point.
The helper writes the flight's **own next en-route speed** there, so no
per-airframe table has to be invented and the mission's existing tuning is
what applies; it only ever raises the value, so it is idempotent and a mission
that sets its own departure speed keeps it. The **approach** runway waypoint
carries the same 108 kt and is left alone on purpose: by then the jet is light
and that is roughly its real approach speed, not a stall command, and DCS runs
its own pattern logic off the landing waypoint. **Missions never call it** —
`MissionBuilder.build_miz` does, right after `snap_base_waypoints`.

## Every speed is km/h true airspeed

**Write speeds in km/h and check them against the airframe.** Every pydcs
speed argument — `add_waypoint`, `OrbitAction`, `awacs_flight`,
`refuel_flight`, `patrol_flight`, `intercept_flight`,
`flight_group_inflight`, and `waypoints.add_ground_waypoint` on top of them —
is **km/h true airspeed**, stored as `speed / 3.6` m/s. None of them says so
in its signature and none of them validates the number, so a knots-shaped
value is accepted in silence and commands roughly **54 % of the intended
speed**.

`core/audit.py` checks every commanded speed against the airframe's own
`max_speed`, and its docstring carries the measured table of what the repo
actually shipped. What matters here is the shape of the failure: **the friendly
package flying its whole sortie in afterburner**, because at a knots-shaped
speed a fighter is far below best-climb and deep on the back side of the drag
curve, and the AI holds the commanded altitude on the throttle.

Fix the speed first, but do not stop there, because the speed is only ever half
of it. **The DCS AI's own take-off and climb-out routine is high deck angle and
both burners lit until it is established, and no waypoint reaches that.** Three
route-level fixes went in against one report of a Hornet climbing out in burner
— the km/h units, the cruise fraction, the 108 kt departure waypoint — and all
three were real defects that changed nothing about the symptom.

So `apply_threat_reaction` takes `restrict_afterburner` (default `False`, the
DCS default). It is off by default because for most of the package it is
pointless or harmful — an E-3A, a KC-135 and an A-10C have no afterburner to
restrict, and a CAP or interceptor needs one in a merge. Set it on a **heavy
strike flight**, where burner buys nothing and the fuel matters; Pontiac is the
one flight in the project that carries it. The trade is that the flight cannot
accelerate out of a SAM engagement either, so it belongs on a flight whose
route already bends around the live rings (`core/routing.py`), not as a blanket
setting.

Sanity bound: `FlyingType.max_speed` is km/h too, so check every leg against
it (the per-airframe table is in PYDCS_REFERENCE.md §4.2). On a **supersonic
fighter** a cruise or orbit speed lands at **0.30–0.40 of `max_speed`**; under
~0.2 is the unit error, over ~0.40 is afterburner — the two bounds `core/audit`
checks. The band does *not* transfer to subsonic types: their `max_speed` is
barely above their cruise, so an E-3A at 0.86 and an A-10C at 0.72 are correct,
and neither has an afterburner to worry about.

**The ratio is the check, not the number.** Getting the unit right is only half
of it: the same km/h is a different fraction of a different jet's ceiling, and
the fast jets are not interchangeable. `idlib_gauntlet`'s Pontiac kept the 800 /
850 km/h that read as a sane cruise for Uzi's F-16C next to it (0.38 / 0.40 of
2120) and flew its whole sortie in burner, because on an F/A-18C — the slowest
fast jet in the fleet at 1950 — the same numbers are **0.41 / 0.44**, and it was
carrying the heaviest configuration any flight here launches with (the wing-tank
pair the paragraph above took off it, four GBU-12 on BRU-33 racks, ATFLIR and
three AAMs). It now flies 680–750 (0.35–0.38, Mach 0.59–0.67, ~280 KIAS). Weigh
the loadout as well as the airframe — a bombed-up jet sits at the bottom of the
band, a clean CAP at the top.

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

A one-off audit across every mission is a short script — build each mission,
walk every flying group, and print each distinct waypoint speed over
`unit_type.max_speed`. That is what found Pontiac after the unit fix had already
gone in, and it is worth re-running after touching any route. **Include the
sub-300 km/h waypoints in that audit.** The first pass filtered them out as
noise and so walked straight past the `200(0.10)` sitting on all thirty flights
— pydcs's hard-coded departure speed, and a worse bug than the cruise numbers
it was looking for (see `waypoints.set_departure_speeds`). A speed under ~0.2 of
`max_speed` is the signal, not the noise.

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
across the whole project that is three: `coastal_cover` `Hawg` (the A-10 pair
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
`trace=True, debug=False` gives the quiet, log-only one — which is what a
shipped mission wants, because Skynet's half prints on the player's screen and
will talk over the mission.

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

**A radar radiates in looks**, and how long a look is says who is running it:
a site takes `emission_limit_s` on the air and then goes quiet for
`emission_pause_s`. Give neither and the look comes off the group's own DCS
`Skill` (`_EMISSION_BY_SKILL`: 20–35 s at `Excellent` through 90–150 s at
`Average`, long enough that a conscript battery effectively has no discipline).
Two things keep it from breaking a mission: **a look never refuses an
engagement**, so the clock is held while there are missiles in flight or a
target inside the launchers' own envelope; and an **EWR or `act_as_ew` site is
exempt** unless a band is given explicitly, because a net whose search coverage
works in bursts has nothing to hand a track to.

**Shoot and scoot is two hops, and the useful one happens before the shot.** An
anti-radiation missile in POS or EOM mode is aimed at a *coordinate*, so going
dark saves nothing against the shot a competent player takes — only a stale
coordinate does. A battery that has spent `scoot_after_s` on the air since it
last moved relocates the next time it goes quiet, on the assumption it was fixed
while it emitted. The **reactive** hop is the second one, and it is a grade on
the duel rather than an escape: it starts when the crew reacts, so a shot from
40 km arrives on ground the battery has left and one from 15 km arrives before
it moved at all.

Leave `jockey_m` as `None` and a table decides (`_MOBILE_TYPES`); anything else
stays put, because an S-125 fires from built revetments and a 55G6 is
march-ordered in hours. Two compositions are refused outright and both arrive by
accident from a pydcs template: **infantry** (a DCS group moves at its slowest
member, and `sa11_site` ships a rifleman) and an **optically guided launcher**
(Strela, Tunguska — with the AI handed back it would keep fighting from a site
the mission believes is suppressed). Every hop is drawn from the site's **start**
point, never the last one, so repeat fire cannot walk a battery out of the ring
`PlanOverlay` drew and `core/dtc.py` loaded.

*Why the bands and the jockey distance are what they are — two measured HARM
flight times, and the two defects measuring them exposed — is in
`core/iads.py`'s `_EMISSION_BY_SKILL` and `_JOCKEY_*` comments.*

**A radar going off or back on the air is only reported if somebody could have
heard it.** `listeners` names the friendly groups that could — a Rivet Joint
track, an AWACS with ESM, a ground collection site — and the gate is live:
alive, in range, and in line of sight of the emitter. Declare none and the net is
silent, which is the honest default. `arm_iads` warns when calls are configured
with no listener, since the wording is set in Python and the silence happens in
Lua, and the briefing has to say whose picture it is or a call that never comes
reads as a bug. Calls are queued `announce_spacing_s` apart, so a shot that
darkens a whole belt gets one per site instead of only the first.

Only list **radar-guided** sites — SA-13 and MANPADS have nothing to shut down,
and listing a mixed convoy would make the whole column hold fire on every HARM
shot.

*Which calls are made and why the silent cases are silent, and the vendored
Skynet build and its two seam tests, are in `core/iads.py`'s docstring. Design
rules (what reveal, what difficulty) are in the `dcs-mission` skill; the pydcs
`DoScript` mechanics in PYDCS_REFERENCE.md §7.*

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

## Laser codes (project-owned)

[`laser`](src/dcs_mission_creator/core/laser.py) owns the one number a briefing
must not get wrong. A mission that hangs a laser-guided weapon makes two claims
— the controller's spot is on code N, and the bombs will track it — and DCS has
already decided both for almost the whole fleet: **an AI JTAC lases 1688 and
nothing else**, and **most cockpits come up on 1688 too**, because the F-16C,
F/A-18C and A-10C carry no laser-code property at all. Which task fields exist
and which four airframes *do* expose one is PYDCS_REFERENCE.md §6.1; the
argument for the rule is `core/laser.py`'s docstring.

The rule: **one code per mission, and unless every laser weapon in it belongs to
an airframe whose code the mission can write, that code is `DEFAULT_CODE`.**

```python
_LASER_CODE = laser.DEFAULT_CODE      # the mission's one code
laser.set_code(section, _LASER_CODE)  # after `arm`, per flight with a laser weapon
laser.laser_guided_stores(flight)     # which loaded stores ride a spot
```

`set_code` writes the properties where they exist and **refuses** any other code
where they do not — which is exactly the bug it was written for, and the case is
in the module docstring. From the cockpit a bomb that
tracks nothing is indistinguishable from one that failed to guide, and the
player's own recourse — retune the pod — is the one thing that cannot help,
because the pod was never the half that was wrong. `core/jtac.py` refuses a
`CoordTarget.laser_code` that is not `laser.AI_JTAC_CODE` for the same reason.

**Then the briefing says the number for the bombs, not only for the spot.**
"`Pinpoint 1-1` lases on 1688" is half a fact; the half a player acts on is that
his own GBU-12s and pod are already there, so there is nothing to arrange. Both
briefing views carry it, and so does the `kneeboard.remark`, which is the one
place it can live on a card — pydcs writes the code into no field, so nothing
about it is derivable. Every mission that hangs a laser-guided weapon says it on
one line: `coastal_cover` ("Pinpoint 1-1 lases on 1688; your GBU-12s are coded
the same"), `kodori_strike`, `kuban_forge`, and `idlib_gauntlet`, where the
flight with the bombs is `Pontiac` rather than the player and the briefing says
that too — `Uzi` carries self-guided SFW and nothing on a laser at all.

### The spot has to be up before the jet is there

The code is only half the claim. The other half is whether anything is lasing
when the player rolls in, and as DCS ships it the answer is **no unless he has
had a radio conversation first**: the AI controller's spot lives inside the
check-in and the talk-on, over a set the player has to be tuned to, in range of
and — because a ground JTAC is a ground unit — in line of sight of. Every low
route in this project is built to deny exactly that. `kuban_forge` spends two
hundred kilometres of valley masked from everything, `coastal_cover` comes out
of a terrain-masked corridor onto a detachment doing 35 km/h, and in both the
player arrives with a pass to fly and no minute to spend raising anybody. The
laser comes up after the pass, or not at all, and from the cockpit that is
indistinguishable from a wrong code — with the cruel detail that the player's
own recourse, retuning the pod, cannot help, because the pod was never the half
that was wrong.

`laser.arm_autolase` puts the spot where the briefing already said it was: on
the target from mission start, held on the nearest vehicle the designator can
**see** (`land.isVisible`, measured) inside its own reach, moved as the target
drives, and gone when the team is dead. It reads nothing about the player — no
check-in, no range, no slot — because a spot that waits for the jet is the same
defect one step further down the road. The stock task stays: `fac_attack_group`
is still what talks, what reads a 9-line to a grid cockpit, and what makes the
controller acquire.

```python
laser.arm_autolase(m, [laser.LaserSpot(
    tacp, detachment, code=_LASER_CODE, label="Pinpoint 1-1",
    lead_correction=True,          # the target is driving and there is one pass
)])
```

**The method is Ciribob's** — [DCS-CTLD](https://github.com/ciribob/DCS-CTLD)'s
`ctld.JTACAutoLase` is the reference implementation — reimplemented in
`core/lua/autolase.lua` rather than vendored, for two reasons written up in
`core/lua/vendor/README.md`: CTLD needs 26 distinct `mist.*` functions
(databases and dynamic spawning, not the thin conversion slice `mist_shim.lua`
covers for Skynet) and its repository carries no licence file. Five of its
decisions were worth taking exactly as they are, and the last is the one that is
invisible when you get it wrong:

- the beam leaves the designating vehicle 2 m up, and the aim point is the
  target's own origin lifted 2 m — not the ground under it;
- line of sight is tested with **both** ends lifted 2 m, because two points on
  the deck fail across the gentlest rise;
- 10 km of reach (CTLD's `JTAC_maxDistance`), which is a vehicle-mounted
  designator rather than a man-portable one;
- the nearest visible vehicle gets the spot, and the one already lased keeps it
  while it still qualifies — a spot that hops to a closer truck mid-fall throws
  the weapon off the vehicle the pilot was talked onto;
- **the spot is moved often enough that the target never travels more than a few
  metres between updates** (`max_drift_m`, bounded by `min_update_s` /
  `max_update_s`), while target *selection* runs on its own slower `verify_s`
  clock because it costs a terrain query per vehicle. A fixed one-second update
  leaves a 35 km/h truck ten metres ahead of the spot, which for a GBU-12 is the
  difference between the hit and the dust behind it.

An IR pointer goes up on the same point by default, which is what makes the
target findable through goggles or an IR pod at night. `lead_correction` is
CTLD's `laseSpotCorrections` — a second of the vehicle's travel and 1.05 s
upwind — off by default because it is not something the crew is really
computing: it is a correction for the way a DCS LGB trails a moving spot, and it
belongs on a mission whose target is at road speed and whose player has one
pass. Both missions where the player takes an AI spot (`coastal_cover`,
`kuban_forge`) carry it.

`idlib_gauntlet` deliberately does **not** arm one, and the reason is the test
for whether a mission needs it: there the laser is for `Pontiac`, an AI Hornet,
and an AI flight needs no radio conversation to use a spot. Arm this where a
*player* takes the controller's laser.

It says nothing on the radio, on purpose — every call here goes through
`core/triggers.py` so the screen text and the TTS render cannot drift, and a
Lua-side call would be text-only. When the laser comes and goes with the
terrain, the mission says so with its own triggers on the geometry that decides
it: `coastal_cover`'s party calls the sight line opening and closing off zones
on the road it is actually watching. `trace=True` writes each decision to
`dcs.log` under `LASER/<label>` (`grep 'LASER/' dcs.log`) and nothing to the
screen, like `core/iads.py`'s trace.

And the briefing has to say the spot is already up, or the feature is invisible
in the same way `core/jtac.py`'s readout was: a player who has been taught that
a JTAC needs a check-in will spend the window doing one. Both briefing views of
both missions now say it, and say that the net is there for the talk-on and the
coordinates rather than as a precondition for the laser.

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

**Every difficulty draws a ring**, including the hard ones, and hands the
estimate back. Withholding it never withheld the *position* — the mission still
had to put steerpoints somewhere, and with no estimate to build from it built
them from the truth. The incident is in `core/map_draw.py`'s docstring; the rule
it produced is the one below. Friendly-plan calls (`route`, `orbit`,
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

The `PlanOverlay` is handed to `_assemble` by the base, so a mission whose
geometry needs the estimate can call `plan.estimate` anywhere and there is only
ever one overlay. The rule: **every planned point that refers to an enemy site
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

**A ring is only for something emplaced**, and the two signatures carry the
rule: `.threat()` returns its estimate and `.mobile_threat()` **returns
nothing**, so a system that drives cannot end up frozen into a pre-planned
cartridge point. Ground truth for the call: a group with waypoints is mobile.
(Why a stale ring is worse than no ring: SKILL.md *Draw the plan*.)

Missions call this in a `_draw_plan` step near the end of `_assemble`, and
hand what it returns back as `Assembled.briefed_threats`. Whether it runs before
or after the trigger steps does not matter — drawing reads no trigger state.
Spawn helpers that own friendly geometry (the ingress corridor, the AWACS,
tanker and CAP tracks) return their `Point`s so `_draw_plan` can annotate them.
The `PlanOverlay` itself is the base's: it is handed to `_assemble`, so a
mission whose route geometry reads `plan.estimate` has no ordering to think
about.

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
a drawing. **Missions never call either** — the base sweeps the whole enemy
coalition (`conceal_coalition`, driven off the side the client slots are *not*
on), so a late-activated reserve, a newly added EWR or a second enemy country
cannot be forgotten. A mission that wants something left visible on purpose
overrides `_finish_briefing`. Design rules live in the `dcs-mission` skill; the
raw pydcs attributes in PYDCS_REFERENCE.md §5.

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
# ...then hand `hsd` back as `Assembled.briefed_threats`. The base loads it.
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
  points **in the mission's own draw order** — so a mission that cares which
  point survives an oversubscribed tab draws it first. Which marks qualify: the
  objective as a `TGT`, the mission's text labels (a seam, an off-load point),
  the air defence that moves, a vague enemy area, and one steerpoint per
  **orbit** at the midpoint of the race-track, because what a pilot wants from a
  tanker station is a range and a bearing. Emplaced threats are deliberately
  absent — they are already the pre-planned threat points above, and a second
  copy costs a navigation slot for nothing. A `PlanOverlay.umbrella` ring takes
  no slot at all: a ring is an *area* and its centre is a battery 4.5 km off the
  runway.
- **The route's steerpoints carry a `TOS`**, and the plan's marks do not —
  nothing scheduled a tanker station or a seam. A route point's time is the same
  instant the kneeboard's route card prints, so there is one schedule and the
  card's zero-wind caveat covers the DED with it. **The clock is zulu and the
  card's is local**; the offset is `Terrain.utc_offset` and the trap is in
  `core/dtc.py`'s docstring. `FIX_Time` stays off everywhere: it is the switch
  that makes the *speed* derived from the times, and the mission tuned those
  speeds per airframe.
- **`GEO_LINES`, steerpoints 31–55.** Twenty-five vertices shared between
  **four** polylines, so this is the scarce tab and the order matters: front
  lines, then a corridor the flight does not itself fly, then orbit tracks with
  whatever is left. Line index is a colour — enemy geometry asks for red, the
  friendly plan for green. A line over its share is thinned with both ends kept.
- **A `route` line the flight itself flies is dropped.** Missions draw the
  corridor they then fly, and the HSD already joins the steerpoints; what
  survives is a lane somebody else flies, which is worth a line.
- **The route wins every budget fight.** Uploading a steerpoint tab *replaces*
  the flight plan DCS put in the cockpit — `mirror_NAV_PTS` is the "do not
  upload" checkbox and defaults to on for exactly that reason — so a plan that
  would push past twenty-five points loses its own marks, never the pilot's
  navigation. `overlay` is required rather than optional: every point carries
  the terrain elevation under it, and a route at sea level would be worse than
  the mirrored default it replaces.
- **The route is re-read at write time, not when the mission arms it.**
  `build_miz` snaps take-off and landing altitudes and rewrites the departure
  speed *after* `_assemble` returns, so a tab frozen at draw time would print
  the sea-level take-off and the 108 kt those two steps exist to correct. Same
  reason the kneeboard is written last.
- Two player Viper *flights* raise: there is one steerpoint tab and every Viper
  slot loads it, so two routes will not fit.

Only **emplaced** systems the briefing names reach the cartridge, which follows
from `mobile_threat` returning nothing for `briefed` to load. EWRs are not rings
either (a search radar has no envelope), and neither are ground-force markers.
The F/A-18C has a cartridge too, but its threats are `MEZ_THRTS` on the SA page
— a second table, not a parameter — and no other module in DCS draws a
pre-planned ring.

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

## Radio-frequency helper (project-owned)

[`radio`](src/dcs_mission_creator/core/radio.py) puts a flight's frequency in
the field DCS binds a radio to. `Mission.awacs_flight` and
`Mission.refuel_flight` take a `frequency=` argument and spend it on a
`SetFrequency` **waypoint task**, leaving the group's own `frequency` field on
the 251 MHz that `MovingGroup.__init__` gives every group pydcs creates. The
group field is what a player's radio has to match to raise an AI controller at
all; the waypoint task retunes the AI minutes into its own route, after it has
taxied and climbed to the departure point.

So **every tanker in this project was briefed on a frequency it was not on** —
and printed on the kneeboard on that frequency too, because
`kneeboard/comms.py` reads the `SetFrequencyCommand` in preference to the field,
that being where the mission's intent was recorded. The tell was which asset
worked: every mission with both a tanker and an AWACS put the AWACS on 251,
which *is* pydcs's default, so the AWACS was reachable by accident and the
tanker never was. ED's own missions do it the other way round — the working
tanker in `Mods/aircraft/F-16C/Missions/QuickStart/F-16C - Caucasus - Air
Refueling.miz` is written `["frequency"] = 305, ["radioSet"] = true`, and that
mission contains no `SetFrequency` task anywhere.

`tune_working_frequencies(m)` mirrors the intent back into the field,
mission-wide. **Missions never call it** — `MissionBuilder.build_miz` does,
after `assign_datalink_identities` and before the save, for the same reason as
`waypoints.snap_base_waypoints`: a flight added later cannot miss it.
`working_frequency(group)` is the single resolution both it and the comms card
read, most specific source first — a FAC task's own params, then a
`SetFrequencyCommand`, then nothing — so the card cannot print a number the
sweep did not tune anyone to.

Two things it deliberately leaves alone, and each is a way a blanket sweep would
break something:

- **A group holding a client slot.** `FlyingGroup.set_frequency` also sets
  `radioSet`, which is DCS's "this mission overrides the cockpit preset table"
  flag — it writes the group frequency into channel 1 of the first compatible
  radio. The comms card annotates a frequency with its preset channel
  (`253.000 AM  R1 CH19`) off the airframe's own `panel_radio`, on the
  assumption nothing overrides it. No client group carries a `SetFrequency` task
  today, which is why the guard costs nothing and is worth having anyway.
- **Ground groups.** A `VehicleGroup` writes no frequency at all unless
  `communication` is set, and a ground JTAC's radio is the frequency inside its
  own FAC task params — where DCS reads it from, and what the card already
  prints. Nothing broken there to mirror.

One thing left as it was, on purpose: the **player flight's own** group field
stays at pydcs's 251, which in every mission here is also the AWACS's channel.
That field is the flight's intra-flight frequency rather than anything the
player has to raise, and moving it means overriding the preset table the card
is annotated from — a separate decision from this one.

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

**It is a radar product, not a photograph, and that is forced by the data** —
the overlay does not carry enough to draw anything finer without inventing it.
The consequence to work with: a vehicle is a fifth of a pixel at the honest
scale, so detections are **symbology drawn after the sensor chain**, never hot
blobs in it. The measurements are in
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

**One table per waypoint, not two**, and **the threat block prints the briefed
picture rather than adding to it** — it comes from `dtc.briefed_threats(m)`, the
same estimates the cartridge was loaded from, so the difficulty policy stays in
`map_draw.py`. Each row carries the pre-planned steerpoint it occupies
(`dtc.FIRST_STEERPOINT`, 56 upward), the three-character HSD code, the position
in DDM, the system's published range and ceiling, and bearing/range from
bullseye. *Why those columns and not others — a kneeboard column earns its place
by being unrecoverable in the cockpit — is in `core/kneeboard/pages.py`.*

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
pages away. But what ED ships is not "all fields" — the coverage is in
[`kneeboard/charts.py`](src/dcs_mission_creator/core/kneeboard/charts.py), which
answers the question by looking: it matches the airport name against the chart
file names, since that is all the name a chart has and pydcs carries no ICAO to
join on, and the card is written only where the answer is no. **With no install
the answer is unknown and the card is written**, because the two failure modes
are not symmetric: a redundant page costs a page, a missing one costs the player
their field's elevation, its ATC channel and where their jet is parked.

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

Remarks are **wrapped** to the page rather than clipped, and continuation lines
are indented so a two-line remark still reads as one item. But the wrap is a
floor and one line of `kneeboard.page.COLUMNS` (98) is the ceiling: a remark that
needs a second line is one that should have been shorter, and *why*, with the
worked case, is in `core/kneeboard/publish.py`'s docstring.

Keep the list short anyway. Everything else on the cards is derived and should
stay that way — a remark is prose, and prose goes stale exactly the way the
hand-typed FREQUENCIES block in every briefing was one edit from being wrong.
That block is what these cards make true: the briefings have always said
"Batumi tower: per kneeboard".

**Three things about the mechanism, each a way the feature would otherwise be
quietly wrong:**

- **pydcs's own `Mission.add_aircraft_kneeboard` is not used** — it writes an
  archive entry with an empty path component (PYDCS_REFERENCE.md §8). The pages
  are appended here with the arcname spelled out and an explicit `ZipInfo`,
  exactly as `core/dtc.py` appends its cartridges, which also fixes the
  timestamp `zipf.write` would otherwise take from the file on disk.
- **DCS has no per-flight kneeboard.** A page goes in a folder named after an
  aircraft *type* and every pilot of that type sees it, so a mission with two
  player flights of different airframes gets both route cards in both folders
  and each card names its flight in the title.
- **Timings are zero-wind and tracks are magnetic off a per-theater table**
  (`flightplan.VARIATION_DEG_EAST`), both printed on the page so they can be
  checked. *Why the wind is not corrected for, and why a runway designator is
  not a heading you may convert, are in `core/kneeboard/flightplan.py`.*

### Navaids come from the installed game

pydcs knows a beacon *exists* and nothing else: `Airport.beacons` is a list of
`AirportBeacon(id='airfield22_3')` — an id, no type, no frequency, no position —
and `Airport.tacan` is `None` for every Caucasus field, Batumi included, which
has one. [`kneeboard/beacons.py`](src/dcs_mission_creator/core/kneeboard/beacons.py)
reads `Mods/terrains/<Theater>/Beacons.lua` out of the install instead, the same
file DCS's own F10 airdrome panel reads. `core/dcs_install.py` already locates
the install for loadouts, so there is nothing new to configure; with it absent
the navaid block comes out empty and the build still succeeds. Nothing from the
install is copied into a generated mission — only numbers computed from it.

*Why the parsing is shaped the way it is — the `{x, altitude, z}` axis order, the
join on beacon id rather than distance, the ILS-callsign pairing that is the only
surveyed runway geometry available offline — is in that module's docstring.*

A label with nowhere to go on the plan view is **dropped**, never overprinted:
it is in the navaid table above the sketch either way, and two labels through
each other loses both. Same rule as `core/recon/landmark.py`.

# Loadouts

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
promised, and most of the missions here once shipped with entirely empty pylons
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

**Legal is not realistic**, and `core/loadout_check` (below) is what says so
mechanically. Three station facts worth knowing before you write a fit, all of
them legal in pydcs and all of them wrong:

- **F-16C wingtips (1/9) carry the AIM-120, not the AIM-9** — every mission here
  had that pair the wrong way round. Where the Sidewinders go depends on the fit,
  and the two arrangements are mirror images: in a **SEAD or strike** fit 3/7 are
  the HARM rails or the bomb stations, so the AIM-9X move out to 2/8; in a
  **pure air-to-air** fit ED fills outboard-in with AMRAAM on 1/2/8/9 and puts
  the AIM-9X on 3/7.
- **Every ED two-tank F-16C payload carries an ALQ-184 on the centreline**
  (station 5). This project left it empty in every mission.
- **The F-15C never flies a single wing tank.** Its fuel stations are 2, 6 and
  10; every ED payload that carries fuel uses 6, and the wing pair only ever
  comes as 2 + 10. A lone `(10, tank)` is an asymmetric jet.

Also check the loadout against the *sortie*, not only the airframe: a modern CAS
or LGB tasking wants a targeting pod on the jet that needs one — except the
A-10C, whose TGP is integrated (no ED payload lists an AAQ-28, so don't add
one).

## Loadouts are split across the flight (project-owned)

**Every mission is built for at least two coop slots** (`MissionBuilder.MIN_PLAYERS`,
and both CLIs default to it), and the two slots do not carry the same jet.
[`loadout`](src/dcs_mission_creator/core/loadout.py) is the table that says who
carries what.

The reason is arithmetic rather than taste. An F-16C-50 has eleven stations, and
by the time the bags are on 4/6, the ALQ-184 on 5 and the pods on 10/11, what is
left is 1/2/3/7/8/9 — of which **1/2 and 8/9 take a missile and nothing else**.
So *three* stations decide the sortie, and a jet that spends 3/7 on HARM has no
bomb while one that spends them on bombs has no HARM. A single-slot mission
therefore spent its whole design budget deciding which half of its own frag to
throw away, and every mission here had a fit that was worse at both jobs than
either of the two it has now. `kodori_strike` is the case that makes it plain:
it flew a **pure air-to-air fit** against a win condition of "the FOB is
wrecked", so its player could not complete it.

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
rather than restarting per section. That is the whole scaling rule: two slots
are the complementary pair the mission was written for, four are two elements
each carrying the same split — so an element that loses its partner is still
half a package rather than a jet that cannot do the job — and a mission that
wants a third capability at four slots declares a third fit. Slot 1 always gets
the first fit, so the briefing can name who is carrying what without holding the
built mission.

**The fits are written per unit.** `FlyingGroup.load_pylon` writes the same
store to every airframe in the group, so arming a flight through it can only
ever produce a uniform one; `loadout.arm_unit` is the per-slot half, and it
clears the stations first for the same reason `mission_kit.arm` does.
`mission_kit.arm` is still the right call for an **AI** flight, which is
uniform by nature.

**Three views, one table.** The fits are a module constant so `readme()` — which
holds no `Mission` — can render them too:

- `self.loadout_table(flight, _FITS)` — the README's markdown table, one row per
  slot, with the slot column holding the `"<group> Pilot #<n>"` string DCS puts
  on the slot-selection screen. A pilot picking a jet reads the same string in
  the briefing and in the game.
- `self.loadout_brief(flight, _FITS)` — the same table as plain text for
  `set_description_text`, wrapped narrow to match the rest of that panel.
- the **kneeboard remark**, written by `MissionBuilder._remark_loadouts` for
  every mission so none can forget it: `Dodge fits: #1/#3 HARM/HTS; #2/#4
  CBU-105*4`. Slots are grouped by fit rather than listed one per slot, because
  a six-slot flight listed individually runs past the card's 98 columns. It
  passes the remark test in the kneeboard section — a pilot's own stores are on
  the SMS page, and **his wingman's are nowhere in the cockpit at all**.

**How to choose the split** — the four patterns, and the rules that decide
between them — is SKILL.md's *The flight splits its loadout*. What is mechanism,
and therefore here, is the cycling rule above, the three views below, and the
`role` string: a dozen characters, named after the weapon that decides the job.

`loadout.air_to_air_shots` counts the missiles off the loaded stores, and
`MissionBuilder.air_to_air_shots(_FITS)` sums them over the assignment — which is
what the force-balance rule below now divides by two, so a mission scaling its
opposition off `--players` cannot drift away from what the jets are carrying.

## Legal is not realistic (project-owned)

[`loadout_check`](src/dcs_mission_creator/core/loadout_check.py) reads ED's own
payload tables out of the installed game and answers "which stations does the
game actually hang this store on". pydcs only checks that a station *accepts* a
store, which is how an AMRAAM ended up on an F-16C's station 2 and a Sidewinder
on its wingtip in every mission here for months.

```python
for note in loadout_check.check(planes.F_16C_50, fit.stores):
    print(note)   # station 11: Sniper ATP — no shipped payload carries it;
                  # the game ships AN/AAQ-28 LITENING here
```

**A note names what the game ships on that station instead**, which is the
difference between something actionable and a shrug — and it is what separates a
swapped pair (an AMRAAM on an F-16C's station 2, where the game hangs a
Sidewinder and nothing else) from a sub-variant substitution (an AIM-120C on an
F-15C's station 4, where ED's own payloads put an AIM-120**B**). Twenty of the
thirty-one findings across this repo were the second kind, and without that half
they read identically to the first.

With no `DCS_INSTALL_DIR` there is nothing to read and every check returns empty,
exactly as `load_task_default_loadout` does. The audit says so out loud rather
than reporting a clean bill of health it did not earn. *The two joins that make
the lookup work, and why they cannot share one, are in the module docstring.*

## Force balance: the magazine is the budget

**A mission may not task more kills than the player flight is carrying weapons
for**, and the number of player slots is an argument, so both sides of that
have to be computed rather than typed. `abkhaz_sweep` shipped as the worked
example of getting it wrong: a fixed six bandits — four Su-27 and a MiG-29S
pair, all `Skill.Excellent` — against a win condition of "both flights dead",
whatever `--players` said. A single-slot `Dodge` therefore launched with **six
air-to-air missiles against six of the best crews in the game** and a frag that
required all six to die, with no tanker and no rearm. Four slots faced the same
six.

The arithmetic that fixes it is short, and the first two terms are facts about
the airframe rather than judgement calls:

- **Count the stations, don't assume.** An F-16C-50 with two wing tanks has
  exactly six air-to-air stations — 1/2/8/9 and 3/7 — because stations 4 and 6
  *accept no missile at all* (read the `PylonN` tables, and see the loadout
  section above). Six is a ceiling, not a choice: there is no F-16C loadout
  that buys a seventh shot without giving up the fuel a 55-minute unsupported
  sortie needs.
- **Two shots per kill** is the planning factor against `Skill.Excellent`
  fighters. So one player jet is worth ~3 kills, and the flight's budget is the
  sum over its slots — which is **not** three times the slot count once the
  fits differ: a Viper carrying HARMs or bombs on 3/7 is worth two kills, not
  three. Count it with `MissionBuilder.air_to_air_shots(_FITS)`, which reads the
  rails rather than a constant, and let the opposition follow it
  (`abkhaz_sweep._plan_bandits` takes the magazine, not the player count).
- **Then pick one of three levers** — all three are legitimate, and
  `abkhaz_sweep` now uses the first and the third:
  1. **Scale the opposition off the player count** (`_plan_bandits`), so the
     number of bandits is derived from the magazine rather than chosen.
  2. **Add friendly AI** — but note this trades away mission character: "no
     tanker, no escort, nothing else airborne" *is* the ace composition here,
     so adding a flight was the wrong lever for this mission. The player's own
     wingman is not that lever either: he is already priced in, because the
     opposition is sized off the flight's whole magazine.
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
X": count what the package carries, divide by two, and compare. The air-to-air
half is now mechanical (`air_to_air_shots`); the air-to-ground half still has to
be counted by hand, and it is what caught `kodori_strike` flying a pure
air-to-air fit against a win condition of "the FOB is wrecked".

# Tools, in the order you run them

## The order the tools go in

There are now four of these and they answer four different questions. Running
them out of order is what makes a mission expensive, and `ansariyah_works` is the
worked example of getting it wrong twice in one build:

| # | Ask | Tool |
|---|-----|------|
| 1 | Is this **layout** legal — does anything reach anything it should not? | `survey` |
| 2 | Can an aeroplane **fly** the line between those places? | `route` |
| 3 | — write the mission — | |
| 4 | Is the **built** mission internally consistent? | `audit` |

Both failures were sequence rather than knowledge. That mission had its target
sited, its corridor planned and half its briefing written before anybody measured
the distance from the target to the southern coastal battery and found the target
**inside** its envelope — a one-line check that arrived four hours late and cost a
re-site of the whole AO. And `audit` was skipped entirely in favour of a
hand-rolled speed audit, which found the same three things `audit` finds and
missed the ones it does not.

So: **survey before you route, route before you write, audit before you say it is
done.** The last one is a shell command and the first two are one command each.

## Siting a mission (project-owned)

[`survey`](src/dcs_mission_creator/core/survey.py) answers the question that
comes before `core/route_plan.py`: **where does everything go, and does that
layout hold together?** It exists because building `ansariyah_works` took about
fifteen throwaway scripts, and they asked the same three questions over and over
— find me a place near here with these properties, how far is each of these from
each of those, and what is this place called.

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

Three things it is worth knowing:

- **A margin is measured against what a system reaches, not against the ring the
  map draws.** Those are different numbers and the difference is the reveal
  policy: `ansariyah_works`' coast crossing is 18.1 and 16.4 km outside what the
  two coastal batteries can actually do, and only 9.7 and 8.2 km outside the
  circles the player is shown. The first pair decides whether a pilot who
  complies with the briefing lives; the second is what the plan *looks* like. The
  report prints both, off `map_draw.reveal_policy` so the two cannot drift.
- **The objective's own defences are declared (`--defends`), not inferred.**
  Borrowing `routing.avoid_threats`' rule — a ring covering the target is not a
  finding — was tried first and was exactly wrong: the defect this module was
  written after *is* a ring covering the target, and the derived rule explained
  it away. Geometry cannot tell the Osa emplaced on the works from the belt that
  reaches it by accident; only the author can, and saying so is a claim they
  should be able to defend.
- **`spots` ranks and describes; `find_placement` samples and returns bare
  points.** Both are reproducible (the sampling is seeded from the overlay), but
  siting wants the best candidate *near where you asked* together with the
  terrain facts and the settlement to name it after — which is the loop that kept
  being written by hand.

## Planning a low route (project-owned)

[`route_plan`](src/dcs_mission_creator/core/route_plan.py) answers the three
questions a mountain corridor has to answer before a line of the mission is
written, and it exists because answering them by hand is the single most
expensive thing in this project. `kuban_forge`'s corridor took most of a day of
hand-picking waypoints, running `clear_terrain`, finding lifts of two and three
kilometres and re-picking; the same corridor comes out of one command in twenty
seconds.

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
ground) finds the valley — which is what a pilot reading a chart picks and what
nobody can pick out of a grid of numbers — and then the trace is thinned to the
fewest waypoints whose *straight legs* still clear, by asking
`waypoints.leg_violation` which leg goes deepest into rock and putting a point
exactly there. The output is a paste-ready `_CORRIDOR` table in degrees.

Three things it is worth knowing about the answers:

- **The real question is not "how low" but "how low for a waypoint budget I can
  afford".** Both halves are load-bearing and neither is guessable.
  `kuban_forge`'s corridor needs **23 waypoints at 250 m AGL and 11 at 600 m** —
  and the 600 m version is still masked from every radar that matters, because
  on that map the massif does the hiding rather than the last three hundred
  metres. `--agl` is that dial; raising the mountain band is what buys back
  waypoints on a route that will not fit.
- **`sighting` is the half that cannot be argued.** Line of sight against the
  raster, from a radar mast to the aeroplane at the altitude actually planned.
  It reproduced the "detected 34 km short of the target" figure `kuban_forge`'s
  briefing is built on, which had been derived by hand three times. It answers
  only about terrain, which is the only thing DCS models — there is no earth
  curvature, so a mission may promise masking and may not promise a horizon.
- **`nav_headroom` is the question nobody thinks to ask until the cartridge is
  full.** The route wins every budget fight in `core/dtc.py`, so a twenty-point
  corridor leaves one navigation steerpoint for the whole F10 plan — and finding
  that out from `arm_plan`'s warning *after* a build is finding it out too late
  to change the route.

Geometry only, like `core/routing.py` and `core/frontline.py`: `Point`s and a
`MapOverlay` in, `Point`s out. What a route *means* — which valley, what the
briefing calls each point, where the IP goes — stays the mission's decision.

## Auditing a built mission (project-owned)

[`audit`](src/dcs_mission_creator/core/audit.py) checks the mechanical half of
the rules in this file, which was until now checked by eye. Every check exists
because the project shipped the mistake it looks for.

```bash
uv run dcs-mission-creator audit kuban_forge     # or omit the slug for all eight
```

It runs against `MissionBuilder.assemble` — the mission exactly as it would be
written, finishing steps and all, minus the save. That split is the reason the
audit is usable at all: a `generate` renders text-to-speech, writes a
five-megabyte archive and draws kneeboard pages, none of which says anything
about whether a waypoint is inside a mountain. Exit code is non-zero if anything
came back at `error`, so it works as a gate.

What it checks: commanded speeds against each airframe's own `max_speed` (the
knots-for-km/h mistake below the floor, afterburner above it), the departure
gate `set_departure_speeds` is supposed to have corrected, take-off and landing
points sitting on the field, **client** routes against the terrain at every
waypoint and along every leg, the cartridge's navigation headroom, enemy groups
left visible on the F10 map, flights with empty pylons, stores on stations the
game itself does not use (`core/loadout_check.py`), and the player flight's
air-to-air magazine against the number of enemy aircraft.

**Findings, not failures.** Several are heuristics about design and a mission is
allowed to be deliberate about any of them. `idlib_gauntlet`'s only warning is
an unarmed MQ-9, which is what a spotter is; `kuban_forge`'s only station note
is the AN/AAQ-33 where ED ships the LITENING, which its own `_FITS` docstring
already calls a deliberate substitution and gives a reason for. Both are the
system working: the check names the deviation, the mission answers it in prose,
and nothing is silently different from the rest of the project. The point is
that the deliberate cases should be the only ones left.

Three false positives had to be killed before the output was worth reading, and
each is a fact about pydcs rather than about any mission:

- **`alt_type`.** pydcs writes most altitudes `"BARO"` (AMSL) and the two
  runway gates `"RADIO"` (above the ground). Read without the type, the 300 m
  gate at Vaziani is 133 m of solid rock, and the audit's first run reported it
  on every mission from a field above 300 m.
- **The approach gate.** `add_runway_waypoint` writes 108 kt at both ends;
  `set_departure_speeds` corrects the departure one because that is below a
  loaded jet's stall speed, and leaves the approach one alone because by then
  the jet is light. Flagging it is the loudest false positive available — every
  flight in the project has one.
- **Water.** The elevation layer holds depth below datum out at sea, so
  `ansariyah_works` — which crosses 250 km of it at sixty metres, under a hard
  deck taken from the S-200's own weapon table — read as flying underground for
  its whole ingress. Sea level is the floor for a route (`waypoints.
  leg_violation(floor_m=…)`), and only for a route.

# Repo hygiene

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
- The three tool modules follow the same constraint and are worth copying from:
  `test_route_plan.py` stands up a synthetic elevation array with the **real**
  row/column transform on it, so the bulk window and the point query cannot
  disagree about which cell a coordinate is in — which is the one way that stub
  could make a broken planner look correct. `test_audit.py` builds two-point
  missions by hand rather than a real one. `test_loadout_check.py` parses a
  fixture payload block instead of the install.

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

## Existing missions

They are in [src/dcs_mission_creator/missions/](src/dcs_mission_creator/missions/),
and each module's own docstring is that mission's brief — what it is a reference
for, what its geometry is built on, what it deliberately does not do. Read the
docstring rather than a summary of it; `README.md` has the one-line index. There
is no list of them here on purpose: a count in prose has no owner and goes stale
the first time one is added, which is how two catalogues came to be missing the
same two missions.

What is worth stating as a *rule* rather than as a catalogue is the one thing
they all do the same way: **every mission draws its plan with `PlanOverlay`,
hands the rings back as `Assembled.briefed_threats`, and lets the base conceal,
load and brief them.** What differs is only how far the estimate sits from
truth, and that is `difficulty` — a `trained` ring is 2 km off, `veteran` a
quarter wider and 4 km off, `ace` wider again and 6 km off. Nothing is withheld
entirely at any label, because withholding never withheld the position: it moved
the leak to the steerpoint the mission then built from the *true* site.

Two deviations are worth knowing about, because both are deliberate and both
would otherwise read as bugs. `kuban_forge` draws an *enemy* thing precisely —
the plant itself, as a `waypoint_label` at its true position — and says why in
its briefing: a chemical works has been on the 1:100,000 sheet for forty years.
Every ring around it is still an assessment. And `ansariyah_works` overrides the
briefed radius on its SA-5 row, printing the 160 km its flight plan was built
from rather than the 255 km the jet's own threat table carries.

