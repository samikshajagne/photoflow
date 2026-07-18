"""WS 4.1.1 tests: content-adaptive flexible spreads."""

from __future__ import annotations

from core.content_analyzer import analyze
from core.album.slot_matcher import SlotProfile
from core.album.flexible_template import (
    FlexibleSpread,
    SlotPoolEntry,
    build_spread_template,
    default_flexible_spread,
    select_flexible_slots,
)
from core.album.spread_layout_calculator import rects_within_bounds

# Photo fixtures by composition.
PORTRAIT = analyze(0.75, [(0.3, 0.15, 0.4, 0.5)])          # 1 big face, tall
GROUP = analyze(1.5, [(0.1 * i, 0.4, 0.07, 0.09) for i in range(3)])  # 3 faces, wide
DETAIL = analyze(1.0, [(0.48, 0.48, 0.03, 0.03)])          # tiny face, square
LANDSCAPE = analyze(1.78, [])                              # no faces, wide


def _pool():
    return (
        SlotPoolEntry(SlotProfile("portrait_large", 0.75, ("portrait",), (1, 2)), 2),
        SlotPoolEntry(SlotProfile("group_square", 1.0, ("group",), (2, 5)), 2),
        SlotPoolEntry(SlotProfile("detail_square", 1.0, ("detail",), (0, 1)), 2),
        SlotPoolEntry(SlotProfile("landscape_wide", 1.78, ("landscape", "group"), (0, 5)), 2),
    )


def test_selects_types_matching_the_photos():
    spread = FlexibleSpread("s", "classic", _pool(), slots_to_fill=3,
                            rules=("no_repetition",))
    chosen = select_flexible_slots([PORTRAIT, GROUP, DETAIL], spread)
    names = {p.name for p in chosen}
    assert len(chosen) == 3
    # The three distinct photo types should each pull in their matching slot.
    assert "portrait_large" in names
    assert "detail_square" in names
    assert "group_square" in names or "landscape_wide" in names


def test_respects_no_repetition_rule():
    # Three portraits offered, but no_repetition should avoid two identical
    # primary-composition slots adjacent when alternatives exist.
    spread = FlexibleSpread("s", "classic", _pool(), slots_to_fill=3,
                            rules=("no_repetition",))
    chosen = select_flexible_slots([PORTRAIT, PORTRAIT, GROUP], spread)
    prims = [p.ideal_composition[0] for p in chosen]
    assert all(prims[i] != prims[i + 1] for i in range(len(prims) - 1))


def test_build_spread_template_is_valid_and_positioned():
    spread = default_flexible_spread(slots_to_fill=3)
    tmpl = build_spread_template([PORTRAIT, GROUP, DETAIL], spread)
    assert tmpl.photo_count == 3
    rects = [s.rect for s in tmpl.slots]
    assert rects_within_bounds(rects)
    # Exactly one hero cutout at most (largest slot, if portrait/landscape).
    assert sum(1 for s in tmpl.slots if s.use_cutout) <= 1


def test_adapts_count_to_available_photos():
    spread = default_flexible_spread(slots_to_fill=5)
    # Only 2 photos -> at most 2 slots.
    chosen = select_flexible_slots([PORTRAIT, DETAIL], spread)
    assert len(chosen) == 2


def test_deterministic():
    spread = default_flexible_spread(slots_to_fill=3)
    a = [p.name for p in select_flexible_slots([PORTRAIT, GROUP, DETAIL], spread)]
    b = [p.name for p in select_flexible_slots([PORTRAIT, GROUP, DETAIL], spread)]
    assert a == b
