"""Air-defense site builders (project-owned helper).

pydcs ships `dcs.templates.VehicleTemplate` with a handful of canned SAM sites
(`sa6_site`, `sa11_site`, `sa15_site`, `Russia.sa10_site`, `USA.patriot_site`,
`USA.hawk_site`) — use those directly for the systems they cover. This module
fills the gaps: the SA-2/3/5/8/13/19 and Western short/medium SHORAD
(NASAMS, IRIS-T SLM, Roland, Rapier, HQ-7) sites pydcs has no template for.

Each builder assembles one complete site — search radar, track/fire-control
radar, command post and launchers, wired into a single `VehicleGroup` — from
one call, so missions stop copy-pasting component type lists and offset math.

Design rule (mirrors `core/placement.py` / `core/map_draw.py` / `core/tts`):
the mission passes in an **absolute** world `Point` and a `heading`; this
helper does the component placement (`point_from_heading` offsets), uniform
`skill`, and — when handed the map overlay — the post-build `snap_units_clear`
that nudges units off canopy/water on rough terrain. Absolute `Point` in, a
built `VehicleGroup` out. There is no faction abstraction anywhere in the
project, so the caller passes the raw pydcs `Country` (red or blue) exactly as
it does for `mission.vehicle_group(...)`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import structlog
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core.placement import snap_units_clear

if TYPE_CHECKING:
    from dcs.country import Country
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.terrain.terrain import Terrain
    from dcs.unittype import VehicleType

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

# Standard component/launcher offsets (m) from the site centre. Radars sit
# close to the centre; launchers ring the site so the fan covers all azimuths.
_RADAR_DIST = 80.0
_CP_DIST = 60.0
_LAUNCHER_RING = 65.0


# -- shared placement primitives -------------------------------------------


def set_skill(group: VehicleGroup, skill: Skill) -> None:
    """Apply `skill` to every unit of `group` (was per-mission `_set_skill`)."""
    for u in group.units:
        u.skill = skill


def _add(
    m: "Mission",
    vg: VehicleGroup,
    name: str,
    type_: "type[VehicleType]",
    center: "Point",
    site_heading: float,
    *,
    bearing: float,
    distance: float,
) -> None:
    """Add one unit to `vg` at `bearing`/`distance` from the site centre."""
    u = m.vehicle(name, type_)
    u.position = center.point_from_heading(bearing, distance)
    u.heading = site_heading
    vg.add_unit(u)


def _ring(
    m: "Mission",
    vg: VehicleGroup,
    label: str,
    type_: "type[VehicleType]",
    center: "Point",
    site_heading: float,
    count: int,
    *,
    radius: float = _LAUNCHER_RING,
) -> None:
    """Add `count` identical units evenly spaced around the site centre."""
    step = 360.0 / count
    for i in range(count):
        _add(
            m,
            vg,
            f"{label} #{i + 1}",
            type_,
            center,
            site_heading,
            bearing=i * step,
            distance=radius,
        )


def _finish(
    vg: VehicleGroup,
    skill: Skill,
    overlay: Optional["MapOverlay"],
    terrain: Optional["Terrain"],
) -> VehicleGroup:
    """Set uniform skill and, if an overlay is given, snap units off canopy."""
    set_skill(vg, skill)
    if overlay is not None and terrain is not None:
        snap_units_clear(overlay, terrain, vg)
    log.debug("built air-defense site", name=vg.name, units=len(vg.units))
    return vg


# -- radar + launcher sites (pydcs has no template for these) ---------------


def build_sa2_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 6,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-2 (S-75 'Guideline'): Flat Face SR + Fan Song TR + RF + N launchers."""
    vg = m.vehicle_group(
        country, prefix + "SA-2 site", AirDefence.P_19_s_125_sr, position, heading
    )
    _add(
        m,
        vg,
        "Fan Song TR",
        AirDefence.SNR_75V,
        position,
        heading,
        bearing=heading,
        distance=_RADAR_DIST,
    )
    _add(
        m,
        vg,
        "RD-75",
        AirDefence.RD_75,
        position,
        heading,
        bearing=heading + 180,
        distance=_CP_DIST,
    )
    _ring(m, vg, "S-75 launcher", AirDefence.S_75M_Volhov, position, heading, launchers)
    return _finish(vg, skill, overlay, terrain)


def build_sa3_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 4,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-3 (S-125 'Goa'): Flat Face SR + Low Blow TR + N launchers."""
    vg = m.vehicle_group(
        country, prefix + "SA-3 site", AirDefence.P_19_s_125_sr, position, heading
    )
    _add(
        m,
        vg,
        "Low Blow TR",
        AirDefence.Snr_s_125_tr,
        position,
        heading,
        bearing=heading,
        distance=_RADAR_DIST,
    )
    _ring(
        m,
        vg,
        "S-125 launcher",
        AirDefence.X_5p73_s_125_ln,
        position,
        heading,
        launchers,
    )
    return _finish(vg, skill, overlay, terrain)


def build_sa5_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 4,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-5 (S-200 'Gammon'): Tin Shield SR + Square Pair TR + N launchers."""
    vg = m.vehicle_group(
        country, prefix + "SA-5 site", AirDefence.RLS_19J6, position, heading
    )
    _add(
        m,
        vg,
        "Square Pair TR",
        AirDefence.RPC_5N62V,
        position,
        heading,
        bearing=heading,
        distance=_RADAR_DIST,
    )
    _ring(
        m,
        vg,
        "S-200 launcher",
        AirDefence.S_200_Launcher,
        position,
        heading,
        launchers,
        radius=90.0,
    )
    return _finish(vg, skill, overlay, terrain)


def build_nasams_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 3,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """NASAMS: MPQ-64F1 Sentinel SR + command post + N AIM-120C launchers."""
    vg = m.vehicle_group(
        country,
        prefix + "NASAMS site",
        AirDefence.NASAMS_Radar_MPQ64F1,
        position,
        heading,
    )
    _add(
        m,
        vg,
        "NASAMS C2",
        AirDefence.NASAMS_Command_Post,
        position,
        heading,
        bearing=heading + 180,
        distance=_CP_DIST,
    )
    _ring(
        m, vg, "NASAMS launcher", AirDefence.NASAMS_LN_C, position, heading, launchers
    )
    return _finish(vg, skill, overlay, terrain)


def build_irist_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 3,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """IRIS-T SLM: search/track radar + command post + N launchers."""
    vg = m.vehicle_group(
        country,
        prefix + "IRIS-T SLM site",
        AirDefence.CHAP_IRISTSLM_STR,
        position,
        heading,
    )
    _add(
        m,
        vg,
        "IRIS-T C2",
        AirDefence.CHAP_IRISTSLM_CP,
        position,
        heading,
        bearing=heading + 180,
        distance=_CP_DIST,
    )
    _ring(
        m,
        vg,
        "IRIS-T launcher",
        AirDefence.CHAP_IRISTSLM_LN,
        position,
        heading,
        launchers,
    )
    return _finish(vg, skill, overlay, terrain)


def build_roland_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 2,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """Roland: acquisition radar (EWR) + N Roland ADS fire units."""
    vg = m.vehicle_group(
        country, prefix + "Roland site", AirDefence.Roland_Radar, position, heading
    )
    _ring(m, vg, "Roland ADS", AirDefence.Roland_ADS, position, heading, launchers)
    return _finish(vg, skill, overlay, terrain)


def build_rapier_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 2,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """Rapier: blindfire TR + optical tracker + N launchers."""
    vg = m.vehicle_group(
        country,
        prefix + "Rapier site",
        AirDefence.Rapier_fsa_blindfire_radar,
        position,
        heading,
    )
    _add(
        m,
        vg,
        "Rapier tracker",
        AirDefence.Rapier_fsa_optical_tracker_unit,
        position,
        heading,
        bearing=heading + 180,
        distance=_CP_DIST,
    )
    _ring(
        m,
        vg,
        "Rapier launcher",
        AirDefence.Rapier_fsa_launcher,
        position,
        heading,
        launchers,
    )
    return _finish(vg, skill, overlay, terrain)


def build_hq7_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 4,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """HQ-7: search radar + N HQ-7B SHORAD TELARs."""
    vg = m.vehicle_group(
        country, prefix + "HQ-7 site", AirDefence.HQ_7_STR_SP, position, heading
    )
    _ring(m, vg, "HQ-7 TELAR", AirDefence.HQ_7_LN_SP, position, heading, launchers)
    return _finish(vg, skill, overlay, terrain)


# -- self-contained SHORAD (each vehicle carries its own radar) -------------


def build_sa8_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 3,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-8 (Osa 'Gecko'): N self-contained TELARs, no separate radar."""
    vg = m.vehicle_group(
        country, prefix + "SA-8 site", AirDefence.Osa_9A33_ln, position, heading
    )
    for i in range(1, launchers):
        _add(
            m,
            vg,
            f"Osa #{i + 1}",
            AirDefence.Osa_9A33_ln,
            position,
            heading,
            bearing=i * (360.0 / launchers),
            distance=_LAUNCHER_RING,
        )
    return _finish(vg, skill, overlay, terrain)


def build_sa13_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 2,
    prefix: str = "",
    skill: Skill = Skill.Average,
    with_dog_ear: bool = True,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-13 (Strela-10 'Gopher'): N TELARs, optional Dog Ear search radar."""
    vg = m.vehicle_group(
        country, prefix + "SA-13 site", AirDefence.Strela_10M3, position, heading
    )
    for i in range(1, launchers):
        _add(
            m,
            vg,
            f"Strela-10 #{i + 1}",
            AirDefence.Strela_10M3,
            position,
            heading,
            bearing=i * (360.0 / launchers),
            distance=_LAUNCHER_RING,
        )
    if with_dog_ear:
        _add(
            m,
            vg,
            "Dog Ear SR",
            AirDefence.Dog_Ear_radar,
            position,
            heading,
            bearing=heading,
            distance=_RADAR_DIST,
        )
    return _finish(vg, skill, overlay, terrain)


def build_sa15_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 2,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-15 (Tor 'Gauntlet'): N self-contained TELARs (current pydcs id)."""
    vg = m.vehicle_group(
        country, prefix + "SA-15 site", AirDefence.Tor_9A331, position, heading
    )
    for i in range(1, launchers):
        _add(
            m,
            vg,
            f"Tor #{i + 1}",
            AirDefence.Tor_9A331,
            position,
            heading,
            bearing=i * (360.0 / launchers),
            distance=_LAUNCHER_RING,
        )
    return _finish(vg, skill, overlay, terrain)


def build_sa19_site(
    m: "Mission",
    country: "Country",
    position: "Point",
    heading: float = 0,
    *,
    launchers: int = 2,
    prefix: str = "",
    skill: Skill = Skill.Average,
    overlay: Optional["MapOverlay"] = None,
    terrain: Optional["Terrain"] = None,
) -> VehicleGroup:
    """SA-19 (2S6 Tunguska 'Grison'): N self-contained gun/missile TELARs."""
    vg = m.vehicle_group(
        country, prefix + "SA-19 site", AirDefence.X_2S6_Tunguska, position, heading
    )
    for i in range(1, launchers):
        _add(
            m,
            vg,
            f"Tunguska #{i + 1}",
            AirDefence.X_2S6_Tunguska,
            position,
            heading,
            bearing=i * (360.0 / launchers),
            distance=_LAUNCHER_RING,
        )
    return _finish(vg, skill, overlay, terrain)
