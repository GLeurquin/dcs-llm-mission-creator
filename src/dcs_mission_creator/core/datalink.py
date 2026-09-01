"""Datalink identity: who each jet is on the net, and who its flight is.

A coop pair that cannot see each other on the HSD, the FCR page or the HUD is
not a wingman problem — it is a mission-file problem. Two things the Mission
Editor writes for every aircraft, and pydcs writes for none, decide it:

- **`AddPropAircraft`** carries the aircraft's identity on the net — its track
  number (`STN_L16` on Link 16, `SADL_TN` on the A-10's SADL) and the voice
  callsign the datalink displays it under (`VoiceCallsignLabel` +
  `VoiceCallsignNumber`). pydcs seeds those keys from the type's
  `property_defaults`, where all three are `None`, and never fills them in: the
  whole package spawns anonymous, and identical.
- **`datalinks`** is the per-unit network configuration — the modules with a
  Datalink dialog in the ME (`connectDatalinks`: F-16C, F/A-18C, A-10C II,
  AH-64D) read their team members out of it. pydcs has no field for it at all,
  so the F-16's MIDS comes up with an empty flight and nobody's PPLI symbol
  ever appears.

`assign_datalink_identities(m)` fills both, mission-wide, matching what the ME
would have written:

- every unit whose type declares a track-number property gets a unique one,
  allocated in per-flight blocks (`00101`, `00102`, …, `00201`, …) so a flight
  reads as a flight and no two aircraft collide on the same net;
- every unit with a Western callsign gets the ME's own two-letter tag plus its
  flight/position digits — `Springfield11` becomes `SD` + `11`, so the four
  slots of a coop flight are four distinct callsigns rather than four blanks;
- every unit of a module with a datalink dialog lists its whole flight as team
  members, which is what puts the other players on the scope.

`MissionBuilder.build_miz` calls it after `_assemble` and before the save, for
the same reason it snaps base waypoints there: every flight exists by then, and
a mission cannot forget it.

Adding a module is a `_NETS` table entry — its dialog's default settings and
whether its team members carry a TDOA flag, both read off
`<DCS>/CoreMods/aircraft/<module>/Datalinks/*.lua`. The AH-64D is deliberately
absent: its IDM uses a different shape (`TN_IDM_LB` / `OwnshipCallSign`) and no
mission here flies one.

Getting `datalinks` past `Unit.dict()` at all needs the patch in
`core/unit_extras.py`, which `core/dtc.py` shares for its own missing key.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import structlog
from dcs import planes

from dcs_mission_creator.core import mission_kit, unit_extras

if TYPE_CHECKING:
    from dcs.flyingunit import FlyingUnit
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup
    from dcs.unittype import FlyingType

log = structlog.get_logger(__name__)

#: Track-number property -> how many octal digits the ME's input filter keeps.
#: `STN_L16` is the Link 16 source track number, `SADL_TN` the A-10's SADL
#: track number; an aircraft type declares at most one of them.
_TRACK_PROPERTIES: dict[str, int] = {"STN_L16": 5, "SADL_TN": 4}

#: Track numbers are handed out one block of eight per flight, so the flight
#: lead is `nn1` and its wingmen `nn2`.. — the way ME-authored missions read.
_BLOCK = 0o100


@dataclass(frozen=True)
class _Net:
    """One module's datalink dialog, as the ME serializes it.

    `settings` takes the unit's 1-based position in the flight, because the
    F-16 and the A-10 mark their lead there. `tdoa` is the Hornet/Viper split:
    the Viper's team members carry a time-difference-of-arrival flag and the
    Hornet's do not.
    """

    key: str
    settings: Callable[[int], dict[str, Any]]
    tdoa: bool


_NETS: dict[str, _Net] = {
    planes.F_16C_50.id: _Net(
        key="Link16",
        settings=lambda index: {
            "missionChannel": 1,
            "fighterChannel": 1,
            "specialChannel": 1,
            "transmitPower": 3,
            "flightLead": index == 1,
        },
        tdoa=True,
    ),
    planes.FA_18C_hornet.id: _Net(
        key="Link16",
        settings=lambda _index: {
            "AIC_Channel": 1,
            "FF1_Channel": 2,
            "FF2_Channel": 3,
            "VOCA_Channel": 4,
            "VOCB_Channel": 5,
            "transmitPower": 0,
        },
        tdoa=False,
    ),
    planes.A_10C_2.id: _Net(
        key="SADL",
        settings=lambda index: {
            "AirKey": 10,
            "GatewayKey": 8,
            "flightLead": index == 1,
        },
        tdoa=False,
    ),
}


def assign_datalink_identities(m: Mission) -> None:
    """Give every flight in `m` a datalink identity and its own team members.

    Walks all flying groups in group-id order — deterministic, so two builds of
    the same mission still produce the same `.miz`, and so a flight added after
    this call site was written cannot be missed.
    """
    unit_extras.emit_unit_key("datalinks", "datalinks")
    blocks: dict[str, int] = {}
    wired = 0
    for group in _flying_groups(m):
        _name_flight(group)
        _number_flight(group, blocks)
        wired += _wire_flight(group, mission_kit.sections_of(m, group))
    log.debug("datalink identities assigned", flights=wired)


def _flying_groups(m: Mission) -> list[FlyingGroup]:
    """Every plane and helicopter group in the mission, in group-id order."""
    groups = [
        group
        for coalition in m.coalition.values()
        for country in coalition.countries.values()
        for group in (*country.plane_group, *country.helicopter_group)
        if group.units
    ]
    return sorted(groups, key=lambda g: g.id)


def _name_flight(group: FlyingGroup) -> None:
    """Set each unit's voice callsign label and number from its own callsign."""
    for unit in group.units:
        defaults = unit.unit_type.property_defaults or {}
        if "VoiceCallsignLabel" not in defaults:
            continue
        callsign = _voice_callsign(unit)
        if callsign is None:
            continue
        label, number = callsign
        unit.set_property("VoiceCallsignLabel", label)
        unit.set_property("VoiceCallsignNumber", number)


def _voice_callsign(unit: FlyingUnit) -> tuple[str, str] | None:
    """DCS's own two-letter tag and two-digit number for a Western callsign.

    The rule is `getCallsignLabel` / `getCallsignNumber` in each module's
    `Datalinks/AddProp.lua`: the label is the first letter of the callsign plus
    the last letter before the digits, the number is the digits — so pydcs's
    `Springfield11` yields `SD` and `11`, and both fields are capped at two
    characters by the editor's input filter. Non-Western callsigns are a bare
    integer with no flight structure to read; those units keep the default.
    """
    if not unit.callsign_is_western:
        return None
    name = unit.callsign_as_str()
    letters = "".join(itertools.takewhile(str.isalpha, name))
    digits = name[len(letters) :]
    if not letters or not digits.isdigit():
        return None
    return (letters[0] + letters[-1]).upper(), digits[:2]


def _number_flight(group: FlyingGroup, blocks: dict[str, int]) -> None:
    """Give the flight the next free block of track numbers on its network.

    `blocks` is keyed by the track property rather than shared, because Link 16
    and SADL are different networks: an STN and a TN may repeat across them.
    """
    prop = _track_property(group.units[0].unit_type)
    if prop is None:
        return
    digits = _TRACK_PROPERTIES[prop]
    block = blocks[prop] = blocks.get(prop, 0) + 1
    for index, unit in enumerate(group.units, start=1):
        number = block * _BLOCK + index
        if number >= 8**digits:
            raise ValueError(
                f"{prop} exhausted at {group.name}: {block} flights of "
                f"{digits} octal digits is the ceiling"
            )
        unit.set_property(prop, f"{number:0{digits}o}")


def _track_property(unit_type: type[FlyingType]) -> str | None:
    """The track-number property this aircraft type carries, if any."""
    defaults = unit_type.property_defaults or {}
    for prop in _TRACK_PROPERTIES:
        if prop in defaults:
            return prop
    return None


def _wire_flight(group: FlyingGroup, sections: tuple[FlyingGroup, ...]) -> int:
    """List the whole flight as every member's team members; 1 if it applies.

    This is the half that puts the other players on the scope: the ME fills the
    network tab with the group's own units, and a module with an empty one
    comes up with nobody on the net.

    `sections` is the flight, which above four coop slots is more than one
    group: a six-slot `Dodge` is two DCS groups and one flight, and teaming each
    group only with itself would reintroduce exactly the blindness this module
    exists to fix — half the flight invisible to the other half. Every other
    flight is its own single section, so nothing else changes.
    """
    net = _NETS.get(group.units[0].unit_type.id)
    if net is None:
        return 0
    members = [
        {"missionUnitId": unit.id} | ({"TDOA": True} if net.tdoa else {})
        for section in sections
        for unit in section.units
    ]
    for index, unit in enumerate(group.units, start=1):
        unit.datalinks = {
            net.key: {
                "settings": net.settings(index),
                "network": {
                    "teamMembers": [dict(member) for member in members],
                    "donors": [],
                },
            }
        }
    return 1
