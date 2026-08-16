"""Mission weather as a record instead of fourteen assignments.

Every mission set the same fourteen `m.weather` fields in the same order,
differing only in the numbers — a data record written as a procedure. Stating
it as one is shorter, diffable, and makes the wind profile readable at a glance
rather than spread over six lines.

Nothing here is a policy: a mission states its own weather. `Wind` carries a
sensible shear default only so a mission that does not care about the profile
does not have to invent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dcs.mission import Mission


@dataclass(frozen=True)
class Wind:
    """Wind at one altitude: direction it blows *from* (deg), speed (m/s)."""

    direction: int
    speed: int


@dataclass(frozen=True)
class Weather:
    """A complete weather state, applied with `apply`.

    `clouds_density` is DCS's 0-10 preset scale (0 clear, ~4 scattered, ~8
    overcast) and `visibility_distance` is in metres.
    """

    name: str
    season_temperature: float
    clouds_base: int
    clouds_thickness: int
    clouds_density: int
    visibility_distance: int
    wind_at_ground: Wind
    wind_at_2000: Wind
    wind_at_8000: Wind
    #: Sea-level pressure in mmHg. Standard unless a mission says otherwise —
    #: all six missions hard-coded this same value.
    qnh: int = 760

    def apply(self, m: Mission) -> None:
        """Write this state onto the mission's weather block."""
        w = m.weather
        w.name = self.name
        w.season_temperature = self.season_temperature
        w.qnh = self.qnh
        w.clouds_base = self.clouds_base
        w.clouds_thickness = self.clouds_thickness
        w.clouds_density = self.clouds_density
        w.visibility_distance = self.visibility_distance
        for target, wind in (
            (w.wind_at_ground, self.wind_at_ground),
            (w.wind_at_2000, self.wind_at_2000),
            (w.wind_at_8000, self.wind_at_8000),
        ):
            target.direction = wind.direction
            target.speed = wind.speed
