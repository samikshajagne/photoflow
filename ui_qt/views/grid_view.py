"""
Thumbnail grid (virtualized).

A ``QListView`` in icon mode backed by
:class:`~ui_qt.models.photo_list_model.PhotoListModel`. The view virtualizes
rendering (only visible cells are realized) and the model loads thumbnails
lazily, so the grid stays responsive across very large folders.

Signals:
    photoSelected(object)   current PhotoEntry changed (single click / arrows)
    photoActivated(object)  PhotoEntry double-clicked (open in loupe)
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QListView

from ui_qt.models.photo_index import PhotoEntry
from ui_qt.models.photo_list_model import PhotoListModel
from ui_qt.workers.thumbnail_loader import ThumbnailLoader


class ThumbnailGrid(QListView):
    photoSelected = pyqtSignal(object)
    photoActivated = pyqtSignal(object)

    def __init__(self, edge: int = 192, parent=None) -> None:
        super().__init__(parent)
        self._edge = edge
        self._loader = ThumbnailLoader(edge=edge)
        self._model = PhotoListModel(self._loader, self)
        self.setModel(self._model)

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(edge, edge))
        self.setGridSize(QSize(edge + 24, edge + 40))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)   # big perf win for large models
        self.setSpacing(8)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)

        self.doubleClicked.connect(self._on_double_clicked)

    def set_entries(self, entries: list[PhotoEntry]) -> None:
        self._model.set_entries(entries)
        self.clearSelection()
        if self.selectionModel() is not None:
            self.selectionModel().currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, current, _previous) -> None:
        entry = self._model.entry_at(current)
        if entry is not None:
            self.photoSelected.emit(entry)

    def _on_double_clicked(self, index) -> None:
        entry = self._model.entry_at(index)
        if entry is not None:
            self.photoActivated.emit(entry)

    def shutdown(self) -> None:
        self._loader.shutdown()
