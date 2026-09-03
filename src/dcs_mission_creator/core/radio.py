"""The frequency a flight actually works on, in the field DCS binds a radio to.

pydcs's `Mission.awacs_flight` and `Mission.refuel_flight` take a `frequency=`
argument and spend it on a `SetFrequency` **waypoint task**, leaving the group's
own `frequency` field at the 251 MHz that `MovingGroup.__init__` gives every
group it creates. ED's own missions do the opposite: the working tanker in
`Mods/aircraft/F-16C/Missions/QuickStart/F-16C - Caucasus - Air Refueling.miz`
is written `["frequency"] = 305, ["radioSet"] = true, ["communication"] = true`,
and that mission contains no `SetFrequency` task anywhere. The group field is
what a player's radio has to match to raise an AI controller; a waypoint task
retunes the AI some minutes into its own route, after it has taxied and climbed
to the departure point, and is not what the mission loads its comms from.

So every mission here briefed a tanker frequency the tanker was not on, and
printed it on the kneeboard as well — `kneeboard/comms.py` reads the
`SetFrequencyCommand` in preference to the field precisely because that is where
the mission's *intent* was recorded. The tell was which asset worked: all four
missions with both a tanker and an AWACS put the AWACS on 251, which is pydcs's
default, so the AWACS was reachable by accident and the tanker never was.

`tune_working_frequencies` mirrors the intent back into the field, mission-wide,
after the last flight exists — the same reason `waypoints.snap_base_waypoints`
and `datalink.assign_datalink_identities` run from `MissionBuilder.build_miz`
rather than from a mission: a flight added later cannot miss it.

Two things it deliberately leaves alone.

- **A group holding a client slot.** `FlyingGroup.set_frequency` also sets
  `radioSet`, which is DCS's flag for "this mission overrides the cockpit preset
  table" — it writes the group frequency into channel 1 of the first compatible
  radio. The comms card annotates a frequency with its preset channel
  (`251.000 AM  R1 CH18`) computed from the airframe's own `panel_radio` on the
  assumption nothing overrides it, so tuning a player group would quietly make
  those annotations wrong. No client group carries a `SetFrequency` task today,
  which is why this guard costs nothing and is worth having anyway.
- **Ground groups.** A `VehicleGroup` writes no frequency at all unless
  `communication` is set, and a ground JTAC's radio is the frequency inside its
  own FAC task params, which is where DCS reads it from and what the card
  already prints. There is nothing broken there to mirror.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dcs import task

from dcs_mission_creator.core import mission_kit

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup

#: DCS's modulation enum, as a mission file carries it.
MODULATION_NAME = {0: "AM", 1: "FM"}

_FAC_TASKS = (task.FAC, task.FACAttackGroup, task.FACEngageGroup)


def working_frequency(group: FlyingGroup) -> tuple[float, int] | None:
    """`(MHz, modulation)` the flight transmits on, most specific source first.

    A FAC task carries its own frequency in its params and that is where the
    controller talks, so it beats a `SetFrequencyCommand` — an airborne FAC left
    on the group default would otherwise be listed on the AWACS's channel.
    `None` means the mission stated nothing beyond the group field itself.
    """
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, _FAC_TASKS):
                params = tsk.params
                # A FAC talks on FM unless it says otherwise; a SetFrequency
                # action defaults to AM. Both match pydcs's own defaults.
                return (
                    float(params["frequency"]) / 1_000_000.0,
                    int(params.get("modulation", 1)),
                )
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, task.SetFrequencyCommand):
                params = tsk.params["action"]["params"]
                return (
                    float(params["frequency"]) / 1_000_000.0,
                    int(params.get("modulation", 0)),
                )
    return None


def tune_working_frequencies(m: Mission) -> None:
    """Write each AI flight's working frequency into its own group field."""
    for group in mission_kit.flying_groups(m):
        if mission_kit.is_client(group):
            continue
        found = working_frequency(group)
        if found is None:
            continue
        frequency_mhz, modulation = found
        # An integral frequency is written as an integer, which is what the
        # mission editor does — 253 rather than 253.0. DCS reads either as a Lua
        # number; keeping the shapes identical means a generated mission diffs
        # against an ME-saved one without noise.
        group.set_frequency(
            int(frequency_mhz) if frequency_mhz % 1 == 0 else frequency_mhz
        )
        group.modulation = modulation
