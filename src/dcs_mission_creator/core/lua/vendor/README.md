# Vendored mission-scripting Lua

Third-party Lua that ships **inside** the `.miz` (not on the Python side). It is
committed rather than fetched so a build is reproducible and works offline, and
it is kept verbatim so the diff against upstream is empty.

## `skynet-iads.lua`

walder's [Skynet-IADS](https://github.com/walder/Skynet-IADS), the
single-file compiled build (`demo-missions/skynet-iads-compiled.lua`).

| | |
|---|---|
| Version | 3.3.0, build 29.12.2023 2311Z |
| Upstream commit | `master` @ 2023-12-29 (latest at time of vendoring) |
| Licence | Apache-2.0 — see `LICENSE-Skynet-IADS.txt` |
| Modified | **No.** Byte-identical to upstream. |

It supplies the half of an integrated air-defence net that is a lot of code to
write and nobody's idea of fun: which sites are cued live by which radars,
per-launcher and per-radar range analysis against the real system envelopes,
point defence, ammunition state, power sources, connection nodes, autonomous
degradation when the net is cut, and jamming.

`core/iads.py` drives it. What that module does **not** hand over is the
reaction to anti-radiation fire — see its docstring: Skynet identifies the
missile in flight, this project reacts to the launch being observed, and those
are different claims about what a crew can know. Skynet's own HARM detection is
switched off at setup.

To update: drop in a newer `skynet-iads-compiled.lua`, update the table above,
re-check `core/lua/mist_shim.lua` still covers every `mist.*` the new build
calls (`grep -o 'mist\.[a-zA-Z0-9_.]*'`), and run the test suite — `tests/
test_iads.py` asserts the setup script only calls Skynet API that exists in this
file.

## Why there is no MIST here

Skynet documents [MIST](https://github.com/mrSkortch/MissionScriptingTools) as a
prerequisite, and MIST is **GPL-3.0**. Shipping it inside every generated `.miz`
alongside this project's own scripts would put a copyleft licence in the middle
of the output, which is not a decision a mission generator should make quietly
on its user's behalf.

It also turns out not to be needed. Skynet calls exactly thirteen MIST
functions, all of them small — a scheduler pair, seven unit conversions and
vector helpers, `mist.random`, `mist.getHeading`, and two name→object lookup
tables used only by the `*ByPrefix` registration methods this project does not
use. `core/lua/mist_shim.lua` is a first-party implementation of that surface,
written from the documented behaviour of each function, and it is ~200 lines
against MIST's 313 KB. It is loaded before `skynet-iads.lua`.

If you would rather ship real MIST, drop `mist_4_5_107.lua` in here and load it
in place of the shim — the shim is a drop-in for the surface Skynet touches.
Mind the licence.

## Why CTLD is not here either, and what was taken from it instead

Ciribob's [DCS-CTLD](https://github.com/ciribob/DCS-CTLD) is the reference
implementation of a JTAC that lases on its own — `ctld.JTACAutoLase` — and
`core/laser.py`'s `arm_autolase` exists to do that one job. CTLD itself is not
vendored, for two reasons that are worth writing down rather than rediscovering:

- **It requires MIST**, and not the thin slice Skynet needs: 26 distinct `mist.*`
  functions across 84 call sites, including `mist.DBs.humansByName`,
  `mist.DBs.unitsByName`, `mist.dynAdd` / `mist.dynAddStatic`,
  `mist.getGroupRoute`, `mist.ground.buildWP` and `mist.getUnitsLOS` — whole
  unit databases and dynamic spawning, not conversions. The autolase path calls
  `mist.scheduleFunction` itself, so this is not a dependency that could be
  trimmed away by using only part of the script. Covering that surface in
  `mist_shim.lua` is a much larger and more fragile job than the ~200 lines of
  Lua the laser actually needs.
- **The repository carries no licence file** (GitHub reports none). The author's
  position is that it is free to use, modify and republish, and this project's
  use is on that basis; even so, the terms are asserted rather than written down,
  which is a thin footing for 8,700 lines shipped inside every mission this tool
  generates.

CTLD is also a logistics framework — crates, troops, beacons, transport,
its own F10 menus — whose JTAC feature is a couple of hundred lines of it, and
several of those parts would collide with this project's own readout
(`core/jtac.py`) and radio calls (`core/triggers.py`).

So `core/lua/autolase.lua` is a reimplementation, credited in its header, and
what it takes from CTLD is the handful of decisions that are load-bearing rather
than incidental: the 2 m beam origin and the 2 m aim point, `land.isVisible`
with **both** ends lifted off the deck, the 10 km reach (`JTAC_maxDistance`),
lasing the nearest visible vehicle while holding the one already lased, and —
the one that is easy to get wrong and invisible when you do — updating the spot
often enough that the target never travels more than a few metres between
updates. The optional lead-and-wind correction is CTLD's `laseSpotCorrections`,
factors included.

If CTLD's *other* features are ever wanted (crates and troop transport are the
obvious ones), that is a different decision from this one: it needs the MIST
question answered first, and it needs the parts that duplicate `core/jtac.py`
turned off.
