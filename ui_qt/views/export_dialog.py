"""
Album export dialog for PhotoFlow.

Lets the photographer choose which rendered formats to write (PNG / JPG / PDF /
layered PSD), where to write them, and whether to bake the auto-edit corrections
into the output. The dialog only collects choices; the actual rendering is done
by :mod:`core.album.raster` from the caller (the main window), so this widget
stays free of heavy image work and is easy to test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.album.raster import FORMAT_JPG, FORMAT_PDF, FORMAT_PNG, FORMAT_PSD


class ExportDialog(QDialog):
    """Collects the formats, output folder and edit option for an album export."""

    def __init__(self, default_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Album")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Render the album to shareable files — no Photoshop needed.\n"
            "PNG, JPG and PDF open anywhere; PSD stays layered for editors."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Format checkboxes. PNG on by default (universal, lossless).
        self.cb_png = QCheckBox("PNG  — lossless, one file per spread")
        self.cb_jpg = QCheckBox("JPG  — smaller, one file per spread")
        self.cb_pdf = QCheckBox("PDF  — single multi-page document")
        self.cb_psd = QCheckBox("PSD  — layered, editable (needs an editor)")
        self.cb_png.setChecked(True)
        for cb in (self.cb_png, self.cb_jpg, self.cb_pdf, self.cb_psd):
            layout.addWidget(cb)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        # Output folder chooser.
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Save to:"))
        self.dir_edit = QLineEdit(str(default_dir))
        folder_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        # Bake auto-edits into the render.
        self.cb_apply_edits = QCheckBox("Apply auto-edit corrections to the render")
        self.cb_apply_edits.setChecked(True)
        layout.addWidget(self.cb_apply_edits)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose export folder", self.dir_edit.text()
        )
        if chosen:
            self.dir_edit.setText(chosen)

    # ------------------------------------------------------------------ #
    # Accessors (kept simple so tests can read choices without exec()).
    # ------------------------------------------------------------------ #
    def selected_formats(self) -> list[str]:
        """Return the chosen formats in a stable order."""
        chosen: list[str] = []
        if self.cb_png.isChecked():
            chosen.append(FORMAT_PNG)
        if self.cb_jpg.isChecked():
            chosen.append(FORMAT_JPG)
        if self.cb_pdf.isChecked():
            chosen.append(FORMAT_PDF)
        if self.cb_psd.isChecked():
            chosen.append(FORMAT_PSD)
        return chosen

    def output_dir(self) -> Path:
        return Path(self.dir_edit.text()).expanduser()

    def apply_edits(self) -> bool:
        return self.cb_apply_edits.isChecked()
