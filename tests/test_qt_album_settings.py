"""
Offscreen tests for the Album Settings dialog.

Verifies the dialog reports the chosen AlbumSpec and density, that presets
drive the width/height, and that Custom enables manual sizing. Skipped where
PyQt6 can't load.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.album_settings_dialog import AlbumSettingsDialog
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.album.layout import AlbumSpec  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_defaults_match_previous_hardcoded_spec(qapp):
    dlg = AlbumSettingsDialog()
    spec = dlg.album_spec()
    assert (spec.page_width_in, spec.page_height_in) == (12.0, 12.0)
    assert spec.dpi == 300
    assert spec.double_page_spread is True
    assert dlg.selected_density() == "balanced"


def test_loads_existing_spec_and_density(qapp):
    spec = AlbumSpec(page_width_in=10, page_height_in=10, dpi=150, double_page_spread=False)
    dlg = AlbumSettingsDialog(spec=spec, density="dense")
    out = dlg.album_spec()
    assert (out.page_width_in, out.page_height_in) == (10.0, 10.0)
    assert out.dpi == 150
    assert out.double_page_spread is False
    # Gutter is forced to 0 when single-page.
    assert out.gutter_in == 0.0
    assert dlg.selected_density() == "dense"


def test_custom_size_is_editable(qapp):
    dlg = AlbumSettingsDialog()
    custom_index = dlg.preset.count() - 1  # "Custom…" is last
    dlg.preset.setCurrentIndex(custom_index)
    assert dlg.width_in.isEnabled()
    dlg.width_in.setValue(16.0)
    dlg.height_in.setValue(9.0)
    spec = dlg.album_spec()
    assert (spec.page_width_in, spec.page_height_in) == (16.0, 9.0)


def test_density_choices_round_trip(qapp):
    for density in ("spacious", "balanced", "dense"):
        dlg = AlbumSettingsDialog(density=density)
        assert dlg.selected_density() == density
