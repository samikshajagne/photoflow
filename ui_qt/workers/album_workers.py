"""
Background workers for album generation and export.

Both operations are heavy (full analysis/layout for generation; rendering
large spreads for export). Running them on the GUI thread freezes the window,
so each runs in a :class:`QThread` and reports back via signals. The heavy work
is cv2/PIL/numpy/psd-tools, which release the GIL, so a thread keeps the UI
responsive without pickling the album project across processes.

Export additionally supports progress and cooperative cancellation.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.album.pacing import PACING_EDITORIAL
from utils.logger import get_logger

logger = get_logger("ui_qt.album_workers")


class GenerateWorker(QThread):
    """Runs the album orchestrator off the GUI thread."""

    succeeded = pyqtSignal(object)  # AlbumProject
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)  # current stage label

    def __init__(
        self,
        folder: str,
        album_spec: object = None,
        density: str = "balanced",
        cover_title: str = "",
        cover_date: str = "",
        target_pages: int = 0,
        layout_options: Optional[dict] = None,
        parent: Optional[object] = None,
        pacing: str = PACING_EDITORIAL,
    ) -> None:
        super().__init__(parent)
        self._folder = str(folder)
        self._album_spec = album_spec
        self._density = density
        self._pacing = pacing
        self._cover_title = cover_title
        self._cover_date = cover_date
        self._target_pages = int(target_pages or 0)
        self._layout_options = dict(layout_options or {})

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            from core.album.layout_select import LayoutSelector
            from core.album.orchestrator import AlbumOrchestrator

            kwargs: dict = {
                "layout_selector": LayoutSelector(
                    density=self._density,
                    pacing=self._pacing,
                    target_pages=self._target_pages,
                ),
                "cover_title": self._cover_title,
                "cover_date": self._cover_date,
                "progress_cb": lambda msg: self.progress.emit(msg),
            }
            # Phase 3/4 layout feature flags (smart ordering, cutouts, flexible
            # layouts, designed cover, themed backgrounds) chosen in the dialog.
            for key in (
                "smart_slot_ordering",
                "use_cutouts",
                "flexible_layout",
                "designed_cover",
                "theme_backgrounds",
            ):
                if key in self._layout_options:
                    kwargs[key] = bool(self._layout_options[key])
            # Pass the chosen template theme ("classic" or "natural") as a string.
            if "theme" in self._layout_options:
                kwargs["theme"] = str(self._layout_options["theme"])
            if self._album_spec is not None:
                kwargs["album_spec"] = self._album_spec
            project = AlbumOrchestrator(**kwargs).generate(self._folder)
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Album generation failed.")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(project)


class PreparePeopleWorker(QThread):
    """
    Runs the orchestrator's people pass off the GUI thread.

    Discovers person clusters (analyze -> detect -> embed -> cluster) without
    building the album layout, so the photographer can label people first. All
    heavy work is cached, so the later album build recomputes nothing.
    """

    succeeded = pyqtSignal(object)  # AlbumProject (photos + clusters, no layout)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)  # current stage label

    def __init__(self, folder: str, parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self._folder = str(folder)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            from core.album.orchestrator import AlbumOrchestrator

            project = AlbumOrchestrator(
                progress_cb=lambda msg: self.progress.emit(msg)
            ).prepare_people(self._folder)
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Preparing people failed.")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(project)


class ExportWorker(QThread):
    """Renders the album to the requested formats, with progress + cancel."""

    progress = pyqtSignal(int, int, str)  # done, total, message
    succeeded = pyqtSignal(object)        # (results dict, skipped list, out_dir)
    failed = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(
        self,
        project: object,
        formats: Iterable[str],
        out_dir: Path,
        apply_edits: bool,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._formats = list(formats)
        self._out_dir = Path(out_dir)
        self._apply_edits = bool(apply_edits)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Request cancellation; the render stops at the next spread boundary."""
        self._cancel.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        from core.album.raster import ExportCancelled, export_renders

        skipped: list = []
        try:
            results = export_renders(
                self._out_dir,
                self._project,
                self._formats,
                apply_edits=self._apply_edits,
                skipped=skipped,
                progress_cb=lambda done, total, msg: self.progress.emit(done, total, msg),
                cancel_event=self._cancel,
            )
        except ExportCancelled:
            self.canceled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Album export failed.")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit((results, skipped, self._out_dir))
