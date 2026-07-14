"""
Offscreen tests for the guided wizard.

Cover the WizardBar widget (CTA text + actionRequested signal) and the
MainWindow step transitions (open&analyze -> album -> export). Skipped
wholesale where PyQt6 can't load.
"""

import os
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.wizard_bar import STEPS, WizardBar
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.album.project import AlbumProject  # noqa: E402
from core.pipeline import PipelineResult  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _empty_result() -> PipelineResult:
    """A minimal completed-analysis result (no photos) for transition tests."""
    return PipelineResult(
        input_folder="/x",
        scanned_count=0,
        duplicate_group_count=0,
        duplicate_count=0,
        blurry_count=0,
        faces_detected_count=0,
        dry_run=False,
        output_root=None,
        category_counts={},
        organization=None,
        blur_failures=(),
        face_failures=(),
        quality_results=(),
        best_shot_candidates=(),
    )


# --------------------------------------------------------------------------- #
# WizardBar widget
# --------------------------------------------------------------------------- #
def test_wizard_bar_cta_and_signal(qapp):
    bar = WizardBar()
    captured: list = []
    bar.actionRequested.connect(captured.append)

    bar.update_view("album", {"open"}, busy=False)
    assert bar._cta.text() == "Build Album"
    assert bar._cta.isEnabled()

    bar._cta.click()
    assert captured == ["album"]


def test_wizard_bar_busy_disables_cta(qapp):
    bar = WizardBar()
    bar.update_view("album", {"open"}, busy=True)
    assert not bar._cta.isEnabled()


def test_wizard_steps_order():
    assert [k for k, _ in STEPS] == ["open", "people", "album", "export"]


# --------------------------------------------------------------------------- #
# MainWindow transitions
# --------------------------------------------------------------------------- #
def test_initial_step_is_open(qapp):
    win = MainWindow()
    assert win._wizard_step == "open"
    assert win._wizard_done == set()


def test_load_folder_stays_on_open_until_analyzed(qapp, tmp_path):
    # Browsing a folder (no analysis yet) leaves the user on the combined
    # Open & Analyze step; "open" is not marked done until analysis finishes.
    Image.new("RGB", (32, 32), (200, 0, 0)).save(tmp_path / "p.jpg", "JPEG")
    win = MainWindow()
    win.load_folder(tmp_path)
    assert win._wizard_step == "open"
    assert win._wizard_done == set()


def test_empty_folder_stays_on_open(qapp, tmp_path):
    win = MainWindow()
    win.load_folder(tmp_path)  # no images
    assert win._wizard_step == "open"


def test_apply_result_advances_to_people(qapp):
    # Analysis completing finishes Open & Analyze; the people-first flow then
    # goes to the Label People step before building the album.
    win = MainWindow()
    win.apply_result(_empty_result())
    assert win._wizard_step == "people"
    assert "open" in win._wizard_done


def test_present_album_advances_to_export(qapp, tmp_path):
    win = MainWindow()
    win._album_project = AlbumProject.new(str(tmp_path), album_spec={"dpi": 300})
    win._present_album()
    assert win._wizard_step == "export"
    assert {"open", "people", "album"} <= win._wizard_done
