"""
Right panel: per-image metrics and file metadata.

Metrics (quality score, face count, blur score) come from the pipeline's
per-photo result via the :class:`~ui_qt.models.photo_index.PhotoEntry`. File
metadata (name, dimensions, size, modified, path) is read GUI-side with
``QFileInfo``/``QImageReader`` -- no backend involvement.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QFileInfo
from PyQt6.QtGui import QImageReader
from PyQt6.QtWidgets import QFormLayout, QFrame, QLabel, QVBoxLayout, QWidget

from ui_qt.models.photo_index import PhotoEntry

_PLACEHOLDER = "—"  # em dash

_METRIC_FIELDS = ("Quality score", "Face count", "Blur score")
_FILE_FIELDS = ("Name", "Dimensions", "Size", "Modified", "Path")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class MetadataPanel(QWidget):
    """Displays metrics and file metadata for the selected photo."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataPanel")
        self._values: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._heading("METRICS"))
        root.addLayout(self._form(_METRIC_FIELDS))
        root.addWidget(self._separator())
        root.addWidget(self._heading("FILE"))
        root.addLayout(self._form(_FILE_FIELDS))
        root.addStretch(1)

        self.clear()

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("PanelHeading")
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setObjectName("MetaSeparator")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        return line

    def _form(self, fields: tuple[str, ...]) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(12, 4, 12, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        for name in fields:
            key_label = QLabel(name)
            key_label.setObjectName("MetaKey")
            value_label = QLabel(_PLACEHOLDER)
            value_label.setObjectName("MetaValue")
            value_label.setWordWrap(True)
            self._values[name] = value_label
            form.addRow(key_label, value_label)
        return form

    def clear(self) -> None:
        """Reset every field to the placeholder (no photo selected)."""
        for label in self._values.values():
            label.setText(_PLACEHOLDER)

    def set_field(self, name: str, value: str) -> None:
        if name in self._values:
            self._values[name].setText(value)

    def show_entry(self, entry: PhotoEntry) -> None:
        """Populate metrics (from analysis) and file metadata for ``entry``."""
        self.set_field(
            "Quality score",
            _PLACEHOLDER if entry.quality_score is None else f"{entry.quality_score:.1f}",
        )
        self.set_field(
            "Face count",
            _PLACEHOLDER if entry.face_count is None else str(entry.face_count),
        )
        self.set_field(
            "Blur score",
            _PLACEHOLDER if entry.blur_score is None else f"{entry.blur_score:.0f}",
        )

        self.set_field("Name", entry.name)
        self.set_field("Path", entry.source_path)

        info = QFileInfo(entry.source_path)
        if info.exists():
            self.set_field("Size", _human_size(info.size()))
            self.set_field("Modified", info.lastModified().toString("yyyy-MM-dd HH:mm"))
            size = QImageReader(entry.source_path).size()
            self.set_field(
                "Dimensions",
                f"{size.width()} × {size.height()}" if size.isValid() else _PLACEHOLDER,
            )
        else:
            for field in ("Dimensions", "Size", "Modified"):
                self.set_field(field, _PLACEHOLDER)
