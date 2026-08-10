"""
Narrative pacing for album spreads.

A uniformly packed album reads as software output. Every spread carries the
same number of photos, so the eye finds no rhythm and nothing is ever given
emphasis. Designers do the opposite: they alternate busy, dense spreads with a
single image handed the whole page, so the book breathes and the frames that
matter land.

This module turns one section's photo count into a *sequence* of per-spread
counts that varies deliberately, while holding three invariants:

- **Nothing is lost or duplicated** — the counts sum to exactly ``total``.
- **The page budget is preserved** — the number of spreads is exactly the
  ``ceil(total / per_spread)`` that uniform packing would have produced, so
  adding rhythm never balloons the book. This is the property that lets pacing
  be switched on without disturbing the density / page-budget policy in
  :mod:`core.album.layout_select`.
- **Chronology survives** — only the *counts* vary. Photo order is untouched,
  so events stay in the sequence they happened.

A rhythm is a short cycle of weights whose mean is exactly 1.0; a spread's
count is its weight times the target ``per_spread``. Because the mean is 1, the
dense beats pay for the sparse ones and the spread count comes out unchanged.

Everything here is deterministic integer arithmetic over counts — no image
data, no geometry, no I/O.
"""

from __future__ import annotations

import math

# Rhythm names. ``uniform`` is the historical behaviour (pacing off).
PACING_UNIFORM = "uniform"
PACING_EDITORIAL = "editorial"
PACING_GENTLE = "gentle"

# Each cycle's weights must average exactly 1.0 so the spread count is
# preserved. Read them as: dense, normal, dense, hero.
_WEIGHTS: dict[str, tuple[float, ...]] = {
    # Pronounced magazine rhythm: two full spreads, then one image nearly alone.
    PACING_EDITORIAL: (1.35, 1.0, 1.3, 0.35),
    # Subtler variation, for albums where a near-empty spread would feel abrupt.
    #
    # The weights are spread wider than "gentle" suggests on purpose. Counts are
    # integers, so at the three-or-four-photos-per-spread most albums use, weights
    # closer to 1.0 all round to the same number and the setting does nothing at
    # all. These are the narrowest weights that still change the count at
    # per_spread=3, which is the point below which pacing stops being a choice.
    PACING_GENTLE: (1.3, 1.0, 0.7, 1.0),
}

# A rhythm needs at least this many spreads to read as a rhythm rather than as
# an inconsistency. Below it, uniform packing is the better-looking choice.
_MIN_SPREADS_FOR_RHYTHM = 3


def available_rhythms() -> tuple[str, ...]:
    """Rhythm names accepted by :func:`pace_counts`, including ``uniform``."""
    return (PACING_UNIFORM,) + tuple(_WEIGHTS)


def pace_counts(
    total: int,
    per_spread: int,
    max_per_spread: int,
    rhythm: str = PACING_EDITORIAL,
) -> list[int]:
    """
    Photos-per-spread for each spread of one section, in album order.

    Args:
        total: Number of photos in the section (``<= 0`` yields an empty list).
        per_spread: Target average photos per spread — the count uniform
            packing would have used. Values ``<= 1`` mean one photo per spread
            (a hero section), which has no rhythm to apply.
        max_per_spread: Hard ceiling on any single spread's count.
        rhythm: A name from :func:`available_rhythms`. ``"uniform"`` (or an
            unknown name) reproduces uniform packing exactly.

    Returns:
        A list of positive counts summing to ``total``. Its length equals
        ``ceil(total / per_spread)`` whenever a rhythm is applied.

    Pacing is skipped — falling back to uniform packing — when the section is
    a hero section, is too short for a rhythm to read, or is too dense for
    ``max_per_spread`` to accommodate. In all three cases varying the counts
    would look like a mistake rather than a decision.
    """
    if total <= 0:
        return []

    per_spread = max(1, int(per_spread))
    cap = max(1, int(max_per_spread))
    n_spreads = -(-total // per_spread)  # ceil(total / per_spread)

    weights = _WEIGHTS.get(rhythm)
    if (
        weights is None
        or per_spread <= 1
        or n_spreads < _MIN_SPREADS_FOR_RHYTHM
        or n_spreads * cap < total  # the cap cannot hold the album this tightly
    ):
        return _uniform(total, per_spread)

    cycle = [weights[i % len(weights)] for i in range(n_spreads)]
    scale = total / sum(cycle)
    counts = _largest_remainder([w * scale for w in cycle], total)
    return _repair(counts, cap)


def _uniform(total: int, per_spread: int) -> list[int]:
    """Historical packing: fill each spread to ``per_spread``, remainder last."""
    counts: list[int] = []
    remaining = total
    while remaining > 0:
        take = min(per_spread, remaining)
        counts.append(take)
        remaining -= take
    return counts


def _largest_remainder(raw: list[float], total: int) -> list[int]:
    """
    Round ``raw`` to integers summing to exactly ``total``.

    Floors every value, then hands the shortfall to the entries with the
    largest discarded fractions (ties broken by index, so the result is
    deterministic). This is the standard apportionment method; rounding each
    value independently would not sum back to ``total``.
    """
    floors = [int(math.floor(v)) for v in raw]
    shortfall = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:shortfall]:
        floors[i] += 1
    return floors


def _repair(counts: list[int], cap: int) -> list[int]:
    """
    Force every count into ``[1, cap]`` while keeping the total unchanged.

    Rounding can leave a spread empty or over the cap. Both are fixed by moving
    photos between spreads rather than by adding or dropping any, so the sum is
    invariant. Each transfer is taken from the fullest spread (or given to the
    emptiest), which preserves as much of the rhythm's shape as the bounds allow.
    """
    counts = list(counts)
    n = len(counts)

    # Nobody gets an empty spread; pay for it from the fullest that can spare one.
    for i in range(n):
        while counts[i] < 1:
            donor = max(range(n), key=lambda j: (counts[j], -j))
            if counts[donor] <= 1:
                break  # every spread is already at the floor
            counts[donor] -= 1
            counts[i] += 1

    # Nobody exceeds the cap; push the surplus onto spreads with headroom.
    for i in range(n):
        while counts[i] > cap:
            taker = min(range(n), key=lambda j: (counts[j], j))
            if counts[taker] >= cap:
                break  # every spread is already at the ceiling
            counts[taker] += 1
            counts[i] -= 1

    return counts


def chunk_by_counts(items: list, counts: list[int]) -> list[list]:
    """
    Split ``items`` into consecutive groups of the given sizes, in order.

    Any items beyond what ``counts`` accounts for are appended as a final
    group, so nothing is ever silently dropped.
    """
    groups: list[list] = []
    start = 0
    for size in counts:
        if start >= len(items):
            break
        groups.append(items[start : start + size])
        start += size
    if start < len(items):
        groups.append(items[start:])
    return groups
