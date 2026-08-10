"""
Tests for the second round of collage features in core.collage:
new layouts, backgrounds, per-photo adjustments/filters, and print safety.

The original geometry/theme tests live in test_collage.py.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from core.collage import (
    BACKGROUND_STYLES,
    BG_BLURRED_PHOTO,
    BG_GRADIENT,
    BG_IMAGE,
    BG_SOLID,
    FILTER_BW,
    FILTER_COOL,
    FILTER_NONE,
    FILTER_SEPIA,
    FILTER_VIVID,
    FILTER_WARM,
    FILTERS,
    LAYOUT_FILMSTRIP,
    LAYOUT_GRID,
    LAYOUT_MAGAZINE,
    LAYOUT_MASONRY,
    LAYOUTS,
    MIN_PRINT_PPI,
    THEMES,
    Background,
    Cell,
    CollageError,
    CollagePhoto,
    CollageSpec,
    PhotoAdjust,
    PrintMarks,
    add_print_marks,
    apply_filter,
    build_collage,
    check_resolution,
    crop_to_cell,
    filmstrip_cells,
    layout_cells,
    magazine_cells,
    masonry_cells,
    render_background,
)


def _photo(w=400, h=300, color=(120, 140, 160), adjust=None, path=None) -> CollagePhoto:
    return CollagePhoto(
        image=Image.new("RGB", (w, h), color),
        adjust=adjust or PhotoAdjust(),
        path=path,
    )


def _mixed(n=6) -> list[CollagePhoto]:
    sizes = [(600, 400), (400, 600), (500, 500), (700, 400), (450, 650), (600, 600)]
    return [_photo(*sizes[i % len(sizes)], color=(40 + i * 30 % 200, 90, 150)) for i in range(n)]


def _spec(w=1000, h=800) -> CollageSpec:
    return CollageSpec(width_px=w, height_px=h)


def _seamless():
    return THEMES["Seamless"]


def _overlaps(a: Cell, b: Cell) -> bool:
    return not (
        a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y
    )


# --------------------------------------------------------------------------- #
# New layouts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("layout", [LAYOUT_MASONRY, LAYOUT_MAGAZINE, LAYOUT_FILMSTRIP])
def test_new_layouts_produce_one_cell_per_photo_without_overlap(layout):
    spec = _spec()
    photos = _mixed(6)
    cells = layout_cells(photos, spec, _seamless(), layout=layout)
    assert len(cells) == len(photos)
    for cell in cells:
        assert cell.w > 0 and cell.h > 0
        assert cell.x >= 0 and cell.y >= 0
        assert cell.x + cell.w <= spec.width_px + 1
        assert cell.y + cell.h <= spec.height_px + 1
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            assert not _overlaps(cells[i], cells[j]), f"{layout}: {cells[i]} vs {cells[j]}"


def test_masonry_columns_share_one_width_and_span_full_height():
    spec = _spec(900, 900)
    cells = masonry_cells(_mixed(8), spec, _seamless())
    by_column: dict[int, list[Cell]] = {}
    for cell in cells:
        by_column.setdefault(cell.x, []).append(cell)
    widths = {cell.w for cell in cells}
    assert len(widths) == 1, "masonry columns must all be the same width"
    for column in by_column.values():
        bottom = max(c.y + c.h for c in column)
        assert bottom == pytest.approx(spec.height_px, abs=2)


def test_masonry_covers_essentially_the_whole_canvas():
    spec = _spec(900, 900)
    cells = masonry_cells(_mixed(8), spec, _seamless())
    covered = sum(c.w * c.h for c in cells)
    assert covered >= 0.97 * spec.width_px * spec.height_px


def test_filmstrip_is_a_single_row_on_a_wide_canvas():
    cells = filmstrip_cells(5, _spec(2000, 500), _seamless())
    assert len({c.y for c in cells}) == 1
    assert all(c.h == cells[0].h for c in cells)


def test_filmstrip_is_a_single_column_on_a_tall_canvas():
    cells = filmstrip_cells(5, _spec(500, 2000), _seamless())
    assert len({c.x for c in cells}) == 1


def test_magazine_hero_spans_the_full_width():
    spec = _spec(1200, 900)
    cells = magazine_cells(6, spec, _seamless())
    assert cells[0].w == pytest.approx(spec.width_px, abs=2)
    assert cells[0].w * cells[0].h == max(c.w * c.h for c in cells)


def test_magazine_wraps_a_large_bottom_band_into_two_rows():
    cells = magazine_cells(8, _spec(1200, 900), _seamless())
    band_rows = {c.y for c in cells[1:]}
    assert len(band_rows) == 2


def test_magazine_single_photo_fills_the_canvas():
    cells = magazine_cells(1, _spec(800, 800), _seamless())
    assert len(cells) == 1
    assert cells[0].w == pytest.approx(800, abs=2)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_including_new_ones_builds(layout):
    img = build_collage(_mixed(6), _spec(600, 500), layout=layout)
    assert img.size == (600, 500)


# --------------------------------------------------------------------------- #
# Backgrounds
# --------------------------------------------------------------------------- #
def test_solid_background_uses_its_own_color():
    bg = render_background(_spec(50, 40), _seamless(), Background(style=BG_SOLID, color=(10, 20, 30)))
    assert bg.size == (50, 40)
    assert np.asarray(bg)[0, 0].tolist() == [10, 20, 30]


def test_background_defaults_to_the_theme_color_when_none():
    theme = THEMES["Gallery Dark"]
    bg = render_background(_spec(20, 20), theme, None)
    assert np.asarray(bg)[0, 0].tolist() == list(theme.background)


def test_vertical_gradient_changes_down_the_canvas():
    bg = render_background(
        _spec(40, 200),
        _seamless(),
        Background(style=BG_GRADIENT, color=(0, 0, 0), color2=(255, 255, 255),
                   gradient_vertical=True),
    )
    arr = np.asarray(bg).astype(int)
    assert arr[0].mean() < arr[-1].mean()
    # Rows should be uniform horizontally for a vertical gradient.
    assert arr[100].std(axis=0).max() < 2


def test_horizontal_gradient_changes_across_the_canvas():
    bg = render_background(
        _spec(200, 40),
        _seamless(),
        Background(style=BG_GRADIENT, color=(0, 0, 0), color2=(255, 255, 255),
                   gradient_vertical=False),
    )
    arr = np.asarray(bg).astype(int)
    assert arr[:, 0].mean() < arr[:, -1].mean()


def test_blurred_photo_background_is_blurred_and_darkened():
    sharp = Image.new("RGB", (200, 200), (255, 255, 255))
    # Hard edge that blurring should soften.
    sharp.paste(Image.new("RGB", (100, 200), (0, 0, 0)), (0, 0))
    photo = CollagePhoto(image=sharp)

    bg = render_background(
        _spec(200, 200), _seamless(),
        Background(style=BG_BLURRED_PHOTO, blur_radius_frac=0.08, darken=0.4),
        [photo],
    )
    arr = np.asarray(bg).astype(int)
    # Darkened: the white half is no longer near-white.
    assert arr[:, -10:].mean() < 200
    # Blurred: mid-line values sit between the two extremes.
    column = arr[100, 95:105].mean()
    assert 10 < column < 245


def test_blurred_photo_background_without_photos_falls_back_to_theme():
    bg = render_background(
        _spec(30, 30), THEMES["Gallery Dark"], Background(style=BG_BLURRED_PHOTO), []
    )
    assert np.asarray(bg)[0, 0].tolist() == list(THEMES["Gallery Dark"].background)


def test_image_background_covers_the_canvas(tmp_path):
    src = tmp_path / "bg.jpg"
    Image.new("RGB", (100, 400), (10, 200, 10)).save(src)
    bg = render_background(
        _spec(300, 300), _seamless(),
        Background(style=BG_IMAGE, image_path=src, darken=0.0),
    )
    assert bg.size == (300, 300)
    assert np.asarray(bg)[:, :, 1].mean() > 150  # still green, i.e. cover-cropped


def test_image_background_errors_clearly():
    with pytest.raises(CollageError, match="needs an image_path"):
        render_background(_spec(10, 10), _seamless(), Background(style=BG_IMAGE))
    with pytest.raises(CollageError, match="Could not open"):
        render_background(
            _spec(10, 10), _seamless(),
            Background(style=BG_IMAGE, image_path="/nope/missing.jpg"),
        )


def test_unknown_background_style_raises():
    with pytest.raises(CollageError, match="Unknown background style"):
        render_background(_spec(10, 10), _seamless(), Background(style="plaid"))


def test_all_background_styles_are_reachable_through_build_collage(tmp_path):
    src = tmp_path / "bg.png"
    Image.new("RGB", (80, 80), (200, 100, 50)).save(src)
    for style in BACKGROUND_STYLES:
        bg = Background(style=style, image_path=src if style == BG_IMAGE else None)
        img = build_collage(_mixed(4), _spec(300, 300), background=bg, layout=LAYOUT_GRID)
        assert img.size == (300, 300)


# --------------------------------------------------------------------------- #
# Filters and per-photo adjustments
# --------------------------------------------------------------------------- #
def test_filter_none_is_a_passthrough():
    img = Image.new("RGB", (10, 10), (200, 100, 50))
    assert apply_filter(img, FILTER_NONE) is img


def test_bw_filter_removes_colour():
    out = apply_filter(Image.new("RGB", (10, 10), (200, 40, 40)), FILTER_BW)
    arr = np.asarray(out)
    assert arr[..., 0].std() == 0
    assert abs(int(arr[0, 0, 0]) - int(arr[0, 0, 2])) <= 1  # R == B => grey


def test_sepia_filter_warms_the_image():
    out = np.asarray(apply_filter(Image.new("RGB", (10, 10), (128, 128, 128)), FILTER_SEPIA))
    assert out[0, 0, 0] > out[0, 0, 2]  # red above blue


def test_warm_and_cool_filters_push_opposite_channels():
    base = Image.new("RGB", (10, 10), (120, 120, 120))
    warm = np.asarray(apply_filter(base, FILTER_WARM)).astype(int)
    cool = np.asarray(apply_filter(base, FILTER_COOL)).astype(int)
    assert warm[0, 0, 0] > cool[0, 0, 0]  # warmer red
    assert cool[0, 0, 2] > warm[0, 0, 2]  # cooler blue


def test_vivid_filter_increases_saturation():
    import colorsys

    base = Image.new("RGB", (10, 10), (180, 90, 90))
    out = np.asarray(apply_filter(base, FILTER_VIVID))[0, 0]
    before = colorsys.rgb_to_hsv(180 / 255, 90 / 255, 90 / 255)[1]
    after = colorsys.rgb_to_hsv(*(v / 255 for v in out))[1]
    assert after > before


def test_unknown_filter_raises():
    with pytest.raises(CollageError, match="Unknown filter"):
        apply_filter(Image.new("RGB", (4, 4)), "lomo")


@pytest.mark.parametrize("filter_name", FILTERS)
def test_every_filter_survives_a_full_build(filter_name):
    photos = [_photo(adjust=PhotoAdjust(filter_name=filter_name)) for _ in range(4)]
    assert build_collage(photos, _spec(300, 300), layout=LAYOUT_GRID).size == (300, 300)


def test_zoom_crops_tighter():
    """Zooming should show less of the source, i.e. a smaller crop window."""
    from core.collage import _adjusted_cover_box

    plain = _photo(800, 800)
    zoomed = _photo(800, 800, adjust=PhotoAdjust(zoom=2.0))
    p_box = _adjusted_cover_box(plain, 1.0)
    z_box = _adjusted_cover_box(zoomed, 1.0)
    p_area = (p_box[2] - p_box[0]) * (p_box[3] - p_box[1])
    z_area = (z_box[2] - z_box[0]) * (z_box[3] - z_box[1])
    assert z_area < p_area


def test_pan_offset_moves_the_crop_window():
    from core.collage import _adjusted_cover_box

    centered = _photo(800, 400, adjust=PhotoAdjust(zoom=1.5))
    panned = _photo(800, 400, adjust=PhotoAdjust(zoom=1.5, offset_x=0.2))
    assert _adjusted_cover_box(panned, 1.0)[0] > _adjusted_cover_box(centered, 1.0)[0]


def test_pan_stays_inside_the_image():
    from core.collage import _adjusted_cover_box

    photo = _photo(400, 400, adjust=PhotoAdjust(zoom=1.2, offset_x=5.0, offset_y=-5.0))
    x0, y0, x1, y1 = _adjusted_cover_box(photo, 1.0)
    assert 0 <= x0 < x1 <= 400
    assert 0 <= y0 < y1 <= 400


def test_rotation_still_fills_the_cell_exactly():
    tile = crop_to_cell(_photo(600, 400, adjust=PhotoAdjust(rotate_deg=12)), Cell(0, 0, 200, 150))
    assert tile.size == (200, 150)


def test_identity_adjust_is_reported_as_such():
    assert PhotoAdjust().is_identity is True
    assert PhotoAdjust(zoom=1.4).is_identity is False
    assert PhotoAdjust(filter_name=FILTER_BW).is_identity is False
    assert PhotoAdjust(beautify=True).is_identity is False


def test_beautify_flag_runs_without_crashing():
    photo = _photo(300, 300, adjust=PhotoAdjust(beautify=True))
    assert crop_to_cell(photo, Cell(0, 0, 150, 150)).size == (150, 150)


# --------------------------------------------------------------------------- #
# Print safety
# --------------------------------------------------------------------------- #
def test_low_resolution_photos_are_flagged():
    spec = CollageSpec(3000, 2400)  # 10x8in at 300dpi
    photos = [_photo(120, 90, path=None) for _ in range(4)]
    cells = layout_cells(photos, spec, _seamless(), layout=LAYOUT_GRID)
    warnings = check_resolution(photos, cells, spec)
    assert len(warnings) == 4
    assert all(w.effective_ppi < MIN_PRINT_PPI for w in warnings)
    assert "may look soft in print" in warnings[0].message


def test_high_resolution_photos_are_not_flagged():
    spec = CollageSpec(1200, 900)
    photos = [_photo(3000, 2400) for _ in range(4)]
    cells = layout_cells(photos, spec, _seamless(), layout=LAYOUT_GRID)
    assert check_resolution(photos, cells, spec) == []


def test_resolution_warning_names_the_file(tmp_path):
    from pathlib import Path

    spec = CollageSpec(3000, 2400)
    photos = [_photo(100, 80, path=Path("bride_sneak.jpg"))]
    cells = layout_cells(photos, spec, _seamless(), layout=LAYOUT_GRID)
    warnings = check_resolution(photos, cells, spec)
    assert "bride_sneak.jpg" in warnings[0].message


def test_resolution_threshold_is_adjustable():
    spec = CollageSpec(1000, 1000)
    photos = [_photo(500, 500)]
    cells = layout_cells(photos, spec, _seamless(), layout=LAYOUT_GRID)
    assert check_resolution(photos, cells, spec, min_ppi=1) == []
    assert check_resolution(photos, cells, spec, min_ppi=100_000) != []


def test_print_marks_disabled_returns_the_same_image():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    assert add_print_marks(img, PrintMarks(), _spec(100, 100)) is img


def test_bleed_grows_the_canvas_on_all_sides():
    spec = CollageSpec(400, 300)
    img = Image.new("RGB", (400, 300), (30, 60, 90))
    out = add_print_marks(img, PrintMarks(bleed_frac=0.05), spec)
    bleed = round(0.05 * spec.short_edge)
    assert out.size == (400 + bleed * 2, 300 + bleed * 2)
    # Bleed is mirrored from the edges, so it must not be blank white paper.
    assert np.asarray(out)[0, out.width // 2].tolist() != [255, 255, 255]


def test_trim_marks_draw_without_bleed():
    spec = CollageSpec(200, 200)
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    out = add_print_marks(img, PrintMarks(trim_marks=True, mark_color=(0, 0, 0)), spec)
    assert out.size == (200, 200)
    assert np.asarray(out).min() < 50  # some black pixels were drawn


def test_marks_are_reachable_through_build_collage():
    spec = _spec(400, 400)
    img = build_collage(
        _mixed(4), spec, layout=LAYOUT_GRID,
        marks=PrintMarks(bleed_frac=0.04, trim_marks=True),
    )
    bleed = round(0.04 * spec.short_edge)
    assert img.size == (400 + bleed * 2, 400 + bleed * 2)
