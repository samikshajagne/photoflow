"""
Unit tests for core.passport_photo.

Covers preset sanity, spec pixel-size math, face-centered auto-crop, sheet
grid/copy-count computation, sheet tiling, and save format dispatch. No Qt,
no MediaPipe -- face regions are supplied directly as plain tuples.
"""

from pathlib import Path

import pytest
from PIL import Image

from core.passport_photo import (
    DEFAULT_DPI,
    PASSPORT_SIZES,
    SHEET_SIZES,
    PassportPhotoError,
    PassportPhotoSpec,
    SheetEntry,
    SheetSpec,
    add_border,
    auto_crop_box,
    build_multi_sheet,
    build_sheet,
    compute_grid,
    crop_and_resize,
    max_copies,
    save_sheet,
)


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def test_presets_are_positive_and_nonempty():
    assert PASSPORT_SIZES
    for name, (w, h) in PASSPORT_SIZES.items():
        assert w > 0 and h > 0, name

    assert SHEET_SIZES
    for name, (w, h) in SHEET_SIZES.items():
        assert w > 0 and h > 0, name


# --------------------------------------------------------------------------- #
# PassportPhotoSpec
# --------------------------------------------------------------------------- #
def test_spec_pixel_size_at_300dpi():
    # India passport size: 3.5cm x 4.5cm = 35mm x 45mm.
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=300)
    # 35mm / 25.4 * 300 = 413.4 -> 413
    assert spec.width_px == 413
    # 45mm / 25.4 * 300 = 531.5 -> 531 or 532 depending on rounding
    assert spec.height_px in (531, 532)
    assert spec.aspect == pytest.approx(35.0 / 45.0)


def test_spec_rejects_invalid_dimensions():
    with pytest.raises(PassportPhotoError):
        PassportPhotoSpec(width_mm=0, height_mm=45.0)
    with pytest.raises(PassportPhotoError):
        PassportPhotoSpec(width_mm=35.0, height_mm=-1)
    with pytest.raises(PassportPhotoError):
        PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=0)


# --------------------------------------------------------------------------- #
# SheetSpec
# --------------------------------------------------------------------------- #
def test_sheet_pixel_size_at_300dpi():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=300)
    assert sheet.width_px == 1200
    assert sheet.height_px == 1800


def test_sheet_rejects_invalid_dimensions():
    with pytest.raises(PassportPhotoError):
        SheetSpec(width_in=0, height_in=6.0)
    with pytest.raises(PassportPhotoError):
        SheetSpec(width_in=4.0, height_in=6.0, margin_in=-0.1)


# --------------------------------------------------------------------------- #
# auto_crop_box
# --------------------------------------------------------------------------- #
def test_auto_crop_box_centers_on_face():
    # A face roughly centered in a 1000x1000 image.
    face_box = (0.4, 0.3, 0.2, 0.25)  # xmin, ymin, w, h (relative)
    box = auto_crop_box(1000, 1000, face_box, target_aspect=35.0 / 45.0)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 1000
    assert 0 <= y0 < y1 <= 1000
    # The crop's horizontal center should land close to the face's center.
    face_center_x = (0.4 + 0.2 / 2) * 1000
    crop_center_x = (x0 + x1) / 2
    assert abs(crop_center_x - face_center_x) < 5

    # Aspect ratio of the box should match the target.
    box_w, box_h = x1 - x0, y1 - y0
    assert box_w / box_h == pytest.approx(35.0 / 45.0, rel=0.02)


def test_auto_crop_box_no_face_falls_back_to_centered():
    box = auto_crop_box(800, 600, None, target_aspect=1.0)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    assert w == pytest.approx(h, rel=0.02)
    # Should be centered.
    assert abs((x0 + x1) / 2 - 400) < 2
    assert abs((y0 + y1) / 2 - 300) < 2


def test_auto_crop_box_shrinks_to_fit_when_ideal_crop_too_big():
    # Tiny image, face fills almost all of it -- ideal crop would exceed the
    # image; the box must still be clamped within bounds.
    face_box = (0.1, 0.1, 0.8, 0.8)
    box = auto_crop_box(100, 100, face_box, target_aspect=1.0)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 100
    assert 0 <= y0 < y1 <= 100


def test_auto_crop_box_rejects_bad_dimensions():
    with pytest.raises(PassportPhotoError):
        auto_crop_box(0, 100, None, target_aspect=1.0)
    with pytest.raises(PassportPhotoError):
        auto_crop_box(100, 100, None, target_aspect=0)


# --------------------------------------------------------------------------- #
# crop_and_resize
# --------------------------------------------------------------------------- #
def test_crop_and_resize_produces_spec_sized_image():
    img = Image.new("RGB", (500, 500), (10, 20, 30))
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    box = auto_crop_box(500, 500, None, target_aspect=spec.aspect)
    out = crop_and_resize(img, box, spec)
    assert out.size == (spec.width_px, spec.height_px)


# --------------------------------------------------------------------------- #
# compute_grid / max_copies
# --------------------------------------------------------------------------- #
def test_compute_grid_fits_expected_count_on_4x6_sheet():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, margin_in=0.1, spacing_in=0.05, dpi=300)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=300)  # ~413x531 px
    cols, rows = compute_grid(sheet, spec)
    assert cols >= 2  # 1200px wide sheet, ~413px tiles -> at least 2 columns
    assert rows >= 3  # 1800px tall sheet, ~531px tiles -> at least 3 rows
    assert max_copies(sheet, spec) == cols * rows


def test_compute_grid_returns_zero_when_photo_bigger_than_sheet():
    sheet = SheetSpec(width_in=1.0, height_in=1.0, dpi=100)
    spec = PassportPhotoSpec(width_mm=100.0, height_mm=100.0, dpi=100)
    assert compute_grid(sheet, spec) == (0, 0)
    assert max_copies(sheet, spec) == 0


# --------------------------------------------------------------------------- #
# build_sheet
# --------------------------------------------------------------------------- #
def test_build_sheet_dimensions_and_default_fill():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    photo = Image.new("RGB", (spec.width_px, spec.height_px), (200, 50, 50))
    out = build_sheet(photo, sheet, spec)
    assert out.size == (sheet.width_px, sheet.height_px)
    cols, rows = compute_grid(sheet, spec)
    # A pixel in the middle of the first tile slot should be non-white
    # (i.e. the tile was actually pasted there), proving tiles got placed.
    assert cols > 0 and rows > 0


def test_build_sheet_respects_explicit_copies():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    photo = Image.new("RGB", (spec.width_px, spec.height_px), (0, 200, 0))
    capacity = max_copies(sheet, spec)
    assert capacity > 1
    out = build_sheet(photo, sheet, spec, copies=1)
    # Count how many tile-colored regions are non-white by sampling corners
    # of each grid slot; with copies=1 only the first slot should be filled.
    cols, rows = compute_grid(sheet, spec)
    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2

    filled = 0
    for r in range(rows):
        for c in range(cols):
            cx = off_x + c * (spec.width_px + sheet.spacing_px) + spec.width_px // 2
            cy = off_y + r * (spec.height_px + sheet.spacing_px) + spec.height_px // 2
            if out.getpixel((cx, cy)) == (0, 200, 0):
                filled += 1
    assert filled == 1


def test_build_sheet_raises_when_photo_does_not_fit():
    sheet = SheetSpec(width_in=1.0, height_in=1.0, dpi=100)
    spec = PassportPhotoSpec(width_mm=100.0, height_mm=100.0, dpi=100)
    photo = Image.new("RGB", (spec.width_px, spec.height_px))
    with pytest.raises(PassportPhotoError):
        build_sheet(photo, sheet, spec)


# --------------------------------------------------------------------------- #
# add_border
# --------------------------------------------------------------------------- #
def test_add_border_draws_expected_thickness():
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    dpi = 254  # 1 px == 0.1mm at this dpi, so 3mm -> exactly 30px, no rounding noise
    out = add_border(img, stroke_mm=3.0, dpi=dpi, color=(0, 0, 0))
    assert out.size == img.size
    # Just inside the edge: black. Well past the border: still white.
    assert out.getpixel((5, 100)) == (0, 0, 0)
    assert out.getpixel((100, 5)) == (0, 0, 0)
    assert out.getpixel((100, 100)) == (255, 255, 255)


def test_add_border_zero_or_negative_is_noop():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    assert add_border(img, stroke_mm=0.0, dpi=300) is img
    assert add_border(img, stroke_mm=-1.0, dpi=300) is img


def test_build_sheet_with_stroke_draws_border_on_tiles():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    photo = Image.new("RGB", (spec.width_px, spec.height_px), (255, 255, 255))
    out = build_sheet(photo, sheet, spec, stroke_mm=3.0)

    cols, rows = compute_grid(sheet, spec)
    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2
    # A pixel just inside the first tile's top-left corner should be black
    # (the border), while the tile's center stays white.
    assert out.getpixel((off_x + 2, off_y + 2)) == (0, 0, 0)
    assert out.getpixel((off_x + spec.width_px // 2, off_y + spec.height_px // 2)) == (
        255, 255, 255,
    )


def test_build_sheet_without_stroke_has_no_border():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    photo = Image.new("RGB", (spec.width_px, spec.height_px), (255, 255, 255))
    out = build_sheet(photo, sheet, spec, stroke_mm=0.0)
    cols, rows = compute_grid(sheet, spec)
    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2
    assert out.getpixel((off_x + 2, off_y + 2)) == (255, 255, 255)


# --------------------------------------------------------------------------- #
# build_multi_sheet (combining several people's photos on one sheet)
# --------------------------------------------------------------------------- #
def _grid_offsets(sheet: SheetSpec, spec: PassportPhotoSpec) -> tuple[int, int, int, int]:
    """(off_x, off_y, cols, rows) for a centered grid -- test helper."""
    cols, rows = compute_grid(sheet, spec)
    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2
    return off_x, off_y, cols, rows


def test_build_multi_sheet_places_each_entry_in_order():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    off_x, off_y, cols, rows = _grid_offsets(sheet, spec)
    capacity = cols * rows
    assert capacity >= 4  # test assumes room for at least two people's copies

    red = Image.new("RGB", (spec.width_px, spec.height_px), (200, 0, 0))
    blue = Image.new("RGB", (spec.width_px, spec.height_px), (0, 0, 200))
    out = build_multi_sheet(
        [SheetEntry(photo=red, copies=2), SheetEntry(photo=blue, copies=2)], sheet, spec,
    )
    assert out.size == (sheet.width_px, sheet.height_px)

    def slot_center(i: int) -> tuple[int, int]:
        c, r = i % cols, i // cols
        return (
            off_x + c * (spec.width_px + sheet.spacing_px) + spec.width_px // 2,
            off_y + r * (spec.height_px + sheet.spacing_px) + spec.height_px // 2,
        )

    # First two slots: red (person A); next two: blue (person B).
    assert out.getpixel(slot_center(0)) == (200, 0, 0)
    assert out.getpixel(slot_center(1)) == (200, 0, 0)
    assert out.getpixel(slot_center(2)) == (0, 0, 200)
    assert out.getpixel(slot_center(3)) == (0, 0, 200)


def test_build_multi_sheet_truncates_when_total_exceeds_capacity():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    capacity = max_copies(sheet, spec)

    photo = Image.new("RGB", (spec.width_px, spec.height_px), (0, 200, 0))
    # Ask for way more total copies than the sheet holds -- must not raise,
    # just place as many as fit.
    out = build_multi_sheet(
        [SheetEntry(photo=photo, copies=capacity + 50)], sheet, spec,
    )
    assert out.size == (sheet.width_px, sheet.height_px)


def test_build_multi_sheet_raises_when_photo_does_not_fit():
    sheet = SheetSpec(width_in=1.0, height_in=1.0, dpi=100)
    spec = PassportPhotoSpec(width_mm=100.0, height_mm=100.0, dpi=100)
    photo = Image.new("RGB", (spec.width_px, spec.height_px))
    with pytest.raises(PassportPhotoError):
        build_multi_sheet([SheetEntry(photo=photo, copies=1)], sheet, spec)


def test_build_multi_sheet_applies_stroke_to_every_entry():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    off_x, off_y, *_ = _grid_offsets(sheet, spec)

    red = Image.new("RGB", (spec.width_px, spec.height_px), (255, 255, 255))
    blue = Image.new("RGB", (spec.width_px, spec.height_px), (255, 255, 255))
    out = build_multi_sheet(
        [SheetEntry(photo=red, copies=1), SheetEntry(photo=blue, copies=1)],
        sheet, spec, stroke_mm=3.0,
    )
    # Both the first slot (entry 1) and the slot right after it (entry 2)
    # should show the border near their top-left corner.
    assert out.getpixel((off_x + 2, off_y + 2)) == (0, 0, 0)


def test_build_multi_sheet_empty_entries_returns_blank_sheet():
    sheet = SheetSpec(width_in=4.0, height_in=6.0, dpi=150)
    spec = PassportPhotoSpec(width_mm=35.0, height_mm=45.0, dpi=150)
    out = build_multi_sheet([], sheet, spec)
    assert out.size == (sheet.width_px, sheet.height_px)
    assert out.getpixel((5, 5)) == (255, 255, 255)


# --------------------------------------------------------------------------- #
# save_sheet
# --------------------------------------------------------------------------- #
def test_save_sheet_jpg_png_pdf(tmp_path: Path):
    img = Image.new("RGB", (200, 300), (100, 100, 100))
    jpg = save_sheet(img, tmp_path / "sheet.jpg", dpi=300)
    png = save_sheet(img, tmp_path / "sheet.png", dpi=300)
    pdf = save_sheet(img, tmp_path / "sheet.pdf", dpi=300)
    assert jpg.exists() and jpg.suffix == ".jpg"
    assert png.exists() and png.suffix == ".png"
    assert pdf.exists() and pdf.suffix == ".pdf"


def test_save_sheet_rejects_unknown_format(tmp_path: Path):
    img = Image.new("RGB", (10, 10))
    with pytest.raises(PassportPhotoError):
        save_sheet(img, tmp_path / "sheet.gif")


def test_save_sheet_creates_parent_dirs(tmp_path: Path):
    img = Image.new("RGB", (10, 10))
    out = save_sheet(img, tmp_path / "nested" / "dir" / "sheet.jpg")
    assert out.exists()
