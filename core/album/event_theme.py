"""
Event → colour palette for themed spread backgrounds (WS 4.3.3).

Turns a classified event (from :mod:`core.album.event_classifier`) into a coherent
background tint + accent, so a Haldi spread reads warm turmeric-yellow and a Mehndi
spread reads green — instead of the muddy average of whatever happened to be in the
frame. Only confident, recognisably-themed events are recoloured; everything else
returns ``None`` so the renderer keeps its existing sampled-colour tint.

Pure colour maths over :mod:`core.album.event_classifier`; no assets, fully testable.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.album.event_classifier import (
    BARAAT,
    HALDI,
    MEHNDI,
    RECEPTION,
    classify_event,
)

RGB = Tuple[int, int, int]

# Canonical palette per event: a saturated base colour (tinted light for the page
# background) and a deeper accent used for rules/section text.
EVENT_PALETTES: dict[str, dict[str, RGB]] = {
    HALDI: {"base": (245, 205, 60), "accent": (176, 120, 20)},      # turmeric yellow / gold
    MEHNDI: {"base": (70, 160, 80), "accent": (34, 96, 46)},        # henna green
    BARAAT: {"base": (196, 52, 48), "accent": (120, 24, 24)},       # festive red
    RECEPTION: {"base": (208, 168, 92), "accent": (150, 110, 40)},  # elegant gold
}

# Only recolour when the classifier is at least this confident.
MIN_CONFIDENCE = 0.5


def _tint(color: RGB, lighten: float) -> RGB:
    t = max(0.0, min(1.0, lighten))
    return tuple(int(round(c * (1 - t) + 255 * t)) for c in color)  # type: ignore[return-value]


def themed_background(
    dominant_rgb: RGB,
    *,
    lighten: float = 0.62,
    min_confidence: float = MIN_CONFIDENCE,
) -> Optional[Tuple[RGB, RGB]]:
    """
    Themed ``(background_tint, accent)`` for a section's dominant colour, or ``None``.

    Classifies ``dominant_rgb`` into an event type; for a confident Haldi / Mehndi /
    Baraat / Reception it returns a light tint of that event's canonical colour plus
    a deeper accent. Ceremony / Portraits (or low confidence) return ``None`` so the
    caller falls back to the raw sampled tint.
    """
    result = classify_event(dominant_rgb)
    palette = EVENT_PALETTES.get(result.event_type)
    if palette is None or result.confidence < min_confidence:
        return None
    return _tint(palette["base"], lighten), palette["accent"]


def themed_background_hex(dominant_rgb: RGB, **kwargs) -> Optional[Tuple[str, str]]:
    """As :func:`themed_background`, but returns ``(#bg, #accent)`` hex strings."""
    pair = themed_background(dominant_rgb, **kwargs)
    if pair is None:
        return None
    (br, bg, bb), (ar, ag, ab) = pair
    return f"#{br:02X}{bg:02X}{bb:02X}", f"#{ar:02X}{ag:02X}{ab:02X}"


__all__ = ["EVENT_PALETTES", "themed_background", "themed_background_hex", "MIN_CONFIDENCE"]
