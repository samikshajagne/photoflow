"""
PhotoFlow main application window.

Three-panel layout (sidebar | center | metadata) in a splitter, a toolbar
(Open Folder / Analyze Folder / Cancel / Refresh), and a status bar.

- Open Folder browses before analysis: it scans the folder (reusing
  ``core.scanner.ImageScanner`` -- no analysis, no backend change), builds a
  :class:`~ui_qt.models.photo_index.PhotoIndex`, and shows the thumbnail grid.
- Analyze Folder runs the existing pipeline in a separate process
  (:class:`~ui_qt.workers.analysis_worker.AnalysisController`); on completion
  the sidebar counts fill, the grid groups by category, and selecting a photo
  shows its metrics + file metadata.

Folder loading and result handling are factored into plain methods
(:meth:`load_folder`, :meth:`apply_result`) so tests can drive them directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStyle,
    QToolBar,
    QWidget,
)

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.scanner import ImageScanner, ScanError
from utils.config import ConfigError, load_config
from utils.logger import get_logger
from ui_qt.models.photo_index import PhotoEntry, PhotoIndex
from ui_qt.views.center_view import CenterView
from ui_qt.views.metadata_panel import MetadataPanel
from ui_qt.views.sidebar import CategorySidebar
from ui_qt.workers.analysis_worker import AnalysisController

logger = get_logger("ui_qt.main_window")


class MainWindow(QMainWindow):
    """Top-level window hosting the toolbar and three-panel layout."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PhotoFlow")
        self.resize(1280, 800)

        self._folder: Optional[Path] = None
        self._result = None
        self._index: Optional[PhotoIndex] = None
        self._analyzed = False
        try:
            self._scanner: Optional[ImageScanner] = ImageScanner.from_config(load_config())
        except ConfigError as exc:  # pragma: no cover - defensive
            logger.warning("Could not load config for scanner: %s", exc)
            self._scanner = None

        self._analysis = AnalysisController(self)
        self._analysis.started.connect(self._on_analysis_started)
        self._analysis.progress.connect(self._on_analysis_progress)
        self._analysis.finished.connect(self._on_analysis_finished)
        self._analysis.failed.connect(self._on_analysis_failed)
        self._analysis.cancelled.connect(self._on_analysis_cancelled)

        self._build_toolbar()
        self._build_central()
        self.statusBar().showMessage("Open a folder to begin.")
        self._update_actions_enabled()

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        style = self.style()
        self.action_open = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open Folder"
        )
        self.action_open.triggered.connect(self._on_open_folder)

        self.action_analyze = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Analyze Folder"
        )
        self.action_analyze.triggered.connect(self._on_analyze_folder)

        self.action_cancel = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), "Cancel"
        )
        self.action_cancel.triggered.connect(self._on_cancel)
        self.action_cancel.setEnabled(False)

        self.action_refresh = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Refresh"
        )
        self.action_refresh.triggered.connect(self._on_refresh)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.sidebar = CategorySidebar()
        self.center = CenterView()
        self.metadata = MetadataPanel()

        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.center)
        splitter.addWidget(self.metadata)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([240, 800, 280])
        splitter.setChildrenCollapsible(False)

        self.sidebar.categorySelected.connect(self._on_category_selected)
        self.center.photoSelected.connect(self._on_photo_selected)
        self.setCentralWidget(splitter)

    # ----------------------------------------------------------------- #
    # Folder browsing (Phase 1/3)
    # ----------------------------------------------------------------- #
    def _on_open_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Open photo folder")
        if chosen:
            self.load_folder(chosen)

    def load_folder(self, folder: str | Path) -> int:
        """
        Browse ``folder``: scan it (no analysis) and show the thumbnail grid.

        Returns the number of supported images found.
        """
        path = Path(folder)
        self._folder = path
        self._result = None
        self._index = None
        self._analyzed = False
        self.setWindowTitle(f"PhotoFlow - {path}")
        self.sidebar.set_counts(None)
        self.metadata.clear()

        images: list[Path] = []
        if self._scanner is not None:
            try:
                images = self._scanner.scan(path)
            except ScanError as exc:
                logger.warning("Scan failed for '%s': %s", path, exc)
                self.center.show_message(f"Could not open folder:\n{exc}")
                self.statusBar().showMessage("Open failed.")
                self._update_actions_enabled()
                return 0

        self._index = PhotoIndex.from_paths(images)
        if images:
            self.center.set_entries(self._index.all_entries())
        else:
            self.center.show_message(f"No photos found in\n{path}")
        self.statusBar().showMessage(f"Loaded {len(images)} photo(s) from {path}")
        logger.info("Browsing folder '%s' (%d images).", path, len(images))
        self._update_actions_enabled()
        return len(images)

    def _on_refresh(self) -> None:
        if self._folder is not None and not self._analysis.is_running():
            self.load_folder(self._folder)

    # ----------------------------------------------------------------- #
    # Analysis (Phase 2)
    # ----------------------------------------------------------------- #
    def _on_analyze_folder(self) -> None:
        if self._folder is None or self._analysis.is_running():
            return
        self._analysis.start(str(self._folder), str(self._folder))

    def _on_cancel(self) -> None:
        self._analysis.cancel()

    def _on_analysis_started(self) -> None:
        self._analyzed = False
        self._set_analyzing(True)
        self.sidebar.set_counts(None)
        self.metadata.clear()
        self.center.show_message("Analyzing… progress is shown in the status bar.")
        self.statusBar().showMessage("Analysis started…")

    def _on_analysis_progress(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_analysis_finished(self, result: object) -> None:
        self._set_analyzing(False)
        self.apply_result(result)

    def apply_result(self, result: object) -> None:
        """Populate sidebar counts and the grouped grid from a PipelineResult."""
        self._result = result
        self._index = PhotoIndex.from_result(result)
        self._analyzed = True

        counts = getattr(result, "category_counts", {}) or {}
        self.sidebar.set_counts(counts)
        self.statusBar().showMessage("Analysis complete.")
        logger.info("Analysis complete: %s", counts)

        categories = self._index.categories()
        if categories:
            # Selecting a category fills the grid via _on_category_selected.
            self.sidebar.select_category(categories[0])
        else:
            self.center.show_message(self._summary_text(result))

    def _on_analysis_failed(self, message: str) -> None:
        self._set_analyzing(False)
        self.statusBar().showMessage("Analysis failed.")
        logger.error("Analysis failed: %s", message)
        QMessageBox.critical(self, "Analysis failed", message)

    def _on_analysis_cancelled(self) -> None:
        self._set_analyzing(False)
        self.sidebar.set_counts(None)
        self.center.show_message("Analysis cancelled.")
        self.statusBar().showMessage("Analysis cancelled.")

    @staticmethod
    def _summary_text(result: object) -> str:
        counts = getattr(result, "category_counts", {}) or {}
        return (
            "Analysis complete\n\n"
            f"Best Shots: {counts.get(FOLDER_BEST_SHOTS, 0)}    "
            f"Duplicates: {counts.get(FOLDER_DUPLICATES, 0)}\n"
            f"Blurry: {counts.get(FOLDER_BLURRY, 0)}    "
            f"Review: {counts.get(FOLDER_REVIEW, 0)}"
        )

    # ----------------------------------------------------------------- #
    # Selection / shared helpers
    # ----------------------------------------------------------------- #
    def _on_category_selected(self, category: str) -> None:
        if self._analyzed and self._index is not None:
            entries = self._index.entries(category)
            self.center.set_entries(entries)
            self.metadata.clear()
            self.statusBar().showMessage(f"{category}: {len(entries)} photo(s)")

    def _on_photo_selected(self, entry: PhotoEntry) -> None:
        self.metadata.show_entry(entry)

    def _set_analyzing(self, analyzing: bool) -> None:
        self.action_open.setEnabled(not analyzing)
        self.action_cancel.setEnabled(analyzing)
        if analyzing:
            self.action_analyze.setEnabled(False)
            self.action_refresh.setEnabled(False)
        else:
            self._update_actions_enabled()

    def _update_actions_enabled(self) -> None:
        has_folder = self._folder is not None
        running = self._analysis.is_running()
        self.action_open.setEnabled(not running)
        self.action_analyze.setEnabled(has_folder and not running)
        self.action_refresh.setEnabled(has_folder and not running)
        self.action_cancel.setEnabled(running)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self._analysis.is_running():
            self._analysis.cancel()
        self.center.shutdown()
        super().closeEvent(event)
