"""
Album settings dialog for PhotoFlow.

Lets the photographer choose the album's physical specification and layout
density before building: page size (presets or a custom width x height), print
resolution, single- vs double-page spreads (with a binding gutter), and how
densely photos are packed onto each spread (which controls the spread count).

The dialog only collects choices; it produces an :class:`AlbumSpec` and a
density string for the layout selector. Defaults match the previous hardcoded
behaviour (12x12 in, 300 dpi, double-page, balanced).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.album.layout import AlbumSpec
from core.album.layout_select import (
    DENSITY_BALANCED,
    DENSITY_DENSE,
    DENSITY_SPACIOUS,
)

# Page-size presets: label -> (width_in, height_in). "Custom…" enables the
# width/height spinboxes.
_PRESETS: list[tuple[str, Optional[tuple[float, float]]]] = [
    ("Square 12 × 12 in", (12.0, 12.0)),
    ("Square 10 × 10 in", (10.0, 10.0)),
    ("Square 8 × 8 in", (8.0, 8.0)),
    ("Landscape 12 × 8 in", (12.0, 8.0)),
    ("Landscape 14 × 11 in", (14.0, 11.0)),
    ("Portrait 8 × 12 in", (8.0, 12.0)),
    ("Custom…", None),
]

_DENSITIES = [
    ("Spacious — fewer photos per spread (more spreads)", DENSITY_SPACIOUS),
    ("Balanced", DENSITY_BALANCED),
    ("Dense — more photos per spread (fewer spreads)", DENSITY_DENSE),
]


class AlbumSettingsDialog(QDialog):
    """Collects the album spec + layout density before building an album."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        spec: Optional[AlbumSpec] = None,
        density: str = DENSITY_BALANCED,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Album Settings")
        self.setMinimumWidth(420)

        spec = spec or AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)

        outer = QVBoxLayout(self)
        intro = QLabel("Choose your album's size, quality, and how densely photos are packed.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        outer.addLayout(form)

        # Page size preset.
        self.preset = QComboBox()
        for label, _ in _PRESETS:
            self.preset.addItem(label)
        form.addRow("Page size:", self.preset)

        # Custom width / height (inches).
        self.width_in = QDoubleSpinBox()
        self.width_in.setRange(1.0, 40.0)
        self.width_in.setSuffix(" in")
        self.width_in.setDecimals(2)
        self.height_in = QDoubleSpinBox()
        self.height_in.setRange(1.0, 40.0)
        self.height_in.setSuffix(" in")
        self.height_in.setDecimals(2)
        form.addRow("Width:", self.width_in)
        form.addRow("Height:", self.height_in)

        # Resolution.
        self.dpi = QComboBox()
        for d in (150, 300, 600):
            self.dpi.addItem(f"{d} dpi", d)
        form.addRow("Resolution:", self.dpi)

        # Double-page + gutter.
        self.double_page = QCheckBox("Double-page spreads (two facing pages)")
        form.addRow("", self.double_page)
        self.gutter_in = QDoubleSpinBox()
        self.gutter_in.setRange(0.0, 2.0)
        self.gutter_in.setSuffix(" in")
        self.gutter_in.setDecimals(2)
        self.gutter_in.setSingleStep(0.05)
        form.addRow("Binding gutter:", self.gutter_in)

        # Density.
        self.density = QComboBox()
        for label, key in _DENSITIES:
            self.density.addItem(label, key)
        form.addRow("Photos per spread:", self.density)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Wiring + initial values.
        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        self.double_page.toggled.connect(self.gutter_in.setEnabled)
        self._load_from_spec(spec, density)

    # ------------------------------------------------------------------ #
    def _load_from_spec(self, spec: AlbumSpec, density: str) -> None:
        # Match a preset if the size is one; otherwise select Custom.
        size = (float(spec.page_width_in), float(spec.page_height_in))
        match = next(
            (i for i, (_, p) in enumerate(_PRESETS) if p is not None and p == size),
            len(_PRESETS) - 1,  # "Custom…"
        )
        self.preset.setCurrentIndex(match)
        self.width_in.setValue(size[0])
        self.height_in.setValue(size[1])
        dpi_idx = self.dpi.findData(int(spec.dpi))
        self.dpi.setCurrentIndex(dpi_idx if dpi_idx >= 0 else 1)
        self.double_page.setChecked(bool(spec.double_page_spread))
        self.gutter_in.setValue(float(spec.gutter_in))
        self.gutter_in.setEnabled(bool(spec.double_page_spread))
        dens_idx = self.density.findData(density)
        self.density.setCurrentIndex(dens_idx if dens_idx >= 0 else 1)
        self._on_preset_changed(self.preset.currentIndex())

    def _on_preset_changed(self, index: int) -> None:
        preset = _PRESETS[index][1]
        is_custom = preset is None
        self.width_in.setEnabled(is_custom)
        self.height_in.setEnabled(is_custom)
        if preset is not None:
            self.width_in.setValue(preset[0])
            self.height_in.setValue(preset[1])

    # ------------------------------------------------------------------ #
    def album_spec(self) -> AlbumSpec:
        """Build the chosen :class:`AlbumSpec` (preserves bleed/margin defaults)."""
        return AlbumSpec(
            page_width_in=float(self.width_in.value()),
            page_height_in=float(self.height_in.value()),
            dpi=int(self.dpi.currentData()),
            gutter_in=float(self.gutter_in.value()) if self.double_page.isChecked() else 0.0,
            double_page_spread=self.double_page.isChecked(),
        )

    def selected_density(self) -> str:
        return str(self.density.currentData())
