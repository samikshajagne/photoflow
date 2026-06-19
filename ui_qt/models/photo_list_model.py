"""
List model backing the thumbnail grid.

A ``QAbstractListModel`` of :class:`~ui_qt.models.photo_index.PhotoEntry`.
Qt's model/view framework only realizes visible rows, so this scales to
thousands of items. Thumbnails are produced lazily: the first time a row's
decoration is requested, the model asks the
:class:`~ui_qt.workers.thumbnail_loader.ThumbnailLoader` for it and returns a
placeholder; when the decode finishes the model converts the ``QImage`` to a
``QPixmap`` (on the GUI thread), stores it in ``QPixmapCache``, and emits
``dataChanged`` for that row.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap, QPixmapCache

from ui_qt.models.photo_index import PhotoEntry, normalize_path
from ui_qt.workers.thumbnail_loader import ThumbnailLoader

ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class PhotoListModel(QAbstractListModel):
    """Lazy, cached thumbnail model over a list of PhotoEntry."""

    def __init__(self, loader: ThumbnailLoader, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[PhotoEntry] = []
        self._row_by_path: dict[str, int] = {}
        self._loader = loader
        self._loader.thumbnailReady.connect(self._on_thumbnail_ready)

        self._edge = loader.edge
        QPixmapCache.setCacheLimit(256 * 1024)  # 256 MB
        self._placeholder = self._solid(QColor(45, 46, 51))
        self._broken = self._solid(QColor(70, 50, 50))

    def _solid(self, color: QColor) -> QPixmap:
        pm = QPixmap(self._edge, self._edge)
        pm.fill(color)
        return pm

    # ----------------------------------------------------------------- #
    # Data
    # ----------------------------------------------------------------- #
    def set_entries(self, entries: list[PhotoEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._row_by_path = {
            normalize_path(e.source_path): i for i, e in enumerate(self._entries)
        }
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]

        if role == int(Qt.ItemDataRole.DisplayRole):
            return entry.name
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return entry.source_path
        if role == ENTRY_ROLE:
            return entry
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._thumbnail_for(entry)
        return None

    def entry_at(self, index: QModelIndex) -> Optional[PhotoEntry]:
        if index.isValid() and 0 <= index.row() < len(self._entries):
            return self._entries[index.row()]
        return None

    # ----------------------------------------------------------------- #
    # Thumbnails
    # ----------------------------------------------------------------- #
    def _cache_key(self, path: str) -> str:
        return f"{self._edge}:{path}"

    def _thumbnail_for(self, entry: PhotoEntry) -> QPixmap:
        key = self._cache_key(entry.source_path)
        cached = QPixmapCache.find(key)
        if cached is not None:
            return cached
        # Not cached yet: kick off a lazy decode and show a placeholder.
        self._loader.request(entry.source_path)
        return self._placeholder

    def _on_thumbnail_ready(self, path: str, image: QImage) -> None:
        pixmap = self._broken if image.isNull() else QPixmap.fromImage(image)
        QPixmapCache.insert(self._cache_key(path), pixmap)
        row = self._row_by_path.get(normalize_path(path))
        if row is not None:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [int(Qt.ItemDataRole.DecorationRole)])
