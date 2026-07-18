"""
Face / head-and-shoulders cutout masks (WS 3.3.1).

Produces a soft-edged alpha mask around a subject so a photo can be placed on a
spread as a *cutout* (feathered vignette) rather than a hard rectangle — the
signature editorial look. The roadmap calls for landmark convex hulls; until
per-face landmarks are wired through the pipeline, this derives a smooth
head-and-shoulders ellipse from the face bounding box, which is stable, cheap and
needs no extra model. When a face box is missing or too small to be reliable the
functions return ``None`` so the renderer falls back to a normal shape clip
(the roadmap's confidence fallback).

Pure Pillow/NumPy — renders and tests without any detection backend.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

RelRect = Tuple[float, float, float, float]

# Below this face fraction of the frame the cutout is unreliable -> fall back.
_MIN_FACE_FRACTION = 0.01
# Head-and-shoulders ellipse size relative to the face box.
_HEAD_W = 1.9   # shoulders are wider than the face
_HEAD_H_UP = 0.7    # headroom above the face (hair/forehead)
_HEAD_H_DOWN = 2.6  # neck + shoulders + chest below the face
_DEFAULT_FEATHER = 0.02  # feather radius as a fraction of the short edge


def segment_face_region(
    size: Tuple[int, int],
    face_box: RelRect,
    *,
    feather: float = _DEFAULT_FEATHER,
) -> Optional[Image.Image]:
    """
    A feathered ``L`` alpha mask covering the head and shoulders around ``face_box``.

    Args:
        size: ``(width, height)`` of the target image in pixels.
        face_box: Relative ``(x, y, w, h)`` face rectangle in ``[0, 1]``.
        feather: Edge softness as a fraction of the image's short edge.

    Returns:
        An ``L`` mask (255 = opaque subject, 0 = transparent), or ``None`` when the
        face is too small/degenerate to cut out reliably (caller should fall back).
    """
    w, h = int(size[0]), int(size[1])
    if w <= 1 or h <= 1:
        return None
    fx, fy, fw, fh = face_box
    if fw <= 0 or fh <= 0 or (fw * fh) < _MIN_FACE_FRACTION:
        return None

    # Face box centre and size in pixels.
    cx = (fx + fw / 2.0) * w
    cy = (fy + fh / 2.0) * h
    box_w = fw * w
    box_h = fh * h

    # Head-and-shoulders ellipse: wider than the face, taller below than above.
    ell_w = box_w * _HEAD_W
    top = cy - box_h * (0.5 + _HEAD_H_UP)
    bottom = cy + box_h * (0.5 + _HEAD_H_DOWN)
    left = cx - ell_w / 2.0
    right = cx + ell_w / 2.0

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse([left, top, right, bottom], fill=255)

    return feather_mask(mask, feather)


def feather_mask(mask: Image.Image, feather: float = _DEFAULT_FEATHER) -> Image.Image:
    """Soften a mask's edge with a Gaussian blur (fraction of the short edge)."""
    w, h = mask.size
    radius = max(1, round(min(w, h) * max(0.0, feather)))
    return mask.filter(ImageFilter.GaussianBlur(radius))


def apply_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    """
    Return ``image`` as RGBA with ``mask`` as its alpha channel (the cutout).

    The mask is resized to the image if needed, so callers can compute a mask at
    any resolution and apply it to the full-size photo.
    """
    rgba = image.convert("RGBA")
    if mask.size != rgba.size:
        mask = mask.resize(rgba.size, Image.BILINEAR)
    rgba.putalpha(mask.convert("L"))
    return rgba


def cutout_from_faces(
    image: Image.Image,
    face_boxes: Tuple[RelRect, ...],
    *,
    feather: float = _DEFAULT_FEATHER,
) -> Optional[Image.Image]:
    """
    Cut ``image`` out around all its faces, or ``None`` if none are reliable.

    Masks for every usable face are unioned (max), so a couple both stay in the
    cutout. Returns an RGBA image, or ``None`` when no face is large enough — the
    signal for the renderer to fall back to a rectangular/shape crop.
    """
    if not face_boxes:
        return None
    w, h = image.size
    union: Optional[Image.Image] = None
    for box in face_boxes:
        m = segment_face_region((w, h), box, feather=feather)
        if m is None:
            continue
        if union is None:
            union = m
        else:
            union = Image.fromarray(
                np.maximum(np.asarray(union), np.asarray(m)).astype("uint8"), "L"
            )
    if union is None:
        return None
    return apply_mask(image, union)
