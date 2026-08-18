"""Mission-file unit keys pydcs does not model (project-owned patch).

`Unit.dict()` builds the unit table from a fixed list of fields, and two keys
the Mission Editor writes are not among them: `datalinks` (the per-unit network
table — `core/datalink.py`) and `DTC` (the data-cartridge list —
`core/dtc.py`). Neither has a pydcs field, and there is no generic passthrough,
so both would be dropped on save.

Patching `FlyingUnit.dict` is therefore unavoidable, but doing it once per
helper is not: two independent wrappers each guard on their own marker
attribute, so the second one to install hides the first one's marker and every
later build wraps the chain again. One registry and one wrapper instead —
`emit_unit_key("dtc", "DTC")` says "if a unit carries `.dtc`, write it as
`DTC`", and installing twice is a no-op.

Keys are emitted in sorted order so the serialized unit table does not depend
on which helper registered first — that would make `generate <slug>` and
`generate` (all missions, one process) disagree byte for byte.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: unit attribute -> mission-file key. Registered by the helper that owns it.
_EXTRA_KEYS: dict[str, str] = {}


def emit_unit_key(attribute: str, key: str) -> None:
    """Make `FlyingUnit.dict()` write `key` from the unit attribute `attribute`.

    Only a truthy value is written, so a unit that never got the attribute (an
    AI flight, an airframe the helper skips) serializes exactly as before.
    """
    _EXTRA_KEYS[attribute] = key
    _install()


def _install() -> None:
    """Wrap `FlyingUnit.dict` once; the registry does the rest."""
    from dcs.flyingunit import FlyingUnit

    if getattr(FlyingUnit.dict, "_emits_extra_keys", False):
        return
    inner = FlyingUnit.dict

    def dict_with_extras(self: FlyingUnit) -> dict[str, Any]:
        d = inner(self)
        for attribute, key in sorted(_EXTRA_KEYS.items()):
            value = getattr(self, attribute, None)
            if value:
                d[key] = value
        return d

    dict_with_extras._emits_extra_keys = True  # ty: ignore[unresolved-attribute]
    FlyingUnit.dict = dict_with_extras  # ty: ignore[invalid-assignment]
    log.debug("patched FlyingUnit.dict to emit extra unit keys")
