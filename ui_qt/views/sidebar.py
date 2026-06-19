"""
Left sidebar: the four PhotoFlow categories with live counts.

Category keys are taken from ``core.organizer`` so the labels always match
the pipeline's output folders. This widget holds no logic beyond display and
selection; it emits :pyattr:`categorySelected` when the user picks a category.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
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


class CategorySidebar(QWidget):
    """Navigation list of the four categories, each with a count badge."""

    categorySelected = pyqtSignal(str)  # emits the category key

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._counts: dict[str, Optional[int]] = {k: None for k in CATEGORY_ORDER}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        heading = QLabel("LIBRARY")
        heading.setObjectName("PanelHeading")
        layout.addWidget(heading)

        self._list = QListWidget()
        self._list.setObjectName("CategoryList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(self._list.focusPolicy())
        for key in CATEGORY_ORDER:
            item = QListWidgetItem(self._render(key))
            item.setData(0x0100, key)  # Qt.ItemDataRole.UserRole
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._list, stretch=1)

    def _render(self, key: str) -> str:
        count = self._counts.get(key)
        shown = _NO_COUNT if count is None else str(count)
        return f"{_LABELS[key]} {shown}"

    def set_counts(self, counts: Optional[dict[str, int]]) -> None:
        """
        Update the per-category counts.

        Pass ``None`` to reset every category to the pre-analysis placeholder.
        """
        for i, key in enumerate(CATEGORY_ORDER):
            self._counts[key] = None if counts is None else int(counts.get(key, 0))
            self._list.item(i).setText(self._render(key))

    def current_category(self) -> Optional[str]:
        item = self._list.currentItem()
        return None if item is None else item.data(0x0100)

    def select_category(self, key: str) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(0x0100) == key:
                self._list.setCurrentRow(i)
                return

    def _on_current_changed(self, current, _previous) -> None:
        if current is not None:
            self.categorySelected.emit(current.data(0x0100))
