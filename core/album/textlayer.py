"""
Text overlays for album spreads — titles + romantic quotes (Phase 4, B4).

Designed wedding albums caption their section openers ("Haldi", a line like
"A true love story never ends"). This module draws that text legibly over any
photo by placing it on a soft translucent plate, using bundled fonts so it
works on any OS with no setup. Script/wedding fonts can be dropped into
``data/fonts`` later and selected by name.

Pure Pillow — renders and tests without the detection backends.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "fonts"

# Bundled fonts by role (redistributable DejaVu). Missing files fall back to
# system fonts, then to Pillow's built-in bitmap font — text never fails.
_BUNDLED = {
    "title": "DejaVuSerif-Bold.ttf",
    "serif": "Lora.ttf",
    "quote": "Lora-Italic.ttf",
    # Drop a real calligraphy font in as data/fonts/Script.ttf to enable it;
    # otherwise the elegant Lora italic is used (see fallbacks below).
    "script": "Script.ttf",
    "sans_bold": "DejaVuSans-Bold.ttf",
}
_SYSTEM_FALLBACKS = {
    "title": ["Lora.ttf", "Georgia Bold.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
    "serif": ["Lora.ttf", "Georgia.ttf", "DejaVuSerif.ttf"],
    "quote": ["Lora-Italic.ttf", "Georgia Italic.ttf", "DejaVuSerif-Italic.ttf"],
    "script": [
        "GreatVibes-Regular.ttf", "DancingScript-Regular.ttf", "Allura-Regular.ttf",
        "Lora-Italic.ttf", "DejaVuSerif-Italic.ttf",
    ],
    "sans_bold": ["Arial Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
}

# A small curated library of short, tasteful captions.
QUOTES: tuple[str, ...] = (
    "A true love story never ends",
    "You are my heart, my life",
    "Two souls, one heart",
    "Where forever begins",
    "Happily ever after",
    "The best is yet to come",
    "Every love story is beautiful, but ours is my favourite",
    "Held hands, whole hearts",
)


def resolve_font(role: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font for ``role`` at ``size`` (bundled → system → default)."""
    size = max(8, int(size))
    names = [_BUNDLED.get(role, "DejaVuSerif.ttf"), *_SYSTEM_FALLBACKS.get(role, [])]
    for name in names:
        if not name:
            continue
        # Try each candidate first from the bundled data/fonts dir, then as a
        # bare name (so system-installed fonts resolve too).
        for cand in (str(_FONT_DIR / name), name):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # older Pillow: load_default takes no size
        return ImageFont.load_default()


def title_for_section(section: str) -> str:
    """A display title for a section name."""
    return (section or "").strip().upper()


def pick_quote(key: str, quotes: Sequence[str] = QUOTES) -> str:
    """Deterministically pick a quote for ``key`` (stable across re-renders)."""
    if not quotes:
        return ""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return quotes[int(digest, 16) % len(quotes)]


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def draw_caption(
    image: Image.Image,
    title: str,
    quote: Optional[str] = None,
    *,
    accent: tuple[int, int, int] = (150, 40, 40),
) -> Image.Image:
    """
    Draw ``title`` (and optional ``quote``) on a soft translucent plate in the
    lower-left of ``image``, and return a new RGB image.

    Sizing is relative to the spread's short edge, so it scales with resolution.
    The plate keeps the text legible over any photo; a short accent rule sits
    under the title.
    """
    base = image.convert("RGBA")
    w, h = base.size
    short = min(w, h)
    pad = max(10, round(short * 0.022))
    margin = max(12, round(short * 0.03))

    title_font = resolve_font("title", round(short * 0.050))
    quote_font = resolve_font("quote", round(short * 0.032))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    tw, th = _text_size(draw, title, title_font) if title else (0, 0)
    qw, qh = _text_size(draw, quote, quote_font) if quote else (0, 0)
    gap = round(pad * 0.5)
    rule_h = max(2, round(short * 0.004))

    content_w = max(tw, qw)
    content_h = th + (gap + rule_h) + ((gap + qh) if quote else 0)
    plate_w = content_w + pad * 2
    plate_h = content_h + pad * 2

    x0, y1 = margin, h - margin
    y0, x1 = y1 - plate_h, margin + plate_w
    radius = max(6, round(short * 0.015))
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(255, 255, 255, 210))

    tx, ty = x0 + pad, y0 + pad
    if title:
        draw.text((tx, ty), title, font=title_font, fill=(43, 43, 43, 255))
        ty += th + gap
        draw.rectangle([tx, ty, tx + max(tw, round(short * 0.10)), ty + rule_h],
                       fill=(*accent, 255))
        ty += rule_h + gap
    if quote:
        draw.text((tx, ty), quote, font=quote_font, fill=(90, 90, 90, 255))

    return Image.alpha_composite(base, overlay).convert("RGB")


def draw_cover(
    image: Image.Image,
    title: str,
    date: str = "",
    subtitle: str = "",
    *,
    accent: tuple[int, int, int] = (150, 40, 40),
) -> Image.Image:
    """
    Draw a centred cover title block (names + date + optional subtitle) on a
    soft plate in the lower half of ``image``. Returns a new RGB image.

    Used for the album's Cover spread — larger and centred, versus the smaller
    lower-left caption on interior openers.
    """
    base = image.convert("RGBA")
    w, h = base.size
    short = min(w, h)
    pad = max(14, round(short * 0.04))
    gap = max(8, round(short * 0.02))
    rule_h = max(3, round(short * 0.006))

    title_font = resolve_font("title", round(short * 0.085))
    date_font = resolve_font("serif", round(short * 0.035))
    sub_font = resolve_font("script", round(short * 0.055))  # calligraphy flourish

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    tw, th = _text_size(draw, title, title_font) if title else (0, 0)
    dw, dh = _text_size(draw, date, date_font) if date else (0, 0)
    sw, sh = _text_size(draw, subtitle, sub_font) if subtitle else (0, 0)

    content_w = max(tw, dw, sw)
    content_h = th + gap + rule_h + ((gap + dh) if date else 0) + ((gap + sh) if subtitle else 0)
    plate_w = content_w + pad * 2
    plate_h = content_h + pad * 2

    x0 = (w - plate_w) // 2
    y0 = min(round(h * 0.58), h - plate_h - round(short * 0.04))
    radius = max(8, round(short * 0.02))
    draw.rounded_rectangle([x0, y0, x0 + plate_w, y0 + plate_h], radius=radius, fill=(255, 255, 255, 210))

    cx = w // 2
    y = y0 + pad
    if title:
        draw.text((cx, y), title, font=title_font, fill=(40, 40, 40, 255), anchor="ma")
        y += th + gap
    rule_w = max(tw, round(short * 0.16))
    draw.rectangle([cx - rule_w // 2, y, cx + rule_w // 2, y + rule_h], fill=(*accent, 255))
    y += rule_h + gap
    if date:
        draw.text((cx, y), date, font=date_font, fill=(80, 80, 80, 255), anchor="ma")
        y += dh + gap
    if subtitle:
        draw.text((cx, y), subtitle, font=sub_font, fill=(*accent, 255), anchor="ma")

    return Image.alpha_composite(base, overlay).convert("RGB")
