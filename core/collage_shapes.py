"""
Shape masks for collages.

Produces an ``L`` mask at any canvas size that confines the photo layer to a
shape -- a heart, a circle, or arbitrary text such as ``"25"`` for an
anniversary or a couple's initials. :func:`core.collage.build_collage` clips
its photo layer with this, so the shape is cut out of the *photos* while the
chosen background still fills the whole canvas.

Pure Pillow/NumPy, no Qt, so shapes are directly testable.

Text shapes deliberately reuse :func:`core.album.textlayer.resolve_font`
instead of loading fonts here: that helper already handles the
bundled -> system -> default fallback chain, and keeping one code path means
font licensing is decided in exactly one place.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from utils.logger import get_logger

logger = get_logger(__name__)

SHAPE_NONE = "none"
SHAPE_HEART = "heart"
SHAPE_CIRCLE = "circle"
SHAPE_ROUNDED = "rounded"
SHAPE_STAR = "star"
SHAPE_TEXT = "text"
SHAPES: tuple[str, ...] = (
    SHAPE_NONE, SHAPE_HEART, SHAPE_CIRCLE, SHAPE_ROUNDED, SHAPE_STAR, SHAPE_TEXT
)

# Shape masks get a slight blur so the cut edge doesn't look jagged.
_EDGE_SOFTEN_FRAC = 0.0015


class ShapeError(Exception):
    """Raised when a shape cannot be produced (unknown name, empty text)."""


def shape_mask(shape: str, size: tuple[int, int], text: str = "") -> Image.Image:
    """
    An ``L`` mask for ``shape`` at ``size`` (255 = keep the photos here).

    Args:
        shape: One of :data:`SHAPES`.
        size: ``(width, height)`` of the canvas.
        text: The characters to cut out when ``shape`` is ``"text"``.

    Raises:
        ShapeError: for an unknown shape, a degenerate size, or a ``text``
            shape with nothing to draw.
    """
    width, height = int(size[0]), int(size[1])
    if width < 2 or height < 2:
        raise ShapeError(f"Canvas too small for a shape mask: {size}")

    if shape in (SHAPE_NONE, "", None):
        return Image.new("L", (width, height), 255)
    if shape == SHAPE_HEART:
        mask = _heart_mask(width, height)
    elif shape == SHAPE_CIRCLE:
        mask = _ellipse_mask(width, height)
    elif shape == SHAPE_ROUNDED:
        mask = _rounded_mask(width, height)
    elif shape == SHAPE_STAR:
        mask = _star_mask(width, height)
    elif shape == SHAPE_TEXT:
        mask = _text_mask(width, height, text)
    else:
        raise ShapeError(f"Unknown shape {shape!r}; expected one of {SHAPES}")

    soften = max(1, round(min(width, height) * _EDGE_SOFTEN_FRAC))
    return mask.filter(ImageFilter.GaussianBlur(soften))


def _inset(width: int, height: int, frac: float = 0.02) -> tuple[int, int, int, int]:
    """A slightly inset drawing box so shapes don't touch the canvas edge."""
    pad_x = round(width * frac)
    pad_y = round(height * frac)
    return pad_x, pad_y, width - pad_x, height - pad_y


def _heart_mask(width: int, height: int) -> Image.Image:
    """
    A heart, drawn from the standard parametric curve.

    Uses ``x = 16sin³t``, ``y = 13cos t - 5cos2t - 2cos3t - cos4t`` sampled
    densely and filled as a polygon, which gives a much better shape than the
    usual two-circles-and-a-triangle approximation.
    """
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    x0, y0, x1, y1 = _inset(width, height, 0.04)
    box_w, box_h = x1 - x0, y1 - y0

    points: list[tuple[float, float]] = []
    steps = 720
    for i in range(steps):
        t = 2 * math.pi * i / steps
        hx = 16 * math.sin(t) ** 3
        hy = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )
        # Curve spans roughly [-16, 16] and [-17, 12]; normalise to the box.
        nx = (hx + 16) / 32
        ny = 1 - (hy + 17) / 29
        points.append((x0 + nx * box_w, y0 + ny * box_h))

    draw.polygon(points, fill=255)
    return mask


def _ellipse_mask(width: int, height: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    # Largest centred circle, so the shape reads as a circle on any canvas.
    side = min(width, height)
    x0 = (width - side) // 2
    y0 = (height - side) // 2
    ImageDraw.Draw(mask).ellipse([x0, y0, x0 + side - 1, y0 + side - 1], fill=255)
    return mask


def _rounded_mask(width: int, height: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    x0, y0, x1, y1 = _inset(width, height, 0.015)
    radius = round(min(width, height) * 0.08)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=255)
    return mask


def _star_mask(width: int, height: int, points: int = 5) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    cx, cy = width / 2, height / 2
    outer = min(width, height) * 0.48
    inner = outer * 0.42
    verts: list[tuple[float, float]] = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = -math.pi / 2 + i * math.pi / points
        verts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    ImageDraw.Draw(mask).polygon(verts, fill=255)
    return mask


def _text_mask(width: int, height: int, text: str) -> Image.Image:
    """
    Cut ``text`` out of the canvas, scaled to fill it.

    The font size is found by measuring at a reference size and scaling, then
    verified once -- simpler and far faster than a binary search, and accurate
    enough since the glyphs are then centred on their real bounding box.
    """
    text = (text or "").strip()
    if not text:
        raise ShapeError("A text shape needs some text (e.g. '25' or 'A&B')")

    from core.album.textlayer import resolve_font  # reuse the font fallback chain

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    reference = 200
    font = resolve_font("sans_bold", reference)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    ref_w, ref_h = max(1, right - left), max(1, bottom - top)

    target_w, target_h = width * 0.94, height * 0.94
    scale = min(target_w / ref_w, target_h / ref_h)
    size = max(12, int(reference * scale))

    font = resolve_font("sans_bold", size)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    # Position from the measured bbox so the glyphs land centred regardless of
    # font ascent/descent quirks.
    x = (width - text_w) / 2 - left
    y = (height - text_h) / 2 - top
    draw.text((x, y), text, font=font, fill=255)

    if not mask.getbbox():
        raise ShapeError(
            f"Could not render the text shape {text!r} (no usable font found)"
        )
    return mask


def shape_coverage(mask: Image.Image) -> float:
    """
    Fraction of the canvas the shape keeps, in ``[0, 1]``.

    Useful for warning that a shape is so small the photos inside it will be
    unrecognisable.
    """
    import numpy as np

    return float((np.asarray(mask, dtype=np.uint8) > 127).mean())
