"""
Spread layout computation for variable-slot spreads (WS 4.1.3).

Once a spread has chosen which slot *types* to use, it needs actual positions on
the canvas. This maps an ordered list of slot orientations to relative rectangles
``(x, y, w, h)`` in ``[0, 1]`` of the spread's usable area. Common arrangements
are pre-designed in :data:`LAYOUT_PATTERNS` (a portrait hero beside a stack of
squares, two stacked widescreens, …); anything without an exact pattern falls
back to :func:`compute_dynamic_layout`, a balanced hero-or-grid arrangement so
any slot count still renders.

Orientations are the strings from :mod:`core.content_analyzer`
(``"portrait"`` / ``"landscape"`` / ``"square"``).
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

from core.content_analyzer import ORIENT_LANDSCAPE, ORIENT_PORTRAIT, ORIENT_SQUARE

Rect = Tuple[float, float, float, float]

_G = 0.02  # gutter between cells, fraction of the usable area


def _stack(x: float, w: float, n: int, y0: float = 0.0, y1: float = 1.0) -> list[Rect]:
    """``n`` rectangles stacked vertically within column ``[x, x+w]``."""
    h = (y1 - y0 - _G * (n - 1)) / n
    return [(x, y0 + i * (h + _G), w, h) for i in range(n)]


def _row(y: float, h: float, n: int, x0: float = 0.0, x1: float = 1.0) -> list[Rect]:
    """``n`` rectangles in a row within band ``[y, y+h]``."""
    w = (x1 - x0 - _G * (n - 1)) / n
    return [(x0 + i * (w + _G), y, w, h) for i in range(n)]


# Pre-designed layouts, keyed by the tuple of slot orientations.
LAYOUT_PATTERNS: dict[Tuple[str, ...], list[Rect]] = {
    (ORIENT_PORTRAIT,): [(0.0, 0.0, 1.0, 1.0)],
    (ORIENT_LANDSCAPE,): [(0.0, 0.0, 1.0, 1.0)],
    (ORIENT_SQUARE,): [(0.0, 0.0, 1.0, 1.0)],
    # Two portraits side by side.
    (ORIENT_PORTRAIT, ORIENT_PORTRAIT): _row(0.0, 1.0, 2),
    # Two widescreens stacked.
    (ORIENT_LANDSCAPE, ORIENT_LANDSCAPE): _stack(0.0, 1.0, 2),
    # Portrait hero on the left, two squares stacked on the right.
    (ORIENT_PORTRAIT, ORIENT_SQUARE, ORIENT_SQUARE): [
        (0.0, 0.0, 0.56, 1.0),
        *_stack(0.58, 0.42, 2),
    ],
    # Portrait hero + three-square stack.
    (ORIENT_PORTRAIT, ORIENT_SQUARE, ORIENT_SQUARE, ORIENT_SQUARE): [
        (0.0, 0.0, 0.52, 1.0),
        *_stack(0.54, 0.46, 3),
    ],
    # Wide banner on top, two squares below.
    (ORIENT_LANDSCAPE, ORIENT_SQUARE, ORIENT_SQUARE): [
        (0.0, 0.0, 1.0, 0.52),
        *_row(0.54, 0.46, 2),
    ],
    # Square + wide-centre + square (wide anchors the middle).
    (ORIENT_SQUARE, ORIENT_LANDSCAPE, ORIENT_SQUARE): [
        (0.0, 0.0, 0.28, 1.0),
        (0.30, 0.18, 0.40, 0.64),
        (0.72, 0.0, 0.28, 1.0),
    ],
}


def get_layout_positions(orientations: Sequence[str]) -> list[Rect]:
    """
    Relative slot rectangles for these ``orientations``.

    Uses an exact :data:`LAYOUT_PATTERNS` entry when one exists, else computes a
    balanced layout dynamically. Always returns exactly ``len(orientations)``
    rectangles, each inside ``[0, 1]``.
    """
    key = tuple(orientations)
    if key in LAYOUT_PATTERNS:
        return list(LAYOUT_PATTERNS[key])
    return compute_dynamic_layout(orientations)


def compute_dynamic_layout(orientations: Sequence[str]) -> list[Rect]:
    """
    A balanced fallback layout for any slot count.

    If a portrait or landscape "hero" leads the list it anchors a large panel and
    the rest fill a stack/row beside or below it; otherwise the slots tile a
    near-square grid. Deterministic for a given input.
    """
    n = len(orientations)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 1.0, 1.0)]

    first = orientations[0]
    if first == ORIENT_PORTRAIT:
        # Left hero + vertical stack of the remainder.
        return [(0.0, 0.0, 0.54, 1.0), *_stack(0.56, 0.44, n - 1)]
    if first == ORIENT_LANDSCAPE:
        # Top hero banner + row of the remainder.
        return [(0.0, 0.0, 1.0, 0.54), *_row(0.56, 0.44, n - 1)]

    # No hero: near-square grid.
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    cell_w = (1.0 - _G * (cols - 1)) / cols
    cell_h = (1.0 - _G * (rows - 1)) / rows
    rects: list[Rect] = []
    for i in range(n):
        r, c = divmod(i, cols)
        rects.append((c * (cell_w + _G), r * (cell_h + _G), cell_w, cell_h))
    return rects


def rects_within_bounds(rects: Sequence[Rect], eps: float = 1e-6) -> bool:
    """True if every rectangle sits inside the unit square with positive size."""
    for x, y, w, h in rects:
        if w <= 0 or h <= 0:
            return False
        if x < -eps or y < -eps or x + w > 1 + eps or y + h > 1 + eps:
            return False
    return True


__all__ = [
    "Rect",
    "LAYOUT_PATTERNS",
    "get_layout_positions",
    "compute_dynamic_layout",
    "rects_within_bounds",
]
