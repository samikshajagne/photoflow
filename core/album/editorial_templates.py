"""
Editorial theme templates — full-bleed, tight-gutter, no dead space.

The classic theme floats shaped photos (circle/diamond/thick borders) on a tinted
background, which leaves large empty voids and reads as clip-art. The **editorial**
theme instead tiles the whole spread with clean rectangular photos — a full-bleed
hero plus a tight-gutter grid — the way a hand-designed album is built. Paired with
:func:`core.album.decor.apply_decorations` for the gold hairline frame + corner
flourishes, it's the "designed" look.

Selected when the album's theme is ``"editorial"``; the renderer merges these into
the template pool (see `core.album.raster`).
"""

from __future__ import annotations

import dataclasses
from typing import List

from core.album.template import (
    SHAPE_ROUNDED,
    Background,
    SpreadTemplate,
    TemplateSlot,
)

THEME = "editorial"

# A clean page margin so photos don't reach the trim — leaves a band for the gold
# hairline frame + corner flourishes to sit in without crossing any photo.
_MARGIN = 0.032
_G = 0.012          # gutter between photos (fraction of usable area)
_BORDER = 0.004     # hairline white frame on inset photos
_WHITE = "#FFFFFF"
_RADIUS = 0.015     # barely-there rounding
# A light neutral page so any gutter/rounding reads clean (decor draws the frame).
_BG = Background(type="sampled", lighten=0.55)


def _photo(rect, *, hero: bool = False) -> TemplateSlot:
    """
    An editorial photo slot: full-bleed hero (no border) or bordered inset.

    The hero is marked ``use_cutout=True`` so that *if* the album enables cutouts
    (Album Settings → "Cut-out hero photos"), the hero becomes a subject cutout on
    the themed background — the reference "person on colour" look. With cutouts
    off (default) it renders as a normal full-bleed photo.
    """
    if hero:
        return TemplateSlot(rect=rect, shape=SHAPE_ROUNDED, corner_radius=0.0,
                            border=0.0, shadow=False, use_cutout=True)
    return TemplateSlot(rect=rect, shape=SHAPE_ROUNDED, corner_radius=_RADIUS,
                        border=_BORDER, border_color=_WHITE, shadow=True)


def _stack(x: float, w: float, n: int) -> List[TemplateSlot]:
    """``n`` inset photos stacked vertically in column ``[x, x+w]``."""
    h = (1.0 - _G * (n - 1)) / n
    return [_photo((x, i * (h + _G), w, h)) for i in range(n)]


def _grid(x0: float, cols: int, rows: int) -> List[TemplateSlot]:
    """A ``cols x rows`` inset grid filling ``[x0, 1] x [0, 1]``."""
    cw = (1.0 - x0 - _G * (cols - 1)) / cols
    ch = (1.0 - _G * (rows - 1)) / rows
    out: List[TemplateSlot] = []
    for r in range(rows):
        for c in range(cols):
            out.append(_photo((x0 + c * (cw + _G), r * (ch + _G), cw, ch)))
    return out


def _inset_rect(rect, m: float):
    x, y, w, h = rect
    return (m + x * (1 - 2 * m), m + y * (1 - 2 * m), w * (1 - 2 * m), h * (1 - 2 * m))


def _with_margin(t: SpreadTemplate, m: float) -> SpreadTemplate:
    """Inset every slot of ``t`` by margin ``m`` so nothing bleeds to the trim."""
    slots = tuple(dataclasses.replace(s, rect=_inset_rect(s.rect, m)) for s in t.slots)
    return dataclasses.replace(t, slots=slots)


def editorial_templates() -> List[SpreadTemplate]:
    """Editorial layouts for 1–6 photos (hero + tight grids), inset by a margin."""
    hero_w = 0.52
    right_x = hero_w + _G
    right_w = 1.0 - right_x
    templates = [
        SpreadTemplate(THEME + "-1", THEME, (_photo((0, 0, 1, 1), hero=True),), _BG),
        SpreadTemplate(
            THEME + "-2", THEME,
            (_photo((0, 0, 0.5 - _G / 2, 1), hero=True),
             _photo((0.5 + _G / 2, 0, 0.5 - _G / 2, 1))),
            _BG,
        ),
        SpreadTemplate(
            THEME + "-3", THEME,
            (_photo((0, 0, hero_w, 1), hero=True), *_stack(right_x, right_w, 2)),
            _BG,
        ),
        SpreadTemplate(
            THEME + "-4", THEME,
            (_photo((0, 0, hero_w, 1), hero=True), *_stack(right_x, right_w, 3)),
            _BG,
        ),
        SpreadTemplate(
            THEME + "-5", THEME,
            (_photo((0, 0, 0.5, 1), hero=True), *_grid(0.5 + _G, 2, 2)),
            _BG,
        ),
        SpreadTemplate(THEME + "-6", THEME, tuple(_grid(0.0, 3, 2)), _BG),
    ]
    return [_with_margin(t, _MARGIN) for t in templates]


__all__ = ["THEME", "editorial_templates"]
