"""
Subject-aware photo-to-slot matching (WS 3.2.2 + 3.2.3).

Given candidate photos (each described by a :class:`~core.content_analyzer.PhotoContent`)
and a set of spread slots (each a :class:`SlotProfile` of composition preferences),
assign photos to slots to maximise total compatibility — so a couple portrait lands
in the big portrait slot and a henna close-up lands in a small detail slot, instead
of the old "next photo into next slot" fill.

This is a bipartite assignment problem. When SciPy is available it is solved
optimally with the Hungarian algorithm; otherwise a deterministic greedy
max-weight matching is used, which is within a small factor and needs no new
dependency. A variety bonus discourages placing three portraits in a row.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Sequence, Tuple

from core.content_analyzer import (
    DETAIL,
    GROUP,
    LANDSCAPE,
    LARGE_GROUP,
    PhotoContent,
    orientation_of,
)

# Weights for the compatibility sub-scores (sum of the first three = 100).
_W_COMPOSITION = 55.0
_W_FACE_COUNT = 25.0
_W_ASPECT = 20.0
_VARIETY_BONUS = 20.0


@dataclasses.dataclass(frozen=True)
class SlotProfile:
    """
    A spread slot described by what photo suits it best.

    Attributes:
        name: Human-readable slot id (e.g. ``"portrait_large"``).
        aspect_ratio: Slot width / height.
        ideal_composition: Composition types that fit well (see
            :data:`core.content_analyzer.COMPOSITION_TYPES`).
        ideal_face_count: Inclusive ``(min, max)`` preferred face count.
    """

    name: str
    aspect_ratio: float
    ideal_composition: Tuple[str, ...]
    ideal_face_count: Tuple[int, int] = (0, 99)


# A reasonable default slot vocabulary (mirrors the roadmap's SLOT_TYPES).
DEFAULT_SLOT_PROFILES: Tuple[SlotProfile, ...] = (
    SlotProfile("portrait_large", 0.75, ("portrait", "full_body"), (1, 2)),
    SlotProfile("portrait_small", 0.75, ("portrait",), (1, 1)),
    SlotProfile("landscape_wide", 1.78, ("group", "landscape"), (0, 5)),
    SlotProfile("group_square", 1.0, ("group", "large_group"), (2, 8)),
    SlotProfile("detail_square", 1.0, ("detail", "environmental"), (0, 1)),
)


def compatibility_score(
    content: PhotoContent,
    slot: SlotProfile,
    recent_types: Sequence[str] = (),
) -> float:
    """
    Score how well ``content`` fits ``slot`` (0–~120 with the variety bonus).

    Combines a composition match, a face-count match, an aspect-ratio fit, and a
    small bonus when this photo's type differs from the recently placed ones (to
    keep a spread visually varied).
    """
    composition = _composition_match(content, slot)
    face = _face_count_match(content, slot)
    aspect = _aspect_match(content.aspect_ratio, slot.aspect_ratio)
    score = (
        _W_COMPOSITION * composition
        + _W_FACE_COUNT * face
        + _W_ASPECT * aspect
    )
    if recent_types and content.composition_type not in recent_types:
        score += _VARIETY_BONUS
    return score


def match_photos_to_slots(
    contents: Sequence[PhotoContent],
    slots: Sequence[SlotProfile],
) -> Dict[int, int]:
    """
    Assign photos to slots maximising total compatibility.

    Returns ``{slot_index: photo_index}`` covering ``min(len(slots), len(photos))``
    slots. Uses SciPy's Hungarian solver when available, else a greedy
    max-weight matching. Both are deterministic for a given input.
    """
    if not contents or not slots:
        return {}

    matrix = [[compatibility_score(c, s) for s in slots] for c in contents]
    optimal = _hungarian(matrix)
    if optimal is not None:
        return optimal
    return _greedy(matrix)


# --------------------------------------------------------------------------- #
# Sub-scores
# --------------------------------------------------------------------------- #
def _composition_match(content: PhotoContent, slot: SlotProfile) -> float:
    if content.composition_type in slot.ideal_composition:
        return 1.0
    # Partial credit for "adjacent" types so a near-miss still beats a clash.
    kin = {
        GROUP: {LARGE_GROUP},
        LARGE_GROUP: {GROUP},
        DETAIL: {LANDSCAPE},
        LANDSCAPE: {DETAIL},
    }.get(content.composition_type, set())
    return 0.5 if kin & set(slot.ideal_composition) else 0.0


def _face_count_match(content: PhotoContent, slot: SlotProfile) -> float:
    lo, hi = slot.ideal_face_count
    n = content.face_count
    if lo <= n <= hi:
        return 1.0
    # Linear falloff outside the band.
    dist = (lo - n) if n < lo else (n - hi)
    return max(0.0, 1.0 - dist / 4.0)


def _aspect_match(photo_ar: float, slot_ar: float) -> float:
    if photo_ar <= 0 or slot_ar <= 0:
        return 0.0
    # Same orientation is most of the score; closeness in ratio refines it.
    orient = 1.0 if orientation_of(photo_ar) == orientation_of(slot_ar) else 0.4
    ratio = min(photo_ar, slot_ar) / max(photo_ar, slot_ar)
    return orient * (0.6 + 0.4 * ratio)


# --------------------------------------------------------------------------- #
# Solvers
# --------------------------------------------------------------------------- #
def _hungarian(matrix: List[List[float]]) -> Optional[Dict[int, int]]:
    """Optimal assignment via SciPy, or ``None`` if SciPy isn't installed."""
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
    except Exception:  # noqa: BLE001 - SciPy is optional
        return None
    cost = -np.asarray(matrix, dtype=float)  # maximise score = minimise -score
    rows, cols = linear_sum_assignment(cost)
    # rows index photos, cols index slots -> we want {slot: photo}.
    return {int(c): int(r) for r, c in zip(rows, cols)}


def _greedy(matrix: List[List[float]]) -> Dict[int, int]:
    """
    Deterministic greedy max-weight matching.

    Repeatedly takes the highest-scoring free (photo, slot) pair. Ties break by
    lowest photo index then lowest slot index, so output is stable.
    """
    triples: List[Tuple[float, int, int]] = []
    for pi, row in enumerate(matrix):
        for si, score in enumerate(row):
            triples.append((score, pi, si))
    triples.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_photos: set[int] = set()
    used_slots: set[int] = set()
    assignment: Dict[int, int] = {}
    for _score, pi, si in triples:
        if pi in used_photos or si in used_slots:
            continue
        assignment[si] = pi
        used_photos.add(pi)
        used_slots.add(si)
    return assignment
