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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import AirDefence

if TYPE_CHECKING:
    from dcs.country import Country
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.terrain.terrain import Terrain
    from dcs.unitgroup import Group
    from dcs.unittype import VehicleType

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

# Standard component/launcher offsets (m) from the site centre. Radars sit
# close to the centre; launchers ring the site so the fan covers all azimuths.
_RADAR_DIST = 80.0
_CP_DIST = 60.0
_LAUNCHER_RING = 65.0


# -- shared placement primitives -------------------------------------------


def set_skill(group: Group, skill: Skill) -> None:
    """Apply `skill` to every unit of `group` (was per-mission `_set_skill`).

    Takes any pydcs `Group` — vehicle sites, flights, ships — not just the
    air-defense groups the rest of this module builds.
    """
    for u in group.units:
        u.skill = skill


def _add(
    m: Mission,
    vg: VehicleGroup,
    name: str,
    type_: type[VehicleType],
    center: Point,
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
    m: Mission,
    vg: VehicleGroup,
    label: str,
    type_: type[VehicleType],
    center: Point,
    site_heading: float,
    count: int,
    *,
    radius: float = _LAUNCHER_RING,
    start: int = 0,
) -> None:
    """Add units evenly spaced around the site centre, for index `start`..`count`.

    `start=1` is the self-contained SHORAD case: the group leader is itself a
    launcher sitting at the centre, so the ring only holds the other `count-1`.

    Bearings are absolute, not relative to `site_heading`. For a full, evenly
    spaced ring that is merely a rotation of the same shape, so it has never
    mattered — but it does mean rotating a site turns its radars and not its
    launcher ring. Changing it would move units, so it is left alone here.
    """
    step = 360.0 / count
    for i in range(start, count):
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
    overlay: MapOverlay | None,
    terrain: Terrain | None,
) -> VehicleGroup:
    """Set uniform skill and, if an overlay is given, snap units off canopy."""
    set_skill(vg, skill)
    if overlay is not None and terrain is not None:
        # Imported here, not at module scope: core.placement reaches into
        # map_overlay.query, which pulls in numpy and the whole raster stack.
        # A module about SAM component layouts should not cost that to import
        # when the caller never asks for snapping.
        from dcs_mission_creator.core.placement import snap_units_clear

        snap_units_clear(overlay, terrain, vg)
    elif overlay is not None or terrain is not None:
        # Snapping needs both. Passing one alone used to skip it in silence,
        # leaving launchers in the canopy on exactly the rough terrain the
        # caller was trying to handle.
        log.warning(
            "air-defense site not snapped to clear ground: pass overlay and terrain",
            name=vg.name,
            overlay=overlay is not None,
            terrain=terrain is not None,
        )
    log.debug("built air-defense site", name=vg.name, units=len(vg.units))
    return vg


# -- site catalogue ----------------------------------------------------------
#
# Every site below was once its own 35-line function whose body differed from
# its neighbours by a handful of unit types. The shape is always the same —
# a leader at the centre, fixed components at a bearing offset from the site
# heading, and a ring of launchers — so it is expressed as data and assembled
# by one of the two engines underneath. Adding a system is a table entry.


@dataclass(frozen=True)
class _Component:
    """One fixed unit offset from the site centre (a radar, a command post)."""

    label: str
    type_: type[VehicleType]
    bearing_offset: float  # degrees, relative to the site heading
    distance: float


@dataclass(frozen=True)
class _SiteSpec:
    """What a site is made of. Force *composition* stays a per-call argument."""

    site_name: str  # group name, before `prefix`
    leader: type[VehicleType]
    launcher_label: str
    launcher: type[VehicleType]
    default_launchers: int
    components: tuple[_Component, ...] = ()
    launcher_radius: float = _LAUNCHER_RING
    #: True when the leader is itself a launcher (self-contained SHORAD), so
    #: the ring holds the remaining `launchers - 1` units.
    leader_is_launcher: bool = False


def _build_site(
    spec: _SiteSpec,
    m: Mission,
    country: Country,
    position: Point,
    heading: float,
    *,
    launchers: int,
    prefix: str,
    skill: Skill,
    overlay: MapOverlay | None,
    terrain: Terrain | None,
    extra: tuple[_Component, ...] = (),
) -> VehicleGroup:
    """Assemble one site from its spec.

    Unit order is part of the output — pydcs numbers units as they are added —
    so a radar site lays down leader, components, launchers, while
    self-contained SHORAD lays down leader, ring, then any trailing component
    (the SA-13's optional Dog Ear).
    """
    vg = m.vehicle_group(
        country, prefix + spec.site_name, spec.leader, position, heading
    )

    def add_ring() -> None:
        _ring(
            m,
            vg,
            spec.launcher_label,
            spec.launcher,
            position,
            heading,
            launchers,
            radius=spec.launcher_radius,
            start=1 if spec.leader_is_launcher else 0,
        )

    def add(components: tuple[_Component, ...]) -> None:
        for c in components:
            _add(
                m,
                vg,
                c.label,
                c.type_,
                position,
                heading,
                bearing=heading + c.bearing_offset,
                distance=c.distance,
            )

    if spec.leader_is_launcher:
        add_ring()
        add(spec.components + extra)
    else:
        add(spec.components + extra)
        add_ring()
    return _finish(vg, skill, overlay, terrain)


_SA2 = _SiteSpec(
    site_name="SA-2 site",
    leader=AirDefence.P_19_s_125_sr,
    components=(
        _Component("Fan Song TR", AirDefence.SNR_75V, 0.0, _RADAR_DIST),
        _Component("RD-75", AirDefence.RD_75, 180.0, _CP_DIST),
    ),
    launcher_label="S-75 launcher",
    launcher=AirDefence.S_75M_Volhov,
    default_launchers=6,
)

_SA3 = _SiteSpec(
    site_name="SA-3 site",
    leader=AirDefence.P_19_s_125_sr,
    components=(_Component("Low Blow TR", AirDefence.Snr_s_125_tr, 0.0, _RADAR_DIST),),
    launcher_label="S-125 launcher",
    launcher=AirDefence.X_5p73_s_125_ln,
    default_launchers=4,
)

_SA5 = _SiteSpec(
    site_name="SA-5 site",
    leader=AirDefence.RLS_19J6,
    components=(_Component("Square Pair TR", AirDefence.RPC_5N62V, 0.0, _RADAR_DIST),),
    launcher_label="S-200 launcher",
    launcher=AirDefence.S_200_Launcher,
    default_launchers=4,
    launcher_radius=90.0,
)

_NASAMS = _SiteSpec(
    site_name="NASAMS site",
    leader=AirDefence.NASAMS_Radar_MPQ64F1,
    components=(
        _Component("NASAMS C2", AirDefence.NASAMS_Command_Post, 180.0, _CP_DIST),
    ),
    launcher_label="NASAMS launcher",
    launcher=AirDefence.NASAMS_LN_C,
    default_launchers=3,
)

_IRIST = _SiteSpec(
    site_name="IRIS-T SLM site",
    leader=AirDefence.CHAP_IRISTSLM_STR,
    components=(_Component("IRIS-T C2", AirDefence.CHAP_IRISTSLM_CP, 180.0, _CP_DIST),),
    launcher_label="IRIS-T launcher",
    launcher=AirDefence.CHAP_IRISTSLM_LN,
    default_launchers=3,
)

_ROLAND = _SiteSpec(
    site_name="Roland site",
    leader=AirDefence.Roland_Radar,
    launcher_label="Roland ADS",
    launcher=AirDefence.Roland_ADS,
    default_launchers=2,
)

_RAPIER = _SiteSpec(
    site_name="Rapier site",
    leader=AirDefence.Rapier_fsa_blindfire_radar,
    components=(
        _Component(
            "Rapier tracker",
            AirDefence.Rapier_fsa_optical_tracker_unit,
            180.0,
            _CP_DIST,
        ),
    ),
    launcher_label="Rapier launcher",
    launcher=AirDefence.Rapier_fsa_launcher,
    default_launchers=2,
)

_HQ7 = _SiteSpec(
    site_name="HQ-7 site",
    leader=AirDefence.HQ_7_STR_SP,
    launcher_label="HQ-7 TELAR",
    launcher=AirDefence.HQ_7_LN_SP,
    default_launchers=4,
)

_SA8 = _SiteSpec(
    site_name="SA-8 site",
    leader=AirDefence.Osa_9A33_ln,
    launcher_label="Osa",
    launcher=AirDefence.Osa_9A33_ln,
    default_launchers=3,
    leader_is_launcher=True,
)

_SA13 = _SiteSpec(
    site_name="SA-13 site",
    leader=AirDefence.Strela_10M3,
    launcher_label="Strela-10",
    launcher=AirDefence.Strela_10M3,
    default_launchers=2,
    leader_is_launcher=True,
)

_SA15 = _SiteSpec(
    site_name="SA-15 site",
    leader=AirDefence.Tor_9A331,
    launcher_label="Tor",
    launcher=AirDefence.Tor_9A331,
    default_launchers=2,
    leader_is_launcher=True,
)

_SA19 = _SiteSpec(
    site_name="SA-19 site",
    leader=AirDefence.X_2S6_Tunguska,
    launcher_label="Tunguska",
    launcher=AirDefence.X_2S6_Tunguska,
    default_launchers=2,
    leader_is_launcher=True,
)

_DOG_EAR = _Component("Dog Ear SR", AirDefence.Dog_Ear_radar, 0.0, _RADAR_DIST)


# -- public builders ---------------------------------------------------------
#
# One per system. They all take the same arguments, so rather than repeat that
# signature a dozen times — which is the boilerplate this module was carrying
# in the first place — it is declared once as `SiteBuilder` and bound to a
# spec by `_builder`. The SA-13 is the only system with an extra knob, so it
# is the only one still written out longhand.


class SiteBuilder(Protocol):
    """Call signature shared by every `build_*_site` function.

    `launchers=None` means "whatever this system normally fields"; pass a
    number to set the size of the site. `overlay` + `terrain` together opt in
    to snapping units off canopy and water — supply both or neither.
    """

    def __call__(
        self,
        m: Mission,
        country: Country,
        position: Point,
        heading: float = 0,
        *,
        launchers: int | None = None,
        prefix: str = "",
        skill: Skill = Skill.Average,
        overlay: MapOverlay | None = None,
        terrain: Terrain | None = None,
    ) -> VehicleGroup: ...


def _builder(spec: _SiteSpec, name: str, doc: str) -> SiteBuilder:
    def build(
        m: Mission,
        country: Country,
        position: Point,
        heading: float = 0,
        *,
        launchers: int | None = None,
        prefix: str = "",
        skill: Skill = Skill.Average,
        overlay: MapOverlay | None = None,
        terrain: Terrain | None = None,
    ) -> VehicleGroup:
        return _build_site(
            spec,
            m,
            country,
            position,
            heading,
            launchers=spec.default_launchers if launchers is None else launchers,
            prefix=prefix,
            skill=skill,
            overlay=overlay,
            terrain=terrain,
        )

    build.__name__ = name
    build.__qualname__ = name
    build.__doc__ = doc
    return build


build_sa2_site = _builder(
    _SA2,
    "build_sa2_site",
    "SA-2 (S-75 'Guideline'): Flat Face SR + Fan Song TR + RF + N launchers.",
)
build_sa3_site = _builder(
    _SA3,
    "build_sa3_site",
    "SA-3 (S-125 'Goa'): Flat Face SR + Low Blow TR + N launchers.",
)
build_sa5_site = _builder(
    _SA5,
    "build_sa5_site",
    "SA-5 (S-200 'Gammon'): Tin Shield SR + Square Pair TR + N launchers.",
)
build_nasams_site = _builder(
    _NASAMS,
    "build_nasams_site",
    "NASAMS: MPQ-64F1 Sentinel SR + command post + N AIM-120C launchers.",
)
build_irist_site = _builder(
    _IRIST,
    "build_irist_site",
    "IRIS-T SLM: search/track radar + command post + N launchers.",
)
build_roland_site = _builder(
    _ROLAND,
    "build_roland_site",
    "Roland: acquisition radar (EWR) + N Roland ADS fire units.",
)
build_rapier_site = _builder(
    _RAPIER,
    "build_rapier_site",
    "Rapier: blindfire TR + optical tracker + N launchers.",
)
build_hq7_site = _builder(
    _HQ7,
    "build_hq7_site",
    "HQ-7: search radar + N HQ-7B SHORAD TELARs.",
)

# Self-contained SHORAD: every vehicle carries its own radar, so the group
# leader is a launcher and there is no separate search radar to kill.
build_sa8_site = _builder(
    _SA8,
    "build_sa8_site",
    "SA-8 (Osa 'Gecko'): N self-contained TELARs, no separate radar.",
)
build_sa15_site = _builder(
    _SA15,
    "build_sa15_site",
    "SA-15 (Tor 'Gauntlet'): N self-contained TELARs (current pydcs id).",
)
build_sa19_site = _builder(
    _SA19,
    "build_sa19_site",
    "SA-19 (2S6 Tunguska 'Grison'): N self-contained gun/missile TELARs.",
)


def build_sa13_site(
    m: Mission,
    country: Country,
    position: Point,
    heading: float = 0,
    *,
    launchers: int | None = None,
    prefix: str = "",
    skill: Skill = Skill.Average,
    with_dog_ear: bool = True,
    overlay: MapOverlay | None = None,
    terrain: Terrain | None = None,
) -> VehicleGroup:
    """SA-13 (Strela-10 'Gopher'): N TELARs, optional Dog Ear search radar.

    The Strela-10 is IR-guided, so the Dog Ear is what gives the section early
    warning — drop it for a genuinely blind, pop-up SHORAD threat.
    """
    return _build_site(
        _SA13,
        m,
        country,
        position,
        heading,
        launchers=_SA13.default_launchers if launchers is None else launchers,
        prefix=prefix,
        skill=skill,
        overlay=overlay,
        terrain=terrain,
        extra=(_DOG_EAR,) if with_dog_ear else (),
    )
