"""
Offscreen tests for the Passport Photos tool (view + MainWindow wiring).

Verifies that ``MainWindow(mode="passport")`` shows only the standalone tool
(no album toolbar/wizard/sidebar) and ``mode="album"`` (the default) shows
none of the passport UI, that size and sheet presets populate the spec
controls correctly, that loading a photo produces a crop box and a preview,
that the interactive crop rectangle keeps its aspect ratio while being
resized, and that exporting/previewing the sheet works end to end.

Skipped wholesale where PyQt6 can't load. Face detection is not mocked --
on a machine without mediapipe/insightface installed, PassportPhotoView
falls back to a centered crop, which these tests also cover correctly.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtWidgets import QApplication, QDialog

    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.passport_photo_view import _CUSTOM, PassportPhotoView, _CropRectItem
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.passport_photo import PASSPORT_SIZES, SHEET_SIZES  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_portrait(tmp_path: Path, name: str = "portrait.jpg") -> Path:
    """A synthetic 'portrait' with a face-colored patch roughly centered."""
    img = Image.new("RGB", (600, 800), (200, 200, 210))
    face = Image.new("RGB", (150, 180), (210, 170, 140))
    img.paste(face, (225, 260))
    path = tmp_path / name
    img.save(path, "JPEG")
    return path


def _make_skin_portrait(tmp_path: Path, name: str = "skin.jpg") -> Path:
    """
    A noisy, skin-toned oval on a light backdrop.

    Closer to a real portrait than ``_make_portrait``'s flat rectangle, so
    the beautify effects (which key off skin tone and backdrop color) have
    something to actually change -- needed by the compare tests.
    """
    w, h = 600, 800
    arr = np.full((h, w, 3), (215, 218, 222), np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    mask = (((xx - w * 0.5) / 110) ** 2 + ((yy - h * 0.42) / 150) ** 2) <= 1.0
    rng = np.random.default_rng(1)
    skin = np.clip(
        np.array([215, 175, 145]) + rng.integers(-25, 25, size=(h, w, 3)), 0, 255
    ).astype(np.uint8)
    arr[mask] = skin[mask]
    path = tmp_path / name
    Image.fromarray(arr, "RGB").save(path, "JPEG", quality=95)
    return path


# --------------------------------------------------------------------------- #
# View construction + controls
# --------------------------------------------------------------------------- #
def test_view_constructs_with_defaults(qapp):
    view = PassportPhotoView()
    assert view.size_preset.count() == len(PASSPORT_SIZES) + 1  # + "Custom…"
    assert view.sheet_preset.count() == len(SHEET_SIZES) + 1
    assert view.width_mm.value() > 0
    assert view.height_mm.value() > 0
    assert not view.btn_auto_crop.isEnabled()  # no photo loaded yet
    assert not view.btn_export.isEnabled()


def test_defaults_match_studio_settings(qapp):
    """The tool should open ready-to-go with this studio's usual settings."""
    view = PassportPhotoView()
    # Photo size: Custom 30x35mm @ 300dpi.
    assert view.size_preset.currentText() == _CUSTOM
    assert view.width_mm.value() == pytest.approx(30.0)
    assert view.height_mm.value() == pytest.approx(35.0)
    assert view.width_mm.isEnabled() and view.height_mm.isEnabled()
    assert view.dpi.currentData() == 300
    # Sheet: 4x6in, 0.10in margin, 0.05in spacing, auto-fill.
    assert view.sheet_preset.currentText() == "4 x 6 in"
    assert view.sheet_width_in.value() == pytest.approx(4.0)
    assert view.sheet_height_in.value() == pytest.approx(6.0)
    assert view.margin_in.value() == pytest.approx(0.10)
    assert view.spacing_in.value() == pytest.approx(0.05)
    assert view.fill_auto.isChecked() is True
    # Cutting-guide border: on, 0.3mm.
    assert view.stroke_enabled.isChecked() is True
    assert view.stroke_width_mm.value() == pytest.approx(0.3)


def test_size_preset_changes_update_mm_fields(qapp):
    view = PassportPhotoView()
    names = list(PASSPORT_SIZES.keys())
    for i, name in enumerate(names):
        view.size_preset.setCurrentIndex(i)
        w, h = PASSPORT_SIZES[name]
        assert view.width_mm.value() == pytest.approx(w)
        assert view.height_mm.value() == pytest.approx(h)
        assert not view.width_mm.isEnabled()  # locked to preset

    # Custom (last entry) unlocks the fields.
    view.size_preset.setCurrentIndex(view.size_preset.count() - 1)
    assert view.width_mm.isEnabled()
    assert view.height_mm.isEnabled()


def test_sheet_preset_changes_update_in_fields(qapp):
    view = PassportPhotoView()
    names = list(SHEET_SIZES.keys())
    for i, name in enumerate(names):
        view.sheet_preset.setCurrentIndex(i)
        w, h = SHEET_SIZES[name]
        assert view.sheet_width_in.value() == pytest.approx(w)
        assert view.sheet_height_in.value() == pytest.approx(h)

    view.sheet_preset.setCurrentIndex(view.sheet_preset.count() - 1)
    assert view.sheet_width_in.isEnabled()
    assert view.sheet_height_in.isEnabled()


def test_sheet_status_reports_capacity(qapp):
    view = PassportPhotoView()
    # Default preset (first passport size) on a 4x6 in sheet should fit at
    # least one copy and say so.
    view.sheet_preset.setCurrentIndex(0)  # a real sheet preset, not Custom
    view._refresh_sheet_status()
    assert "Fits" in view.sheet_status.text()
    assert view.copies.maximum() > 0


# --------------------------------------------------------------------------- #
# Loading a photo
# --------------------------------------------------------------------------- #
def test_load_photo_sets_crop_and_preview(qapp, tmp_path):
    view = PassportPhotoView()
    path = _make_portrait(tmp_path)
    view.load_photo(path)

    assert view._image is not None
    assert view._crop_item is not None
    assert view.btn_auto_crop.isEnabled()
    assert view.btn_export.isEnabled()
    assert not view.preview_label.pixmap().isNull()

    # The crop box must respect the current spec's aspect ratio.
    spec = view._current_spec()
    x0, y0, x1, y1 = view._crop_item.crop_box()
    assert (x1 - x0) / (y1 - y0) == pytest.approx(spec.aspect, rel=0.05)


def test_load_photo_bad_path_shows_error_not_crash(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: shown.setdefault("called", True)
    )
    view = PassportPhotoView()
    view.load_photo(tmp_path / "does_not_exist.jpg")
    assert shown.get("called") is True
    assert view._image is None


# --------------------------------------------------------------------------- #
# Interactive crop rectangle geometry
# --------------------------------------------------------------------------- #
def test_crop_rect_resize_from_handle_keeps_aspect(qapp):
    from PyQt6.QtCore import QPointF

    bounds = QRectF(0, 0, 1000, 1000)
    rect = QRectF(100, 100, 200, 200)
    item = _CropRectItem(rect, aspect=1.0, bounds=bounds)
    # Drag the bottom-right handle to (500, 500); the top-left (100, 100) is
    # the anchor, so this should grow the box to a 400x400 square.
    item.resize_from_handle("br", QPointF(500, 500))
    w = item.rect().width()
    h = item.rect().height()
    assert w == pytest.approx(400, rel=1e-6)
    assert w / h == pytest.approx(1.0, rel=1e-6)


def test_crop_rect_resize_clamped_to_bounds():
    from PyQt6.QtCore import QPointF

    bounds = QRectF(0, 0, 300, 300)
    rect = QRectF(50, 50, 100, 100)
    item = _CropRectItem(rect, aspect=1.0, bounds=bounds)
    # Try to drag the bottom-right handle way outside the image bounds.
    item.resize_from_handle("br", QPointF(5000, 5000))
    r = item.rect()
    assert r.right() <= bounds.right() + 1e-6
    assert r.bottom() <= bounds.bottom() + 1e-6
    assert r.width() / r.height() == pytest.approx(1.0, rel=1e-6)


def test_crop_rect_non_square_aspect_preserved():
    bounds = QRectF(0, 0, 1000, 1000)
    aspect = 35.0 / 45.0
    rect = QRectF(100, 100, 175, 225)
    item = _CropRectItem(rect, aspect=aspect, bounds=bounds)
    from PyQt6.QtCore import QPointF

    item.resize_from_handle("br", QPointF(450, 999))
    r = item.rect()
    assert r.width() / r.height() == pytest.approx(aspect, rel=1e-6)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_export_writes_sheet_file(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view.fill_auto.setChecked(True)

    out_path = tmp_path / "sheet_out.jpg"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), "")
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    view._on_export()
    assert out_path.exists()


# --------------------------------------------------------------------------- #
# Cutting-guide border (stroke)
# --------------------------------------------------------------------------- #
def test_stroke_checkbox_defaults_on_and_toggle_disables_width(qapp):
    view = PassportPhotoView()
    assert view.stroke_enabled.isChecked() is True
    assert view.stroke_width_mm.isEnabled() is True
    assert view.stroke_width_mm.value() == pytest.approx(0.3)

    view.stroke_enabled.setChecked(False)
    assert not view.stroke_width_mm.isEnabled()


def test_stroke_width_is_editable(qapp):
    view = PassportPhotoView()
    view.stroke_width_mm.setValue(5.5)
    assert view.stroke_width_mm.value() == pytest.approx(5.5)


def test_build_current_sheet_respects_stroke_toggle(qapp, tmp_path):
    from core.passport_photo import compute_grid

    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    spec = view._current_spec()
    sheet = view._current_sheet_spec()
    cols, rows = compute_grid(sheet, spec)
    assert cols > 0 and rows > 0

    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2
    sample = (off_x + 2, off_y + 2)  # just inside the first tile's corner

    view.stroke_enabled.setChecked(True)
    view.stroke_width_mm.setValue(3.0)
    with_border, _, _, _, _, _ = view._build_current_sheet()

    view.stroke_enabled.setChecked(False)
    without_border, _, _, _, _, _ = view._build_current_sheet()

    assert with_border.getpixel(sample) != without_border.getpixel(sample)


# --------------------------------------------------------------------------- #
# Enhance faces (global beautification)
# --------------------------------------------------------------------------- #
def test_beautify_controls_start_disabled_with_zero_sliders(qapp):
    view = PassportPhotoView()
    assert view.beautify_enabled.isChecked() is False
    for key, slider in view.beautify_sliders.items():
        assert slider.value() == 0
        assert not slider.isEnabled()
    opts = view._current_beautify_options()
    assert opts.enabled is False


def test_enabling_beautify_seeds_default_intensities(qapp):
    from core.face_beautify import BeautifyOptions

    view = PassportPhotoView()
    view.beautify_enabled.setChecked(True)

    defaults = BeautifyOptions.default_on()
    for key, slider in view.beautify_sliders.items():
        assert slider.value() == round(getattr(defaults, key) * 100)
        assert slider.isEnabled()
        assert view.beautify_value_labels[key].text() == f"{slider.value()}%"


def test_disabling_beautify_keeps_slider_values_but_disables_them(qapp):
    view = PassportPhotoView()
    view.beautify_enabled.setChecked(True)
    values_on = {k: s.value() for k, s in view.beautify_sliders.items()}

    view.beautify_enabled.setChecked(False)
    for key, slider in view.beautify_sliders.items():
        assert slider.value() == values_on[key]  # unchanged, just disabled
        assert not slider.isEnabled()
    assert view._current_beautify_options().enabled is False


def test_reenabling_beautify_does_not_clobber_a_manual_adjustment(qapp):
    view = PassportPhotoView()
    view.beautify_enabled.setChecked(True)
    view.beautify_sliders["skin_smooth"].setValue(77)

    view.beautify_enabled.setChecked(False)
    view.beautify_enabled.setChecked(True)

    assert view.beautify_sliders["skin_smooth"].value() == 77


def test_current_beautify_options_reflects_slider_values(qapp):
    view = PassportPhotoView()
    view.beautify_enabled.setChecked(True)
    view.beautify_sliders["skin_smooth"].setValue(20)
    view.beautify_sliders["auto_correct"].setValue(60)
    view.beautify_sliders["background_whiten"].setValue(0)
    view.beautify_sliders["teeth_eye_whiten"].setValue(100)

    opts = view._current_beautify_options()
    assert opts.enabled is True
    assert opts.skin_smooth == pytest.approx(0.20)
    assert opts.auto_correct == pytest.approx(0.60)
    assert opts.background_whiten == pytest.approx(0.0)
    assert opts.teeth_eye_whiten == pytest.approx(1.0)


def test_build_current_sheet_changes_when_beautify_enabled(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view.fill_auto.setChecked(True)

    built_off = view._build_current_sheet()
    assert built_off is not None
    img_off = np.asarray(built_off[0])

    view.beautify_enabled.setChecked(True)
    view.beautify_sliders["background_whiten"].setValue(100)
    view.beautify_sliders["skin_smooth"].setValue(100)
    built_on = view._build_current_sheet()
    assert built_on is not None
    img_on = np.asarray(built_on[0])

    assert img_off.shape == img_on.shape
    assert not np.array_equal(img_off, img_on)


def test_build_current_sheet_applies_beautify_to_every_person_on_sheet(qapp, tmp_path):
    """Global scope: turning beautify on affects *every* person's photo, not
    just whichever one is currently loaded/selected in the canvas."""
    view = PassportPhotoView()

    view.load_photo(_make_portrait(tmp_path, "one.jpg"))
    view.fill_auto.setChecked(False)
    view.copies.setValue(2)
    view._on_add_to_sheet()

    view.load_photo(_make_portrait(tmp_path, "two.jpg"))
    view.copies.setValue(2)
    view._on_add_to_sheet()

    built_off = view._build_current_sheet()
    img_off = np.asarray(built_off[0])

    view.beautify_enabled.setChecked(True)
    view.beautify_sliders["background_whiten"].setValue(100)
    built_on = view._build_current_sheet()
    img_on = np.asarray(built_on[0])

    assert not np.array_equal(img_off, img_on)


# --------------------------------------------------------------------------- #
# Hold-to-compare (raw vs enhanced)
# --------------------------------------------------------------------------- #
def test_compare_button_disabled_when_beautify_is_off(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view._update_preview()
    # Nothing was enhanced, so there is nothing to compare against.
    assert not view.btn_compare.isEnabled()


def test_compare_button_enabled_once_beautify_changes_the_photo(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)
    view._update_preview()
    assert view.btn_compare.isEnabled()


def test_holding_compare_shows_raw_and_releasing_restores_enhanced(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)
    view._update_preview()

    enhanced = view.preview_label.pixmap().toImage()

    view.btn_compare.pressed.emit()
    raw = view.preview_label.pixmap().toImage()
    assert view._comparing is True
    assert raw != enhanced  # actually showing different pixels

    view.btn_compare.released.emit()
    assert view._comparing is False
    assert view.preview_label.pixmap().toImage() == enhanced


def test_compare_caption_tells_the_user_which_version_they_are_seeing(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)
    view._update_preview()

    normal_caption = view.preview_caption.text()
    view.btn_compare.pressed.emit()
    held_caption = view.preview_caption.text()
    view.btn_compare.released.emit()

    assert "original" in held_caption.lower()
    assert held_caption != normal_caption
    assert view.preview_caption.text() == normal_caption


def test_holding_compare_does_not_recompute_the_preview(qapp, tmp_path):
    """The gesture must be a cached pixmap swap -- no image work mid-hold."""
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)
    view._update_preview()

    calls = {"n": 0}
    view._update_preview = lambda: calls.__setitem__("n", calls["n"] + 1)

    view.btn_compare.pressed.emit()
    view.btn_compare.released.emit()
    assert calls["n"] == 0


def test_turning_beautify_off_while_comparing_clears_compare_state(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_skin_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)
    view._update_preview()
    view.btn_compare.pressed.emit()
    assert view._comparing is True

    # Beautify off -> nothing to compare; state must not get stuck showing
    # a stale "original" caption with a disabled button.
    view.beautify_enabled.setChecked(False)
    view._update_preview()
    assert not view.btn_compare.isEnabled()
    assert view._comparing is False


# --------------------------------------------------------------------------- #
# Preview performance guards
#
# These exist because a first cut of the beautify feature recomputed the full
# pipeline on every intermediate slider/crop value, against the
# full-resolution source, with rembg's BiRefNet matte in the loop -- which
# hung the app hard enough to take the machine down with it. Each test below
# pins one of the three fixes.
# --------------------------------------------------------------------------- #
def test_slider_drag_does_not_recompute_preview_synchronously(qapp, tmp_path):
    """Rapid slider changes should be coalesced by the debounce timer, not
    trigger one full recompute each."""
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view.beautify_enabled.setChecked(True)

    calls = {"n": 0}
    view._update_preview = lambda: calls.__setitem__("n", calls["n"] + 1)

    for value in range(0, 40):
        view.beautify_sliders["skin_smooth"].setValue(value)

    assert calls["n"] == 0  # all deferred to the timer
    assert view._preview_timer.isActive()


def test_crop_drag_does_not_recompute_preview_synchronously(qapp, tmp_path):
    from PyQt6.QtCore import QPointF

    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))

    calls = {"n": 0}
    view._update_preview = lambda: calls.__setitem__("n", calls["n"] + 1)

    for i in range(10):
        view._crop_item.resize_from_handle("br", QPointF(400 + i, 500 + i))

    assert calls["n"] == 0
    assert view._preview_timer.isActive()


def test_preview_uses_downscaled_source_not_the_full_resolution_original(qapp, tmp_path):
    """The live thumbnail must not crop/resize the full-size original every
    time -- that alone cost ~0.26s per update on a 24MP file."""
    from ui_qt.views.passport_photo_view import _PREVIEW_SOURCE_MAX_DIM

    big = Image.new("RGB", (4000, 5000), (200, 200, 210))
    path = tmp_path / "big.jpg"
    big.save(path, "JPEG")

    view = PassportPhotoView()
    view.load_photo(path)
    view._update_preview()

    source = view._preview_source()
    assert source is not None
    assert max(source.size) <= _PREVIEW_SOURCE_MAX_DIM
    assert view._image.size == (4000, 5000)  # original kept intact for export


def test_preview_source_cache_is_invalidated_when_a_new_photo_loads(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path, "one.jpg"))
    first = view._preview_source()

    view.load_photo(_make_portrait(tmp_path, "two.jpg"))
    second = view._preview_source()

    assert second is not first


def test_beautify_options_do_not_enable_rembg_from_the_ui(qapp):
    """rembg's BiRefNet matte runs a heavy neural-net inference per call, so
    the interactive path must never request it."""
    view = PassportPhotoView()
    view.beautify_enabled.setChecked(True)
    assert view._current_beautify_options().use_rembg is False


# --------------------------------------------------------------------------- #
# Sheet preview
# --------------------------------------------------------------------------- #
def test_build_current_sheet_matches_grid(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    built = view._build_current_sheet()
    assert built is not None
    sheet_img, spec, cols, rows, total_placed, num_people = built
    assert cols > 0 and rows > 0
    assert total_placed > 0
    assert num_people == 1
    sheet_spec = view._current_sheet_spec()
    assert sheet_img.size == (sheet_spec.width_px, sheet_spec.height_px)


def test_build_current_sheet_without_photo_shows_message(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: shown.setdefault("called", True)
    )
    view = PassportPhotoView()
    assert view._build_current_sheet() is None
    assert shown.get("called") is True


def test_preview_sheet_dialog_opens_without_crash(qapp, tmp_path, monkeypatch):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    # Prevent the modal dialog from actually blocking the test.
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    view._on_preview_sheet()  # must not raise


def test_preview_sheet_export_button_triggers_export(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialogButtonBox, QFileDialog, QMessageBox

    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))

    out_path = tmp_path / "from_preview.jpg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    captured_dialogs = []

    def _fake_exec(self):
        captured_dialogs.append(self)
        # Simulate the user clicking the "Export…" button before closing.
        for btn in self.findChildren(QDialogButtonBox):
            for b in btn.buttons():
                if b.text() == "Export…":
                    b.click()
        return 0

    monkeypatch.setattr(QDialog, "exec", _fake_exec)
    view._on_preview_sheet()
    assert out_path.exists()


# --------------------------------------------------------------------------- #
# People on this sheet (combining 2-3 people's photos onto one sheet)
# --------------------------------------------------------------------------- #
def test_people_list_starts_empty_and_add_disabled(qapp):
    view = PassportPhotoView()
    assert view.people_list.count() == 0
    assert not view.btn_add_to_sheet.isEnabled()
    assert not view.btn_remove_person.isEnabled()


def test_load_photo_enables_add_button(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    assert view.btn_add_to_sheet.isEnabled()


def test_add_current_photo_appends_and_disables_add(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view._on_add_to_sheet()

    assert view.people_list.count() == 1
    assert len(view._people) == 1
    assert view._current_index == 0
    assert not view.btn_add_to_sheet.isEnabled()  # already on the sheet


def test_adding_two_people_combines_them_on_one_sheet(qapp, tmp_path):
    view = PassportPhotoView()

    view.load_photo(_make_portrait(tmp_path, "one.jpg"))
    # With nobody else on the sheet yet, "fill sheet automatically" means this
    # first photo alone would claim every slot -- turn it off to request a
    # specific count and leave room for a second person.
    view.fill_auto.setChecked(False)
    view.copies.setValue(2)
    view._on_add_to_sheet()

    view.load_photo(_make_portrait(tmp_path, "two.jpg"))
    view.copies.setValue(3)
    view._on_add_to_sheet()

    assert view.people_list.count() == 2

    built = view._build_current_sheet()
    assert built is not None
    _sheet_img, _spec, _cols, _rows, total_placed, num_people = built
    assert num_people == 2
    assert total_placed == 5


def test_remove_person_removes_from_list_and_reenables_add(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view._on_add_to_sheet()
    assert view.people_list.count() == 1

    view.people_list.setCurrentRow(0)
    view._on_remove_selected()

    assert view.people_list.count() == 0
    assert len(view._people) == 0
    assert view.btn_add_to_sheet.isEnabled()  # photo still loaded, free to re-add


def test_selecting_person_row_reloads_crop_and_copies_for_editing(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path, "one.jpg"))
    view.fill_auto.setChecked(False)
    view.copies.setValue(4)
    view._on_add_to_sheet()

    view.load_photo(_make_portrait(tmp_path, "two.jpg"))
    view.copies.setValue(1)
    view._on_add_to_sheet()

    # Re-select the first person; the canvas/copies should reflect their data.
    view.people_list.setCurrentRow(0)
    assert view._current_index == 0
    assert view.copies.value() == 4
    assert view._crop_item.crop_box() == view._people[0].crop_box


def test_editing_crop_while_selected_syncs_live_to_people_entry(qapp, tmp_path):
    from PyQt6.QtCore import QPointF

    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view._on_add_to_sheet()

    before = view._people[0].crop_box
    view._crop_item.resize_from_handle("br", QPointF(590, 790))
    after = view._people[0].crop_box

    assert after != before
    assert after == view._crop_item.crop_box()


def test_copies_change_while_selected_syncs_live_to_people_entry(qapp, tmp_path):
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    view._on_add_to_sheet()

    view.copies.setValue(7)
    assert view._people[0].copies == 7


def test_unattached_loaded_photo_still_fills_sheet_alone(qapp, tmp_path):
    """Without ever clicking "Add", a single loaded photo still builds a full
    sheet -- the original single-photo flow must keep working untouched."""
    view = PassportPhotoView()
    view.load_photo(_make_portrait(tmp_path))
    assert view.people_list.count() == 0

    built = view._build_current_sheet()
    assert built is not None
    _sheet_img, _spec, _cols, _rows, total_placed, num_people = built
    assert num_people == 1
    assert total_placed > 0


# --------------------------------------------------------------------------- #
# MainWindow wiring (mode="passport")
# --------------------------------------------------------------------------- #
def test_main_window_passport_mode_shows_only_the_tool(qapp):
    win = MainWindow(mode="passport")
    assert win.centralWidget() is win.passport
    assert isinstance(win.passport, PassportPhotoView)
    # None of the album-only UI should exist in this mode.
    for attr in ("action_open", "action_album", "action_export", "sidebar", "wizard"):
        assert not hasattr(win, attr), attr


def test_main_window_album_mode_has_no_passport_ui(qapp):
    win = MainWindow(mode="album")
    assert not hasattr(win, "action_passport")
    assert not hasattr(win, "passport")
    # Center stack only has the two album pages (grid + preview).
    assert win.center_stack.count() == 2


def test_main_window_defaults_to_album_mode(qapp):
    win = MainWindow()
    assert win._mode == "album"
    assert hasattr(win, "action_open")
