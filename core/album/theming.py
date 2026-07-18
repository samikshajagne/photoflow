"""
Per-event colour theming (Phase 3, B3).

A designed wedding album gives each event its own colour mood — Haldi spreads
read yellow, a lawn reception reads green, and so on — because the backgrounds
are tinted from that event's photos. This module derives, from a set of photos,
a single representative colour (the "mood"), a light background tint from it,
and a best-effort event name.

Pure Pillow/NumPy so it renders and tests without the detection backends. The
renderer (``core.album.raster``) uses :func:`dominant_color` +
:func:`background_tint` to give every spread in a section the *same* background,
for a coherent per-event look.
"""

from __future__ import annotations

import colorsys
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageOps

PathLike = Union[str, Path]
RGB = tuple[int, int, int]
Loader = Callable[[str], Image.Image]

# Neutral fallback when no photo yields a usable colour.
NEUTRAL: RGB = (210, 210, 210)

# A pixel counts toward the "mood" colour only if it is colourful enough
# (saturated) and not too dark — so a few vivid garlands/turmeric tones drive
# the theme instead of being washed out by neutral walls and skin tones.
_SAT_MIN = 0.25
_VAL_MIN = 0.20
_MIN_COLOURFUL_FRACTION = 0.04


def _default_loader(path: str) -> Optional[Image.Image]:
    try:
        img = Image.open(path)
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")
    except Exception:  # noqa: BLE001 - unreadable photo simply doesn't vote
        return None


def _sample(paths: Sequence[PathLike], count: int) -> list[str]:
    """Evenly spaced sample of up to ``count`` paths across the list."""
    items = [str(p) for p in paths]
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


def dominant_color(
    image_paths: Sequence[PathLike],
    sample: int = 8,
    *,
    loader: Optional[Loader] = None,
) -> RGB:
    """
    Return a representative "mood" colour for a set of photos.

    Samples up to ``sample`` photos, pools their pixels (downscaled), and
    averages the *colourful* ones (saturated, not-too-dark). Falls back to the
    overall average, then to a neutral grey, so it never fails.
    """
    open_image = loader or _default_loader
    colourful: list[np.ndarray] = []
    allpix: list[np.ndarray] = []
    for path in _sample(image_paths, sample):
        img = open_image(path)
        if img is None:
            continue
        small = np.asarray(img.convert("RGB").resize((48, 48), Image.BILINEAR), dtype=np.float64)
        px = small.reshape(-1, 3)
        allpix.append(px)
        arr = px / 255.0
        mx = arr.max(axis=1)
        mn = arr.min(axis=1)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        mask = (sat > _SAT_MIN) & (mx > _VAL_MIN)
        if mask.any():
            colourful.append(px[mask])

    if allpix:
        total = sum(len(p) for p in allpix)
        colourful_n = sum(len(c) for c in colourful)
        if colourful and colourful_n >= _MIN_COLOURFUL_FRACTION * total:
            mean = np.concatenate(colourful).mean(axis=0)
        else:
            mean = np.concatenate(allpix).mean(axis=0)
        return tuple(int(round(v)) for v in mean)  # type: ignore[return-value]
    return NEUTRAL


def background_tint(rgb: RGB, lighten: float = 0.66) -> RGB:
    """Blend ``rgb`` toward white by ``lighten`` (0=raw colour, 1=white)."""
    t = max(0.0, min(1.0, lighten))
    return tuple(int(round(c * (1 - t) + 255 * t)) for c in rgb)  # type: ignore[return-value]


def to_hex(rgb: RGB) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def classify_event_name(rgb: RGB) -> Optional[str]:
    """
    Best-effort event name from the mood colour.

    Only high-confidence, colour-distinctive events are named — currently the
    turmeric-yellow **Haldi**. Everything else returns ``None`` (the caller
    keeps its existing chronological/section name), because most wedding events
    are not reliably separable by colour alone.
    """
    r, g, b = (c / 255.0 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue_deg = h * 360.0
    if s >= 0.35 and v >= 0.4 and 40.0 <= hue_deg <= 70.0:
        return "Haldi"
    return None
