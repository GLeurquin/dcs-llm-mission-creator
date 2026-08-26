"""Render-and-cache a still, put it in the `.miz`, and copy it beside the README.

The cache follows `core/tts/synth.py`: a content hash under `cache/`, a hit only
if the file exists and is non-empty, and a post-render check that raises rather
than shipping a truncated file. Two things differ, and both are deliberate.

**The key is the sampled scene, not the query that produced it.** `sensor_still`
always samples the overlay and only skips the render, because a key built from
"theater plus frame geometry" would keep serving a cached picture after the
overlay was rebuilt underneath it — exactly the situation this project's Syria
road rebuild creates. Sampling is a few zarr window reads and some filters;
correctness is worth the milliseconds.

**The mtime and mode are pinned.** pydcs stores the file *path* and writes it into
the archive during `Mission.save` with `zipf.write`, which records the source
file's `st_mtime`, and `ZipInfo.from_file` puts the file mode into
`external_attr`. So a freshly rendered PNG, or one written under a different
umask, changes the `.miz` bytes even when every pixel is identical. `core/dtc.py`
solves the same problem with an explicit `ZipInfo(date_time=...)`, but nothing on
the `MapResource` path takes a `ZipInfo` — the fix has to happen on the filesystem
instead. Note that the project already leaned on this without saying so: the
reproducibility test passes today only because both builds hit a warm
`cache/voice/`, so the WAV mtimes happen not to change.

One guard has nothing to do with caching. `MapResource.store` flattens every
resource to `l10n/DEFAULT/<basename>` and skips a name it has already written,
leaving the second resource key pointing at the *first* file's bytes. That failure
is invisible until someone opens the mission and sees the wrong picture, so a
colliding basename raises here instead.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import structlog

from dcs_mission_creator.core.recon.chrome import Chrome, draw_chrome
from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.render import Mark, render
from dcs_mission_creator.core.recon.sample import Scene, sample_frame

if TYPE_CHECKING:
    from dcs.mission import Mission

    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

#: Bump when a change to the sensor chain, or to how the PNG is encoded, should
#: invalidate every cached render.
RENDER_VERSION = "recon-2"

_DEFAULT_CACHE = Path("cache") / "recon"

#: Attribute the pending stills are parked on, mirroring `core/dtc.py`'s stash.
_STASH = "recon_stills"

#: 1980-01-01T00:00:00Z — the earliest timestamp the zip format can represent.
_FIXED_MTIME = 315_532_800
_FIXED_MODE = 0o644


@dataclass(frozen=True)
class ReconStill:
    """A rendered still: where it is cached, and what it is called everywhere else.

    `filename` is simultaneously the cache file name, the `l10n/DEFAULT` entry in
    the `.miz`, and the README's link target — one name, so the three cannot drift.
    """

    cache_path: Path
    filename: str
    frame: Frame
    caption: str

    def markdown(self) -> str:
        """The figure block a `readme()` embeds, image plus italic caption.

        The alt text is a short description rather than the file name — the name
        carries a content hash, which tells a reader nothing.
        """
        return f"![Wide-area radar still]({self.filename})\n\n*{self.caption}*"


def sensor_still(
    m: Mission,
    frame: Frame,
    marks: Sequence[Mark],
    chrome: Chrome,
    *,
    overlay: MapOverlay,
    slug: str,
    label: str,
    cache_dir: Path | None = None,
) -> ReconStill:
    """Render (or reuse) a still, attach it as a briefing slide, and stash it.

    Attaches via `Mission.add_picture_blue`, so the image becomes a slide on the
    briefing screen. The copy that lands beside the README is `publish_stills`'
    job, which `MissionBuilder.build_miz` calls — a mission cannot forget it.
    """
    return still_from_scene(
        m,
        sample_frame(overlay, frame),
        marks,
        chrome,
        slug=slug,
        label=label,
        cache_dir=cache_dir,
    )


def still_from_scene(
    m: Mission,
    scene: Scene,
    marks: Sequence[Mark],
    chrome: Chrome,
    *,
    slug: str,
    label: str,
    cache_dir: Path | None = None,
) -> ReconStill:
    """`sensor_still` minus the overlay read — everything from a sampled `Scene` on.

    Split out so the cache, the determinism pins and the basename guard are all
    reachable from the default test selection, which has no overlay to sample.
    """
    frame = scene.frame
    digest = _key(scene, marks, chrome)
    filename = f"{slug}-{label}-{digest.hex()[:8]}.png"
    path = (cache_dir or _DEFAULT_CACHE).resolve() / filename

    if path.exists() and path.stat().st_size > 0:
        log.debug("recon cache hit", path=str(path))
    else:
        _render_to(path, scene, marks, chrome, digest)

    still = ReconStill(
        cache_path=path, filename=filename, frame=frame, caption=chrome.caption
    )
    _guard_basename(m, still)
    m.add_picture_blue(str(path))
    _stash(m).append(still)
    return still


def publish_stills(m: Mission, dest: Path) -> list[Path]:
    """Copy every stashed still into `dest`, so the README's relative link resolves.

    The one finishing step in `build_miz` that is about an asset *beside* the
    package rather than inside it — see that method's docstring.
    """
    stills = getattr(m, _STASH, None)
    if not stills:
        return []
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for still in stills:
        out = dest / still.filename
        shutil.copyfile(still.cache_path, out)
        _pin(out)
        written.append(out)
    log.info("recon.published", count=len(written), dest=str(dest))
    return written


# -- internals ---------------------------------------------------------------


def _render_to(
    path: Path, scene: Scene, marks: Sequence[Mark], chrome: Chrome, digest: bytes
) -> None:
    """Render, draw the chrome, write the PNG, then pin mtime and mode."""
    seed = int.from_bytes(digest[:8], "big")
    img = render(scene, marks, seed=seed)
    draw_chrome(img, scene.frame, chrome)

    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("recon render", path=str(path), size=img.size, marks=len(marks))
    # No `pnginfo`: Pillow writes a tIME chunk only when one is handed to it, and a
    # timestamp in the file would defeat the whole point of the mtime pin.
    #
    # Written as RGB even though every pixel is neutral: the whole chain works in
    # mode `L`, and a single-channel PNG comes out of the DCS briefing screen in
    # shades of red — the game's texture loader takes the lone channel for red and
    # leaves green and blue at zero. Any image viewer renders the same file as the
    # grey it is, which is what made the bug look like a viewer problem. Three
    # identical channels cost ~1.5x the file and are unambiguous everywhere.
    img.convert("RGB").save(path, format="PNG", compress_level=6, optimize=False)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(
            f"rendering {path.name} produced no image (frame={scene.frame}, "
            f"marks={len(marks)}, chrome={chrome.platform!r})"
        )
    _pin(path)


def _pin(path: Path) -> None:
    """Fix mtime and mode so the zip entry is a function of the pixels alone."""
    os.chmod(path, _FIXED_MODE)
    os.utime(path, (_FIXED_MTIME, _FIXED_MTIME))


def _key(scene: Scene, marks: Sequence[Mark], chrome: Chrome) -> bytes:
    h = hashlib.sha256()
    h.update(RENDER_VERSION.encode())
    h.update(b"\0")
    h.update(scene.fingerprint())
    for mark in marks:
        h.update(
            f"|{mark.kind}:{mark.x:.2f},{mark.y:.2f},{mark.text},"
            f"{mark.track_deg},{mark.radius_m}".encode()
        )
    h.update(b"\0")
    h.update(
        "|".join(
            (
                chrome.platform,
                chrome.mode,
                chrome.taken_at,
                chrome.classification,
                chrome.footer,
                chrome.caption,
            )
        ).encode()
    )
    return h.digest()


def _guard_basename(m: Mission, still: ReconStill) -> None:
    """Refuse a basename another resource already claimed. See the module docstring."""
    for path in m.map_resource.files.get("DEFAULT", {}).values():
        if Path(path).name == still.filename and Path(path) != still.cache_path:
            raise ValueError(
                f"resource name {still.filename!r} is already taken by {path} — "
                "the .miz flattens resources to l10n/DEFAULT/<basename>, so one of "
                "the two would be silently dropped"
            )


def _stash(m: Mission) -> list[ReconStill]:
    stills = getattr(m, _STASH, None)
    if stills is None:
        stills = []
        setattr(m, _STASH, stills)
    return stills
