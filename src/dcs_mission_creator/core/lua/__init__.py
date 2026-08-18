"""Mission-script Lua, kept in `.lua` files instead of Python string literals.

Anything that ends up inside a `DoScript` action lives here as a real `.lua`
file so editors, linters and diffs treat it as Lua. Python loads it with
`render`, which substitutes `__TOKEN__` placeholders and refuses to emit a
script that still carries an unfilled one:

```python
from dcs_mission_creator.core import lua

script = lua.render("iads.lua", SITES=rows, SIDE="coalition.side.BLUE")
rule.add_action(lua.InlineDoScript(script))
```

Substitution is deliberately dumb (literal `__NAME__` → text): the values are
already-formatted Lua source built on the Python side, so nothing here quotes
or escapes for you — build string literals with `quote`.

Use `InlineDoScript`, never pydcs's `action.DoScript` — see its docstring. The
one exception is a script too large to sensibly inline: the vendored IADS
framework under `vendor/` goes in with `action.DoScriptFile` and a path from
`file_path`, which is a different predicate and is not affected by the l10n bug.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Optional

from dcs import action

_PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")


@lru_cache(maxsize=None)
def source(name: str) -> str:
    """Return the raw text of `name` (e.g. `"iads.lua"`) from this package.

    Sub-paths work, which is how the vendored third-party scripts under
    `vendor/` are reached (`source("vendor/skynet-iads.lua")`).
    """
    if not name.endswith(".lua"):
        raise ValueError(f"lua script name must end in .lua, got {name!r}")
    return files(__name__).joinpath(name).read_text(encoding="utf-8")


# Materialised copies of packaged scripts, for the zipped-wheel case below. Kept
# alive for the life of the process because `Mission.map_resource` stores a path
# and only reads it at `save()` time.
_extracted: Optional[tempfile.TemporaryDirectory] = None


@lru_cache(maxsize=None)
def file_path(name: str) -> Path:
    """Return `name` as a real file on disk.

    For scripts too large to inline sensibly — the vendored IADS framework is
    117 KB — `Mission.map_resource.add_resource_file` takes a filesystem path,
    stores it, and reads it back when the `.miz` is written. Installed from
    source that is just the packaged file; installed from a zipped wheel there is
    no such file, so it is extracted once to a temporary directory that lives as
    long as the process (the read happens at save time, not here).
    """
    if not name.endswith(".lua"):
        raise ValueError(f"lua script name must end in .lua, got {name!r}")
    resource = files(__name__).joinpath(name)
    if isinstance(resource, Path) and resource.is_file():
        return resource
    global _extracted
    if _extracted is None:
        _extracted = tempfile.TemporaryDirectory(prefix="dcs-mission-creator-lua-")
    target = Path(_extracted.name) / Path(name).name
    if not target.is_file():
        with resource.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


def quote(text: Optional[str]) -> str:
    """Return `text` as a Lua string literal, or the literal `nil` for `None`.

    The `None` case is what makes this worth sharing: every table row rendered
    into a script has optional fields (a radio call nobody wrote, a laser code
    the target has not got), and they have to reach Lua as `nil` rather than as
    an empty string a truth test would still accept.
    """
    if text is None:
        return "nil"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render(name: str, **substitutions: str) -> str:
    """Load `name` and replace each `__KEY__` placeholder with its value.

    Raises `KeyError` if a substitution names a placeholder the script does not
    have, or if the script still holds a `__PLACEHOLDER__` once every given
    substitution has been applied — a silent leftover would reach DCS as a Lua
    syntax error at mission start.
    """
    text = source(name)
    for key, value in substitutions.items():
        token = f"__{key}__"
        if token not in text:
            raise KeyError(f"{name} has no placeholder {token}")
        text = text.replace(token, value)
    leftover = sorted(set(_PLACEHOLDER.findall(text)))
    if leftover:
        raise KeyError(f"{name} left unsubstituted: {', '.join(leftover)}")
    return text


class InlineDoScript(action.Action):
    """`a_do_script` carrying the Lua inline, the way the Mission Editor writes it.

    pydcs's `action.DoScript` is a `TextAction`: it parks the script in the
    l10n dictionary and emits
    `a_do_script(getValueDictByKey("DictKey_Translation_N"))`. DCS does **not**
    resolve dictionary keys inside the scripting sandbox — `getValueDictByKey`
    hands the key straight back, so the game compiles the string
    `DictKey_Translation_N` as Lua and every such trigger dies at mission start
    with `[string "DictKey_Translation_N"]:1: '=' expected near '<eof>'`.

    Stock ED missions store the source in the action's own `text` field with no
    `KeyDict_text` alongside; this does the same. `dcs.lua.serialize.dumps`
    escapes quotes and turns each newline into a Lua line-continuation, so a
    multi-line script survives the (double) escaping verbatim.
    """

    predicate = "a_do_script"

    def __init__(self, script: str) -> None:
        super().__init__(InlineDoScript.predicate)
        self.script = script
        self.params.append(script)

    def dict(self) -> Dict[Any, Any]:
        d = super().dict()
        d["text"] = self.script
        return d
