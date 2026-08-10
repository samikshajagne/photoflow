"""
Full-window startup chooser for PhotoFlow.

Used as :class:`~ui_qt.views.main_window.MainWindow`'s initial central widget
when it's built with ``mode="chooser"`` (see ``ui_qt.main``) -- picking a
mode is part of the app's own UI, not a separate popup dialog. Emits
:pyattr:`modeChosen` with :data:`MODE_ALBUM` or :data:`MODE_PASSPORT`;
``MainWindow._enter_mode`` handles rebuilding the window for that choice.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

MODE_ALBUM = "album"
MODE_PASSPORT = "passport"
MODE_COLLAGE = "collage"

_ACCENT = "#3A82F6"
_ACCENT_HOVER = "#4A8EF7"
_TEXT_DIM = "#96989E"


class _IconBadge(QLabel):
    """A soft, tinted circle behind an emoji glyph -- a lightweight app icon."""

    def __init__(self, glyph: str, tint: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(glyph, parent)
        self.setFixedSize(56, 56)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background: {tint}; border-radius: 28px; font-size: 24px; border: none;"
        )


class _OptionCard(QFrame):
    """A large clickable card (icon + title + description) for one mode choice."""

    clicked = pyqtSignal()

    def __init__(
        self,
        glyph: str,
        tint: str,
        title: str,
        subtitle: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OptionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(300, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "#OptionCard {"
            "  background: #212226;"
            "  border: 1px solid #2e3036;"
            "  border-radius: 16px;"
            "}"
            "#OptionCard:hover {"
            f"  border: 1px solid {_ACCENT};"
            "  background: #24262c;"
            "}"
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 26)
        layout.setSpacing(4)

        layout.addWidget(_IconBadge(glyph, tint))
        layout.addSpacing(18)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet(
            "font-size: 19px; font-weight: 700; color: #f2f2f3; border: none; background: transparent;"
        )
        layout.addWidget(title_label)

        desc_label = QLabel(subtitle)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(
            f"font-size: 13px; color: {_TEXT_DIM}; border: none; background: transparent;"
        )
        layout.addWidget(desc_label)

        layout.addStretch(1)

        self.button = QPushButton("Choose")
        self.button.setMinimumHeight(42)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet(
            "QPushButton {"
            f"  background: {_ACCENT}; color: #ffffff; border: none;"
            "   border-radius: 9px; font-weight: 600; font-size: 13px;"
            "}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            "QPushButton:pressed { background: #2f6fd6; }"
        )
        self.button.clicked.connect(self.clicked.emit)
        layout.addWidget(self.button)

    def mousePressEvent(self, event) -> None:  # noqa: D401, N802 - Qt override
        # Clicking the button already emits (and consumes the event before it
        # reaches here), so this only fires for clicks elsewhere on the card.
        super().mousePressEvent(event)
        self.clicked.emit()


class ModeChooserView(QWidget):
    """Full-window landing screen with two large mode options."""

    modeChosen = pyqtSignal(str)  # MODE_ALBUM or MODE_PASSPORT

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background-color: #1a1b1e;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 48, 48, 48)
        outer.addStretch(2)

        # These need an explicit transparent background: the app-wide QSS sets a
        # background on QWidget, which QLabel would otherwise paint, drawing
        # visible lighter bands across this view's darker backdrop.
        brand = QLabel("PhotoFlow")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet(
            "background: transparent; border: none; font-size: 13px; "
            f"font-weight: 700; letter-spacing: 3px; color: {_ACCENT};"
        )
        outer.addWidget(brand)

        title = QLabel("What would you like to do?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "background: transparent; border: none; font-size: 26px; "
            "font-weight: 700; color: #f2f2f3; margin-top: 4px;"
        )
        outer.addWidget(title)

        subtitle = QLabel("Pick one to get started.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "background: transparent; border: none; "
            f"color: {_TEXT_DIM}; font-size: 13px; margin-top: 2px;"
        )
        outer.addWidget(subtitle)

        outer.addSpacing(36)

        row = QHBoxLayout()
        row.setSpacing(24)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addLayout(row, 3)
        centered.addStretch(1)
        outer.addLayout(centered)

        self.album_card = _OptionCard(
            "🖼️",
            "#2a3a52",
            "Generate Album",
            "Sort a wedding shoot, find and label people, and build a printable album.",
        )
        self.album_card.clicked.connect(lambda: self.modeChosen.emit(MODE_ALBUM))
        row.addWidget(self.album_card)

        self.passport_card = _OptionCard(
            "🪪",
            "#2f4a3a",
            "Passport Photos",
            "Crop a portrait to a standard passport/ID size and tile copies onto a print sheet.",
        )
        self.passport_card.clicked.connect(lambda: self.modeChosen.emit(MODE_PASSPORT))
        row.addWidget(self.passport_card)

        self.collage_card = _OptionCard(
            "🧩",
            "#4a3a52",
            "Make a Collage",
            "Pick photos, choose a theme and layout, and get a finished collage ready to print or post.",
        )
        self.collage_card.clicked.connect(lambda: self.modeChosen.emit(MODE_COLLAGE))
        row.addWidget(self.collage_card)

        outer.addStretch(3)
