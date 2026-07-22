"""
Semantic event classification from vision labels (Implementation Plan — Component 3).

Uses the scene labels in a :class:`~core.vision_brain.PhotoBrain` (from Google
Vision) to name each photo's function — Haldi, Mehndi, Ceremony, Baraat,
Reception, Portraits — instead of the timestamp-gap-only segmentation. When a
photo has no usable labels (e.g. the local-fallback path produced none), it
defers to the existing colour heuristic in
:mod:`core.album.event_classifier` so behaviour degrades gracefully.

Event-type names are reused from :mod:`core.album.event_classifier` so the whole
app speaks one vocabulary.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence, Tuple

from core.album.event_classifier import (
    BARAAT,
    CEREMONY,
    HALDI,
    MEHNDI,
    PORTRAITS,
    RECEPTION,
    classify_event as _classify_by_color,
)

# Keyword vocabulary per event. Vision labels are matched case-insensitively as
# substrings, so "wedding ceremony" matches "ceremony". Ordering doesn't matter;
# scoring sums the confidence of every matched keyword.
# Expanded for GPT-4o output which often returns compound phrases.
EVENT_LABELS: dict[str, tuple[str, ...]] = {
    HALDI: (
        "haldi", "turmeric", "marigold", "yellow", "floral", "flower",
        "yellow saree", "ritual bath",
    ),
    MEHNDI: (
        "mehndi", "henna", "mehendi", "hand", "intricate pattern",
    ),
    BARAAT: (
        "baraat", "horse", "procession", "parade", "band", "dhol", "drum",
        "folk music", "street celebration", "groom entry",
    ),
    RECEPTION: (
        "reception", "dance", "stage", "cake", "banquet", "party", "disco",
        "nightclub", "sangeet", "dance floor", "celebration", "ballroom",
        "entertainment", "performance",
    ),
    CEREMONY: (
        "mandap", "priest", "ritual", "altar", "ceremony", "temple", "fire",
        "wedding ceremony", "sacred", "rite", "wedding ritual", "pheras",
        "vows", "garland", "wedding canopy",
    ),
    PORTRAITS: (
        "bride", "groom", "couple", "ring", "portrait", "gown", "wedding dress",
        "wedding photo", "wedding portrait", "pre-wedding", "bridal",
        "groomsmen", "bridesmaid", "wedding couple",
    ),
}

# Below this summed keyword score, the labels aren't decisive -> colour fallback.
# Set to 0.25 (was 0.5) so a single GPT-4o label matching at ~0.85 confidence
# is enough to name the event without needing 2+ keyword hits.
MIN_LABEL_SCORE = 0.25


@dataclasses.dataclass(frozen=True)
class EventResult:
    """A classified event with its confidence and how it was decided."""

    event_type: str
    confidence: float
    source: str  # "labels" | "color" | "unknown"


def classify_labels(
    labels: Sequence[str], confidences: Optional[Sequence[float]] = None
) -> Tuple[Optional[str], float]:
    """
    Best event type for a set of scene labels, and its score.

    Each event scores the summed confidence of the labels that contain any of its
    keywords (confidence defaults to 1.0 per label when not given). Returns
    ``(None, 0.0)`` when nothing matches.
    """
    if not labels:
        return None, 0.0
    conf = list(confidences) if confidences is not None else [1.0] * len(labels)
    if len(conf) < len(labels):
        conf = conf + [1.0] * (len(labels) - len(conf))

    scores: dict[str, float] = {}
    for label, c in zip(labels, conf):
        text = str(label).lower()
        for event, keywords in EVENT_LABELS.items():
            if any(kw in text for kw in keywords):
                scores[event] = scores.get(event, 0.0) + float(c)

    if not scores:
        return None, 0.0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def classify_photo(brain, *, min_label_score: float = MIN_LABEL_SCORE) -> EventResult:
    """
    Classify one photo from its :class:`~core.vision_brain.PhotoBrain`.

    Label-first: if the scene labels decide an event above ``min_label_score``,
    use it. Otherwise fall back to the colour heuristic on the photo's dominant
    colour. Returns an :class:`EventResult` (``source`` records which path won).
    """
    labels = list(getattr(brain, "scene_labels", []) or [])
    confidences = list(getattr(brain, "scene_confidence", []) or [])
    event, score = classify_labels(labels, confidences)
    if event is not None and score >= min_label_score:
        # Normalise the summed score into a rough 0..1 confidence.
        return EventResult(event, min(1.0, score / 2.0), "labels")

    colors = list(getattr(brain, "dominant_colors", []) or [])
    if colors:
        col = _classify_by_color(tuple(colors[0]))
        return EventResult(col.event_type, col.confidence, "color")

    return EventResult(CEREMONY, 0.0, "unknown")


def classify_event_group(brains: Sequence, *, min_label_score: float = MIN_LABEL_SCORE) -> EventResult:
    """
    Classify a *group* of photos (e.g. a timeline segment) by majority vote.

    Sums confidence per event across all photos and returns the strongest, so a
    handful of mislabelled frames don't rename the whole event.
    """
    if not brains:
        return EventResult(CEREMONY, 0.0, "unknown")
    tally: dict[str, float] = {}
    source_used = "unknown"
    for b in brains:
        r = classify_photo(b, min_label_score=min_label_score)
        if r.confidence <= 0:
            continue
        tally[r.event_type] = tally.get(r.event_type, 0.0) + r.confidence
        if r.source == "labels":
            source_used = "labels"
        elif source_used != "labels":
            source_used = r.source
    if not tally:
        return EventResult(CEREMONY, 0.0, "unknown")
    best = max(tally.items(), key=lambda kv: kv[1])
    return EventResult(best[0], min(1.0, best[1] / max(1, len(brains))), source_used)


__all__ = [
    "EVENT_LABELS",
    "EventResult",
    "classify_labels",
    "classify_photo",
    "classify_event_group",
]
