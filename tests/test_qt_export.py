"""
Offscreen tests for the album export UI wiring.

Verifies the Export Album toolbar action exists and is gated on a generated
album, that the ExportDialog reports the chosen formats, and that the main
window's blocking export actually writes files via core.album.raster.

Skipped wholesale where PyQt6 can't load.
"""

import os
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.export_dialog import ExportDialog
    from ui_qt.views.main_window import MainWindow
    from ui_qt.workers.album_workers import ExportWorker
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.album.project import AlbumProject, PhotoRecord, SpreadRecord  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _album(tmp_path: Path) -> AlbumProject:
    a = str(tmp_path / "a.jpg")
    b = str(tmp_path / "b.jpg")
    Image.new("RGB", (200, 200), (220, 20, 20)).save(a, "JPEG")
    Image.new("RGB", (200, 200), (20, 20, 220)).save(b, "JPEG")
    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 300})
    proj.add_photo(PhotoRecord(source_path=a))
    proj.add_photo(PhotoRecord(source_path=b))
    proj.spreads = [
        SpreadRecord(
            index=0,
            section="Cover",
            width_px=400,
            height_px=200,
            placements=[
                {"path": a, "frame_px": [0, 0, 200, 200], "crop": [0, 0, 1, 1]},
                {"path": b, "frame_px": [200, 0, 200, 200], "crop": [0, 0, 1, 1]},
            ],
        )
    ]
    return proj


def test_export_action_gated_on_album(qapp, tmp_path):
    win = MainWindow()
    assert hasattr(win, "action_export")
    # No album yet -> disabled.
    assert not win.action_export.isEnabled()
    # With an album present -> enabled.
    win._album_project = _album(tmp_path)
    win._update_actions_enabled()
    assert win.action_export.isEnabled()


def test_export_dialog_reports_formats(qapp):
    dlg = ExportDialog(Path("/tmp/renders"))
    # PNG is on by default.
    assert dlg.selected_formats() == ["png"]
    dlg.cb_jpg.setChecked(True)
    dlg.cb_pdf.setChecked(True)
    dlg.cb_psd.setChecked(True)
    assert dlg.selected_formats() == ["png", "jpg", "pdf", "psd"]
    assert dlg.apply_edits() is True
    assert dlg.output_dir() == Path("/tmp/renders")


def test_export_worker_writes_files(qapp, tmp_path):
    # Calling run() directly executes the worker body synchronously on this
    # thread; same-thread signal connections fire synchronously too.
    worker = ExportWorker(_album(tmp_path), ["png", "pdf"], tmp_path / "out", apply_edits=True)
    captured: dict = {}
    worker.succeeded.connect(lambda payload: captured.update(ok=payload))
    worker.run()
    assert "ok" in captured
    results, skipped, out_dir = captured["ok"]
    assert set(results) == {"png", "pdf"}
    assert (tmp_path / "out" / "spread_01.png").exists()
    assert (tmp_path / "out" / "album.pdf").exists()


def test_export_worker_cancel_before_run_emits_canceled(qapp, tmp_path):
    worker = ExportWorker(_album(tmp_path), ["png"], tmp_path / "out", apply_edits=True)
    events: list = []
    worker.canceled.connect(lambda: events.append("canceled"))
    worker.succeeded.connect(lambda *_: events.append("succeeded"))
    worker.cancel()  # request cancel before starting
    worker.run()
    assert events == ["canceled"]


def test_main_window_has_export_worker_state(qapp):
    win = MainWindow()
    assert win._export_worker is None
    assert win._export_dialog is None
