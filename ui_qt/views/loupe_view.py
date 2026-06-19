"""
Loupe: large single-image preview.

A ``QGraphicsView`` showing one photo fit to the viewport. The image is
decoded lazily on a worker (scaled to a bounded max edge so a 24MP file
doesn't blow up memory) and shown when ready.

Navigation overlays sit on top of the image: a Back button (also Esc) returns
to the grid, and Prev/Next buttons (also Left/Right arrow keys) step through
the photos in the currently displayed set. A small counter shows the position.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QPushButton,
)

from ui_qt.workers.thumbnail_loader import ThumbnailLoader


class LoupeView(QGraphicsView):
    backRequested = pyqtSignal()
    nextRequested = pyqtSignal()
    prevRequested = pyqtSignal()

    def __init__(self, max_edge: int = 1600, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: Optional[QGraphicsPixmapItem] = None
        self._current_path: Optional[str] = None

        self.setRenderHints(self.renderHints())
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Overlay controls live on the viewport so they paint over the scene.
        self._back_btn = self._make_button("‹  Back", "LoupeBackButton")
        self._prev_btn = self._make_button("‹", "LoupeNavButton")
        self._next_btn = self._make_button("›", "LoupeNavButton")

        self._counter = QLabel("", self.viewport())
        self._counter.setObjectName("LoupeCounter")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet(
            "QLabel#LoupeCounter {"
            " color: white; background: rgba(0,0,0,140);"
            " padding: 4px 10px; border-radius: 10px; }"
        )

        self._back_btn.clicked.connect(self.backRequested)
        self._prev_btn.clicked.connect(self.prevRequested)
        self._next_btn.clicked.connect(self.nextRequested)

        # Reuse the thumbnail machinery at a larger edge for the preview.
        self._loader = ThumbnailLoader(edge=max_edge, max_threads=1)
        self._loader.thumbnailReady.connect(self._on_ready)

        self._reposition_overlays()

    def _make_button(self, text: str, object_name: str) -> QPushButton:
        btn = QPushButton(text, self.viewport())
        btn.setObjectName(object_name)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setStyleSheet(
            "QPushButton {"
            " color: white; background: rgba(0,0,0,140);"
            " border: none; border-radius: 18px;"
            " padding: 8px 16px; font-size: 16px; }"
            "QPushButton:hover { background: rgba(60,60,60,200); }"
            "QPushButton:disabled { color: rgba(255,255,255,70); }"
        )
        return btn

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #
    def show_image(self, path: str) -> None:
        self._current_path = path
        self._loader.request(path)

    def set_nav_state(self, index: int, total: int) -> None:
        """Update the counter and enable/disable Prev/Next at the ends."""
        if total <= 0:
            self._counter.setText("")
        else:
            self._counter.setText(f"{index + 1} / {total}")
        self._prev_btn.setEnabled(index > 0)
        self._next_btn.setEnabled(0 <= index < total - 1)
        self._counter.adjustSize()
        self._reposition_overlays()

    # ----------------------------------------------------------------- #
    # Rendering / layout
    # ----------------------------------------------------------------- #
    def _on_ready(self, path: str, image: QImage) -> None:
        if path != self._current_path or image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        self._scene.clear()
        self._item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(pixmap.rect().toRectF())
        self._fit()

    def _fit(self) -> None:
        if self._item is not None:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def _reposition_overlays(self) -> None:
        margin = 16
        vp = self.viewport()
        w, h = vp.width(), vp.height()

        self._back_btn.adjustSize()
        self._back_btn.move(margin, margin)

        # Center the counter along the top edge.
        cx = max(margin, (w - self._counter.width()) // 2)
        self._counter.move(cx, margin)

        # Prev/Next vertically centered against the left/right edges.
        for btn in (self._prev_btn, self._next_btn):
            btn.adjustSize()
        cy = max(margin, (h - self._prev_btn.height()) // 2)
        self._prev_btn.move(margin, cy)
        self._next_btn.move(max(margin, w - self._next_btn.width() - margin), cy)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit()
        self._reposition_overlays()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.backRequested.emit()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.prevRequested.emit()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.nextRequested.emit()
            return
        super().keyPressEvent(event)

    def shutdown(self) -> None:
        self._loader.shutdown()
