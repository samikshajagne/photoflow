"""WS 4.1.2 tests: layout rules over slot sequences."""

from __future__ import annotations

from core.album.slot_matcher import SlotProfile
from core.album.layout_rules import (
    detail_not_at_ends,
    layout_penalty,
    no_repetition,
    validate_layout,
    vary_orientations,
    wide_in_center,
)

PORTRAIT = SlotProfile("portrait_large", 0.75, ("portrait",), (1, 2))
PORTRAIT2 = SlotProfile("portrait_small", 0.72, ("portrait",), (1, 1))
DETAIL = SlotProfile("detail_square", 1.0, ("detail",), (0, 1))
WIDE = SlotProfile("landscape_wide", 1.78, ("landscape",), (0, 5))
GROUP = SlotProfile("group_square", 1.0, ("group",), (2, 5))


def test_no_repetition():
    assert no_repetition([PORTRAIT, DETAIL, GROUP])
    assert not no_repetition([PORTRAIT, PORTRAIT2, GROUP])  # two portraits adjacent


def test_vary_orientations():
    # three portraits in a row -> monotonous
    assert not vary_orientations([PORTRAIT, PORTRAIT2, PORTRAIT])
    assert vary_orientations([PORTRAIT, WIDE, PORTRAIT2])


def test_detail_not_at_ends():
    assert not detail_not_at_ends([DETAIL, PORTRAIT, GROUP])
    assert not detail_not_at_ends([PORTRAIT, GROUP, DETAIL])
    assert detail_not_at_ends([PORTRAIT, DETAIL, GROUP])


def test_wide_in_center():
    assert not wide_in_center([WIDE, PORTRAIT, GROUP])       # wide at start
    assert not wide_in_center([PORTRAIT, GROUP, WIDE])       # wide at end
    assert wide_in_center([PORTRAIT, WIDE, GROUP])           # wide in middle
    assert wide_in_center([PORTRAIT, WIDE])                  # < 3 slots -> trivially ok


def test_validate_and_penalty():
    good = [PORTRAIT, WIDE, DETAIL, GROUP]
    rules = ["no_repetition", "vary_orientations", "detail_not_at_ends", "wide_in_center"]
    assert validate_layout(good, rules)
    assert layout_penalty(good, rules) == 0

    bad = [PORTRAIT, PORTRAIT2, DETAIL]  # repetition + detail at end
    assert not validate_layout(bad, rules)
    assert layout_penalty(bad, rules) >= 2


def test_unknown_rule_ignored():
    assert validate_layout([PORTRAIT, DETAIL], ["no_repetition", "does_not_exist"])
