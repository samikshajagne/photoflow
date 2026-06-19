"""
Lazy thumbnail loader.

Decodes images to small thumbnails on a bounded ``QThreadPool`` so the UI
never blocks, even for thousands of photos. Workers decode to ``QImage``
(thread-safe) at a *scaled* size via ``QImageReader.setScaledSize`` -- a 24MP
JPEG is read straight to a ~200px thumbnail, so full-resolution pixels never
enter memory. The GUI thread converts the ``QImage`` to a ``QPixmap`` and
caches it (see :class:`~ui_qt.models.photo_list_model.PhotoListModel`).

Requests are de-duplicated so scrolling back and forth doesn't queue the same
decode twice. ``QPixmap`` is never touched off the GUI thread.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader

from utils.logger import get_logger

logger = get_logger("ui_qt.thumbnails")


class _TaskSignals(QObject):
    done = pyqtSignal(str, QImage)


class _ThumbnailTask(QRunnable):
    """Decodes one image to a scaled QImage on a pool thread."""

    def __init__(self, path: str, edge: int, signals: _TaskSignals) -> None:
        super().__init__()
        self._path = path
        self._edge = edge
        self._signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable entry point
        image = self._decode()
        self._signals.done.emit(self._path, image)

    def _decode(self) -> QImage:
        reader = QImageReader(self._path)
        reader.setAutoTransform(True)  # honor EXIF orientation
        size = reader.size()
        if size.isValid() and (size.width() > self._edge or size.height() > self._edge):
            scaled = size.scaled(self._edge, self._edge, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(scaled)
        image = reader.read()
        if image.isNull():
            logger.debug("Thumbnail decode failed: %s (%s)", self._path, reader.errorString())
        return image


class ThumbnailLoader(QObject):
    """Schedules thumbnail decodes and reports completions via a signal."""

    thumbnailReady = pyqtSignal(str, QImage)  # (path, image)

    def __init__(self, edge: int = 192, max_threads: int = 4, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._edge = edge
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_threads))
        self._inflight: set[str] = set()

    @property
    def edge(self) -> int:
        return self._edge

    def request(self, path: str) -> None:
        """Queue a decode for ``path`` (ignored if one is already in flight)."""
        if path in self._inflight:
            return
        self._inflight.add(path)
        signals = _TaskSignals()
        signals.done.connect(self._on_done)
        self._pool.start(_ThumbnailTask(path, self._edge, signals))

    def _on_done(self, path: str, image: QImage) -> None:
        self._inflight.discard(path)
        self.thumbnailReady.emit(path, image)

    def shutdown(self) -> None:
        """Wait for queued decodes to finish (call on close)."""
        self._pool.clear()
        self._pool.waitForDone(2000)
