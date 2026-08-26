"""The route as numbers: track, distance, altitude, speed, time — per leg.

Everything here is read off the flight group the mission already built, so the
page cannot disagree with the route the jet flies. Nothing is invented and one
thing is deliberately *not* computed:

**The timings are zero-wind, and say so.** The mission file's wind `dir` is one
number with two possible readings — the direction the wind blows from, or the
direction it blows to — and the project's own `core/weather.py` documents it as
"from" while DCS's editor labels it only `DIR`. A wind-corrected heading printed
off the wrong reading is out by twice the drift angle and looks authoritative, so
the page carries the wind profile as its own block and leaves the correction to
the pilot. At 400 kt TAS against the 8-23 kt winds these missions set, the ETE
error is under six per cent; a 180-degree wind error would be worse than that and
invisible.

**Magnetic tracks come from a per-theater constant**, printed on the page next to
the number so it can be checked, and omitted (leaving true tracks only) for a
theater the table does not cover. DCS models one declination per map, not a
geomagnetic field, and there is no pydcs field carrying it — `Airport.tacan` is
`None` for a field with a TACAN, so the theater data is not the place to look.

The other pydcs shape worth knowing: `add_runway_waypoint` (the departure and
approach gate every mission puts 7 km off the field) writes `alt_type="RADIO"`,
i.e. an *AGL* altitude, while every other waypoint is `BARO`. Printing both as
MSL would put the gate 1000 ft below the terrain it is over, so an AGL altitude
is flagged in its own column rather than silently converted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from dcs import task

if TYPE_CHECKING:
    from dcs.mapping import Point
    from dcs.point import MovingPoint
    from dcs.unitgroup import FlyingGroup

M_PER_NM = 1852.0
FT_PER_M = 3.280839895

#: Theater → magnetic variation, degrees east. DCS applies a single declination
#: per map; these are the published values for the map epochs, good to about a
#: degree. A theater absent from the table prints true tracks only, which is why
#: this is a lookup and not a default of zero.
VARIATION_DEG_EAST: dict[str, float] = {
    "Caucasus": 6.0,
    "Syria": 5.0,
    "PersianGulf": 2.0,
    "Nevada": 12.0,
    "Sinai": 4.5,
    "MarianaIslands": 1.0,
    "Normandy": -10.0,
    "TheChannel": -10.0,
    "Falklands": 9.0,
}


@dataclass(frozen=True)
class Leg:
    """One waypoint and the leg flown to it."""

    number: int
    name: str
    position: Point
    #: Metres, and whether they are above the terrain rather than above the sea.
    altitude_m: float
    agl: bool
    #: Commanded true airspeed in km/h, as the mission wrote it.
    tas_kph: float
    #: True track *into* this waypoint; `None` for the first point of the route.
    track_true: float | None
    leg_m: float
    total_m: float
    ete_s: float | None
    elapsed_s: float
    remark: str

    @property
    def leg_nm(self) -> float:
        return self.leg_m / M_PER_NM

    @property
    def total_nm(self) -> float:
        return self.total_m / M_PER_NM

    @property
    def altitude_ft(self) -> float:
        return self.altitude_m * FT_PER_M

    @property
    def tas_kt(self) -> float:
        return self.tas_kph / M_PER_NM * 1000.0


def flight_plan(group: FlyingGroup) -> list[Leg]:
    """Every waypoint of `group`, with the leg into it worked out.

    Speeds are taken from the waypoint being flown *to*, which is how DCS reads
    them, and a waypoint with no speed of its own (a `land_at` point, which pydcs
    writes as zero) inherits the last commanded speed rather than dividing by it.
    """
    names = waypoint_names(group)
    legs: list[Leg] = []
    total_m = 0.0
    elapsed_s = 0.0
    last_speed_ms = 0.0
    for index, point in enumerate(group.points):
        speed_ms = point.speed or last_speed_ms
        last_speed_ms = speed_ms or last_speed_ms
        if index == 0:
            leg_m, track, ete_s = 0.0, None, None
        else:
            previous = group.points[index - 1]
            leg_m = previous.position.distance_to_point(point.position)
            track = previous.position.heading_between_point(point.position)
            ete_s = leg_m / speed_ms if speed_ms > 0 else None
            total_m += leg_m
            elapsed_s += ete_s or 0.0
        legs.append(
            Leg(
                number=index + 1,
                name=names[index],
                position=point.position,
                altitude_m=float(point.alt),
                agl=point.alt_type == "RADIO",
                tas_kph=speed_ms * 3.6,
                track_true=track,
                leg_m=leg_m,
                total_m=total_m,
                ete_s=ete_s,
                elapsed_s=elapsed_s,
                remark=remark(point),
            )
        )
    return legs


def waypoint_names(group: FlyingGroup) -> list[str]:
    """Waypoint names, with pydcs's two unnamed gates given the names they earn.

    Public because `core/dtc.py` writes the same names into the Viper's
    steerpoint tab: a route card and a cartridge that disagree about what a
    point is called is the sort of thing a pilot only notices at the worst
    moment.

    `add_runway_waypoint` is the departure and approach gate every mission puts
    7 km off the field — pydcs names it nothing and marks it `alt_type="RADIO"`,
    so a card printed straight from the route said `WP 2` and `WP 8` for the two
    points a pilot actually navigates by on the way out and back. Named from
    position in the route rather than from the altitude type alone: an unnamed AGL
    point in the middle of a route is a low-level turning point, not a gate.
    """
    names = [waypoint_name(point, index) for index, point in enumerate(group.points)]
    last = len(group.points) - 1
    for index, point in enumerate(group.points):
        if str(point.name or "").strip() or point.alt_type != "RADIO":
            continue
        if index == 1:
            names[index] = "DEP GATE"
        elif index in (last - 1, last):
            names[index] = "APCH GATE"
    return names


def waypoint_name(point: MovingPoint, index: int) -> str:
    """The mission's own name for the point, or what pydcs's type says it is."""
    name = str(point.name or "").strip()
    if name:
        return name.upper()
    by_type = {
        "TakeOffParking": "TAKEOFF COLD",
        "TakeOffParkingHot": "TAKEOFF HOT",
        "TakeOff": "TAKEOFF RWY",
        "TakeOffGround": "TAKEOFF GND",
        "TakeOffGroundHot": "TAKEOFF GND",
        "Land": "LAND",
        "LandingReFuAr": "LAND / REARM",
    }
    return by_type.get(point.type, f"WP {index + 1}")


def remark(point: MovingPoint) -> str:
    """What the waypoint is tasked with, in the words a briefing would use.

    Only the tasks that change what the pilot does there: an orbit, an attack, a
    refuelling join, a landing. Everything else (the option-setting commands every
    flight carries) would fill the column without telling anyone anything.
    """
    labels = {
        task.OrbitAction: "ORBIT",
        task.AttackGroup: "ATTACK GROUP",
        task.AttackUnit: "ATTACK UNIT",
        task.Bombing: "BOMBING",
        task.BombingRunway: "RUNWAY ATK",
        task.RefuelingTaskAction: "REFUEL",
        task.EngageTargets: "ENGAGE",
        task.EngageTargetsInZone: "ENGAGE ZONE",
        task.Follow: "FOLLOW",
        task.EscortTaskAction: "ESCORT",
        task.Land: "LAND",
    }
    for tsk in point.tasks:
        for cls, label in labels.items():
            if isinstance(tsk, cls):
                return label
    if point.type == "Land":
        return "LAND"
    return ""


def variation_deg(theater: str) -> float | None:
    """Magnetic variation east for `theater`, or `None` if we do not know it."""
    return VARIATION_DEG_EAST.get(theater)


def magnetic(track_true: float | None, variation: float | None) -> float | None:
    """True to magnetic: subtract easterly variation."""
    if track_true is None or variation is None:
        return None
    return (track_true - variation) % 360.0


def hms(seconds: float | None) -> str:
    """`MM:SS`, or `H:MM:SS` past the hour — a route table's whole time format."""
    if seconds is None:
        return "--"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def ddm(position: Point) -> str:
    """Degrees and decimal minutes — the format a DED, UFC or CDU is typed in.

    Identical to the JTAC readout's `ddm` (`core/lua/jtac_coords.lua`) on purpose:
    a coordinate a player hears on the radio and one they read off the kneeboard
    have to look the same or one of them gets typed wrong.
    """
    latlng = position.latlng()
    north, lat_d, lat_m = _deg_min(latlng.lat)
    east, lon_d, lon_m = _deg_min(latlng.lng)
    return (
        f"{'N' if north else 'S'} {lat_d:02d} {lat_m:06.3f}  "
        f"{'E' if east else 'W'} {lon_d:03d} {lon_m:06.3f}"
    )


def _deg_min(value: float) -> tuple[bool, int, float]:
    positive = value >= 0.0
    magnitude = abs(value)
    degrees = int(math.floor(magnitude))
    return positive, degrees, (magnitude - degrees) * 60.0


def bearing_range(origin: Point, target: Point) -> tuple[float, float]:
    """`(true bearing, nautical miles)` from `origin` to `target`."""
    return (
        origin.heading_between_point(target),
        origin.distance_to_point(target) / M_PER_NM,
    )


def total_time_s(legs: Sequence[Leg]) -> float:
    return legs[-1].elapsed_s if legs else 0.0
