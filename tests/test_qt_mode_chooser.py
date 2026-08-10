"""
Offscreen tests for the in-window startup chooser.

Covers the standalone ``ModeChooserView`` widget (clicking either card emits
``modeChosen`` with the right value) and ``MainWindow``'s use of it: a bare
``MainWindow(mode="chooser")`` shows only the landing page (no toolbar, no
album/passport state), and choosing a card rebuilds the *same* window object
in place via ``_enter_mode`` -- no separate popup dialog.

Skipped wholesale where PyQt6 can't load.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QToolBar

    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.mode_chooser_view import MODE_ALBUM, MODE_PASSPORT, ModeChooserView
    from ui_qt.views.passport_photo_view import PassportPhotoView
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------- #
# ModeChooserView in isolation
# --------------------------------------------------------------------------- #
def test_clicking_album_card_emits_album_mode(qapp):
    view = ModeChooserView()
    captured = []
    view.modeChosen.connect(captured.append)
    view.album_card.clicked.emit()
    assert captured == [MODE_ALBUM]


def test_clicking_passport_card_emits_passport_mode(qapp):
    view = ModeChooserView()
    captured = []
    view.modeChosen.connect(captured.append)
    view.passport_card.clicked.emit()
    assert captured == [MODE_PASSPORT]


def test_card_button_click_emits_once(qapp):
    """Clicking the card's own button must not double-emit via the card's
    mousePressEvent fallback."""
    view = ModeChooserView()
    captured = []
    view.modeChosen.connect(captured.append)
    view.album_card.button.click()
    assert captured == [MODE_ALBUM]


# --------------------------------------------------------------------------- #
# MainWindow in "chooser" mode + transition via _enter_mode
# --------------------------------------------------------------------------- #
def test_main_window_chooser_mode_shows_only_the_landing_page(qapp):
    win = MainWindow(mode="chooser")
    assert win.centralWidget() is win.chooser
    assert isinstance(win.chooser, ModeChooserView)
    assert len(win.findChildren(QToolBar)) == 0
    for attr in ("action_open", "passport", "sidebar", "wizard"):
        assert not hasattr(win, attr), attr


def test_choosing_album_rebuilds_same_window_in_album_mode(qapp):
    win = MainWindow(mode="chooser")
    win.chooser.album_card.clicked.emit()
    assert win._mode == "album"
    assert hasattr(win, "action_open")
    assert len(win.findChildren(QToolBar)) == 1
    assert not hasattr(win, "chooser") or win.centralWidget() is not win.chooser


def test_choosing_passport_rebuilds_same_window_in_passport_mode(qapp):
    win = MainWindow(mode="chooser")
    win.chooser.passport_card.clicked.emit()
    assert win._mode == "passport"
    assert isinstance(win.passport, PassportPhotoView)
    assert win.centralWidget() is win.passport
    # Passport mode now gets a minimal toolbar holding just the "All Tools"
    # action -- without it, entering a tool was a one-way trip and the only way
    # to switch was restarting the app. It still has none of the album chrome.
    assert len(win.findChildren(QToolBar)) == 1
    assert hasattr(win, "action_back")
    assert not hasattr(win, "action_open")


def test_default_main_window_is_still_album_mode(qapp):
    """Plain MainWindow() (no mode arg) must keep working for existing
    callers/tests that expect the album UI immediately."""
    win = MainWindow()
    assert win._mode == "album"
    assert hasattr(win, "action_open")
