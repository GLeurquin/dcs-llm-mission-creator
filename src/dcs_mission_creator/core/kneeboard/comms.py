"""Who is on which frequency, read out of the mission rather than the briefing.

Every mission's briefing carries a FREQUENCIES block hand-typed next to the
`frequency=` argument that actually set it, and one of the two is one edit away
from being stale. This walks the built mission instead: the flights, the
controllers, the airfield ATC channels and the tanker's TACAN all come off the
same objects DCS will read, so the card cannot drift from the package.

Four pydcs shapes decide where a number lives, and none of them is the obvious
one:

- **A flight's own frequency is `MovingGroup.frequency`** in MHz, and pydcs
  defaults it to 251 for every group it creates — it does not seed it from the
  airframe's `radio_frequency`. So a mission that never called `set_frequency`
  has its whole package nominally on one channel, and the page shows that rather
  than the tidier number a briefing might claim.
- **`awacs_flight` and `refuel_flight` put the working frequency in a
  `SetFrequencyCommand` task** on the first waypoint, not in the group field. That
  is the frequency the AI controller actually transmits on, so it wins here.
- **A tanker's TACAN is an `ActivateBeaconCommand`** whose params carry the
  channel, the X/Y mode and the beacon callsign.
- **A JTAC's frequency is inside its `FAC*` task params**, in Hz.

The one thing that is *not* a mission frequency: `FlyingType.panel_radio` is the
airframe's default preset table, which DCS loads into the cockpit because none of
these missions overrides the presets. Cross-referencing the two says whether a
frequency is a preset channel or has to be dialled by hand, which is the
difference between a comms card that saves time and one that lists numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from dcs import task
from dcs.unit import Skill

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup, Group
    from dcs.unittype import FlyingType

#: DCS's modulation enum, as a mission file carries it.
_MODULATION = {0: "AM", 1: "FM"}


@dataclass(frozen=True)
class Station:
    """One radio a pilot might have to talk to."""

    callsign: str
    aircraft: str
    role: str
    frequency_mhz: float | None
    modulation: str
    tacan: str | None = None
    station: str | None = None
    player: bool = False
    preset: str | None = None
    #: A controller the player talks to rather than a flight in the package — a
    #: ground JTAC or an airborne FAC(A). Which one it is shows in `role`.
    controller: bool = False

    @property
    def frequency_text(self) -> str:
        if self.frequency_mhz is None:
            return "--"
        return f"{self.frequency_mhz:7.3f} {self.modulation}"


@dataclass(frozen=True)
class AtcChannel:
    """An airfield's tower/ground/approach radio, which DCS gives one per band."""

    airfield: str
    uhf_mhz: float | None
    vhf_high_mhz: float | None
    vhf_low_mhz: float | None
    hf_mhz: float | None


def player_groups(m: Mission) -> list[FlyingGroup]:
    """Every flying group holding a client slot, in build order."""
    return [
        group
        for group in _flying_groups(m)
        if any(u.skill in (Skill.Client, Skill.Player) for u in group.units)
    ]


def stations(m: Mission, *, coalition: str = "blue") -> list[Station]:
    """The flights of `coalition` plus any ground controller, player flights first."""
    out: list[Station] = []
    preset_table = _presets(_player_type(m))
    for group in _flying_groups(m, coalition=coalition):
        is_player = any(u.skill in (Skill.Client, Skill.Player) for u in group.units)
        frequency, modulation = _frequency(group)
        # An airborne FAC is a controller, not another jet in the package: it is
        # what the player talks to for a talk-on, and a pilot looks for it under
        # CONTROL beside the ground JTACs.
        designation = _fac_designation(group)
        out.append(
            Station(
                callsign=group.name,
                aircraft=group.units[0].unit_type.id if group.units else "",
                role=(
                    f"FAC(A) {designation}".strip()
                    if designation is not None
                    else str(group.task or "")
                ),
                controller=designation is not None,
                frequency_mhz=frequency,
                modulation=modulation,
                tacan=_tacan(group),
                station=_station(m, group),
                player=is_player,
                preset=preset_table.get(_key(frequency, modulation)),
            )
        )
    for group in _controllers(m, coalition=coalition):
        frequency, modulation, designation = _fac(group)
        out.append(
            Station(
                callsign=group.name,
                aircraft=group.units[0].type if group.units else "",
                role=f"JTAC {designation}".strip(),
                controller=True,
                frequency_mhz=frequency,
                modulation=modulation,
                preset=preset_table.get(_key(frequency, modulation)),
            )
        )
    return sorted(out, key=lambda s: (not s.player,))


def atc_channels(m: Mission, airfields) -> list[AtcChannel]:
    """The ATC radio of each airfield, off pydcs's own `AtcRadio` record.

    DCS gives a field one radio per band shared by ground, tower and approach —
    there is no separate tower and ground frequency to print, and a card that
    invented one would send the player looking for a channel that does not exist.
    """
    out = []
    for airport in airfields:
        radio = airport.atc_radio
        out.append(
            AtcChannel(
                airfield=airport.name,
                uhf_mhz=_mhz(getattr(radio, "uhf_hz", None)),
                vhf_high_mhz=_mhz(getattr(radio, "vhf_high_hz", None)),
                vhf_low_mhz=_mhz(getattr(radio, "vhf_low_hz", None)),
                hf_mhz=_mhz(getattr(radio, "hf_hz", None)),
            )
        )
    return out


# -- internals ---------------------------------------------------------------


def _flying_groups(
    m: Mission, *, coalition: str | None = None
) -> Iterator[FlyingGroup]:
    for name, side in m.coalition.items():
        if coalition is not None and name != coalition:
            continue
        for country in side.countries.values():
            yield from country.plane_group
            yield from country.helicopter_group


def _controllers(m: Mission, *, coalition: str) -> Iterator[Group]:
    """Ground groups carrying a FAC task — the JTACs, and only those."""
    for name, side in m.coalition.items():
        if name != coalition:
            continue
        for country in side.countries.values():
            for group in country.vehicle_group:
                if group.points and any(
                    isinstance(t, (task.FAC, task.FACAttackGroup, task.FACEngageGroup))
                    for t in group.points[0].tasks
                ):
                    yield group


def _fac_designation(group: FlyingGroup) -> str | None:
    """`LASER`/`AUTO`/… if this flight is tasked as a FAC, else `None`."""
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, (task.FAC, task.FACAttackGroup, task.FACEngageGroup)):
                return str(tsk.params.get("designation", "") or "").upper()
    return None


def _frequency(group: FlyingGroup) -> tuple[float | None, str]:
    """The frequency the flight actually works on, most specific source first.

    A FAC task carries its own frequency in its params and that is where the
    controller transmits, so it wins over both a `SetFrequencyCommand` and the
    group field — an airborne FAC left at pydcs's default 251 in the group field
    would otherwise be listed on the AWACS's channel.
    """
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, (task.FAC, task.FACAttackGroup, task.FACEngageGroup)):
                params = tsk.params
                return (
                    float(params["frequency"]) / 1_000_000.0,
                    _MODULATION.get(int(params.get("modulation", 0)), "FM"),
                )
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, task.SetFrequencyCommand):
                params = tsk.params["action"]["params"]
                return (
                    float(params["frequency"]) / 1_000_000.0,
                    _MODULATION.get(int(params.get("modulation", 0)), "AM"),
                )
    if not group.frequency:
        return None, "AM"
    return float(group.frequency), _MODULATION.get(int(group.modulation or 0), "AM")


def _tacan(group: FlyingGroup) -> str | None:
    for point in group.points:
        for tsk in point.tasks:
            if isinstance(tsk, task.ActivateBeaconCommand):
                params = tsk.params["action"]["params"]
                callsign = params.get("callsign") or ""
                return f"{params['channel']}{params['modeChannel']} {callsign}".strip()
    return None


def _station(m: Mission, group: FlyingGroup) -> str | None:
    """Where an orbiting flight holds, as bullseye bearing/range and altitude."""
    from dcs.mapping import Point

    from dcs_mission_creator.core.kneeboard.flightplan import FT_PER_M, bearing_range

    for point in group.points:
        if not any(isinstance(t, task.OrbitAction) for t in point.tasks):
            continue
        bulls = Point(
            m.terrain.bullseye_blue["x"], m.terrain.bullseye_blue["y"], m.terrain
        )
        bearing, nm = bearing_range(bulls, point.position)
        feet = round(point.alt * FT_PER_M / 100.0) * 100
        return f"BULLS {bearing:03.0f}/{nm:.0f}  {feet:,} FT"
    return None


def _fac(group: Group) -> tuple[float | None, str, str]:
    """Frequency (MHz), modulation and designation out of a FAC task's params."""
    for tsk in group.points[0].tasks:
        if isinstance(tsk, (task.FAC, task.FACAttackGroup, task.FACEngageGroup)):
            params = tsk.params
            designation = str(params.get("designation", "") or "")
            return (
                float(params["frequency"]) / 1_000_000.0,
                _MODULATION.get(int(params.get("modulation", 0)), "FM"),
                designation.upper(),
            )
    return None, "FM", ""


def _player_type(m: Mission) -> FlyingType | None:
    groups = player_groups(m)
    if not groups or not groups[0].units:
        return None
    return groups[0].units[0].unit_type


def _presets(unit_type: FlyingType | None) -> dict[tuple[int, str], str]:
    """`(kHz, modulation) -> "R2 CH5"` for the airframe's default preset table.

    Keyed on kilohertz so 251.0 and 251 compare equal without a float epsilon.
    Every entry is registered as AM, because both preset radios in the airframes
    these missions fly are AM sets — which is also the guard that stops a JTAC's
    FM frequency from matching a VHF-AM preset channel that happens to share the
    number.
    """
    panel = None if unit_type is None else unit_type.panel_radio
    if not panel:
        return {}
    out: dict[tuple[int, str], str] = {}
    for radio_id, radio in sorted(panel.items(), key=lambda kv: str(kv[0])):
        for channel, frequency in sorted(
            radio.get("channels", {}).items(), key=lambda kv: int(kv[0])
        ):
            out.setdefault(_key(float(frequency), "AM"), f"R{radio_id} CH{channel}")
    return out


def _key(frequency_mhz: float | None, modulation: str) -> tuple[int, str]:
    if frequency_mhz is None:
        return (0, modulation)
    return (int(round(frequency_mhz * 1000)), modulation)


def _mhz(hertz: float | None) -> float | None:
    return None if not hertz else float(hertz) / 1_000_000.0
