"""
Bridge from the flexible-layout engine to the renderer (WS 4.1 integration).

:func:`flexible_template_for` turns a spread's photos into a concrete, content-
adapted :class:`~core.album.template.SpreadTemplate` — the drop-in replacement
for the fixed ``select_template`` call in :func:`core.album.raster.render_spread_template`
when the album opts into flexible layouts. It analyses each photo's composition,
lets :mod:`core.album.flexible_template` choose and position slot types, and
returns the template (or ``None`` so the caller falls back to a fixed template).

Kept as a thin, separately-tested module so the large render file only needs a
one-line, guarded call.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

RelRect = Tuple[float, float, float, float]


def _header_aspect(path: str) -> float:
    """Width/height from the image header (EXIF-aware), 1.0 if unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
            orientation = img.getexif().get(0x0112, 1)
        if orientation in (5, 6, 7, 8):
            w, h = h, w
        if w > 0 and h > 0:
            return float(w) / float(h)
    except Exception:  # noqa: BLE001 - unreadable -> square fallback
        pass
    return 1.0


def flexible_template_for(
    paths: Sequence[str],
    faces_by_path: Dict[str, Sequence[RelRect]],
    theme: str,
    *,
    aspect_fn: Optional[Callable[[str], float]] = None,
):
    """
    Build a content-adapted :class:`SpreadTemplate` for ``paths``, or ``None``.

    Args:
        paths: Source photo paths for this spread, in placement order.
        faces_by_path: ``path -> relative face boxes`` (from the layout stage).
        theme: Album theme string, copied onto the produced template.
        aspect_fn: How to read a photo's aspect ratio (defaults to a header read).

    Returns:
        A :class:`~core.album.template.SpreadTemplate` whose slot *types* and
        positions were chosen to fit these photos, or ``None`` on any failure so
        the caller can fall back to a fixed template.
    """
    if not paths:
        return None
    aspect = aspect_fn or _header_aspect
    try:
        from core.content_analyzer import analyze
        from core.album.flexible_template import (
            build_spread_template,
            default_flexible_spread,
        )

        contents = [analyze(aspect(p), faces_by_path.get(p, ())) for p in paths]
        spread = default_flexible_spread(
            name=f"adaptive-{len(paths)}", theme=theme, slots_to_fill=len(paths)
        )
        template = build_spread_template(contents, spread)
        # Only use it when it covers every photo (else the fixed template is safer).
        if template.photo_count == len(paths):
            return template
        logger.debug(
            "Flexible template covered %d/%d slots; using fixed template.",
            template.photo_count,
            len(paths),
        )
    except Exception as exc:  # noqa: BLE001 - never break rendering
        logger.debug("Flexible layout unavailable (%s); using fixed template.", exc)
    return None


__all__ = ["flexible_template_for"]
