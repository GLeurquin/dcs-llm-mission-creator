"""Spatial overlay database for DCS theaters.

Public API:
    MapOverlay   - read-only runtime accessor
    Vegetation   - enum for vegetation_at() return type
    Placement    - single-cell filter for find_placement()
    TacticalScene - multi-placement composition helpers

Build pipeline lives in `builder_*` modules; invoked via the
`dcs-mission-creator map-overlay build <theater>` CLI subcommand.
"""

from __future__ import annotations

from dcs_mission_creator.map_overlay.placement import Placement, Vegetation
from dcs_mission_creator.map_overlay.query import MapOverlay
from dcs_mission_creator.map_overlay.scene import ConvoyRoute, TacticalScene

__all__ = [
    "ConvoyRoute",
    "MapOverlay",
    "Placement",
    "TacticalScene",
    "Vegetation",
]
