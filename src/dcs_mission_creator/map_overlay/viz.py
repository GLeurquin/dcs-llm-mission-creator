"""Render overlay layers as PNGs for visual inspection.

Each layer becomes one PNG; `--layers all` emits a multi-panel composite.
The image is downsampled to ~4000 px on the long edge to keep file size sane
(Caucasus at 50 m is 19 600×33 800 cells — rendering at full res produces
~600 MB PNGs).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import structlog
import zarr

from dcs_mission_creator.map_overlay.query import overlay_root

_LOGGER = structlog.get_logger(__name__)

_MAX_PIXELS = 4_000  # longest-edge cap


def _load_layer_downsampled(root: Path, name: str, max_px: int) -> np.ndarray | None:
    """Open the layer's zarr and stride-sample down to `max_px` on the long edge.

    Reads only the strided cells from the zarr store so peak RAM is the
    output array, not the full uncompressed layer.
    """
    p = root / f"{name}.zarr"
    if not p.exists():
        return None
    z = zarr.open_array(str(p), mode="r")
    h, w = z.shape
    scale = max(h, w) / max_px
    step = max(1, int(round(scale)))
    return np.asarray(z[::step, ::step])


_COLORMAPS = {
    "elevation": ("terrain", None, None),
    "slope": ("magma", 0, 60),
    "vegetation": ("Greens", 0, 3),
    "buildings": ("OrRd", 0, 3),
    "roads_dt": ("Greys_r", 0, 200),  # cells; clipped for contrast
    "rivers_dt": ("Blues_r", 0, 200),
}

_AVAILABLE = list(_COLORMAPS)


def render(theater: str, layers: list[str], output: Path) -> None:
    root = overlay_root(theater)
    if not root.exists():
        raise FileNotFoundError(f"no overlay at {root}; build it first")
    if not layers or layers == ["all"]:
        layers = _AVAILABLE

    panels: list[tuple[str, np.ndarray]] = []
    for name in layers:
        if name not in _COLORMAPS:
            raise ValueError(f"unknown layer {name!r}; known: {_AVAILABLE}")
        arr = _load_layer_downsampled(root, name, _MAX_PIXELS)
        if arr is None:
            _LOGGER.info("viz.layer_missing", layer=name)
            continue
        panels.append((name, arr))

    if not panels:
        raise RuntimeError("no layers to render")

    cols = min(3, len(panels))
    rows = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 4 * rows), squeeze=False)
    for ax in axes.flat:
        ax.set_axis_off()
    for i, (name, arr) in enumerate(panels):
        ax = axes[i // cols][i % cols]
        cmap, vmin, vmax = _COLORMAPS[name]
        im = ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
        ax.set_title(f"{name}  shape={arr.shape}", fontsize=8)
        ax.set_axis_on()
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, fraction=0.03)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    _LOGGER.info("viz.saved", path=str(output))
