"""
PhotoFlow main application window.

Three-panel layout (sidebar | center | metadata) in a splitter, a toolbar
(Open & Analyze / Re-analyze / Cancel / Refresh), and a status bar.

- Open & Analyze scans the chosen folder (reusing
  ``core.scanner.ImageScanner``), shows the thumbnail grid, and then flows
  straight into analysis — one action for the user, not two. ``load_folder``
  itself stays analysis-free so plain browsing/refresh never runs the pipeline.
- Analysis runs the existing pipeline in a separate process
  (:class:`~ui_qt.workers.analysis_worker.AnalysisController`); on completion
  the sidebar counts fill, the grid groups by category, and selecting a photo
  shows its metrics + file metadata. "Re-analyze" re-runs it on demand.

Folder loading and result handling are factored into plain methods
(:meth:`load_folder`, :meth:`apply_result`) so tests can drive them directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QEventLoop
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStyle,
    QToolBar,
    QVBoxLayout,
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
from ui_qt.views.wizard_bar import WizardBar
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
        # Album state (Phase 1/2).
        self._album_project = None
        self._album_dir: Optional[Path] = None
        self._album_entries: dict[str, list[PhotoEntry]] = {}
        # True once person clusters have been discovered for the current folder
        # (the people-first "Label People" step), so we don't re-run the pass.
        self._people_prepared = False
        # Album settings chosen by the user (None until first Build Album).
        self._album_spec = None
        self._album_density = "balanced"
        # Background export worker + its progress dialog (None when idle).
        self._export_worker = None
        self._export_dialog: Optional[QProgressDialog] = None
        # Guided-wizard state: the step the user is on and the steps finished.
        self._wizard_step = "open"
        self._wizard_done: set[str] = set()
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
        self._analysis.stopped.connect(self._update_actions_enabled)

        self._build_toolbar()
        self._build_central()
        self.statusBar().showMessage("Open a folder to begin.")
        self._update_actions_enabled()
        self._refresh_wizard()

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
            style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open & Analyze"
        )
        self.action_open.triggered.connect(self._on_open_folder)

        # Secondary: re-run analysis on the already-open folder (opening a
        # folder analyzes it automatically, so this is for power users only).
        self.action_analyze = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Re-analyze"
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

        toolbar.addSeparator()
        self.action_album = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Generate Album",
        )
        self.action_album.triggered.connect(self._on_generate_album)

        self.action_label = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogYesButton), "Label People"
        )
        self.action_label.triggered.connect(self._on_label_people)
        self.action_label.setEnabled(False)

        self.action_export = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export Album"
        )
        self.action_export.triggered.connect(self._on_export_album)
        self.action_export.setEnabled(False)

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
        self.sidebar.sectionSelected.connect(self._on_section_selected)
        self.center.photoSelected.connect(self._on_photo_selected)

        # Guided step bar across the top, with the three-panel workspace below.
        self.wizard = WizardBar()
        self.wizard.actionRequested.connect(self._on_wizard_action)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.wizard)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(container)

    # ----------------------------------------------------------------- #
    # Folder browsing (Phase 1/3)
    # ----------------------------------------------------------------- #
    def _on_open_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Open photo folder")
        if not chosen:
            return
        # Opening a folder flows straight into analysis — one action, not two.
        # (``load_folder`` itself stays analysis-free so plain browsing/refresh
        # never triggers the pipeline.)
        found = self.load_folder(chosen)
        if found and not self._analysis.is_running():
            self._on_analyze_folder()

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
        self._album_project = None
        self._album_dir = None
        self._album_entries = {}
        self._people_prepared = False
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
        # Opening a new folder restarts the guided flow. The "open" step is not
        # marked done until analysis finishes (Open & Analyze is one step), so
        # browsing alone leaves the user on the first step.
        self._wizard_done = set()
        self._wizard_step = "open"
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

        # Analysis done -> the Open & Analyze step is complete; the people-first
        # flow now labels people before building the album.
        self._wizard_done.update({"open"})
        self._wizard_step = "people"
        self._refresh_wizard()

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
    # Album generation + identity labelling (Phase 1/2)
    # ----------------------------------------------------------------- #
    def _on_generate_album(self) -> None:
        if self._folder is None or self._analysis.is_running():
            return
        # Let the user choose album size / quality / density before building.
        from ui_qt.views.album_settings_dialog import AlbumSettingsDialog

        dialog = AlbumSettingsDialog(self, spec=self._album_spec, density=self._album_density)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._album_spec = dialog.album_spec()
        self._album_density = dialog.selected_density()

        if not self._generate_album("Building your album… this can take a while."):
            return
        self._present_album()
        self._update_actions_enabled()

    def _generate_album(self, status: str) -> bool:
        """Build the album on the current folder in the background. True on success."""
        from ui_qt.workers.album_workers import GenerateWorker

        worker = GenerateWorker(str(self._folder), self._album_spec, self._album_density)
        ok, payload = self._run_busy_worker(worker, status)
        if not ok:
            logger.error("Album generation failed: %s", payload)
            QMessageBox.critical(self, "Album generation failed", str(payload))
            self.statusBar().showMessage("Album generation failed.")
            return False
        self._album_project = payload
        self._album_dir = Path(self._album_project.export.manifest_path).parent
        self._people_prepared = True  # generate() also discovers clusters
        return True

    def _prepare_people(self, status: str) -> bool:
        """
        Discover person clusters on the current folder (people-first flow).

        Runs the orchestrator's people pass in the background and stores the
        resulting project (photos + clusters, no layout) so it can be labelled.
        True on success.
        """
        from ui_qt.workers.album_workers import PreparePeopleWorker

        worker = PreparePeopleWorker(str(self._folder))
        ok, payload = self._run_busy_worker(worker, status)
        if not ok:
            logger.error("Finding people failed: %s", payload)
            QMessageBox.critical(self, "Could not find people", str(payload))
            self.statusBar().showMessage("Finding people failed.")
            return False
        self._album_project = payload
        self._album_dir = Path(self._album_project.export.manifest_path).parent
        self._people_prepared = True
        return True

    def _run_busy_worker(self, worker, status: str) -> tuple[bool, object]:
        """
        Run a background worker behind a modal busy dialog, blocking via a
        nested event loop until it finishes.

        Returns ``(ok, payload)`` — the worker's result on success, or its error
        message on failure. The worker must expose ``succeeded(object)`` and
        ``failed(str)`` signals. Used for both the album build and the people
        pass (neither has a progress signal nor is cleanly cancellable, so the
        dialog is indeterminate with no Cancel button).
        """
        self.statusBar().showMessage(status)
        dialog = QProgressDialog(status, None, 0, 0, self)
        dialog.setWindowTitle("PhotoFlow")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setCancelButton(None)

        outcome: dict[str, object] = {}
        loop = QEventLoop()

        def _ok(result: object) -> None:
            outcome["result"] = result
            loop.quit()

        def _err(message: str) -> None:
            outcome["error"] = message
            loop.quit()

        worker.succeeded.connect(_ok)
        worker.failed.connect(_err)
        worker.start()
        dialog.show()
        loop.exec()
        dialog.close()
        worker.wait()

        if "error" in outcome:
            return False, outcome["error"]
        return True, outcome.get("result")

    def _present_album(self) -> None:
        """Show the generated album: its sections in the sidebar, photos in the grid."""
        project = self._album_project
        if project is None:
            return
        self._album_entries = self._build_album_entries(project)
        sections = [(s.name, len(s.photos)) for s in project.sections]
        self.sidebar.set_sections(sections)

        n_clusters = len(project.clusters)
        n_labelled = sum(1 for c in project.clusters if c.label)
        jsx = ""
        if project.export.manifest_path:
            jsx_path = Path(project.export.manifest_path).parent / "photoshop_album.jsx"
            if jsx_path.exists():
                jsx = f"  Photoshop script: {jsx_path}"
        self.statusBar().showMessage(
            f"Album: {len(project.sections)} section(s), {len(project.spreads)} spread(s); "
            f"people {n_clusters} (labelled {n_labelled}). "
            f"Manifest: {project.export.manifest_path}{jsx}"
        )
        if sections:
            # Selecting a section fills the grid via _on_section_selected.
            self.sidebar.select_section(sections[0][0])
        else:
            self.center.show_message("Album generated, but no eligible photos to show.")

        # Album built -> the guided flow can move on to exporting.
        self._wizard_done.update({"open", "people", "album"})
        self._wizard_step = "export"
        self._refresh_wizard()

    def _build_album_entries(self, project) -> dict[str, list[PhotoEntry]]:
        """Turn each album section's photo paths into grid-ready PhotoEntry rows."""
        entries: dict[str, list[PhotoEntry]] = {}
        for section in project.sections:
            rows: list[PhotoEntry] = []
            for path in section.photos:
                rec = project.get(path)
                if rec is None:
                    rows.append(PhotoEntry(source_path=path))
                    continue
                rows.append(
                    PhotoEntry(
                        source_path=rec.source_path,
                        category=rec.category,
                        quality_score=rec.quality_score,
                        blur_score=rec.blur_score,
                        face_count=rec.face_count,
                        faces_detected=rec.faces_detected,
                        is_best_shot=rec.is_best_shot,
                        tier=rec.tier,
                    )
                )
            entries[section.name] = rows
        return entries

    def _on_section_selected(self, name: str) -> None:
        entries = self._album_entries.get(name, [])
        self.center.set_entries(entries)
        self.metadata.clear()
        self.statusBar().showMessage(f"{name}: {len(entries)} photo(s)")

    def _on_label_people(self) -> None:
        if self._folder is None or self._analysis.is_running():
            return
        # People-first: discover the person clusters before labelling. This is
        # cheap after analysis (faces + embeddings are cached) and does not
        # build the album yet.
        if not self._people_prepared:
            if not self._prepare_people("Finding the people in your photos…"):
                return
        if self._album_project is None:
            return

        from ui_qt.views.identity_panel import IdentityPanel

        dialog = QDialog(self)
        dialog.setWindowTitle("Label People")
        dialog.resize(640, 720)
        panel = IdentityPanel(self._album_project)
        layout = QVBoxLayout(dialog)
        layout.addWidget(panel)

        def _on_applied() -> None:
            try:
                if self._album_dir is not None:
                    self._album_project.save(self._album_dir)
            finally:
                dialog.accept()

        panel.applied.connect(_on_applied)
        dialog.finished.connect(lambda *_: panel.shutdown())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted

        if accepted:
            # Labels are saved to the manifest; building the album re-binds them
            # by centroid. Advance the guided flow to the album build.
            self._wizard_done.add("people")
            self._wizard_step = "album"
            self._refresh_wizard()
        self._update_actions_enabled()

    # ----------------------------------------------------------------- #
    # Album export (PNG / JPG / PDF / layered PSD — no Photoshop needed)
    # ----------------------------------------------------------------- #
    def _on_export_album(self) -> None:
        if self._album_project is None or self._analysis.is_running():
            return

        from ui_qt.views.export_dialog import ExportDialog

        default_dir = (self._album_dir or Path.cwd()) / "renders"
        dialog = ExportDialog(default_dir, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        formats = dialog.selected_formats()
        if not formats:
            QMessageBox.information(
                self, "Export Album", "Select at least one format to export."
            )
            return

        self._start_export(formats, dialog.output_dir(), dialog.apply_edits())

    def _start_export(self, formats: list[str], out_dir: Path, apply_edits: bool) -> None:
        """Render the album in a background worker with progress + cancel."""
        if self._export_worker is not None and self._export_worker.isRunning():
            return
        from ui_qt.workers.album_workers import ExportWorker

        worker = ExportWorker(self._album_project, formats, out_dir, apply_edits)
        dialog = QProgressDialog(
            f"Exporting album as {', '.join(formats)}…", "Cancel", 0, len(formats), self
        )
        dialog.setWindowTitle("Export Album")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(worker.cancel)

        worker.progress.connect(self._on_export_progress)
        worker.succeeded.connect(self._on_export_succeeded)
        worker.failed.connect(self._on_export_failed)
        worker.canceled.connect(self._on_export_canceled)
        # Tear-down common to every outcome.
        for sig in (worker.succeeded, worker.failed, worker.canceled):
            sig.connect(lambda *_: self._finish_export())

        self._export_worker = worker
        self._export_dialog = dialog
        self._update_actions_enabled()
        self.statusBar().showMessage("Exporting album…")
        dialog.show()
        worker.start()

    def _on_export_progress(self, done: int, total: int, message: str) -> None:
        if self._export_dialog is not None:
            self._export_dialog.setMaximum(total)
            self._export_dialog.setValue(done)
            self._export_dialog.setLabelText(message)

    def _on_export_succeeded(self, payload: object) -> None:
        results, skipped, out_dir = payload  # type: ignore[misc]
        n_files = sum(len(v) if isinstance(v, list) else 1 for v in results.values())
        self.statusBar().showMessage(
            f"Exported {n_files} file(s) in {len(results)} format(s) to {out_dir}"
        )
        self._wizard_done.add("export")
        self._refresh_wizard()
        if skipped:
            QMessageBox.warning(
                self,
                "Export Album",
                f"{len(skipped)} photo(s) could not be rendered and were left "
                f"blank in the album (missing or unreadable source files):\n\n"
                + "\n".join(skipped[:10])
                + ("\n…" if len(skipped) > 10 else ""),
            )
        self._offer_open_folder(Path(out_dir), n_files)

    def _on_export_failed(self, message: str) -> None:
        logger.error("Album export failed: %s", message)
        self.statusBar().showMessage("Album export failed.")
        QMessageBox.critical(self, "Export failed", message)

    def _on_export_canceled(self) -> None:
        self.statusBar().showMessage("Export cancelled.")

    def _finish_export(self) -> None:
        """Close the progress dialog and release the worker after any outcome."""
        if self._export_dialog is not None:
            self._export_dialog.close()
            self._export_dialog = None
        if self._export_worker is not None:
            self._export_worker.wait()
            self._export_worker = None
        self._update_actions_enabled()

    def _offer_open_folder(self, out_dir: Path, n_files: int) -> None:
        """Tell the user the export succeeded and offer to open the folder."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Export Album")
        box.setText(f"Exported {n_files} file(s) to:\n{out_dir}")
        open_btn = box.addButton("Open Folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))

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

    # ----------------------------------------------------------------- #
    # Guided wizard
    # ----------------------------------------------------------------- #
    def _on_wizard_action(self, step_key: str) -> None:
        """Run the action for the wizard's current step (the big CTA button)."""
        if step_key == "open":
            # Opens a folder and analyzes it in one step.
            self._on_open_folder()
        elif step_key == "people":
            self._on_label_people()
        elif step_key == "album":
            self._on_generate_album()
        elif step_key == "export":
            self._on_export_album()

    def _refresh_wizard(self) -> None:
        """Sync the wizard bar with the current step, completion, and busy state."""
        if not hasattr(self, "wizard"):
            return
        busy = self._analysis.is_running() or self._export_running()
        self.wizard.update_view(self._wizard_step, self._wizard_done, busy=busy)

    def _set_analyzing(self, analyzing: bool) -> None:
        self.action_open.setEnabled(not analyzing)
        self.action_cancel.setEnabled(analyzing)
        if analyzing:
            self.action_analyze.setEnabled(False)
            self.action_refresh.setEnabled(False)
            self.action_album.setEnabled(False)
            self.action_label.setEnabled(False)
            self.action_export.setEnabled(False)
        else:
            self._update_actions_enabled()

    def _export_running(self) -> bool:
        return self._export_worker is not None and self._export_worker.isRunning()

    def _update_actions_enabled(self) -> None:
        has_folder = self._folder is not None
        busy = self._analysis.is_running() or self._export_running()
        self.action_open.setEnabled(not busy)
        self.action_analyze.setEnabled(has_folder and not busy)
        self.action_refresh.setEnabled(has_folder and not busy)
        self.action_cancel.setEnabled(self._analysis.is_running())
        self.action_album.setEnabled(has_folder and not busy)
        # People-first: labelling is available once the folder is analyzed
        # (the clusters are discovered on demand), not only after an album build.
        self.action_label.setEnabled(self._analyzed and not busy)
        self.action_export.setEnabled(self._album_project is not None and not busy)
        self._refresh_wizard()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self._analysis.is_running():
            self._analysis.cancel()
        if self._export_running():
            # Ask the export to stop and wait for the thread to unwind so we
            # don't destroy the worker while it is still running.
            self._export_worker.cancel()
            self._export_worker.wait()
        self.center.shutdown()
        super().closeEvent(event)
