# pydcs LLM reference

Task-oriented API reference for **pydcs** — the version installed in this
project's `.venv/`. Every signature below was read from that installed source —
trust it over memory, and re-grep the source when you touch an API that isn't
listed. `SKILL.md` covers *design*; this file covers *the API*.

> **Golden rule.** pydcs ships thousands of unit/plane/vehicle classes with
> inconsistent names (`F_16C_50` but `MiG_29S`; `Strela_10M3` but
> `X_1L13_EWR`). Never invent a class or attribute name — `grep` it first:
> ```bash
> DCS=$(uv run python -c 'import dcs, pathlib; print(pathlib.Path(dcs.__file__).parent)')
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

`Point` (in `dcs.mapping`) is DCS **world meters**, not lat/lon. `x` is roughly
north/south, `y` roughly east/west. Anchor on an airport and offset — see §9.

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
  sides. Do not call any `add_country`; fetch with `m.country("USA")` /
  `m.country("Russia")`. The argument is a **string** —
  `m.country(countries.USA.name)`, never `m.country(countries.USA)`.
- `m.start_time` is a `datetime` whose **wall clock is map-local time — do not
  apply any UTC offset**. pydcs serialises it as `hour*3600 + minute*60 + second`
  verbatim (`mission.py`, `_get_mission_dict`) and DCS reads that field as local
  time, so `tzinfo` never reaches the `.miz`. 10:00 local on Caucasus is
  `datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)` — the `tzinfo` is inert
  filler that keeps ruff's `DTZ` rules quiet. Subtracting the terrain offset
  silently moves the mission four hours earlier.
- There is **no `mission.duration`**. End the sortie with triggers (§7) or let
  bingo fuel resolve it.
- `m.forced_options` (`dcs.forcedoptions.ForcedOptions`) is the ME's *Mission
  Options* panel: every field is `None` until set, and only the set ones reach
  the `.miz`'s `forcedOptions` table. Fields are snake_case mirrors of the Lua
  keys — `permit_crash`, `external_views`, `labels`, `easy_radar`,
  `unrestricted_satnav`, `immortal`, `fuel`, `weapons`, … The project forces
  exactly one, in `MissionBuilder._permit_crash_recovery`: `permit_crash = True`,
  without which a crash ends the sortie at the debriefing instead of returning
  to slot selection.
- Import/save on non-Windows prints `"Cannot read registry keys on non-Windows
  OS, returning None"` and `"Couldn't detect any installed DCS World version"`.
  Harmless — do not suppress.

### Terrain classes (`dcs.terrain`)
`Caucasus`, `PersianGulf`, `Syria`, `Nevada`, `Normandy`, `TheChannel`, `Sinai`,
`Falklands`, `MarianaIslands` (**not** `Marianas`), `Kola`, `Germany`.

---

## 3. Airports

**Dict-indexed, not methods.** Use display names:

```python
batumi = m.terrain.airports["Batumi"]           # "Senaki-Kolkhi", "Sukhumi-Babushara", …
batumi.set_blue()                               # set_red() / set_neutral()
pos = batumi.position                           # a Point — anchor offsets here
```

Caucasus airports default **neutral** — always set a coalition explicitly for any
base you spawn from. Bullseye lives at `m.terrain.bullseye_blue` /
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
- `StartType.Warm` = hot ramp, engines running — the project default.
- `group_size` silently clamps to `aircraft_type.group_size_max`. AI flights of
  5+ desync formations — keep to ≤4.

### 4.2 Purpose-built helpers
| Helper | Signature highlights | Builds |
|---|---|---|
| `awacs_flight` | `(country, name, plane_type, airport, position, race_distance=30_000, heading=90, altitude=4500, speed=550, start_type=Cold, frequency=140)` | Race-track AWACS. `airport=None` → spawns airborne. `speed` is **km/h TAS**, used for the transit waypoints *and* the `OrbitAction`. |
| `refuel_flight` | same as awacs + `tacanchannel="10X"`, `speed=407` | Tanker on `task.Refueling`. The 407 default is 220 kt TAS, far too slow for a KC-135; override it. |
| `patrol_flight` | `(country, name, patrol_type, airport, pos1, pos2, start_type=Cold, speed=600, altitude=4000, max_engage_distance=60_000, group_size=2)` | **CAP** — two-point race-track engaging air within `max_engage_distance`. There is **no `cap_flight`**. |
| `escort_flight` | `(country, name, escort_type, airport, group_to_escort, …)` | Escort tasked to a target group. |
| `intercept_flight` | `(country, name, patrol_type, airport, zone, late_activation=True, speed=600, altitude=4000, …, group_size=2)` | Scrambles when blue enters `zone`. **Needs a `TriggerZoneCircular`** (§8). Auto-creates a `TriggerContinious` + `AITaskPush`. |
| `sead_flight` | `(country, name, plane_type, target_pos, airport, start_type=Cold, max_engage_distance=20_000, group_size=2)` | SEAD at a position. |
| `strike_flight` | `(country, name, _type, target, airport, start_type=Cold, group_size=2)` | Strike a single **`Unit`** (e.g. `group.units[0]`), not a group. IP/attack/fence-out/RTB added automatically when `airport` given. |

`*_to_group` variants (`patrol_flight_to_group`, `sead_flight_to_group`,
`strike_flight_to_group`) take an already-created `FlyingGroup` and just add the
tasking waypoints.

`sead_flight_to_group` / `strike_flight_to_group` pick their own speed as
`flight_type().max_speed * 0.8` — for an F-16C that is **1696 km/h, Mach 1.4**,
i.e. the entire route in afterburner. One more reason to build strike routes by
hand (§4.3).

> **Gotcha — every pydcs speed argument is km/h true airspeed, and not one of
> them says so.** `add_waypoint`, `OrbitAction`, `awacs_flight`,
> `refuel_flight`, `patrol_flight`, `intercept_flight` and
> `flight_group_inflight` all store `speed / 3.6`. Nothing validates the number,
> so a knots-shaped value is accepted in silence and commands **~54 %** of the
> intended speed. The symptom is the friendly package flying its whole sortie in
> afterburner: at those speeds a fighter is far below best-climb speed and deep
> on the back side of the drag curve, and the AI holds the commanded altitude on
> the throttle. Sanity bound: `FlyingType.max_speed` is km/h as well (F-15C 2650,
> F-16C 2120, F/A-18C 1950, MiG-29S 2450, Su-27 2500, A-10C **720**, E-3A 860,
> KC-135 980, MQ-9 400), so a cruise or orbit speed sits around 0.3–0.4 of it and
> anything under ~0.2 is the unit error. Note the A-10C bound especially —
> 400 kt is 740 km/h, *above* its never-exceed — so the correction is
> per-airframe km/h values, never a blanket ×1.852.

### 4.3 Manual waypoints (`unitgroup.FlyingGroup`)
```python
grp.add_runway_waypoint(airport)          # departure/approach point, alt 300 m RADIO (AGL)
                                          # speed hard-coded to 200 km/h — see below
grp.add_waypoint(position, altitude, speed=600, name=None)   # returns MovingPoint
grp.land_at(airport)                                          # RTB waypoint
```

`altitude` is **metres AMSL** — the point's `alt_type` is `"BARO"` unless you set
`mp.alt_type = "RADIO"` (AGL) on the returned `MovingPoint`. `speed` is **km/h
TAS** in, m/s in the file.

> **Gotcha — pydcs has no height map, so ground-level waypoints come out
> wrong.** `land_at()` writes `alt = 0`, and the take-off point built by
> `flight_group_from_airport` copies `units[0].alt`, which is also `0`: at
> Vaziani (464 m) or Kutaisi (44 m) both base waypoints are buried under the
> field. A steerpoint placed on a ground target has the opposite problem — it
> inherits whatever ingress altitude the route used. DCS hands those altitudes to
> the aircraft as steerpoint elevations (CCRP/CCIP, HUD, DED), so both have to be
> corrected. Use
> [`core/waypoints.py`](../../../src/dcs_mission_creator/core/waypoints.py):
> ```python
> from dcs_mission_creator.core import waypoints
>
> waypoints.add_ground_waypoint(player, target_pos, overlay=ov,
>                               speed=750, name="CONVOY AO")
> waypoints.snap_base_waypoints(m, ov)   # every flight's take-off + landing
> ov.elevation_at(point)                 # raw lookup (int m AMSL)
> ```
> Only client-flown routes want a deck-level target steerpoint — an AI flight
> flies its route altitudes into the terrain.

> **Gotcha — `add_runway_waypoint` commands 108 kt and takes no speed
> argument.** It hard-codes `mp.speed = 200 / 3.6` at 300 m AGL, so the first
> waypoint after rotation orders the flight to fly slower than it can: a loaded
> F/A-18C at ~19.6 t needs CL 2.8 to hold that, against a CLmax near 1.8 — 19 %
> below its stall speed. The AI pitches to max alpha and firewalls the throttle.
> Fix by overwriting the returned `MovingPoint`'s speed; the project does it for
> every flight in `MissionBuilder.build_miz` via
> `waypoints.set_departure_speeds`. The same call is used for the **approach**
> point, where 108 kt is near enough a real approach speed on a light jet.

### 4.4 Skills, clients, callsigns
```python
for u in grp.units:
    u.skill = Skill.Client        # coop human slots — NEVER Skill.Player for multi-slot
```
- `Skill` enum: `Average, Good, High, Excellent, Random, Player, Client`.
- Player/coop slots → `Skill.Client`. AI → difficulty-derived.
- pydcs auto-assigns callsigns; the flight `name` seeds them. Project convention:
  player USAF/NATO `Dodge`/`Springfield`/`Uzi`, player Russian `Boris`/`Ivan`; AI
  `Magic`/`Hawg`/`Eagle`/`Texaco`.

### 4.5 Loadouts — **spell them out, always**

> **Gotcha.** `load_task_default_loadout(task)` / `load_loadout(name)` read
> payload files from a **local DCS installation**
> (`dcs.payloads.PayloadDirectories`), found only via the Windows registry. This
> project feeds pydcs the path from `$DCS_INSTALL_DIR`
> ([core/dcs_install.py](../../../src/dcs_mission_creator/core/dcs_install.py),
> wired into `MissionBuilder.__init__`); with the var unset every flight ships
> with **empty pylons**.
>
> **Corollary.** `Mission.flight_group*` calls `load_task_default_loadout` itself
> at group creation, so a flight already carries a generic task loadout by the
> time you touch it. `load_pylon` merges into that — clear the stations first or
> the defaults survive on every pylon your list skips.

```python
f16 = planes.F_16C_50
for u in grp.units:              # make the spelled-out loadout authoritative
    u.pylons.clear()
grp.load_pylon(f16.Pylon3.AGM_88C_HARM___High_Speed_Anti_Radiation_Missile_)
grp.load_pylon(f16.Pylon10.AN_ASQ_213_HTS___HARM_Targeting_System)
```
- Each `PlaneType` carries `pylons` (valid station numbers) and a `PylonN` class
  per station whose attributes are the stores that station legally accepts —
  `(pylon_number, {"clsid": …})` tuples. Do not hand-write CLSIDs, and do not
  assume a store fits a station (the F-16C takes Mavericks on 3/7 but not 4/6).
- `FlyingGroup.load_pylon(store, pylon=None)` applies to every unit in the group.
  Discover options with
  `[a for a in dir(planes.F_16C_50.Pylon3) if not a.startswith("_")]`.
- **Pylons are per unit, and the group method hides that.** `FlyingUnit` carries
  its own `pylons` dict and its own `load_pylon`, so two slots of one flight can
  fly different fits — which every player flight in this project does. The group
  method is right for an AI flight and wrong for a coop one; `core/loadout.py`
  writes the per-slot version (`mission_kit.arm` is the group-wide one).
- Raw CLSIDs live in `dcs.weapons_data.Weapons`.

---

## 5. Ground, ships, statics

```python
# single type, N units
grp = m.vehicle_group(country, name, _type, position, heading=0, group_size=N,
                      formation=VehicleGroup.Formation.Line)

# mixed types in one group — use for a convoy
grp = m.vehicle_group_platoon(country, name, types, position, heading=0, …)

# ground movement: OnRoad on the spawn waypoint too, not just the destination
grp = m.vehicle_group_platoon(country, name, types, position, heading=hdg,
                              move_formation=PointAction.OnRoad)
grp.add_waypoint(destination, move_formation=PointAction.OnRoad, speed=40)  # km/h

# ships
sg = m.ship_group(country, name, _type, position, heading=0, group_size=1)

# a single static object (building, cargo, dead vehicle)
st = m.static_group(country, name, _type, position, heading=0, hidden=False, dead=False)
```

### Ground movement: `PointAction` / road pathing
`dcs.point.PointAction` is the *action* of a ground waypoint, and in DCS that
action governs the leg **leaving** that waypoint — so the last waypoint's action
is inert and waypoint 0's is what decides how the group drives its first leg.

`vehicle_group`, `vehicle_group_platoon` and `vehicle_group_from_vehicles` all
create waypoint 0 with `move_formation=PointAction.OffRoad`, and
`VehicleGroup.add_waypoint(position, move_formation=…, speed=…)` defaults the
same way. **Gotcha:** setting `OnRoad` only on the destination waypoint looks
right and does nothing. Pass `move_formation=PointAction.OnRoad` to the group
constructor **and** to every `add_waypoint`.

`speed` on `add_waypoint` is **km/h** — same unit as the flying-group call. Other
actions: `OffRoad`, `OnRailroads`, and the formation-while-moving values
`LineAbreast` (serialises as `"Rank"`), `Cone`, `Vee`, `Diamond`, `EchelonLeft`,
`EchelonRight`.

### Map visibility flags (any `unitgroup.Group`)
Set on the base `Group`, so flying, vehicle, ship and static groups all have
them. Purely cosmetic — a hidden group still spawns, radiates and shoots.

```python
grp.hidden = True             # "Hidden on map" — no F10 icon in game
grp.hidden_on_planner = True  # no icon on the briefing / mission-planner map
grp.hidden_on_mfd = True      # excluded from datalink / MFD symbology
```

Serialize as `hidden` / `hiddenOnPlanner` / `hiddenOnMFD`. `m.static_group(…)`
also takes `hidden=` as a ctor kwarg.

### Formations (`dcs.unitgroup.VehicleGroup.Formation`)
`Line` (default — 20 m perpendicular row), `Vee`, `Rectangle`, `Star`,
`Scattered`. Pass `formation=…` or call `group.formation_<x>(heading, distance=…)`
post-spawn; `formation_scattered(heading=0, max_radius=None)`. No-op for
`group_size=1`.

### Vehicle catalog (`dcs.vehicles`, namespaced)
- `vehicles.AirDefence.*` — SAM/AAA/EWR. Leading-`X_` names for radars/TELs:
  `X_1L13_EWR`, `X_55G6_EWR`, `X_2S6_Tunguska`, `X_5p73_s_125_ln`. SHORAD:
  `Strela_10M3` (SA-13), `Osa_9A33_ln` (SA-8), `Tor_9A331` (SA-15),
  `Kub_2P25_ln` (SA-6 TEL), `ZSU_23_4_Shilka`.
- `vehicles.Armor.*` — `T_72B`, `T_72B3`, `T_55`, `BTR_80`, `BTR_D`, …
- `vehicles.Artillery`, `.Infantry`, `.Fortification`, `.Unarmed`,
  `.MissilesSS`, `.Locomotive`, `.Carriage`.

### Ships (`dcs.ships`)
Carriers: `Stennis`, `CVN_71`/`72`/`73`/`75`, `KUZNECOW`. Grep for the rest.

### SAM / air-defense site builders
Don't hand-place radar + TR + launchers unit-by-unit.
- **pydcs** `dcs.templates.VehicleTemplate` — canned sites for the systems it
  covers: `sa6_site`, `sa11_site`, `sa15_site`, `Russia.sa10_site`,
  `USA.patriot_site`, `USA.hawk_site`, signature
  `(mission, [country,] position, heading, prefix="", skill=Skill.Average)`.
- **project** [core/air_defense.py](../../../src/dcs_mission_creator/core/air_defense.py)
  fills the gaps: `build_sa2/sa3/sa5/sa8/sa13/sa15/sa19_site`,
  `build_nasams/irist/roland/rapier/hq7_site`. See §12 / CLAUDE.md.

---

## 6. Tasks (`dcs.task`)

`MainTask` subclasses (pass as `maintask=`): `Nothing`, `AWACS`,
`AntishipStrike`, `CAS`, `CAP`, `Escort`, `FighterSweep`, `GroundAttack`,
`Intercept`, `PinpointStrike`, `Reconnaissance`, `Refueling`, `SEAD`,
`Transport`.

`task.EngageTargets(max_distance, [task.Targets.All.Air])` and friends are what
`patrol_flight` uses internally. `task.Targets` is a metaclass tree
(`Targets.All.Air`, `Targets.All.GroundUnits`, …).

Enroute tasks and **options** are appended to a group's spawn-waypoint ComboTask:
`group.points[0].tasks.append(<task>)`. Group-level init tasks use
`group.add_trigger_action(<task>)`.

### 6.1 JTAC / FAC (talk-ons & lasing)
```python
group.points[0].tasks.append(task.FACAttackGroup(
    target_group.id, target_group.name,           # BOTH must match — mismatch = silent no-op
    designation=task.Designation.Laser,           # lases the target for LGBs
    frequency=133, modulation=task.Modulation.AM)) # MHz the JTAC talks on
```
- `task.Designation`: `Auto`, `No`, `WP`, `IR_Pointer`, `Laser`.
  `task.WeaponType` picks what the FAC clears you to use.
- `FACAttackGroup` works on a **ground JTAC vehicle group** or an airborne
  **FAC(A)** flight (give the flight `maintask=task.AFAC`). `FACEngageGroup` is
  the fire-and-forget variant.
- **Project wrapper:** `tasking.fac_attack_group(fac_group, target_group,
  frequency=…, callsign=FacCallsign.HAMMER)`.

Four things the task does **not** do for you — miss any one and the player gets
no radio option and no laser spot, with nothing logged:

1. **Range.** The FAC only lases what its own sensor sees. Park an airborne FAC
   within ~10 km slant of the target (a race-track *abeam* the target's route,
   ~5 km cross-track, is the reliable shape). Beware deriving the offset from a
   "friendly side" heading that runs *along* the target's axis — check the
   cross-track distance.
2. **Endurance.** A plain waypoint list runs out and the FAC flies home. Hold it
   with `task.OrbitAction(alt, speed, RaceTrack)` on the first race-track
   waypoint, the far end as the next waypoint. Skip `land_at`. `alt` is metres
   AMSL and `speed` **km/h TAS** — give the orbit and its waypoints the same
   number or the flight fights itself.
3. **Callsign.** `callsign=` is an index into the fixed DCS FAC callname table
   (`Axeman, Darknight, Warrior, Pointer, Eyeball, Moonbeam, Whiplash, Finger,
   Pinpoint, Ferret, Shaba, Playboy, Hammer, Jaguar, Deathstar, Anvil, Firefly,
   Mantis, Badger` — see `countries.USA.callsign["GroundUnits"]`), **not** the
   group name. Default 1 makes a group named `Hammer` check in as *Axeman 1-1*.
   Use `tasking.FacCallsign`.
4. **Radio.** The FAC entry appears in the player's menu only on the FAC net. Set
   the FAC's own radio to match with `task.SetFrequencyCommand(mhz,
   Modulation.AM)` (pydcs otherwise leaves the group on 251.0) and brief the net
   as a **frequency** ("133.000 AM into COMM2"), never as a preset "CH N". A
   channel number next to the tanker's "TACAN 10X" reads as a TACAN channel.

A FAC parked close enough to see the target is usually inside a MEZ. Give it
`task.SetInvisibleCommand(True)`.

**Coordinates are MGRS-only and nothing in the mission changes that.** The 9-line
and the target call both format the position with `MGRS:make(point, 4)` in the
game's `Scripts/Speech/NATO.lua`, so a 4-digit grid is what every airframe is
read — including the ones with no way to enter one (the F-16's DED and the
Hornet's UFC take degrees and decimal minutes). There is no task field, option or
ME setting for it. **Project wrapper:** `jtac.arm_jtac_coords(m,
[CoordTarget(convoy, label="Hammer 1-1", what="the resupply column",
laser_code=1688)], menu_title="Hammer 1-1")` adds a radio request that answers in
the requesting cockpit's own format, off a live unit. Arm it *alongside* the FAC
task and give it `push_at_s`.

**The laser code is 1688 and no task carries it.** The ME's own `FAC - Attack
Group` action declares `groupId` and `weaponType` and nothing else
(`<DCS>/MissionEditor/modules/me_action_db.lua`), and pydcs's `FACAttackGroup`
adds only designation, frequency, modulation, callsign and datalink — so the code
in the AI controller's 9-line is DCS's own default. The bombs are the same story
for most of the fleet: the F-16C, F/A-18C and A-10C carry **no** laser-code
property, so their seekers come up on 1688. Only four families in pydcs expose
the code as `AddPropAircraft` (`unit.set_property`) — AV-8B and JF-17 as
`LaserCode100/10/1` digits (plus the Harrier's separate `GBULaserCode*`), F-4E as
`LaserCodeDigit1..4`, F-15E as `Sta2LaserCode` and friends — and every one
defaults to 1688 too. **Project wrapper:** `laser.set_code(flight,
laser.DEFAULT_CODE)`; `jtac.arm_jtac_coords` refuses a `CoordTarget.laser_code`
that is not `laser.AI_JTAC_CODE`. Per-pylon `settings = {laser_code = …}` exists
in ED's own F-14 payloads but pydcs's weapon table carries no `settings`.

### 6.2 AI behaviour options — the difficulty dial
Each is a `task.Opt*` appended to `points[0].tasks`:
- `OptROE(OptROE.Values.X)` — `WeaponFree` / `OpenFireWeaponFree` / `OpenFire` /
  `ReturnFire` / `WeaponHold`.
- `OptReactOnThreat(Values.X)` — `NoReaction` / `PassiveDefense` (CM only) /
  `EvadeFire` / `ByPassAndEscape` / `AllowAbortMission`.
- `OptRadarUsing(Values.X)` — EMCON: `NeverUse` … `UseForContinuousSearch`.
- `OptECMUsing(Values.X)` — `NeverUse` … `AlwaysUse`.
- `OptAlarmState(value)` — ground/SAM readiness (0 auto / 1 green-radars-off /
  2 red-hot). Green = radars dark until threatened → ambush.
- Booleans: `OptRTBOnBingoFuel(bool)`, `OptRTBOnOutOfAmmo(...)`,
  `OptRestrictAfterburner(bool)`, `OptRestrictJettison(bool)`.
- **Project wrapper:** `tasking.apply_ai_difficulty(group, difficulty)`.

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
- **Project wrapper:** `tasking.scramble_on_trigger(m, group, *conditions)`.

### 6.4 Controlled tasks (start/stop conditions)
`task.ControlledTask(inner_task)` gates any task on conditions:
`.start_after_time(t)`, `.stop_after_time(t)`, `.stop_after_duration(t)`,
condition variants.

### 6.5 Unit `WrappedAction` commands (`points[0].tasks.append(...)`)
- **TACAN/beacon:** `task.ActivateBeaconCommand(channel=10, modechannel="X",
  callsign="TKR", bearing=True, unit_id=0, aa=True)` — DCS frequency computed
  internally (`aa=True` yardstick, `aa=False` ground). `refuel_flight` already
  adds one. `DeActivateBeaconCommand`.
- **Carrier recovery:** `task.ActivateICLSCommand(channel, unit_id)`,
  `ActivateLink4Command`, `ActivateACLSCommand` (+ their `DeActivate*`).
- **Radio:** `task.TransmitMessage(soundfile_reskey, subtitle_resstring,
  loop=False, subtitle_duration=5)`; `StopTransmission`; `SetFrequencyCommand(freq)`.
- **FX/state:** `task.SmokeCommand(bool)`, `SetInvisibleCommand`,
  `SetImmortalCommand`, `SetCallsignCommand`, `EPLRS`.

### 6.6 Carrier recovery tanker
`task.RecoveryTanker(groupId, speed, altitude, lastWaypoint)` on the tanker's
`points[0].tasks` — `groupId` is the carrier group, `speed` **km/h TAS**,
`altitude` metres AMSL.

---

## 7. Triggers & mission-end

Trigger rules go on `m.triggerrules.triggers`. Most end-logic is a `TriggerOnce`.

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
  `SoundToAll` / `SoundToCoalition` / `SoundToGroup`.
- `Coalition` enum lives in **`dcs.action`**, not `dcs.coalition`.
- **Wrap all displayed text in `m.string("…")`** before handing it to a
  `MessageTo*`/briefing setter.

### 7.1 Mission scripting (`DoScript`)

```python
from dcs_mission_creator.core import lua

t = triggers.TriggerStart(comment="ARM reaction")
t.add_action(lua.InlineDoScript(lua_source))   # multi-line Lua is fine
m.triggerrules.triggers.append(t)
```
- **Do not use pydcs's `action.DoScript`.** It is a `TextAction`: the Lua lands in
  the l10n dictionary and the rule references it as
  `a_do_script(getValueDictByKey("DictKey_…"))`. DCS does *not* resolve dictionary
  keys in the scripting sandbox — `getValueDictByKey` returns the key itself, so
  the game compiles the string `DictKey_Translation_N` as Lua and the trigger dies
  at mission start: `Mission script error: [string "DictKey_Translation_N"]:1: '='
  expected near '<eof>'`. Stock ED missions store the source inline in the
  action's own `text` field; `lua.InlineDoScript` (in
  [core/lua/__init__.py](../../../src/dcs_mission_creator/core/lua/__init__.py))
  does that. `dcs.lua.serialize.dumps` escapes `"` and turns each newline into a
  Lua line-continuation, so a multi-line script survives verbatim.
- `DoScriptFile(res_key)` runs a `.lua` added via
  `m.map_resource.add_resource_file(...)` instead. It is a different predicate
  from `a_do_script`, so the l10n bug does not apply — this is how a script too
  large to inline gets in (the 117 KB vendored framework).
- The mission-scripting env (`world`, `timer`, `Group.getByName`,
  `Controller:setOption`, `trigger.action.out*`) is what makes reactive AI
  possible: `AI.Option.Ground.id.ALARM_STATE` / `.ROE` toggle a SAM site between
  radiating and dark, `world.addEventHandler` sees `S_EVENT_SHOT`, and
  `timer.scheduleFunction(fn, arg, t)` schedules the way back.
- Two more carry the "what can this site *know*" half: `land.isVisible(vec3From,
  vec3To)` is a terrain-only LOS trace (lift a ground unit's point a few metres or
  the trace starts inside its own hill), and
  `group:getController():getDetectedTargets(Controller.Detection.RADAR)` is what a
  radar actually holds — DCS's own detection, so radar horizon and masking are
  already in it.
- Sounds played from Lua are addressed **by file name**
  (`trigger.action.outSoundForCoalition(side, "x.wav")`), not by resource key —
  register the WAV with `m.map_resource.add_resource_file(path)`; it lands at
  `l10n/DEFAULT/<basename>` (project wrapper: `VoiceSynth.register`).
- **Project wrapper:** `iads.arm_iads(m, sites, …)` builds the whole net; see
  CLAUDE.md before hand-rolling any of it.

### Briefing setters (on `Mission`)
`set_description_text`, `set_description_bluetask_text`,
`set_description_redtask_text`, `set_sortie_text`. Plain text.

- **Briefing pictures:** `m.add_picture_blue(filepath)` / `add_picture_red` /
  `add_picture_neutral` — image slides on the briefing screen. Returns a
  `ResourceKey`, appends to `pictureFileNameB/R/N`. Three facts the docstring does
  not tell you:
  - pydcs says "jpg or bmp"; that is **stale**. DCS's own file filter
    (`MissionEditor/modules/FileDialogFilters.lua`) is `(*.jpg;*.jpeg;*.png)`, so
    PNG is fine.
  - It records an **absolute path**, not the bytes. The file must still exist,
    unchanged, when `m.save` runs.
  - `MapResource.store` flattens every resource to `l10n/DEFAULT/<basename>` and
    **skips a basename it has already written**, leaving the second resource key
    pointing at the *first* file's bytes. Basenames must be globally unique across
    every resource in the mission; this is why the voice and recon caches both put
    a content hash in the filename, and why `recon.publish` raises on a collision.
- **Kneeboard:** `m.add_aircraft_kneeboard(aircraft_type, page_path)` — DCS reads
  pages from `KNEEBOARD/<type id>/IMAGES/` inside the `.miz`, per aircraft *type*:
  there is no per-flight kneeboard. **Project wrapper: `kneeboard.publish` — use
  that, not this.** Two reasons, both in CLAUDE.md: this helper writes the entry as
  `f'{directory}/{page.name}'` where `directory` already ends in `/`, giving
  `KNEEBOARD/<type>/IMAGES//page.png` with an empty path component, and it writes
  the file at save time with `zipf.write`, which records the source file's mtime
  and mode into the archive.
- **Navaids:** `Airport.beacons` is a list of ids and nothing else
  (`AirportBeacon(id='airfield22_3')`) — no type, frequency or position — and
  `Airport.tacan` is `None` even for a field that has one (Batumi, channel 16X).
  `Runway`/`RunwayApproach` give the designator and its ×10 heading, never a
  length or a threshold position. The real data is
  `Mods/terrains/<Theater>/Beacons.lua` in the installed game; project wrapper
  `kneeboard/beacons.py` reads it. The theatre's own aerodrome charts live in
  `Mods/terrains/<Theater>/Kneeboard/` — but only for the fields it ships: 21 on
  Caucasus, **three** on Syria. `kneeboard/charts.py` checks which.

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
`Point(x, y, terrain)` — the third arg is the terrain instance. Build all world
positions by offsetting from a known anchor, never from raw lat/lon.

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
objects. Never construct drawing classes directly; call `add_*` on a **layer**.

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

- **Layer visibility:** `Blue` → blue coalition only, `Red` → red only, `Common`
  → everyone. Put player-facing plan annotations on `Blue`.
- **Colours:** `Rgba(r, g, b, a)` 0–255. Fills want low alpha
  (`Rgba(255,0,0,60)`), outlines full. `LineStyle`: `Solid`, `Dash`, `Dot`,
  `DotDash`, `Square`.
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
`line_freeform`, `freeform_polygon`) `position` is the anchor and `points` are
**offsets relative to that anchor** — the first is usually
`Point(0, 0, terrain)`. Build offsets with `anchor.point_from_heading(hdg, dist)`
then subtract the anchor. `add_oblong` is the exception: two absolute points.

---

## 12. The project's own wrappers

Several project helpers wrap the APIs above, and a mission should call those
rather than these. Which ones exist and what they promise is **CLAUDE.md's**.
The rule for reading the two together: where a paragraph says "pydcs does X
wrong, the project does Y instead", the defect is here and the remedy is there.

Four items with nowhere else to live:

- `Coalition` is in `dcs.action`, not `dcs.coalition`.
- `TriggerContinious` and `Preceptions` are spelled that way in the source.
- `group_size` caps at 4 for fighters; five or more desyncs the formation output.
  `mission_kit.player_flight` splits sections for that reason.
- There is no `mission.duration` — a mission ends through triggers or bingo fuel.
