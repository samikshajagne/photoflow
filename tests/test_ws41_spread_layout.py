"""WS 4.1.3 tests: spread layout position computation."""

from __future__ import annotations

from core.content_analyzer import ORIENT_LANDSCAPE, ORIENT_PORTRAIT, ORIENT_SQUARE
from core.album.spread_layout_calculator import (
    compute_dynamic_layout,
    get_layout_positions,
    rects_within_bounds,
)


def test_exact_pattern_portrait_hero_plus_two_squares():
    rects = get_layout_positions([ORIENT_PORTRAIT, ORIENT_SQUARE, ORIENT_SQUARE])
    assert len(rects) == 3
    assert rects_within_bounds(rects)
    # Hero is the widest slot and on the left.
    assert rects[0][2] == max(r[2] for r in rects)
    assert rects[0][0] == 0.0


def test_two_widescreens_stack_vertically():
    rects = get_layout_positions([ORIENT_LANDSCAPE, ORIENT_LANDSCAPE])
    assert len(rects) == 2
    assert rects_within_bounds(rects)
    # Same width (full), stacked -> different y.
    assert rects[0][1] < rects[1][1]


def test_dynamic_fallback_grid_for_unseen_signature():
    orients = [ORIENT_SQUARE, ORIENT_SQUARE, ORIENT_SQUARE, ORIENT_SQUARE, ORIENT_SQUARE]
    rects = get_layout_positions(orients)
    assert len(rects) == 5
    assert rects_within_bounds(rects)


def test_dynamic_portrait_hero():
    rects = compute_dynamic_layout([ORIENT_PORTRAIT, ORIENT_SQUARE, ORIENT_SQUARE, ORIENT_SQUARE])
    assert len(rects) == 4
    assert rects_within_bounds(rects)
    assert rects[0][0] == 0.0  # hero anchored left


def test_all_counts_stay_in_bounds():
    for n in range(1, 9):
        rects = get_layout_positions([ORIENT_SQUARE] * n)
        assert len(rects) == n
        assert rects_within_bounds(rects), f"n={n} out of bounds"


def test_empty():
    assert get_layout_positions([]) == []
