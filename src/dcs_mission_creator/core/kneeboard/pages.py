"""The cards: flight plan, comms, and an airfield page where the game has none.

Content only — every number here is read off the built mission by
`flightplan.py`, `comms.py` and `airfields.py`, and every layout decision is
`page.py`'s. What this module owns is *what a pilot needs on his leg*, which is
the same judgement the briefings make and the reason the cards are worth having:
the F10 map shows the plan, the briefing explains it, and neither is readable
with a jet in the air.

**The airfield page is conditional, and `kneeboard/charts.py` is the condition.**
DCS ships the theatre's own aerodrome and approach charts on the same kneeboard,
and where it has one for the field, ED's surveyed drawing beats anything derivable
here — so nothing is drawn and only the mission-specific lines appear (the
departure and recovery fields with this flight's parking slot on the route card,
every relevant field's ATC bands and navaids on the comms card). But that coverage
is per theatre and per field: Caucasus charts all 21 of its airfields, Syria charts
**three**, and `idlib_gauntlet`'s player starts at Hatay, which is not one of them.
For a field like that the page below is the only page about it there is.

Two conventions carried from the briefings (see CLAUDE.md): a card names
factions, never coalitions, and it states no trigger logic.

**The threat block is not a reveal channel**, and that is the condition on its
existing. It prints `dtc.briefed_threats` — the estimates `PlanOverlay.threat`
returned and the Viper's cartridge was loaded from — so it repeats the F10 plan
rather than adding to it, and a `veteran`/`ace` mission, where `PlanOverlay`
withholds the sites and the list comes back empty, gets no block. What it adds is
the half of that picture no cockpit holds: the F-16C is the only module in DCS
that draws a pre-planned threat ring, so for every other airframe the briefed
coordinates exist nowhere but here, and even the Viper's HSD ring is a shape on a
scope with no numbers to read off it. A kneeboard that invented a position, or
kept one the map was not given, would be the fourth channel this used to warn
about — which is why the block's only input is what the other two were built
from.

**One table per waypoint, not two.** The route card used to carry the legs and
then repeat the same points as a steerpoint list for their coordinates; a pilot
reading his position off one table and his timing off another, four inches apart,
is doing the join the card should have done. The coordinates moved into the route
table and the second one went away, which cost the cumulative-distance column
(the sortie block has the route total) and the true-track column (the page prints
the theatre variation, so it is a subtraction away).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Sequence

from dcs_mission_creator.core.kneeboard import comms as comms_data, sketch
from dcs_mission_creator.core.kneeboard.airfields import AirfieldCard, approach_line
from dcs_mission_creator.core.kneeboard.flightplan import (
    FT_PER_M,
    M_PER_NM,
    bearing_range,
    ddm,
    flight_plan,
    hms,
    magnetic,
    variation_deg,
)
from dcs_mission_creator.core.kneeboard.page import Column, Page

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.unitgroup import FlyingGroup

    from dcs_mission_creator.map_overlay.query import MapOverlay

_KT_PER_MS = 1.9438445
_INHG_PER_MMHG = 0.03937008

_FOOTER = "Derived from the mission file. Timings zero-wind, taxi not included."


def flight_plan_page(
    m: Mission,
    group: FlyingGroup,
    *,
    title: str,
    cards: Sequence[AirfieldCard] = (),
    overlay: MapOverlay | None = None,
) -> Page:
    """Route card: the legs, the steerpoint coordinates, and the weather.

    `overlay` supplies the terrain elevation under each steerpoint, which is a
    different number from the waypoint's own altitude and the one a CCRP or a
    HUD-designated attack needs. Without it that column reads `--` rather than
    repeating the altitude beside it.

    `cards` carry the two fields this flight actually uses, which is where the
    field elevation and its own parking slot come from — the mission-specific part
    of an airfield, the part the theatre's aerodrome chart cannot know.
    """
    legs = flight_plan(group)
    variation = variation_deg(m.terrain.name)
    departure = _airport_name(m, group, first=True)
    recovery = _airport_name(m, group, first=False)
    takeoff = m.start_time + timedelta(seconds=float(group.start_time or 0))

    page = Page(
        title=title,
        subtitle=(
            f"{group.name.upper()} — {_type_id(group)} — {len(group.units)} SHIP — "
            f"{departure} {takeoff:%H:%M}L"
        ),
        label="flight plan",
        footer=_FOOTER,
    )

    page.section("sortie")
    page.line(f"DEPART   {_field_line(cards, departure, group)}")
    page.line(f"RECOVER  {_field_line(cards, recovery, None)}")
    # The ETA column below is local, because that is the clock in the briefing
    # and on the tower's frequency. The Viper's own steerpoint times are not:
    # `core/dtc.py` writes them in zulu, which is what its DED reads, so the
    # card has to print the one number that lets a pilot line the two up.
    zulu = takeoff - (m.terrain.utc_offset.utcoffset(None) or timedelta())
    page.line(
        f"TAKEOFF  {takeoff:%H:%M}L / {zulu:%H:%M}Z   "
        f"ROUTE {legs[-1].total_nm:.0f} NM   "
        f"{hms(legs[-1].elapsed_s)} EN ROUTE"
    )
    bulls = _bullseye(m)
    page.line(f"BULLSEYE {ddm(bulls)}")
    page.line(
        "VARIATION "
        + (
            f"{abs(variation):.0f} DEG {'E' if variation >= 0 else 'W'} "
            f"({m.terrain.name})"
            if variation is not None
            else f"unknown for {m.terrain.name} — tracks below are TRUE only"
        )
    )

    page.section("route")
    page.table(
        (
            Column("#", 2, ">"),
            Column("WAYPOINT", 12),
            Column("LAT / LONG (DDM)", 25),
            Column("ALT", 7, ">"),
            Column("GND", 6, ">"),
            Column("TRK M" if variation is not None else "TRK T", 5, ">"),
            Column("NM", 5, ">"),
            Column("TAS", 4, ">"),
            Column("ETE", 5, ">"),
            Column("ETA L", 5, ">"),
            Column("REMARK", 12),
        ),
        [
            (
                str(leg.number),
                leg.name[:12],
                ddm(leg.position),
                f"{leg.altitude_ft:,.0f}{'A' if leg.agl else ''}",
                _terrain_ft(overlay, leg.position),
                _track_text(leg.track_true, variation),
                "--" if leg.number == 1 else f"{leg.leg_nm:.1f}",
                "--" if leg.tas_kt <= 0 else f"{leg.tas_kt:.0f}",
                hms(leg.ete_s),
                f"{(takeoff + timedelta(seconds=leg.elapsed_s)):%H:%M}",
                leg.remark,
            )
            for leg in legs
        ],
    )
    page.note(
        "ALT is the waypoint's own altitude in feet, a trailing A meaning above "
        "ground level (DCS 'RADIO'); GND is the terrain under it, which is what a "
        "CCRP release needs. Tracks are "
        + (
            "magnetic, at the variation above."
            if variation is not None
            else "TRUE — this theatre publishes no variation."
        )
        + " Distances in nautical miles, TAS in knots."
    )

    _threats_section(page, m)

    page.section("weather")
    for line in _weather_lines(m):
        page.line(line)
    return page


def comms_page(
    m: Mission,
    cards: Sequence[AirfieldCard],
    *,
    title: str,
    remarks: Sequence[str] = (),
) -> Page:
    """Comms card: the package, the controllers, ATC, and the field navaids."""
    stations = comms_data.stations(m)
    page = Page(
        title=title, subtitle="RADIO AND NAVAID CARD", label="comms", footer=_FOOTER
    )

    columns = (
        Column("CALLSIGN", 12),
        Column("TYPE", 14),
        Column("ROLE", 13),
        Column("FREQUENCY", 18),
        Column("TACAN", 10),
        Column("STATION", 24),
    )
    for heading, rows in (
        ("your flight", [s for s in stations if s.player]),
        ("package", [s for s in stations if not s.player and not s.controller]),
        ("control", [s for s in stations if s.controller and not s.player]),
    ):
        if not rows:
            continue
        page.section(heading)
        page.table(
            columns,
            [
                (
                    s.callsign.upper(),
                    s.aircraft,
                    s.role.upper(),
                    s.frequency_text + ("" if s.preset is None else f"  {s.preset}"),
                    s.tacan or "--",
                    s.station or "",
                )
                for s in rows
            ],
        )

    page.note(
        "A frequency followed by Rn CHnn is that channel on the airframe's own "
        "default preset table — the rest are dialled by hand."
    )

    page.section("airfield atc")
    page.note(
        "DCS gives a field one radio per band for ground, tower and approach alike."
    )
    page.table(
        (
            Column("AIRFIELD", 20),
            Column("UHF", 9, ">"),
            Column("VHF HI", 9, ">"),
            Column("VHF LO", 9, ">"),
            Column("HF", 9, ">"),
        ),
        [
            (
                channel.airfield.upper(),
                _freq(channel.uhf_mhz),
                _freq(channel.vhf_high_mhz),
                _freq(channel.vhf_low_mhz),
                _freq(channel.hf_mhz),
            )
            for channel in comms_data.atc_channels(m, [c.airport for c in cards])
        ],
    )

    navaids = [(card, beacon) for card in cards for beacon in card.beacons]
    if navaids:
        page.section("navaids")
        page.table(
            (
                Column("AIRFIELD", 20),
                Column("TYPE", 11),
                Column("ID", 6),
                Column("TUNE", 11, ">"),
                Column("FROM FIELD", 14, ">"),
            ),
            [
                (
                    card.airport.name.upper(),
                    beacon.kind_label,
                    beacon.callsign,
                    beacon.label,
                    _from_field(card, beacon, m),
                )
                for card, beacon in navaids
            ],
        )
    if remarks:
        page.section("remarks")
        for remark in remarks:
            page.line(remark)
    return page


def airfield_page(m: Mission, card: AirfieldCard, *, title: str) -> Page:
    """One field the theatre does not chart: its numbers, its traffic, a plan view.

    Only reached for a field `kneeboard/charts.py` found no shipped chart of — see
    the module docstring. Everything drawn is a surveyed position; what cannot be
    derived (runway extent) is drawn as a direction and labelled as one.
    """
    airport = card.airport
    page = Page(
        title=title,
        subtitle=(
            f"{airport.name.upper()} — ELEV {card.elevation_ft:,.0f} FT — "
            f"RWY {card.runway_text}"
        ),
        label="airfield",
        footer=_FOOTER,
    )

    page.section("field")
    page.line(f"POSITION  {ddm(airport.position)}")
    page.line(
        f"ELEVATION {card.elevation_ft:,.0f} FT  ({card.elevation_m:.0f} M)"
        f"   PARKING {len(airport.parking_slots)} SLOTS"
    )
    for runway in card.runways:
        page.line(
            f"RUNWAY {runway.name:<9} "
            + "   ".join(approach_line(a) for a in runway.approaches)
        )

    radio = airport.atc_radio
    page.section("atc")
    page.line(
        f"UHF {_freq(_mhz(getattr(radio, 'uhf_hz', None)))}   "
        f"VHF HI {_freq(_mhz(getattr(radio, 'vhf_high_hz', None)))}   "
        f"VHF LO {_freq(_mhz(getattr(radio, 'vhf_low_hz', None)))}   "
        f"HF {_freq(_mhz(getattr(radio, 'hf_hz', None)))}"
    )

    if card.beacons:
        page.section("navaids")
        page.table(
            (
                Column("TYPE", 11),
                Column("ID", 6),
                Column("TUNE", 11, ">"),
                Column("BRG / RANGE", 14, ">"),
                Column("SERVES", 12),
            ),
            [
                (
                    beacon.kind_label,
                    beacon.callsign,
                    beacon.label,
                    _from_field(card, beacon, m),
                    _serves(card, beacon),
                )
                for beacon in card.beacons
            ],
        )

    if card.spawns or card.landings:
        page.section("this mission")
        for spawn in card.spawns:
            slots = ", ".join(spawn.slots) if spawn.slots else "runway"
            page.line(
                f"{'>' if spawn.player else ' '} {spawn.flight.upper():<12} "
                f"{spawn.aircraft:<14} START {spawn.start:<11} PARKING {slots}"
            )
        if card.landings:
            page.line(f"  RECOVERY    {', '.join(n.upper() for n in card.landings)}")

    page.art(660, lambda draw, box: sketch.draw_airfield(draw, box, card, m.terrain))
    page.note(sketch.legend(card))
    page.note(
        "This theatre ships no kneeboard chart for this field, which is why the page "
        "exists. Parking, beacon and reference positions are DCS survey data; it is "
        "not a taxi chart."
    )
    return page


# -- internals ---------------------------------------------------------------


def _serves(card: AirfieldCard, beacon) -> str:
    """The runway end whose approach lists this beacon, if any does."""
    for runway in card.runways:
        for approach in runway.approaches:
            if beacon in approach.aids:
                return f"RWY {approach.designator}"
    return ""


def _field_line(
    cards: Sequence[AirfieldCard], name: str, group: FlyingGroup | None
) -> str:
    """`BATUMI               ELEV 30 FT    PARKING 03, 04 (HOT)`.

    Elevation and parking, and nothing a chart already draws. `group` is passed
    for the departure field only: the parking slot belongs to the flight reading
    the card, not to whichever flight happens to be listed first at that field.
    """
    card = next((c for c in cards if c.airport.name.upper() == name), None)
    if card is None:
        return f"{name:<20}"
    text = f"{name:<20} ELEV {card.elevation_ft:>6,.0f} FT"
    spawn = None if group is None else card.spawn_of(group.name)
    if spawn is not None:
        slots = ", ".join(spawn.slots) if spawn.slots else "runway"
        text += f"    PARKING {slots} ({spawn.start})"
    return text


def _weather_lines(m: Mission) -> list[str]:
    w = m.weather
    winds = (
        ("SFC", w.wind_at_ground),
        ("2000 M", w.wind_at_2000),
        ("8000 M", w.wind_at_8000),
    )
    lines = [
        "WIND     "
        + "   ".join(
            f"{label} {wind.direction:03d}/{wind.speed * _KT_PER_MS:02.0f} KT"
            for label, wind in winds
        ),
        f"QNH      {w.qnh:.0f} MMHG  ({w.qnh * _INHG_PER_MMHG:.2f} INHG)"
        f"   TEMP {w.season_temperature:.0f} C",
        f"CLOUD    BASE {w.clouds_base * FT_PER_M:,.0f} FT  "
        f"THICKNESS {w.clouds_thickness * FT_PER_M:,.0f} FT  "
        f"DENSITY {w.clouds_density}/10",
        f"VIS      {w.visibility_distance / 1000:.0f} KM",
    ]
    lines.append("")
    return lines


def _track_text(track_true: float | None, variation: float | None) -> str:
    """The track a pilot flies: magnetic where the theatre's variation is known.

    One column rather than two — the merged route table has no room for both,
    and the variation is printed above it, so the true track is a subtraction
    away. A theatre absent from `flightplan.VARIATION_DEG_EAST` prints the true
    track under a `TRK T` heading instead of a magnetic one that is really true.
    """
    if track_true is None:
        return "--"
    if variation is None:
        return f"{track_true:03.0f}"
    value = magnetic(track_true, variation)
    return "--" if value is None else f"{value:03.0f}"


def _threats_section(page: Page, m: Mission) -> None:
    """The briefed air defence: the same points, and the same claim, as the HSD.

    This is not a fourth reveal channel. Every entry came from
    `PlanOverlay.threat` by way of `dtc.briefed`, so it is the estimate the F10
    plan drew under the mission's difficulty policy — nothing here is known that
    the map does not already show, and at `veteran`/`ace` `PlanOverlay` withholds
    the sites, `dtc.briefed_threats` comes back empty and the block is not
    written at all. What it adds is the half of that picture the cockpit cannot
    hold: only the F-16C loads a pre-planned threat cartridge, so for every other
    airframe in the package these coordinates exist nowhere but here, and even in
    the Viper the HSD ring has no readable position to check a mark against.

    `STPT` is the pre-planned steerpoint the point occupies in the Viper's
    cartridge (56 upward, in this order), so a ring on the HSD and a line on the
    card can be tied together by number. Two sites briefed under the same name —
    a pair of SA-13s on the same road — are numbered apart, since the coordinates
    are otherwise the only thing that tells them apart in a radio call.
    """
    from dcs_mission_creator.core import dtc

    points = dtc.briefed_threats(m)
    if not points:
        return
    bulls = _bullseye(m)
    titles = _numbered([point.title() for point in points])
    page.section("threats")
    page.table(
        (
            Column("STPT", 4, ">"),
            Column("THREAT", 20),
            Column("HSD", 3),
            Column("LAT / LONG (DDM)", 25),
            Column("RNG NM", 6, ">"),
            Column("CEIL FT", 7, ">"),
            Column("FROM BULLS", 11, ">"),
        ),
        [
            (
                str(dtc.FIRST_STEERPOINT + index),
                title,
                point.hsd_code(),
                ddm(point.position),
                f"{_radius_m(point) / M_PER_NM:.1f}",
                f"{_ceiling_m(point) * FT_PER_M:,.0f}",
                "%03.0fT/%.0fNM" % bearing_range(bulls, point.position),
            )
            for index, (point, title) in enumerate(zip(points, titles))
        ],
    )
    page.note(
        "Briefed positions, not fixes — the same estimates the F10 plan was drawn "
        "from, no better than the source the briefing cites for them. RNG and "
        "CEIL are the system's published envelope: the edge of a threat, not the "
        "edge of a safe area. STPT is this point on an F-16C's HSD. Air defence "
        "that drives with a column is absent on purpose — it is on the map as an "
        "icon with no ring, having left any position given here by the time the "
        "package is overhead."
    )


def _numbered(titles: Sequence[str]) -> list[str]:
    """`SA-13, SA-13` -> `SA-13 1, SA-13 2`; a name used once is left alone."""
    seen: dict[str, int] = {}
    out = []
    for title in titles:
        if titles.count(title) == 1:
            out.append(title)
            continue
        seen[title] = seen.get(title, 0) + 1
        out.append(f"{title} {seen[title]}")
    return out


def _radius_m(point) -> float:
    return point.system.radius_m if point.radius_m is None else point.radius_m


def _ceiling_m(point) -> float:
    return point.system.ceiling_m if point.ceiling_m is None else point.ceiling_m


def _airport_name(m: Mission, group: FlyingGroup, *, first: bool) -> str:
    if not group.points:
        return "--"
    point = group.points[0] if first else group.points[-1]
    if point.airdrome_id is None:
        return "AIRBORNE" if first else "--"
    airport = m.terrain.airport_by_id(point.airdrome_id)
    return airport.name.upper() if airport is not None else "--"


def _type_id(group: FlyingGroup) -> str:
    return group.units[0].unit_type.id if group.units else "--"


def _bullseye(m: Mission):
    from dcs.mapping import Point

    return Point(m.terrain.bullseye_blue["x"], m.terrain.bullseye_blue["y"], m.terrain)


def _from_field(card: AirfieldCard, beacon, m: Mission) -> str:
    bearing, nm = bearing_range(card.airport.position, beacon.position(m.terrain))
    return f"{bearing:03.0f}T / {nm:.1f} NM"


def _terrain_ft(overlay: MapOverlay | None, position) -> str:
    """Ground elevation under a steerpoint, in feet, or `--` with no overlay."""
    if overlay is None:
        return "--"
    from dcs_mission_creator.core import waypoints

    return f"{waypoints.ground_elevation_m(overlay, position) * FT_PER_M:,.0f}"


def _freq(mhz: float | None) -> str:
    return "--" if mhz is None else f"{mhz:.3f}"


def _mhz(hertz: float | None) -> float | None:
    return None if not hertz else float(hertz) / 1_000_000.0
