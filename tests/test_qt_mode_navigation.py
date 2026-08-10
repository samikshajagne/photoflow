"""
Tests for navigating *between* modes — specifically getting back out of one.

Entering a tool used to be a one-way trip: the only way to switch was closing
and reopening the application. These tests pin the fix, and in particular the
repeated round-tripping, because switching modes destroys widgets at the C++
level and any leftover Python reference to one raises
"wrapped C/C++ object has been deleted" the moment it's touched.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QToolBar

    from ui_qt.views.collage_view import CollageView
    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.mode_chooser_view import (
        MODE_ALBUM,
        MODE_COLLAGE,
        MODE_PASSPORT,
        ModeChooserView,
    )
    from ui_qt.views.passport_photo_view import PassportPhotoView
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_CARDS = {
    MODE_ALBUM: "album_card",
    MODE_PASSPORT: "passport_card",
    MODE_COLLAGE: "collage_card",
}


def _enter(win: MainWindow, mode: str) -> None:
    getattr(win.chooser, _CARDS[mode]).button.click()


# --------------------------------------------------------------------------- #
# The back action exists everywhere it should
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", [MODE_ALBUM, MODE_PASSPORT, MODE_COLLAGE])
def test_every_mode_offers_a_way_back(qapp, mode):
    win = MainWindow(mode="chooser")
    _enter(win, mode)
    assert win._mode == mode
    assert hasattr(win, "action_back"), f"{mode} has no way back to the menu"
    assert win.action_back.isEnabled()


@pytest.mark.parametrize("mode", [MODE_PASSPORT, MODE_COLLAGE])
def test_standalone_tools_get_a_toolbar_just_for_navigation(qapp, mode):
    """They have no album chrome, but still need somewhere to put Back."""
    win = MainWindow(mode=mode)
    assert win.findChildren(QToolBar), f"{mode} has no toolbar at all"
    assert hasattr(win, "action_back")


def test_the_chooser_itself_has_no_back_action(qapp):
    win = MainWindow(mode="chooser")
    assert not hasattr(win, "action_back")
    assert not win.findChildren(QToolBar)


def test_back_action_has_a_keyboard_shortcut(qapp):
    win = MainWindow(mode=MODE_COLLAGE)
    assert win.action_back.shortcut().toString() != ""


# --------------------------------------------------------------------------- #
# Going back actually works
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", [MODE_ALBUM, MODE_PASSPORT, MODE_COLLAGE])
def test_back_returns_to_the_chooser(qapp, mode):
    win = MainWindow(mode="chooser")
    _enter(win, mode)
    win.action_back.trigger()

    assert win._mode == "chooser"
    assert isinstance(win.centralWidget(), ModeChooserView)
    assert win.windowTitle() == "PhotoFlow"


def test_switching_between_tools_repeatedly_does_not_crash(qapp):
    """The regression that matters: stale references to destroyed widgets."""
    win = MainWindow(mode="chooser")
    for _ in range(3):
        _enter(win, MODE_COLLAGE)
        assert isinstance(win.centralWidget(), CollageView)
        win.action_back.trigger()

        _enter(win, MODE_PASSPORT)
        assert isinstance(win.centralWidget(), PassportPhotoView)
        win.action_back.trigger()

        _enter(win, MODE_ALBUM)
        assert win._mode == MODE_ALBUM
        win.action_back.trigger()

    assert win._mode == "chooser"


def test_going_back_removes_the_previous_tool_widgets(qapp):
    """Stale attributes pointing at deleted C++ objects are the bug here."""
    win = MainWindow(mode="chooser")
    _enter(win, MODE_COLLAGE)
    assert hasattr(win, "collage")

    win.action_back.trigger()
    assert not hasattr(win, "collage")

    _enter(win, MODE_PASSPORT)
    assert hasattr(win, "passport")
    assert not hasattr(win, "collage")


def test_album_actions_are_gone_after_leaving_album_mode(qapp):
    win = MainWindow(mode="chooser")
    _enter(win, MODE_ALBUM)
    assert hasattr(win, "action_open")

    win.action_back.trigger()
    assert not hasattr(win, "action_open")
    assert not hasattr(win, "action_album")


def test_only_one_toolbar_exists_after_switching(qapp):
    """Toolbars accumulate unless explicitly removed, which would stack rows."""
    win = MainWindow(mode="chooser")
    for mode in (MODE_ALBUM, MODE_COLLAGE, MODE_PASSPORT):
        _enter(win, mode)
        assert len(win.findChildren(QToolBar)) == 1, f"{mode}: toolbars piled up"
        win.action_back.trigger()


def test_returning_to_a_tool_starts_it_clean(qapp, tmp_path):
    """Re-entering the collage maker shouldn't remember the last session."""
    from PIL import Image

    photo = tmp_path / "p.jpg"
    Image.new("RGB", (400, 300), (120, 90, 60)).save(photo)

    win = MainWindow(mode="chooser")
    _enter(win, MODE_COLLAGE)
    win.collage.add_photos([str(photo)])
    assert len(win.collage._items) == 1

    win.action_back.trigger()
    _enter(win, MODE_COLLAGE)
    assert win.collage._items == []


def test_album_state_is_reset_when_leaving(qapp):
    win = MainWindow(mode="chooser")
    _enter(win, MODE_ALBUM)
    win._analyzed = True
    win._people_prepared = True

    win.action_back.trigger()
    assert win._analyzed is False
    assert win._people_prepared is False
    assert win._folder is None


# --------------------------------------------------------------------------- #
# Confirmation when work is in progress
# --------------------------------------------------------------------------- #
def test_busy_album_asks_before_switching(qapp, monkeypatch):
    win = MainWindow(mode="chooser")
    _enter(win, MODE_ALBUM)

    monkeypatch.setattr(win._analysis, "is_running", lambda: True)
    asked = {}
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: asked.setdefault("called", True) or QMessageBox.StandardButton.No,
    )
    win.action_back.trigger()

    assert asked.get("called") is True
    assert win._mode == MODE_ALBUM, "declining must keep the user where they were"


def test_confirming_while_busy_cancels_the_work_and_switches(qapp, monkeypatch):
    win = MainWindow(mode="chooser")
    _enter(win, MODE_ALBUM)

    monkeypatch.setattr(win._analysis, "is_running", lambda: True)
    cancelled = {}
    monkeypatch.setattr(win._analysis, "cancel", lambda: cancelled.setdefault("yes", True))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    win.action_back.trigger()

    assert cancelled.get("yes") is True
    assert win._mode == "chooser"


def test_idle_tools_switch_without_a_prompt(qapp, monkeypatch):
    """No work in progress means no interruption."""
    win = MainWindow(mode="chooser")
    _enter(win, MODE_COLLAGE)

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: pytest.fail("should not prompt when nothing is running"),
    )
    win.action_back.trigger()
    assert win._mode == "chooser"
