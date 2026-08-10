"""
PhotoFlow main application window.

Three mutually-exclusive modes, tracked in ``self._mode``:

- ``"chooser"``: the startup landing page
  (:class:`~ui_qt.views.mode_chooser_view.ModeChooserView`) filling the whole
  window as its central widget, no toolbar. This is what ``ui_qt.main``
  actually launches with (``MainWindow(mode="chooser")``) -- picking a card
  calls :meth:`_enter_mode` to rebuild the *same* window in place, so the
  chooser is part of the app's own UI rather than a separate popup dialog.
- ``"album"`` (the constructor default, so plain ``MainWindow()`` -- e.g. in
  tests -- still gets the full UI immediately): three-panel layout
  (sidebar | center | metadata) in a splitter, the guided wizard bar, and the
  Open & Analyze / Re-analyze / Cancel / Refresh / Generate Album / ... toolbar.
- ``"passport"``: just the standalone Passport Photos tool
  (:class:`~ui_qt.views.passport_photo_view.PassportPhotoView`) filling the
  window, with no album toolbar, wizard, sidebar, or metadata panel.
- ``"collage"``: just the standalone Collage Maker
  (:class:`~ui_qt.views.collage_view.CollageView`), likewise filling the
  window with none of the album chrome.

Each mode only builds the widgets/actions it needs, so e.g. a "passport"
window never references album-only state and vice versa.

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
    QStackedWidget,
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
from core.album.pacing import PACING_EDITORIAL
from core.scanner import ImageScanner, ScanError
from utils.config import ConfigError, load_config
from utils.logger import get_logger
from ui_qt.models.photo_index import PhotoEntry, PhotoIndex
from ui_qt.views.center_view import CenterView
from ui_qt.views.metadata_panel import MetadataPanel
from ui_qt.views.mode_chooser_view import ModeChooserView
from ui_qt.views.collage_view import CollageView
from ui_qt.views.passport_photo_view import PassportPhotoView
from ui_qt.views.preview_view import PreviewView
from ui_qt.views.sidebar import CategorySidebar
from ui_qt.views.wizard_bar import WizardBar
from ui_qt.views.api_settings_dialog import ApiSettingsDialog
from ui_qt.workers.analysis_worker import AnalysisController
from ui_qt.workers.preview_worker import PreviewWorker

logger = get_logger("ui_qt.main_window")


class MainWindow(QMainWindow):
    """Top-level window hosting the toolbar and three-panel layout."""

    def __init__(self, parent: Optional[QWidget] = None, mode: str = "album") -> None:
        super().__init__(parent)
        self._mode = mode  # "chooser" | "album" | "passport" -- see the module docstring
        self._set_title_for_mode(mode)
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
        # Narrative rhythm: whether spread density varies or stays uniform.
        self._album_pacing = PACING_EDITORIAL
        # Cover text (couple names + date) printed on the album cover.
        self._cover_title = ""
        self._cover_date = ""
        # Target number of album spreads (0 = auto ~20-30).
        self._target_pages = 0
        # Phase 3/4 layout feature flags (smart ordering on by default, rest opt-in).
        self._layout_options = {
            "smart_slot_ordering": True,
            "flexible_layout": False,
            "use_cutouts": False,
            "designed_cover": False,
            "theme_backgrounds": False,
        }
        # Background preview renderer (None when idle).
        self._preview_worker = None
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
        self._show_initial_status()

    _MODE_TITLES = {
        "passport": "PhotoFlow - Passport Photos",
        "collage": "PhotoFlow - Collage Maker",
    }

    def _set_title_for_mode(self, mode: str) -> None:
        self.setWindowTitle(self._MODE_TITLES.get(mode, "PhotoFlow"))

    def _show_initial_status(self) -> None:
        if self._mode == "album":
            self.statusBar().showMessage("Open a folder to begin.")
            self._update_actions_enabled()
            self._refresh_wizard()
        elif self._mode == "passport":
            self.statusBar().showMessage("Choose a photo to begin.")
        elif self._mode == "collage":
            self.statusBar().showMessage("Add photos to start your collage.")
        else:  # "chooser"
            self.statusBar().showMessage("Choose how you'd like to use PhotoFlow.")

    # ----------------------------------------------------------------- #
    # Switching from the startup chooser into a real mode
    # ----------------------------------------------------------------- #
    def _on_back_to_menu(self) -> None:
        """
        Return to the tool menu, confirming first if work is in progress.

        Switching tools throws away the current tool's state (the album's
        analysis, a half-built collage), so anything long-running gets a
        confirmation rather than silently discarding a studio's work.
        """
        busy = self._mode == "album" and (
            self._analysis.is_running() or self._export_running()
        )
        if busy:
            reply = QMessageBox.question(
                self,
                "Switch tools?",
                "Work is still in progress. Going back will stop it.\n\n"
                "Switch tools anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            if self._analysis.is_running():
                self._analysis.cancel()
        self._enter_mode("chooser")

    def _clear_mode_state(self) -> None:
        """
        Drop references to the outgoing mode's widgets before rebuilding.

        Qt deletes the old central widget and its children at the C++ level, so
        attributes like ``self.passport`` would otherwise point at deleted
        objects -- touching one raises "wrapped C/C++ object has been deleted".
        Album state is reset too, so returning to a tool starts clean rather
        than half-remembering the previous folder.
        """
        for attr in (
            "chooser", "passport", "collage",
            "sidebar", "center", "preview", "metadata", "center_stack", "wizard",
        ):
            if hasattr(self, attr):
                delattr(self, attr)
        # Album-only actions belong to the toolbar we just removed.
        for attr in list(vars(self)):
            if attr.startswith("action_"):
                delattr(self, attr)

        if self._mode == "album":
            self._folder = None
            self._result = None
            self._index = None
            self._analyzed = False
            self._album_project = None
            self._album_dir = None
            self._album_entries = {}
            self._people_prepared = False
            self._wizard_step = "open"
            self._wizard_done = set()

    def _enter_mode(self, mode: str) -> None:
        """
        Rebuild this window in place for ``mode`` ("chooser", "album",
        "passport" or "collage"). Called when the user picks a card on
        :class:`~ui_qt.views.mode_chooser_view.ModeChooserView`, and when they
        use the toolbar's "All Tools" action to come back, so switching tools
        feels like navigating within the app rather than restarting it.
        """
        # Shut the album's background thumbnail machinery down before its
        # widgets are destroyed (closeEvent does the same on exit).
        if self._mode == "album" and hasattr(self, "center"):
            try:
                self.center.shutdown()
            except Exception as exc:  # noqa: BLE001 - never block navigation
                logger.debug("Center shutdown while switching modes: %s", exc)

        self._clear_mode_state()
        self._mode = mode
        self._set_title_for_mode(mode)
        # QMainWindow doesn't auto-remove toolbars when you add new ones (it
        # allows several at once), so the chooser's lack-of-toolbar state and
        # any toolbar from a previous mode must be cleared explicitly.
        for tb in self.findChildren(QToolBar):
            self.removeToolBar(tb)
            # setParent(None) detaches it *now*; deleteLater() alone defers
            # until the event loop next runs, so rapid mode switching would
            # leave orphaned toolbars parented to the window in the meantime.
            tb.setParent(None)
            tb.deleteLater()
        self._build_toolbar()
        self._build_central()  # replaces the central widget; Qt deletes the old one
        self._show_initial_status()

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    def _add_back_action(self, toolbar: QToolBar) -> None:
        """
        Add the "All Tools" action that returns to the startup chooser.

        Every mode gets this: without it, picking a tool was a one-way trip and
        the only way to switch was restarting the application.
        """
        self.action_back = toolbar.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack), "All Tools"
        )
        self.action_back.setToolTip("Back to the tool menu (Alt+Left)")
        self.action_back.setShortcut("Alt+Left")
        self.action_back.triggered.connect(self._on_back_to_menu)
        toolbar.addSeparator()

    def _build_toolbar(self) -> None:
        if self._mode == "chooser":
            return  # the chooser is the menu; nothing to go back to

        if self._mode != "album":
            # The standalone tools have no album chrome, but they still need a
            # way back to the menu, so they get a toolbar containing just that.
            tool_bar = QToolBar("Navigation")
            tool_bar.setMovable(False)
            tool_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.addToolBar(tool_bar)
            self._add_back_action(tool_bar)
            return

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        self._add_back_action(toolbar)

        style = self.style()
        self.action_open = toolbar.addAction(
            # "&&" because Qt reads a single & in action text as a keyboard
            # mnemonic, which rendered this as "Open  Analyze".
            style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open && Analyze"
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

        self.action_preview = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView), "Preview"
        )
        self.action_preview.triggered.connect(self._on_preview)
        self.action_preview.setEnabled(False)

        self.action_change_size = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton), "Change Size"
        )
        self.action_change_size.triggered.connect(self._on_change_size)
        self.action_change_size.setEnabled(False)

        self.action_export = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export Album"
        )
        self.action_export.triggered.connect(self._on_export_album)
        self.action_export.setEnabled(False)

        toolbar.addSeparator()
        self.action_api_settings = toolbar.addAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView), "API Settings"
        )
        self.action_api_settings.triggered.connect(self._on_api_settings)
        self.action_api_settings.setToolTip(
            "Configure your OpenAI API key for smart event classification"
        )
    def _build_central(self) -> None:
        if self._mode == "chooser":
            # Startup landing page: fills the whole window; picking a card
            # rebuilds this same window via _enter_mode, not a popup dialog.
            self.chooser = ModeChooserView()
            self.chooser.modeChosen.connect(self._enter_mode)
            self.setCentralWidget(self.chooser)
            return

        if self._mode == "passport":
            # Standalone tool: it fills the whole window, no sidebar/metadata/
            # wizard -- those only make sense for the album workflow.
            self.passport = PassportPhotoView()
            self.setCentralWidget(self.passport)
            return

        if self._mode == "collage":
            self.collage = CollageView()
            self.setCentralWidget(self.collage)
            return

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.sidebar = CategorySidebar()
        self.center = CenterView()
        self.preview = PreviewView()
        self.metadata = MetadataPanel()

        # Center area toggles between the analysis grid and the album preview.
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.center)   # index 0: thumbnail grid
        self.center_stack.addWidget(self.preview)  # index 1: rendered preview

        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.center_stack)
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
        self.center_stack.setCurrentWidget(self.center)  # back to the grid view
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

    def _on_api_settings(self) -> None:
        """Open the OpenAI API key configuration dialog."""
        dlg = ApiSettingsDialog(parent=self)
        dlg.exec()

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
        self.center_stack.setCurrentWidget(self.center)  # show the analysis grid

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

        dialog = AlbumSettingsDialog(
            self,
            spec=self._album_spec,
            density=self._album_density,
            pacing=self._album_pacing,
            cover_title=self._cover_title,
            cover_date=self._cover_date,
            target_pages=self._target_pages,
            layout_options=self._layout_options,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._album_spec = dialog.album_spec()
        self._album_density = dialog.selected_density()
        self._album_pacing = dialog.selected_pacing()
        self._cover_title = dialog.cover_title()
        self._cover_date = dialog.cover_date()
        self._target_pages = dialog.target_pages()
        self._layout_options = dialog.layout_options()

        if not self._generate_album("Building your album… this can take a while."):
            return
        self._present_album()
        self._update_actions_enabled()

    def _generate_album(self, status: str) -> bool:
        """Build the album on the current folder in the background. True on success."""
        from ui_qt.workers.album_workers import GenerateWorker

        worker = GenerateWorker(
            str(self._folder),
            self._album_spec,
            self._album_density,
            pacing=self._album_pacing,
            cover_title=self._cover_title,
            cover_date=self._cover_date,
            target_pages=self._target_pages,
            layout_options=self._layout_options,
        )
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
        ``failed(str)`` signals. If the worker also exposes a ``progress(str)``
        signal, its messages are shown live in the dialog label.
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

        def _progress(message: str) -> None:
            dialog.setLabelText(message)
            self.statusBar().showMessage(message)

        worker.succeeded.connect(_ok)
        worker.failed.connect(_err)
        # Connect progress if the worker supports it (GenerateWorker / PreparePeopleWorker do).
        if hasattr(worker, "progress"):
            worker.progress.connect(_progress)
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

        # Album built -> preview it before exporting.
        self._wizard_done.update({"open", "people", "album"})
        self._wizard_step = "preview"
        self._refresh_wizard()

    # ----------------------------------------------------------------- #
    # Album preview + size
    # ----------------------------------------------------------------- #
    def _current_spec(self):
        """The album spec to preview/export with (chosen, or a sensible default)."""
        if self._album_spec is not None:
            return self._album_spec
        from core.album.layout import AlbumSpec

        return AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)

    def _on_preview(self) -> None:
        if self._album_project is None or self._analysis.is_running():
            return
        self.center_stack.setCurrentWidget(self.preview)
        self._start_preview()
        self._wizard_done.update({"open", "people", "album"})
        self._wizard_step = "preview"
        self._update_actions_enabled()

    def _start_preview(self) -> None:
        """(Re)render the album spreads into the preview panel in the background."""
        if self._album_project is None:
            return
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.cancel()
            self._preview_worker.wait()
        self.preview.show_message("Rendering preview…")
        worker = PreviewWorker(self._album_project, self._current_spec(), apply_edits=False)
        worker.countKnown.connect(self.preview.begin)
        worker.spreadReady.connect(self.preview.set_spread)
        worker.finishedAll.connect(self.preview.finish)
        worker.failed.connect(
            lambda msg: self.preview.show_message(f"Preview failed:\n{msg}")
        )
        self._preview_worker = worker
        worker.start()

    def _on_change_size(self) -> None:
        if self._album_project is None or self._analysis.is_running():
            return
        from ui_qt.views.album_settings_dialog import AlbumSettingsDialog

        dialog = AlbumSettingsDialog(
            self,
            spec=self._current_spec(),
            density=self._album_density,
            pacing=self._album_pacing,
            cover_title=self._cover_title,
            cover_date=self._cover_date,
            target_pages=self._target_pages,
            layout_options=self._layout_options,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._album_spec = dialog.album_spec()
        self._album_density = dialog.selected_density()
        self._album_pacing = dialog.selected_pacing()
        self._cover_title = dialog.cover_title()
        self._cover_date = dialog.cover_date()
        self._target_pages = dialog.target_pages()
        self._layout_options = dialog.layout_options()
        # Re-lay the album at the new size so the export matches, then re-preview.
        self._relayout_album()
        self.center_stack.setCurrentWidget(self.preview)
        self._start_preview()

    def _relayout_album(self) -> None:
        """Re-run layout selection at the current size so export/preview match."""
        project = self._album_project
        if project is None or self._album_spec is None:
            return
        try:
            import dataclasses

            from core.album.layout_select import LayoutSelector

            selector = LayoutSelector(
                density=self._album_density,
                pacing=self._album_pacing,
                target_pages=self._target_pages or 0,
            )
            project.spreads = selector.select(project, self._album_spec)
            meta = dict(getattr(project.meta, "album_spec", {}) or {})
            meta.update(dataclasses.asdict(self._album_spec))
            if self._cover_title:
                meta["cover_title"] = self._cover_title
            if self._cover_date:
                meta["cover_date"] = self._cover_date
            project.meta.album_spec = meta
        except Exception as exc:  # noqa: BLE001 - never let a re-layout crash the UI
            logger.warning("Re-layout at new size failed: %s", exc)

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
        elif step_key == "preview":
            self._on_preview()
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
            self.action_preview.setEnabled(False)
            self.action_change_size.setEnabled(False)
            self.action_export.setEnabled(False)
        else:
            self._update_actions_enabled()

    def _export_running(self) -> bool:
        return self._export_worker is not None and self._export_worker.isRunning()

    def _update_actions_enabled(self) -> None:
        if self._mode != "album":
            return
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
        has_album = self._album_project is not None
        self.action_preview.setEnabled(has_album and not busy)
        self.action_change_size.setEnabled(has_album and not busy)
        self.action_export.setEnabled(has_album and not busy)
        self._refresh_wizard()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self._analysis.is_running():
            self._analysis.cancel()
        if self._export_running():
            # Ask the export to stop and wait for the thread to unwind so we
            # don't destroy the worker while it is still running.
            self._export_worker.cancel()
            self._export_worker.wait()
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.cancel()
            self._preview_worker.wait()
        if self._mode == "album":
            self.center.shutdown()
        super().closeEvent(event)
