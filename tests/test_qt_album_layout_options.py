"""
Offscreen tests for the album layout-feature toggles (Phase 3/4).

Verifies the Album Settings dialog exposes the five layout flags, defaults smart
placement on and the rest off, round-trips preselected values, and reports the
chosen flags via ``layout_options()``. Skipped where PyQt6 can't load.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.album_settings_dialog import AlbumSettingsDialog
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_KEYS = {
    "smart_slot_ordering",
    "flexible_layout",
    "use_cutouts",
    "designed_cover",
    "theme_backgrounds",
    "theme",
}


def test_default_flags(qapp):
    opts = AlbumSettingsDialog().layout_options()
    assert set(opts) == _KEYS
    assert opts["smart_slot_ordering"] is True   # on by default
    assert opts["flexible_layout"] is False
    assert opts["use_cutouts"] is False
    assert opts["designed_cover"] is False
    assert opts["theme_backgrounds"] is False
    assert opts["theme"] == "classic"


def test_preselected_flags_round_trip(qapp):
    preset = {
        "smart_slot_ordering": False,
        "flexible_layout": True,
        "use_cutouts": True,
        "designed_cover": True,
        "theme_backgrounds": True,
        "theme": "natural",
    }
    dlg = AlbumSettingsDialog(layout_options=preset)
    assert dlg.layout_options() == preset


def test_toggling_a_checkbox_reflects_in_options(qapp):
    dlg = AlbumSettingsDialog()
    dlg.use_cutouts.setChecked(True)
    dlg.theme_backgrounds.setChecked(True)
    opts = dlg.layout_options()
    assert opts["use_cutouts"] is True
    assert opts["theme_backgrounds"] is True
    assert opts["designed_cover"] is False
