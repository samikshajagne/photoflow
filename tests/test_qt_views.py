"""
Offscreen tests for the Phase 3 views: thumbnail loader/model, grid, loupe,
metadata population, and the MainWindow browse/analyzed wiring.

Skipped wholesale where PyQt6/native libs can't load.
"""

import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication

    from ui_qt.models.photo_list_model import ENTRY_ROLE, PhotoListModel
    from ui_qt.views.center_view import PAGE_GRID, PAGE_LOUPE, PAGE_PLACEHOLDER, CenterView
    from ui_qt.views.grid_view import ThumbnailGrid
    from ui_qt.views.main_window import MainWindow
    from ui_qt.views.metadata_panel import MetadataPanel
    from ui_qt.workers.thumbnail_loader import ThumbnailLoader
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.organizer import FOLDER_BEST_SHOTS, FOLDER_DUPLICATES  # noqa: E402
from ui_qt.models.photo_index import PhotoEntry, PhotoIndex  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _photo(path: Path, value: int = 127) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((64, 80, 3), value, np.uint8))
    return path


def _spin_until(qapp, predicate, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while not predicate() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    return predicate()


# --------------------------------------------------------------------------- #
# Thumbnail loader
# --------------------------------------------------------------------------- #
def test_loader_decodes_scaled_thumbnail(qapp, tmp_path: Path):
    img = _photo(tmp_path / "a.jpg")
    loader = ThumbnailLoader(edge=32, max_threads=2)
    got = {}
    loader.thumbnailReady.connect(lambda p, im: got.update({p: im}))

    loader.request(str(img))
    assert _spin_until(qapp, lambda: str(img) in got)
    image: QImage = got[str(img)]
    assert not image.isNull()
    assert max(image.width(), image.height()) <= 32  # scaled down
    loader.shutdown()


def test_loader_dedupes_inflight(qapp, tmp_path: Path):
    img = _photo(tmp_path / "a.jpg")
    loader = ThumbnailLoader(edge=32)
    loader.request(str(img))
    loader.request(str(img))  # duplicate, ignored while in flight
    assert _spin_until(qapp, lambda: len(loader._inflight) == 0)
    loader.shutdown()


# --------------------------------------------------------------------------- #
# List model
# --------------------------------------------------------------------------- #
def test_model_rows_and_roles(qapp, tmp_path: Path):
    a = _photo(tmp_path / "a.jpg")
    b = _photo(tmp_path / "b.png")
    loader = ThumbnailLoader(edge=32)
    model = PhotoListModel(loader)
    model.set_entries([PhotoEntry(str(a)), PhotoEntry(str(b))])

    assert model.rowCount() == 2
    idx0 = model.index(0, 0)
    assert model.data(idx0, int(Qt.ItemDataRole.DisplayRole)) == "a.jpg"
    assert isinstance(model.data(idx0, ENTRY_ROLE), PhotoEntry)
    # Decoration returns a pixmap (placeholder initially) and kicks off a load.
    pm = model.data(idx0, int(Qt.ItemDataRole.DecorationRole))
    assert pm is not None
    loader.shutdown()


# --------------------------------------------------------------------------- #
# Grid + center navigation
# --------------------------------------------------------------------------- #
def test_grid_emits_selection(qapp, tmp_path: Path):
    a = _photo(tmp_path / "a.jpg")
    grid = ThumbnailGrid(edge=32)
    selected = []
    grid.photoSelected.connect(selected.append)
    grid.set_entries([PhotoEntry(str(a))])
    grid.setCurrentIndex(grid._model.index(0, 0))
    assert _spin_until(qapp, lambda: len(selected) >= 1)
    assert selected[-1].name == "a.jpg"
    grid.shutdown()


def test_center_navigates_placeholder_grid_loupe(qapp, tmp_path: Path):
    a = _photo(tmp_path / "a.jpg")
    center = CenterView()
    assert center.currentIndex() == PAGE_PLACEHOLDER
    center.set_entries([PhotoEntry(str(a))])
    assert center.currentIndex() == PAGE_GRID
    center._open_loupe(PhotoEntry(str(a)))
    assert center.currentIndex() == PAGE_LOUPE
    center.show_grid()
    assert center.currentIndex() == PAGE_GRID
    center.shutdown()


def test_center_loupe_next_prev_navigation(qapp, tmp_path: Path):
    paths = [_photo(tmp_path / f"{c}.jpg") for c in "abc"]
    entries = [PhotoEntry(str(p)) for p in paths]
    center = CenterView()
    center.set_entries(entries)

    # Open the middle photo, then walk forward and backward.
    center._open_loupe(entries[1])
    assert center.currentIndex() == PAGE_LOUPE
    assert center._loupe_index == 1

    center._show_next()
    assert center._loupe_index == 2
    # At the end: Next is a no-op and the Next button is disabled.
    center._show_next()
    assert center._loupe_index == 2
    assert center.loupe._next_btn.isEnabled() is False

    center._show_prev()
    assert center._loupe_index == 1
    center._show_prev()
    assert center._loupe_index == 0
    # At the start: Prev is a no-op and disabled.
    center._show_prev()
    assert center._loupe_index == 0
    assert center.loupe._prev_btn.isEnabled() is False
    center.shutdown()


# --------------------------------------------------------------------------- #
# Metadata panel
# --------------------------------------------------------------------------- #
def test_metadata_show_entry_with_metrics(qapp, tmp_path: Path):
    a = _photo(tmp_path / "shot.jpg")
    panel = MetadataPanel()
    entry = PhotoEntry(
        source_path=str(a),
        category=FOLDER_BEST_SHOTS,
        quality_score=87.5,
        blur_score=4210.0,
        face_count=2,
        faces_detected=True,
        is_best_shot=True,
    )
    panel.show_entry(entry)
    assert panel._values["Quality score"].text() == "87.5"
    assert panel._values["Face count"].text() == "2"
    assert panel._values["Blur score"].text() == "4210"
    assert panel._values["Name"].text() == "shot.jpg"
    assert "×" in panel._values["Dimensions"].text()  # 80 × 64


def test_metadata_browse_entry_has_placeholder_metrics(qapp, tmp_path: Path):
    a = _photo(tmp_path / "x.jpg")
    panel = MetadataPanel()
    panel.show_entry(PhotoEntry(source_path=str(a)))  # no metrics (browse)
    assert panel._values["Quality score"].text() == "—"
    assert panel._values["Name"].text() == "x.jpg"


# --------------------------------------------------------------------------- #
# MainWindow integration (no subprocess)
# --------------------------------------------------------------------------- #
def test_main_window_browse_populates_grid(qapp, tmp_path: Path):
    _photo(tmp_path / "a.jpg")
    _photo(tmp_path / "b.png")
    win = MainWindow()
    count = win.load_folder(tmp_path)
    assert count == 2
    assert win.center.currentIndex() == PAGE_GRID
    assert win.center.grid._model.rowCount() == 2


def _fake_result(tmp_path: Path):
    import dataclasses

    from core.pipeline import PipelineResult
    from core.quality_scorer import QualityResult

    a = _photo(tmp_path / "keep.jpg")
    b = _photo(tmp_path / "dup.jpg")

    @dataclasses.dataclass
    class _Op:
        source: str
        destination: str
        category: str

    @dataclasses.dataclass
    class _Org:
        operations: tuple
        output_root: str = ""
        skipped: tuple = ()

    org = _Org((
        _Op(str(a), str(tmp_path / "out/BestShots/keep.jpg"), FOLDER_BEST_SHOTS),
        _Op(str(b), str(tmp_path / "out/Duplicates/dup.jpg"), FOLDER_DUPLICATES),
    ))
    quality = (
        QualityResult(str(a), 90.0, 5000.0, 128.0, 70.0, True, 1),
        QualityResult(str(b), 80.0, 4000.0, 120.0, 60.0, False, 0),
    )
    return PipelineResult(
        input_folder=str(tmp_path),
        scanned_count=2,
        duplicate_group_count=1,
        duplicate_count=1,
        blurry_count=0,
        faces_detected_count=1,
        dry_run=False,
        output_root=str(tmp_path / "out/PhotoFlow_Output"),
        category_counts={FOLDER_BEST_SHOTS: 1, FOLDER_DUPLICATES: 1},
        organization=org,
        blur_failures=(),
        face_failures=(),
        quality_results=quality,
        best_shot_candidates=(str(a),),
    )


def test_main_window_apply_result_groups_and_selects(qapp, tmp_path: Path):
    win = MainWindow()
    win.apply_result(_fake_result(tmp_path))

    texts = [win.sidebar._list.item(i).text() for i in range(4)]
    assert any("Best Shots" in t and "1" in t for t in texts)
    assert win.center.currentIndex() == PAGE_GRID
    assert win.center.grid._model.rowCount() == 1
    win._on_photo_selected(win._index.get(str(tmp_path / "keep.jpg")))
    assert win.metadata._values["Quality score"].text() == "90.0"
    win.center.shutdown()
