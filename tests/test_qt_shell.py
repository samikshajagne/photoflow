"""
Smoke tests for the PyQt6 desktop shell (Phase 1).

These run headless via Qt's 'offscreen' platform. PyQt6 needs native GL/Qt
libraries; where those are unavailable the whole module is skipped (so the
core suite stays green), and they run normally on a real desktop install.
"""

import os
from pathlib import Path

import pytest

# Must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Skip the whole module if PyQt6 or its native libraries can't load. (A
# missing shared library raises a plain ImportError, which importorskip would
# re-raise rather than skip, so we guard explicitly.)
try:
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    from ui_qt.views.main_window import MainWindow  # noqa: E402
    from ui_qt.views.sidebar import CATEGORY_ORDER, CategorySidebar  # noqa: E402
    from ui_qt.views.metadata_panel import MetadataPanel  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment without Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.organizer import (  # noqa: E402
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def test_sidebar_has_four_categories(qapp):
    sidebar = CategorySidebar()
    assert CATEGORY_ORDER == (
        FOLDER_BEST_SHOTS,
        FOLDER_DUPLICATES,
        FOLDER_BLURRY,
        FOLDER_REVIEW,
    )
    assert sidebar._list.count() == 4


def test_sidebar_counts_update(qapp):
    sidebar = CategorySidebar()
    sidebar.set_counts({FOLDER_BEST_SHOTS: 12, FOLDER_DUPLICATES: 8, FOLDER_BLURRY: 5, FOLDER_REVIEW: 40})
    texts = [sidebar._list.item(i).text() for i in range(4)]
    assert any("12" in t for t in texts)
    assert any("Best Shots" in t for t in texts)
    # Reset to placeholder.
    sidebar.set_counts(None)
    assert all("—" in sidebar._list.item(i).text() for i in range(4))


def test_sidebar_emits_selection(qapp):
    sidebar = CategorySidebar()
    received = []
    sidebar.categorySelected.connect(received.append)
    sidebar.select_category(FOLDER_DUPLICATES)
    assert received and received[-1] == FOLDER_DUPLICATES


# --------------------------------------------------------------------------- #
# Metadata panel
# --------------------------------------------------------------------------- #
def test_metadata_panel_placeholders_and_set(qapp):
    panel = MetadataPanel()
    assert panel._values["Quality score"].text() == "—"
    panel.set_field("Quality score", "87.5")
    assert panel._values["Quality score"].text() == "87.5"
    panel.clear()
    assert panel._values["Quality score"].text() == "—"


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
def test_main_window_has_toolbar_actions(qapp):
    win = MainWindow()
    labels = {win.action_open.text(), win.action_analyze.text(), win.action_refresh.text()}
    # "Open && Analyze": a single & would be swallowed by Qt as a keyboard
    # mnemonic and displayed as "Open  Analyze".
    assert labels == {"Open && Analyze", "Re-analyze", "Refresh"}
    # Re-analyze/Refresh disabled until a folder is loaded.
    assert win.action_analyze.isEnabled() is False
    assert win.action_refresh.isEnabled() is False


def test_main_window_has_three_panels(qapp):
    win = MainWindow()
    assert win.sidebar is not None
    assert win.center is not None
    assert win.metadata is not None


def test_load_folder_browses_without_analysis(qapp, tmp_path: Path):
    import cv2
    import numpy as np

    # Two supported images + one ignored file.
    img = np.full((32, 32, 3), 127, dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "a.jpg"), img)
    cv2.imwrite(str(tmp_path / "b.png"), img)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    win = MainWindow()
    count = win.load_folder(tmp_path)

    assert count == 2  # browsing counts images, no analysis
    assert win.action_analyze.isEnabled() is True
    assert win.action_refresh.isEnabled() is True
    # No PhotoFlow_Output created -- browsing must not run the pipeline.
    assert not (tmp_path / "PhotoFlow_Output").exists()


def test_load_missing_folder_is_handled(qapp, tmp_path: Path):
    win = MainWindow()
    count = win.load_folder(tmp_path / "does_not_exist")
    assert count == 0
