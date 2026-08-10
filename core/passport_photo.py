"""
Passport / ID photo generator for PhotoFlow.

Automates the manual studio workflow of: crop a portrait to a standard
passport/ID photo size, then tile copies of that crop onto a single print
sheet (e.g. a 4x6 in print) so a lab can print many copies at once instead of
one photo per page.

This module is pure image-processing logic (Pillow + plain math) with no Qt
dependency, so it is unit-testable on its own; :mod:`ui_qt.views.passport_photo_view`
wraps it in an interactive widget.

Scope (v1): cropping + sheet tiling only. Color correction and background
replacement are deliberately out of scope for this pass.

Sizing
------
Photo dimensions are specified in millimetres (the unit passport-photo specs
are usually quoted in) and print-sheet dimensions in inches (the unit photo
labs quote print sizes in); both are converted to pixels via the target DPI.

Auto-crop heuristic
--------------------
:func:`auto_crop_box` centers the crop on a detected face region (as produced
by :class:`core.face_detector.FaceDetector`) using the common studio framing
rule of thumb: the head spans roughly 60-70% of the photo's height, with the
eye-line sitily above center. Face-detector boxes are tight around
eyes/nose/mouth, not full crown-to-chin, so the head span is *estimated* from
the detected box with fixed multipliers below. This is a heuristic aimed at
"looks right", not biometric/ICAO compliance -- studios can (and, per the
manual workflow this replaces, do) nudge the crop by hand afterward.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #

#: Standard passport/ID photo sizes in millimetres: name -> (width_mm, height_mm).
#: Not exhaustive -- callers may also supply custom mm dimensions via
#: :class:`PassportPhotoSpec` directly.
PASSPORT_SIZES: dict[str, tuple[float, float]] = {
    "Usual (3 x 3.5 cm)": (30.0, 35.0),
    "US Passport / Visa (2 x 2 in)": (50.8, 50.8),
    "India Passport / Visa (3.5 x 4.5 cm)": (35.0, 45.0),
    "UK / EU / Schengen (35 x 45 mm)": (35.0, 45.0),
    "Canada Passport (50 x 70 mm)": (50.0, 70.0),
    "China Passport / Visa (33 x 48 mm)": (33.0, 48.0),
    "Indian PAN Card (2.5 x 3.5 cm)": (25.0, 35.0),
}

#: Common print-sheet sizes in inches: name -> (width_in, height_in).
SHEET_SIZES: dict[str, tuple[float, float]] = {
    "4 x 6 in": (4.0, 6.0),
    "5 x 7 in": (5.0, 7.0),
    "6 x 8 in": (6.0, 8.0),
    "A4": (8.27, 11.69),
    "Letter": (8.5, 11.0),
}

DEFAULT_DPI = 300
DEFAULT_SHEET_MARGIN_IN = 0.1
DEFAULT_SHEET_SPACING_IN = 0.05

_MM_PER_IN = 25.4
_WHITE = (255, 255, 255)

# Face-detector-box -> head-span heuristic (see module docstring). These are
# empirical constants, not measured biometric ratios.
_HEAD_SPAN_FROM_FACE_BOX = 1.8  # crown-to-chin span ~= face box height * this
_EYE_LINE_FROM_FACE_TOP = 0.35  # eye line sits this far down the face box
_DEFAULT_HEAD_HEIGHT_FRACTION = 0.65  # head should fill this much of the crop
_DEFAULT_EYE_LINE_FRACTION = 0.45  # eye line sits this far down the crop


class PassportPhotoError(Exception):
    """Raised for invalid passport-photo or sheet specifications."""


# --------------------------------------------------------------------------- #
# Specs
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class PassportPhotoSpec:
    """
    A target passport/ID photo size.

    Args:
        width_mm: Photo width in millimetres (> 0).
        height_mm: Photo height in millimetres (> 0).
        dpi: Print resolution in dots per inch (> 0).
    """

    width_mm: float
    height_mm: float
    dpi: int = DEFAULT_DPI

    def __post_init__(self) -> None:
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise PassportPhotoError(
                f"width_mm/height_mm must be > 0, got {self.width_mm}x{self.height_mm}"
            )
        if self.dpi <= 0:
            raise PassportPhotoError(f"dpi must be > 0, got {self.dpi}")

    @property
    def width_px(self) -> int:
        return _mm_to_px(self.width_mm, self.dpi)

    @property
    def height_px(self) -> int:
        return _mm_to_px(self.height_mm, self.dpi)

    @property
    def aspect(self) -> float:
        """Width / height."""
        return self.width_mm / self.height_mm


@dataclasses.dataclass(frozen=True)
class SheetSpec:
    """
    A print sheet onto which copies of one passport photo are tiled.

    Args:
        width_in: Sheet width in inches (> 0).
        height_in: Sheet height in inches (> 0).
        margin_in: Blank border kept around the tiled grid on every edge.
        spacing_in: Gap kept between adjacent photo tiles.
        dpi: Print resolution in dots per inch (> 0). Should normally match
            the :class:`PassportPhotoSpec` used to build the tile.
    """

    width_in: float
    height_in: float
    margin_in: float = DEFAULT_SHEET_MARGIN_IN
    spacing_in: float = DEFAULT_SHEET_SPACING_IN
    dpi: int = DEFAULT_DPI

    def __post_init__(self) -> None:
        if self.width_in <= 0 or self.height_in <= 0:
            raise PassportPhotoError(
                f"width_in/height_in must be > 0, got {self.width_in}x{self.height_in}"
            )
        if self.margin_in < 0 or self.spacing_in < 0:
            raise PassportPhotoError("margin_in/spacing_in must be >= 0")
        if self.dpi <= 0:
            raise PassportPhotoError(f"dpi must be > 0, got {self.dpi}")

    @property
    def width_px(self) -> int:
        return round(self.width_in * self.dpi)

    @property
    def height_px(self) -> int:
        return round(self.height_in * self.dpi)

    @property
    def margin_px(self) -> int:
        return round(self.margin_in * self.dpi)

    @property
    def spacing_px(self) -> int:
        return round(self.spacing_in * self.dpi)


def _mm_to_px(mm: float, dpi: int) -> int:
    return round(mm / _MM_PER_IN * dpi)


# --------------------------------------------------------------------------- #
# Auto-crop
# --------------------------------------------------------------------------- #

# A face bounding box as relative (xmin, ymin, width, height) in [0, 1] -- the
# same shape FaceDetector.FaceResult.regions elements use.
FaceBox = tuple[float, float, float, float]


def auto_crop_box(
    image_width: int,
    image_height: int,
    face_box: Optional[FaceBox],
    target_aspect: float,
    head_height_fraction: float = _DEFAULT_HEAD_HEIGHT_FRACTION,
    eye_line_fraction: float = _DEFAULT_EYE_LINE_FRACTION,
) -> tuple[int, int, int, int]:
    """
    Compute a passport-photo crop box centered on a face.

    Args:
        image_width: Source image width in pixels (> 0).
        image_height: Source image height in pixels (> 0).
        face_box: Detected face region as relative ``(xmin, ymin, w, h)`` in
            ``[0, 1]``, e.g. from ``FaceDetector.detect(...).regions[0]``.
            When ``None`` (no face found), the crop falls back to a centered
            box using ``target_aspect`` at the largest size that fits.
        target_aspect: Desired crop width / height (from
            :attr:`PassportPhotoSpec.aspect`).
        head_height_fraction: Fraction of the crop's height the estimated
            head span should occupy. Larger = tighter crop on the face.
        eye_line_fraction: Fraction of the crop's height, from the top, where
            the eye line should land.

    Returns:
        ``(x0, y0, x1, y1)`` integer pixel box, clamped to the image bounds
        and guaranteed to have ``target_aspect`` (adjusted to fit if the ideal
        crop would exceed the source image).

    Raises:
        PassportPhotoError: if ``image_width``/``image_height``/``target_aspect``
            are not positive.
    """
    if image_width <= 0 or image_height <= 0:
        raise PassportPhotoError(
            f"image_width/image_height must be > 0, got {image_width}x{image_height}"
        )
    if target_aspect <= 0:
        raise PassportPhotoError(f"target_aspect must be > 0, got {target_aspect}")

    if face_box is None:
        return _centered_fallback_box(image_width, image_height, target_aspect)

    fx = face_box[0] * image_width
    fy = face_box[1] * image_height
    fw = face_box[2] * image_width
    fh = face_box[3] * image_height
    if fw <= 0 or fh <= 0:
        return _centered_fallback_box(image_width, image_height, target_aspect)

    head_span = fh * _HEAD_SPAN_FROM_FACE_BOX
    eye_y = fy + fh * _EYE_LINE_FROM_FACE_TOP
    face_center_x = fx + fw / 2.0

    crop_h = head_span / head_height_fraction
    crop_w = crop_h * target_aspect

    # If the ideal crop is bigger than the source image, shrink it (keeping
    # aspect) to the largest size that fits, rather than failing.
    scale = min(1.0, image_width / crop_w, image_height / crop_h)
    crop_w *= scale
    crop_h *= scale

    x0 = face_center_x - crop_w / 2.0
    y0 = eye_y - crop_h * eye_line_fraction

    x0 = _clamp(x0, 0.0, image_width - crop_w)
    y0 = _clamp(y0, 0.0, image_height - crop_h)

    return (
        round(x0),
        round(y0),
        round(x0 + crop_w),
        round(y0 + crop_h),
    )


def _centered_fallback_box(
    image_width: int, image_height: int, target_aspect: float
) -> tuple[int, int, int, int]:
    """Largest ``target_aspect`` box centered in the image (no face found)."""
    crop_h = min(image_height, image_width / target_aspect)
    crop_w = crop_h * target_aspect
    x0 = (image_width - crop_w) / 2.0
    y0 = (image_height - crop_h) / 2.0
    return (round(x0), round(y0), round(x0 + crop_w), round(y0 + crop_h))


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return low
    return max(low, min(value, high))


def crop_and_resize(
    image: Image.Image, box: tuple[int, int, int, int], spec: PassportPhotoSpec
) -> Image.Image:
    """Crop ``image`` to ``box`` and resize to ``spec``'s pixel dimensions."""
    cropped = image.crop(box)
    return cropped.resize((spec.width_px, spec.height_px), Image.LANCZOS)


def add_border(
    image: Image.Image,
    stroke_mm: float,
    dpi: int,
    color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """
    Draw a solid border inset from ``image``'s edges (a cutting-guide stroke).

    Common in studio passport-photo sheets: a thin black outline printed on
    each photo so the lab/customer can trim precisely. The border is drawn
    *inward* (it eats into the photo, not the sheet's spacing), so it does
    not change the tile's placement or the sheet's grid math.

    Args:
        image: The photo tile (already cropped/resized to its final size).
        stroke_mm: Border thickness in millimetres. ``<= 0`` returns
            ``image`` unchanged (no border drawn).
        dpi: Resolution used to convert ``stroke_mm`` to pixels.
        color: Border color as an ``(r, g, b)`` tuple.

    Returns:
        A copy of ``image`` with the border drawn on it.
    """
    stroke_px = _mm_to_px(stroke_mm, dpi) if stroke_mm > 0 else 0
    if stroke_px <= 0:
        return image
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    # Concentric single-pixel rectangles give an exact stroke_px-wide band,
    # regardless of how Pillow centers/thickens a single wide-outline call.
    max_inset = min(w, h) // 2
    for i in range(min(stroke_px, max_inset)):
        draw.rectangle([i, i, w - 1 - i, h - 1 - i], outline=color)
    return out


# --------------------------------------------------------------------------- #
# Print-sheet tiling
# --------------------------------------------------------------------------- #

def compute_grid(sheet: SheetSpec, spec: PassportPhotoSpec) -> tuple[int, int]:
    """
    Return ``(cols, rows)`` -- how many photo tiles fit on the sheet.

    Uses the sheet's margin and inter-tile spacing; ``(0, 0)`` if even a
    single tile does not fit within the margins.
    """
    usable_w = sheet.width_px - 2 * sheet.margin_px
    usable_h = sheet.height_px - 2 * sheet.margin_px
    tile_w = spec.width_px
    tile_h = spec.height_px
    if usable_w < tile_w or usable_h < tile_h:
        return (0, 0)
    cols = int((usable_w + sheet.spacing_px) // (tile_w + sheet.spacing_px))
    rows = int((usable_h + sheet.spacing_px) // (tile_h + sheet.spacing_px))
    return (max(0, cols), max(0, rows))


def max_copies(sheet: SheetSpec, spec: PassportPhotoSpec) -> int:
    """The maximum number of copies of one photo that fit on the sheet."""
    cols, rows = compute_grid(sheet, spec)
    return cols * rows


def _grid_positions(sheet: SheetSpec, spec: PassportPhotoSpec) -> list[tuple[int, int]]:
    """Top-left ``(x, y)`` pixel position of every tile slot, centered as a
    block on the sheet, in row-major order. Empty if nothing fits."""
    cols, rows = compute_grid(sheet, spec)
    if cols <= 0 or rows <= 0:
        return []
    block_w = cols * spec.width_px + (cols - 1) * sheet.spacing_px
    block_h = rows * spec.height_px + (rows - 1) * sheet.spacing_px
    off_x = (sheet.width_px - block_w) // 2
    off_y = (sheet.height_px - block_h) // 2
    positions = []
    for r in range(rows):
        for c in range(cols):
            x = off_x + c * (spec.width_px + sheet.spacing_px)
            y = off_y + r * (spec.height_px + sheet.spacing_px)
            positions.append((x, y))
    return positions


def _prepare_tile(
    photo: Image.Image,
    spec: PassportPhotoSpec,
    stroke_mm: float,
    stroke_color: tuple[int, int, int],
) -> Image.Image:
    """Resize (if needed) to ``spec``'s pixel size, convert to RGB, and draw
    the optional cutting-guide border -- the per-photo prep shared by
    :func:`build_sheet` and :func:`build_multi_sheet`."""
    tile = photo if photo.size == (spec.width_px, spec.height_px) else photo.resize(
        (spec.width_px, spec.height_px), Image.LANCZOS
    )
    if tile.mode != "RGB":
        tile = tile.convert("RGB")
    if stroke_mm > 0:
        tile = add_border(tile, stroke_mm, spec.dpi, stroke_color)
    return tile


def build_sheet(
    photo: Image.Image,
    sheet: SheetSpec,
    spec: PassportPhotoSpec,
    copies: Optional[int] = None,
    stroke_mm: float = 0.0,
    stroke_color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """
    Tile copies of one photo onto a single print sheet, centered as a block.

    For combining several *different* people's photos onto one sheet (e.g. a
    family submitting passport photos together), see :func:`build_multi_sheet`.

    Args:
        photo: The already-cropped passport photo (any size; it is resized
            to ``spec``'s pixel dimensions before tiling).
        sheet: Physical sheet size/margins/spacing.
        spec: The passport photo's target size/DPI (should match ``sheet.dpi``).
        copies: Number of copies to place; ``None`` fills the sheet with as
            many as fit. Clamped to ``[0, max_copies(sheet, spec)]``.
        stroke_mm: Optional cutting-guide border thickness in millimetres,
            drawn on every tile (see :func:`add_border`). ``0`` (default)
            draws no border.
        stroke_color: Border color, when ``stroke_mm > 0``.

    Returns:
        An RGB Pillow image the size of the sheet, with ``copies`` (or the
        maximum that fit) tiles arranged in a centered grid.

    Raises:
        PassportPhotoError: if no tile fits on the sheet at all.
    """
    positions = _grid_positions(sheet, spec)
    capacity = len(positions)
    if capacity <= 0:
        raise PassportPhotoError(
            f"Photo {spec.width_mm}x{spec.height_mm}mm does not fit on sheet "
            f"{sheet.width_in}x{sheet.height_in}in with the given margin/spacing."
        )
    n = capacity if copies is None else max(0, min(int(copies), capacity))
    tile = _prepare_tile(photo, spec, stroke_mm, stroke_color)

    canvas = Image.new("RGB", (sheet.width_px, sheet.height_px), _WHITE)
    for x, y in positions[:n]:
        canvas.paste(tile, (x, y))

    logger.info(
        "Passport sheet: placed %d/%d tile(s) on %sx%s in sheet.",
        n, capacity, sheet.width_in, sheet.height_in,
    )
    return canvas


@dataclasses.dataclass
class SheetEntry:
    """One person's cropped photo, plus how many copies of it to place."""

    photo: Image.Image
    copies: int


def build_multi_sheet(
    entries: list[SheetEntry],
    sheet: SheetSpec,
    spec: PassportPhotoSpec,
    stroke_mm: float = 0.0,
    stroke_color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """
    Tile several people's passport photos onto one shared print sheet.

    Common studio case: two or three people (e.g. a family) each bring one
    portrait, and the lab prints all of them on a single 4x6in sheet instead
    of one sheet per person. Every entry's photo is cropped/resized to the
    same ``spec`` already (by the caller); this function just fills the
    sheet's grid slots in the order ``entries`` are given, each contributing
    ``entry.copies`` tiles.

    Args:
        entries: One :class:`SheetEntry` per person, in placement order.
        sheet: Physical sheet size/margins/spacing.
        spec: The shared passport photo size/DPI for every entry.
        stroke_mm: Optional cutting-guide border thickness in millimetres,
            drawn on every tile. ``0`` (default) draws no border.
        stroke_color: Border color, when ``stroke_mm > 0``.

    Returns:
        An RGB Pillow image the size of the sheet. If the requested copies
        add up to more than the sheet holds, only as many as fit are placed
        (extra copies are dropped from whichever entry runs the sheet out) --
        callers should compare against :func:`max_copies` beforehand and warn
        the user if that matters to them.

    Raises:
        PassportPhotoError: if no tile fits on the sheet at all.
    """
    positions = _grid_positions(sheet, spec)
    capacity = len(positions)
    if capacity <= 0:
        raise PassportPhotoError(
            f"Photo {spec.width_mm}x{spec.height_mm}mm does not fit on sheet "
            f"{sheet.width_in}x{sheet.height_in}in with the given margin/spacing."
        )

    canvas = Image.new("RGB", (sheet.width_px, sheet.height_px), _WHITE)
    idx = 0
    total_placed = 0
    for entry in entries:
        if idx >= capacity:
            break
        tile = _prepare_tile(entry.photo, spec, stroke_mm, stroke_color)
        n = max(0, int(entry.copies))
        for _ in range(n):
            if idx >= capacity:
                break
            x, y = positions[idx]
            canvas.paste(tile, (x, y))
            idx += 1
            total_placed += 1

    logger.info(
        "Passport sheet: placed %d/%d tile(s) from %d entr%s on %sx%s in sheet.",
        total_placed, capacity, len(entries), "y" if len(entries) == 1 else "ies",
        sheet.width_in, sheet.height_in,
    )
    return canvas


def save_sheet(image: Image.Image, path: PathLike, dpi: int = DEFAULT_DPI) -> Path:
    """
    Save a tiled sheet image, choosing the format from ``path``'s suffix.

    Supports ``.jpg``/``.jpeg``/``.png`` (raster, DPI embedded) and ``.pdf``
    (single page at ``dpi``). Raises :class:`PassportPhotoError` for any
    other suffix.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.convert("RGB").save(out, "JPEG", quality=95, dpi=(dpi, dpi))
    elif suffix == ".png":
        image.save(out, "PNG", dpi=(dpi, dpi))
    elif suffix == ".pdf":
        image.convert("RGB").save(out, "PDF", resolution=float(dpi))
    else:
        raise PassportPhotoError(
            f"Unsupported sheet output format '{suffix}'; use .jpg, .png, or .pdf"
        )
    return out
