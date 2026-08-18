"""Product furniture — the text and marginalia a sensor frame carries.

This is not decoration. The chrome is where the frame states **what it can and
cannot show**: `POST 50M` and a scale bar tell the reader the ground sample
distance, so a viewer calibrates their expectations against a declared number
instead of against how sharp the picture happens to look. That is the actual
answer to "will this be believable" — a declared resolution, not more invented
detail. A frame without it is a frame making an unbounded claim.

Everything here is a string the *mission* supplies. In particular `taken_at` is
not derived from the mission clock: turning a DCS mission time into a Zulu or
local stamp needs a theater timezone the project does not model, and inventing one
inside a core helper is the kind of quiet wrongness that survives for a year. The
mission knows what its own briefing claims; it passes that.

Drawn in PIL over the finished grayscale, with a dark stroke round every glyph —
the base is mid-grey speckle, so plain white text would dissolve into it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dcs_mission_creator.core.recon.frame import Frame

#: Inset of the chrome from the image edge, in pixels.
_PAD = 14

#: Glyph sizes. Small enough to read as instrument furniture rather than as a
#: caption typeset over a photograph.
_SIZE_MAIN = 15
_SIZE_SMALL = 13

#: Candidate scale-bar lengths in metres, longest first. The bar takes the longest
#: that still fits in a fifth of the frame, so it is always a round number a reader
#: can pace off rather than whatever a fixed fraction happened to work out to.
_SCALE_STEPS_M = (50_000.0, 20_000.0, 10_000.0, 5_000.0, 2_000.0, 1_000.0, 500.0)


@dataclass(frozen=True)
class Chrome:
    """The strings and numbers a product frame carries in its margins.

    `caption` is the prose that goes with the figure wherever it is published; it
    lives here rather than on the mission so the figure and its caption cannot
    drift apart, the same reasoning as `triggers.message_to_all` taking one `text`.
    """

    platform: str
    mode: str
    taken_at: str
    classification: str = ""
    footer: str = ""
    caption: str = ""


def draw_chrome(img: Image.Image, frame: Frame, chrome: Chrome) -> None:
    """Draw `chrome` onto `img` in place. Expects a mode-`L` image."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    main = mono_font(_SIZE_MAIN)
    small = mono_font(_SIZE_SMALL)

    draw.rectangle((0, 0, w - 1, h - 1), outline=200, width=1)

    _text(draw, (_PAD, _PAD), f"{chrome.platform}   {chrome.mode}", main)
    _text(draw, (w - _PAD, _PAD), chrome.taken_at, main, anchor="ra")
    if chrome.classification:
        _text(draw, (w // 2, _PAD), chrome.classification, small, anchor="ma")

    _scale_bar(draw, frame, w, h, small)
    _north_arrow(draw, frame, w, h, small)
    if chrome.footer:
        _text(draw, (w - _PAD, h - _PAD), chrome.footer, main, anchor="rd")


# -- pieces ------------------------------------------------------------------


def _scale_bar(
    draw: ImageDraw.ImageDraw,
    frame: Frame,
    w: int,
    h: int,
    font: ImageFont.FreeTypeFont,
) -> None:
    """A round-number bar, plus the post spacing the product was built from."""
    length_m = _bar_length_m(frame.width_m)
    length_px = length_m / frame.gsd_m
    x0 = _PAD
    y = h - _PAD - 16
    x1 = x0 + length_px
    draw.line((x0, y, x1, y), fill=255, width=2)
    for x in (x0, (x0 + x1) / 2.0, x1):
        draw.line((x, y - 4, x, y + 4), fill=255, width=2)
    label = f"{length_m / 1000.0:g} KM" if length_m >= 1000.0 else f"{length_m:g} M"
    _text(draw, (x0, y + 6), f"{label}    POST {frame.cell_size_m}M", font)


def _bar_length_m(frame_width_m: float) -> float:
    limit = frame_width_m / 5.0
    for step in _SCALE_STEPS_M:
        if step <= limit:
            return step
    return _SCALE_STEPS_M[-1]


def _north_arrow(
    draw: ImageDraw.ImageDraw,
    frame: Frame,
    w: int,
    h: int,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Where north is, given the frame is rotated to something else.

    The frame's up axis is `heading_deg`, so north sits at minus that angle from
    up — the one piece of chrome that would be wrong if the rotation convention
    were ever inverted.
    """
    # Right edge, vertically centred. The bottom corners are taken by the scale
    # bar and the footer, and an arrow drawn into either of them overlaps the text.
    cx = w - _PAD - 16
    cy = h // 2
    rad = math.radians(-frame.heading_deg)
    dx, dy = math.sin(rad) * 20.0, -math.cos(rad) * 20.0
    draw.line((cx, cy, cx + dx, cy + dy), fill=255, width=2)
    # Arrowhead: two short barbs swept back from the tip.
    for sweep in (2.5, -2.5):
        bx = math.sin(rad + sweep) * 7.0
        by = -math.cos(rad + sweep) * 7.0
        draw.line((cx + dx, cy + dy, cx + dx + bx, cy + dy + by), fill=255, width=2)
    _text(draw, (cx + dx * 1.6, cy + dy * 1.6), "N", font, anchor="mm")


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    anchor: str = "la",
) -> None:
    """White glyphs with a dark stroke, so they survive over mid-grey speckle."""
    draw.text(
        xy, text, font=font, fill=255, anchor=anchor, stroke_width=2, stroke_fill=0
    )


@lru_cache(maxsize=4)
def mono_font(size: int) -> ImageFont.FreeTypeFont:
    """DejaVu Sans Mono, taken from matplotlib's bundled data.

    matplotlib is already a hard dependency, so this needs no new font asset and
    cannot vary with what fonts the host machine happens to have installed. Read
    straight off `get_data_path` rather than through `font_manager.findfont`, which
    would consult (and build) matplotlib's font cache. A missing file raises: a
    silent fall back to `load_default` would change every pixel of the chrome and
    quietly break the render cache's byte-identity.
    """
    import matplotlib

    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSansMono.ttf"
    if not path.is_file():
        raise FileNotFoundError(
            f"no monospace font at {path} — matplotlib's bundled fonts are missing, "
            "and falling back to a default would change the rendered chrome"
        )
    return ImageFont.truetype(str(path), size)
