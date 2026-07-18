"""
Album cover designer (WS 4.4).

Composes a finished album cover from a hero photo (ideally the couple), the
couple's names, the wedding date, and a themed colour: a tinted background, the
hero placed as a soft feathered cutout (via :mod:`core.album.face_segmenter`,
falling back to a rounded panel when no reliable face exists), and a centred
names/date/tagline block (via :func:`core.album.textlayer.draw_cover`).

Pure Pillow + the existing helpers, so it renders and tests without any art
assets or detection backend.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageOps

from core.album.face_segmenter import cutout_from_faces
from core.album.textlayer import draw_cover

RelRect = Tuple[float, float, float, float]
RGB = Tuple[int, int, int]

# Tagline libraries keyed by style; the caller picks a style or passes text.
TAGLINES = {
    "romantic_bollywood": (
        "Our Love Story",
        "Forever Begins Here",
        "Two Souls, One Journey",
    ),
    "elegant_western": (
        "Happily Ever After",
        "The Beginning of Forever",
        "Together, Always",
    ),
}
DEFAULT_SIZE = (5400, 3600)


def _default_loader(path: str) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
        return ImageOps.exif_transpose(img).convert("RGBA")
    except Exception:  # noqa: BLE001 - unreadable hero -> neutral placeholder
        return Image.new("RGBA", (1200, 1600), (210, 210, 210, 255))


def _tint(color: RGB, lighten: float = 0.55) -> RGB:
    t = max(0.0, min(1.0, lighten))
    return tuple(int(round(c * (1 - t) + 255 * t)) for c in color)  # type: ignore[return-value]


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale-to-fill then centre-crop ``img`` to ``w x h`` (preserves alpha)."""
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new(img.mode, (w, h))
    scale = max(w / iw, h / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _rounded_panel(img_rgb: Image.Image, radius_frac: float = 0.04) -> Image.Image:
    """Give an RGB tile soft rounded corners (returns RGBA)."""
    w, h = img_rgb.size
    tile = img_rgb.convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    r = max(1, round(min(w, h) * radius_frac))
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    tile.putalpha(mask)
    return tile


def _hero_tile(
    hero: Image.Image,
    face_boxes: Sequence[RelRect],
    w: int,
    h: int,
) -> Image.Image:
    """
    The hero as a placed tile: a feathered face cutout when a reliable face
    exists, else a rounded photo panel. Always returns an ``(w, h)`` RGBA tile.
    """
    if face_boxes:
        cut = cutout_from_faces(hero.convert("RGBA"), tuple(face_boxes))
        if cut is not None:
            return _cover(cut, w, h)
    return _rounded_panel(_cover(hero.convert("RGB"), w, h))


def generate_cover(
    hero_photo: str,
    couple_names: str,
    wedding_date: str = "",
    *,
    theme_color: RGB = (150, 40, 40),
    tagline: str = "",
    tagline_style: str = "romantic_bollywood",
    size: Tuple[int, int] = DEFAULT_SIZE,
    face_boxes: Sequence[RelRect] = (),
    loader: Optional[Callable[[str], Image.Image]] = None,
) -> Image.Image:
    """
    Compose and return the album cover as an RGB image.

    Args:
        hero_photo: Path to the hero (couple) photo.
        couple_names: e.g. ``"Aisha & Rohan"``.
        wedding_date: Printed under the names (any preformatted string).
        theme_color: Accent/background colour (e.g. from
            :func:`core.album.event_classifier.classify_event`).
        tagline: Explicit tagline; if empty, the first of ``tagline_style`` is used.
        tagline_style: Which :data:`TAGLINES` set to draw a default from.
        size: Cover pixel size (default a 5400x3600 spread).
        face_boxes: Relative face boxes for the hero, enabling a feathered cutout.
        loader: Image loader (defaults to a header-safe PIL read).

    Returns:
        The finished cover (RGB).
    """
    w, h = size
    canvas = Image.new("RGBA", size, (*_tint(theme_color), 255))

    # Hero occupies a centred panel in the upper ~55% of the cover.
    hero_w, hero_h = int(w * 0.46), int(h * 0.52)
    hx, hy = (w - hero_w) // 2, int(h * 0.05)
    hero = (loader or _default_loader)(hero_photo)
    tile = _hero_tile(hero, face_boxes, hero_w, hero_h)
    canvas.alpha_composite(tile, (hx, hy))

    # A slim themed rule just under the hero, for a designed feel.
    rule_y = hy + hero_h + max(6, round(min(w, h) * 0.015))
    rule_w = int(w * 0.22)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        [(w - rule_w) // 2, rule_y, (w + rule_w) // 2, rule_y + max(3, round(min(w, h) * 0.004))],
        fill=(*theme_color, 255),
    )

    # Names + date + tagline block (lower half).
    if not tagline:
        pool = TAGLINES.get(tagline_style) or TAGLINES["romantic_bollywood"]
        tagline = pool[0]
    return draw_cover(
        canvas.convert("RGB"),
        couple_names,
        wedding_date,
        subtitle=tagline,
        accent=theme_color,
    )


__all__ = ["generate_cover", "TAGLINES", "DEFAULT_SIZE"]
