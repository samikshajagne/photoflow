"""
Unit tests for core.collage.

Covers the geometry (cells must tile the canvas without gaps or overlaps), the
face-aware cropping that keeps heads intact, the mosaic's self-tuning row
count, layout/theme application, determinism, and the awkward edge cases
(single photo, extreme aspect ratios, spacing that swallows the canvas).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from core.collage import (
    DEFAULT_THEME,
    LAYOUT_FEATURE,
    LAYOUT_GRID,
    LAYOUT_MOSAIC,
    LAYOUT_SCATTER,
    LAYOUTS,
    SIZE_PRESETS,
    THEMES,
    Cell,
    CollageError,
    CollagePhoto,
    CollageSpec,
    assign_by_orientation,
    build_collage,
    crop_to_cell,
    face_aware_cover_box,
    grid_dimensions,
    layout_cells,
    mosaic_cells,
    pick_hero_index,
    save_collage,
    suggest_layout,
)


def _photo(w: int = 400, h: int = 300, faces=(), color=(120, 140, 160)) -> CollagePhoto:
    return CollagePhoto(image=Image.new("RGB", (w, h), color), face_boxes=tuple(faces))


def _photos(n: int, w: int = 400, h: int = 300) -> list[CollagePhoto]:
    return [_photo(w, h, color=(50 + i * 7 % 200, 90, 140)) for i in range(n)]


def _mixed_photos() -> list[CollagePhoto]:
    """A realistic mix: landscapes, portraits and a square."""
    return [
        _photo(600, 400),
        _photo(300, 500),
        _photo(400, 400),
        _photo(800, 450),
        _photo(350, 600),
    ]


def _spec(w: int = 1200, h: int = 900) -> CollageSpec:
    return CollageSpec(width_px=w, height_px=h)


def _theme(name: str = DEFAULT_THEME):
    return THEMES[name]


# --------------------------------------------------------------------------- #
# Spec validation
# --------------------------------------------------------------------------- #
def test_spec_rejects_nonpositive_size():
    with pytest.raises(CollageError):
        CollageSpec(width_px=0, height_px=100)
    with pytest.raises(CollageError):
        CollageSpec(width_px=100, height_px=-5)


def test_spec_rejects_bad_dpi():
    with pytest.raises(CollageError):
        CollageSpec(width_px=100, height_px=100, dpi=0)


def test_size_presets_are_all_positive():
    assert SIZE_PRESETS
    for name, (w, h) in SIZE_PRESETS.items():
        assert w > 0 and h > 0, name


# --------------------------------------------------------------------------- #
# Face-aware cropping -- the core "smart" behaviour
# --------------------------------------------------------------------------- #
def test_cover_box_has_requested_aspect_and_fits_inside_image():
    box = face_aware_cover_box(800, 600, target_aspect=1.0)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 800
    assert 0 <= y0 < y1 <= 600
    assert (x1 - x0) / (y1 - y0) == pytest.approx(1.0, rel=0.02)


def test_cover_box_centers_when_no_faces():
    x0, y0, x1, y1 = face_aware_cover_box(800, 400, target_aspect=1.0)
    # Square crop from an 800x400 frame -> 400x400 centred horizontally.
    assert (x1 - x0, y1 - y0) == (400, 400)
    assert x0 == pytest.approx(200, abs=1)


def test_cover_box_keeps_an_off_center_face_inside():
    """A face near the top of a tall photo must survive a wide crop -- the
    naive centre-crop is exactly what decapitates people."""
    face = (0.40, 0.02, 0.20, 0.18)  # near the top edge
    x0, y0, x1, y1 = face_aware_cover_box(600, 1200, target_aspect=1.5, face_boxes=[face])

    fx0, fy0 = face[0] * 600, face[1] * 1200
    fx1, fy1 = fx0 + face[2] * 600, fy0 + face[3] * 1200
    assert x0 <= fx0 and x1 >= fx1, "face cropped horizontally"
    assert y0 <= fy0 and y1 >= fy1, "face cropped vertically"


def test_cover_box_keeps_multiple_faces_inside_when_geometrically_possible():
    faces = [(0.10, 0.35, 0.12, 0.14), (0.75, 0.38, 0.12, 0.14)]
    x0, y0, x1, y1 = face_aware_cover_box(1000, 1000, 1.6, face_boxes=faces)
    for fx, fy, fw, fh in faces:
        assert x0 <= fx * 1000 and x1 >= (fx + fw) * 1000
        assert y0 <= fy * 1000 and y1 >= (fy + fh) * 1000


def test_cover_box_handles_faces_wider_than_the_crop_window():
    """A group spanning the whole frame can't fully fit a narrow crop; it must
    still return a valid in-bounds box rather than raising."""
    faces = [(0.02, 0.4, 0.96, 0.2)]
    x0, y0, x1, y1 = face_aware_cover_box(1000, 1000, 0.5, face_boxes=faces)
    assert 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000


def test_cover_box_rejects_bad_inputs():
    with pytest.raises(CollageError):
        face_aware_cover_box(0, 100, 1.0)
    with pytest.raises(CollageError):
        face_aware_cover_box(100, 100, 0.0)


def test_crop_to_cell_returns_exact_cell_size():
    tile = crop_to_cell(_photo(800, 600), Cell(x=0, y=0, w=317, h=211))
    assert tile.size == (317, 211)


# --------------------------------------------------------------------------- #
# Grid geometry
# --------------------------------------------------------------------------- #
def test_grid_dimensions_are_squarish_and_sufficient():
    for count in range(1, 17):
        cols, rows = grid_dimensions(count, canvas_aspect=1.0)
        assert cols * rows >= count
        assert abs(cols - rows) <= 2, (count, cols, rows)


def test_grid_dimensions_follow_canvas_shape():
    wide_cols, wide_rows = grid_dimensions(6, canvas_aspect=3.0)
    tall_cols, tall_rows = grid_dimensions(6, canvas_aspect=1 / 3)
    assert wide_cols > wide_rows
    assert tall_rows > tall_cols


def test_grid_dimensions_rejects_zero():
    with pytest.raises(CollageError):
        grid_dimensions(0, 1.0)


# --------------------------------------------------------------------------- #
# Cells tile the canvas (per layout)
# --------------------------------------------------------------------------- #
def _overlaps(a: Cell, b: Cell) -> bool:
    return not (
        a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y
    )


@pytest.mark.parametrize("layout", [LAYOUT_GRID, LAYOUT_MOSAIC, LAYOUT_FEATURE])
def test_cells_do_not_overlap_and_stay_in_bounds(layout):
    spec = _spec()
    photos = _mixed_photos()
    cells = layout_cells(photos, spec, _theme("Seamless"), layout=layout)
    assert len(cells) == len(photos)
    for cell in cells:
        assert cell.w > 0 and cell.h > 0
        assert cell.x >= 0 and cell.y >= 0
        assert cell.x + cell.w <= spec.width_px + 1
        assert cell.y + cell.h <= spec.height_px + 1
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            assert not _overlaps(cells[i], cells[j]), f"{layout}: {cells[i]} vs {cells[j]}"


def test_seamless_mosaic_covers_essentially_the_whole_canvas():
    """With no margin or spacing the mosaic should leave no visible gaps."""
    spec = _spec(1200, 900)
    cells = mosaic_cells(_mixed_photos(), spec, _theme("Seamless"))
    covered = sum(c.w * c.h for c in cells)
    assert covered >= 0.985 * spec.width_px * spec.height_px


def test_mosaic_rows_span_the_full_width():
    spec = _spec(1200, 900)
    cells = mosaic_cells(_mixed_photos(), spec, _theme("Seamless"))
    rows: dict[int, list[Cell]] = {}
    for cell in cells:
        rows.setdefault(cell.y, []).append(cell)
    for row_cells in rows.values():
        left = min(c.x for c in row_cells)
        right = max(c.x + c.w for c in row_cells)
        assert left == pytest.approx(0, abs=2)
        assert right == pytest.approx(spec.width_px, abs=2)


def test_mosaic_needs_less_cropping_than_grid_for_mixed_orientations():
    """The mosaic's whole reason to exist: it bends cells to the photos rather
    than cropping the photos to fit uniform cells."""
    import math

    spec = _spec(1200, 900)
    theme = _theme("Seamless")
    photos = _mixed_photos()

    def total_mismatch(cells):
        pairs = zip(photos, cells)
        return sum(abs(math.log(p.aspect / c.aspect)) for p, c in pairs)

    mosaic = total_mismatch(mosaic_cells(photos, spec, theme))
    grid = total_mismatch(layout_cells(photos, spec, theme, layout=LAYOUT_GRID))
    assert mosaic < grid


def test_feature_layout_gives_the_first_cell_the_largest_area():
    spec = _spec(1600, 900)
    cells = layout_cells(_photos(5), spec, _theme("Seamless"), layout=LAYOUT_FEATURE)
    areas = [c.w * c.h for c in cells]
    assert areas[0] == max(areas)
    assert areas[0] > 2 * sorted(areas)[-2]


def test_feature_layout_orients_to_the_canvas():
    photos = _photos(4)
    theme = _theme("Seamless")
    wide = layout_cells(photos, _spec(1600, 900), theme, layout=LAYOUT_FEATURE)
    tall = layout_cells(photos, _spec(900, 1600), theme, layout=LAYOUT_FEATURE)
    # Landscape canvas: hero occupies full height. Portrait: full width.
    assert wide[0].h > wide[0].w * 0.5
    assert tall[0].w == pytest.approx(900, abs=2)


def test_single_photo_fills_the_canvas_in_every_layout():
    spec = _spec(1000, 1000)
    for layout in LAYOUTS:
        cells = layout_cells(_photos(1), spec, _theme("Seamless"), layout=layout)
        assert len(cells) == 1
        if layout != LAYOUT_SCATTER:  # scatter deliberately jitters/oversizes
            assert cells[0].w == pytest.approx(1000, abs=2)
            assert cells[0].h == pytest.approx(1000, abs=2)


def test_layout_rejects_unknown_name_and_empty_input():
    with pytest.raises(CollageError):
        layout_cells(_photos(3), _spec(), _theme(), layout="spiral")
    with pytest.raises(CollageError):
        layout_cells([], _spec(), _theme(), layout=LAYOUT_GRID)


# --------------------------------------------------------------------------- #
# Shape-aware assignment and hero picking
# --------------------------------------------------------------------------- #
def test_assign_by_orientation_puts_portraits_in_tall_cells():
    portrait = _photo(300, 600)
    landscape = _photo(600, 300)
    tall = Cell(x=0, y=0, w=100, h=200)
    wide = Cell(x=100, y=0, w=200, h=100)

    pairs = assign_by_orientation([landscape, portrait], [tall, wide])
    mapping = {id(cell): photo for photo, cell in pairs}
    assert mapping[id(tall)] is portrait
    assert mapping[id(wide)] is landscape


def test_assign_by_orientation_preserves_cell_order():
    cells = [Cell(0, 0, 10, 20), Cell(10, 0, 20, 10), Cell(30, 0, 15, 15)]
    pairs = assign_by_orientation(_photos(3), cells)
    assert [cell for _photo, cell in pairs] == cells


def test_pick_hero_prefers_the_prominent_face():
    small_face = _photo(1000, 1000, faces=[(0.4, 0.4, 0.05, 0.05)])
    big_face = _photo(800, 800, faces=[(0.3, 0.2, 0.35, 0.40)])
    no_face = _photo(4000, 4000)
    assert pick_hero_index([small_face, big_face, no_face]) == 1


def test_pick_hero_falls_back_to_resolution_without_faces():
    assert pick_hero_index([_photo(400, 300), _photo(2000, 1500)]) == 1


def test_pick_hero_rejects_empty():
    with pytest.raises(CollageError):
        pick_hero_index([])


def test_suggest_layout_reacts_to_the_photo_set():
    assert suggest_layout(_photos(1), _spec()) == LAYOUT_FEATURE
    assert suggest_layout(_mixed_photos(), _spec()) == LAYOUT_MOSAIC  # mixed shapes
    uniform = [_photo(400, 400) for _ in range(4)]
    assert suggest_layout(uniform, _spec()) == LAYOUT_GRID


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("layout", LAYOUTS)
def test_build_collage_returns_exact_spec_size(layout):
    spec = _spec(800, 600)
    img = build_collage(_mixed_photos(), spec, _theme(), layout=layout)
    assert img.size == (800, 600)
    assert img.mode == "RGB"


@pytest.mark.parametrize("theme_name", list(THEMES))
def test_every_theme_renders(theme_name):
    img = build_collage(_photos(4), _spec(600, 600), THEMES[theme_name], layout=LAYOUT_GRID)
    assert img.size == (600, 600)


def test_build_collage_actually_places_photo_content():
    """A rendered collage must contain the photos, not just background."""
    photos = [_photo(400, 400, color=(220, 30, 40)) for _ in range(4)]
    img = build_collage(photos, _spec(600, 600), THEMES["Seamless"], layout=LAYOUT_GRID)
    arr = np.asarray(img)
    reds = ((arr[:, :, 0] > 180) & (arr[:, :, 1] < 90)).mean()
    assert reds > 0.8


def test_themes_differ_visibly():
    photos = _photos(4)
    spec = _spec(600, 600)
    light = np.asarray(build_collage(photos, spec, THEMES["Classic White"], layout=LAYOUT_GRID))
    dark = np.asarray(build_collage(photos, spec, THEMES["Gallery Dark"], layout=LAYOUT_GRID))
    assert light.mean() > dark.mean()


def test_build_collage_rejects_empty_photo_list():
    with pytest.raises(CollageError):
        build_collage([], _spec())


def test_scatter_is_deterministic_per_seed_and_varies_across_seeds():
    photos = _photos(5)
    spec = _spec(800, 800)
    a = np.asarray(build_collage(photos, spec, _theme(), layout=LAYOUT_SCATTER, seed=1))
    b = np.asarray(build_collage(photos, spec, _theme(), layout=LAYOUT_SCATTER, seed=1))
    c = np.asarray(build_collage(photos, spec, _theme(), layout=LAYOUT_SCATTER, seed=2))
    assert np.array_equal(a, b), "same seed must reproduce the same collage"
    assert not np.array_equal(a, c), "different seed should rearrange"


def test_feature_layout_promotes_the_hero_photo():
    """The photo with the prominent face should end up in the big cell."""
    hero = _photo(800, 800, faces=[(0.3, 0.2, 0.4, 0.45)], color=(240, 20, 20))
    others = [_photo(400, 400, color=(20, 20, 240)) for _ in range(3)]
    spec = _spec(1200, 800)
    img = build_collage([*others, hero], spec, THEMES["Seamless"], layout=LAYOUT_FEATURE)
    arr = np.asarray(img)
    # Hero cell is on the left for a landscape canvas: it should be mostly red.
    left = arr[:, : spec.width_px // 3]
    assert ((left[:, :, 0] > 180) & (left[:, :, 2] < 90)).mean() > 0.8


def test_many_photos_still_render():
    img = build_collage(_photos(24), _spec(1200, 1200), _theme(), layout=LAYOUT_MOSAIC)
    assert img.size == (1200, 1200)


def test_extreme_aspect_photos_render():
    photos = [_photo(2000, 200), _photo(200, 2000), _photo(500, 500)]
    img = build_collage(photos, _spec(900, 900), _theme(), layout=LAYOUT_MOSAIC)
    assert img.size == (900, 900)


def test_oversized_margin_does_not_crash():
    from core.collage import CollageTheme

    greedy = CollageTheme(name="Greedy", margin_frac=0.9, spacing_frac=0.4)
    img = build_collage(_photos(4), _spec(400, 400), greedy, layout=LAYOUT_GRID)
    assert img.size == (400, 400)


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ext", [".jpg", ".png", ".pdf"])
def test_save_collage_writes_supported_formats(tmp_path, ext):
    img = build_collage(_photos(3), _spec(400, 300), _theme(), layout=LAYOUT_GRID)
    out = save_collage(img, tmp_path / f"collage{ext}")
    assert out.exists() and out.stat().st_size > 0


def test_save_collage_rejects_unsupported_format(tmp_path):
    img = build_collage(_photos(2), _spec(200, 200), _theme(), layout=LAYOUT_GRID)
    with pytest.raises(CollageError):
        save_collage(img, tmp_path / "collage.gif")


def test_save_collage_records_dpi(tmp_path):
    img = build_collage(_photos(2), _spec(600, 400), _theme(), layout=LAYOUT_GRID)
    out = save_collage(img, tmp_path / "c.jpg", dpi=300)
    with Image.open(out) as reopened:
        assert reopened.info.get("dpi", (0, 0))[0] == pytest.approx(300, abs=1)
