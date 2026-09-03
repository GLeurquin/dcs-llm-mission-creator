"""The four documents, checked against the code they describe.

Nothing in CI read a document until this file existed, and every one of them had
drifted: two mission catalogues frozen at six entries in a repo of eight, a
`--difficulty` flag `generate` has never had, a `python3.12` grep recipe against
a 3.14 venv, `core/kneeboard.py` for a package. None of that is catchable by
reading — it is only catchable by asking the code.

The checks are deliberately about *facts a doc asserts*, never about prose. A
document may say whatever it likes; it may not name a file that is not there, a
flag that does not parse, or a count that a directory listing contradicts. Where
a fact has one true home the test points at that home rather than at a literal —
the mission catalogue is checked against `missions/`, the slot range against
`MissionBuilder`.

Fast by construction: no DCS install, no map overlay, no mission is built.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import re
from pathlib import Path

import pytest

from dcs_mission_creator.core import mission_builder

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".claude" / "skills" / "dcs-mission"

#: The four documents, in the order a reader meets them. `README.md` is here
#: because it went stale by itself, on the same fact as `CLAUDE.md` did.
DOCS = {
    "README.md": ROOT / "README.md",
    "CLAUDE.md": ROOT / "CLAUDE.md",
    "SKILL.md": SKILL_DIR / "SKILL.md",
    "PYDCS_REFERENCE.md": SKILL_DIR / "PYDCS_REFERENCE.md",
}

DOC_IDS = sorted(DOCS)


def _text(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


# -- paths ------------------------------------------------------------------

#: A repo-relative path as the docs write them, in prose or in a markdown link.
#: Anchored on the two directories the docs actually point into, so a bare word
#: like `tests/` or an example path inside a code block is not mistaken for one.
_PATH_RE = re.compile(r"(?<![\w/.])((?:src/dcs_mission_creator|tests)/[\w./-]*[\w/])")


@pytest.mark.parametrize("doc", DOC_IDS)
def test_referenced_paths_exist(doc: str) -> None:
    """Every `src/` or `tests/` path a document names is on disk."""
    missing = sorted(
        {p for p in _PATH_RE.findall(_text(doc)) if not (ROOT / p).exists()}
    )
    assert not missing, f"{doc} names paths that do not exist: {missing}"


@pytest.mark.parametrize("doc", DOC_IDS)
def test_module_paths_are_not_packages(doc: str) -> None:
    """A path written `<name>.py` is a module, not a package.

    `core/kneeboard.py` was a module and became a package of nine, and the
    reference in `CLAUDE.md` outlived the move. The distinction matters to a
    reader who is about to open the file.
    """
    wrong = sorted(
        {
            p
            for p in _PATH_RE.findall(_text(doc))
            if p.endswith(".py") and (ROOT / p).is_dir()
        }
    )
    assert not wrong, f"{doc} calls a package a module: {wrong}"


# -- the mission catalogue --------------------------------------------------


def _mission_slugs() -> set[str]:
    directory = ROOT / "src" / "dcs_mission_creator" / "missions"
    return {p.stem for p in directory.glob("*.py") if not p.stem.startswith("_")}


def test_readme_catalogue_lists_every_mission() -> None:
    """`README.md` holds the one mission list in the repo, so it holds all of them.

    There used to be two — this one and a 157-line section of `CLAUDE.md` — and
    both were missing the same two missions, because a list nothing checks is a
    list nobody rereads. Having one is what makes this test possible; this test
    is what makes having one safe.
    """
    listed = set(re.findall(r"missions/(\w+)\.py\)", _text("README.md")))
    assert listed == _mission_slugs()


@pytest.mark.parametrize("doc", DOC_IDS)
def test_no_hard_coded_mission_count(doc: str) -> None:
    """No document states how many missions there are; the directory does that."""
    found = re.findall(
        r"\b(?:all|the|those|these)\s+"
        r"(?:two|three|four|five|six|seven|eight|nine|ten)\s+missions\b",
        _text(doc),
        re.IGNORECASE,
    )
    assert not found, f"{doc} hard-codes a mission count: {found}"


# -- values that have exactly one true home ---------------------------------


@pytest.mark.parametrize("doc", DOC_IDS)
def test_slot_range_matches_the_base_class(doc: str) -> None:
    """A stated coop-slot range is `MissionBuilder`'s, not a remembered one.

    `CLAUDE.md` said 1–6 and contradicted itself two lines later; `SKILL.md`
    said 1–4 and contradicted itself twice.
    """
    expected = f"{mission_builder.MIN_PLAYERS}–{mission_builder.MAX_PLAYERS}"
    for match in re.finditer(
        r"(\d)–(\d)\s+(?:coop\s+)?(?:client\s+)?slots", _text(doc)
    ):
        assert match.group(0).startswith(expected), (
            f"{doc}: {match.group(0)!r} does not match "
            f"MIN_PLAYERS..MAX_PLAYERS ({expected})"
        )


@pytest.mark.parametrize("doc", DOC_IDS)
def test_no_pinned_interpreter_path(doc: str) -> None:
    """No document pins `.venv/lib/python3.<n>/`.

    `PYDCS_REFERENCE.md` told the agent to run a grep against `python3.12` on a
    3.14 venv — an instruction that fails rather than misleads, which is worse:
    the agent then guesses the API the doc exists to stop it guessing.
    """
    found = re.findall(r"python3\.\d+", _text(doc))
    assert not found, f"{doc} pins an interpreter version: {sorted(set(found))}"


# -- the CLI ----------------------------------------------------------------


def _cli_flags() -> dict[str, set[str]]:
    """Every option string each subcommand's parser accepts, by subcommand.

    Read off the built parser rather than the source, so this cannot drift from
    what `argparse` will actually take.
    """
    from dcs_mission_creator.__main__ import _discover, build_parser

    flags: dict[str, set[str]] = {}
    for action in build_parser(_discover())._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            flags[name] = {option for a in sub._actions for option in a.option_strings}
    return flags


#: The commands a document is allowed to quote a flag for. `map-overlay` is
#: nested and its flags are checked through its own subparsers, not here.
_CHECKED_COMMANDS = ("generate", "audit", "survey", "route", "list")


@pytest.mark.parametrize("doc", DOC_IDS)
def test_quoted_cli_flags_exist(doc: str) -> None:
    """Every `--flag` shown on a `dcs-mission-creator <cmd>` line is a real one.

    `SKILL.md` advertised `--difficulty`, `--airframe` and `--length-minutes`,
    none of which has ever existed: theatre, airframe, difficulty and length are
    edited in the mission module. An agent following that line gets an argparse
    error, having already written the mission.
    """
    flags = _cli_flags()
    bad: list[str] = []
    for line in _text(doc).splitlines():
        if "dcs-mission-creator" not in line:
            continue
        command = next(
            (c for c in _CHECKED_COMMANDS if re.search(rf"\b{c}\b", line)), None
        )
        if command is None:
            continue
        known = flags[command] | {"--help", "-h"}
        bad += [
            f"{command} {f}" for f in re.findall(r"--[\w-]+", line) if f not in known
        ]
    assert not bad, f"{doc} quotes flags that do not exist: {sorted(set(bad))}"


# -- symbols ----------------------------------------------------------------

#: `from dcs_mission_creator.<pkg> import a, b` in a fenced code block. The
#: `import x as y` form is excluded on purpose: what it imports is the module,
#: which the path check above already covers, and the alias says nothing.
_IMPORT_RE = re.compile(
    r"^from\s+(dcs_mission_creator[\w.]*)\s+import\s+"
    r"([A-Za-z_][\w,\s]*?)\s*$",
    re.MULTILINE,
)


@pytest.mark.parametrize("doc", DOC_IDS)
def test_documented_imports_resolve(doc: str) -> None:
    """Every import a document shows is one an agent can paste and run.

    These are the lines most likely to be copied verbatim, so a renamed helper
    costs an agent a round trip rather than a raised eyebrow.
    """
    broken: list[str] = []
    for module_name, names in _IMPORT_RE.findall(_text(doc)):
        if " as " in names:
            continue
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            broken.append(module_name)
            continue
        for name in (n.strip() for n in names.split(",")):
            if not name or hasattr(module, name):
                continue
            # `from …core import survey` imports a submodule, which is not an
            # attribute of the package until something imports it.
            try:
                importlib.import_module(f"{module_name}.{name}")
            except ImportError:
                broken.append(f"{module_name}.{name}")
    assert not broken, f"{doc} imports symbols that do not exist: {sorted(set(broken))}"


@pytest.mark.parametrize("doc", DOC_IDS)
def test_documented_core_attributes_resolve(doc: str) -> None:
    """A `module.CONSTANT` or `module.function` named in prose still exists.

    Restricted to the `core/` modules a document actually imports, and to
    dotted names in backticks, because that is the shape the docs use for a
    contract: `dtc.MAX_NAV_POINTS`, `waypoints.clear_terrain`, `sanc.HAWK`.
    """
    text = _text(doc)
    #: The docs alias two modules on import; everything else is its own name.
    aliases = {"sanc": "sanctuary", "ad": "air_defense", "recon": "recon.publish"}
    core = ROOT / "src" / "dcs_mission_creator" / "core"
    modules = {p.stem for p in core.glob("*.py") if not p.stem.startswith("_")}

    broken: list[str] = []
    for match in re.finditer(r"`(\w+)\.([A-Za-z_]\w*)`", text):
        alias, attribute = match.groups()
        if attribute in ("py", "lua", "md", "json"):  # a filename, not an attribute
            continue
        name = aliases.get(alias, alias)
        if name not in modules:
            continue
        module = importlib.import_module(f"dcs_mission_creator.core.{name}")
        if not hasattr(module, attribute):
            broken.append(f"{alias}.{attribute}")
    assert not broken, (
        f"{doc} names core attributes that do not exist: {sorted(set(broken))}"
    )


def test_the_parser_is_importable_without_building_anything() -> None:
    """The checks above are only fast if importing the CLI stays cheap.

    `route_plan_defaults` was called while *building* the parser, so every
    invocation — `list`, `--help`, this test — paid for numpy and the overlay
    stack that its own docstring said it was avoiding.
    """
    source = (ROOT / "src" / "dcs_mission_creator" / "__main__.py").read_text()
    tree = ast.parse(source)
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_parser"
    )
    called = {
        node.func.id
        for node in ast.walk(builder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "route_plan_defaults" not in called, (
        "build_parser calls route_plan_defaults, which imports numpy and the "
        "overlay stack on every invocation including `list` and `--help`"
    )
