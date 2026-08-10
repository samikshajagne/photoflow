"""Tests for the editorial theme template set."""

from __future__ import annotations

from core.album.editorial_templates import THEME, editorial_templates


def test_counts_1_to_6_present():
    tpls = {t.name: t for t in editorial_templates()}
    for n in range(1, 7):
        assert f"editorial-{n}" in tpls
        assert tpls[f"editorial-{n}"].photo_count == n
        assert tpls[f"editorial-{n}"].theme == THEME


def test_slots_stay_in_bounds():
    eps = 1e-6
    for t in editorial_templates():
        for s in t.slots:
            x, y, w, h = s.rect
            assert w > 0 and h > 0
            assert -eps <= x and -eps <= y
            assert x + w <= 1 + eps and y + h <= 1 + eps


def test_hero_is_borderless_tallest_and_cutout_eligible():
    # The first slot of the 3/4-photo layouts is the hero: no border, tallest,
    # and cutout-eligible (so the cutouts toggle acts on it).
    for n in (3, 4):
        t = {t.name: t for t in editorial_templates()}[f"editorial-{n}"]
        hero = t.slots[0]
        assert hero.border == 0.0
        assert hero.use_cutout is True
        assert hero.rect[3] == max(s.rect[3] for s in t.slots)  # tallest
        assert hero.rect[3] > 0.9  # nearly full height (inset by the page margin)
        # Inset (non-hero) slots are not cutout-eligible.
        assert all(s.use_cutout is False for s in t.slots[1:])


def test_selectable_via_select_template():
    from core.album.template import select_template

    t = select_template(editorial_templates(), 4, "editorial", variant=0)
    assert t.name == "editorial-4"
    assert t.photo_count == 4
