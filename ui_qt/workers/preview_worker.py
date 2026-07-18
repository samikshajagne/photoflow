"""
Background renderer for the in-window album preview.

Renders each spread at a low preview resolution (via the *same* renderer the
export uses, so the preview matches the output) and streams the images back to
the GUI as they finish, keeping the window responsive.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from utils.logger import get_logger

logger = get_logger("ui_qt.preview")


def pil_to_qimage(img: Any) -> QImage:
    """Convert a PIL RGB image to a standalone QImage (owns its buffer)."""
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()  # detach from the temporary bytes buffer


class PreviewWorker(QThread):
    """Renders the album's spreads to preview-size images, one signal each."""

    countKnown = pyqtSignal(int)          # total spread count (before any render)
    spreadReady = pyqtSignal(int, object)  # (index, QImage)
    finishedAll = pyqtSignal(int)          # count rendered
    failed = pyqtSignal(str)

    def __init__(self, project: Any, spec: Any, apply_edits: bool = False,
                 parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self._project = project
        self._spec = spec
        self._apply = bool(apply_edits)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            from core.album.raster import preview_spec, render_spread_template

            spreads = list(getattr(self._project, "spreads", []) or [])
            self.countKnown.emit(len(spreads))
            pv = preview_spec(self._spec)
            for i, spread in enumerate(spreads):
                if self._cancel:
                    return
                img = render_spread_template(
                    self._project, spread, apply_edits=self._apply, spec=pv
                )
                self.spreadReady.emit(i, pil_to_qimage(img))
            self.finishedAll.emit(len(spreads))
        except Exception as exc:  # noqa: BLE001 - report to the UI
            logger.exception("Preview render failed.")
            self.failed.emit(str(exc))
