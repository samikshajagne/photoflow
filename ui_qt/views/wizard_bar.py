"""
Guided step bar for PhotoFlow.

A horizontal strip across the top of the window that walks a non-technical
photographer through the whole journey -- Open, Analyze, Review, Build Album,
Export -- one step at a time. It shows where they are, what each step does in
plain language, and a single prominent button for the action to take next.

The bar is purely presentational: it emits :attr:`actionRequested` with the
current step key when the primary button is pressed, and the main window decides
what to do and calls :meth:`update_view` to reflect the new state. Step gating
(you can't export before building an album) is enforced by the main window.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Ordered steps: (key, short chip label). Opening a folder sorts it in the same
# step (no separate Analyze/Review clicks). The people-first flow then labels
# the people found before laying out the album:
#   Open & Analyze -> Label People -> Build Album -> Export.
STEPS: tuple[tuple[str, str], ...] = (
    ("open", "Open & Analyze"),
    ("people", "Label People"),
    ("album", "Build Album"),
    ("export", "Export"),
)

# Plain-language title + one-line description per step.
_TITLES = {
    "open": "Choose and sort your photos",
    "people": "Label the people",
    "album": "Build your album",
    "export": "Save your album",
}
_DESCRIPTIONS = {
    "open": "Pick your shoot's folder — PhotoFlow sorts every photo into Best "
    "Shots, Duplicates, Blurry, and Review automatically.",
    "people": "Name the key people (bride, groom, family) so your album is built "
    "around them. You can skip anyone you don't need.",
    "album": "Lay your best photos into album spreads automatically.",
    "export": "Save the album as PNG, JPG, PDF, or layered PSD — no Photoshop needed.",
}
# Primary button label per step.
_CTA = {
    "open": "Open & Analyze…",
    "people": "Label People",
    "album": "Build Album",
    "export": "Export Album",
}

_ACCENT = "#3A82F6"
_TEXT = "#DCDDE0"
_DIM = "#96989E"
_DONE = "#5BB974"  # muted green for completed steps


class WizardBar(QWidget):
    """A guided, step-by-step header strip."""

    actionRequested = pyqtSignal(str)  # current step key

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardBar")
        self._current = "open"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        # --- Step chips row ------------------------------------------------ #
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chips: dict[str, QLabel] = {}
        for i, (key, label) in enumerate(STEPS):
            chip = QLabel()
            chip.setObjectName("wizardChip")
            chip.setTextFormat(Qt.TextFormat.RichText)
            self._chips[key] = chip
            chips.addWidget(chip)
            if i < len(STEPS) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet(f"color: {_DIM};")
                chips.addWidget(arrow)
        chips.addStretch(1)
        outer.addLayout(chips)

        # --- Current-step instruction + primary action -------------------- #
        row = QHBoxLayout()
        row.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._title = QLabel()
        self._title.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 600;")
        self._desc = QLabel()
        self._desc.setStyleSheet(f"color: {_DIM};")
        self._desc.setWordWrap(True)
        text_col.addWidget(self._title)
        text_col.addWidget(self._desc)
        row.addLayout(text_col, 1)

        self._cta = QPushButton()
        self._cta.setObjectName("wizardCta")
        self._cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cta.setMinimumHeight(34)
        self._cta.setStyleSheet(
            "QPushButton#wizardCta {"
            f"  background: {_ACCENT}; color: white; border: none;"
            "   border-radius: 6px; padding: 6px 18px; font-weight: 600;"
            "}"
            "QPushButton#wizardCta:disabled { background: #3a3b40; color: #6a6c72; }"
            "QPushButton#wizardCta:hover:!disabled { background: #4a8ef7; }"
        )
        self._cta.clicked.connect(lambda: self.actionRequested.emit(self._current))
        row.addWidget(self._cta, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row)

        # Hairline separator under the bar.
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #303138;")
        outer.addWidget(line)

        self.update_view("open", set(), busy=False)

    # ------------------------------------------------------------------ #
    def update_view(
        self, current_key: str, completed: Iterable[str], busy: bool = False
    ) -> None:
        """
        Refresh the bar for the given current step and completed steps.

        Args:
            current_key: The step the user is on now.
            completed: Step keys already finished (shown with a check).
            busy: When True, the primary button is disabled (an operation is
                running).
        """
        self._current = current_key
        done = set(completed)

        for key, label in STEPS:
            chip = self._chips[key]
            if key in done:
                chip.setText(f"<b>✓ {label}</b>")
                chip.setStyleSheet(f"color: {_DONE}; padding: 2px 4px;")
            elif key == current_key:
                chip.setText(f"<b>{label}</b>")
                chip.setStyleSheet(
                    f"color: white; background: {_ACCENT};"
                    " border-radius: 4px; padding: 2px 8px;"
                )
            else:
                chip.setText(label)
                chip.setStyleSheet(f"color: {_DIM}; padding: 2px 4px;")

        self._title.setText(_TITLES.get(current_key, ""))
        self._desc.setText(_DESCRIPTIONS.get(current_key, ""))
        self._cta.setText(_CTA.get(current_key, "Next"))
        self._cta.setEnabled(not busy)
