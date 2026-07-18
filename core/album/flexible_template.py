"""
Flexible, content-adaptive spread templates (WS 4.1.1).

A fixed template says "this spread has 1 portrait + 2 squares." A *flexible*
template instead offers a **pool** of slot types and a number of slots to fill,
and lets the actual photos decide which types to use — so a spread of three
close-ups becomes three detail slots, while a spread with a couple portrait and a
group shot becomes a portrait hero + a wide slot.

The selection maximises how well the chosen slots match the candidate photos
(via :mod:`core.album.slot_matcher`) while honouring taste rules
(:mod:`core.album.layout_rules`), then positions them with
:mod:`core.album.spread_layout_calculator` and emits a concrete
:class:`~core.album.template.SpreadTemplate` the renderer already understands.
"""

from __future__ import annotations

import dataclasses
import itertools
from typing import List, Optional, Sequence, Tuple

from core.content_analyzer import (
    ORIENT_LANDSCAPE,
    ORIENT_PORTRAIT,
    PhotoContent,
    orientation_of,
)
from core.album.layout_rules import layout_penalty
from core.album.slot_matcher import (
    DEFAULT_SLOT_PROFILES,
    SlotProfile,
    compatibility_score,
    match_photos_to_slots,
)
from core.album.spread_layout_calculator import get_layout_positions
from core.album.template import (
    SHAPE_CIRCLE,
    SHAPE_RECT,
    SHAPE_ROUNDED,
    Background,
    SpreadTemplate,
    TemplateSlot,
)

# Bound the search so a big pool can't blow up (deterministic caps).
_MAX_EXPANDED_POOL = 10
_MAX_ORDERINGS = 120


@dataclasses.dataclass(frozen=True)
class SlotPoolEntry:
    """One slot type offered by a flexible spread, with how many are available."""

    profile: SlotProfile
    count_available: int = 1


@dataclasses.dataclass(frozen=True)
class FlexibleSpread:
    """
    A spread that adapts its slot types to the photos.

    Attributes:
        name: Template name.
        theme: Theme string (passed onto the produced :class:`SpreadTemplate`).
        slot_pool: The slot types this spread may draw from.
        slots_to_fill: How many slots to actually use.
        rules: Named layout rules to honour (see :mod:`core.album.layout_rules`).
    """

    name: str
    theme: str
    slot_pool: Tuple[SlotPoolEntry, ...]
    slots_to_fill: int
    rules: Tuple[str, ...] = ("no_repetition", "vary_orientations")


def _expand_pool(pool: Sequence[SlotPoolEntry]) -> List[SlotProfile]:
    out: List[SlotProfile] = []
    for entry in pool:
        out.extend([entry.profile] * max(1, entry.count_available))
        if len(out) >= _MAX_EXPANDED_POOL:
            break
    return out[:_MAX_EXPANDED_POOL]


def _total_compatibility(
    contents: Sequence[PhotoContent], slots: Sequence[SlotProfile]
) -> float:
    """Best photo→slot matching score for this ordered slot set."""
    assignment = match_photos_to_slots(contents, slots)
    return sum(
        compatibility_score(contents[pi], slots[si]) for si, pi in assignment.items()
    )


def select_flexible_slots(
    contents: Sequence[PhotoContent], spread: FlexibleSpread
) -> List[SlotProfile]:
    """
    Choose and order the slot profiles that best fit ``contents``.

    Considers each distinct combination of ``slots_to_fill`` types from the pool,
    finds each combination's best ordering (fewest rule violations, then highest
    photo compatibility), and returns the winning ordered list. Deterministic.
    """
    k = max(1, min(spread.slots_to_fill, len(contents) or spread.slots_to_fill))
    expanded = _expand_pool(spread.slot_pool)
    if not expanded:
        return []
    k = min(k, len(expanded))

    # Distinct multisets of profiles, keyed by their names (order-independent).
    seen: set[Tuple[str, ...]] = set()
    best: Optional[Tuple[int, float, List[SlotProfile]]] = None  # (penalty, -compat, slots)

    for combo in itertools.combinations(range(len(expanded)), k):
        profiles = [expanded[i] for i in combo]
        key = tuple(sorted(p.name for p in profiles))
        if key in seen:
            continue
        seen.add(key)

        # Find the best ordering of this multiset.
        orderings = _distinct_orderings(profiles)
        for order in orderings:
            penalty = layout_penalty(order, spread.rules)
            compat = _total_compatibility(contents, order)
            cand = (penalty, -compat, list(order))
            if best is None or cand[:2] < best[:2]:
                best = cand

    return best[2] if best is not None else list(expanded[:k])


def _distinct_orderings(profiles: Sequence[SlotProfile]) -> List[Tuple[SlotProfile, ...]]:
    """Unique orderings of ``profiles`` (dedup by name tuple), capped for safety."""
    seen: set[Tuple[str, ...]] = set()
    out: List[Tuple[SlotProfile, ...]] = []
    for perm in itertools.permutations(profiles):
        key = tuple(p.name for p in perm)
        if key in seen:
            continue
        seen.add(key)
        out.append(perm)
        if len(out) >= _MAX_ORDERINGS:
            break
    return out


def _shape_for(profile: SlotProfile, is_hero: bool) -> str:
    comp = profile.ideal_composition[0] if profile.ideal_composition else ""
    if is_hero:
        return SHAPE_RECT
    if comp in ("detail",):
        return SHAPE_CIRCLE
    return SHAPE_ROUNDED


def build_spread_template(
    contents: Sequence[PhotoContent],
    spread: FlexibleSpread,
    *,
    border: float = 0.008,
    background: Optional[Background] = None,
) -> SpreadTemplate:
    """
    Produce a concrete :class:`SpreadTemplate` adapted to ``contents``.

    Selects slot types (:func:`select_flexible_slots`), positions them
    (:mod:`core.album.spread_layout_calculator`), and authors each
    :class:`TemplateSlot` — the largest portrait hero is made ``use_cutout=True``
    so the editorial silhouette can be enabled downstream.
    """
    slots = select_flexible_slots(contents, spread)
    if not slots:
        raise ValueError("flexible spread produced no slots")

    orientations = [orientation_of(p.aspect_ratio) for p in slots]
    rects = get_layout_positions(orientations)

    # The hero is the largest-area slot; only a portrait/landscape hero is cutout-eligible.
    hero_idx = max(range(len(rects)), key=lambda i: rects[i][2] * rects[i][3])

    template_slots: List[TemplateSlot] = []
    for i, (profile, rect) in enumerate(zip(slots, rects)):
        is_hero = i == hero_idx
        cutout = is_hero and orientations[i] in (ORIENT_PORTRAIT, ORIENT_LANDSCAPE)
        template_slots.append(
            TemplateSlot(
                rect=rect,
                shape=_shape_for(profile, is_hero),
                border=border,
                shadow=True,
                use_cutout=cutout,
            )
        )

    return SpreadTemplate(
        name=spread.name,
        theme=spread.theme,
        slots=tuple(template_slots),
        background=background or Background(),
    )


# A sensible default flexible spread over the standard slot vocabulary.
def default_flexible_spread(name: str = "adaptive", theme: str = "classic", slots_to_fill: int = 3) -> FlexibleSpread:
    pool = tuple(SlotPoolEntry(p, count_available=2) for p in DEFAULT_SLOT_PROFILES)
    return FlexibleSpread(
        name=name,
        theme=theme,
        slot_pool=pool,
        slots_to_fill=slots_to_fill,
        rules=("no_repetition", "vary_orientations", "detail_not_at_ends"),
    )


__all__ = [
    "SlotPoolEntry",
    "FlexibleSpread",
    "select_flexible_slots",
    "build_spread_template",
    "default_flexible_spread",
]
