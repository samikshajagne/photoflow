"""
Passport / ID photo generator view.

Replaces the manual studio workflow of cropping a portrait to a standard
passport size in an image editor, then hand-copying it into a grid on a
print sheet. This widget: loads a photo, auto-crops it to a chosen passport
size (centered on the detected face, adjustable by dragging), and exports a
tiled print sheet ready to hand to a lab.

Standalone tool: it does not require an analyzed PhotoFlow folder, and keeps
its own photo choice independent of the main grid (a photo can also be handed
to it via :meth:`PassportPhotoView.load_photo` from the main window, e.g. the
currently selected photo, but that wiring is optional).

Combining multiple people on one sheet: studios often print two or three
different people's passport photos on a single sheet (e.g. a family
submitting together) rather than wasting a whole sheet per person. The
"People on this sheet" list lets each added photo keep its own crop and copy
count while sharing one physical sheet; :meth:`_build_current_sheet` combines
them via :func:`core.passport_photo.build_multi_sheet`. A photo that is
loaded but never explicitly added is still included as an implicit trailing
entry, so the original one-photo-fills-the-sheet flow needs no extra clicks.

Enhance faces: an optional, global "beautify" pass (skin smoothing,
brightness/color auto-correct, background whitening, teeth/eye whitening --
see :mod:`core.face_beautify`) that applies the same intensities to every
photo on the sheet. One "Enhance faces" checkbox turns it on/off; turning it
on the first time seeds sensible default intensities onto the four sliders,
which can then be dialed up/down per studio taste.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.face_beautify import BeautifyOptions, beautify
from core.passport_photo import (
    PASSPORT_SIZES,
    SHEET_SIZES,
    FaceBox,
    PassportPhotoError,
    PassportPhotoSpec,
    SheetEntry,
    SheetSpec,
    auto_crop_box,
    build_multi_sheet,
    compute_grid,
    crop_and_resize,
    max_copies,
    save_sheet,
)
from utils.logger import get_logger

logger = get_logger("ui_qt.passport_photo_view")

_CUSTOM = "Custom…"
_HANDLE_SIZE = 10.0
_MIN_CROP_PX = 20.0
_PREVIEW_MAX_W = 220
_MAX_VIEW_DIM = 900  # scene image is downscaled to at most this on the long edge
_SHEET_PREVIEW_MAX_DIM = 720  # sheet-preview dialog image is scaled to at most this
# Live preview is computed from a copy of the source downscaled to at most
# this on the long edge -- see PassportPhotoView._preview_source.
_PREVIEW_SOURCE_MAX_DIM = 900
# How long to wait for a drag to settle before recomputing the preview.
_PREVIEW_DEBOUNCE_MS = 120

# Defaults matching this studio's day-to-day settings (not a named preset --
# picked so the tool opens ready to go without the user reconfiguring it
# every time): a 30x35mm custom photo size and a thin 0.3mm cutting-guide
# border, tiled on a 4x6in sheet (the first SHEET_SIZES entry, already the
# default via _apply_sheet_preset(0)).
_DEFAULT_CUSTOM_WIDTH_MM = 30.0
_DEFAULT_CUSTOM_HEIGHT_MM = 35.0
_DEFAULT_STROKE_MM = 0.3

# Slider keys, in display order -- match core.face_beautify.BeautifyOptions'
# field names so slider values can be passed straight through as kwargs.
_BEAUTIFY_SLIDERS: tuple[tuple[str, str], ...] = (
    ("skin_smooth", "Skin smoothing"),
    ("auto_correct", "Brightness/color"),
    ("background_whiten", "Background whiten"),
    ("teeth_eye_whiten", "Teeth/eye whiten"),
)


@dataclasses.dataclass
class _PersonEntry:
    """One photo added to the "People on this sheet" list."""

    path: Path
    image: Image.Image  # full source image (RGB)
    face_box: Optional[FaceBox]
    crop_box: tuple[int, int, int, int]  # in this image's own pixel coords
    copies: int

    @property
    def name(self) -> str:
        return self.path.stem or str(self.path)


# --------------------------------------------------------------------------- #
# Image <-> Qt conversion
# --------------------------------------------------------------------------- #
def _pil_to_qpixmap(image: Image.Image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    # .copy() forces a deep copy so the pixmap survives after `data` (and its
    # backing bytes) go out of scope.
    return QPixmap.fromImage(qimg.copy())


# --------------------------------------------------------------------------- #
# Interactive, aspect-locked crop rectangle
# --------------------------------------------------------------------------- #
class _Handle(QGraphicsRectItem):
    """A small draggable square at one corner of a :class:`_CropRectItem`."""

    _CURSORS = {
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
    }

    def __init__(self, owner: "_CropRectItem", corner: str) -> None:
        half = _HANDLE_SIZE / 2
        super().__init__(-half, -half, _HANDLE_SIZE, _HANDLE_SIZE, owner)
        self._owner = owner
        self._corner = corner
        self.setBrush(QColor(255, 255, 255))
        self.setPen(QPen(QColor(30, 144, 255), 1))
        self.setCursor(self._CURSORS[corner])
        self.setZValue(10)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._owner.resize_from_handle(self._corner, event.scenePos())
        event.accept()


class _CropRectItem(QGraphicsRectItem):
    """
    A movable, aspect-locked crop rectangle over the loaded photo.

    ``rect()`` always holds the crop box in scene (image-pixel) coordinates;
    the item's own position stays at ``(0, 0)`` so rect <-> image-pixel math
    never needs a pos offset.
    """

    def __init__(self, rect: QRectF, aspect: float, bounds: QRectF, on_change=None) -> None:
        super().__init__(rect)
        self.aspect = aspect
        self.bounds = bounds
        self.on_change = on_change
        self.setPen(QPen(QColor(30, 144, 255), 2))
        self.setBrush(QColor(30, 144, 255, 40))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_anchor: Optional[QRectF] = None
        self._handles = {c: _Handle(self, c) for c in ("tl", "tr", "bl", "br")}
        self._reposition_handles()

    # -- moving the whole box ------------------------------------------------
    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_anchor = QRectF(self.rect())
        self._drag_start = event.scenePos()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_anchor is None:
            return
        delta = event.scenePos() - self._drag_start
        moved = self._drag_anchor.translated(delta.x(), delta.y())
        moved = self._clamp_translate(moved)
        self.set_crop_rect(moved)
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_anchor = None
        event.accept()

    def _clamp_translate(self, rect: QRectF) -> QRectF:
        x = min(max(rect.x(), self.bounds.left()), self.bounds.right() - rect.width())
        y = min(max(rect.y(), self.bounds.top()), self.bounds.bottom() - rect.height())
        return QRectF(x, y, rect.width(), rect.height())

    # -- resizing via a corner handle ----------------------------------------
    def resize_from_handle(self, corner: str, scene_pos) -> None:
        r = self.rect()
        p = scene_pos
        if corner == "br":
            anchor_x, anchor_y = r.left(), r.top()
            w = max(_MIN_CROP_PX, p.x() - anchor_x)
            h = w / self.aspect
            new_rect = QRectF(anchor_x, anchor_y, w, h)
        elif corner == "tl":
            anchor_x, anchor_y = r.right(), r.bottom()
            w = max(_MIN_CROP_PX, anchor_x - p.x())
            h = w / self.aspect
            new_rect = QRectF(anchor_x - w, anchor_y - h, w, h)
        elif corner == "tr":
            anchor_x, anchor_y = r.left(), r.bottom()
            w = max(_MIN_CROP_PX, p.x() - anchor_x)
            h = w / self.aspect
            new_rect = QRectF(anchor_x, anchor_y - h, w, h)
        else:  # "bl"
            anchor_x, anchor_y = r.right(), r.top()
            w = max(_MIN_CROP_PX, anchor_x - p.x())
            h = w / self.aspect
            new_rect = QRectF(anchor_x - w, anchor_y, w, h)

        new_rect = self._clamp_resize(new_rect, corner)
        self.set_crop_rect(new_rect)

    def _clamp_resize(self, rect: QRectF, corner: str) -> QRectF:
        """Shrink (keeping aspect + the fixed anchor corner) so it fits ``bounds``."""
        max_w = self.bounds.width()
        max_h = self.bounds.height()
        scale = min(1.0, max_w / rect.width() if rect.width() else 1.0,
                    max_h / rect.height() if rect.height() else 1.0)
        w = max(_MIN_CROP_PX, rect.width() * scale)
        h = w / self.aspect
        if corner == "br":
            x, y = rect.left(), rect.top()
        elif corner == "tl":
            x, y = rect.right() - w, rect.bottom() - h
        elif corner == "tr":
            x, y = rect.left(), rect.bottom() - h
        else:
            x, y = rect.right() - w, rect.top()
        x = min(max(x, self.bounds.left()), self.bounds.right() - w)
        y = min(max(y, self.bounds.top()), self.bounds.bottom() - h)
        return QRectF(x, y, w, h)

    # -- shared plumbing ------------------------------------------------------
    def set_crop_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self.setRect(rect)
        self._reposition_handles()
        if self.on_change is not None:
            self.on_change()

    def _reposition_handles(self) -> None:
        r = self.rect()
        self._handles["tl"].setPos(r.topLeft())
        self._handles["tr"].setPos(r.topRight())
        self._handles["bl"].setPos(r.bottomLeft())
        self._handles["br"].setPos(r.bottomRight())

    def crop_box(self) -> tuple[int, int, int, int]:
        r = self.rect()
        return (round(r.left()), round(r.top()), round(r.right()), round(r.bottom()))


# --------------------------------------------------------------------------- #
# Main view
# --------------------------------------------------------------------------- #
class PassportPhotoView(QWidget):
    """Load a photo, crop it to a passport size, and export a tiled print sheet."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._image: Optional[Image.Image] = None
        self._image_path: Optional[Path] = None
        self._face_box: Optional[FaceBox] = None
        self._face_detector = None  # lazily created; may stay None if unavailable
        self._crop_item: Optional[_CropRectItem] = None
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        # People combined onto one sheet, and which one (if any) the canvas
        # is currently showing/editing. `None` means the loaded photo (if
        # any) hasn't been added yet -- see the module docstring.
        self._people: list[_PersonEntry] = []
        self._current_index: Optional[int] = None
        # Downscaled copy of the loaded photo used only for the live preview
        # thumbnail; invalidated whenever a different photo is displayed.
        self._preview_source_cache: Optional[Image.Image] = None
        # Both preview variants are cached so "Hold to see original" swaps
        # between them with no recompute during the gesture.
        self._preview_raw_pixmap: Optional[QPixmap] = None
        self._preview_enhanced_pixmap: Optional[QPixmap] = None
        self._comparing = False  # True while the compare button is held down

        # Single-shot, restartable: coalesces bursts of crop/slider changes
        # into one preview recompute (see _request_preview_update).
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)

        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene)
        self._view.setMinimumSize(420, 420)
        self._view.setRenderHints(self._view.renderHints())

        root = QHBoxLayout(self)
        root.addWidget(self._build_controls(), 0)
        root.addWidget(self._build_canvas(), 1)

        self._refresh_sheet_status()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_canvas(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.btn_choose = QPushButton("Choose Photo…")
        self.btn_choose.clicked.connect(self._on_choose_photo)
        self.btn_auto_crop = QPushButton("Auto-Crop (Detect Face)")
        self.btn_auto_crop.clicked.connect(self._on_auto_crop)
        self.btn_auto_crop.setEnabled(False)
        row.addWidget(self.btn_choose)
        row.addWidget(self.btn_auto_crop)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(self._view, 1)

        self.status_label = QLabel("Choose a photo to begin.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return container

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)

        # -- Photo size ------------------------------------------------------
        size_box = QGroupBox("Photo size")
        form = QFormLayout(size_box)

        self.size_preset = QComboBox()
        for name in PASSPORT_SIZES:
            self.size_preset.addItem(name)
        self.size_preset.addItem(_CUSTOM)
        self.size_preset.currentIndexChanged.connect(self._on_size_changed)
        form.addRow("Standard:", self.size_preset)

        self.width_mm = QDoubleSpinBox()
        self.width_mm.setRange(5.0, 300.0)
        self.width_mm.setSuffix(" mm")
        self.height_mm = QDoubleSpinBox()
        self.height_mm.setRange(5.0, 300.0)
        self.height_mm.setSuffix(" mm")
        self.width_mm.valueChanged.connect(self._on_size_changed)
        self.height_mm.valueChanged.connect(self._on_size_changed)
        form.addRow("Width:", self.width_mm)
        form.addRow("Height:", self.height_mm)

        self.dpi = QComboBox()
        for d in (150, 300, 600):
            self.dpi.addItem(f"{d} dpi", d)
        self.dpi.setCurrentIndex(1)
        self.dpi.currentIndexChanged.connect(self._on_size_changed)
        form.addRow("Resolution:", self.dpi)

        outer.addWidget(size_box)
        # Default to Custom at this studio's usual 30x35mm rather than the
        # first named preset -- see the module-level comment above. Signals
        # are blocked while setting this up because the cascade
        # (_on_size_changed -> _refresh_sheet_status) reaches into the
        # print-sheet controls, which don't exist yet at this point.
        self.width_mm.blockSignals(True)
        self.height_mm.blockSignals(True)
        self.width_mm.setValue(_DEFAULT_CUSTOM_WIDTH_MM)
        self.height_mm.setValue(_DEFAULT_CUSTOM_HEIGHT_MM)
        self.width_mm.blockSignals(False)
        self.height_mm.blockSignals(False)
        custom_index = self.size_preset.count() - 1
        self.size_preset.blockSignals(True)
        self.size_preset.setCurrentIndex(custom_index)
        self.size_preset.blockSignals(False)
        self._apply_preset(custom_index)  # enables the width/height fields

        # -- Print sheet -------------------------------------------------------
        sheet_box = QGroupBox("Print sheet")
        sform = QFormLayout(sheet_box)

        self.sheet_preset = QComboBox()
        for name in SHEET_SIZES:
            self.sheet_preset.addItem(name)
        self.sheet_preset.addItem(_CUSTOM)
        self.sheet_preset.currentIndexChanged.connect(self._on_sheet_preset_changed)
        sform.addRow("Sheet size:", self.sheet_preset)

        self.sheet_width_in = QDoubleSpinBox()
        self.sheet_width_in.setRange(1.0, 40.0)
        self.sheet_width_in.setSuffix(" in")
        self.sheet_height_in = QDoubleSpinBox()
        self.sheet_height_in.setRange(1.0, 40.0)
        self.sheet_height_in.setSuffix(" in")
        self.sheet_width_in.valueChanged.connect(self._refresh_sheet_status)
        self.sheet_height_in.valueChanged.connect(self._refresh_sheet_status)
        sform.addRow("Width:", self.sheet_width_in)
        sform.addRow("Height:", self.sheet_height_in)

        self.margin_in = QDoubleSpinBox()
        self.margin_in.setRange(0.0, 2.0)
        self.margin_in.setDecimals(2)
        self.margin_in.setSingleStep(0.05)
        self.margin_in.setValue(0.1)
        self.margin_in.setSuffix(" in")
        self.margin_in.valueChanged.connect(self._refresh_sheet_status)
        sform.addRow("Margin:", self.margin_in)

        self.spacing_in = QDoubleSpinBox()
        self.spacing_in.setRange(0.0, 1.0)
        self.spacing_in.setDecimals(2)
        self.spacing_in.setSingleStep(0.05)
        self.spacing_in.setValue(0.05)
        self.spacing_in.setSuffix(" in")
        self.spacing_in.valueChanged.connect(self._refresh_sheet_status)
        sform.addRow("Spacing:", self.spacing_in)

        self.fill_auto = QCheckBox("Fill sheet automatically")
        self.fill_auto.setChecked(True)
        self.fill_auto.toggled.connect(self._on_fill_auto_toggled)
        sform.addRow("", self.fill_auto)

        self.copies = QSpinBox()
        self.copies.setRange(0, 200)
        self.copies.setEnabled(False)
        self.copies.valueChanged.connect(self._on_copies_changed)
        sform.addRow("Copies:", self.copies)

        self.stroke_enabled = QCheckBox("Cutting-guide border")
        self.stroke_enabled.setChecked(True)
        self.stroke_enabled.toggled.connect(self._on_stroke_toggled)
        sform.addRow("", self.stroke_enabled)

        self.stroke_width_mm = QDoubleSpinBox()
        self.stroke_width_mm.setRange(0.1, 20.0)
        self.stroke_width_mm.setDecimals(1)
        self.stroke_width_mm.setSingleStep(0.1)
        self.stroke_width_mm.setValue(_DEFAULT_STROKE_MM)
        self.stroke_width_mm.setSuffix(" mm")
        sform.addRow("Border width:", self.stroke_width_mm)

        outer.addWidget(sheet_box)
        self._apply_sheet_preset(0)

        self.sheet_status = QLabel("")
        self.sheet_status.setWordWrap(True)
        outer.addWidget(self.sheet_status)

        # -- People on this sheet ---------------------------------------------
        # Lets two or three different people's photos share one sheet (e.g. a
        # family submitting together), each keeping its own crop + copy count.
        people_box = QGroupBox("People on this sheet")
        pform = QVBoxLayout(people_box)

        self.people_list = QListWidget()
        self.people_list.setMaximumHeight(110)
        self.people_list.currentRowChanged.connect(self._on_people_row_changed)
        pform.addWidget(self.people_list)

        # Stacked rather than side-by-side: at this panel's width, two buttons
        # sharing one row squeeze "Add Current Photo" until its label clips.
        self.btn_add_to_sheet = QPushButton("Add Current Photo")
        self.btn_add_to_sheet.clicked.connect(self._on_add_to_sheet)
        self.btn_add_to_sheet.setEnabled(False)
        pform.addWidget(self.btn_add_to_sheet)

        self.btn_remove_person = QPushButton("Remove")
        self.btn_remove_person.clicked.connect(self._on_remove_selected)
        self.btn_remove_person.setEnabled(False)
        pform.addWidget(self.btn_remove_person)

        self.people_status = QLabel(
            "No one added yet — the current photo alone will fill the sheet."
        )
        self.people_status.setWordWrap(True)
        pform.addWidget(self.people_status)

        outer.addWidget(people_box)

        # -- Enhance faces (global beautification) --------------------------
        # One set of intensities applied to *every* photo on the sheet (not
        # per-person) -- see core.face_beautify for what each effect does.
        beautify_box = QGroupBox("Enhance faces")
        bform = QVBoxLayout(beautify_box)

        self.beautify_enabled = QCheckBox("Enhance faces for everyone on this sheet")
        self.beautify_enabled.toggled.connect(self._on_beautify_toggled)
        bform.addWidget(self.beautify_enabled)

        self.beautify_sliders: dict[str, QSlider] = {}
        self.beautify_value_labels: dict[str, QLabel] = {}
        for key, title in _BEAUTIFY_SLIDERS:
            row = QHBoxLayout()
            label = QLabel(title + ":")
            label.setMinimumWidth(108)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setEnabled(False)
            slider.valueChanged.connect(self._on_beautify_slider_changed)
            value_label = QLabel("0%")
            value_label.setMinimumWidth(34)
            row.addWidget(label)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            bform.addLayout(row)
            self.beautify_sliders[key] = slider
            self.beautify_value_labels[key] = value_label

        outer.addWidget(beautify_box)

        # -- Preview + export -----------------------------------------------
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(140)
        self.preview_label.setStyleSheet("background:#202124; border-radius:4px;")
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.preview_caption = QLabel("Cropped preview:")
        outer.addWidget(self.preview_caption)
        outer.addWidget(self.preview_label)

        # Press-and-hold to peek at the un-enhanced crop. Disabled until
        # there's an actual difference to show (see _sync_compare_button).
        self.btn_compare = QPushButton("Hold to see original")
        self.btn_compare.setEnabled(False)
        self.btn_compare.pressed.connect(self._on_compare_pressed)
        self.btn_compare.released.connect(self._on_compare_released)
        outer.addWidget(self.btn_compare)

        self.btn_preview_sheet = QPushButton("Preview Sheet…")
        self.btn_preview_sheet.clicked.connect(self._on_preview_sheet)
        self.btn_preview_sheet.setEnabled(False)
        outer.addWidget(self.btn_preview_sheet)

        self.btn_export = QPushButton("Export Sheet…")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        outer.addWidget(self.btn_export)

        outer.addStretch(1)

        # The panel's content (Photo size + Print sheet + People-on-this-sheet
        # + preview + export) can need more vertical space than a shorter
        # screen has available -- e.g. a 1366x768 laptop display, where a
        # *maximized* window is capped to the screen's work-area height. With
        # no scroll area, Qt was squeezing every row to fit, which visually
        # overlapped labels and fields into unreadable garbage. Wrapping the
        # panel in a scroll area lets it scroll instead of squashing.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(380)
        scroll.setWidget(panel)
        return scroll

    # ------------------------------------------------------------------ #
    # Photo loading
    # ------------------------------------------------------------------ #
    def _on_choose_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a photo", "", "Images (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)"
        )
        if path:
            self.load_photo(path)

    def load_photo(self, path: str | Path) -> None:
        """Load ``path`` as a *new* (not-yet-added) source photo and auto-crop it."""
        path = Path(path)
        try:
            image = Image.open(path)
            image.load()
            image = image.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - surface any load failure to the user
            logger.warning("Passport photo: failed to load '%s': %s", path, exc)
            QMessageBox.critical(self, "Passport Photos", f"Could not open photo:\n{exc}")
            return

        face_box = self._detect_face_box(path)
        self._current_index = None  # a freshly chosen photo isn't on the sheet yet
        self._display_image(image, path, face_box)

        # Once someone else is already on the sheet, "fill sheet automatically"
        # no longer means anything (there's no single photo to fill it with),
        # so switch to manual copies and suggest whatever capacity is left.
        if self._people:
            if self.fill_auto.isChecked():
                self.fill_auto.setChecked(False)
            sheet = self._current_sheet_spec()
            spec = self._current_spec()
            capacity = max_copies(sheet, spec)
            used = sum(p.copies for p in self._people)
            self.copies.blockSignals(True)
            self.copies.setValue(max(0, capacity - used))
            self.copies.blockSignals(False)

        self._on_auto_crop()
        self._refresh_people_status()
        self.status_label.setText(f"Loaded {path.name} ({image.width}x{image.height}px).")

    def _display_image(self, image: Image.Image, path: Path, face_box: Optional[FaceBox]) -> None:
        """Push ``image`` into the graphics scene/state. Caller sets the crop box."""
        self._image = image
        self._image_path = path
        self._face_box = face_box
        self._preview_source_cache = None  # different photo -> rebuild on demand

        self._scene.clear()
        # scene.clear() deletes every item it owned (including any crop
        # rectangle from a previously displayed photo) at the C++ level, so
        # drop our reference too -- otherwise _set_crop_box() would later try
        # to removeItem() an already-deleted object.
        self._crop_item = None
        pixmap = _pil_to_qpixmap(image)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(0, 0, image.width, image.height)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        self.btn_auto_crop.setEnabled(True)
        self.btn_preview_sheet.setEnabled(True)
        self.btn_export.setEnabled(True)
        # Already on the sheet (self._current_index set) -> nothing to add;
        # a fresh, unattached photo -> offer to add it.
        self.btn_add_to_sheet.setEnabled(self._current_index is None)

    def _detect_face_box(self, path: Path) -> Optional[FaceBox]:
        """Best-effort face detection; returns ``None`` on any failure."""
        try:
            if self._face_detector is None:
                from core.face_detector import FaceDetector  # local: heavy/optional import

                self._face_detector = FaceDetector()
            result = self._face_detector.detect(path)
            if result.regions:
                # Largest face by area is the subject.
                return max(result.regions, key=lambda r: r[2] * r[3])
        except Exception as exc:  # noqa: BLE001 - face detection is a nice-to-have
            logger.info("Passport photo: face detection unavailable/failed (%s).", exc)
        return None

    # ------------------------------------------------------------------ #
    # Crop box
    # ------------------------------------------------------------------ #
    def _on_auto_crop(self) -> None:
        if self._image is None:
            return
        spec = self._current_spec()
        w, h = self._image.size
        box = auto_crop_box(w, h, self._face_box, spec.aspect)
        self._set_crop_box(box, spec.aspect)

    def _set_crop_box(self, box: tuple[int, int, int, int], aspect: float) -> None:
        if self._image is None:
            return
        x0, y0, x1, y1 = box
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        bounds = QRectF(0, 0, self._image.width, self._image.height)
        if self._crop_item is not None:
            self._scene.removeItem(self._crop_item)
        self._crop_item = _CropRectItem(rect, aspect, bounds, on_change=self._on_crop_changed)
        self._scene.addItem(self._crop_item)
        self._update_preview()

    def _on_crop_changed(self) -> None:
        """Crop rectangle moved/resized: refresh the preview, and if this
        photo is already on the sheet, keep its stored crop box in sync live
        (no separate "update" step needed)."""
        self._request_preview_update()
        if self._current_index is not None and 0 <= self._current_index < len(self._people):
            self._people[self._current_index].crop_box = self._crop_item.crop_box()
            self._refresh_people_row_label(self._current_index)

    def _request_preview_update(self) -> None:
        """
        Coalesce rapid preview requests into one recompute.

        Dragging a slider or the crop box emits a signal per intermediate
        value -- dozens per gesture. Recomputing the beautified preview for
        each one made the UI crawl, so restart a short timer instead and only
        do the work once the user pauses.
        """
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    def _preview_source(self) -> Optional[Image.Image]:
        """
        A cached, downscaled copy of the loaded photo for preview cropping.

        The live thumbnail is only ~220px, but the source can be a 24MP
        camera file; cropping and resizing *that* at full print resolution
        cost ~0.26s per update on its own. Downscaling once per loaded photo
        makes each later preview crop effectively free. Export and Preview
        Sheet still use the full-resolution original, so print quality is
        unaffected.
        """
        if self._image is None:
            return None
        if self._preview_source_cache is None:
            scaled = self._image.copy()
            scaled.thumbnail((_PREVIEW_SOURCE_MAX_DIM, _PREVIEW_SOURCE_MAX_DIM))
            self._preview_source_cache = scaled
        return self._preview_source_cache

    def _update_preview(self) -> None:
        """
        Recompute both preview thumbnails (raw crop + enhanced) and show one.

        Both are kept so "Hold to see original" can swap between them
        instantly without redoing any image work mid-gesture.
        """
        if self._image is None or self._crop_item is None:
            return
        source = self._preview_source()
        if source is None:
            return
        spec = self._current_spec()
        x0, y0, x1, y1 = self._crop_item.crop_box()
        # Scale the crop box from full-image coords into the downscaled
        # preview source's coords.
        sx = source.width / self._image.width
        sy = source.height / self._image.height
        box = (round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy))
        try:
            cropped = crop_and_resize(source, box, spec)
            cropped.thumbnail((_PREVIEW_MAX_W, _PREVIEW_MAX_W))
            enhanced = beautify(cropped, self._current_beautify_options())
        except Exception as exc:  # noqa: BLE001 - keep the UI alive on odd geometry
            logger.debug("Passport photo: preview crop failed: %s", exc)
            return

        self._preview_raw_pixmap = _pil_to_qpixmap(cropped)
        # beautify() returns the input untouched when disabled, in which case
        # there is genuinely nothing to compare against.
        self._preview_enhanced_pixmap = (
            self._preview_raw_pixmap if enhanced is cropped else _pil_to_qpixmap(enhanced)
        )
        self._sync_compare_button()
        self._show_preview_variant(raw=self._comparing)

    def _show_preview_variant(self, *, raw: bool) -> None:
        pixmap = self._preview_raw_pixmap if raw else self._preview_enhanced_pixmap
        if pixmap is not None:
            self.preview_label.setPixmap(pixmap)
        self.preview_caption.setText("Original (unenhanced)" if raw else "Cropped preview:")

    def _sync_compare_button(self) -> None:
        """Only offer the comparison when there's actually a difference to see."""
        has_both = (
            self._preview_raw_pixmap is not None
            and self._preview_enhanced_pixmap is not None
            and self._preview_enhanced_pixmap is not self._preview_raw_pixmap
        )
        self.btn_compare.setEnabled(has_both)
        if not has_both and self._comparing:
            self._comparing = False

    def _on_compare_pressed(self) -> None:
        self._comparing = True
        self._show_preview_variant(raw=True)

    def _on_compare_released(self) -> None:
        self._comparing = False
        self._show_preview_variant(raw=False)

    # ------------------------------------------------------------------ #
    # Photo-size controls
    # ------------------------------------------------------------------ #
    def _apply_preset(self, index: int) -> None:
        names = list(PASSPORT_SIZES.keys())
        if index < len(names):
            w, h = PASSPORT_SIZES[names[index]]
            self.width_mm.blockSignals(True)
            self.height_mm.blockSignals(True)
            self.width_mm.setValue(w)
            self.height_mm.setValue(h)
            self.width_mm.blockSignals(False)
            self.height_mm.blockSignals(False)
        is_custom = index >= len(names)
        self.width_mm.setEnabled(is_custom)
        self.height_mm.setEnabled(is_custom)

    def _on_size_changed(self) -> None:
        self._apply_preset(self.size_preset.currentIndex())
        if self._image is not None:
            self._on_auto_crop()
        self._refresh_sheet_status()

    def _current_spec(self) -> PassportPhotoSpec:
        return PassportPhotoSpec(
            width_mm=self.width_mm.value(),
            height_mm=self.height_mm.value(),
            dpi=int(self.dpi.currentData()),
        )

    # ------------------------------------------------------------------ #
    # Sheet controls
    # ------------------------------------------------------------------ #
    def _apply_sheet_preset(self, index: int) -> None:
        names = list(SHEET_SIZES.keys())
        if index < len(names):
            w, h = SHEET_SIZES[names[index]]
            self.sheet_width_in.blockSignals(True)
            self.sheet_height_in.blockSignals(True)
            self.sheet_width_in.setValue(w)
            self.sheet_height_in.setValue(h)
            self.sheet_width_in.blockSignals(False)
            self.sheet_height_in.blockSignals(False)
        is_custom = index >= len(names)
        self.sheet_width_in.setEnabled(is_custom)
        self.sheet_height_in.setEnabled(is_custom)

    def _on_sheet_preset_changed(self, index: int) -> None:
        self._apply_sheet_preset(index)
        self._refresh_sheet_status()

    def _on_fill_auto_toggled(self, checked: bool) -> None:
        self._sync_copies_enabled()
        self._refresh_sheet_status()

    def _on_stroke_toggled(self, checked: bool) -> None:
        self.stroke_width_mm.setEnabled(checked)

    # ------------------------------------------------------------------ #
    # Enhance faces (global beautification)
    # ------------------------------------------------------------------ #
    def _on_beautify_toggled(self, checked: bool) -> None:
        if checked and all(self.beautify_sliders[k].value() == 0 for k, _ in _BEAUTIFY_SLIDERS):
            # First time turning it on with every slider still at zero: seed
            # sensible defaults rather than leave it "on but doing nothing".
            defaults = BeautifyOptions.default_on()
            for key, _title in _BEAUTIFY_SLIDERS:
                slider = self.beautify_sliders[key]
                slider.blockSignals(True)
                slider.setValue(round(getattr(defaults, key) * 100))
                slider.blockSignals(False)
                self.beautify_value_labels[key].setText(f"{slider.value()}%")
        for key, _title in _BEAUTIFY_SLIDERS:
            self.beautify_sliders[key].setEnabled(checked)
        self._request_preview_update()

    def _on_beautify_slider_changed(self, _value: int) -> None:
        # Labels are cheap, so update them immediately for responsive
        # feedback; only the expensive image work is debounced.
        for key, _title in _BEAUTIFY_SLIDERS:
            self.beautify_value_labels[key].setText(f"{self.beautify_sliders[key].value()}%")
        self._request_preview_update()

    def _current_beautify_options(self) -> BeautifyOptions:
        return BeautifyOptions(
            enabled=self.beautify_enabled.isChecked(),
            **{key: self.beautify_sliders[key].value() / 100.0 for key, _title in _BEAUTIFY_SLIDERS},
        )

    def _current_sheet_spec(self) -> SheetSpec:
        spec = self._current_spec()
        return SheetSpec(
            width_in=self.sheet_width_in.value(),
            height_in=self.sheet_height_in.value(),
            margin_in=self.margin_in.value(),
            spacing_in=self.spacing_in.value(),
            dpi=spec.dpi,
        )

    def _refresh_sheet_status(self) -> None:
        try:
            spec = self._current_spec()
            sheet = self._current_sheet_spec()
            cols, rows = compute_grid(sheet, spec)
            capacity = cols * rows
        except PassportPhotoError as exc:
            self.sheet_status.setText(str(exc))
            self.copies.setMaximum(0)
            self._refresh_people_status()
            return
        if capacity <= 0:
            self.sheet_status.setText(
                "This photo size does not fit on the chosen sheet with the "
                "current margin/spacing."
            )
        else:
            self.sheet_status.setText(f"Fits {capacity} copies ({cols} x {rows} grid).")
        self.copies.setMaximum(max(0, capacity))
        self._refresh_people_status()

    # ------------------------------------------------------------------ #
    # People on this sheet (combining multiple people's photos onto one sheet)
    # ------------------------------------------------------------------ #
    def _sync_copies_enabled(self) -> None:
        """
        The Copies field is only tied to "Fill sheet automatically" while
        there's just one (not-yet-added) photo; once anyone is on the list,
        each person's copies are always set directly.
        """
        self.copies.setEnabled((not self.fill_auto.isChecked()) or bool(self._people))

    def _on_copies_changed(self, value: int) -> None:
        if self._current_index is not None and 0 <= self._current_index < len(self._people):
            self._people[self._current_index].copies = int(value)
            self._refresh_people_row_label(self._current_index)
        self._refresh_people_status()

    def _entry_copies(self) -> int:
        """Copies to use for the *currently loaded, not-yet-added* photo."""
        if self.fill_auto.isChecked() and not self._people:
            try:
                return max_copies(self._current_sheet_spec(), self._current_spec())
            except PassportPhotoError:
                return 0
        return int(self.copies.value())

    def _pending_entries(self) -> list[_PersonEntry]:
        """``self._people``, plus the loaded-but-not-yet-added photo, if any."""
        entries = list(self._people)
        unattached = (
            self._current_index is None
            and self._image is not None
            and self._crop_item is not None
        )
        if unattached:
            entries.append(
                _PersonEntry(
                    path=self._image_path or Path("photo"),
                    image=self._image,
                    face_box=self._face_box,
                    crop_box=self._crop_item.crop_box(),
                    copies=self._entry_copies(),
                )
            )
        return entries

    @staticmethod
    def _entry_label(entry: _PersonEntry) -> str:
        n = entry.copies
        return f"{entry.name} — {n} cop{'y' if n == 1 else 'ies'}"

    def _refresh_people_row_label(self, index: int) -> None:
        item = self.people_list.item(index)
        if item is not None and 0 <= index < len(self._people):
            item.setText(self._entry_label(self._people[index]))

    def _on_add_to_sheet(self) -> None:
        if self._image is None or self._crop_item is None:
            QMessageBox.information(self, "Passport Photos", "Choose a photo first.")
            return
        if self._current_index is not None:
            return  # already on the sheet -- edits sync live, nothing to add

        copies = self._entry_copies()
        if copies <= 0:
            QMessageBox.information(
                self,
                "Passport Photos",
                "The sheet is already full. Remove or reduce someone else's "
                "copies first, or choose a bigger sheet.",
            )
            return

        entry = _PersonEntry(
            path=self._image_path or Path("photo"),
            image=self._image,
            face_box=self._face_box,
            crop_box=self._crop_item.crop_box(),
            copies=copies,
        )
        self._people.append(entry)
        self.people_list.addItem(self._entry_label(entry))
        self._current_index = len(self._people) - 1
        self.people_list.blockSignals(True)
        self.people_list.setCurrentRow(self._current_index)
        self.people_list.blockSignals(False)
        self.btn_add_to_sheet.setEnabled(False)
        self._refresh_people_status()
        self.status_label.setText(
            f"Added {entry.name} to the sheet ({entry.copies} "
            f"cop{'y' if entry.copies == 1 else 'ies'})."
        )

    def _on_remove_selected(self) -> None:
        row = self.people_list.currentRow()
        if row < 0 or row >= len(self._people):
            return
        del self._people[row]
        self.people_list.takeItem(row)
        self._current_index = None
        self.btn_add_to_sheet.setEnabled(self._image is not None)
        self._refresh_people_status()

    def _on_people_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._people):
            return
        entry = self._people[row]
        self._current_index = row
        self._display_image(entry.image, entry.path, entry.face_box)
        self._set_crop_box(entry.crop_box, self._current_spec().aspect)
        self.copies.blockSignals(True)
        self.copies.setValue(entry.copies)
        self.copies.blockSignals(False)
        self._sync_copies_enabled()
        n = entry.copies
        self.status_label.setText(
            f"Editing {entry.name} ({n} cop{'y' if n == 1 else 'ies'}) on the sheet."
        )

    def _refresh_people_status(self) -> None:
        self._sync_copies_enabled()
        self.btn_remove_person.setEnabled(self.people_list.currentRow() >= 0)
        if not hasattr(self, "people_status"):
            return
        if not self._people:
            self.people_status.setText(
                "No one added yet — the current photo alone will fill the sheet."
            )
            return
        try:
            capacity = max_copies(self._current_sheet_spec(), self._current_spec())
        except PassportPhotoError:
            capacity = 0
        used = sum(p.copies for p in self._people)
        remaining = capacity - used
        who = f"{len(self._people)} " + ("person" if len(self._people) == 1 else "people")
        if remaining >= 0:
            self.people_status.setText(f"{who} added — {used} of {capacity} copies used ({remaining} left).")
        else:
            self.people_status.setText(f"{who} added — {used} of {capacity} copies used ({-remaining} over capacity!).")

    # ------------------------------------------------------------------ #
    # Sheet building (shared by Preview Sheet and Export Sheet)
    # ------------------------------------------------------------------ #
    def _build_current_sheet(
        self,
    ) -> Optional[tuple[Image.Image, PassportPhotoSpec, int, int, int, int]]:
        """
        Build the sheet exactly as it would be exported right now, combining
        everyone in "People on this sheet" plus the currently loaded photo
        if it hasn't been added yet.

        Returns ``(sheet_image, spec, cols, rows, total_placed, num_people)``,
        or ``None`` (after showing a message) if there is nothing to build or
        the current photo/sheet sizes are incompatible.
        """
        entries = self._pending_entries()
        if not entries:
            QMessageBox.information(self, "Passport Photos", "Choose a photo first.")
            return None

        spec = self._current_spec()
        sheet = self._current_sheet_spec()
        stroke_mm = self.stroke_width_mm.value() if self.stroke_enabled.isChecked() else 0.0
        beautify_opts = self._current_beautify_options()
        try:
            sheet_entries = [
                SheetEntry(
                    photo=beautify(crop_and_resize(e.image, e.crop_box, spec), beautify_opts),
                    copies=e.copies,
                )
                for e in entries
            ]
            sheet_img = build_multi_sheet(sheet_entries, sheet, spec, stroke_mm=stroke_mm)
        except PassportPhotoError as exc:
            QMessageBox.warning(self, "Passport Photos", str(exc))
            return None
        cols, rows = compute_grid(sheet, spec)
        total_placed = min(cols * rows, sum(e.copies for e in entries))
        return sheet_img, spec, cols, rows, total_placed, len(entries)

    # ------------------------------------------------------------------ #
    # Preview
    # ------------------------------------------------------------------ #
    def _on_preview_sheet(self) -> None:
        built = self._build_current_sheet()
        if built is None:
            return
        sheet_img, _spec, cols, rows, total_placed, num_people = built
        who = f"{num_people} photo" + ("" if num_people == 1 else "s")

        dlg = QDialog(self)
        dlg.setWindowTitle("Sheet Preview")
        layout = QVBoxLayout(dlg)

        info = QLabel(
            f"{total_placed} copies across {who} in a {cols} x {rows} grid — "
            "exactly what Export Sheet will write."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        pixmap = _pil_to_qpixmap(sheet_img)
        if max(pixmap.width(), pixmap.height()) > _SHEET_PREVIEW_MAX_DIM:
            pixmap = pixmap.scaled(
                _SHEET_PREVIEW_MAX_DIM, _SHEET_PREVIEW_MAX_DIM,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        image_label = QLabel()
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(image_label)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        export_btn = buttons.addButton("Export…", QDialogButtonBox.ButtonRole.ActionRole)
        export_btn.clicked.connect(lambda: (dlg.accept(), self._on_export()))
        layout.addWidget(buttons)

        dlg.resize(
            min(_SHEET_PREVIEW_MAX_DIM + 60, pixmap.width() + 60),
            min(_SHEET_PREVIEW_MAX_DIM + 140, pixmap.height() + 140),
        )
        dlg.exec()

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _on_export(self) -> None:
        built = self._build_current_sheet()
        if built is None:
            return
        sheet_img, spec, _cols, _rows, _total_placed, _num_people = built

        default_name = (self._image_path.stem if self._image_path else "passport") + "_sheet.jpg"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export print sheet", default_name,
            "JPEG (*.jpg);;PNG (*.png);;PDF (*.pdf)",
        )
        if not path:
            return
        try:
            out = save_sheet(sheet_img, path, dpi=spec.dpi)
        except PassportPhotoError as exc:
            QMessageBox.critical(self, "Passport Photos", str(exc))
            return
        QMessageBox.information(self, "Passport Photos", f"Saved print sheet to:\n{out}")
