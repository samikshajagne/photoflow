"""
Event classification for Indian wedding albums (WS 4.3.1).

Timeline segmentation (:func:`core.timeline.segment_events`) splits a shoot into
chronological events; this module gives each event a *type* — Haldi, Mehndi,
Baraat, Reception, Ceremony or Portraits — from its dominant colour mood. The
type then drives theme colours and (in Phase 4) decorative assets.

This extends the previous colour-name heuristic (which only recognised the
turmeric-yellow Haldi) to the full ceremony set, returning a confidence so the
caller can fall back to a neutral chronological name when unsure.

Pure Pillow/NumPy via :mod:`core.album.theming`; no detection backends needed.
"""

from __future__ import annotations

import colorsys
import dataclasses
from typing import Optional, Sequence, Tuple

from core.album.theming import RGB, dominant_color, to_hex

# Event type constants.
HALDI = "Haldi"
MEHNDI = "Mehndi"
BARAAT = "Baraat"
RECEPTION = "Reception"
CEREMONY = "Ceremony"
PORTRAITS = "Portraits"

EVENT_TYPES = frozenset({HALDI, MEHNDI, BARAAT, RECEPTION, CEREMONY, PORTRAITS})

# Minimum confidence below which callers should keep a neutral name.
MIN_CONFIDENCE = 0.45


@dataclasses.dataclass(frozen=True)
class EventClassification:
    """A classified event: its type, a 0–1 confidence, and the mood colour."""

    event_type: str
    confidence: float
    dominant_hex: str


def classify_event(rgb: RGB) -> EventClassification:
    """
    Classify an event from its dominant "mood" colour.

    Hue drives the type (turmeric yellow -> Haldi, green -> Mehndi, red ->
    Baraat); low-saturation colours read as neutral **Portraits**, and warm
    low-saturation golds as **Reception**. Anything colourful but off the named
    hues falls back to **Ceremony**. Confidence scales with how saturated and
    how hue-central the colour is.
    """
    r, g, b = (c / 255.0 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue = h * 360.0

    # Desaturated -> neutral studio portraits, unless there's a faint warm gold
    # tint (some saturation + bright + warm hue) which reads as reception elegance.
    if s < 0.18:
        if s >= 0.08 and v >= 0.55 and (hue <= 65.0 or hue >= 350.0):
            return EventClassification(RECEPTION, _conf(0.18 - s + 0.3, v), to_hex(rgb))
        return EventClassification(PORTRAITS, _conf(0.5, v), to_hex(rgb))

    if _in_hue(hue, 40.0, 70.0) and v >= 0.4:
        return EventClassification(HALDI, _conf(s, v, center=55.0, hue=hue), to_hex(rgb))
    if _in_hue(hue, 80.0, 160.0):
        return EventClassification(MEHNDI, _conf(s, v, center=120.0, hue=hue), to_hex(rgb))
    if _in_hue(hue, 340.0, 360.0) or _in_hue(hue, 0.0, 20.0):
        return EventClassification(BARAAT, _conf(s, v, center=5.0, hue=hue % 360), to_hex(rgb))
    if _in_hue(hue, 20.0, 40.0) and v >= 0.4:
        # Warm amber/gold between red and yellow -> reception elegance.
        return EventClassification(RECEPTION, _conf(s, v, center=30.0, hue=hue), to_hex(rgb))

    return EventClassification(CEREMONY, 0.4, to_hex(rgb))


def classify_event_from_photos(image_paths: Sequence[str], **kwargs) -> EventClassification:
    """Classify an event directly from its photos (samples their dominant colour)."""
    return classify_event(dominant_color(image_paths, **kwargs))


def event_name(rgb: RGB, min_confidence: float = MIN_CONFIDENCE) -> Optional[str]:
    """
    Confident event type as a display name, or ``None`` below ``min_confidence``.

    Drop-in richer replacement for the old ``theming.classify_event_name`` (which
    only ever returned "Haldi"). ``None`` lets the caller keep a chronological
    label when the colour isn't distinctive enough.
    """
    result = classify_event(rgb)
    if result.event_type == CEREMONY or result.confidence < min_confidence:
        return None
    return result.event_type


def _in_hue(hue: float, lo: float, hi: float) -> bool:
    return lo <= hue <= hi


def _conf(s: float, v: float, center: Optional[float] = None, hue: Optional[float] = None) -> float:
    """
    Confidence in ``[0, 1]`` from saturation/value and (optionally) how close the
    hue sits to a named centre.
    """
    base = max(0.0, min(1.0, 0.35 + 0.65 * s)) * max(0.4, min(1.0, v))
    if center is not None and hue is not None:
        closeness = max(0.0, 1.0 - abs(hue - center) / 45.0)
        base = 0.5 * base + 0.5 * closeness
    return round(max(0.0, min(1.0, base)), 3)
