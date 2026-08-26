"""The one monospace face this project draws with, regular and bold.

Both things that render text — `core/recon` (sensor chrome and mark labels) and
`core/kneeboard` (whole pages of it) — need a font that is the *same font on
every machine*, because both cache their output on a content hash: a face that
varied with what the host happens to have installed would make a cached PNG
disagree with the page a rebuild produces.

DejaVu Sans Mono, read straight out of matplotlib's bundled font data. matplotlib
is already a hard dependency, so this costs no new asset, and going through
`get_data_path` rather than `font_manager.findfont` avoids consulting (and
building) matplotlib's font cache. A missing file raises: falling back to
`ImageFont.load_default` would silently change every glyph.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_FACES = {False: "DejaVuSansMono.ttf", True: "DejaVuSansMono-Bold.ttf"}


@lru_cache(maxsize=32)
def mono(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """DejaVu Sans Mono at `size` px, bold on request."""
    import matplotlib

    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / _FACES[bold]
    if not path.is_file():
        raise FileNotFoundError(
            f"no monospace font at {path} — matplotlib's bundled fonts are missing, "
            "and falling back to a default would change every rendered page"
        )
    return ImageFont.truetype(str(path), size)


def char_width(font: ImageFont.FreeTypeFont) -> float:
    """Advance width of one glyph — the face is monospaced, so any glyph will do."""
    return font.getlength("0")
