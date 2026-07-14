"""
Left sidebar navigation.

Two modes over one list:
- **Categories** (after analysis): the four PhotoFlow buckets with live counts,
  keyed by ``core.organizer`` folder names. Emits :pyattr:`categorySelected`.
- **Sections** (after album generation): the album's story sections (Cover,
  Couple, Bride, ...), each with a photo count. Emits :pyattr:`sectionSelected`.

The widget holds no logic beyond display and selection.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)

# Display order and labels for the categories.
CATEGORY_ORDER: tuple[str, ...] = (
    FOLDER_BEST_SHOTS,
    FOLDER_DUPLICATES,
    FOLDER_BLURRY,
    FOLDER_REVIEW,
)
_LABELS = {
    FOLDER_BEST_SHOTS: "Best Shots",
    FOLDER_DUPLICATES: "Duplicates",
    FOLDER_BLURRY: "Blurry",
    FOLDER_REVIEW: "Review",
}

_NO_COUNT = "—"  # em dash, shown before analysis
_USER_ROLE = int(Qt.ItemDataRole.UserRole)


class CategorySidebar(QWidget):
    """Navigation list with a categories mode and an album-sections mode."""

    categorySelected = pyqtSignal(str)  # category key
    sectionSelected = pyqtSignal(str)   # album section name

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._counts: dict[str, Optional[int]] = {k: None for k in CATEGORY_ORDER}
        self._mode = "categories"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._heading = QLabel("LIBRARY")
        self._heading.setObjectName("PanelHeading")
        layout.addWidget(self._heading)

        self._list = QListWidget()
        self._list.setObjectName("CategoryList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._populate_categories()
        self._list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._list, stretch=1)

    # ----------------------------------------------------------------- #
    # Categories mode
    # ----------------------------------------------------------------- #
    def _render(self, key: str) -> str:
        count = self._counts.get(key)
        shown = _NO_COUNT if count is None else str(count)
        return f"{_LABELS[key]} {shown}"

    def _populate_categories(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for key in CATEGORY_ORDER:
            item = QListWidgetItem(self._render(key))
            item.setData(_USER_ROLE, key)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._mode = "categories"
        self._heading.setText("LIBRARY")

    def set_counts(self, counts: Optional[dict[str, int]]) -> None:
        """Update per-category counts (``None`` resets to placeholders)."""
        if self._mode != "categories":
            self._populate_categories()
        for i, key in enumerate(CATEGORY_ORDER):
            self._counts[key] = None if counts is None else int(counts.get(key, 0))
            self._list.item(i).setText(self._render(key))

    # ----------------------------------------------------------------- #
    # Sections mode (album)
    # ----------------------------------------------------------------- #
    def set_sections(self, sections: list[tuple[str, int]]) -> None:
        """Show album sections as ``(name, photo_count)`` pairs, in order."""
        self._list.blockSignals(True)
        self._list.clear()
        for name, count in sections:
            item = QListWidgetItem(f"{name}  {count}")
            item.setData(_USER_ROLE, name)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._mode = "sections"
        self._heading.setText("ALBUM")

    # ----------------------------------------------------------------- #
    # Selection
    # ----------------------------------------------------------------- #
    def current_category(self) -> Optional[str]:
        item = self._list.currentItem()
        return None if item is None else item.data(_USER_ROLE)

    def _select_by_data(self, value: str) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(_USER_ROLE) == value:
                self._list.setCurrentRow(i)
                return

    def select_category(self, key: str) -> None:
        self._select_by_data(key)

    def select_section(self, name: str) -> None:
        self._select_by_data(name)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        value = current.data(_USER_ROLE)
        if self._mode == "sections":
            self.sectionSelected.emit(value)
        else:
            self.categorySelected.emit(value)
