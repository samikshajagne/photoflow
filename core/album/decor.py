"""
Procedural spread decoration (editorial polish, no art assets).

The single biggest gap between PhotoFlow's output and a hand-designed album is
*decoration*: the tasteful hairline frames, corner flourishes, dividers and
section titles that make a page read as "designed" instead of "a collage on a
tinted background". This module draws those elements programmatically with Pillow
— so it works with zero art assets — and also loads real PNG overlays from
``data/themes/<theme>/decorations/`` when a designer supplies them (those always
win over the procedural version).

Everything is drawn onto a transparent overlay and alpha-composited over a
finished spread, so it never disturbs the photos underneath.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

RGB = Tuple[int, int, int]

_ASSET_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "themes"


def apply_decorations(
    spread: Image.Image,
    *,
    theme: str = "classic",
    accent: RGB = (150, 40, 40),
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    frame: bool = True,
    corners: bool = True,
    divider: bool = False,
) -> Image.Image:
    """
    Composite decorations over ``spread`` and return a new RGB image.

    Args:
        spread: The finished RGB spread.
        theme: Theme name (used to look up PNG overlays under ``data/themes``).
        accent: Line/ornament colour (usually the event's themed accent).
        title / subtitle: Optional section caption drawn bottom-centre.
        frame: Draw a hairline inset border around the trim.
        corners: Draw a flourish in each corner.
        divider: Draw a centred ornamental divider near the bottom.
    """
    base = spread.convert("RGBA")
    w, h = base.size
    short = min(w, h)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Real PNG assets (if a designer provided them) take precedence.
    assets = _load_theme_assets(theme, base.size)
    if assets is not None:
        base = Image.alpha_composite(base, assets)
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

    if frame:
        _hairline_frame(draw, w, h, accent, short)
    if corners:
        c = max(1, round(short * 0.055))
        _corner_flourish(draw, (0, 0), c, accent, short, quadrant=0)
        _corner_flourish(draw, (w, 0), c, accent, short, quadrant=1)
        _corner_flourish(draw, (0, h), c, accent, short, quadrant=2)
        _corner_flourish(draw, (w, h), c, accent, short, quadrant=3)
    if divider:
        _divider(draw, w // 2, round(h * 0.93), round(short * 0.16), accent, short)

    out = Image.alpha_composite(base, overlay)

    if title:
        try:
            from core.album import textlayer as _tl

            out = _tl.draw_caption(
                out.convert("RGB"), title, subtitle or "", accent=accent
            ).convert("RGBA")
        except Exception:  # noqa: BLE001 - caption is best-effort
            pass

    return out.convert("RGB")


# --------------------------------------------------------------------------- #
# Procedural elements
# --------------------------------------------------------------------------- #
def _hairline_frame(draw, w, h, color: RGB, short: int) -> None:
    """A double hairline border inset from the trim — the classic album frame."""
    inset = round(short * 0.018)
    gap = max(2, round(short * 0.006))
    line = max(1, round(short * 0.0016))
    a = (*color, 200)
    draw.rectangle([inset, inset, w - 1 - inset, h - 1 - inset], outline=a, width=line)
    draw.rectangle(
        [inset + gap, inset + gap, w - 1 - inset - gap, h - 1 - inset - gap],
        outline=(*color, 120),
        width=line,
    )


def _corner_flourish(draw, corner, size: int, color: RGB, short: int, quadrant: int) -> None:
    """
    A small elegant corner ornament: two nested quarter-arcs plus a dot, oriented
    into the page from ``corner``. ``quadrant`` 0=TL, 1=TR, 2=BL, 3=BR.
    """
    cx, cy = corner
    off = round(short * 0.02)
    # Direction into the page for each corner.
    dx = 1 if quadrant in (0, 2) else -1
    dy = 1 if quadrant in (0, 1) else -1
    ox, oy = cx + dx * off, cy + dy * off
    line = max(1, round(short * 0.0018))
    a = (*color, 210)
    # Two nested arcs sweeping the 90° that opens into the page.
    base_ang = {0: 0, 1: 90, 2: 270, 3: 180}[quadrant]
    for r in (size, round(size * 0.62)):
        box = [ox - r, oy - r, ox + r, oy + r]
        draw.arc(box, base_ang, base_ang + 90, fill=a, width=line)
    # A small filled dot at the arc origin.
    d = max(2, round(short * 0.004))
    draw.ellipse([ox - d, oy - d, ox + d, oy + d], fill=a)


def _divider(draw, cx: int, y: int, half_w: int, color: RGB, short: int) -> None:
    """A thin centred rule with a small diamond in the middle."""
    line = max(1, round(short * 0.0016))
    a = (*color, 200)
    dgap = round(short * 0.012)
    draw.line([cx - half_w, y, cx - dgap, y], fill=a, width=line)
    draw.line([cx + dgap, y, cx + half_w, y], fill=a, width=line)
    s = max(2, round(short * 0.006))
    draw.polygon([(cx, y - s), (cx + s, y), (cx, y + s), (cx - s, y)], fill=a)


# --------------------------------------------------------------------------- #
# Optional real assets
# --------------------------------------------------------------------------- #
def _load_theme_assets(theme: str, size: Tuple[int, int]) -> Optional[Image.Image]:
    """
    Overlay composed from PNGs in ``data/themes/<theme>/decorations/`` (each
    stretched to the spread), or ``None`` if that folder is absent/empty. Lets a
    designer drop in real florals/borders without any code change.
    """
    folder = _ASSET_ROOT / theme / "decorations"
    if not folder.is_dir():
        return None
    pngs = sorted(folder.glob("*.png"))
    if not pngs:
        return None
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    for p in pngs:
        try:
            layer = Image.open(p).convert("RGBA").resize(size, Image.LANCZOS)
        except Exception:  # noqa: BLE001 - a bad asset is skipped
            continue
        overlay = Image.alpha_composite(overlay, layer)
    return overlay


__all__ = ["apply_decorations"]
