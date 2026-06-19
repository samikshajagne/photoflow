"""
Center area: hosts the thumbnail grid and the loupe preview.

A ``QStackedWidget`` with three pages -- a placeholder, the thumbnail grid,
and the loupe. Selecting a thumbnail re-emits :pyattr:`photoSelected`;
double-clicking opens the loupe; Esc in the loupe returns to the grid.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QStackedWidget, QWidget

from ui_qt.models.photo_index import PhotoEntry
from ui_qt.views.grid_view import ThumbnailGrid
from ui_qt.views.loupe_view import LoupeView

PAGE_PLACEHOLDER = 0
PAGE_GRID = 1
PAGE_LOUPE = 2


class CenterView(QStackedWidget):
    photoSelected = pyqtSignal(object)  # PhotoEntry

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CenterView")

        # Entries currently browsable in the loupe, plus the active index.
        self._entries: list[PhotoEntry] = []
        self._loupe_index: int = -1

        self._placeholder = QLabel("Open a folder to browse your photos")
        self._placeholder.setObjectName("CenterPlaceholder")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self.addWidget(self._placeholder)        # PAGE_PLACEHOLDER

        self.grid = ThumbnailGrid()
        self.addWidget(self.grid)                # PAGE_GRID

        self.loupe = LoupeView()
        self.addWidget(self.loupe)               # PAGE_LOUPE

        self.grid.photoSelected.connect(self.photoSelected)
        self.grid.photoActivated.connect(self._open_loupe)
        self.loupe.backRequested.connect(self.show_grid)
        self.loupe.nextRequested.connect(self._show_next)
        self.loupe.prevRequested.connect(self._show_prev)

        self.setCurrentIndex(PAGE_PLACEHOLDER)

    def show_message(self, message: str) -> None:
        self._placeholder.setText(message)
        self.setCurrentIndex(PAGE_PLACEHOLDER)

    def set_entries(self, entries: list[PhotoEntry]) -> None:
        """Show the grid populated with ``entries``."""
        self._entries = list(entries)
        self._loupe_index = -1
        self.grid.set_entries(entries)
        self.setCurrentIndex(PAGE_GRID)

    def show_grid(self) -> None:
        self.setCurrentIndex(PAGE_GRID)

    def _open_loupe(self, entry: PhotoEntry) -> None:
        index = self._index_of(entry)
        if index < 0:
            # Entry isn't in the current set; show it standalone.
            self._entries = [entry]
            index = 0
        self._show_at(index)

    def _index_of(self, entry: PhotoEntry) -> int:
        for i, candidate in enumerate(self._entries):
            if candidate.source_path == entry.source_path:
                return i
        return -1

    def _show_at(self, index: int) -> None:
        if not self._entries:
            return
        index = max(0, min(index, len(self._entries) - 1))
        self._loupe_index = index
        entry = self._entries[index]
        self.loupe.show_image(entry.source_path)
        self.loupe.set_nav_state(index, len(self._entries))
        self.setCurrentIndex(PAGE_LOUPE)
        self.loupe.setFocus()

    def _show_next(self) -> None:
        if self._loupe_index < len(self._entries) - 1:
            self._show_at(self._loupe_index + 1)

    def _show_prev(self) -> None:
        if self._loupe_index > 0:
            self._show_at(self._loupe_index - 1)

    def shutdown(self) -> None:
        self.grid.shutdown()
        self.loupe.shutdown()
