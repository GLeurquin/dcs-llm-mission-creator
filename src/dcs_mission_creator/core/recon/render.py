"""Scene -> a grayscale radar frame. Pure numpy plus one PIL handoff.

## Why radar and not an EO/IR photo

The overlay is 50 m posts. Measured over the Idlib convoy route, a 6 km frame
contains **no roads, no water and no trees**, elevation spanning 271-319 m in
whole metres, and 49 distinct elevation values — about 38 % of adjacent posts are
bit-identical. An EO/IR frame of that ground is *supposed* to show parcel
boundaries, crop tone, tracks and building shadows, so ~95 % of its pixels would
have to be invented, and hillshading 1 m-quantised elevation at ~1.2° slope gives
contour terracing rather than terrain.

A wide-area radar product is the one register whose own resolution limit matches
this data. What a flat agricultural plain looks like in SAR — speckly mid-grey,
near-black specular water and roads, bright double-bounce villages, mid-bright
forest — is exactly the four surfaces the overlay actually has. And its grain
comes from a **named physical model** (fully developed speckle, `gamma(L, 1/L)`
for an L-look product) rather than from invented texture, so the dominant signal
in the image is something true. It also annihilates the elevation quantisation
for free.

Note the polarity, which is the opposite of an optical image and the fastest way
to tell a real radar frame from a faked one: **water and asphalt are dark**
(specular, reflecting away from the sensor), **towns are the brightest thing in
the frame** (double-bounce off walls), and **forest is brighter than bare
ground** (volume scattering).

## Vehicles are annotation, not imagery

At 25 m/px a 7 m vehicle is a fifth of a pixel. Drawing it as a "hot blob" would
be a claim about resolution the data cannot support. Real wide-area MTI products
draw movers as *symbology over* the radar base — an open box, optionally with a
velocity tick — applied after the sensor chain at full contrast. So that is what
`Mark` is, and it is why the detections survive the speckle and the stretch
untouched: they were never part of the image.

## Texture: statistics yes, features no

The first version of this renderer held sigma-zero constant across bare ground,
on the principle that inventing land texture is dishonest. The result was a frame
that was 90 % uniform white noise — it read as *broken* rather than as *coarse*,
which is a worse failure, because a viewer cannot tell a coarse product from a
malfunctioning one.

The line that actually matters is between inventing **statistics** and inventing
**features**. Real farmland has field-to-field sigma-zero variation of a few dB,
correlated over field-sized patches; modelling that as a correlated random field
claims only "this ground has roughness variation at roughly this scale and
amplitude", which is true of the ground and is exactly the kind of claim speckle
already makes. It does *not* claim a hedge, a track or a parcel boundary is in any
particular place. So the roughness field stays, and the prohibition it replaces
is narrower and sharper: no feature the overlay does not know about ever gets
drawn.

## One deliberate omission

**No radar shadow.** Shadowing at 45° nominal incidence needs slopes past 45°.
This terrain has a mean slope of ~1.3° and a 95th percentile of ~2.9°, so an
implementation would compute a mask that is empty everywhere and cost a rotated
cumulative maximum to do it. Add it when a mission frames real mountains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from dcs.mapping import Point
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

from dcs_mission_creator.core.recon.chrome import mono_font
from dcs_mission_creator.core.recon.frame import Frame
from dcs_mission_creator.core.recon.sample import Scene

#: Backscatter coefficients, linear power. Radar polarity, not optical — see the
#: module docstring. Roughly C-band HH at 45 degrees over dry terrain.
_SIGMA_BARE = 0.08
_SIGMA_FOREST = 0.25
_SIGMA_URBAN = 0.70
_SIGMA_ROAD = 0.010
_SIGMA_WATER = 0.004

#: Correlated roughness of open ground, as `(correlation length in metres, dB)`.
#: Field-block scale down to within-field scale — see the module docstring on why
#: this is a statistical claim and not an invented feature. Amplitudes are the
#: standard deviation of the log-modulation: about a dB at field-block scale, which
#: is the field-to-field spread of real farmland and, against the fixed display
#: window below, stays a texture instead of becoming the subject of the picture.
_ROUGHNESS_OCTAVES = ((250.0, 1.1), (110.0, 0.8), (50.0, 0.5))

#: Built-up areas get their own finer, stronger modulation: a town in SAR is a
#: mosaic of bright walls and black street shadow, not a uniform bright patch.
_URBAN_OCTAVES = ((100.0, 2.2), (40.0, 1.8))

#: Nominal incidence angle of the product, degrees from vertical.
_INCIDENCE_DEG = 45.0

#: Number of looks. A 5-look product is the usual wide-area compromise: enough
#: averaging to see a road through the speckle, little enough that the speckle is
#: still the dominant texture.
_LOOKS = 5

#: Noise-equivalent sigma-zero — the receiver floor, added *before* the log. This
#: is what keeps water and roads dark-but-grainy instead of crushed to black, and
#: its absence is the single most obvious tell of a synthesised radar image. About
#: -26 dB, a decent airborne figure: high enough to lift water off pure black, low
#: enough not to swamp the specular surfaces it sits under.
_NESZ = 0.0025

#: Point-spread blur at supersampled resolution, in supersampled pixels.
_PSF_SIGMA = 1.6

#: Multiplicative azimuth (per-column) gain ripple, and additive detector read
#: noise as a fraction of full scale.
_AZIMUTH_RIPPLE = 0.003
_READ_NOISE = 1.2 / 255.0

#: Fixed display window, dB. **Not** a per-frame percentile stretch, and that is a
#: correctness point rather than a preference: this scene is ~90 % open ground, so
#: percentiles land inside the ground's own roughness distribution and renormalise
#: whatever roughness amplitude is chosen up to full black-to-white — the texture
#: becomes the subject of the picture and the constants above stop meaning
#: anything. A fixed window is also what a calibrated product does, so a town is
#: equally bright in every frame instead of depending on what else was in shot.
_DISPLAY_LO_DB, _DISPLAY_HI_DB = -26.0, 1.0

#: Grey level painted where the frame left the overlay.
_NO_DATA = 12

#: Glyph size for annotation labels. Public because `landmark` measures a place
#: name against this exact size to decide whether it fits in the frame.
LABEL_SIZE = 14

MarkKind = Literal["detection", "group", "aimpoint", "label", "place"]


@dataclass(frozen=True)
class Mark:
    """One piece of product annotation, positioned in DCS world coordinates.

    `x` is north and `y` is east, matching pydcs. `track_deg` is a world heading;
    the renderer rotates it into the frame, so a caller never needs to know how the
    frame is oriented. `radius_m` sizes a `group` box.

    The `group` kind exists because of an arithmetic fact about this scale: a
    column at a 120 m march interval is 4.8 px between vehicles at 25 m/px, so
    eleven individual boxes overlap into one unreadable ladder. A real wide-area
    MTI display solves that the same way — small ticks for the individual returns,
    one box round the cluster carrying the count. It doubles as the target-area box
    on a static graphic, where there is no track to draw: leave `track_deg` unset.

    `place` is the other register entirely — a named settlement, drawn as a plain
    dot so it cannot be read as something the sensor found. Reference points, not
    detections; see `landmark.py`.
    """

    x: float
    y: float
    kind: MarkKind = "detection"
    text: str = ""
    track_deg: float | None = None
    radius_m: float = 0.0


def render(
    scene: Scene, marks: Sequence[Mark] = (), *, seed: int, annotate: bool = True
) -> Image.Image:
    """Run the sensor chain over `scene` and draw `marks` on top of the result.

    `seed` must come from the render cache key, never from stdlib `random`: the
    pixels are then a function of the inputs alone, so two callers with identical
    inputs cannot disagree and a cache hit is always what a fresh render would
    have produced.
    """
    rng = np.random.default_rng(seed)

    sigma0 = _backscatter(scene, rng)
    sigma0 = sigma0 * _incidence_gain(scene)
    sigma0 = gaussian_filter(sigma0, sigma=_PSF_SIGMA)  # optics, before sampling
    power = _decimate(sigma0, scene.frame.supersample)

    # Speckle belongs at the resolution cell, which is one output pixel here.
    power = power * rng.gamma(_LOOKS, 1.0 / _LOOKS, size=power.shape)
    power = power + _NESZ
    power = power * (1.0 + _AZIMUTH_RIPPLE * rng.standard_normal((1, power.shape[1])))

    valid = _decimate(scene.valid.astype(np.float32), scene.frame.supersample) > 0.5
    grey = _to_display(power)
    grey = grey + rng.standard_normal(grey.shape) * _READ_NOISE * 255.0
    grey = np.where(valid, np.clip(grey, 0.0, 255.0), float(_NO_DATA))

    img = Image.fromarray(grey.astype(np.uint8), mode="L")
    if annotate and marks:
        _draw_marks(img, scene, marks)
    return img


# -- sensor chain ------------------------------------------------------------


def _backscatter(scene: Scene, rng: np.random.Generator) -> np.ndarray:
    """Composite sigma-zero from the surface coverage fractions.

    Blended in increasing order of how completely each surface overrides what is
    under it: forest and buildings sit *on* the ground, a road replaces it, and
    water replaces everything. Open ground and built-up areas each carry their own
    correlated roughness; the specular surfaces do not, because a road and a lake
    are smooth and that is precisely why they are dark.
    """
    gsd = scene.frame.gsd_m / scene.frame.supersample
    shape = scene.elevation_m.shape

    ground = _SIGMA_BARE * _roughness(shape, gsd, _ROUGHNESS_OCTAVES, rng)
    sigma = _blend(ground, _SIGMA_FOREST, scene.forest)
    urban = _SIGMA_URBAN * _roughness(shape, gsd, _URBAN_OCTAVES, rng)
    sigma = sigma * (1.0 - scene.urban) + urban * scene.urban
    sigma = _blend(sigma, _SIGMA_ROAD, scene.road)
    sigma = _blend(sigma, _SIGMA_WATER, np.maximum(scene.water, scene.river))
    return sigma


def _blend(base: np.ndarray, value: float, fraction: np.ndarray) -> np.ndarray:
    return base * (1.0 - fraction) + value * fraction


def _roughness(
    shape: tuple[int, ...],
    gsd_m: float,
    octaves: tuple[tuple[float, float], ...],
    rng: np.random.Generator,
) -> np.ndarray:
    """A correlated multiplicative sigma-zero field, mean 1.

    Each octave is white noise smoothed to its correlation length, renormalised to
    unit variance (a gaussian blur reduces variance by an amount that depends on
    the kernel, so the requested dB would otherwise be silently scaled down), then
    summed in the log domain and exponentiated.
    """
    log_mod = np.zeros(shape, dtype=np.float32)
    for length_m, amplitude_db in octaves:
        sigma_px = max(length_m / gsd_m, 0.5)
        field = gaussian_filter(rng.standard_normal(shape).astype(np.float32), sigma_px)
        std = float(field.std())
        if std > 1e-9:
            field /= std
        log_mod += field * amplitude_db
    return np.power(10.0, log_mod / 10.0).astype(np.float32)


def _incidence_gain(scene: Scene) -> np.ndarray:
    """Brightening of slopes tilted toward the sensor, dimming of those tilted away.

    The look direction is across the frame (frame right), the way a side-looking
    radar images a swath it is flying alongside. Only the slope component along
    that direction changes the local incidence angle.
    """
    gsd = scene.frame.gsd_m / scene.frame.supersample
    # Gradient along the look direction = across image columns.
    slope = np.gradient(scene.elevation_m, gsd, axis=1)
    nominal = math.radians(_INCIDENCE_DEG)
    local = nominal - np.arctan(slope)
    gain = np.cos(local) / math.cos(nominal)
    return np.clip(gain, 0.2, 3.0).astype(np.float32)


def _decimate(arr: np.ndarray, factor: int) -> np.ndarray:
    """Exact integer box average — no `zoom`, whose grid convention has drifted."""
    if factor == 1:
        return arr
    h, w = arr.shape
    return arr.reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))


def _to_display(power: np.ndarray) -> np.ndarray:
    """Log-magnitude against the fixed calibration window — see `_DISPLAY_LO_DB`."""
    db = 10.0 * np.log10(np.maximum(power, 1e-9))
    span = _DISPLAY_HI_DB - _DISPLAY_LO_DB
    return np.clip((db - _DISPLAY_LO_DB) / span, 0.0, 1.0) * 255.0


# -- annotation --------------------------------------------------------------


#: Where `_draw_marks` puts a label relative to its anchor, and how tall a line of
#: `LABEL_SIZE` glyphs plus its stroke is. Shared with `mark_extent` so the
#: collision geometry cannot drift from the drawing.
_TEXT_DX_PX = 8.0
_TEXT_DY_PX = -7.0
_TEXT_HEIGHT_PX = LABEL_SIZE + 6.0


def _symbol_half_px(mark: Mark, gsd_m: float) -> float:
    """Half-width of a mark's symbol, in pixels — the numbers the drawers use."""
    if mark.kind == "group":
        return max(max(mark.radius_m / gsd_m, 6.0), 4.0)
    if mark.kind == "aimpoint":
        return 7.0
    if mark.kind == "detection":
        return 1.0
    if mark.kind == "place":
        return 2.0
    return 0.0


def _label_dx_px(mark: Mark, gsd_m: float) -> float:
    """Horizontal offset of a mark's text. A group box pushes its text clear."""
    if mark.kind == "group":
        return _symbol_half_px(mark, gsd_m) + 4.0
    return _TEXT_DX_PX


def mark_extent(frame: Frame, mark: Mark) -> tuple[float, float, float, float]:
    """Pixel box `(x0, y0, x1, y1)` covering everything a mark draws.

    Symbol *and* text, because the text is the part that reaches: a group label
    like `11 DET  TRK 314  35 KM/H` is ~190 px, which at 25 m/px is 4.7 km of
    ground — so a place name three kilometres away from a bracket's centre can
    still be printed straight through its label, which is what happened before
    this existed. Lives here, next to the drawing it measures.
    """
    px, py = frame.world_to_px(Point(mark.x, mark.y, frame.center._terrain))
    half = _symbol_half_px(mark, frame.gsd_m)
    x0, y0, x1, y1 = px - half, py - half, px + half, py + half
    if mark.text:
        text_x0 = px + _label_dx_px(mark, frame.gsd_m)
        text_y0 = py + _TEXT_DY_PX
        x1 = max(x1, text_x0 + mono_font(LABEL_SIZE).getlength(mark.text))
        y0 = min(y0, text_y0)
        y1 = max(y1, text_y0 + _TEXT_HEIGHT_PX)
    return x0, y0, x1, y1


def _draw_marks(img: Image.Image, scene: Scene, marks: Sequence[Mark]) -> None:
    """Draw product symbology at full contrast, after the sensor chain."""
    draw = ImageDraw.Draw(img)
    terrain = scene.frame.center._terrain
    frame = scene.frame
    for mark in marks:
        px, py = frame.world_to_px(Point(mark.x, mark.y, terrain))
        label_dx = _label_dx_px(mark, frame.gsd_m)
        if mark.kind == "detection":
            _detection_tick(draw, px, py)
        elif mark.kind == "group":
            _group_box(draw, px, py, mark, frame.gsd_m, frame.heading_deg)
        elif mark.kind == "aimpoint":
            _aimpoint(draw, px, py)
        elif mark.kind == "place":
            _place_dot(draw, px, py)
        if mark.text:
            # Same face as the chrome, and stroked: the default PIL bitmap font is
            # tiny and unstroked, which left labels unreadable over speckle.
            draw.text(
                (px + label_dx, py + _TEXT_DY_PX),
                mark.text,
                font=mono_font(LABEL_SIZE),
                fill=255,
                stroke_width=2,
                stroke_fill=0,
            )


def _detection_tick(draw: ImageDraw.ImageDraw, px: float, py: float) -> None:
    """One resolved mover: a 3 px open box, small enough to sit in a column."""
    draw.rectangle((px - 1, py - 1, px + 1, py + 1), outline=255, width=1)


def _group_box(
    draw: ImageDraw.ImageDraw,
    px: float,
    py: float,
    mark: Mark,
    gsd_m: float,
    frame_heading: float,
) -> None:
    """A bracket round a cluster of movers, with its track vector.

    Label placement is `_label_dx_px`'s job, so `mark_extent` and the drawing agree.
    """
    half = max(mark.radius_m / gsd_m, 6.0)
    corner = max(half * 0.35, 4.0)
    x0, y0, x1, y1 = px - half, py - half, px + half, py + half
    # Corner brackets rather than a closed rectangle — it reads as symbology laid
    # over the image instead of as a feature in it.
    for cx, sx in ((x0, 1.0), (x1, -1.0)):
        for cy, sy in ((y0, 1.0), (y1, -1.0)):
            draw.line((cx, cy, cx + corner * sx, cy), fill=255, width=2)
            draw.line((cx, cy, cx, cy + corner * sy), fill=255, width=2)
    if mark.track_deg is not None:
        # World heading -> frame-relative, then into pixel space where up is -y.
        rel = math.radians(mark.track_deg - frame_heading)
        tip = half + 18.0
        draw.line(
            (px, py, px + math.sin(rel) * tip, py - math.cos(rel) * tip),
            fill=255,
            width=1,
        )


def _place_dot(draw: ImageDraw.ImageDraw, px: float, py: float) -> None:
    """A named settlement: a small filled dot, outlined so it reads over speckle.

    Deliberately not a box or a cross — those are the sensor's own vocabulary here
    (a return, an aimpoint), and a place name is neither. It marks where a thing
    the reader already has on their map is.
    """
    draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=255, outline=0, width=1)


def _aimpoint(draw: ImageDraw.ImageDraw, px: float, py: float) -> None:
    arm = 7
    draw.line((px - arm, py, px + arm, py), fill=255, width=1)
    draw.line((px, py - arm, px, py + arm), fill=255, width=1)
