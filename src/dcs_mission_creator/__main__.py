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
    dcs-mission-creator audit [<name>]          # build without saving, report
    dcs-mission-creator route <theater> --via LAT,LNG --via LAT,LNG [...]
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
from collections.abc import Callable, Collection
from pathlib import Path

import argcomplete
import structlog
from dcs.mapping import LatLng, Point
from dcs.terrain.terrain import Terrain

from dcs_mission_creator import missions
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.log import configure as configure_logging
from dcs_mission_creator.core.mission_builder import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    MissionBuilder,
)
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


def route_plan_defaults() -> tuple[float, float]:
    """The planner's grid step and shortest leg, for the parser's help text.

    Imported lazily like the other heavy commands: `core/route_plan` pulls in
    numpy and the overlay stack, and `dcs-mission-creator list` should not pay
    for that.
    """
    from dcs_mission_creator.core import route_plan

    return route_plan.SEARCH_CELL_M, route_plan.MIN_LEG_M


def _cmd_audit(
    missions_map: dict[str, type[MissionBuilder]],
    name: str | None,
    players: int,
) -> int:
    """Build one mission — or every one — without saving, and print the findings.

    Exits non-zero when anything came back at `error`, so it is usable as a gate;
    warnings and notes are for reading. Building rather than saving is the whole
    point: this is the same mission `generate` would write, minus the minute of
    text-to-speech, archive and kneeboard rendering that says nothing about
    whether a waypoint is inside a mountain.
    """
    from dcs_mission_creator.core.audit import audit, report

    slugs = [name] if name else sorted(missions_map)
    errors = 0
    for slug in slugs:
        cls = missions_map.get(slug)
        if cls is None:
            available = ", ".join(sorted(missions_map)) or "(none)"
            log.error("unknown mission", name=slug, available=available)
            return 2
        findings = audit(cls(players=players))
        errors += sum(1 for f in findings if f.severity == "error")
        print(f"\n=== {slug} ({len(findings)} finding(s))")
        print(report(findings))
    return 1 if errors else 0


def _cmd_survey(
    theater: str,
    points: list[str],
    sites: list[str],
    defends: list[str],
    agl_m: float,
    difficulty: str,
) -> int:
    """Check a layout before anything is written around it.

    The complement to `route`: that one asks whether an aeroplane fits down a
    line, this one asks whether the things the line runs between are in legal
    places. Every briefed point against every emplaced system — how far, how
    much margin against what the system really reaches, what the same margin
    will look like once the F10 plan has coarsened it, and whether the terrain
    between them hides one from the other.

    Exits non-zero when a point sits inside an envelope the plan was meant to
    stay out of, which is the finding worth a gate. `--defends` names the
    systems that are *for* the objective — its point defence, or an area system
    the whole sortie is flown inside — and those stay on the table but off the
    findings list, because being in them is the mission. Nothing else is
    excused, a ring over the target least of all.
    """
    from dcs_mission_creator.core import survey
    from dcs_mission_creator.map_overlay.query import MapOverlay
    from dcs_mission_creator.map_overlay.terrains import terrain_for

    terrain = terrain_for(theater)
    overlay = MapOverlay.load(theater)
    if not points:
        log.error("survey needs at least one --point")
        return 2

    named: dict[str, Point] = {}
    for spec in points:
        position, label = _labelled(terrain, spec)
        named[label] = position
    layout = [_site(terrain, spec, defends=set(defends)) for spec in sites]
    unknown = set(defends) - {site.label for site in layout}
    if unknown:
        log.error("--defends names no --site", unknown=sorted(unknown))
        return 2

    rows = survey.reaches(overlay, named, layout, agl_m=agl_m)
    print(survey.report(rows, difficulty=difficulty))
    for label, position in named.items():
        print(f"\n{label:14s} {survey.describe(overlay, position).row()}")
    inside = survey.covered(rows)
    if inside:
        print()
        for row in inside:
            log.error(
                "briefed point inside an envelope",
                point=row.point,
                site=row.site.label,
                inside_km=round(-(row.margin_m or 0.0) / 1000, 1),
            )
        return 1
    return 0


def _site(terrain: Terrain, spec: str, *, defends: Collection[str] = ()):
    """`"SA-3 Tartus@34.90,35.92:18000"` → a `survey.Site`.

    The radius is what the system **reaches**, not the ring the map will draw —
    that is the distinction the whole check turns on.
    """
    from dcs_mission_creator.core import survey

    head, _, radius = spec.rpartition(":")
    if not head:
        head, radius = spec, "0"
    position, label = _labelled(terrain, head)
    return survey.Site(
        label, position, float(radius or 0.0), defends_objective=label in defends
    )


def _cmd_route(
    theater: str,
    via: list[str],
    agl: str,
    threats: list[str],
    names: str | None,
    speed: float,
    cell_m: float,
    min_leg_m: float,
) -> int:
    """Plan a low corridor through `via`, and say what it costs.

    Prints three things, which are the three questions a mountain route has to
    answer before a line of the mission is written: the waypoints and heights it
    actually needs, which threats can see it and from where, and how much of the
    navigation tab is left for the F10 plan afterwards.
    """
    from dcs_mission_creator.core import route_plan
    from dcs_mission_creator.map_overlay.query import MapOverlay
    from dcs_mission_creator.map_overlay.terrains import terrain_for

    terrain = terrain_for(theater)
    overlay = MapOverlay.load(theater)
    anchors = [_degrees(terrain, point) for point in via]
    if len(anchors) < 2:
        log.error("route needs at least two --via points")
        return 2

    route = route_plan.plan_corridor(
        overlay,
        anchors,
        agl_for=route_plan.agl_bands(_agl_bands(agl)),
        cell_m=cell_m,
        min_leg_m=min_leg_m,
    )
    labels = [n.strip() for n in names.split(",")] if names else []
    print(route.table(labels, speed_kph=speed))
    print(
        f"\n{len(route.points)} waypoints, {route.length_m / 1000:.0f} km, "
        f"worst lift {route.worst_lift_m:.0f} m"
    )
    if threats:
        print()
        sites = [_labelled(terrain, spec) for spec in threats]
        for look in route_plan.sighting(
            overlay, route.points, sites, altitudes_m=route.altitude_m
        ):
            print(f"  {look.summary()}")
    spare = route_plan.nav_headroom(len(route.points))
    print(f"\ncartridge: {spare} navigation steerpoint(s) left for the F10 plan")
    return 0


def _degrees(terrain: Terrain, spec: str) -> Point:
    """`"LAT,LNG"` → a world point on `terrain`."""
    lat, lng = (float(part) for part in spec.split(",", 1))
    return Point.from_latlng(LatLng(lat, lng), terrain)


def _labelled(terrain: Terrain, spec: str) -> tuple[Point, str]:
    """`"SA-11@43.98,41.88"` → the point and what to call it."""
    label, _, coords = spec.rpartition("@")
    return _degrees(terrain, coords), label or coords


def _agl_bands(spec: str) -> list[tuple[float, float]]:
    """`"0:300,800:600"` → `[(0, 300), (800, 600)]` — ground floor to height flown."""
    bands = []
    for pair in spec.split(","):
        floor, _, agl = pair.partition(":")
        bands.append((float(floor), float(agl)))
    return bands


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

    from dcs_mission_creator.map_overlay.terrains import known_theaters

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
        default=MIN_PLAYERS,
        choices=range(MIN_PLAYERS, MAX_PLAYERS + 1),
        help=(
            "Number of coop client slots in the player flight "
            f"(default: {MIN_PLAYERS}). The flight splits its loadout across "
            "them, so slot 1 and slot 2 do not carry the same jet."
        ),
    )

    aud = sub.add_parser(
        "audit",
        help=(
            "Build a mission without saving it and report what looks wrong "
            "(all missions if no name is given)."
        ),
    )
    aud.add_argument(
        "name",
        nargs="?",
        default=None,
        choices=sorted(missions_map) or None,
        help="Mission slug. Omit to audit every mission.",
    )
    aud.add_argument(
        "--players",
        type=int,
        default=MIN_PLAYERS,
        choices=range(MIN_PLAYERS, MAX_PLAYERS + 1),
        help=f"Coop client slots to build for (default: {MIN_PLAYERS}).",
    )

    srv = sub.add_parser(
        "survey",
        help="Check a layout: distances, envelope margins and line of sight.",
    )
    srv.add_argument("theater", choices=known_theaters())
    srv.add_argument(
        "--point",
        action="append",
        default=[],
        metavar="LABEL@LAT,LNG",
        help="A briefed position — target, crossing, station. Repeatable.",
    )
    srv.add_argument(
        "--site",
        action="append",
        default=[],
        metavar="LABEL@LAT,LNG:REACH_M",
        help=(
            "An emplaced system and what it really reaches — not the ring the "
            "map will draw. Omit the reach for something that cannot shoot. "
            "Repeatable."
        ),
    )
    srv.add_argument(
        "--defends",
        action="append",
        default=[],
        metavar="SITE_LABEL",
        help=(
            "A --site that is *for* the objective: its point defence, or an "
            "area system the whole sortie is flown inside. Reported, but not a "
            "finding. Repeatable."
        ),
    )
    srv.add_argument(
        "--agl",
        type=float,
        default=150.0,
        metavar="M",
        help="Height above the ground to test line of sight at (default: %(default)s).",
    )
    srv.add_argument(
        "--difficulty",
        default="trained",
        choices=[d.value for d in Difficulty],
        help="Whose reveal to report the drawn margin for (default: %(default)s).",
    )

    rte = sub.add_parser(
        "route",
        help="Plan a terrain-clearing low corridor and report what can see it.",
    )
    rte.add_argument("theater", choices=known_theaters())
    rte.add_argument(
        "--via",
        action="append",
        default=[],
        metavar="LAT,LNG",
        help=(
            "A place the corridor has to pass, in order. Give at least two; "
            "the valley search fills in between them."
        ),
    )
    rte.add_argument(
        "--agl",
        default="0:300,800:600,2000:900",
        metavar="GROUND:HEIGHT,...",
        help=(
            "Height above the ground to fly, by the elevation under the point "
            "(default: %(default)s). Raising the mountain bands is what buys "
            "back waypoints on a route that will not fit."
        ),
    )
    rte.add_argument(
        "--threat",
        action="append",
        default=[],
        metavar="LABEL@LAT,LNG",
        help="A site to test line of sight from. Repeatable.",
    )
    rte.add_argument(
        "--names",
        default=None,
        metavar="N,N,...",
        help=(
            "Names for the emitted table, in output order. How many waypoints "
            "the terrain needs is the answer, not the question — run once "
            "without this, then name what came back."
        ),
    )
    rte.add_argument(
        "--speed",
        type=float,
        default=0.0,
        metavar="KPH",
        help="Add a commanded true airspeed column to the emitted table.",
    )
    rte.add_argument(
        "--cell",
        type=float,
        default=route_plan_defaults()[0],
        metavar="M",
        help="Valley-search grid step in metres (default: %(default)s).",
    )
    rte.add_argument(
        "--min-leg",
        type=float,
        default=route_plan_defaults()[1],
        metavar="M",
        help=(
            "Shortest leg the planner may create (default: %(default)s). "
            "Larger trades terrain clearance for fewer waypoints."
        ),
    )

    _add_overlay_subcommand(sub)

    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)

    if args.command == "list":
        return _cmd_list(missions_map)
    if args.command == "generate":
        return _cmd_generate(missions_map, args.name, args.output_dir, args.players)
    if args.command == "audit":
        return _cmd_audit(missions_map, args.name, args.players)
    if args.command == "survey":
        return _cmd_survey(
            args.theater,
            args.point,
            args.site,
            args.defends,
            args.agl,
            args.difficulty,
        )
    if args.command == "route":
        return _cmd_route(
            args.theater,
            args.via,
            args.agl,
            args.threat,
            args.names,
            args.speed,
            args.cell,
            args.min_leg,
        )
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
