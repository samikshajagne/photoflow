"""
Narrative pacing: varying spread density without changing the album's size.

The invariants matter more than the exact counts, because the counts are a
taste decision that may be retuned. What must never change is that pacing
loses no photos, keeps chronology, and leaves the page budget alone — those
are what let it be switched on without disturbing the density policy.
"""

import math

import pytest

from core.album.layout import AlbumLayoutEngine, AlbumSpec, PhotoItem
from core.album.pacing import (
    PACING_EDITORIAL,
    PACING_GENTLE,
    PACING_UNIFORM,
    available_rhythms,
    chunk_by_counts,
    pace_counts,
    _WEIGHTS,
)

RHYTHMS = [PACING_EDITORIAL, PACING_GENTLE]


def _spec():
    return AlbumSpec(page_width_in=12, page_height_in=12, dpi=100, gutter_in=0.5)


# --------------------------------------------------------------------------- #
# Rhythm definitions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", RHYTHMS)
def test_rhythm_weights_average_exactly_one(name):
    """
    The mean weight is what preserves the spread count. If a rhythm's weights
    drifted off 1.0, every album using it would silently gain or lose spreads.
    """
    weights = _WEIGHTS[name]
    assert sum(weights) / len(weights) == pytest.approx(1.0, abs=1e-9)


def test_available_rhythms_includes_uniform():
    assert PACING_UNIFORM in available_rhythms()
    for name in RHYTHMS:
        assert name in available_rhythms()


# --------------------------------------------------------------------------- #
# Invariants
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("total", [0, 1, 2, 3, 7, 12, 36, 137, 600])
@pytest.mark.parametrize("per", [1, 2, 3, 4, 6])
@pytest.mark.parametrize("name", RHYTHMS)
def test_counts_sum_to_total(total, per, name):
    """Nothing is lost or duplicated, whatever the rhythm does."""
    counts = pace_counts(total, per, max_per_spread=8, rhythm=name)
    assert sum(counts) == total


@pytest.mark.parametrize("total", [3, 7, 12, 36, 137, 600])
@pytest.mark.parametrize("per", [2, 3, 4, 6])
@pytest.mark.parametrize("name", RHYTHMS)
def test_spread_count_matches_uniform_packing(total, per, name):
    """
    Pacing must not change how long the album is — that is the page budget's
    job. A rhythm that added spreads would quietly overrun the book.
    """
    counts = pace_counts(total, per, max_per_spread=8, rhythm=name)
    assert len(counts) == math.ceil(total / per)


@pytest.mark.parametrize("total", [3, 7, 12, 36, 137])
@pytest.mark.parametrize("per", [2, 3, 4])
@pytest.mark.parametrize("name", RHYTHMS)
def test_counts_respect_bounds(total, per, name):
    """No empty spread, and nothing over the cap."""
    cap = 6
    counts = pace_counts(total, per, max_per_spread=cap, rhythm=name)
    assert all(1 <= c <= cap for c in counts)


def test_uniform_reproduces_historical_packing():
    """The default must be byte-identical to the old fixed-chunk behaviour."""
    assert pace_counts(10, 3, 4, rhythm=PACING_UNIFORM) == [3, 3, 3, 1]
    assert pace_counts(12, 4, 4, rhythm=PACING_UNIFORM) == [4, 4, 4]


def test_unknown_rhythm_falls_back_to_uniform():
    assert pace_counts(10, 3, 4, rhythm="jazz") == [3, 3, 3, 1]


# --------------------------------------------------------------------------- #
# The rhythm actually varies
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", RHYTHMS)
@pytest.mark.parametrize("per", [3, 4, 5])
def test_every_rhythm_actually_changes_something(name, per):
    """
    A rhythm offered in the UI must do something at the densities albums really
    use. Counts are integers, so weights close to 1.0 round to the same number
    and the setting silently becomes a no-op — which is exactly how the density
    dropdown was found to be doing nothing.
    """
    total = per * 12
    paced = pace_counts(total, per, max_per_spread=per + 3, rhythm=name)
    uniform = pace_counts(total, per, max_per_spread=per + 3, rhythm=PACING_UNIFORM)
    assert paced != uniform, f"{name} at {per}/spread is indistinguishable from uniform"


@pytest.mark.parametrize("per", [3, 4, 5])
def test_gentle_pacing_avoids_near_empty_spreads(per):
    """
    Gentle's whole reason to exist is varying density *without* the abrupt
    single-photo spread editorial produces. If it drops that low it is just a
    second editorial.
    """
    counts = pace_counts(per * 12, per, max_per_spread=per + 3, rhythm=PACING_GENTLE)
    assert min(counts) > 1


def test_editorial_pacing_varies_spread_density():
    """
    The whole point: spreads must not all carry the same number of photos.
    """
    counts = pace_counts(36, 3, max_per_spread=6, rhythm=PACING_EDITORIAL)
    assert len(set(counts)) > 1, f"pacing produced a uniform album: {counts}"


def test_editorial_pacing_produces_a_breathing_single_photo_spread():
    """
    A full-bleed single image is the rhythm's payoff — a dense grid followed by
    one photo given the whole spread. Without it the variation is just noise.
    """
    counts = pace_counts(36, 3, max_per_spread=6, rhythm=PACING_EDITORIAL)
    assert 1 in counts
    # And that hero follows a denser spread rather than opening the section.
    hero = counts.index(1)
    assert hero > 0 and counts[hero - 1] > 1


# --------------------------------------------------------------------------- #
# Cases where pacing should stand down
# --------------------------------------------------------------------------- #
def test_hero_sections_are_untouched():
    """One photo per spread has no rhythm to apply."""
    assert pace_counts(5, 1, 4, rhythm=PACING_EDITORIAL) == [1, 1, 1, 1, 1]


@pytest.mark.parametrize("total", [1, 2, 3, 4, 5, 6])
def test_short_sections_pack_uniformly(total):
    """
    Under three spreads a varying count reads as an inconsistency rather than a
    rhythm, so pacing stands down.
    """
    per = 3
    if math.ceil(total / per) < 3:
        assert pace_counts(total, per, 6, rhythm=PACING_EDITORIAL) == pace_counts(
            total, per, 6, rhythm=PACING_UNIFORM
        )


def test_pacing_stands_down_when_the_cap_cannot_hold_the_album():
    """
    When every spread must already sit at the ceiling, there is no slack to
    borrow from and pacing must not invent any.
    """
    # 20 photos, 5 per spread, cap 5 -> 4 spreads all necessarily full.
    counts = pace_counts(20, 5, max_per_spread=5, rhythm=PACING_EDITORIAL)
    assert counts == [5, 5, 5, 5]


# --------------------------------------------------------------------------- #
# chunk_by_counts
# --------------------------------------------------------------------------- #
def test_chunk_by_counts_preserves_order_and_loses_nothing():
    items = list(range(10))
    groups = chunk_by_counts(items, [4, 1, 5])
    assert groups == [[0, 1, 2, 3], [4], [5, 6, 7, 8, 9]]
    assert [x for g in groups for x in g] == items


def test_chunk_by_counts_keeps_unaccounted_items():
    """A short count list must never silently drop the tail."""
    groups = chunk_by_counts(list(range(10)), [4, 1])
    assert [x for g in groups for x in g] == list(range(10))


# --------------------------------------------------------------------------- #
# Wired into the engine
# --------------------------------------------------------------------------- #
def _items(n):
    return [PhotoItem(path=f"p{i}.jpg", aspect_ratio=1.5) for i in range(n)]


def test_engine_defaults_to_uniform_packing():
    """Pacing is opt-in at the engine, so existing callers are unaffected."""
    spreads = AlbumLayoutEngine(max_per_spread=4).layout(
        _items(10), _spec(), per_spread=3
    )
    assert [len(s.placements) for s in spreads] == [3, 3, 3, 1]


def test_engine_pacing_varies_spreads_but_keeps_every_photo():
    items = _items(36)
    spreads = AlbumLayoutEngine(max_per_spread=6).layout(
        items, _spec(), per_spread=3, pacing=PACING_EDITORIAL
    )
    counts = [len(s.placements) for s in spreads]

    assert len(set(counts)) > 1
    assert sum(counts) == len(items)

    placed = [p.path for s in spreads for p in s.placements]
    assert placed == [it.path for it in items], "chronology or contents changed"


def test_engine_pacing_preserves_spread_count():
    items = _items(36)
    engine = AlbumLayoutEngine(max_per_spread=6)
    uniform = engine.layout(items, _spec(), per_spread=3, pacing=PACING_UNIFORM)
    paced = engine.layout(items, _spec(), per_spread=3, pacing=PACING_EDITORIAL)
    assert len(paced) == len(uniform)


def test_engine_pacing_is_deterministic():
    items = _items(36)
    engine = AlbumLayoutEngine(max_per_spread=6)
    a = engine.layout(items, _spec(), per_spread=3, pacing=PACING_EDITORIAL)
    b = engine.layout(items, _spec(), per_spread=3, pacing=PACING_EDITORIAL)
    assert a == b


def test_layout_selector_paces_by_default_without_changing_album_length(tmp_path):
    """
    Pacing is on by default (it is the point of the feature), but it must not
    move the album's spread count — that stays the density setting's job.
    """
    import cv2
    import numpy as np

    from core.album.layout_select import LayoutSelector
    from core.album.project import AlbumProject, SectionRecord

    paths = []
    for i in range(40):
        p = tmp_path / f"p{i}.jpg"
        cv2.imwrite(str(p), np.full((100, 200, 3), 127, np.uint8))
        paths.append(str(p))

    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Ceremony", "ceremony", paths)]
    spec = _spec()

    paced = LayoutSelector(density="balanced").select(project, spec)
    uniform = LayoutSelector(density="balanced", pacing=PACING_UNIFORM).select(
        project, spec
    )

    assert len(paced) == len(uniform), "pacing changed the album's length"
    assert sum(len(s.placements) for s in paced) == len(paths)

    paced_counts = [len(s.placements) for s in paced]
    assert len(set(paced_counts)) > 1, "default pacing did not vary spread density"
    assert len(set(len(s.placements) for s in uniform)) <= 2  # uniform + remainder


def test_layout_selector_rejects_unknown_pacing():
    from core.album.layout_select import LayoutSelector

    assert LayoutSelector(pacing="disco").pacing == PACING_UNIFORM


def test_paced_single_photo_spread_is_full_bleed():
    """
    A hero beat should fill the spread, not sit letterboxed in a collage cell.
    The engine already covers-fits lone photos; this pins that pacing reaches it.
    """
    spreads = AlbumLayoutEngine(max_per_spread=6).layout(
        _items(36), _spec(), per_spread=3, pacing=PACING_EDITORIAL
    )
    heroes = [s for s in spreads if len(s.placements) == 1]
    assert heroes, "editorial pacing produced no single-photo spread"
    assert all(s.placements[0].fit == "cover" for s in heroes)
