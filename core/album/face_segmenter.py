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

import os
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from utils.logger import get_logger

logger = get_logger(__name__)

RelRect = Tuple[float, float, float, float]

# Below this face fraction of the frame the cutout is unreliable -> fall back.
_MIN_FACE_FRACTION = 0.01

# rembg model for true subject cutouts. BiRefNet-general is MIT-licensed (safe to
# ship commercially) and the best-quality option; override via env if wanted.
_REMBG_MODEL = os.environ.get("PHOTOFLOW_CUTOUT_MODEL", "birefnet-general")
_rembg_sessions: dict = {}
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


# --------------------------------------------------------------------------- #
# rembg subject cutout (true background removal)
# --------------------------------------------------------------------------- #
def rembg_available() -> bool:
    """True if the optional ``rembg`` package is importable."""
    try:
        import rembg  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - rembg is optional
        return False


def _rembg_remove(image: Image.Image, model_name: str) -> Image.Image:
    """
    Run rembg on ``image`` and return an RGBA cutout. Isolated (and reusing one
    cached session per model) so tests can monkeypatch it without rembg installed.
    """
    from rembg import new_session, remove

    session = _rembg_sessions.get(model_name)
    if session is None:
        session = new_session(model_name)
        _rembg_sessions[model_name] = session
    return remove(image.convert("RGB"), session=session).convert("RGBA")


def subject_cutout(
    image: Image.Image, *, model_name: Optional[str] = None, feather: float = 0.004
) -> Optional[Image.Image]:
    """
    A clean background-removed RGBA cutout of the subject(s) via rembg, or ``None``
    if rembg is unavailable / the result is unusable (so the caller can fall back
    to the head-and-shoulders ellipse).

    Unlike the ellipse method this keeps the *actual* silhouette (hair, sari,
    everyone in a group) and needs no face boxes — it's the studio-quality path.
    A tiny feather softens the matte edge.
    """
    if not rembg_available():
        return None
    try:
        rgba = _rembg_remove(image, model_name or _REMBG_MODEL)
    except Exception as exc:  # noqa: BLE001 - never break the render on a model error
        logger.warning("rembg cutout failed (%s); falling back.", exc)
        return None
    # Reject degenerate mattes (all-opaque = nothing removed, all-transparent = lost subject).
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    frac = float((alpha > 20).mean())
    if frac < 0.02 or frac > 0.995:
        return None
    if feather > 0:
        soft = feather_mask(rgba.getchannel("A"), feather)
        rgba.putalpha(soft)
    return rgba


def cutout_from_faces(
    image: Image.Image,
    face_boxes: Tuple[RelRect, ...],
    *,
    feather: float = _DEFAULT_FEATHER,
    use_rembg: bool = True,
) -> Optional[Image.Image]:
    """
    Cut ``image`` out around its subject(s), or ``None`` if that isn't reliable.

    Prefers a true rembg background removal (studio quality, whole silhouette) when
    rembg is installed and ``use_rembg`` is set; otherwise unions a feathered
    head-and-shoulders ellipse per face box. Returns RGBA, or ``None`` (the signal
    for the renderer to fall back to a rectangular/shape crop) when neither path
    yields a usable cutout.
    """
    if use_rembg:
        cut = subject_cutout(image)
        if cut is not None:
            return cut

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
