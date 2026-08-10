"""
Automatic collage maker view.

Third standalone mode alongside album generation and passport photos: pick
photos, pick a theme and layout, and a finished collage appears. All geometry
and rendering lives in :mod:`core.collage` (plus ``collage_shapes``,
``collage_text``, ``collage_auto`` and ``collage_presets``); this module is only
the Qt shell around them.

The controls are grouped into tabs -- Photos, Style, Background, Text, Photo,
Auto, Print -- because there are now far too many for one flat column to stay
readable on a laptop screen.

Memory and responsiveness notes (learned the hard way elsewhere in this app --
see the beautify slider incident):

* Full-resolution source images are **not** held in memory. A collage of 20
  photos from a 24MP camera would be ~1.4 GB decoded. Each added photo keeps
  only a downscaled preview copy plus its path, face boxes and adjustments;
  the originals are re-opened once, lazily, at export.
* The live preview renders at a reduced canvas size, never the full print spec
  (an A4 300dpi collage is 3508x2480 and far too slow to redraw interactively).
* Every control change goes through a debounce timer, so dragging a slider
  schedules exactly one re-render instead of dozens.

Known limitation: auto-build scans and scores photos on the UI thread, so a
large folder will briefly freeze the window (a wait cursor is shown, and the
scan is capped by the "Scan at most" box). Moving it onto the existing worker
infrastructure in ``ui_qt/workers`` is the natural next step.
"""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.collage import (
    BACKGROUND_STYLES,
    BG_BLURRED_PHOTO,
    BG_GRADIENT,
    BG_IMAGE,
    BG_SOLID,
    DEFAULT_THEME,
    FILTERS,
    LAYOUTS,
    SIZE_PRESETS,
    THEMES,
    Background,
    CollageError,
    CollagePhoto,
    CollageSpec,
    CollageTheme,
    PhotoAdjust,
    PrintMarks,
    build_collage,
    check_resolution,
    layout_cells,
    save_collage,
    suggest_layout,
)
from core.collage_presets import (
    CollagePreset,
    PresetError,
    delete_preset,
    load_presets,
    upsert_preset,
)
from core.collage_shapes import SHAPE_NONE, SHAPE_TEXT, SHAPES
from core.collage_text import POSITIONS, TextOverlay, Watermark
from core.face_detector import FaceBox
from utils.logger import get_logger

logger = get_logger("ui_qt.collage_view")

_SOURCE_PREVIEW_MAX_DIM = 1000
_PREVIEW_CANVAS_MAX_DIM = 820
_PREVIEW_DEBOUNCE_MS = 140

_LAYOUT_LABELS: dict[str, str] = {
    "mosaic": "Mosaic (follows photo shapes)",
    "grid": "Grid (equal tiles)",
    "feature": "Feature (one big photo)",
    "masonry": "Masonry (columns)",
    "magazine": "Magazine (hero + row)",
    "filmstrip": "Filmstrip (single strip)",
    "scatter": "Scatter (loose prints)",
}
_AUTO_LAYOUT = "Auto (pick for me)"
_BG_LABELS: dict[str, str] = {
    BG_SOLID: "Solid colour",
    BG_GRADIENT: "Gradient",
    BG_IMAGE: "Image file",
    BG_BLURRED_PHOTO: "Blurred first photo",
}
_SHAPE_LABELS: dict[str, str] = {
    "none": "No shape (fill canvas)",
    "heart": "Heart",
    "circle": "Circle",
    "rounded": "Rounded rectangle",
    "star": "Star",
    "text": "Text / number",
}


def _wrapped(text: str) -> QLabel:
    """
    A word-wrapping explanatory label.

    Wrapping matters structurally, not just cosmetically: a non-wrapping QLabel
    reports its full single-line width as a minimum, which forces the whole
    control panel wider than its scroll viewport and clips every button in it.
    """
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _pil_to_qpixmap(image: Image.Image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class _ColorButton(QPushButton):
    """A button that shows its colour and opens a picker when clicked."""

    def __init__(self, color: tuple[int, int, int], on_change=None) -> None:
        super().__init__()
        self._color = color
        self._on_change = on_change
        self.setFixedHeight(24)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> tuple[int, int, int]:
        return self._color

    def set_color(self, color: tuple[int, int, int]) -> None:
        self._color = tuple(int(c) for c in color)  # type: ignore[assignment]
        self._refresh()

    def _refresh(self) -> None:
        r, g, b = self._color
        # Label text flips to stay legible on light or dark swatches.
        text = "#111" if (r + g + b) > 380 else "#eee"
        self.setText(f"#{r:02x}{g:02x}{b:02x}")
        self.setStyleSheet(
            f"background: rgb({r},{g},{b}); color: {text}; border: 1px solid #46484f;"
            "border-radius: 6px; font-weight: 600;"
        )

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(*self._color), self, "Pick a colour")
        if chosen.isValid():
            self.set_color((chosen.red(), chosen.green(), chosen.blue()))
            if self._on_change:
                self._on_change()


@dataclasses.dataclass
class _CollageItem:
    """One chosen photo: a small preview copy plus what's needed to re-read it."""

    path: Path
    preview: Image.Image
    face_boxes: tuple[FaceBox, ...]
    full_size: tuple[int, int]
    adjust: PhotoAdjust = dataclasses.field(default_factory=PhotoAdjust)

    @property
    def name(self) -> str:
        return self.path.name


class CollageView(QWidget):
    """Pick photos, a theme and a layout; get a finished collage."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: list[_CollageItem] = []
        self._face_detector = None
        self._seed = 0
        self._rendered: Optional[Image.Image] = None
        self._loading_preset = False  # suppresses re-renders while applying a preset

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)

        root = QHBoxLayout(self)
        root.addWidget(self._build_controls(), 0)
        root.addWidget(self._build_canvas(), 1)
        self._refresh_preset_list()
        self._sync_enabled()

    # ------------------------------------------------------------------ #
    # Canvas
    # ------------------------------------------------------------------ #
    def _build_canvas(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.preview_label = QLabel("Add photos to start your collage.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 480)
        self.preview_label.setStyleSheet(
            "background:#202124; border-radius:8px; color:#96989e;"
        )
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.preview_label, 1)

        self.status_label = QLabel("No photos yet.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color:#e0a03a;")
        layout.addWidget(self.warning_label)
        return container

    # ------------------------------------------------------------------ #
    # Controls
    # ------------------------------------------------------------------ #
    def _build_controls(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)

        # Presets sit above the tabs: they cut across every other setting.
        preset_row = QHBoxLayout()
        self.preset_choice = QComboBox()
        self.preset_choice.setMinimumWidth(120)
        self.preset_choice.activated.connect(self._on_apply_preset)
        self.btn_save_preset = QPushButton("Save")
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        self.btn_delete_preset = QPushButton("Delete")
        self.btn_delete_preset.clicked.connect(self._on_delete_preset)
        preset_row.addWidget(QLabel("Preset:"))
        preset_row.addWidget(self.preset_choice, 1)
        preset_row.addWidget(self.btn_save_preset)
        preset_row.addWidget(self.btn_delete_preset)
        outer.addLayout(preset_row)

        self.tabs = QTabWidget()
        # Short labels so all seven fit without tab-bar scroll arrows. "Tune"
        # rather than "Photo" because a "Photo" tab sitting next to a "Photos"
        # tab reads as a typo.
        self.tabs.addTab(self._tab_photos(), "Photos")
        self.tabs.addTab(self._tab_style(), "Style")
        self.tabs.addTab(self._tab_background(), "BG")
        self.tabs.addTab(self._tab_text(), "Text")
        self.tabs.addTab(self._tab_per_photo(), "Tune")
        self.tabs.addTab(self._tab_auto(), "Auto")
        self.tabs.addTab(self._tab_print(), "Print")
        outer.addWidget(self.tabs, 1)

        self.btn_export = QPushButton("Export Collage…")
        self.btn_export.clicked.connect(self._on_export)
        outer.addWidget(self.btn_export)

        wrapper = QScrollArea()
        wrapper.setWidgetResizable(True)
        wrapper.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrapper.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        # Wide enough that all seven tab labels fit without Qt adding scroll
        # arrows to the tab bar.
        wrapper.setMinimumWidth(380)
        wrapper.setMaximumWidth(470)
        wrapper.setWidget(panel)
        return wrapper

    def _tab_photos(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.photo_list = QListWidget()
        self.photo_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.photo_list.currentRowChanged.connect(self._on_photo_selected)
        layout.addWidget(self.photo_list, 1)

        self.btn_add = QPushButton("Add Photos…")
        self.btn_add.clicked.connect(self._on_add_photos)
        layout.addWidget(self.btn_add)

        row = QHBoxLayout()
        self.btn_up = QPushButton("Move Up")
        self.btn_up.clicked.connect(lambda: self._move_selected(-1))
        self.btn_down = QPushButton("Move Down")
        self.btn_down.clicked.connect(lambda: self._move_selected(1))
        row.addWidget(self.btn_up)
        row.addWidget(self.btn_down)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.clicked.connect(self._on_clear)
        row2.addWidget(self.btn_remove)
        row2.addWidget(self.btn_clear)
        layout.addLayout(row2)
        return tab

    def _tab_style(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()

        self.layout_choice = QComboBox()
        self.layout_choice.addItem(_AUTO_LAYOUT, None)
        for key in LAYOUTS:
            self.layout_choice.addItem(_LAYOUT_LABELS.get(key, key), key)
        self.layout_choice.currentIndexChanged.connect(self._request_preview)
        form.addRow("Layout:", self.layout_choice)

        self.theme_choice = QComboBox()
        for name in THEMES:
            self.theme_choice.addItem(name, name)
        self.theme_choice.setCurrentText(DEFAULT_THEME)
        self.theme_choice.currentIndexChanged.connect(self._request_preview)
        form.addRow("Theme:", self.theme_choice)

        self.size_choice = QComboBox()
        for name in SIZE_PRESETS:
            self.size_choice.addItem(name, name)
        self.size_choice.currentIndexChanged.connect(self._request_preview)
        form.addRow("Size:", self.size_choice)

        self.shape_choice = QComboBox()
        for key in SHAPES:
            self.shape_choice.addItem(_SHAPE_LABELS.get(key, key), key)
        self.shape_choice.currentIndexChanged.connect(self._on_shape_changed)
        form.addRow("Shape:", self.shape_choice)

        self.shape_text = QLineEdit()
        self.shape_text.setPlaceholderText("e.g. 25  or  A&B")
        self.shape_text.setEnabled(False)
        self.shape_text.textChanged.connect(self._request_preview)
        form.addRow("Shape text:", self.shape_text)
        layout.addLayout(form)

        self.sliders: dict[str, QSlider] = {}
        self.slider_labels: dict[str, QLabel] = {}
        for key, title, default, maximum in (
            ("spacing", "Gap", 14, 60),
            ("border", "Photo border", 0, 60),
            ("corner", "Rounded corners", 6, 60),
        ):
            layout.addLayout(self._slider_row(key, title, default, maximum))

        self.btn_shuffle = QPushButton("Shuffle Arrangement")
        self.btn_shuffle.clicked.connect(self._on_shuffle)
        layout.addWidget(self.btn_shuffle)
        layout.addStretch(1)
        return tab

    def _slider_row(self, key: str, title: str, default: int, maximum: int) -> QHBoxLayout:
        row = QHBoxLayout()
        caption = QLabel(f"{title}:")
        caption.setMinimumWidth(104)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, maximum)
        slider.setValue(default)
        slider.valueChanged.connect(self._on_slider_changed)
        value = QLabel(str(default))
        value.setMinimumWidth(28)
        row.addWidget(caption)
        row.addWidget(slider, 1)
        row.addWidget(value)
        self.sliders[key] = slider
        self.slider_labels[key] = value
        return row

    def _tab_background(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()

        self.bg_style = QComboBox()
        for key in BACKGROUND_STYLES:
            self.bg_style.addItem(_BG_LABELS.get(key, key), key)
        self.bg_style.currentIndexChanged.connect(self._on_bg_style_changed)
        form.addRow("Style:", self.bg_style)

        self.bg_color = _ColorButton((255, 255, 255), self._request_preview)
        form.addRow("Colour:", self.bg_color)
        self.bg_color2 = _ColorButton((225, 232, 240), self._request_preview)
        form.addRow("Second colour:", self.bg_color2)

        self.bg_vertical = QCheckBox("Vertical gradient")
        self.bg_vertical.setChecked(True)
        self.bg_vertical.toggled.connect(self._request_preview)
        form.addRow("", self.bg_vertical)

        image_row = QHBoxLayout()
        self.bg_image_path = QLineEdit()
        self.bg_image_path.setPlaceholderText("(no image chosen)")
        self.bg_image_path.setReadOnly(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._on_pick_background_image)
        image_row.addWidget(self.bg_image_path, 1)
        image_row.addWidget(btn_browse)
        form.addRow("Image:", image_row)
        layout.addLayout(form)

        layout.addLayout(self._slider_row("darken", "Darken", 25, 100))
        layout.addStretch(1)
        self._on_bg_style_changed()
        return tab

    def _tab_text(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title_box = QGroupBox("Title")
        tform = QFormLayout(title_box)
        self.title_text = QLineEdit()
        self.title_text.setPlaceholderText("Priya & Arjun\\n12 Feb 2026")
        self.title_text.textChanged.connect(self._request_preview)
        tform.addRow("Text:", self.title_text)

        self.title_position = QComboBox()
        for key in POSITIONS:
            self.title_position.addItem(key, key)
        self.title_position.setCurrentText("bottom-center")
        self.title_position.currentIndexChanged.connect(self._request_preview)
        tform.addRow("Position:", self.title_position)

        self.title_color = _ColorButton((255, 255, 255), self._request_preview)
        tform.addRow("Colour:", self.title_color)

        self.title_font = QComboBox()
        for role in ("serif", "serif_italic", "sans_bold", "script"):
            self.title_font.addItem(role, role)
        self.title_font.currentIndexChanged.connect(self._request_preview)
        tform.addRow("Font:", self.title_font)

        self.title_shadow = QCheckBox("Drop shadow (keeps text readable)")
        self.title_shadow.setChecked(True)
        self.title_shadow.toggled.connect(self._request_preview)
        tform.addRow("", self.title_shadow)
        layout.addWidget(title_box)
        layout.addLayout(self._slider_row("title_size", "Title size", 60, 200))

        logo_box = QGroupBox("Studio logo / watermark")
        lform = QFormLayout(logo_box)
        logo_row = QHBoxLayout()
        self.logo_path = QLineEdit()
        self.logo_path.setPlaceholderText("(no logo chosen)")
        self.logo_path.setReadOnly(True)
        btn_logo = QPushButton("Browse…")
        btn_logo.clicked.connect(self._on_pick_logo)
        btn_logo_clear = QPushButton("Clear")
        btn_logo_clear.clicked.connect(self._on_clear_logo)
        logo_row.addWidget(self.logo_path, 1)
        logo_row.addWidget(btn_logo)
        logo_row.addWidget(btn_logo_clear)
        lform.addRow("File:", logo_row)

        self.logo_position = QComboBox()
        for key in POSITIONS:
            self.logo_position.addItem(key, key)
        self.logo_position.setCurrentText("bottom-right")
        self.logo_position.currentIndexChanged.connect(self._request_preview)
        lform.addRow("Position:", self.logo_position)
        layout.addWidget(logo_box)
        layout.addLayout(self._slider_row("logo_width", "Logo size", 16, 60))
        layout.addLayout(self._slider_row("logo_opacity", "Logo opacity", 180, 255))
        layout.addStretch(1)
        return tab

    def _tab_per_photo(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.selected_label = QLabel("Select a photo in the Photos tab to adjust it.")
        self.selected_label.setWordWrap(True)
        layout.addWidget(self.selected_label)

        layout.addLayout(self._slider_row("zoom", "Zoom", 100, 300))
        layout.addLayout(self._slider_row("pan_x", "Pan left/right", 0, 100))
        layout.addLayout(self._slider_row("pan_y", "Pan up/down", 0, 100))
        layout.addLayout(self._slider_row("rotate", "Rotate", 0, 360))
        # Pan sliders are centred: 50 == no shift.
        self.sliders["pan_x"].setValue(50)
        self.sliders["pan_y"].setValue(50)

        form = QFormLayout()
        self.photo_filter = QComboBox()
        for key in FILTERS:
            self.photo_filter.addItem(key, key)
        self.photo_filter.currentIndexChanged.connect(self._on_photo_adjust_changed)
        form.addRow("Filter:", self.photo_filter)
        layout.addLayout(form)

        self.photo_beautify = QCheckBox("Enhance face (skin, colour, teeth/eyes)")
        self.photo_beautify.toggled.connect(self._on_photo_adjust_changed)
        layout.addWidget(self.photo_beautify)

        row = QHBoxLayout()
        self.btn_apply_all = QPushButton("Apply to All Photos")
        self.btn_apply_all.clicked.connect(self._on_apply_adjust_to_all)
        self.btn_reset_adjust = QPushButton("Reset")
        self.btn_reset_adjust.clicked.connect(self._on_reset_adjust)
        row.addWidget(self.btn_apply_all)
        row.addWidget(self.btn_reset_adjust)
        layout.addLayout(row)
        layout.addStretch(1)
        return tab

    def _tab_auto(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(
            _wrapped(
                "Pick the best photos from a folder automatically, using "
                "PhotoFlow's quality scoring."
            )
        )
        form = QFormLayout()

        folder_row = QHBoxLayout()
        self.auto_folder = QLineEdit()
        self.auto_folder.setPlaceholderText("(no folder chosen)")
        self.auto_folder.setReadOnly(True)
        btn_folder = QPushButton("Browse…")
        btn_folder.clicked.connect(self._on_pick_auto_folder)
        folder_row.addWidget(self.auto_folder, 1)
        folder_row.addWidget(btn_folder)
        form.addRow("Folder:", folder_row)

        self.auto_count = QSpinBox()
        self.auto_count.setRange(1, 60)
        self.auto_count.setValue(9)
        form.addRow("How many photos:", self.auto_count)

        self.auto_limit = QSpinBox()
        self.auto_limit.setRange(0, 100_000)
        self.auto_limit.setValue(200)
        self.auto_limit.setSpecialValueText("no limit")
        form.addRow("Scan at most:", self.auto_limit)
        layout.addLayout(form)

        self.auto_faces = QCheckBox("Only photos with a face")
        layout.addWidget(self.auto_faces)
        self.auto_spread = QCheckBox("Spread across the shoot (avoid one burst)")
        self.auto_spread.setChecked(True)
        layout.addWidget(self.auto_spread)

        # "&&" because Qt reads a single & in button text as a keyboard
        # mnemonic, which would render this as "Pick Photos _Build".
        self.btn_auto_build = QPushButton("Pick Photos && Build")
        self.btn_auto_build.clicked.connect(self._on_auto_build)
        layout.addWidget(self.btn_auto_build)

        layout.addWidget(
            _wrapped("Scoring a big folder takes a moment and the window will "
                     "pause while it runs.")
        )
        layout.addStretch(1)
        return tab

    def _tab_print(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(self._slider_row("bleed", "Bleed", 0, 60))
        self.trim_marks = QCheckBox("Trim / cut marks")
        self.trim_marks.toggled.connect(self._request_preview)
        layout.addWidget(self.trim_marks)
        layout.addWidget(
            _wrapped(
                "Bleed extends the image past the trim line so a slightly "
                "off cut doesn't show white paper."
            )
        )
        self.print_report = QLabel("")
        self.print_report.setWordWrap(True)
        layout.addWidget(self.print_report)
        layout.addStretch(1)
        return tab

    # ------------------------------------------------------------------ #
    # Photo management
    # ------------------------------------------------------------------ #
    def _on_add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose photos", "", "Images (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)"
        )
        if paths:
            self.add_photos(paths)

    def add_photos(self, paths) -> None:
        """Load ``paths`` into the collage (skipping any that fail to open)."""
        added, failed = 0, []
        for raw in paths:
            path = Path(raw)
            try:
                with Image.open(path) as opened:
                    opened.load()
                    full_size = opened.size
                    preview = opened.convert("RGB")
                    preview.thumbnail((_SOURCE_PREVIEW_MAX_DIM, _SOURCE_PREVIEW_MAX_DIM))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Collage: could not open '%s': %s", path, exc)
                failed.append(path.name)
                continue
            self._items.append(
                _CollageItem(
                    path=path,
                    preview=preview,
                    face_boxes=self._detect_faces(path),
                    full_size=full_size,
                )
            )
            self.photo_list.addItem(path.name)
            added += 1

        if failed:
            QMessageBox.warning(
                self, "Collage",
                "These files could not be opened:\n" + "\n".join(failed[:8]),
            )
        if added:
            self.status_label.setText(f"{len(self._items)} photos in the collage.")
            self._sync_enabled()
            self._request_preview()

    def _detect_faces(self, path: Path) -> tuple[FaceBox, ...]:
        try:
            if self._face_detector is None:
                from core.face_detector import FaceDetector

                self._face_detector = FaceDetector()
            return tuple(self._face_detector.detect(path).regions)
        except Exception as exc:  # noqa: BLE001 - faces are a nice-to-have
            logger.info("Collage: face detection unavailable/failed (%s).", exc)
            return ()

    def _selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.photo_list.selectedIndexes())

    def _on_remove_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            if 0 <= row < len(self._items):
                del self._items[row]
                self.photo_list.takeItem(row)
        self.status_label.setText(f"{len(self._items)} photos in the collage.")
        self._sync_enabled()
        self._request_preview()

    def _on_clear(self) -> None:
        self._items.clear()
        self.photo_list.clear()
        self._rendered = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Add photos to start your collage.")
        self.status_label.setText("No photos yet.")
        self.warning_label.setText("")
        self._sync_enabled()

    def _move_selected(self, delta: int) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        order = rows if delta < 0 else list(reversed(rows))
        moved: list[int] = []
        for row in order:
            target = row + delta
            if target < 0 or target >= len(self._items):
                moved.append(row)
                continue
            self._items[row], self._items[target] = self._items[target], self._items[row]
            item = self.photo_list.takeItem(row)
            self.photo_list.insertItem(target, item)
            moved.append(target)
        self.photo_list.clearSelection()
        for row in moved:
            self.photo_list.item(row).setSelected(True)
        self._request_preview()

    # ------------------------------------------------------------------ #
    # Per-photo adjustments
    # ------------------------------------------------------------------ #
    def _current_item(self) -> Optional[_CollageItem]:
        row = self.photo_list.currentRow()
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def _on_photo_selected(self, row: int) -> None:
        """Load the selected photo's adjustments into the Photo tab."""
        item = self._current_item()
        if item is None:
            self.selected_label.setText("Select a photo in the Photos tab.")
            return
        self.selected_label.setText(f"Adjusting: {item.name}")
        adjust = item.adjust
        for key, value in (
            ("zoom", round(adjust.zoom * 100)),
            ("pan_x", round(adjust.offset_x * 100) + 50),
            ("pan_y", round(adjust.offset_y * 100) + 50),
            ("rotate", int(adjust.rotate_deg) % 360),
        ):
            slider = self.sliders[key]
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            self.slider_labels[key].setText(str(slider.value()))
        self.photo_filter.blockSignals(True)
        self.photo_filter.setCurrentText(adjust.filter_name)
        self.photo_filter.blockSignals(False)
        self.photo_beautify.blockSignals(True)
        self.photo_beautify.setChecked(adjust.beautify)
        self.photo_beautify.blockSignals(False)

    def _on_photo_adjust_changed(self, *_args) -> None:
        """Write the Photo tab's controls back onto the selected photo."""
        item = self._current_item()
        if item is None or self._loading_preset:
            return
        item.adjust = PhotoAdjust(
            zoom=max(1.0, self.sliders["zoom"].value() / 100.0),
            offset_x=(self.sliders["pan_x"].value() - 50) / 100.0,
            offset_y=(self.sliders["pan_y"].value() - 50) / 100.0,
            rotate_deg=float(self.sliders["rotate"].value()),
            filter_name=self.photo_filter.currentData(),
            beautify=self.photo_beautify.isChecked(),
        )
        self._request_preview()

    def _on_apply_adjust_to_all(self) -> None:
        item = self._current_item()
        if item is None:
            QMessageBox.information(self, "Collage", "Select a photo first.")
            return
        for other in self._items:
            other.adjust = item.adjust
        self.status_label.setText(f"Applied {item.name}'s adjustments to all photos.")
        self._request_preview()

    def _on_reset_adjust(self) -> None:
        item = self._current_item()
        if item is None:
            return
        item.adjust = PhotoAdjust()
        self._on_photo_selected(self.photo_list.currentRow())
        self._request_preview()

    # ------------------------------------------------------------------ #
    # Settings -> core objects
    # ------------------------------------------------------------------ #
    def _current_spec(self, for_preview: bool) -> CollageSpec:
        width, height = SIZE_PRESETS[self.size_choice.currentData()]
        if not for_preview:
            return CollageSpec(width_px=width, height_px=height)
        longest = max(width, height)
        if longest <= _PREVIEW_CANVAS_MAX_DIM:
            return CollageSpec(width_px=width, height_px=height)
        scale = _PREVIEW_CANVAS_MAX_DIM / longest
        return CollageSpec(
            width_px=max(2, round(width * scale)), height_px=max(2, round(height * scale))
        )

    def _current_theme(self) -> CollageTheme:
        base = THEMES[self.theme_choice.currentData()]
        return dataclasses.replace(
            base,
            spacing_frac=self.sliders["spacing"].value() / 1000.0,
            border_px_frac=self.sliders["border"].value() / 1000.0,
            corner_radius_frac=self.sliders["corner"].value() / 1000.0,
        )

    def _current_background(self) -> Background:
        raw_path = self.bg_image_path.text().strip()
        return Background(
            style=self.bg_style.currentData(),
            color=self.bg_color.color(),
            color2=self.bg_color2.color(),
            gradient_vertical=self.bg_vertical.isChecked(),
            image_path=Path(raw_path) if raw_path else None,
            darken=self.sliders["darken"].value() / 100.0,
        )

    def _current_layout(self, photos) -> str:
        chosen = self.layout_choice.currentData()
        if chosen:
            return chosen
        return suggest_layout(photos, self._current_spec(for_preview=True))

    def _current_shape(self) -> tuple[Optional[str], str]:
        shape = self.shape_choice.currentData()
        if shape == SHAPE_NONE:
            return None, ""
        return shape, self.shape_text.text().strip()

    def _current_text_overlays(self) -> list[TextOverlay]:
        text = self.title_text.text().strip()
        if not text:
            return []
        return [
            TextOverlay(
                text=text.replace("\\n", "\n"),
                position=self.title_position.currentData(),
                size_frac=self.sliders["title_size"].value() / 1000.0,
                color=self.title_color.color(),
                font_role=self.title_font.currentData(),
                shadow=self.title_shadow.isChecked(),
            )
        ]

    def _current_watermark(self) -> Optional[Watermark]:
        raw = self.logo_path.text().strip()
        if not raw:
            return None
        return Watermark(
            image_path=Path(raw),
            position=self.logo_position.currentData(),
            width_frac=max(0.01, self.sliders["logo_width"].value() / 100.0),
            opacity=self.sliders["logo_opacity"].value(),
        )

    def _current_marks(self) -> PrintMarks:
        return PrintMarks(
            bleed_frac=self.sliders["bleed"].value() / 1000.0,
            trim_marks=self.trim_marks.isChecked(),
        )

    def _preview_photos(self) -> list[CollagePhoto]:
        return [
            CollagePhoto(
                image=item.preview, face_boxes=item.face_boxes,
                path=item.path, adjust=item.adjust,
            )
            for item in self._items
        ]

    # ------------------------------------------------------------------ #
    # Reacting to controls
    # ------------------------------------------------------------------ #
    def _on_shape_changed(self, *_args) -> None:
        self.shape_text.setEnabled(self.shape_choice.currentData() == SHAPE_TEXT)
        self._request_preview()

    def _on_bg_style_changed(self, *_args) -> None:
        style = self.bg_style.currentData()
        self.bg_color.setEnabled(style in (BG_SOLID, BG_GRADIENT))
        self.bg_color2.setEnabled(style == BG_GRADIENT)
        self.bg_vertical.setEnabled(style == BG_GRADIENT)
        self.bg_image_path.setEnabled(style == BG_IMAGE)
        self.sliders["darken"].setEnabled(style in (BG_IMAGE, BG_BLURRED_PHOTO))
        self._request_preview()

    def _on_pick_background_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a background image", "", "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if path:
            self.bg_image_path.setText(path)
            self.bg_style.setCurrentIndex(self.bg_style.findData(BG_IMAGE))
            self._request_preview()

    def _on_pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a logo", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            self.logo_path.setText(path)
            self._request_preview()

    def _on_clear_logo(self) -> None:
        self.logo_path.clear()
        self._request_preview()

    def _on_slider_changed(self, _value: int) -> None:
        for key, slider in self.sliders.items():
            self.slider_labels[key].setText(str(slider.value()))
        # Photo-tab sliders belong to the selected photo, not the whole collage.
        if any(
            self.sliders[k].hasFocus() for k in ("zoom", "pan_x", "pan_y", "rotate")
        ):
            self._on_photo_adjust_changed()
            return
        self._request_preview()

    def _on_shuffle(self) -> None:
        if not self._items:
            return
        self._seed = random.randrange(1, 1_000_000)
        random.shuffle(self._items)
        self.photo_list.clear()
        for item in self._items:
            self.photo_list.addItem(item.name)
        self._request_preview()

    def _sync_enabled(self) -> None:
        has = bool(self._items)
        for button in (
            self.btn_export, self.btn_shuffle, self.btn_remove,
            self.btn_clear, self.btn_up, self.btn_down,
        ):
            button.setEnabled(has)

    def _request_preview(self, *_args) -> None:
        if self._items and not self._loading_preset:
            self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    # ------------------------------------------------------------------ #
    # Preview
    # ------------------------------------------------------------------ #
    def _render_preview(self) -> None:
        if not self._items:
            return
        photos = self._preview_photos()
        layout = self._current_layout(photos)
        shape, shape_text = self._current_shape()
        try:
            image = build_collage(
                photos,
                self._current_spec(for_preview=True),
                self._current_theme(),
                layout=layout,
                seed=self._seed,
                background=self._current_background(),
                shape=shape,
                shape_text=shape_text,
                text_overlays=self._current_text_overlays(),
                watermark=self._current_watermark(),
                marks=self._current_marks(),
            )
        except Exception as exc:  # noqa: BLE001 - keep the UI alive
            logger.warning("Collage preview failed: %s", exc)
            self.status_label.setText(f"Could not build collage: {exc}")
            return

        self._rendered = image
        self.preview_label.setText("")
        self.preview_label.setPixmap(_pil_to_qpixmap(image))
        export_spec = self._current_spec(for_preview=False)
        self.status_label.setText(
            f"{len(self._items)} photos — {layout} layout, "
            f"exports at {export_spec.width_px}x{export_spec.height_px}px."
        )
        self._refresh_print_report(photos, layout, export_spec)

    def _refresh_print_report(self, photos, layout: str, export_spec: CollageSpec) -> None:
        """
        Warn about photos that will be soft at the export size.

        Uses each photo's *original* pixel size (not the downscaled preview
        copy), since that's what export will actually use.
        """
        try:
            cells = layout_cells(photos, export_spec, self._current_theme(), layout=layout)
            full_size_photos = [
                CollagePhoto(
                    image=Image.new("RGB", item.full_size), path=item.path
                )
                for item in self._items
            ]
            warnings = check_resolution(full_size_photos, cells, export_spec)
        except Exception as exc:  # noqa: BLE001 - reporting must never break preview
            logger.debug("Collage: resolution check failed (%s).", exc)
            return

        if not warnings:
            self.warning_label.setText("")
            self.print_report.setText("All photos have enough resolution for this size.")
            return
        summary = f"{len(warnings)} photo(s) may look soft at this size."
        self.warning_label.setText(summary)
        self.print_report.setText(
            summary + "\n\n" + "\n\n".join(w.message for w in warnings[:6])
        )

    # ------------------------------------------------------------------ #
    # Presets
    # ------------------------------------------------------------------ #
    def _refresh_preset_list(self) -> None:
        self.preset_choice.blockSignals(True)
        self.preset_choice.clear()
        self.preset_choice.addItem("(none)", None)
        try:
            for preset in load_presets():
                self.preset_choice.addItem(preset.name, preset.name)
        except PresetError as exc:
            logger.warning("Collage: could not load presets (%s).", exc)
        self.preset_choice.blockSignals(False)

    def _gather_preset(self, name: str) -> CollagePreset:
        shape, shape_text = self._current_shape()
        return CollagePreset(
            name=name,
            layout=self.layout_choice.currentData(),
            theme=self.theme_choice.currentData(),
            size_preset=self.size_choice.currentData(),
            spacing=self.sliders["spacing"].value(),
            border=self.sliders["border"].value(),
            corner=self.sliders["corner"].value(),
            background_style=self.bg_style.currentData(),
            background_color=self.bg_color.color(),
            background_color2=self.bg_color2.color(),
            background_darken=self.sliders["darken"].value() / 100.0,
            shape=shape or SHAPE_NONE,
            shape_text=shape_text,
            title=self.title_text.text(),
            title_position=self.title_position.currentData(),
            title_size=self.sliders["title_size"].value() / 1000.0,
            title_color=self.title_color.color(),
            bleed_frac=self.sliders["bleed"].value() / 1000.0,
            trim_marks=self.trim_marks.isChecked(),
        )

    def apply_preset(self, preset: CollagePreset) -> None:
        """
        Push a preset's values into every control.

        ``_loading_preset`` suppresses the per-control re-render so applying a
        preset costs one render at the end, not a dozen.
        """
        self._loading_preset = True
        try:
            index = self.layout_choice.findData(preset.layout)
            self.layout_choice.setCurrentIndex(max(0, index))
            if preset.theme in THEMES:
                self.theme_choice.setCurrentText(preset.theme)
            if preset.size_preset in SIZE_PRESETS:
                self.size_choice.setCurrentText(preset.size_preset)
            self.sliders["spacing"].setValue(preset.spacing)
            self.sliders["border"].setValue(preset.border)
            self.sliders["corner"].setValue(preset.corner)

            bg_index = self.bg_style.findData(preset.background_style)
            self.bg_style.setCurrentIndex(max(0, bg_index))
            self.bg_color.set_color(preset.background_color)
            self.bg_color2.set_color(preset.background_color2)
            self.sliders["darken"].setValue(round(preset.background_darken * 100))

            shape_index = self.shape_choice.findData(preset.shape)
            self.shape_choice.setCurrentIndex(max(0, shape_index))
            self.shape_text.setText(preset.shape_text)

            self.title_text.setText(preset.title)
            pos_index = self.title_position.findData(preset.title_position)
            self.title_position.setCurrentIndex(max(0, pos_index))
            self.sliders["title_size"].setValue(round(preset.title_size * 1000))
            self.title_color.set_color(preset.title_color)

            self.sliders["bleed"].setValue(round(preset.bleed_frac * 1000))
            self.trim_marks.setChecked(preset.trim_marks)
        finally:
            self._loading_preset = False
        self._on_shape_changed()
        self._on_bg_style_changed()
        self._request_preview()

    def _on_apply_preset(self, _index: int) -> None:
        name = self.preset_choice.currentData()
        if not name:
            return
        for preset in load_presets():
            if preset.name == name:
                self.apply_preset(preset)
                self.status_label.setText(f"Applied preset '{name}'.")
                return

    def _on_save_preset(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        try:
            upsert_preset(self._gather_preset(name))
        except PresetError as exc:
            QMessageBox.critical(self, "Collage", str(exc))
            return
        self._refresh_preset_list()
        self.preset_choice.setCurrentIndex(self.preset_choice.findData(name))
        self.status_label.setText(f"Saved preset '{name}'.")

    def _on_delete_preset(self) -> None:
        name = self.preset_choice.currentData()
        if not name:
            return
        try:
            delete_preset(name)
        except PresetError as exc:
            QMessageBox.critical(self, "Collage", str(exc))
            return
        self._refresh_preset_list()
        self.status_label.setText(f"Deleted preset '{name}'.")

    # ------------------------------------------------------------------ #
    # Auto-build
    # ------------------------------------------------------------------ #
    def _on_pick_auto_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of photos")
        if folder:
            self.auto_folder.setText(folder)

    def _on_auto_build(self) -> None:
        folder = self.auto_folder.text().strip()
        if not folder:
            QMessageBox.information(self, "Collage", "Choose a folder first.")
            return

        from core.collage_auto import CollageAutoError, select_best_photos

        self.status_label.setText("Scoring photos…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            chosen = select_best_photos(
                folder,
                count=self.auto_count.value(),
                require_faces=self.auto_faces.isChecked(),
                spread=self.auto_spread.isChecked(),
                limit=self.auto_limit.value(),
            )
        except CollageAutoError as exc:
            QMessageBox.warning(self, "Collage", str(exc))
            self.status_label.setText("Auto-build failed.")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-build failed: %s", exc)
            QMessageBox.critical(self, "Collage", f"Auto-build failed:\n{exc}")
            self.status_label.setText("Auto-build failed.")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._on_clear()
        self.add_photos([str(p.path) for p in chosen])
        self.status_label.setText(
            f"Picked the best {len(chosen)} of the photos in that folder."
        )

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export_photos(self) -> list[CollagePhoto]:
        """Re-read originals at full resolution for the final render."""
        photos: list[CollagePhoto] = []
        for item in self._items:
            try:
                with Image.open(item.path) as opened:
                    opened.load()
                    full = opened.convert("RGB")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Collage export: re-reading '%s' failed (%s); using preview copy.",
                    item.path, exc,
                )
                full = item.preview
            photos.append(
                CollagePhoto(
                    image=full, face_boxes=item.face_boxes,
                    path=item.path, adjust=item.adjust,
                )
            )
        return photos

    def _on_export(self) -> None:
        if not self._items:
            QMessageBox.information(self, "Collage", "Add some photos first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export collage", "collage.jpg",
            "JPEG (*.jpg);;PNG (*.png);;PDF (*.pdf)",
        )
        if not path:
            return

        spec = self._current_spec(for_preview=False)
        shape, shape_text = self._current_shape()
        self.status_label.setText("Rendering collage at full resolution…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            photos = self._export_photos()
            image = build_collage(
                photos, spec, self._current_theme(),
                layout=self._current_layout(photos),
                seed=self._seed,
                background=self._current_background(),
                shape=shape,
                shape_text=shape_text,
                text_overlays=self._current_text_overlays(),
                watermark=self._current_watermark(),
                marks=self._current_marks(),
            )
            out = save_collage(image, path, dpi=spec.dpi)
        except CollageError as exc:
            QMessageBox.critical(self, "Collage", str(exc))
            self.status_label.setText("Export failed.")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Collage export failed: %s", exc)
            QMessageBox.critical(self, "Collage", f"Export failed:\n{exc}")
            self.status_label.setText("Export failed.")
            return
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Collage", f"Saved collage to:\n{out}")
        self.status_label.setText(f"Saved {out.name} ({spec.width_px}x{spec.height_px}px).")
