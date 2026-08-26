"""In-cockpit kneeboard cards, built from the mission itself.

Two cards, one job each:

- **flight plan** — the route as numbers, one line per steerpoint: its position
  in degrees and decimal minutes, the terrain under it, track, leg distance,
  altitude, commanded speed, ETE and ETA; then the air defence the mission
  briefed, with the same coordinates the F-16C's cartridge carries; then the
  departure and recovery fields with this flight's parking slot, and the weather
  the timings were flown against;
- **comms** — the package's frequencies, the controllers, each relevant field's
  ATC bands and the theater navaids, with a note where a frequency happens to be
  one of the airframe's default presets.

Plus an **airfield** card per field the *theatre ships no chart of*
(`kneeboard/charts.py`): DCS's own aerodrome and approach charts are better than
anything derivable here, but it ships 21 of them on Caucasus and three on Syria —
so Hatay, where `idlib_gauntlet` starts, has no page until this writes one.

Everything is derived from the built mission, so a card cannot contradict the
route, the frequencies or the fields it came from. `MissionBuilder.build_miz`
calls `publish` after the save — the pages are files inside the `.miz`, and a
mission decides none of this. A mission may add free-text lines to the comms
card's REMARKS block with `remark`.
"""

from dcs_mission_creator.core.kneeboard.publish import KneeboardPage, publish, remark

__all__ = ["KneeboardPage", "publish", "remark"]
