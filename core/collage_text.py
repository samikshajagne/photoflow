"""
Text and studio-branding overlays for collages.

Two things studios ask for on every deliverable: a title (couple's names, event,
date) and their own logo or watermark. Both are rendered here onto a finished
collage canvas.

Fonts come from :func:`core.album.textlayer.resolve_font`, which already walks a
bundled -> system -> Pillow-default chain. Reusing it keeps font *licensing*
decisions in one place (see the licensing notes in the product plan: shipping a
font requires a commercial licence, so PhotoFlow prefers bundled-or-system
fonts over embedding arbitrary files).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional, Sequence, Union

from PIL import Image, ImageDraw

from utils.logger import get_logger

logger = get_logger(__name__)

RGB = tuple[int, int, int]
PathLike = Union[str, Path]

# Anchor positions shared by text overlays and watermarks.
POS_TOP_LEFT = "top-left"
POS_TOP_CENTER = "top-center"
POS_TOP_RIGHT = "top-right"
POS_CENTER = "center"
POS_BOTTOM_LEFT = "bottom-left"
POS_BOTTOM_CENTER = "bottom-center"
POS_BOTTOM_RIGHT = "bottom-right"
POSITIONS: tuple[str, ...] = (
    POS_TOP_LEFT, POS_TOP_CENTER, POS_TOP_RIGHT,
    POS_CENTER,
    POS_BOTTOM_LEFT, POS_BOTTOM_CENTER, POS_BOTTOM_RIGHT,
)

FONT_ROLES: tuple[str, ...] = ("serif", "serif_italic", "sans_bold", "script")


class CollageTextError(Exception):
    """Raised when text or a watermark cannot be rendered."""


@dataclasses.dataclass(frozen=True)
class TextOverlay:
    """
    One line (or block) of text drawn on the collage.

    Sizes and offsets are fractions of the canvas so the same overlay looks
    identical on a small preview and a full-resolution export.

    Attributes:
        text: The string to draw. Blank overlays are skipped.
        position: One of :data:`POSITIONS`.
        size_frac: Font size as a fraction of the canvas short edge.
        color: Text colour.
        font_role: A role understood by ``core.album.textlayer.resolve_font``.
        font_path: An explicit font file, which wins over ``font_role`` when set.
        opacity: ``0..255``.
        margin_frac: Distance from the anchored edge, as a fraction of the
            short edge.
        offset_x_frac: Extra horizontal nudge (fraction of width).
        offset_y_frac: Extra vertical nudge (fraction of height).
        shadow: Draw a soft dark shadow behind the text so it stays readable
            over a busy photo.
    """

    text: str
    position: str = POS_BOTTOM_CENTER
    size_frac: float = 0.06
    color: RGB = (255, 255, 255)
    font_role: str = "serif"
    font_path: Optional[PathLike] = None
    opacity: int = 255
    margin_frac: float = 0.035
    offset_x_frac: float = 0.0
    offset_y_frac: float = 0.0
    shadow: bool = True


@dataclasses.dataclass(frozen=True)
class Watermark:
    """
    A studio logo composited onto the collage.

    Attributes:
        image_path: The logo file. PNG with transparency works best.
        position: One of :data:`POSITIONS`.
        width_frac: Logo width as a fraction of the canvas width.
        opacity: ``0..255``.
        margin_frac: Distance from the anchored edge (fraction of short edge).
    """

    image_path: PathLike
    position: str = POS_BOTTOM_RIGHT
    width_frac: float = 0.16
    opacity: int = 180
    margin_frac: float = 0.03


def _resolve_font(role: str, size: int, font_path: Optional[PathLike]):
    """An explicit font file if given and loadable, else the shared fallback."""
    size = max(8, int(size))
    if font_path:
        try:
            from PIL import ImageFont

            return ImageFont.truetype(str(font_path), size)
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail
            logger.warning(
                "Could not load font '%s' (%s); falling back to role %r.",
                font_path, exc, role,
            )
    from core.album.textlayer import resolve_font

    return resolve_font(role, size)


def _anchor(
    position: str,
    canvas: tuple[int, int],
    item: tuple[int, int],
    margin: int,
) -> tuple[int, int]:
    """Top-left pixel for an ``item`` box anchored at ``position``."""
    if position not in POSITIONS:
        raise CollageTextError(
            f"Unknown position {position!r}; expected one of {POSITIONS}"
        )
    canvas_w, canvas_h = canvas
    item_w, item_h = item

    if "left" in position:
        x = margin
    elif "right" in position:
        x = canvas_w - item_w - margin
    else:
        x = (canvas_w - item_w) // 2

    if position.startswith("top"):
        y = margin
    elif position.startswith("bottom"):
        y = canvas_h - item_h - margin
    else:
        y = (canvas_h - item_h) // 2
    return x, y


def draw_text_overlays(
    image: Image.Image, overlays: Sequence[TextOverlay], spec
) -> Image.Image:
    """
    Draw each overlay onto a copy of ``image``.

    ``spec`` is a :class:`core.collage.CollageSpec`; only its ``short_edge`` is
    used, to keep sizing resolution-independent.
    """
    if not overlays:
        return image
    canvas = image.convert("RGBA")
    short_edge = spec.short_edge

    for overlay in overlays:
        text = (overlay.text or "").strip()
        if not text:
            continue
        font = _resolve_font(
            overlay.font_role, round(overlay.size_frac * short_edge), overlay.font_path
        )
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font)
        text_w, text_h = max(1, right - left), max(1, bottom - top)
        margin = round(overlay.margin_frac * short_edge)
        x, y = _anchor(overlay.position, canvas.size, (text_w, text_h), margin)
        x += round(overlay.offset_x_frac * canvas.width) - left
        y += round(overlay.offset_y_frac * canvas.height) - top

        alpha = max(0, min(255, int(overlay.opacity)))
        if overlay.shadow:
            # Offset dark copy first: keeps light text legible over a photo.
            blur_off = max(1, round(0.0025 * short_edge))
            draw.multiline_text(
                (x + blur_off, y + blur_off), text, font=font,
                fill=(0, 0, 0, int(alpha * 0.55)), align="center",
            )
        draw.multiline_text(
            (x, y), text, font=font, fill=(*overlay.color, alpha), align="center"
        )
        canvas.alpha_composite(layer)

    return canvas.convert("RGB")


def draw_watermark(image: Image.Image, watermark: Watermark, spec) -> Image.Image:
    """
    Composite a studio logo onto a copy of ``image``.

    Raises:
        CollageTextError: if the logo file can't be opened.
    """
    try:
        with Image.open(watermark.image_path) as opened:
            opened.load()
            logo = opened.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise CollageTextError(
            f"Could not open watermark image '{watermark.image_path}': {exc}"
        ) from exc

    target_w = max(1, round(watermark.width_frac * image.width))
    scale = target_w / logo.width
    logo = logo.resize(
        (target_w, max(1, round(logo.height * scale))), Image.LANCZOS
    )

    opacity = max(0, min(255, int(watermark.opacity)))
    if opacity < 255:
        faded = logo.getchannel("A").point(lambda v: int(v * opacity / 255))
        logo.putalpha(faded)

    canvas = image.convert("RGBA")
    margin = round(watermark.margin_frac * spec.short_edge)
    position = _anchor(watermark.position, canvas.size, logo.size, margin)
    canvas.alpha_composite(logo, dest=(max(0, position[0]), max(0, position[1])))
    return canvas.convert("RGB")
