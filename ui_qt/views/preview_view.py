"""
Scrollable album-preview panel.

Shows the rendered spreads top-to-bottom exactly as they will export. The main
window feeds it images from :class:`~ui_qt.workers.preview_worker.PreviewWorker`
as they render.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

# On-screen width each spread is scaled to (native preview is ~1100px wide).
_DISPLAY_WIDTH = 960


class PreviewView(QScrollArea):
    """A vertical, scrollable list of rendered spread images."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(18)

        self._status = QLabel("Build an album, then choose Preview.")
        self._status.setStyleSheet("color: #96989E; padding: 6px;")
        self._status.setWordWrap(True)
        self._layout.addWidget(self._status)

        self.setWidget(self._body)
        self._labels: list[QLabel] = []

    # ------------------------------------------------------------------ #
    def show_message(self, text: str) -> None:
        self._clear_labels()
        self._status.setText(text)

    def begin(self, count: int) -> None:
        """Prepare ``count`` empty slots to fill as spreads render."""
        self._clear_labels()
        self._status.setText(f"Rendering {count} spread(s)…")
        for i in range(count):
            label = QLabel(f"Spread {i + 1}…")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(140)
            label.setStyleSheet("background: #202124; color: #6a6c72; border-radius: 4px;")
            self._labels.append(label)
            self._layout.addWidget(label)

    def set_spread(self, index: int, image: QImage) -> None:
        if 0 <= index < len(self._labels):
            pixmap = QPixmap.fromImage(image)
            if pixmap.width() > _DISPLAY_WIDTH:
                pixmap = pixmap.scaledToWidth(
                    _DISPLAY_WIDTH, Qt.TransformationMode.SmoothTransformation
                )
            label = self._labels[index]
            label.setText("")
            label.setStyleSheet("")
            label.setMinimumHeight(0)
            label.setPixmap(pixmap)

    def finish(self, count: int) -> None:
        self._status.setText(
            f"{count} spread(s) — this is how the album will export. "
            "Use Change Size to adjust, then Export Album."
        )

    # ------------------------------------------------------------------ #
    def _clear_labels(self) -> None:
        for label in self._labels:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._labels = []
