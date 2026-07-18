"""
Layout rules engine for variable-slot spreads (WS 4.1.2).

Once a spread can choose *which* slot types to use (see
:mod:`core.album.flexible_template`), it needs taste: don't put two portraits
side by side, alternate orientations for rhythm, keep tiny detail slots out of
the corners, and let a wide landscape anchor the centre. Each rule is a pure
predicate over the ordered list of :class:`~core.album.slot_matcher.SlotProfile`
that a spread would use; :func:`validate_layout` passes a layout only when every
requested rule holds.

Rules are addressed by name so a template can declare, e.g.,
``["no_repetition", "vary_orientations"]`` in data.
"""

from __future__ import annotations

from typing import Callable, Sequence

from core.content_analyzer import (
    ORIENT_LANDSCAPE,
    ORIENT_PORTRAIT,
    orientation_of,
)
from core.album.slot_matcher import SlotProfile

# A rule is a predicate over the ordered slot profiles of one spread.
Rule = Callable[[Sequence[SlotProfile]], bool]


def _orientations(slots: Sequence[SlotProfile]) -> list[str]:
    return [orientation_of(s.aspect_ratio) for s in slots]


def _primary_composition(slot: SlotProfile) -> str:
    """The slot's headline composition (its first ideal), or ``''``."""
    return slot.ideal_composition[0] if slot.ideal_composition else ""


def no_repetition(slots: Sequence[SlotProfile]) -> bool:
    """No two adjacent slots share the same primary composition."""
    prev = None
    for s in slots:
        cur = _primary_composition(s)
        if prev is not None and cur == prev:
            return False
        prev = cur
    return True


def vary_orientations(slots: Sequence[SlotProfile]) -> bool:
    """
    No three consecutive slots share one orientation.

    A softer rule than strict alternation (which is impossible for odd counts of
    one orientation); it just forbids a monotonous run.
    """
    orients = _orientations(slots)
    for i in range(len(orients) - 2):
        if orients[i] == orients[i + 1] == orients[i + 2]:
            return False
    return True


def detail_not_at_ends(slots: Sequence[SlotProfile]) -> bool:
    """Small ``detail`` slots shouldn't open or close a spread."""
    if len(slots) < 2:
        return True
    ends = (slots[0], slots[-1])
    return not any(_primary_composition(s) == "detail" for s in ends)


def wide_in_center(slots: Sequence[SlotProfile]) -> bool:
    """
    Any landscape/wide slot should sit in the interior, not at an end.

    Wide group/landscape shots read best anchoring the middle of a spread; at the
    very edge they unbalance it. Trivially true when there are < 3 slots.
    """
    if len(slots) < 3:
        return True
    orients = _orientations(slots)
    for i, o in enumerate(orients):
        if o == ORIENT_LANDSCAPE and i in (0, len(orients) - 1):
            return False
    return True


# Name -> rule, so templates can declare rules as data.
LAYOUT_RULES: dict[str, Rule] = {
    "no_repetition": no_repetition,
    "vary_orientations": vary_orientations,
    "detail_not_at_ends": detail_not_at_ends,
    "wide_in_center": wide_in_center,
}


def validate_layout(slots: Sequence[SlotProfile], rules: Sequence[str]) -> bool:
    """
    True only if ``slots`` satisfies every named rule.

    Unknown rule names are ignored (forward-compatible with templates that
    reference a rule this version doesn't ship).
    """
    for name in rules:
        rule = LAYOUT_RULES.get(name)
        if rule is not None and not rule(slots):
            return False
    return True


def layout_penalty(slots: Sequence[SlotProfile], rules: Sequence[str]) -> int:
    """
    Number of requested rules a layout violates (0 = perfect).

    Lets a selector prefer the least-bad ordering when no arrangement satisfies
    every rule, rather than rejecting all of them.
    """
    penalty = 0
    for name in rules:
        rule = LAYOUT_RULES.get(name)
        if rule is not None and not rule(slots):
            penalty += 1
    return penalty


__all__ = [
    "Rule",
    "LAYOUT_RULES",
    "no_repetition",
    "vary_orientations",
    "detail_not_at_ends",
    "wide_in_center",
    "validate_layout",
    "layout_penalty",
    "ORIENT_PORTRAIT",
    "ORIENT_LANDSCAPE",
]
