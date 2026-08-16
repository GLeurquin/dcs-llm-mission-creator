"""Radar EMCON reactions to anti-radiation missiles (project-owned helper).

Out of the box a DCS SAM site keeps its radar lit while a HARM rides the beam
all the way in: the crew never reacts, so every anti-radiation shot is a
guaranteed kill and SEAD degenerates into a shooting gallery. Real crews do the
opposite — the launch is called over the IADS net, the fire-control radar drops
emissions within seconds, the missile loses its emitter and goes for the last
known point, and the site comes back up several minutes later once the shooter
is assumed dry.

`arm_emcon_reaction` builds that behaviour as a mission-start `DoScript`: one
Lua event handler on `S_EVENT_SHOT` that recognises anti-radiation weapons,
decides per site whether that crew catches the launch, and cycles the site
through `ALARM_STATE GREEN` (radars off, weapons hold) and back to
`ALARM_STATE RED`. What makes it read as crew behaviour rather than a switch:

- **Not every crew reacts.** Each site has its own `probability`; a green crew
  keeps radiating and eats the missile.
- **Reaction takes time.** `delay_s` seconds pass between launch and emissions
  drop, so a HARM already close still kills.
- **The site comes back.** `shutdown_s` seconds later the radar re-radiates —
  suppression is temporary, destruction is not.
- **Repeat fire makes crews shy.** A second launch while a site is dark extends
  the shutdown instead of restarting it.
- **Range gates the reaction.** Only sites within `react_range_m` of the
  shooter hear about the launch; a HARM fired at one end of the map does not
  shut down the whole theater.

Only radar-guided sites belong in the list. Optically/IR-guided SHORAD (SA-13,
MANPADS) has nothing to shut down, and putting a mixed convoy in here would
make the whole column hold fire on every HARM shot.

Design rule (mirrors `core/air_defense.py` / `core/tasking.py`): built pydcs
groups in, one trigger out. The mission owns the wording of the radio calls;
pass a `VoiceSynth` to get them spoken as well as printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence, Union

import structlog
from dcs import triggers

from dcs_mission_creator.core import lua

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import VehicleGroup

    from dcs_mission_creator.core.tts import VoiceSynth

log = structlog.get_logger(__name__)

_SIDE = {"blue": "coalition.side.BLUE", "red": "coalition.side.RED"}


@dataclass
class ArmSite:
    """One radar site that reacts to anti-radiation fire.

    `label` is what the radio call names ("SA-6", "EWR"). `probability` is the
    chance this crew catches a given launch, `delay_s` the recognition delay
    and `shutdown_s` how long the site stays dark, both randomised per event
    within the given range. `react_range_m` is how far from the site a launch
    still gets passed down the net.
    """

    group: "VehicleGroup"
    label: str
    probability: float = 0.85
    delay_s: tuple[float, float] = (3.0, 8.0)
    shutdown_s: tuple[float, float] = (240.0, 360.0)
    react_range_m: float = 60_000.0


# The Lua handler itself lives in `core/lua/emcon.lua`; the placeholders it
# declares (`__SITES__` / `__SIDE__` / `__SPACING__`) are filled in below.
_SCRIPT = "emcon.lua"


def _lua_str(text: Optional[str]) -> str:
    """Quote `text` as a Lua string literal (or `nil`)."""
    if text is None:
        return "nil"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def arm_emcon_reaction(
    m: "Mission",
    sites: Sequence[Union["VehicleGroup", ArmSite]],
    *,
    voice: Optional["VoiceSynth"] = None,
    coalition: str = "blue",
    down_call: Optional[str] = "Magic: {label} has ceased emissions, site is dark.",
    up_call: Optional[str] = "Magic: {label} radar is radiating again.",
    announce_spacing_s: float = 7.0,
    comment: str = "ARM reaction — SAM EMCON",
) -> triggers.TriggerStart:
    """Make `sites` drop emissions when an anti-radiation missile is fired at them.

    Pass plain `VehicleGroup`s for default behaviour or `ArmSite` entries to tune
    probability / delay / shutdown per site. `down_call` and `up_call` are
    `{label}` templates announced to `coalition`; with a `VoiceSynth` the same
    words are also rendered and played from the script. Every site gets its own
    call — a single shot that darkens a whole belt queues them and plays them
    `announce_spacing_s` apart rather than dropping the later ones. Returns the
    mission-start trigger carrying the generated `DoScript`.
    """
    if coalition not in _SIDE:
        raise ValueError(f"coalition must be blue/red, got {coalition!r}")
    entries = [s if isinstance(s, ArmSite) else ArmSite(s, s.name) for s in sites]
    if not entries:
        raise ValueError("arm_emcon_reaction needs at least one site")

    rows: list[str] = []
    for site in entries:
        down_text = down_call.format(label=site.label) if down_call else None
        up_text = up_call.format(label=site.label) if up_call else None
        down_sound = voice.register(m, down_text) if voice and down_text else None
        up_sound = voice.register(m, up_text) if voice and up_text else None
        rows.append(
            "    {{name={name}, prob={prob:.3f}, delayMin={dmin:.1f}, "
            "delayMax={dmax:.1f}, downMin={smin:.1f}, downMax={smax:.1f}, "
            "range={rng:.1f}, downText={dtext}, upText={utext}, "
            "downSound={dsound}, upSound={usound}}},".format(
                name=_lua_str(site.group.name),
                prob=site.probability,
                dmin=site.delay_s[0],
                dmax=site.delay_s[1],
                smin=site.shutdown_s[0],
                smax=site.shutdown_s[1],
                rng=site.react_range_m,
                dtext=_lua_str(down_text),
                utext=_lua_str(up_text),
                dsound=_lua_str(down_sound),
                usound=_lua_str(up_sound),
            )
        )

    script = lua.render(
        _SCRIPT,
        SITES="\n".join(rows),
        SIDE=_SIDE[coalition],
        SPACING=f"{announce_spacing_s:.1f}",
    )
    rule = triggers.TriggerStart(comment=comment)
    rule.add_action(lua.InlineDoScript(script))
    m.triggerrules.triggers.append(rule)
    log.debug("armed EMCON reaction", sites=[s.group.name for s in entries])
    return rule
