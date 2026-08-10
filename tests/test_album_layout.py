"""
Unit tests for core.album.layout.

The album layout engine is pure geometry -- it never decodes images -- so
these tests build :class:`PhotoItem` objects with explicit aspect ratios and
face boxes and assert on the resulting pixel frames and relative crops. No
real image files are needed.

This module tests album layout geometry only.
"""

from __future__ import annotations

import math

import pytest

from core.album.layout import (
    AlbumLayoutEngine,
    AlbumLayoutError,
    AlbumSpec,
    Frame,
    PhotoItem,
    Placement,
    Spread,
    choose_template,
    template_for,
)

_EPS = 1e-6


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _frames_within_bounds(frames) -> bool:
    for f in frames:
        if f.x < -_EPS or f.y < -_EPS:
            return False
        if f.x + f.w > 1 + _EPS or f.y + f.h > 1 + _EPS:
            return False
    return True


def _no_overlap(frames) -> bool:
    rects = [(f.x, f.y, f.x + f.w, f.y + f.h) for f in frames]
    for i in range(len(rects)):
        ax0, ay0, ax1, ay1 = rects[i]
        for j in range(i + 1, len(rects)):
            bx0, by0, bx1, by1 = rects[j]
            # Overlap iff they intersect on both axes (touching edges is fine).
            overlap_x = min(ax1, bx1) - max(ax0, bx0)
            overlap_y = min(ay1, by1) - max(ay0, by0)
            if overlap_x > _EPS and overlap_y > _EPS:
                return False
    return True


# --------------------------------------------------------------------------- #
# AlbumSpec
# --------------------------------------------------------------------------- #
def test_album_spec_pixel_math_double_page():
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    assert spec.double_page_spread is True
    assert spec.spread_width_in == 24
    assert spec.spread_height_in == 12
    assert spec.spread_width_px == round(24 * 300) == 7200
    assert spec.spread_height_px == round(12 * 300) == 3600


def test_album_spec_pixel_math_single_page():
    spec = AlbumSpec(
        page_width_in=8, page_height_in=10, dpi=150, double_page_spread=False
    )
    assert spec.spread_width_in == 8
    assert spec.spread_width_px == round(8 * 150) == 1200
    assert spec.spread_height_px == round(10 * 150) == 1500


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_width_in": 0, "page_height_in": 12, "dpi": 300},
        {"page_width_in": -1, "page_height_in": 12, "dpi": 300},
        {"page_width_in": 12, "page_height_in": 0, "dpi": 300},
        {"page_width_in": 12, "page_height_in": 12, "dpi": 0},
        {"page_width_in": 12, "page_height_in": 12, "dpi": -5},
        {"page_width_in": 12, "page_height_in": 12, "dpi": 300, "margin_in": -0.1},
        {"page_width_in": 12, "page_height_in": 12, "dpi": 300, "bleed_in": -0.1},
        {"page_width_in": 12, "page_height_in": 12, "dpi": 300, "gutter_in": -0.1},
    ],
)
def test_album_spec_validation_errors(kwargs):
    with pytest.raises(AlbumLayoutError):
        AlbumSpec(**kwargs)


# --------------------------------------------------------------------------- #
# PhotoItem / Frame validation
# --------------------------------------------------------------------------- #
def test_photo_item_validation():
    with pytest.raises(AlbumLayoutError):
        PhotoItem(path="a.jpg", aspect_ratio=0)
    with pytest.raises(AlbumLayoutError):
        PhotoItem(path="a.jpg", aspect_ratio=-1.5)
    with pytest.raises(AlbumLayoutError):
        PhotoItem(path="a.jpg", aspect_ratio=1.0, face_boxes=((0.5, 0.5, 0.7, 0.1),))
    with pytest.raises(AlbumLayoutError):
        PhotoItem(path="a.jpg", aspect_ratio=1.0, face_boxes=((0.1, 0.1, 0.0, 0.1),))
    # Valid item does not raise.
    PhotoItem(path="a.jpg", aspect_ratio=1.5, face_boxes=((0.1, 0.1, 0.2, 0.2),))


def test_frame_validation():
    with pytest.raises(AlbumLayoutError):
        Frame(0.0, 0.0, 0.0, 0.5)
    with pytest.raises(AlbumLayoutError):
        Frame(0.6, 0.0, 0.5, 0.5)  # x + w > 1
    Frame(0.0, 0.0, 1.0, 1.0)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_template_builtin_counts(count):
    frames = template_for(count)
    assert len(frames) == count
    assert _frames_within_bounds(frames)
    assert _no_overlap(frames)


@pytest.mark.parametrize("count", [5, 6, 7, 9])
def test_template_grid_fallback(count):
    frames = template_for(count)
    assert len(frames) == count
    assert _frames_within_bounds(frames)
    assert _no_overlap(frames)


def test_template_invalid_count():
    with pytest.raises(AlbumLayoutError):
        template_for(0)
    with pytest.raises(AlbumLayoutError):
        template_for(-3)


# --------------------------------------------------------------------------- #
# layout(): chunking and spread geometry
# --------------------------------------------------------------------------- #
def _items(n, aspect=1.0):
    return [PhotoItem(path=f"p{i}.jpg", aspect_ratio=aspect) for i in range(n)]


def test_layout_empty():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    assert engine.layout([], spec) == []


def test_layout_chunks_into_expected_spreads():
    engine = AlbumLayoutEngine(max_per_spread=4)
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    items = _items(10)
    spreads = engine.layout(items, spec, per_spread=4)
    # 10 items, 4 per spread -> 3 spreads (4, 4, 2).
    assert len(spreads) == 3
    assert [len(s.placements) for s in spreads] == [4, 4, 2]
    for idx, s in enumerate(spreads):
        assert s.index == idx
        assert s.width_px == spec.spread_width_px
        assert s.height_px == spec.spread_height_px
    # Every photo placed exactly once -- nothing lost or duplicated.
    flat = [p.path for s in spreads for p in s.placements]
    assert sorted(flat) == sorted(it.path for it in items)
    assert len(flat) == len(set(flat))

    # Chronology is preserved at the *spread* level: spread k holds the k-th
    # chunk of the input. Within a spread, order may differ -- the engine
    # deliberately pairs photos to frames by aspect ratio
    # (AlbumLayoutEngine._assign_by_orientation: "portrait photos to tall
    # frames, landscape to wide ones"), so asserting a globally-unchanged
    # order would be asserting that feature is broken.
    expected_chunks = [
        {it.path for it in items[0:4]},
        {it.path for it in items[4:8]},
        {it.path for it in items[8:10]},
    ]
    assert [{p.path for p in s.placements} for s in spreads] == expected_chunks


def test_layout_per_spread_capped_at_max():
    engine = AlbumLayoutEngine(max_per_spread=2)
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    spreads = engine.layout(_items(5), spec, per_spread=4)
    # per_spread clamped to 2 -> ceil(5/2) = 3 spreads.
    assert len(spreads) == 3
    assert [len(s.placements) for s in spreads] == [2, 2, 1]


def test_layout_invalid_per_spread():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    with pytest.raises(AlbumLayoutError):
        engine.layout(_items(2), spec, per_spread=0)


def test_engine_invalid_max_per_spread():
    with pytest.raises(AlbumLayoutError):
        AlbumLayoutEngine(max_per_spread=0)


# --------------------------------------------------------------------------- #
# Cover-fit crop
# --------------------------------------------------------------------------- #
def test_cover_crop_wide_photo_into_square_frame():
    engine = AlbumLayoutEngine()
    # Single page, no margin/gutter so the one full frame is square-ish.
    spec = AlbumSpec(
        page_width_in=10,
        page_height_in=10,
        dpi=100,
        margin_in=0.0,
        bleed_in=0.0,
        double_page_spread=False,
    )
    item = PhotoItem(path="wide.jpg", aspect_ratio=2.0)
    spread = engine.layout([item], spec)[0]
    cx, cy, cw, ch = spread.placements[0].crop
    # Wide photo into square frame -> horizontal crop: w < 1, h == 1.
    assert cw < 1.0 - _EPS
    assert ch == pytest.approx(1.0, abs=1e-6)
    assert cw == pytest.approx(0.5, abs=1e-6)  # frame_ar 1 / photo_ar 2
    # Stays within bounds.
    assert 0 <= cx and cx + cw <= 1 + _EPS
    assert 0 <= cy and cy + ch <= 1 + _EPS


def test_cover_crop_tall_photo_into_square_frame():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(
        page_width_in=10,
        page_height_in=10,
        dpi=100,
        margin_in=0.0,
        bleed_in=0.0,
        double_page_spread=False,
    )
    item = PhotoItem(path="tall.jpg", aspect_ratio=0.5)
    spread = engine.layout([item], spec)[0]
    cx, cy, cw, ch = spread.placements[0].crop
    # Tall photo -> vertical crop: w == 1, h < 1.
    assert cw == pytest.approx(1.0, abs=1e-6)
    assert ch < 1.0 - _EPS
    assert ch == pytest.approx(0.5, abs=1e-6)
    assert 0 <= cx and cx + cw <= 1 + _EPS
    assert 0 <= cy and cy + ch <= 1 + _EPS


# --------------------------------------------------------------------------- #
# Face safety
# --------------------------------------------------------------------------- #
def _contains(crop, box) -> bool:
    cx, cy, cw, ch = crop
    bx, by, bw, bh = box
    return (
        cx - _EPS <= bx
        and cy - _EPS <= by
        and bx + bw <= cx + cw + _EPS
        and by + bh <= cy + ch + _EPS
    )


def test_face_safety_keeps_face_in_crop():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(
        page_width_in=10,
        page_height_in=10,
        dpi=100,
        margin_in=0.0,
        bleed_in=0.0,
        double_page_spread=False,
    )
    # Wide photo into square frame forces a horizontal crop (cw == 0.5).
    # A face near the right edge would be cut by a centered crop; the
    # engine must shift the window to keep it visible.
    face = (0.72, 0.4, 0.2, 0.2)  # small enough to fit in a 0.5-wide window
    item = PhotoItem(path="wide_face.jpg", aspect_ratio=2.0, face_boxes=(face,))
    spread = engine.layout([item], spec)[0]
    crop = spread.placements[0].crop
    assert crop[2] == pytest.approx(0.5, abs=1e-6)  # horizontal crop confirmed
    assert _contains(crop, face), f"face {face} not inside crop {crop}"


def test_face_safety_tall_photo_vertical_face():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(
        page_width_in=10,
        page_height_in=10,
        dpi=100,
        margin_in=0.0,
        bleed_in=0.0,
        double_page_spread=False,
    )
    face = (0.4, 0.78, 0.2, 0.18)  # near bottom edge
    item = PhotoItem(path="tall_face.jpg", aspect_ratio=0.5, face_boxes=(face,))
    spread = engine.layout([item], spec)[0]
    crop = spread.placements[0].crop
    assert crop[3] == pytest.approx(0.5, abs=1e-6)  # vertical crop confirmed
    assert _contains(crop, face), f"face {face} not inside crop {crop}"


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_layout_is_deterministic():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300, gutter_in=0.25)
    items = [
        PhotoItem(path="a.jpg", aspect_ratio=1.5, face_boxes=((0.1, 0.1, 0.2, 0.2),)),
        PhotoItem(path="b.jpg", aspect_ratio=0.75),
        PhotoItem(path="c.jpg", aspect_ratio=2.0, face_boxes=((0.7, 0.5, 0.2, 0.2),)),
    ]
    a = engine.layout(items, spec, per_spread=3)
    b = engine.layout(items, spec, per_spread=3)
    assert a == b


def test_double_page_frames_do_not_straddle_gutter():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300, gutter_in=0.5)
    spreads = engine.layout(_items(2), spec, per_spread=2)
    gutter_px = round(spec.gutter_in * spec.dpi)
    center = spec.spread_width_px / 2.0
    left_edge = center - gutter_px / 2.0
    right_edge = center + gutter_px / 2.0
    for p in spreads[0].placements:
        x, _, w, _ = p.frame_px
        # Each frame lies wholly on one side of the gutter band.
        assert x + w <= left_edge + 1 or x >= right_edge - 1


# --------------------------------------------------------------------------- #
# Fit mode (heroes fill, collages contain) + orientation-aware assignment
# --------------------------------------------------------------------------- #
def test_single_photo_is_full_bleed_cover():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    spreads = engine.layout([PhotoItem(path="hero.jpg", aspect_ratio=1.5)], spec, per_spread=1)
    assert spreads[0].placements[0].fit == "cover"


def test_collage_cells_use_contain_and_full_crop():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    items = [
        PhotoItem(path="a.jpg", aspect_ratio=1.5),
        PhotoItem(path="b.jpg", aspect_ratio=0.7),
        PhotoItem(path="c.jpg", aspect_ratio=1.0),
    ]
    spreads = engine.layout(items, spec, per_spread=3)
    for p in spreads[0].placements:
        assert p.fit == "contain"
        assert p.crop == (0.0, 0.0, 1.0, 1.0)  # whole photo, nothing cropped


def test_orientation_assignment_puts_portrait_in_tallest_frame():
    engine = AlbumLayoutEngine()
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    # One strong portrait + two landscapes; the 3-up template has one tall
    # (left) cell and two short (right) cells.
    items = [
        PhotoItem(path="land1.jpg", aspect_ratio=1.8),
        PhotoItem(path="portrait.jpg", aspect_ratio=0.5),
        PhotoItem(path="land2.jpg", aspect_ratio=1.7),
    ]
    spreads = engine.layout(items, spec, per_spread=3)
    placements = {p.path: p for p in spreads[0].placements}
    tallest = max(p.frame_px[3] for p in spreads[0].placements)
    assert placements["portrait.jpg"].frame_px[3] == tallest


# --------------------------------------------------------------------------- #
# Designed template library (choose_template)
# --------------------------------------------------------------------------- #
def _aspect(frame, spec):
    return (frame.w * spec.spread_width_px) / (frame.h * spec.spread_height_px)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_choose_template_returns_valid_count(count):
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    frames = choose_template([1.0] * count, spec, variant_index=0)
    assert len(frames) == count
    assert _frames_within_bounds(frames)
    assert _no_overlap(frames)


def test_choose_template_prefers_tall_frames_for_portraits():
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    frames = choose_template([0.6, 0.6, 0.6, 0.6], spec)
    # Four portraits should land in tall columns, not wide grid cells.
    assert all(_aspect(f, spec) < 1.2 for f in frames)


def test_choose_template_prefers_wide_frames_for_landscapes():
    """
    Template selection should favour wide frames for landscape photos.

    Note it cannot make *every* frame wide: on a square-page spread the
    best-fitting 3-frame arrangement for three 1.9 landscapes is
    ``[0.98, 2.0, 2.0]``, whose summed log-aspect mismatch (0.765) beats every
    all-wide alternative -- e.g. ``[2, 2, 4]`` scores 0.847 and three stacked
    full-width rows ``[6, 6, 6]`` scores 3.45. So the meaningful assertions are
    that frames skew wide and that selection genuinely responds to photo
    aspect, not that every frame is individually > 1.0.
    """
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    landscapes = [1.9, 1.9, 1.9]
    portraits = [0.6, 0.6, 0.6]

    frames = choose_template(landscapes, spec)
    aspects = [_aspect(f, spec) for f in frames]

    # Skews wide: most frames wide, and wide on average.
    assert sum(1 for a in aspects if a > 1.0) >= 2
    assert sum(aspects) / len(aspects) > 1.0

    # Responds to aspect: the template picked for landscapes fits landscapes
    # better than the one picked for portraits does.
    def _mismatch(frs, photos):
        fr = sorted(_aspect(f, spec) for f in frs)
        return sum(abs(math.log(a / b)) for a, b in zip(fr, sorted(photos)))

    assert _mismatch(frames, landscapes) < _mismatch(
        choose_template(portraits, spec), landscapes
    )


def test_choose_template_avoids_gutter_when_requested():
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300, gutter_in=0.5)
    frames = choose_template([1.0, 1.0, 1.0], spec, avoid_gutter=True)
    assert not any(f.x < 0.5 - _EPS and f.x + f.w > 0.5 + _EPS for f in frames)


def test_choose_template_is_deterministic():
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    a = choose_template([1.5, 0.6, 1.0], spec, variant_index=2)
    b = choose_template([1.5, 0.6, 1.0], spec, variant_index=2)
    assert a == b
