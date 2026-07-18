"""Tests for template variety / variant rotation (Phase 6c)."""

from core.album.template import auto_grid_template, default_templates, select_template


def test_grid_variants_differ():
    a = auto_grid_template(9, "classic", 0)
    b = auto_grid_template(9, "classic", 1)
    c = auto_grid_template(9, "classic", 2)
    assert len({a.name, b.name, c.name}) == 3          # three distinct layouts
    assert a.photo_count == b.photo_count == c.photo_count == 9


def test_select_rotates_shape_variants_for_count_3():
    tpls = default_templates()
    t0 = select_template(tpls, 3, "classic", variant=0)
    t1 = select_template(tpls, 3, "classic", variant=1)
    assert t0.name != t1.name                          # classic-3 vs classic-3b
    assert select_template(tpls, 3, "classic", variant=2).name == t0.name  # wraps


def test_count_4_has_two_variants():
    tpls = default_templates()
    names = {select_template(tpls, 4, "classic", v).name for v in range(2)}
    assert len(names) == 2


def test_dense_fallback_grid_varies_by_variant():
    tpls = default_templates()
    g0 = select_template(tpls, 10, "classic", 0)
    g1 = select_template(tpls, 10, "classic", 1)
    assert g0.name != g1.name and g0.photo_count == 10
