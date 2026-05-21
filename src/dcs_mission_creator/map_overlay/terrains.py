"""Slug → pydcs Terrain class registry.

Mirrors the canonical names used by the `dcs-mission-creator map-overlay`
subcommand.
"""

from __future__ import annotations

from typing import Callable

from dcs.terrain import (
    Caucasus,
    Falklands,
    MarianaIslands,
    Nevada,
    Normandy,
    PersianGulf,
    Sinai,
    Syria,
    TheChannel,
)
from dcs.terrain.terrain import Terrain

# Each concrete terrain provides a no-arg `__init__`; the abstract base does
# not, so we treat the registry as a no-arg factory rather than `type[Terrain]`.
_REGISTRY: dict[str, Callable[[], Terrain]] = {
    "caucasus": Caucasus,
    "syria": Syria,
    "persiangulf": PersianGulf,
    "nevada": Nevada,
    "normandy": Normandy,
    "thechannel": TheChannel,
    "sinai": Sinai,
    "falklands": Falklands,
    "marianaislands": MarianaIslands,
}


def terrain_for(slug: str) -> Terrain:
    factory = _REGISTRY.get(slug)
    if factory is None:
        raise ValueError(f"unknown theater slug: {slug!r}. known: {sorted(_REGISTRY)}")
    return factory()


def known_theaters() -> list[str]:
    return sorted(_REGISTRY)
