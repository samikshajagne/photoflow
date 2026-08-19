"""
Offscreen tests for the Collage Maker (view + MainWindow wiring).

Verifies that ``MainWindow(mode="collage")`` shows only the standalone tool,
that photos can be added/reordered/removed, that the preview renders and
respects the debounce + downscaling rules (the beautify slider incident is the
reason those rules exist), that shuffle produces a different arrangement, and
that export writes a real file at the full chosen resolution.

Skipped wholesale where PyQt6 can't load. Face detection isn't mocked -- with
no MediaPipe installed, CollageView falls back to no face boxes, which these
tests also cover correctly.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.collage_view import CollageView
    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.mode_chooser_view import (
        MODE_ALBUM,
        MODE_COLLAGE,
        MODE_PASSPORT,
        ModeChooserView,
    )
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.collage import LAYOUTS, SIZE_PRESETS, THEMES  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_photo(tmp_path: Path, name: str, w: int = 400, h: int = 300, color=(120, 90, 60)) -> Path:
    path = tmp_path / name
    Image.new("RGB", (w, h), color).save(path, "JPEG", quality=90)
    return path


def _make_photos(tmp_path: Path, n: int = 4) -> list[Path]:
    sizes = [(400, 300), (300, 400), (350, 350), (500, 280)]
    return [
        _make_photo(tmp_path, f"p{i}.jpg", *sizes[i % len(sizes)],
                    color=(40 + i * 30 % 200, 90, 150))
        for i in range(n)
    ]


def _view_with_photos(tmp_path: Path, n: int = 4) -> CollageView:
    view = CollageView()
    view.add_photos([str(p) for p in _make_photos(tmp_path, n)])
    view._render_preview()
    return view


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
def test_view_constructs_empty_with_controls_disabled(qapp):
    view = CollageView()
    assert view.photo_list.count() == 0
    assert not view.btn_export.isEnabled()
    assert not view.btn_shuffle.isEnabled()
    assert not view.btn_remove.isEnabled()
    assert view.btn_add.isEnabled()  # always available


def test_view_offers_every_layout_theme_and_size(qapp):
    view = CollageView()
    # +1 for the "Auto (pick for me)" entry.
    assert view.layout_choice.count() == len(LAYOUTS) + 1
    assert view.theme_choice.count() == len(THEMES)
    assert view.size_choice.count() == len(SIZE_PRESETS)


def test_all_sliders_exist_with_matching_labels(qapp):
    view = CollageView()
    expected = {
        "spacing", "border", "corner",          # style
        "darken",                                # background
        "title_size", "logo_width", "logo_opacity",  # text/branding
        "zoom", "pan_x", "pan_y", "rotate",      # per-photo
        "bleed",                                 # print
    }
    assert set(view.sliders) == expected
    for key, slider in view.sliders.items():
        assert view.slider_labels[key].text() == str(slider.value())


# --------------------------------------------------------------------------- #
# Adding / managing photos
# --------------------------------------------------------------------------- #
def test_adding_photos_populates_list_and_enables_controls(qapp, tmp_path):
    view = CollageView()
    view.add_photos([str(p) for p in _make_photos(tmp_path, 3)])
    assert view.photo_list.count() == 3
    assert len(view._items) == 3
    assert view.btn_export.isEnabled()
    assert view.btn_shuffle.isEnabled()


def test_added_photos_are_downscaled_not_kept_at_full_size(qapp, tmp_path):
    """Full-resolution sources must not be retained -- 20 photos from a 24MP
    camera would be well over a gigabyte of decoded pixels."""
    from ui_qt.views.collage_view import _SOURCE_PREVIEW_MAX_DIM

    big = _make_photo(tmp_path, "big.jpg", 4000, 3000)
    view = CollageView()
    view.add_photos([str(big)])

    item = view._items[0]
    assert max(item.preview.size) <= _SOURCE_PREVIEW_MAX_DIM
    assert item.full_size == (4000, 3000)  # remembered for export


def test_bad_files_are_reported_not_crashed(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: shown.setdefault("called", True)
    )
    good = _make_photo(tmp_path, "good.jpg")
    bad = tmp_path / "not_an_image.jpg"
    bad.write_text("definitely not a jpeg")

    view = CollageView()
    view.add_photos([str(good), str(bad)])
    assert shown.get("called") is True
    assert len(view._items) == 1  # the good one still loaded


def test_remove_selected_photo(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    view.photo_list.item(1).setSelected(True)
    view._on_remove_selected()
    assert view.photo_list.count() == 2
    assert len(view._items) == 2


def test_clear_all_resets_the_view(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    view._on_clear()
    assert view.photo_list.count() == 0
    assert view._items == []
    assert not view.btn_export.isEnabled()
    assert view.preview_label.text()  # placeholder message returns


def test_move_photo_up_and_down_reorders_both_list_and_model(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    original = [item.name for item in view._items]

    view.photo_list.clearSelection()
    view.photo_list.item(2).setSelected(True)
    view._move_selected(-1)

    names = [item.name for item in view._items]
    assert names == [original[0], original[2], original[1]]
    # The list widget must agree with the model, or the preview order lies.
    assert [view.photo_list.item(i).text() for i in range(3)] == names


def test_move_at_boundary_is_a_noop(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    before = [item.name for item in view._items]
    view.photo_list.clearSelection()
    view.photo_list.item(0).setSelected(True)
    view._move_selected(-1)  # already at the top
    assert [item.name for item in view._items] == before


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
def test_preview_renders_after_adding_photos(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 4)
    assert view._rendered is not None
    assert not view.preview_label.pixmap().isNull()


def test_preview_canvas_is_downscaled_from_the_export_size(qapp, tmp_path):
    """An A4 300dpi collage is 3508x2480; re-rendering that on every control
    change would be unusably slow, so the preview must be smaller."""
    from ui_qt.views.collage_view import _PREVIEW_CANVAS_MAX_DIM

    view = _view_with_photos(tmp_path, 3)
    view.size_choice.setCurrentText("A4 Landscape @300dpi")

    preview_spec = view._current_spec(for_preview=True)
    export_spec = view._current_spec(for_preview=False)
    assert max(preview_spec.width_px, preview_spec.height_px) <= _PREVIEW_CANVAS_MAX_DIM
    assert export_spec.width_px == 3508 and export_spec.height_px == 2480
    # Aspect ratio must survive the downscale or the preview would mislead.
    assert preview_spec.aspect == pytest.approx(export_spec.aspect, rel=0.01)


def test_small_preset_is_not_upscaled_for_preview(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 2)
    view.size_choice.setCurrentText("Facebook Cover (1640x664)")
    spec = view._current_spec(for_preview=True)
    assert spec.width_px <= 1640


def test_slider_changes_are_debounced_not_rendered_synchronously(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 4)
    calls = {"n": 0}
    view._render_preview = lambda: calls.__setitem__("n", calls["n"] + 1)

    for value in range(0, 30):
        view.sliders["spacing"].setValue(value)

    assert calls["n"] == 0
    assert view._preview_timer.isActive()


def test_slider_labels_update_immediately_even_though_render_is_deferred(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 2)
    view.sliders["border"].setValue(21)
    assert view.slider_labels["border"].text() == "21"


def test_changing_theme_and_layout_schedules_a_rerender(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    view._preview_timer.stop()
    view.theme_choice.setCurrentText("Gallery Dark")
    assert view._preview_timer.isActive()

    view._preview_timer.stop()
    view.layout_choice.setCurrentIndex(2)
    assert view._preview_timer.isActive()


def test_no_preview_scheduled_without_photos(qapp):
    view = CollageView()
    view._request_preview()
    assert not view._preview_timer.isActive()


def test_theme_choice_changes_the_rendered_pixels(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 4)
    view.theme_choice.setCurrentText("Classic White")
    view.sliders["spacing"].setValue(30)  # make the background clearly visible
    view._render_preview()
    light = np.asarray(view._rendered).mean()

    view.theme_choice.setCurrentText("Gallery Dark")
    view._render_preview()
    dark = np.asarray(view._rendered).mean()
    assert light > dark


@pytest.mark.parametrize("index", range(1, len(LAYOUTS) + 1))
def test_every_layout_renders_without_error(qapp, tmp_path, index):
    view = _view_with_photos(tmp_path, 5)
    view.layout_choice.setCurrentIndex(index)
    view._render_preview()
    assert view._rendered is not None


def test_auto_layout_picks_something_valid(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 4)
    view.layout_choice.setCurrentIndex(0)  # "Auto (pick for me)"
    assert view.layout_choice.currentData() is None
    assert view._current_layout(view._preview_photos()) in LAYOUTS


def test_shuffle_changes_the_arrangement(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 6)
    before_seed = view._seed
    before = np.asarray(view._rendered).copy()

    view._on_shuffle()
    view._render_preview()

    assert view._seed != before_seed
    assert not np.array_equal(before, np.asarray(view._rendered))
    # The visible list must stay in step with the shuffled model.
    assert [view.photo_list.item(i).text() for i in range(view.photo_list.count())] == [
        item.name for item in view._items
    ]


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_export_writes_a_file_at_the_full_chosen_resolution(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    view = _view_with_photos(tmp_path, 4)
    view.size_choice.setCurrentText("Instagram Square (1080x1080)")

    out = tmp_path / "out_collage.jpg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    view._on_export()
    assert out.exists()
    with Image.open(out) as saved:
        assert saved.size == (1080, 1080)


def test_export_rereads_originals_at_full_resolution(qapp, tmp_path):
    """The preview copies are downscaled, so export must go back to the source
    files or the printed collage would be soft."""
    big = _make_photo(tmp_path, "big.jpg", 2400, 1800)
    view = CollageView()
    view.add_photos([str(big)])

    photos = view._export_photos()
    assert photos[0].image.size == (2400, 1800)


def test_export_falls_back_to_the_preview_copy_if_the_original_vanished(qapp, tmp_path):
    """A photo deleted or unplugged mid-session shouldn't lose the collage."""
    path = _make_photo(tmp_path, "temp.jpg", 800, 600)
    view = CollageView()
    view.add_photos([str(path)])
    path.unlink()

    photos = view._export_photos()
    assert len(photos) == 1
    assert photos[0].image.size == view._items[0].preview.size


def test_export_without_photos_shows_a_message(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: shown.setdefault("called", True)
    )
    CollageView()._on_export()
    assert shown.get("called") is True


def test_cancelling_the_save_dialog_writes_nothing(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    view = _view_with_photos(tmp_path, 2)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    view._on_export()  # must simply return
    assert not list(tmp_path.glob("*collage*"))


# --------------------------------------------------------------------------- #
# Shapes, backgrounds, text and branding
# --------------------------------------------------------------------------- #
def test_shape_text_field_only_enabled_for_the_text_shape(qapp):
    from core.collage_shapes import SHAPE_HEART, SHAPE_TEXT

    view = CollageView()
    view.shape_choice.setCurrentIndex(view.shape_choice.findData(SHAPE_HEART))
    assert not view.shape_text.isEnabled()
    view.shape_choice.setCurrentIndex(view.shape_choice.findData(SHAPE_TEXT))
    assert view.shape_text.isEnabled()


def test_shape_selection_reaches_the_renderer(qapp, tmp_path):
    from core.collage_shapes import SHAPE_CIRCLE, SHAPE_NONE

    view = _view_with_photos(tmp_path, 4)
    assert view._current_shape() == (None, "")

    view.shape_choice.setCurrentIndex(view.shape_choice.findData(SHAPE_CIRCLE))
    shape, _text = view._current_shape()
    assert shape == SHAPE_CIRCLE
    view._render_preview()
    assert view._rendered is not None


def test_background_controls_enable_by_style(qapp):
    from core.collage import BG_BLURRED_PHOTO, BG_GRADIENT, BG_IMAGE, BG_SOLID

    view = CollageView()
    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_SOLID))
    assert view.bg_color.isEnabled() and not view.bg_color2.isEnabled()

    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_GRADIENT))
    assert view.bg_color2.isEnabled() and view.bg_vertical.isEnabled()

    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_IMAGE))
    assert view.bg_image_path.isEnabled() and view.sliders["darken"].isEnabled()

    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_BLURRED_PHOTO))
    assert view.sliders["darken"].isEnabled()


def test_gradient_background_changes_the_render(qapp, tmp_path):
    from core.collage import BG_GRADIENT

    view = _view_with_photos(tmp_path, 3)
    view.sliders["spacing"].setValue(50)  # make the background clearly visible
    view._render_preview()
    before = np.asarray(view._rendered).copy()

    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_GRADIENT))
    view.bg_color.set_color((255, 0, 0))
    view.bg_color2.set_color((0, 0, 255))
    view._render_preview()
    assert not np.array_equal(before, np.asarray(view._rendered))


def test_title_text_is_rendered(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    before = np.asarray(view._rendered).copy()
    view.title_text.setText("Priya & Arjun")
    view._render_preview()
    assert not np.array_equal(before, np.asarray(view._rendered))
    assert view._current_text_overlays()[0].text == "Priya & Arjun"


def test_empty_title_produces_no_overlay(qapp):
    view = CollageView()
    assert view._current_text_overlays() == []


def test_literal_backslash_n_becomes_a_real_newline(qapp):
    """The single-line field can't hold a real newline, so a typed \\n must
    still produce two lines."""
    view = CollageView()
    view.title_text.setText("Priya & Arjun\\n12 Feb 2026")
    assert "\n" in view._current_text_overlays()[0].text


def test_watermark_is_none_until_a_logo_is_chosen(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 2)
    assert view._current_watermark() is None

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (120, 60), (255, 0, 0, 255)).save(logo)
    view.logo_path.setText(str(logo))
    watermark = view._current_watermark()
    assert watermark is not None and Path(watermark.image_path) == logo


def test_clearing_the_logo_removes_the_watermark(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 2)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 50), (0, 255, 0, 255)).save(logo)
    view.logo_path.setText(str(logo))
    view._on_clear_logo()
    assert view._current_watermark() is None


# --------------------------------------------------------------------------- #
# Per-photo adjustments
# --------------------------------------------------------------------------- #
def test_selecting_a_photo_loads_its_adjustments(qapp, tmp_path):
    from core.collage import FILTER_SEPIA, PhotoAdjust

    view = _view_with_photos(tmp_path, 3)
    view._items[1].adjust = PhotoAdjust(zoom=1.5, filter_name=FILTER_SEPIA, beautify=True)
    view.photo_list.setCurrentRow(1)

    assert view.sliders["zoom"].value() == 150
    assert view.photo_filter.currentData() == FILTER_SEPIA
    assert view.photo_beautify.isChecked()
    assert "Adjusting" in view.selected_label.text()


def test_changing_a_filter_only_affects_the_selected_photo(qapp, tmp_path):
    from core.collage import FILTER_BW, FILTER_NONE

    view = _view_with_photos(tmp_path, 3)
    view.photo_list.setCurrentRow(0)
    view.photo_filter.setCurrentIndex(view.photo_filter.findData(FILTER_BW))

    assert view._items[0].adjust.filter_name == FILTER_BW
    assert view._items[1].adjust.filter_name == FILTER_NONE


def test_apply_to_all_copies_adjustments_across_photos(qapp, tmp_path):
    from core.collage import FILTER_SEPIA

    view = _view_with_photos(tmp_path, 4)
    view.photo_list.setCurrentRow(0)
    view.photo_filter.setCurrentIndex(view.photo_filter.findData(FILTER_SEPIA))
    view._on_apply_adjust_to_all()
    assert all(item.adjust.filter_name == FILTER_SEPIA for item in view._items)


def test_reset_restores_default_adjustments(qapp, tmp_path):
    from core.collage import FILTER_BW, PhotoAdjust

    view = _view_with_photos(tmp_path, 2)
    view._items[0].adjust = PhotoAdjust(zoom=2.0, filter_name=FILTER_BW)
    view.photo_list.setCurrentRow(0)
    view._on_reset_adjust()
    assert view._items[0].adjust.is_identity


def test_adjustments_are_carried_into_export(qapp, tmp_path):
    from core.collage import FILTER_BW, PhotoAdjust

    view = _view_with_photos(tmp_path, 2)
    view._items[0].adjust = PhotoAdjust(filter_name=FILTER_BW)
    exported = view._export_photos()
    assert exported[0].adjust.filter_name == FILTER_BW


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def test_preset_round_trip_through_the_view(qapp, tmp_path):
    from core.collage import BG_GRADIENT

    view = _view_with_photos(tmp_path, 3)
    view.theme_choice.setCurrentText("Gallery Dark")
    view.sliders["spacing"].setValue(33)
    view.bg_style.setCurrentIndex(view.bg_style.findData(BG_GRADIENT))
    view.bg_color.set_color((12, 34, 56))
    view.title_text.setText("Saved Style")

    preset = view._gather_preset("House")
    assert preset.theme == "Gallery Dark"
    assert preset.spacing == 33
    assert preset.background_color == (12, 34, 56)
    assert preset.title == "Saved Style"

    fresh = CollageView()
    fresh.apply_preset(preset)
    assert fresh.theme_choice.currentData() == "Gallery Dark"
    assert fresh.sliders["spacing"].value() == 33
    assert fresh.bg_color.color() == (12, 34, 56)
    assert fresh.title_text.text() == "Saved Style"


def test_applying_a_preset_renders_only_once(qapp, tmp_path):
    """Preset application sets a dozen controls; it must coalesce into one
    render rather than firing one per control."""
    view = _view_with_photos(tmp_path, 3)
    preset = view._gather_preset("X")

    calls = {"n": 0}
    view._render_preview = lambda: calls.__setitem__("n", calls["n"] + 1)
    view._preview_timer.stop()
    view.apply_preset(preset)
    assert calls["n"] == 0          # deferred, not immediate
    assert view._preview_timer.isActive()  # exactly one pending render


def test_preset_dropdown_starts_with_a_none_entry(qapp):
    view = CollageView()
    assert view.preset_choice.count() >= 1
    assert view.preset_choice.itemData(0) is None


# --------------------------------------------------------------------------- #
# Print safety
# --------------------------------------------------------------------------- #
def test_low_resolution_photos_are_reported(qapp, tmp_path):
    small = _make_photo(tmp_path, "tiny.jpg", 120, 90)
    view = CollageView()
    view.add_photos([str(small)] * 4)
    view.size_choice.setCurrentText("A4 Landscape @300dpi")
    view._render_preview()

    assert "soft" in view.warning_label.text().lower()
    assert "tiny.jpg" in view.print_report.text()


def test_high_resolution_photos_report_no_problem(qapp, tmp_path):
    big = _make_photo(tmp_path, "big.jpg", 3000, 2400)
    view = CollageView()
    view.add_photos([str(big)] * 2)
    view.size_choice.setCurrentText("Instagram Square (1080x1080)")
    view._render_preview()

    assert view.warning_label.text() == ""
    assert "enough resolution" in view.print_report.text()


def test_resolution_check_uses_original_size_not_the_preview_copy(qapp, tmp_path):
    """The stored preview is downscaled to <=1000px, so checking that instead
    of the original would raise false warnings on perfectly good photos."""
    big = _make_photo(tmp_path, "big.jpg", 4000, 3000)
    view = CollageView()
    view.add_photos([str(big)])
    assert max(view._items[0].preview.size) <= 1000
    assert view._items[0].full_size == (4000, 3000)

    view.size_choice.setCurrentText("5x7 in @300dpi")
    view._render_preview()
    assert view.warning_label.text() == ""


def test_bleed_and_trim_marks_reach_the_renderer(qapp, tmp_path):
    view = _view_with_photos(tmp_path, 3)
    view.sliders["bleed"].setValue(30)
    view.trim_marks.setChecked(True)
    marks = view._current_marks()
    assert marks.bleed_frac == pytest.approx(0.03)
    assert marks.trim_marks is True

    view._render_preview()
    spec = view._current_spec(for_preview=True)
    # Bleed grows the rendered canvas beyond the nominal spec.
    assert view._rendered.width > spec.width_px


# --------------------------------------------------------------------------- #
# Auto-build
# --------------------------------------------------------------------------- #
def test_auto_build_without_a_folder_asks_for_one(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    shown = {}
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: shown.setdefault("called", True)
    )
    CollageView()._on_auto_build()
    assert shown.get("called") is True


def test_auto_build_picks_photos_from_a_folder(qapp, tmp_path, monkeypatch):
    import cv2
    from PyQt6.QtWidgets import QMessageBox

    # Auto-build runs the real scorer/face-detector pipeline against the temp
    # folder below. If it ever raises, _on_auto_build's `except Exception`
    # handler shows a QMessageBox.critical(...) dialog -- a modal
    # QDialog.exec() that blocks forever under the offscreen platform used
    # for headless test runs, since there's no user to dismiss it. That
    # turns a rare failure into an indefinite pytest hang instead of a
    # readable test failure. Mock it the same way
    # test_auto_build_reports_a_bad_folder mocks QMessageBox.warning, so any
    # future failure here shows up fast as an assertion with the error
    # message attached.
    crashed = {}
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *a, **k: crashed.setdefault("message", a[2] if len(a) > 2 else k.get("text")),
    )

    folder = tmp_path / "shoot"
    folder.mkdir()
    rng = np.random.default_rng(0)
    for i in range(6):
        cv2.imwrite(
            str(folder / f"p{i}.png"),
            rng.integers(0, 255, (240, 320, 3), dtype=np.uint8),
        )

    view = CollageView()
    view.auto_folder.setText(str(folder))
    view.auto_count.setValue(4)
    view._on_auto_build()

    assert not crashed, f"auto-build raised unexpectedly: {crashed.get('message')}"
    assert len(view._items) == 4
    assert view.photo_list.count() == 4
    assert view._rendered is not None or view._preview_timer.isActive()


def test_auto_build_reports_a_bad_folder(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    warned = {}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.setdefault("called", True)
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    view = CollageView()
    view.auto_folder.setText(str(empty))
    view._on_auto_build()
    assert warned.get("called") is True


# --------------------------------------------------------------------------- #
# MainWindow + mode chooser wiring
# --------------------------------------------------------------------------- #
def test_main_window_collage_mode_shows_only_the_tool(qapp):
    win = MainWindow(mode="collage")
    assert win.centralWidget() is win.collage
    assert isinstance(win.collage, CollageView)
    for attr in ("action_open", "action_album", "sidebar", "wizard", "passport"):
        assert not hasattr(win, attr), attr
    assert "Collage" in win.windowTitle()


def test_album_mode_has_no_collage_ui(qapp):
    win = MainWindow(mode="album")
    assert not hasattr(win, "collage")


def test_chooser_offers_three_modes(qapp):
    chooser = ModeChooserView()
    assert hasattr(chooser, "album_card")
    assert hasattr(chooser, "passport_card")
    assert hasattr(chooser, "collage_card")


def test_clicking_collage_card_emits_collage_mode(qapp):
    chooser = ModeChooserView()
    seen: list[str] = []
    chooser.modeChosen.connect(seen.append)
    chooser.collage_card.button.click()
    assert seen == [MODE_COLLAGE]


def test_choosing_collage_rebuilds_the_same_window_in_collage_mode(qapp):
    win = MainWindow(mode="chooser")
    win.chooser.collage_card.button.click()
    assert win._mode == MODE_COLLAGE
    assert isinstance(win.centralWidget(), CollageView)


def test_other_modes_still_reachable_from_the_chooser(qapp):
    for mode, expected in ((MODE_ALBUM, "album"), (MODE_PASSPORT, "passport")):
        win = MainWindow(mode="chooser")
        card = win.chooser.album_card if mode == MODE_ALBUM else win.chooser.passport_card
        card.button.click()
        assert win._mode == expected
