"""Point pydcs at a local DCS World installation so loadouts populate.

pydcs finds the game only through the Windows registry (`dcs.installation`),
so under Linux/WSL `get_dcs_install_directory()` returns `""`, every payload
directory in `dcs.payloads.PayloadDirectories` is missing, and
`FlyingType.load_payloads()` yields nothing — `load_task_default_loadout()`
then silently leaves every pylon empty.

`configure()` replaces that lookup with an explicit path taken from
`$DCS_INSTALL_DIR` (Windows spelling accepted: `E:\\Games\\DCS World OpenBeta`
is translated to `/mnt/e/Games/DCS World OpenBeta` under WSL). It is called
from `MissionBuilder.__init__`, i.e. before any flight is created, which is
what matters: `PayloadDirectories` caches its directory list on first use.

Custom loadouts saved in-game live under the Saved Games folder; that one is
derived from `dcs_variant.txt` next to the install (or forced with
`$DCS_SAVED_GAMES_DIR`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import dcs.installation as installation
import dcs.liveries.liveryscanner as liveryscanner
import structlog

INSTALL_ENV = "DCS_INSTALL_DIR"
SAVED_GAMES_ENV = "DCS_SAVED_GAMES_DIR"

log = structlog.get_logger(__name__)

_configured = False


def _local_path(raw: str) -> Path:
    """Map a Windows path onto its WSL mount; pass anything else through."""
    text = raw.strip().strip('"')
    drive_letter = re.fullmatch(r"([A-Za-z]):[\\/](.*)", text)
    if drive_letter is not None and Path("/mnt").is_dir():
        drive, rest = drive_letter.groups()
        return Path("/mnt") / drive.lower() / rest.replace("\\", "/")
    return Path(text)


def _variant_suffix(install: Path) -> str:
    """`.openbeta` for a beta install, `""` for stable — as DCS names it."""
    variant = install / "dcs_variant.txt"
    if not variant.exists():
        return ""
    return "." + re.sub(r"[^\w\d-]", "", variant.read_text())


def _saved_games_dir(install: Path) -> Path | None:
    """Locate the Saved Games folder holding user liveries/payloads/mods."""
    forced = os.environ.get(SAVED_GAMES_ENV)
    if forced:
        return _local_path(forced)

    name = f"DCS{_variant_suffix(install)}"
    native = Path(os.path.expanduser("~")) / "Saved Games" / name
    if native.is_dir():
        return native
    # WSL: the game writes into the Windows profile, not the Linux home.
    windows_users = Path("/mnt/c/Users")
    if windows_users.is_dir():
        for profile in sorted(windows_users.iterdir()):
            candidate = profile / "Saved Games" / name
            if candidate.is_dir():
                return candidate
    return None


def install_dir() -> Path | None:
    """The DCS folder from `$DCS_INSTALL_DIR`, or `None` with one warning.

    `configure` teaches *pydcs* where the game is; this answers the same question
    for the parts of this project that read the install directly — the kneeboard's
    navaid table (`Mods/terrains/<T>/Beacons.lua`) and its check for which
    airfields the theatre already ships a chart of. Kept here so the env var, the
    Windows-path translation and the "is this really an install" test have one
    implementation.
    """
    raw = os.environ.get(INSTALL_ENV)
    if not raw:
        log.warning(
            "DCS install dir unknown",
            hint=f"export {INSTALL_ENV}=<DCS World folder>",
        )
        return None
    path = _local_path(str(raw))
    if not (path / "Mods").is_dir():
        log.warning("not a DCS installation", path=str(path))
        return None
    return path


def configure(install_dir: str | Path | None = None) -> None:
    """Teach pydcs where DCS lives. Idempotent; first call wins."""
    global _configured
    if _configured:
        return
    _configured = True

    raw = install_dir if install_dir is not None else os.environ.get(INSTALL_ENV)
    if not raw:
        if sys.platform != "win32":
            log.warning(
                "DCS install dir unknown, loadouts will be empty",
                hint=f"export {INSTALL_ENV}=<DCS World folder>",
            )
        return  # on Windows pydcs reads the registry itself

    install = _local_path(str(raw))
    if not (install / "MissionEditor").is_dir():
        log.warning("not a DCS installation, ignoring", path=str(install))
        return

    saved = _saved_games_dir(install)
    install_str = f"{install}{os.path.sep}"
    saved_str = str(saved) if saved is not None else ""
    # `dcs.payloads` calls these through the module, so rebinding them here is
    # enough for loadouts.
    installation.get_dcs_install_directory = lambda: install_str  # ty: ignore[invalid-assignment]
    if saved_str:
        installation.get_dcs_saved_games_directory = lambda: saved_str  # ty: ignore[invalid-assignment]
    _mute_livery_scanner()
    log.debug("pydcs install dir", install=install_str, saved_games=saved_str)


def _mute_livery_scanner() -> None:
    """Stop pydcs's livery scanner from probing the absent Windows registry.

    `dcs.liveries.liveryscanner` imported the two lookups by name, so it keeps
    its own binding and the rebinding above does not reach it — it re-runs the
    registry probe and prints four warnings per mission build. Pointing it at
    the real install is not an option off Windows: `Livery.from_lua` derives the
    livery id with `path.split("\\\\")`, so a Linux scan produces full-path ids
    DCS cannot resolve (and takes ~50 s across /mnt). Feeding it "" keeps
    liveries at the DCS default — today's behaviour, minus the noise.
    """
    if sys.platform == "win32":
        return
    liveryscanner.get_dcs_install_directory = lambda: ""  # ty: ignore[invalid-assignment]
    liveryscanner.get_dcs_saved_games_directory = lambda: ""  # ty: ignore[invalid-assignment]
