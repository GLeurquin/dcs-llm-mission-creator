"""The `python -m dcs_mission_creator.missions.<slug>` entry point.

Every mission module stayed runnable on its own, which meant every one carried
the same twenty-five lines of argparse. Worse, each spelled its own output
default as a literal `out/<slug>` beside a `name = "<slug>"` class attribute,
so the two could drift apart.

Missions now call `run_cli(TheBuilder)` and the slug comes from the class.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import structlog

from dcs_mission_creator.core.log import configure as configure_logging
from dcs_mission_creator.core.mission_builder import MAX_PLAYERS

if TYPE_CHECKING:
    from dcs_mission_creator.core.mission_builder import MissionBuilder

log = structlog.get_logger(__name__)


def run_cli(
    builder: type[MissionBuilder], argv: Sequence[str] | None = None
) -> tuple[Path, Path]:
    """Parse args for a single mission, generate it, and report what was written.

    The unified `dcs-mission-creator generate` CLI is the usual way in; this is
    the per-module fallback, and it reports through structlog so both paths log
    the same way rather than one of them using bare `print`.
    """
    configure_logging()
    parser = argparse.ArgumentParser(
        description=f"Generate the '{builder.title}' DCS mission."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out") / builder.name,
        help="Output directory for the .miz and README.md (default: %(default)s)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=1,
        choices=range(1, MAX_PLAYERS + 1),
        help="Number of coop client slots in the player flight (default: 1).",
    )
    args = parser.parse_args(argv)

    miz, readme = builder(players=args.players).generate(args.output_dir)
    log.info("wrote", mission=builder.name, path=str(miz))
    log.info("wrote", mission=builder.name, path=str(readme))
    return miz, readme
