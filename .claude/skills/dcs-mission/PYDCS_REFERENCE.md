# pydcs LLM reference

Task-oriented API reference for writing DCS missions with **pydcs**
(the version vendored under
[.venv/lib/python3.12/site-packages/dcs/](../../../.venv/lib/python3.12/site-packages/dcs/)).
Every signature below was read from that installed source — trust it over
memory, and re-grep the source when you touch an API that isn't listed.
`SKILL.md` covers *design* (what package to build, difficulty policy);
this file covers *the API* (what to call).

> **Golden rule.** pydcs ships thousands of unit/plane/vehicle classes with
> inconsistent names (`F_16C_50` but `MiG_29S`; `Strela_10M3` but
> `X_1L13_EWR`). Never invent a class or attribute name — `grep` it first:
> ```bash
> DCS=.venv/lib/python3.12/site-packages/dcs
> grep -nE "^class <Name>" "$DCS/planes.py"        # or helicopters.py / ships.py
> grep -nE "^class <Name>" "$DCS/vehicles.py"      # AirDefence / Armor / … namespaces
> ```

---

## 1. Imports you almost always need

```python
from datetime import datetime, timezone
from dcs.mission import Mission, StartType
from dcs.terrain import Caucasus              # PersianGulf, Syria, Nevada, …
from dcs import task                          # task.CAP, task.SEAD, task.GroundAttack, …
from dcs import planes, helicopters, vehicles, ships, countries
from dcs.unit import Skill
from dcs.mapping import Point
from dcs.point import PointAction              # rarely needed directly
from dcs.triggers import TriggerOnce, TriggerContinious, TriggerStart
from dcs import condition, action
from dcs.action import Coalition
from dcs.weather import Weather, Wind, CloudPreset
from dcs.drawing.drawings import StandardLayer
from dcs.drawing.drawing import Rgba, LineStyle
from dcs.drawing.icon import StandardIcon
```

`Point` (in `dcs.mapping`) is DCS **world meters**, not lat/lon. `x` is
roughly north/south, `y` roughly east/west depending on terrain. Anchor on
an airport and offset — see §9.

---

## 2. Mission lifecycle

```python
m = Mission(terrain=Caucasus())          # default terrain is Caucasus
...                                       # build everything
miz_path.parent.mkdir(parents=True, exist_ok=True)   # pydcs won't mkdir
m.save(str(miz_path))                     # write the .miz

status = Mission().load_file(str(miz_path))   # reload to sanity-check
assert not status                             # empty list == clean
```

- `Mission.__init__` **pre-populates all coalitions and countries** on both
  sides. Do not call any `add_country`; just fetch with `m.country("USA")` /
  `m.country("Russia")`. The argument is a **string** —
  `m.country(countries.USA.name)`, never `m.country(countries.USA)`.
- `m.start_time` is a UTC `datetime`. Convert local→UTC by the terrain
  offset. Caucasus is UTC+4, so 10:00 local on 15 May 2026 =
  `datetime(2026, 5, 15, 6, 0, 0, tzinfo=timezone.utc)`.
- There is **no `mission.duration`**. End the sortie with triggers (§7) or
  let bingo fuel resolve it.
- Import/save on non-Windows prints
  `"Cannot read registry keys on non-Windows OS, returning None"` and
  `"Couldn't detect any installed DCS World version"`. Harmless — do not
  suppress.

### Terrain classes (`dcs.terrain`)
`Caucasus`, `PersianGulf`, `Syria`, `Nevada`, `Normandy`, `TheChannel`,
`Sinai`, `Falklands`, `MarianaIslands` (**not** `Marianas`), `Kola`,
`Germany`.

---

## 3. Airports

**Dict-indexed, not methods.** Use display names:

```python
batumi = m.terrain.airports["Batumi"]           # "Senaki-Kolkhi", "Sukhumi-Babushara", …
batumi.set_blue()                               # set_red() / set_neutral()
pos = batumi.position                           # a Point — anchor offsets here
```

Caucasus airports default **neutral** — always set a coalition explicitly for
any base you spawn from. Bullseye lives at `m.terrain.bullseye_blue` /
`bullseye_red`, each a `{"x": …, "y": …}` dict.

---

## 4. Flight groups

All flight helpers live on `Mission` (`dcs/mission.py`). They return a
`FlyingGroup`; iterate `group.units` to set skill/callsign.

### 4.1 Generic spawn
```python
grp = m.flight_group_from_airport(
    country, name, aircraft_type, airport,
    maintask=None,               # defaults to aircraft_type.task_default
    start_type=StartType.Cold,   # Cold | Warm | Runway — NO "Hot"
    group_size=1,                # capped at aircraft_type.group_size_max (≤4 for fighters)
    parking_slots=None,
)
```
- `StartType.Warm` = hot ramp, engines running — the project default for
  player and AI flights unless the user asks for cold start.
- `group_size` silently clamps to `aircraft_type.group_size_max`. AI flights
  of 5+ desync formations — keep to ≤4.

### 4.2 Purpose-built helpers
| Helper | Signature highlights | Builds |
|---|---|---|
| `awacs_flight` | `(country, name, plane_type, airport, position, race_distance=30_000, heading=90, altitude=4500, speed=550, start_type=Cold, frequency=140)` | Race-track AWACS. `airport=None` → spawns airborne. |
| `refuel_flight` | same as awacs + `tacanchannel="10X"`, `speed=407` | Tanker on `task.Refueling`. |
| `patrol_flight` | `(country, name, patrol_type, airport, pos1, pos2, start_type=Cold, speed=600, altitude=4000, max_engage_distance=60_000, group_size=2)` | **CAP** — two-point race-track that engages air within `max_engage_distance`. There is **no `cap_flight`**. |
| `escort_flight` | `(country, name, escort_type, airport, group_to_escort, …)` | Escort tasked to a target group. |
| `intercept_flight` | `(country, name, patrol_type, airport, zone, late_activation=True, …, group_size=2)` | Scrambles when blue enters `zone`. **Needs a `TriggerZoneCircular`** (§8). Auto-creates a `TriggerContinious` + `AITaskPush`. |
| `sead_flight` | `(country, name, plane_type, target_pos, airport, start_type=Cold, max_engage_distance=20_000, group_size=2)` | SEAD at a position. |
| `strike_flight` | `(country, name, _type, target, airport, start_type=Cold, group_size=2)` | Strike a single **`Unit`** (e.g. `group.units[0]`), not a group. IP/attack/fence-out/RTB added automatically when `airport` given. |

`*_to_group` variants (`patrol_flight_to_group`, `sead_flight_to_group`,
`strike_flight_to_group`) take an already-created `FlyingGroup` and just add
the tasking waypoints — use when you built the group yourself.

### 4.3 Manual waypoints (`unitgroup.FlyingGroup`)
```python
grp.add_runway_waypoint(airport)
grp.add_waypoint(position, altitude, speed=600, name=None)   # returns MovingPoint
grp.land_at(airport)                                          # RTB waypoint
```

### 4.4 Skills, clients, callsigns
```python
for u in grp.units:
    u.skill = Skill.Client        # coop human slots — NEVER Skill.Player for multi-slot
```
- `Skill` enum: `Average, Good, High, Excellent, Random, Player, Client`.
- Player/coop slots → `Skill.Client`. AI → difficulty-derived (`High` for
  trained, `Excellent` for ace bosses).
- pydcs auto-assigns callsigns; the flight `name` seeds them. Project
  convention: player USAF/NATO `Dodge`/`Springfield`/`Uzi`, player Russian
  `Boris`/`Ivan`; AI `Magic`/`Hawg`/`Eagle`/`Texaco`.

---

## 5. Ground, ships, statics

```python
# single type, N units
grp = m.vehicle_group(country, name, _type, position, heading=0, group_size=N,
                      formation=VehicleGroup.Formation.Line)

# mixed types in one group — use for a convoy
grp = m.vehicle_group_platoon(country, name, types, position, heading=0, …)

# ships
sg = m.ship_group(country, name, _type, position, heading=0, group_size=1)

# a single static object (building, cargo, dead vehicle)
st = m.static_group(country, name, _type, position, heading=0, hidden=False, dead=False)
```

### Formations (`dcs.unitgroup.VehicleGroup.Formation`)
`Line` (default — 20 m perpendicular row, reads as scripted from the air),
`Vee`, `Rectangle`, `Star`, `Scattered`. Pass `formation=…` or call
`group.formation_<x>(heading, distance=…)` post-spawn;
`formation_scattered(heading=0, max_radius=None)`. No-op for `group_size=1`.

### Vehicle catalog (`dcs.vehicles`, namespaced)
- `vehicles.AirDefence.*` — SAM/AAA/EWR. Leading-`X_` names for radars/TELs:
  `X_1L13_EWR`, `X_55G6_EWR`, `X_2S6_Tunguska`, `X_5p73_s_125_ln`.
  SHORAD: `Strela_10M3` (SA-13), `Osa_9A33_ln` (SA-8), `Tor_9A331` (SA-15),
  `Kub_2P25_ln` (SA-6 TEL), `ZSU_23_4_Shilka`.
- `vehicles.Armor.*` — `T_72B`, `T_72B3`, `T_55`, `BTR_80`, `BTR_D`, …
- `vehicles.Artillery`, `.Infantry`, `.Fortification`, `.Unarmed`,
  `.MissilesSS`, `.Locomotive`, `.Carriage`.

### Ships (`dcs.ships`)
Carriers: `Stennis`, `CVN_71`/`72`/`73`/`75`, `KUZNECOW`. Grep for the rest.

### SAM / air-defense site builders
Don't hand-place radar + TR + launchers unit-by-unit; build a whole site in
one call.
- **pydcs** `dcs.templates.VehicleTemplate` — canned sites for the systems it
  covers: `sa6_site`, `sa11_site`, `sa15_site`, `Russia.sa10_site`,
  `USA.patriot_site`, `USA.hawk_site`, signature
  `(mission, [country,] position, heading, prefix="", skill=Skill.Average)`.
- **project** [core/air_defense.py](../../../src/dcs_mission_creator/core/air_defense.py)
  fills the gaps pydcs has no template for:
  `build_sa2/sa3/sa5/sa8/sa13/sa15/sa19_site`,
  `build_nasams/irist/roland/rapier/hq7_site`. See §12 / CLAUDE.md.

---

## 6. Tasks (`dcs.task`)

`MainTask` subclasses (pass as `maintask=`): `Nothing`, `AWACS`,
`AntishipStrike`, `CAS`, `CAP`, `Escort`, `FighterSweep`, `GroundAttack`,
`Intercept`, `PinpointStrike`, `Reconnaissance`, `Refueling`, `SEAD`,
`Transport`. The purpose-built helpers in §4.2 set these for you.

`task.EngageTargets(max_distance, [task.Targets.All.Air])` and friends are
what `patrol_flight` uses internally — reach for them only when hand-rolling
tasking. `task.Targets` is a metaclass tree (`Targets.All.Air`,
`Targets.All.GroundUnits`, …).

Enroute tasks and **options** are appended to a group's spawn-waypoint
ComboTask: `group.points[0].tasks.append(<task>)` (this is how pydcs's own
flight helpers do it). Group-level init tasks use
`group.add_trigger_action(<task>)`.

### 6.1 JTAC / FAC (talk-ons & lasing)
```python
group.points[0].tasks.append(task.FACAttackGroup(
    target_group.id, target_group.name,           # BOTH must match — mismatch = silent no-op
    designation=task.Designation.Laser,           # lases the target for LGBs
    frequency=133, modulation=task.Modulation.AM)) # MHz the JTAC talks on
```
- `task.Designation`: `Auto`, `No`, `WP` (white-phos mark), `IR_Pointer`,
  `Laser`. `task.WeaponType` picks what the FAC clears you to use.
- `FACAttackGroup` works on a **ground JTAC vehicle group** or an airborne
  **FAC(A)** flight (give the flight `maintask=task.AFAC`). `FACEngageGroup`
  is the fire-and-forget variant.
- **Project wrapper:** `tasking.fac_attack_group(fac_group, target_group,
  frequency=…)` derives both id+name from one group and defaults to Laser.

### 6.2 AI behaviour options — the difficulty dial
Each is a `task.Opt*` appended to `points[0].tasks`:
- `OptROE(OptROE.Values.X)` — `WeaponFree` / `OpenFireWeaponFree` / `OpenFire`
  / `ReturnFire` / `WeaponHold`. Whether/when the AI shoots.
- `OptReactOnThreat(Values.X)` — `NoReaction` / `PassiveDefense` (CM only) /
  `EvadeFire` / `ByPassAndEscape` / `AllowAbortMission`.
- `OptRadarUsing(Values.X)` — EMCON: `NeverUse` … `UseForContinuousSearch`.
- `OptECMUsing(Values.X)` — `NeverUse` … `AlwaysUse`.
- `OptAlarmState(value)` — ground/SAM readiness (0 auto / 1 green-radars-off /
  2 red-hot). Green = radars dark until threatened → ambush.
- Booleans: `OptRTBOnBingoFuel(bool)`, `OptRTBOnOutOfAmmo(...)`,
  `OptRestrictAfterburner(bool)`, `OptRestrictJettison(bool)`.
- **Project wrapper:** `tasking.apply_ai_difficulty(group, difficulty)` sets
  ROE/react/radar/ECM/bingo/afterburner from a recruit→ace label in one call.

### 6.3 Cold-ramp scramble (uncontrolled + `StartCommand`)
Aircraft sit shut down on the ramp until a trigger starts them — an alert-5,
distinct from `late_activation` (pops fully airborne).
```python
group.add_trigger_action(task.StartCommand()); group.uncontrolled = True
t = TriggerOnce(comment="scramble"); t.rules.append(<condition>)
t.actions.append(action.AITaskPush(group.id, 1))
m.triggerrules.triggers.append(t)
```
- pydcs `FlyingGroup.delay_start(m, seconds)` does this for a **time** trigger.
- **Project wrapper:** `tasking.scramble_on_trigger(m, group, *conditions)`
  generalizes it to any condition(s).

### 6.4 Controlled tasks (start/stop conditions)
`task.ControlledTask(inner_task)` gates any task on conditions:
`.start_after_time(t)`, `.stop_after_time(t)`, `.stop_after_duration(t)`,
condition variants. Generalizes the `AITaskPush` intercept trick.

### 6.5 Unit `WrappedAction` commands (`points[0].tasks.append(...)`)
- **TACAN/beacon:** `task.ActivateBeaconCommand(channel=10, modechannel="X",
  callsign="TKR", bearing=True, unit_id=0, aa=True)` — DCS frequency computed
  internally (`aa=True` yardstick, `aa=False` ground). `refuel_flight` already
  adds one; use this for AWACS/FARP/ship beacons. `DeActivateBeaconCommand`.
- **Carrier recovery:** `task.ActivateICLSCommand(channel, unit_id)`,
  `ActivateLink4Command`, `ActivateACLSCommand` (+ their `DeActivate*`).
- **Radio:** `task.TransmitMessage(soundfile_reskey, subtitle_resstring,
  loop=False, subtitle_duration=5)` broadcasts a sound on the current freq;
  `StopTransmission`; `SetFrequencyCommand(freq)`. Pairs with `VoiceSynth`.
- **FX/state:** `task.SmokeCommand(bool)` (colored marker smoke),
  `SetInvisibleCommand`, `SetImmortalCommand`, `SetCallsignCommand`, `EPLRS`.

### 6.6 Carrier recovery tanker
`task.RecoveryTanker(groupId, speed, altitude, lastWaypoint)` on the tanker's
`points[0].tasks` — `groupId` is the carrier group it recovers to.

---

## 7. Triggers & mission-end (`dcs.triggers`, `dcs.condition`, `dcs.action`)

Trigger rules go on `m.triggerrules.triggers`. Most end-logic is a
`TriggerOnce`.

```python
from dcs.triggers import TriggerOnce
from dcs import condition, action

t = TriggerOnce(comment="convoy destroyed")
t.rules.append(condition.GroupDead(convoy.id))
t.actions.append(action.MessageToCoalition(
    m.string("Convoy destroyed. RTB."), 20,
    coalitionlist=Coalition.Blue))
m.triggerrules.triggers.append(t)
```

- **Trigger classes:** `TriggerOnce`, `TriggerContinious` (sic), `TriggerStart`,
  `TriggerCondition`.
- **Conditions (`dcs.condition`):** `GroupAlive(id)`, `GroupDead(id)`,
  `GroupLifeLess(id, percent)`, `UnitAlive`, `UnitDead`, `TimeAfter`,
  `TimeBefore`, `FlagEquals`, `AllOfCoalitionInZone`, `PartOfCoalitionInZone`.
- **Actions (`dcs.action`):** `MessageToAll(text, seconds, clearview=False)`,
  `MessageToCoalition(text, seconds, …, coalitionlist=Coalition.Blue)`,
  `SoundToAll` / `SoundToCoalition` / `SoundToGroup` (voice helper wraps these).
- `Coalition` enum (`Coalition.Blue` / `Coalition.Red`) lives in
  **`dcs.action`**, not `dcs.coalition` (that's the Mission's instance class).
- **Wrap all displayed text in `m.string("…")`** to register it in the
  translation table before handing it to a `MessageTo*`/briefing setter.

### Briefing setters (on `Mission`)
`set_description_text`, `set_description_bluetask_text`,
`set_description_redtask_text`, `set_sortie_text`. Plain text.

- **Briefing pictures:** `m.add_picture_blue(filepath)` /
  `add_picture_red` / `add_picture_neutral` — image slides on the briefing
  screen (target photo, map snapshot). Returns a `ResourceKey`.
- **Kneeboard:** `m.add_aircraft_kneeboard(aircraft_type, page_path)` — an
  in-cockpit kneeboard page (freqs, laser codes, target photo) per airframe.

---

## 8. Trigger zones

```python
zone = m.triggers.add_triggerzone(position, radius, hidden=False,
                                  name="zone", color=None)   # TriggerZoneCircular
```
Feed to `intercept_flight(zone=…)` (make it `hidden=True` for a scramble
trigger). `PartOfCoalitionInZone("blue", zone.id)` reads it in a condition.

---

## 9. Coordinates (`dcs.mapping.Point`)

```python
p = airport.position                          # a Point
q = p.point_from_heading(hdg_deg, dist_m)     # offset along a heading
hdg = p.heading_between_point(other)          # degrees
d   = p.distance_to_point(other)              # meters
p2  = p + Point(1000, 0, m.terrain)           # __add__ / __sub__ supported
```
`Point(x, y, terrain)` — the third arg is the terrain instance. Build all
world positions by offsetting from a known anchor (an airport, bullseye),
never from raw lat/lon.

---

## 10. Weather (`m.weather`, a `Weather` instance)

```python
m.weather.season_temperature = 20            # °C
m.weather.qnh = 760                          # mmHg
m.weather.wind_at_ground = Wind(direction=270, speed=4)   # deg, m/s
m.weather.wind_at_2000 = Wind(…)
m.weather.wind_at_8000 = Wind(…)
m.weather.clouds_base = 1500                 # m
m.weather.clouds_thickness = 500             # m
m.weather.clouds_density = 4                 # 0 clear · 3–4 scattered · 5–7 broken · 8–10 overcast
m.weather.clouds_preset = CloudPreset.by_name("…")        # optional named preset
m.weather.clouds_iprecptns = Weather.Preceptions.Rain     # None_ | Rain | Thunderstorm (sic)
m.weather.visibility_distance = 80000        # m
m.weather.enable_fog = False
m.weather.fog_thickness = 0
m.weather.fog_visibility = 0
```
`Wind` has `.direction` (deg) and `.speed` (m/s).

---

## 11. F10 map drawings (`dcs.drawing.*`)

Coloured shapes/labels on the in-game F10 planning map — **not** 3D-world
objects. Never construct drawing classes directly; call `add_*` on a
**layer**.

```python
from dcs.drawing.drawings import StandardLayer
from dcs.drawing.drawing import Rgba, LineStyle
from dcs.drawing.icon import StandardIcon

blue = m.drawings.get_layer(StandardLayer.Blue)   # Red | Blue | Neutral | Common | Author
blue.add_circle(center, radius=25_000, color=Rgba(255,0,0,255), fill=Rgba(255,0,0,40))
blue.add_text_box(center, "SA-6 (est.)", font_size=20, angle=0,
                  color=Rgba(255,0,0,255), fill=Rgba(0,0,0,0))
blue.add_icon(pos, StandardIcon.AirDefense, scale=1.0, color=Rgba(255,0,0,255))
blue.add_arrow(start_pos, angle=heading_deg, length=30_000)
```

- **Layer visibility:** `Blue` → blue coalition only, `Red` → red only,
  `Common` → everyone. Put player-facing plan annotations on `Blue`.
- **Colours:** `Rgba(r, g, b, a)` 0–255. Fills want low alpha
  (`Rgba(255,0,0,60)`), outlines full (`…,255)`). `LineStyle`: `Solid`,
  `Dash`, `Dot`, `DotDash`, `Square`.
- **`StandardIcon`** NATO symbols: `Mechanized`, `MechanizedInfantry`,
  `MechanizedInfantryWithFightingVehicle`, `Recce`, `Logistics`,
  `MechanizedArtillery`, `MechanizedRocketArtillery`, `AirDefense`,
  `SearchRadar`. Or a raw `.png` filename string.

### Full `add_*` set (on `dcs.drawing.layer.Layer`)
| Method | Coord model |
|---|---|
| `add_circle(position, radius)` | absolute `position` |
| `add_oval(position, r1, r2, angle=0)` | absolute |
| `add_rectangle(position, width, height, angle=0)` | absolute |
| `add_arrow(position, angle, length)` | absolute; `angle` in degrees |
| `add_icon(position, file\|StandardIcon, scale=1.0)` | absolute |
| `add_text_box(position, text, font_size=20, angle=0)` | absolute |
| `add_oblong(p1, p2, radius)` | **two absolute** points (capsule/corridor) |
| `add_line_segment(position, end_point)` | offset-based ↓ |
| `add_line_segments(position, points, closed=False)` | offset-based |
| `add_line_freeform(position, points, closed=False)` | offset-based |
| `add_freeform_polygon(position, points)` | offset-based |

**Coordinate gotcha.** For the point-list drawings (`line_segment(s)`,
`line_freeform`, `freeform_polygon`) `position` is the anchor and `points`
are **offsets relative to that anchor** — the first is usually
`Point(0, 0, terrain)`. Build offsets with
`anchor.point_from_heading(hdg, dist)` then subtract the anchor.
`add_oblong` is the exception: two absolute points, it does the transform.

---

## 12. Project-owned helpers

Two project helpers wrap the raw pydcs API above — **prefer them** over
calling §11 / triggers directly. Their contracts live in
[CLAUDE.md](../../../CLAUDE.md) (they are project convention, not pydcs):

- **`PlanOverlay`** ([core/map_draw.py](../../../src/dcs_mission_creator/core/map_draw.py))
  — wraps §11 with blue-layer placement + difficulty-scaled enemy reveal.
- **`VoiceSynth`** ([core/tts/synth.py](../../../src/dcs_mission_creator/core/tts/synth.py))
  — TTS → cached WAV → `SoundTo*` action on a trigger rule.
- **air-defense builders** ([core/air_defense.py](../../../src/dcs_mission_creator/core/air_defense.py))
  — `build_sa2/sa3/sa5/sa8/sa13/sa15/sa19_site`,
  `build_nasams/irist/roland/rapier/hq7_site` `(m, country, position, heading,
  *, launchers=…, skill=…, overlay=…, terrain=…)`. Fills the SAM sites pydcs's
  `VehicleTemplate` lacks (§5). Absolute `Point` in, `VehicleGroup` out.
- **AI-tasking wrappers** ([core/tasking.py](../../../src/dcs_mission_creator/core/tasking.py))
  — `apply_ai_difficulty(group, difficulty)` (§6.2 dial),
  `fac_attack_group(fac, target, …)` (§6.1), `scramble_on_trigger(m, group,
  *conditions)` (§6.3). Only the ones with real project policy — carrier/nav
  beacons stay raw pydcs (§6.5).

---

## 13. Gotcha checklist

- `cap_flight` does **not** exist → `patrol_flight(patrol_type=…)`.
- Airports dict-indexed, display names: `m.terrain.airports["Batumi"]`.
- `m.country("USA")` takes a **string**.
- `StartType` has no `Hot`; `Warm` is hot-ramp.
- `group_size` caps at 4 for fighters; ≥5 AI desyncs formation output.
- `Point` is world meters, not lat/lon; third ctor arg is the terrain.
- `m.save(path)` does **not** mkdir the parent.
- No `mission.duration` — end via triggers or bingo fuel.
- `Coalition` is in `dcs.action`, not `dcs.coalition`.
- `TriggerContinious` and `Preceptions` are spelled that way in the source.
- Never commit `.miz` (binary, gitignored).
- Run `ruff check`, `ruff format --check`, `ty check` on `src/` after edits.
