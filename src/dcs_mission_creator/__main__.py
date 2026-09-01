# PYTHON_ARGCOMPLETE_OK
"""CLI entry-point: discover and run `MissionBuilder` subclasses by name.

Each module under `dcs_mission_creator.missions` that defines a concrete
subclass of `MissionBuilder` (with a `name` slug) is exposed as a subcommand
of the same name. The CLI instantiates the class with the requested player
count and calls `.generate(output_dir)`, which writes both the `.miz` and a
`README.md` describing the mission.

Usage:
    dcs-mission-creator list
    dcs-mission-creator generate coastal_cover [--output-dir DIR] [--players N]
    dcs-mission-creator generate                # every discovered mission
    dcs-mission-creator map-overlay build <theater> [--layers L,L,...]
    dcs-mission-creator map-overlay inspect <theater> [--layers L] [--out FILE]
    dcs-mission-creator map-overlay query <theater> --point X,Z --layer L

Shell completion (argcomplete):
    # one-shot, current shell
    eval "$(register-python-argcomplete dcs-mission-creator)"
    # or enable global completion once:
    activate-global-python-argcomplete --user
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import pkgutil
from collections.abc import Callable
from pathlib import Path

import argcomplete
import structlog

from dcs_mission_creator import missions
from dcs_mission_creator.core.log import configure as configure_logging
from dcs_mission_creator.core.mission_builder import MAX_PLAYERS, MissionBuilder
from dcs_mission_creator.map_overlay.layers import BuildLayer, QueryLayer, RenderLayer

_MISSIONS_ENV = "DCS_MISSIONS_FOLDER"
_GENERATED_SUBDIR = "IAGeneratedMissions"

log = structlog.get_logger(__name__)


def _discover() -> dict[str, type[MissionBuilder]]:
    """Return {slug: MissionBuilder subclass} for every public missions module."""
    found: dict[str, type[MissionBuilder]] = {}
    for info in pkgutil.iter_modules(missions.__path__):
        if info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{missions.__name__}.{info.name}")
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and obj is not MissionBuilder
                and issubclass(obj, MissionBuilder)
                and obj.__module__ == module.__name__
            ):
                slug = getattr(obj, "name", None)
                if isinstance(slug, str) and slug:
                    found[slug] = obj
                    break
    return found


def _cmd_list(missions_map: dict[str, type[MissionBuilder]]) -> int:
    if not missions_map:
        log.info("no missions found")
        return 0
    for slug in sorted(missions_map):
        title = getattr(missions_map[slug], "title", slug)
        log.info("mission", slug=slug, title=title)
    return 0


def _default_output_dir(name: str) -> Path:
    """Default output folder: $DCS_MISSIONS_FOLDER/IAGeneratedMissions/<name>/."""
    root = os.environ.get(_MISSIONS_ENV)
    if not root:
        raise SystemExit(
            f"{_MISSIONS_ENV} is not set; either export it or pass --output-dir."
        )
    return Path(root) / _GENERATED_SUBDIR / name


def _generate_one(
    slug: str,
    cls: type[MissionBuilder],
    target: Path,
    players: int,
) -> None:
    miz, readme = cls(players=players).generate(target)
    log.info("wrote", mission=slug, path=str(miz))
    log.info("wrote", mission=slug, path=str(readme))


def _cmd_generate(
    missions_map: dict[str, type[MissionBuilder]],
    name: str | None,
    output_dir: Path | None,
    players: int,
) -> int:
    """Generate one mission by slug, or every discovered mission when `name` is None."""
    if name is not None:
        cls = missions_map.get(name)
        if cls is None:
            available = ", ".join(sorted(missions_map)) or "(none)"
            log.error("unknown mission", name=name, available=available)
            return 2
        _generate_one(name, cls, output_dir or _default_output_dir(name), players)
        return 0

    if not missions_map:
        log.error("no missions found")
        return 2

    # With no slug, `--output-dir` is the parent that receives one folder per mission.
    failed: list[str] = []
    for slug in sorted(missions_map):
        target = output_dir / slug if output_dir else _default_output_dir(slug)
        try:
            _generate_one(slug, missions_map[slug], target, players)
        except Exception:
            log.exception("failed to generate mission", mission=slug)
            failed.append(slug)
    if failed:
        log.error("some missions failed", missions=", ".join(failed))
        return 1
    return 0


def _cmd_overlay_build(theater: str, layers: list[BuildLayer]) -> int:
    from dcs_mission_creator.map_overlay import (
        builder_elevation,
        builder_forest,
        builder_osm,
    )
    from dcs_mission_creator.map_overlay.terrains import terrain_for

    selected = BuildLayer.expand(set(layers) if layers else {BuildLayer.ALL})
    terrain = terrain_for(theater)
    if BuildLayer.ELEVATION in selected:
        builder_elevation.build(terrain, theater)
    if BuildLayer.OSM in selected:
        builder_osm.build(terrain, theater)
    if BuildLayer.FOREST in selected:
        builder_forest.build(terrain, theater)
    if BuildLayer.PLACES in selected:
        # Cheap and independent of the rasters — safe to run on an overlay that
        # is already built, which is how the existing two got their sidecar.
        builder_osm.build_places(terrain, theater)
    return 0


def _cmd_overlay_inspect(theater: str, layers: list[RenderLayer], out: Path) -> int:
    from dcs_mission_creator.map_overlay import viz

    selected = RenderLayer.expand(set(layers) if layers else {RenderLayer.ALL})
    viz.render(theater, [m.value for m in sorted(selected)], out)
    return 0


def _cmd_overlay_query(theater: str, point_arg: str, layer: QueryLayer) -> int:
    from dcs.mapping import Point

    from dcs_mission_creator.map_overlay import MapOverlay
    from dcs_mission_creator.map_overlay.terrains import terrain_for

    try:
        x_str, z_str = point_arg.split(",", 1)
        x, z = float(x_str), float(z_str)
    except ValueError as err:
        raise SystemExit(f"--point must be X,Z (got {point_arg!r}): {err}") from err
    terrain = terrain_for(theater)
    pt = Point(x, z, terrain)
    ov = MapOverlay.load(theater)
    queries: dict[QueryLayer, Callable[[Point], object]] = {
        QueryLayer.ELEVATION: lambda p: ov.elevation_at(p),
        QueryLayer.SLOPE: lambda p: ov.slope_at(p),
        QueryLayer.VEGETATION: lambda p: ov.vegetation_at(p).name,
        QueryLayer.ROAD_DISTANCE: lambda p: ov.distance_to_road_m(p),
        QueryLayer.RIVER_DISTANCE: lambda p: ov.distance_to_river_m(p),
        QueryLayer.BUILT_UP: lambda p: ov.is_built_up(p),
        QueryLayer.FOREST_EDGE: lambda p: ov.distance_to_forest_edge_m(p),
        QueryLayer.PROMINENCE: lambda p: ov.local_prominence_m(p),
    }
    log.info("query", layer=layer.value, point=(x, z), result=queries[layer](pt))
    return 0


def _comma_list(enum_cls: type[BuildLayer | RenderLayer]):
    """argparse `type=` callable: parse "a,b,c" into a list of enum members."""

    def parse(raw: str) -> list:
        items = [s for s in raw.split(",") if s]
        try:
            return [enum_cls(s) for s in items]
        except ValueError as err:
            choices = ", ".join(m.value for m in enum_cls)
            raise argparse.ArgumentTypeError(f"{err}. choose from: {choices}") from err

    return parse


def _add_overlay_subcommand(sub: argparse._SubParsersAction) -> None:
    from dcs_mission_creator.map_overlay.terrains import known_theaters

    overlay = sub.add_parser(
        "map-overlay",
        help="Build/inspect/query the per-theater spatial overlay.",
    )
    osub = overlay.add_subparsers(dest="overlay_command", required=True)

    build_choices = ",".join(m.value for m in BuildLayer)
    build = osub.add_parser("build", help="Build overlay layers for a theater.")
    build.add_argument("theater", choices=known_theaters())
    build.add_argument(
        "--layers",
        type=_comma_list(BuildLayer),
        default=[BuildLayer.ALL],
        help=f"Comma-separated subset of {build_choices} (default: all).",
    )

    render_choices = ",".join(m.value for m in RenderLayer)
    inspect = osub.add_parser(
        "inspect",
        help="Render layers as a PNG for visual review.",
    )
    inspect.add_argument("theater", choices=known_theaters())
    inspect.add_argument(
        "--layers",
        type=_comma_list(RenderLayer),
        default=[RenderLayer.ALL],
        help=f"Comma-separated subset of {render_choices} (default: all).",
    )
    inspect.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/overlay.png"),
        help="Output PNG path (default: %(default)s).",
    )

    query = osub.add_parser(
        "query",
        help="Query a single point against one layer.",
    )
    query.add_argument("theater", choices=known_theaters())
    query.add_argument(
        "--point",
        required=True,
        help="DCS world coords as X,Z (meters).",
    )
    query.add_argument(
        "--layer",
        required=True,
        type=QueryLayer,
        choices=list(QueryLayer),
        help="Which layer to query at the given point.",
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    missions_map = _discover()

    parser = argparse.ArgumentParser(
        prog="dcs-mission-creator",
        description="Generate DCS missions defined under dcs_mission_creator.missions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List available missions.")

    gen = sub.add_parser(
        "generate",
        help="Generate a mission by name (all missions if no name is given).",
    )
    gen.add_argument(
        "name",
        nargs="?",
        default=None,
        choices=sorted(missions_map) or None,
        help="Mission slug (e.g. coastal_cover). Omit to generate every mission.",
    )
    out_arg = gen.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            f"Output directory for the .miz and README.md "
            f"(default: ${_MISSIONS_ENV}/{_GENERATED_SUBDIR}/<name>/). "
            f"With no mission name, it is the parent that receives one "
            f"<name>/ folder per mission."
        ),
    )
    out_arg.completer = argcomplete.completers.DirectoriesCompleter()  # ty: ignore[unresolved-attribute]
    gen.add_argument(
        "--players",
        type=int,
        default=1,
        choices=range(1, MAX_PLAYERS + 1),
        help="Number of coop client slots in the player flight (default: 1).",
    )

    _add_overlay_subcommand(sub)

    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)

    if args.command == "list":
        return _cmd_list(missions_map)
    if args.command == "generate":
        return _cmd_generate(missions_map, args.name, args.output_dir, args.players)
    if args.command == "map-overlay":
        if args.overlay_command == "build":
            return _cmd_overlay_build(args.theater, args.layers)
        if args.overlay_command == "inspect":
            return _cmd_overlay_inspect(args.theater, args.layers, args.out)
        if args.overlay_command == "query":
            return _cmd_overlay_query(args.theater, args.point, args.layer)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
