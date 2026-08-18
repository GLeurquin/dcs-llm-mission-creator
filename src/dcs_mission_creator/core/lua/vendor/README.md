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
