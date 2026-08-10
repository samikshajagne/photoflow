"""
Automatic collage builder for PhotoFlow.

Turns a set of photos into a single finished collage image: pick photos, pick a
theme and a layout, get a result. Pure Pillow/NumPy -- no Qt -- so every layout
and rendering decision here is directly unit-testable.

The four layouts each suit a different kind of photo set:

* ``grid`` -- equal cells in a near-square arrangement. Predictable and tidy.
* ``mosaic`` -- justified rows of varying widths that follow each photo's own
  aspect ratio, so a mixed portrait/landscape set needs far less cropping.
  This is the "photo wall" look and is usually the best default.
* ``feature`` -- one large hero photo with the rest arranged around it. Good
  when one frame clearly carries the story.
* ``scatter`` -- loosely rotated, overlapping prints with white borders, like
  photos dropped on a table.

What makes it "smart" rather than just a tiler
----------------------------------------------
1. **Faces are never cut.** Cells rarely match a photo's aspect ratio, so
   something must be cropped away. :func:`face_aware_cover_box` picks the crop
   window that keeps every detected face inside it, instead of blindly
   centre-cropping and beheading someone.
2. **Photos go where they fit.** :func:`assign_by_orientation` pairs portrait
   photos with tall cells and landscape photos with wide ones, minimising the
   total amount cropped away (same idea as the album engine's slot matching).
3. **The mosaic picks its own row count.** :func:`mosaic_cells` tries every
   plausible number of rows and keeps whichever needs the least cropping for
   *this particular* set of aspect ratios, rather than guessing.
4. **The hero is chosen, not assumed.** :func:`pick_hero_index` prefers a photo
   with a large, prominent face and enough resolution to survive being enlarged.
5. **Variants are free and repeatable.** Every layout takes a ``seed``, so
   "shuffle" produces a genuinely different arrangement, and the same seed
   always reproduces the same collage.

Cells always tile the canvas exactly (no gaps, no overlaps) except in
``scatter``, where overlapping is the point.
"""

from __future__ import annotations

import dataclasses
import math
import random
from pathlib import Path
from typing import Optional, Sequence, Union

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]
RGB = tuple[int, int, int]
# Relative face box (x, y, w, h) in [0, 1], matching core.face_detector.FaceBox.
FaceBox = tuple[float, float, float, float]

DEFAULT_DPI = 300

# Output sizes. Social sizes are in pixels; print sizes are inches x dpi.
SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "Instagram Square (1080x1080)": (1080, 1080),
    "Instagram Portrait (1080x1350)": (1080, 1350),
    "Instagram Story (1080x1920)": (1080, 1920),
    "Facebook Cover (1640x664)": (1640, 664),
    "HD Landscape (1920x1080)": (1920, 1080),
    "4x6 in @300dpi": (1800, 1200),
    "5x7 in @300dpi": (2100, 1500),
    "8x10 in @300dpi": (3000, 2400),
    "A4 Landscape @300dpi": (3508, 2480),
    "A4 Portrait @300dpi": (2480, 3508),
    "12x12 in @300dpi": (3600, 3600),
}

LAYOUT_GRID = "grid"
LAYOUT_MOSAIC = "mosaic"
LAYOUT_FEATURE = "feature"
LAYOUT_SCATTER = "scatter"
LAYOUT_MASONRY = "masonry"
LAYOUT_FILMSTRIP = "filmstrip"
LAYOUT_MAGAZINE = "magazine"
LAYOUTS: tuple[str, ...] = (
    LAYOUT_MOSAIC,
    LAYOUT_GRID,
    LAYOUT_FEATURE,
    LAYOUT_MASONRY,
    LAYOUT_MAGAZINE,
    LAYOUT_FILMSTRIP,
    LAYOUT_SCATTER,
)

# Background styles.
BG_SOLID = "solid"
BG_GRADIENT = "gradient"
BG_IMAGE = "image"
BG_BLURRED_PHOTO = "blurred_photo"
BACKGROUND_STYLES: tuple[str, ...] = (BG_SOLID, BG_GRADIENT, BG_IMAGE, BG_BLURRED_PHOTO)

# Per-photo colour filters.
FILTER_NONE = "none"
FILTER_BW = "bw"
FILTER_SEPIA = "sepia"
FILTER_WARM = "warm"
FILTER_COOL = "cool"
FILTER_VIVID = "vivid"
FILTERS: tuple[str, ...] = (
    FILTER_NONE, FILTER_BW, FILTER_SEPIA, FILTER_WARM, FILTER_COOL, FILTER_VIVID
)

# Below this many pixels along a cell's long edge, a source photo has to be
# upscaled to fill it, which shows as softness in print.
MIN_PRINT_PPI = 150

# Cropping beyond this fraction of a photo's area is treated as "expensive"
# when the mosaic compares candidate row counts.
_MAX_REASONABLE_ROWS = 12


class CollageError(Exception):
    """Raised when a collage cannot be built (bad spec, no photos)."""


@dataclasses.dataclass(frozen=True)
class CollageTheme:
    """
    The visual treatment applied to a collage.

    Attributes:
        name: Display name.
        background: Canvas colour behind and between the photos.
        margin_frac: Outer margin as a fraction of the canvas short edge.
        spacing_frac: Gap between photos, as a fraction of the short edge.
        border_px_frac: White/coloured print border around each photo, as a
            fraction of the short edge. ``0`` disables it.
        border_color: Colour of that border.
        corner_radius_frac: Rounded-corner radius as a fraction of the short
            edge. ``0`` keeps square corners.
        shadow: Whether to drop a soft shadow behind each photo.
        shadow_opacity: ``0..255`` strength of that shadow.
    """

    name: str
    background: RGB = (255, 255, 255)
    margin_frac: float = 0.02
    spacing_frac: float = 0.012
    border_px_frac: float = 0.0
    border_color: RGB = (255, 255, 255)
    corner_radius_frac: float = 0.0
    shadow: bool = False
    shadow_opacity: int = 70


THEMES: dict[str, CollageTheme] = {
    "Classic White": CollageTheme(
        name="Classic White",
        background=(255, 255, 255),
        spacing_frac=0.014,
        corner_radius_frac=0.006,
    ),
    "Gallery Dark": CollageTheme(
        name="Gallery Dark",
        background=(24, 25, 28),
        spacing_frac=0.016,
        corner_radius_frac=0.006,
        shadow=True,
        shadow_opacity=110,
    ),
    "Polaroid": CollageTheme(
        name="Polaroid",
        background=(238, 235, 228),
        margin_frac=0.03,
        spacing_frac=0.022,
        border_px_frac=0.012,
        border_color=(255, 255, 255),
        shadow=True,
        shadow_opacity=90,
    ),
    "Soft Blush": CollageTheme(
        name="Soft Blush",
        background=(250, 240, 240),
        spacing_frac=0.016,
        border_px_frac=0.005,
        border_color=(255, 255, 255),
        corner_radius_frac=0.01,
        shadow=True,
        shadow_opacity=45,
    ),
    "Warm Vintage": CollageTheme(
        name="Warm Vintage",
        background=(242, 232, 214),
        spacing_frac=0.018,
        border_px_frac=0.008,
        border_color=(252, 248, 240),
        shadow=True,
        shadow_opacity=60,
    ),
    "Seamless": CollageTheme(
        name="Seamless",
        background=(255, 255, 255),
        margin_frac=0.0,
        spacing_frac=0.0,
        corner_radius_frac=0.0,
    ),
}

DEFAULT_THEME = "Classic White"


@dataclasses.dataclass(frozen=True)
class CollageSpec:
    """
    Output geometry for a collage.

    Attributes:
        width_px: Canvas width in pixels (> 0).
        height_px: Canvas height in pixels (> 0).
        dpi: Stored in the saved file so print sizes come out right.
    """

    width_px: int
    height_px: int
    dpi: int = DEFAULT_DPI

    def __post_init__(self) -> None:
        if self.width_px <= 0 or self.height_px <= 0:
            raise CollageError(
                f"Collage size must be positive, got {self.width_px}x{self.height_px}"
            )
        if self.dpi <= 0:
            raise CollageError(f"dpi must be positive, got {self.dpi}")

    @property
    def aspect(self) -> float:
        return self.width_px / self.height_px

    @property
    def short_edge(self) -> int:
        return min(self.width_px, self.height_px)


@dataclasses.dataclass(frozen=True)
class Cell:
    """
    One photo's slot on the canvas, in absolute pixels.

    ``rotation_deg`` is only non-zero for the scatter layout.
    """

    x: int
    y: int
    w: int
    h: int
    rotation_deg: float = 0.0

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 1.0


@dataclasses.dataclass(frozen=True)
class PhotoAdjust:
    """
    Per-photo tweaks applied before the photo is placed in its cell.

    Attributes:
        zoom: Extra magnification inside the cell. ``1.0`` uses the normal
            face-aware cover crop; ``1.5`` crops 50% tighter.
        offset_x: Horizontal nudge of the crop window, as a fraction of the
            photo's width. Negative moves the visible area left.
        offset_y: Vertical nudge, as a fraction of height.
        rotate_deg: Rotation of the photo itself (not the cell).
        filter_name: One of :data:`FILTERS`.
        beautify: Run the face-beautify pass (see :mod:`core.face_beautify`).
    """

    zoom: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotate_deg: float = 0.0
    filter_name: str = FILTER_NONE
    beautify: bool = False

    @property
    def is_identity(self) -> bool:
        """True when this adjustment would leave the photo untouched."""
        return (
            abs(self.zoom - 1.0) < 1e-6
            and abs(self.offset_x) < 1e-6
            and abs(self.offset_y) < 1e-6
            and abs(self.rotate_deg) < 1e-6
            and self.filter_name == FILTER_NONE
            and not self.beautify
        )


@dataclasses.dataclass
class CollagePhoto:
    """
    A photo to place, plus what we know about it.

    Attributes:
        image: The decoded RGB image.
        face_boxes: Relative face boxes in ``[0, 1]``; empty is fine (the crop
            then falls back to centred).
        path: Optional source path, used only for logging/labels.
        adjust: Per-photo zoom/pan/rotate/filter settings.
    """

    image: Image.Image
    face_boxes: tuple[FaceBox, ...] = ()
    path: Optional[Path] = None
    adjust: PhotoAdjust = dataclasses.field(default_factory=PhotoAdjust)

    @property
    def aspect(self) -> float:
        return self.image.width / self.image.height if self.image.height else 1.0


@dataclasses.dataclass(frozen=True)
class Background:
    """
    What sits behind the photos.

    Attributes:
        style: One of :data:`BACKGROUND_STYLES`.
        color: Base colour for ``solid``, and the first stop for ``gradient``.
        color2: Second gradient stop.
        gradient_vertical: Gradient direction.
        image_path: Source for the ``image`` style (cover-cropped to fill).
        blur_radius_frac: Blur strength for ``blurred_photo``, as a fraction of
            the canvas short edge.
        darken: ``0..1`` amount to darken an image/blurred backdrop so the
            photos on top stay legible.
    """

    style: str = BG_SOLID
    color: RGB = (255, 255, 255)
    color2: RGB = (225, 232, 240)
    gradient_vertical: bool = True
    image_path: Optional[Path] = None
    blur_radius_frac: float = 0.05
    darken: float = 0.25


@dataclasses.dataclass(frozen=True)
class PrintMarks:
    """
    Optional bleed and trim marks for handing a collage to a print lab.

    Attributes:
        bleed_frac: Extra image area added outside the trim edge, as a fraction
            of the canvas short edge. The canvas grows by this on all sides.
        trim_marks: Draw corner crop marks at the trim line.
        mark_color: Colour of those marks.
    """

    bleed_frac: float = 0.0
    trim_marks: bool = False
    mark_color: RGB = (0, 0, 0)

    @property
    def enabled(self) -> bool:
        return self.bleed_frac > 0 or self.trim_marks


# --------------------------------------------------------------------------- #
# Face-aware cropping
# --------------------------------------------------------------------------- #
def face_aware_cover_box(
    image_width: int,
    image_height: int,
    target_aspect: float,
    face_boxes: Sequence[FaceBox] = (),
) -> tuple[int, int, int, int]:
    """
    Largest crop of ``target_aspect`` that keeps every face visible.

    Filling a cell whose shape differs from the photo's means discarding part
    of the photo. Centre-cropping is the naive choice and routinely cuts off
    heads -- especially with portrait photos in wide cells. So: take the
    largest window of the required aspect that fits inside the image, then
    slide it (never shrink it) so it contains the union of the detected face
    boxes, biased towards the faces' centre. With no faces, it centres.

    Returns ``(x0, y0, x1, y1)`` in pixels, always inside the image bounds.
    """
    if image_width <= 0 or image_height <= 0:
        raise CollageError(f"Invalid image size {image_width}x{image_height}")
    if target_aspect <= 0:
        raise CollageError(f"target_aspect must be > 0, got {target_aspect}")

    # Largest window of target_aspect fitting inside the image.
    if image_width / image_height > target_aspect:
        crop_h = image_height
        crop_w = max(1, min(image_width, round(crop_h * target_aspect)))
    else:
        crop_w = image_width
        crop_h = max(1, min(image_height, round(crop_w / target_aspect)))

    max_x = image_width - crop_w
    max_y = image_height - crop_h

    if not face_boxes:
        return _box_at(_clamp_int(round(max_x / 2), 0, max_x),
                       _clamp_int(round(max_y / 2), 0, max_y), crop_w, crop_h)

    # Union of faces in absolute pixels.
    fx0 = min(max(0.0, b[0]) for b in face_boxes) * image_width
    fy0 = min(max(0.0, b[1]) for b in face_boxes) * image_height
    fx1 = max(min(1.0, b[0] + b[2]) for b in face_boxes) * image_width
    fy1 = max(min(1.0, b[1] + b[3]) for b in face_boxes) * image_height

    # Aim to centre the faces, then pull back so the union stays inside the
    # window wherever that's geometrically possible.
    face_cx = (fx0 + fx1) / 2.0
    face_cy = (fy0 + fy1) / 2.0
    x = round(face_cx - crop_w / 2.0)
    y = round(face_cy - crop_h / 2.0)

    # Keep faces inside: the window must start left of the union's left edge
    # and end right of its right edge (when the union is narrower than the
    # window; otherwise centring on the union is the best available).
    if fx1 - fx0 <= crop_w:
        x = _clamp_int(x, math.ceil(fx1 - crop_w), math.floor(fx0))
    if fy1 - fy0 <= crop_h:
        # Slight upward bias: for head-and-shoulders framing it's better to
        # keep hair/forehead than an extra sliver of chest.
        y = _clamp_int(y, math.ceil(fy1 - crop_h), math.floor(fy0))

    return _box_at(_clamp_int(x, 0, max_x), _clamp_int(y, 0, max_y), crop_w, crop_h)


def _box_at(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    return (x, y, x + w, y + h)


def _clamp_int(value: int, low: int, high: int) -> int:
    if high < low:  # window larger than the allowed span; pin to `low`
        return low
    return max(low, min(high, int(value)))


def apply_filter(image: Image.Image, filter_name: str) -> Image.Image:
    """Apply a named colour filter from :data:`FILTERS`."""
    if filter_name in (FILTER_NONE, "", None):
        return image
    rgb = image.convert("RGB")
    if filter_name == FILTER_BW:
        return ImageOps.grayscale(rgb).convert("RGB")
    if filter_name == FILTER_SEPIA:
        grey = ImageOps.grayscale(rgb)
        return ImageOps.colorize(grey, black=(38, 24, 10), white=(255, 240, 205))
    if filter_name == FILTER_WARM:
        return _shift_channels(rgb, 1.08, 1.01, 0.92)
    if filter_name == FILTER_COOL:
        return _shift_channels(rgb, 0.92, 1.01, 1.10)
    if filter_name == FILTER_VIVID:
        return ImageEnhance.Color(ImageEnhance.Contrast(rgb).enhance(1.10)).enhance(1.35)
    raise CollageError(f"Unknown filter {filter_name!r}; expected one of {FILTERS}")


def _shift_channels(image: Image.Image, r: float, g: float, b: float) -> Image.Image:
    red, green, blue = image.split()
    return Image.merge(
        "RGB",
        (
            red.point(lambda v: min(255, int(v * r))),
            green.point(lambda v: min(255, int(v * g))),
            blue.point(lambda v: min(255, int(v * b))),
        ),
    )


def _adjusted_cover_box(
    photo: CollagePhoto, target_aspect: float
) -> tuple[int, int, int, int]:
    """Face-aware crop window, then apply this photo's zoom/pan."""
    width, height = photo.image.size
    box = face_aware_cover_box(width, height, target_aspect, photo.face_boxes)
    adjust = photo.adjust
    if adjust.zoom <= 1.0 + 1e-6 and not adjust.offset_x and not adjust.offset_y:
        return box

    x0, y0, x1, y1 = box
    zoom = max(1.0, float(adjust.zoom))
    box_w, box_h = (x1 - x0) / zoom, (y1 - y0) / zoom
    cx = (x0 + x1) / 2.0 + adjust.offset_x * width
    cy = (y0 + y1) / 2.0 + adjust.offset_y * height

    nx0 = _clamp_int(round(cx - box_w / 2), 0, max(0, width - round(box_w)))
    ny0 = _clamp_int(round(cy - box_h / 2), 0, max(0, height - round(box_h)))
    return (nx0, ny0, nx0 + max(1, round(box_w)), ny0 + max(1, round(box_h)))


def crop_to_cell(photo: CollagePhoto, cell: Cell) -> Image.Image:
    """
    Face-aware crop of ``photo``, with its per-photo adjustments, filling ``cell``.

    Order: rotate the source first (so the crop is taken from the rotated
    frame), then crop/zoom/pan, resize to the cell, then colour-filter and
    optionally beautify.
    """
    if cell.w <= 0 or cell.h <= 0:
        raise CollageError(f"Cell has no area: {cell}")

    source = photo.image
    adjust = photo.adjust
    working = photo
    if adjust.rotate_deg:
        source = source.rotate(adjust.rotate_deg, resample=Image.BICUBIC, expand=True)
        # Face boxes no longer map onto the rotated frame, so fall back to a
        # centred crop rather than protecting the wrong region.
        working = dataclasses.replace(photo, image=source, face_boxes=())

    box = _adjusted_cover_box(working, cell.w / cell.h)
    tile = source.crop(box).resize((cell.w, cell.h), Image.LANCZOS)

    if adjust.beautify:
        tile = _beautify_tile(tile)
    return apply_filter(tile, adjust.filter_name)


def _beautify_tile(tile: Image.Image) -> Image.Image:
    """
    Run the face-beautify pass on a tile, best-effort.

    Imported lazily and guarded: beautify is a nice-to-have here, and a failure
    (or a crop containing no face) must not take the whole collage down.
    """
    try:
        from core.face_beautify import BeautifyOptions, beautify

        return beautify(tile, BeautifyOptions.default_on())
    except Exception as exc:  # noqa: BLE001 - never break a collage over polish
        logger.warning("Collage: beautify failed on a tile (%s); using it as-is.", exc)
        return tile


def crop_cost(photo: CollagePhoto, cell_aspect: float) -> float:
    """
    How badly a photo mismatches a cell shape, as ``|log(ratio)|``.

    Symmetric (twice too wide costs the same as twice too tall) and ``0`` for a
    perfect match, which makes it usable as an assignment cost.
    """
    if cell_aspect <= 0 or photo.aspect <= 0:
        return 0.0
    return abs(math.log(photo.aspect / cell_aspect))


def assign_by_orientation(
    photos: Sequence[CollagePhoto], cells: Sequence[Cell]
) -> list[tuple[CollagePhoto, Cell]]:
    """
    Pair photos to cells so shapes match as closely as possible.

    Deterministic greedy match: both lists are sorted by aspect ratio and
    zipped, so portrait photos land in tall cells and landscape photos in wide
    ones, then results are emitted in the original cell order so the visual
    arrangement is unchanged. Ties break by index for stable output.
    """
    n = min(len(photos), len(cells))
    if n == 0:
        return []
    if n == 1:
        return [(photos[0], cells[0])]

    photos_by_aspect = sorted(range(n), key=lambda i: (photos[i].aspect, i))
    cells_by_aspect = sorted(range(n), key=lambda j: (cells[j].aspect, j))
    assignment: dict[int, int] = {}
    for photo_i, cell_j in zip(photos_by_aspect, cells_by_aspect):
        assignment[cell_j] = photo_i
    return [(photos[assignment[j]], cells[j]) for j in range(n)]


def pick_hero_index(photos: Sequence[CollagePhoto]) -> int:
    """
    Index of the photo best suited to a large feature slot.

    Prefers a prominent face (largest face area relative to the frame) since a
    portrait carries an enlargement better than a landscape does, and breaks
    ties on pixel count so an enlarged hero doesn't come out soft. Falls back
    to the highest-resolution photo when nothing has a detected face.
    """
    if not photos:
        raise CollageError("No photos to choose a hero from")

    def score(index: int) -> tuple[float, int]:
        photo = photos[index]
        face_area = max(
            (b[2] * b[3] for b in photo.face_boxes), default=0.0
        )
        return (face_area, photo.image.width * photo.image.height)

    return max(range(len(photos)), key=score)


# --------------------------------------------------------------------------- #
# Layout engines -- each returns cells that tile the usable area
# --------------------------------------------------------------------------- #
def _usable_area(spec: CollageSpec, theme: CollageTheme) -> tuple[int, int, int, int]:
    margin = round(theme.margin_frac * spec.short_edge)
    x0, y0 = margin, margin
    x1, y1 = spec.width_px - margin, spec.height_px - margin
    if x1 - x0 < 2 or y1 - y0 < 2:  # margins swallowed the canvas
        return 0, 0, spec.width_px, spec.height_px
    return x0, y0, x1, y1


def _spacing_px(spec: CollageSpec, theme: CollageTheme) -> int:
    return max(0, round(theme.spacing_frac * spec.short_edge))


def grid_dimensions(count: int, canvas_aspect: float) -> tuple[int, int]:
    """
    Column/row counts giving the squarest cells for ``count`` photos.

    Tries every column count and keeps whichever makes each cell closest to
    square on this canvas, so a wide canvas gets more columns automatically.
    """
    if count <= 0:
        raise CollageError("count must be > 0")
    best = (1, count, float("inf"))
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        cell_aspect = (canvas_aspect / cols) * rows
        penalty = abs(math.log(cell_aspect)) + 0.04 * (cols * rows - count)
        if penalty < best[2]:
            best = (cols, rows, penalty)
    return best[0], best[1]


def grid_cells(count: int, spec: CollageSpec, theme: CollageTheme) -> list[Cell]:
    """Equal cells in a near-square grid, exactly tiling the usable area."""
    cols, rows = grid_dimensions(count, spec.aspect)
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)

    total_w = (x1 - x0) - gap * (cols - 1)
    total_h = (y1 - y0) - gap * (rows - 1)
    if total_w <= 0 or total_h <= 0:
        raise CollageError("Spacing/margin leave no room for photos")

    cells: list[Cell] = []
    placed = 0
    for row in range(rows):
        # Last row may be short; centre it so the grid doesn't look broken.
        in_row = min(cols, count - placed)
        if in_row <= 0:
            break
        cell_w = total_w // cols
        cell_h = total_h // rows
        row_width = in_row * cell_w + gap * (in_row - 1)
        offset = x0 + ((x1 - x0) - row_width) // 2
        for col in range(in_row):
            cells.append(
                Cell(
                    x=offset + col * (cell_w + gap),
                    y=y0 + row * (cell_h + gap),
                    w=cell_w,
                    h=cell_h,
                )
            )
        placed += in_row
    return cells


def _partition_contiguous(aspects: Sequence[float], rows: int) -> list[list[int]]:
    """Split indices into ``rows`` contiguous groups of similar aspect sums."""
    total = sum(aspects) or 1.0
    target = total / rows
    groups: list[list[int]] = []
    current: list[int] = []
    running = 0.0
    for index, aspect in enumerate(aspects):
        remaining_rows = rows - len(groups)
        # Leave at least one photo for each remaining row.
        must_close = len(aspects) - index == remaining_rows - 1 and current
        if current and (running + aspect / 2 > target or must_close) and remaining_rows > 1:
            groups.append(current)
            current = [index]
            running = aspect
        else:
            current.append(index)
            running += aspect
    if current:
        groups.append(current)
    # Pad/trim defensively so callers always get exactly `rows` non-empty rows
    # when that's possible.
    while len(groups) < rows and any(len(g) > 1 for g in groups):
        biggest = max(range(len(groups)), key=lambda i: len(groups[i]))
        if len(groups[biggest]) < 2:
            break
        group = groups[biggest]
        half = len(group) // 2
        groups[biggest : biggest + 1] = [group[:half], group[half:]]
    return [g for g in groups if g]


def mosaic_cells(
    photos: Sequence[CollagePhoto], spec: CollageSpec, theme: CollageTheme
) -> list[Cell]:
    """
    Justified rows whose widths follow each photo's own aspect ratio.

    Rows all span the full width; within a row, a wide photo gets a wide cell.
    The number of rows isn't guessed -- every plausible count is tried and the
    one needing the least total cropping for *this* set of photos wins, which
    is what keeps a mixed portrait/landscape set from being mangled.
    """
    count = len(photos)
    if count == 0:
        raise CollageError("No photos to lay out")
    aspects = [p.aspect for p in photos]
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)
    avail_w, avail_h = x1 - x0, y1 - y0

    best_rows, best_cost = 1, float("inf")
    for rows in range(1, min(count, _MAX_REASONABLE_ROWS) + 1):
        groups = _partition_contiguous(aspects, rows)
        if len(groups) != rows:
            continue
        cost = _mosaic_cost(groups, aspects, avail_w, avail_h, gap)
        if cost < best_cost:
            best_rows, best_cost = rows, cost

    groups = _partition_contiguous(aspects, best_rows)
    return _mosaic_cells_for(groups, aspects, x0, y0, avail_w, avail_h, gap)


def _mosaic_cost(
    groups: list[list[int]],
    aspects: Sequence[float],
    avail_w: int,
    avail_h: int,
    gap: int,
) -> float:
    """Total aspect mismatch if these row groups were used (lower is better)."""
    cells = _mosaic_cells_for(groups, aspects, 0, 0, avail_w, avail_h, gap)
    if len(cells) != len(aspects):
        return float("inf")
    return sum(
        abs(math.log((c.w / c.h) / a)) if c.h and a > 0 else 0.0
        for c, a in zip(cells, aspects)
    )


def _mosaic_cells_for(
    groups: list[list[int]],
    aspects: Sequence[float],
    x0: int,
    y0: int,
    avail_w: int,
    avail_h: int,
    gap: int,
) -> list[Cell]:
    """Build cells for a fixed row partition, exactly filling the area."""
    rows = len(groups)
    if rows == 0:
        return []
    inner_h = avail_h - gap * (rows - 1)
    if inner_h <= 0:
        raise CollageError("Spacing leaves no room for photo rows")

    # A row's natural height is whatever makes its photos span the full width
    # at their own aspect ratios; taller rows hold squarer/portrait photos.
    natural: list[float] = []
    for group in groups:
        row_w = avail_w - gap * (len(group) - 1)
        aspect_sum = sum(aspects[i] for i in group) or 1.0
        natural.append(max(1.0, row_w / aspect_sum))
    scale = inner_h / (sum(natural) or 1.0)

    cells: list[Cell] = []
    y = y0
    for row_index, group in enumerate(groups):
        row_h = max(1, int(round(natural[row_index] * scale)))
        if row_index == rows - 1:  # absorb rounding so the last row lands flush
            row_h = max(1, (y0 + avail_h) - y)
        row_w = avail_w - gap * (len(group) - 1)
        aspect_sum = sum(aspects[i] for i in group) or 1.0
        x = x0
        for pos, photo_index in enumerate(group):
            width = max(1, int(round(row_w * aspects[photo_index] / aspect_sum)))
            if pos == len(group) - 1:  # flush to the right edge
                width = max(1, (x0 + avail_w) - x)
            cells.append(Cell(x=x, y=y, w=width, h=row_h))
            x += width + gap
        y += row_h + gap
    return cells


def feature_cells(count: int, spec: CollageSpec, theme: CollageTheme) -> list[Cell]:
    """
    One large hero cell plus a strip of smaller cells beside or below it.

    The hero takes the larger share along the canvas's long axis, so a
    landscape canvas gets hero-left/strip-right and a portrait canvas gets
    hero-top/strip-below.
    """
    if count <= 0:
        raise CollageError("count must be > 0")
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)
    avail_w, avail_h = x1 - x0, y1 - y0

    if count == 1:
        return [Cell(x=x0, y=y0, w=avail_w, h=avail_h)]

    others = count - 1
    hero_share = 0.62

    if spec.aspect >= 1.0:
        hero_w = max(1, int((avail_w - gap) * hero_share))
        strip_w = max(1, avail_w - gap - hero_w)
        cells = [Cell(x=x0, y=y0, w=hero_w, h=avail_h)]
        strip_x = x0 + hero_w + gap
        inner = avail_h - gap * (others - 1)
        each = max(1, inner // others)
        y = y0
        for i in range(others):
            h = each if i < others - 1 else max(1, (y0 + avail_h) - y)
            cells.append(Cell(x=strip_x, y=y, w=strip_w, h=h))
            y += h + gap
        return cells

    hero_h = max(1, int((avail_h - gap) * hero_share))
    strip_h = max(1, avail_h - gap - hero_h)
    cells = [Cell(x=x0, y=y0, w=avail_w, h=hero_h)]
    strip_y = y0 + hero_h + gap
    inner = avail_w - gap * (others - 1)
    each = max(1, inner // others)
    x = x0
    for i in range(others):
        w = each if i < others - 1 else max(1, (x0 + avail_w) - x)
        cells.append(Cell(x=x, y=strip_y, w=w, h=strip_h))
        x += w + gap
    return cells


def scatter_cells(
    count: int, spec: CollageSpec, theme: CollageTheme, seed: int = 0
) -> list[Cell]:
    """
    Loosely placed, slightly rotated cells that may overlap.

    Starts from a grid so coverage stays even, then jitters each cell's
    position, size and angle. Deterministic for a given ``seed``.
    """
    if count <= 0:
        raise CollageError("count must be > 0")
    rng = random.Random(seed)
    base = grid_cells(count, spec, theme)
    cells: list[Cell] = []
    for cell in base:
        grow = rng.uniform(1.02, 1.20)
        w = max(1, int(cell.w * grow))
        h = max(1, int(cell.h * grow))
        jitter_x = int(cell.w * rng.uniform(-0.10, 0.10))
        jitter_y = int(cell.h * rng.uniform(-0.10, 0.10))
        cells.append(
            Cell(
                x=cell.x + jitter_x,
                y=cell.y + jitter_y,
                w=w,
                h=h,
                rotation_deg=rng.uniform(-9.0, 9.0),
            )
        )
    return cells


def masonry_cells(
    photos: Sequence[CollagePhoto], spec: CollageSpec, theme: CollageTheme
) -> list[Cell]:
    """
    Pinterest-style columns of equal width and varying heights.

    The transpose of the mosaic: columns span the full height, and each photo's
    height follows its own aspect ratio. Suits portrait-heavy sets, where the
    mosaic's full-width rows would force landscape-ish crops.
    """
    count = len(photos)
    if count == 0:
        raise CollageError("No photos to lay out")
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)
    avail_w, avail_h = x1 - x0, y1 - y0

    # Column count aimed at roughly square cells, then clamped to the count.
    aspects = [p.aspect for p in photos]
    mean_aspect = sum(aspects) / len(aspects)
    ideal = math.sqrt(max(1e-6, count * spec.aspect / max(1e-6, mean_aspect)))
    columns = max(1, min(count, round(ideal)))

    # Deal photos into the currently-shortest column so heights stay even.
    buckets: list[list[int]] = [[] for _ in range(columns)]
    heights = [0.0] * columns
    col_w = (avail_w - gap * (columns - 1)) / columns
    if col_w <= 0:
        raise CollageError("Spacing leaves no room for photo columns")
    for index, aspect in enumerate(aspects):
        target = min(range(columns), key=lambda c: heights[c])
        buckets[target].append(index)
        heights[target] += col_w / max(1e-6, aspect)

    cells: list[Cell] = [None] * count  # type: ignore[list-item]
    for col, bucket in enumerate(buckets):
        if not bucket:
            continue
        natural = [col_w / max(1e-6, aspects[i]) for i in bucket]
        inner_h = avail_h - gap * (len(bucket) - 1)
        scale = inner_h / max(1e-6, sum(natural))
        x = int(round(x0 + col * (col_w + gap)))
        width = int(round(col_w))
        y = y0
        for pos, photo_index in enumerate(bucket):
            h = max(1, int(round(natural[pos] * scale)))
            if pos == len(bucket) - 1:  # flush to the bottom edge
                h = max(1, (y0 + avail_h) - y)
            cells[photo_index] = Cell(x=x, y=y, w=width, h=h)
            y += h + gap
    return [c for c in cells if c is not None]


def filmstrip_cells(count: int, spec: CollageSpec, theme: CollageTheme) -> list[Cell]:
    """
    A single row (or column) of equal cells, like a strip of film.

    Follows the canvas: a wide canvas gets one row, a tall canvas one column.
    """
    if count <= 0:
        raise CollageError("count must be > 0")
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)
    avail_w, avail_h = x1 - x0, y1 - y0

    if spec.aspect >= 1.0:
        inner = avail_w - gap * (count - 1)
        each = max(1, inner // count)
        cells, x = [], x0
        for i in range(count):
            w = each if i < count - 1 else max(1, (x0 + avail_w) - x)
            cells.append(Cell(x=x, y=y0, w=w, h=avail_h))
            x += w + gap
        return cells

    inner = avail_h - gap * (count - 1)
    each = max(1, inner // count)
    cells, y = [], y0
    for i in range(count):
        h = each if i < count - 1 else max(1, (y0 + avail_h) - y)
        cells.append(Cell(x=x0, y=y, w=avail_w, h=h))
        y += h + gap
    return cells


def magazine_cells(count: int, spec: CollageSpec, theme: CollageTheme) -> list[Cell]:
    """
    Editorial "1 + N" look: a full-bleed-feeling hero band above a row below.

    Differs from ``feature`` in that the hero spans the full width rather than
    sharing a side with a vertical strip, which reads more like a magazine
    opening spread.
    """
    if count <= 0:
        raise CollageError("count must be > 0")
    x0, y0, x1, y1 = _usable_area(spec, theme)
    gap = _spacing_px(spec, theme)
    avail_w, avail_h = x1 - x0, y1 - y0

    if count == 1:
        return [Cell(x=x0, y=y0, w=avail_w, h=avail_h)]

    hero_h = max(1, int((avail_h - gap) * 0.58))
    rest_h = max(1, avail_h - gap - hero_h)
    cells = [Cell(x=x0, y=y0, w=avail_w, h=hero_h)]

    others = count - 1
    # More than four in the bottom band gets cramped, so wrap into two rows.
    if others <= 4:
        rows = [others]
    else:
        half = (others + 1) // 2
        rows = [half, others - half]

    band_y = y0 + hero_h + gap
    row_h_total = rest_h - gap * (len(rows) - 1)
    row_h = max(1, row_h_total // len(rows))
    for row_index, in_row in enumerate(rows):
        inner = avail_w - gap * (in_row - 1)
        each = max(1, inner // in_row)
        x = x0
        y = band_y + row_index * (row_h + gap)
        h = row_h if row_index < len(rows) - 1 else max(1, (y0 + avail_h) - y)
        for i in range(in_row):
            w = each if i < in_row - 1 else max(1, (x0 + avail_w) - x)
            cells.append(Cell(x=x, y=y, w=w, h=h))
            x += w + gap
    return cells


def layout_cells(
    photos: Sequence[CollagePhoto],
    spec: CollageSpec,
    theme: CollageTheme,
    layout: str = LAYOUT_MOSAIC,
    seed: int = 0,
) -> list[Cell]:
    """Cells for the requested layout. Raises on an unknown layout name."""
    count = len(photos)
    if count == 0:
        raise CollageError("Add at least one photo to build a collage")
    if layout == LAYOUT_GRID:
        return grid_cells(count, spec, theme)
    if layout == LAYOUT_MOSAIC:
        return mosaic_cells(photos, spec, theme)
    if layout == LAYOUT_FEATURE:
        return feature_cells(count, spec, theme)
    if layout == LAYOUT_SCATTER:
        return scatter_cells(count, spec, theme, seed=seed)
    if layout == LAYOUT_MASONRY:
        return masonry_cells(photos, spec, theme)
    if layout == LAYOUT_FILMSTRIP:
        return filmstrip_cells(count, spec, theme)
    if layout == LAYOUT_MAGAZINE:
        return magazine_cells(count, spec, theme)
    raise CollageError(f"Unknown layout {layout!r}; expected one of {LAYOUTS}")


def suggest_layout(photos: Sequence[CollagePhoto], spec: CollageSpec) -> str:
    """
    A sensible layout for this particular photo set.

    Mixed orientations benefit most from the mosaic (it bends the cells to the
    photos instead of cropping them); a uniform set looks tidier in a grid; and
    one clearly dominant face suggests a feature layout.
    """
    if not photos:
        return LAYOUT_MOSAIC
    if len(photos) == 1:
        return LAYOUT_FEATURE
    aspects = [p.aspect for p in photos]
    spread = max(aspects) / max(1e-6, min(aspects))
    if spread > 1.6:
        return LAYOUT_MOSAIC
    return LAYOUT_GRID


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if radius > 0:
        draw.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    else:
        draw.rectangle([0, 0, size[0] - 1, size[1] - 1], fill=255)
    return mask


def _decorate(tile: Image.Image, theme: CollageTheme, short_edge: int) -> Image.Image:
    """Apply border + rounded corners, returning an RGBA tile."""
    border = max(0, round(theme.border_px_frac * short_edge))
    radius = max(0, round(theme.corner_radius_frac * short_edge))

    if border > 0:
        framed = Image.new(
            "RGB",
            (tile.width + border * 2, tile.height + border * 2),
            theme.border_color,
        )
        framed.paste(tile, (border, border))
        tile = framed

    rgba = tile.convert("RGBA")
    if radius > 0:
        rgba.putalpha(_rounded_mask(rgba.size, radius))
    return rgba


def _paste_with_shadow(
    canvas: Image.Image,
    tile: Image.Image,
    position: tuple[int, int],
    theme: CollageTheme,
    short_edge: int,
) -> None:
    """Composite an RGBA tile onto the canvas, with an optional soft shadow."""
    x, y = position
    if theme.shadow:
        blur = max(2, round(0.006 * short_edge))
        offset = max(1, round(0.003 * short_edge))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        silhouette = Image.new(
            "RGBA", tile.size, (0, 0, 0, max(0, min(255, theme.shadow_opacity)))
        )
        silhouette.putalpha(
            Image.composite(
                silhouette.getchannel("A"),
                Image.new("L", tile.size, 0),
                tile.getchannel("A"),
            )
        )
        shadow.paste(silhouette, (x + offset, y + offset), silhouette)
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        canvas.alpha_composite(shadow)
    canvas.alpha_composite(tile, dest=(max(0, x), max(0, y)))


def render_background(
    spec: CollageSpec,
    theme: CollageTheme,
    background: Optional[Background] = None,
    photos: Sequence[CollagePhoto] = (),
) -> Image.Image:
    """
    Build the backdrop canvas as RGB at ``spec`` size.

    ``None`` (or the ``solid`` style) falls back to the theme's own background
    colour, which is what keeps the simple case simple.
    """
    size = (spec.width_px, spec.height_px)
    if background is None or background.style == BG_SOLID:
        color = theme.background if background is None else background.color
        return Image.new("RGB", size, color)

    if background.style == BG_GRADIENT:
        return _gradient(size, background.color, background.color2,
                         background.gradient_vertical)

    if background.style == BG_IMAGE:
        if not background.image_path:
            raise CollageError("Background style 'image' needs an image_path")
        try:
            with Image.open(background.image_path) as opened:
                opened.load()
                source = opened.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise CollageError(
                f"Could not open background image '{background.image_path}': {exc}"
            ) from exc
        return _darken(_cover(source, size), background.darken)

    if background.style == BG_BLURRED_PHOTO:
        if not photos:
            return Image.new("RGB", size, theme.background)
        # First photo is the backdrop: blown up, blurred and darkened so the
        # collage on top stays readable. A very common, well-liked look.
        blurred = _cover(photos[0].image.convert("RGB"), size)
        radius = max(2, round(background.blur_radius_frac * spec.short_edge))
        blurred = blurred.filter(ImageFilter.GaussianBlur(radius))
        return _darken(blurred, background.darken)

    raise CollageError(
        f"Unknown background style {background.style!r}; "
        f"expected one of {BACKGROUND_STYLES}"
    )


def _gradient(size: tuple[int, int], start: RGB, end: RGB, vertical: bool) -> Image.Image:
    """Two-stop linear gradient, drawn small and scaled up (cheap and smooth)."""
    width, height = size
    steps = max(2, (height if vertical else width))
    strip = Image.new("RGB", (1, steps) if vertical else (steps, 1))
    pixels = strip.load()
    for i in range(steps):
        t = i / (steps - 1)
        color = tuple(round(start[c] + (end[c] - start[c]) * t) for c in range(3))
        if vertical:
            pixels[0, i] = color
        else:
            pixels[i, 0] = color
    return strip.resize(size, Image.BILINEAR)


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale-and-centre-crop ``image`` to exactly fill ``size``."""
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    scaled = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )
    left = (scaled.width - target_w) // 2
    top = (scaled.height - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def _darken(image: Image.Image, amount: float) -> Image.Image:
    amount = max(0.0, min(1.0, float(amount)))
    if amount <= 0:
        return image
    return ImageEnhance.Brightness(image).enhance(1.0 - amount)


# --------------------------------------------------------------------------- #
# Print safety
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class ResolutionWarning:
    """One photo that will be visibly soft at the chosen output size."""

    photo_index: int
    name: str
    source_px: tuple[int, int]
    cell_px: tuple[int, int]
    effective_ppi: float

    @property
    def message(self) -> str:
        return (
            f"{self.name}: {self.source_px[0]}x{self.source_px[1]}px stretched into a "
            f"{self.cell_px[0]}x{self.cell_px[1]}px slot "
            f"(~{self.effective_ppi:.0f} ppi) — may look soft in print."
        )


def check_resolution(
    photos: Sequence[CollagePhoto],
    cells: Sequence[Cell],
    spec: CollageSpec,
    min_ppi: int = MIN_PRINT_PPI,
) -> list[ResolutionWarning]:
    """
    Flag photos that must be upscaled to fill their cell.

    A collage can silently turn a small phone photo into a soft, blurry panel
    on an A4 print. This reports the effective ppi per photo so the studio finds
    out *before* sending the file to the lab, not after.
    """
    warnings: list[ResolutionWarning] = []
    for index, (photo, cell) in enumerate(zip(photos, cells)):
        if cell.w <= 0 or cell.h <= 0:
            continue
        # How many source pixels land per output pixel, then convert to ppi at
        # the spec's dpi.
        scale = min(photo.image.width / cell.w, photo.image.height / cell.h)
        effective_ppi = scale * spec.dpi
        if effective_ppi < min_ppi:
            warnings.append(
                ResolutionWarning(
                    photo_index=index,
                    name=(photo.path.name if photo.path else f"photo {index + 1}"),
                    source_px=photo.image.size,
                    cell_px=(cell.w, cell.h),
                    effective_ppi=effective_ppi,
                )
            )
    return warnings


def add_print_marks(image: Image.Image, marks: PrintMarks, spec: CollageSpec) -> Image.Image:
    """
    Add bleed margin and/or corner trim marks around a finished collage.

    Bleed extends the canvas by mirroring the edges outward, so trimming
    slightly off-register doesn't expose white paper.
    """
    if not marks.enabled:
        return image

    bleed = max(0, round(marks.bleed_frac * spec.short_edge))
    if bleed > 0:
        canvas = ImageOps.expand(image, border=bleed, fill=(255, 255, 255))
        # Mirror the outer strips into the bleed so a slightly-off cut still
        # shows image, not paper.
        top = image.crop((0, 0, image.width, 1)).resize((image.width, bleed))
        bottom = image.crop((0, image.height - 1, image.width, image.height)).resize(
            (image.width, bleed)
        )
        canvas.paste(top, (bleed, 0))
        canvas.paste(bottom, (bleed, bleed + image.height))
        left = canvas.crop((bleed, 0, bleed + 1, canvas.height)).resize(
            (bleed, canvas.height)
        )
        right = canvas.crop(
            (bleed + image.width - 1, 0, bleed + image.width, canvas.height)
        ).resize((bleed, canvas.height))
        canvas.paste(left, (0, 0))
        canvas.paste(right, (bleed + image.width, 0))
        image = canvas

    if marks.trim_marks:
        draw = ImageDraw.Draw(image)
        length = max(6, round(0.02 * spec.short_edge))
        thickness = max(1, round(0.0015 * spec.short_edge))
        # Trim line sits where the original canvas edges are.
        for x in (bleed, bleed + spec.width_px):
            for y in (bleed, bleed + spec.height_px):
                draw.line([(x - length, y), (x + length, y)],
                          fill=marks.mark_color, width=thickness)
                draw.line([(x, y - length), (x, y + length)],
                          fill=marks.mark_color, width=thickness)
    return image


def build_collage(
    photos: Sequence[CollagePhoto],
    spec: CollageSpec,
    theme: Optional[CollageTheme] = None,
    layout: str = LAYOUT_MOSAIC,
    seed: int = 0,
    shape_aware: bool = True,
    background: Optional[Background] = None,
    shape: Optional[str] = None,
    shape_text: str = "",
    text_overlays: Sequence = (),
    watermark=None,
    marks: Optional[PrintMarks] = None,
) -> Image.Image:
    """
    Render a finished collage.

    Args:
        photos: The photos to place, in the order the user arranged them.
        spec: Output canvas geometry.
        theme: Visual treatment; defaults to :data:`DEFAULT_THEME`.
        layout: One of :data:`LAYOUTS`.
        seed: Varies the scatter layout and hero choice shuffling. Same seed,
            same collage.
        shape_aware: Pair photos to the cells whose shape they fit best. Turn
            off to place strictly in the given order.

    Returns:
        An RGB image of exactly ``spec.width_px`` x ``spec.height_px``.

    Raises:
        CollageError: for an empty photo list, unknown layout, or a spec whose
            margins/spacing leave no room.
    """
    if not photos:
        raise CollageError("Add at least one photo to build a collage")
    theme = theme or THEMES[DEFAULT_THEME]

    ordered = list(photos)
    if layout in (LAYOUT_FEATURE, LAYOUT_MAGAZINE) and len(ordered) > 1:
        # The hero cell is first in both of these, so move the best candidate
        # to the front.
        hero = pick_hero_index(ordered)
        ordered.insert(0, ordered.pop(hero))

    cells = layout_cells(ordered, spec, theme, layout=layout, seed=seed)
    if not cells:
        raise CollageError("Layout produced no cells")

    # Shape matching would fight the deliberate hero-first ordering, and the
    # mosaic/masonry engines already size cells from the photos' own aspects.
    if shape_aware and layout in (LAYOUT_GRID, LAYOUT_SCATTER, LAYOUT_FILMSTRIP):
        pairs = assign_by_orientation(ordered, cells)
    else:
        pairs = list(zip(ordered, cells))

    backdrop = render_background(spec, theme, background, ordered)
    canvas = backdrop.convert("RGBA")
    short_edge = spec.short_edge

    # Photos are composited onto their own transparent layer so a shape mask
    # can clip *them* without also cutting a hole in the background.
    photo_layer = Image.new("RGBA", (spec.width_px, spec.height_px), (0, 0, 0, 0))
    for photo, cell in pairs:
        try:
            tile = crop_to_cell(photo, cell)
        except Exception as exc:  # noqa: BLE001 - one bad photo shouldn't kill the collage
            logger.warning("Skipping a photo that could not be cropped: %s", exc)
            continue
        decorated = _decorate(tile, theme, short_edge)
        if cell.rotation_deg:
            decorated = decorated.rotate(
                cell.rotation_deg, resample=Image.BICUBIC, expand=True
            )
        # Rotation/border grow the tile; keep it centred on the original cell.
        px = cell.x - (decorated.width - cell.w) // 2
        py = cell.y - (decorated.height - cell.h) // 2
        _paste_with_shadow(photo_layer, decorated, (px, py), theme, short_edge)

    if shape:
        from core.collage_shapes import shape_mask  # local: avoids a cycle

        mask = shape_mask(shape, (spec.width_px, spec.height_px), text=shape_text)
        photo_layer.putalpha(
            Image.composite(
                photo_layer.getchannel("A"), Image.new("L", photo_layer.size, 0), mask
            )
        )

    canvas.alpha_composite(photo_layer)
    result = canvas.convert("RGB")

    if text_overlays or watermark is not None:
        from core.collage_text import draw_text_overlays, draw_watermark

        if text_overlays:
            result = draw_text_overlays(result, text_overlays, spec)
        if watermark is not None:
            result = draw_watermark(result, watermark, spec)

    if marks is not None and marks.enabled:
        result = add_print_marks(result, marks, spec)

    logger.info(
        "Built %s collage: %d photos, %dx%dpx, theme=%s%s",
        layout, len(pairs), spec.width_px, spec.height_px, theme.name,
        f", shape={shape}" if shape else "",
    )
    return result


def save_collage(image: Image.Image, path: PathLike, dpi: int = DEFAULT_DPI) -> Path:
    """
    Save a collage, choosing the format from the extension.

    Returns the written path. Raises :class:`CollageError` on write failure.
    """
    out = Path(path)
    suffix = out.suffix.lower()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if suffix in (".jpg", ".jpeg"):
            image.convert("RGB").save(out, "JPEG", quality=95, dpi=(dpi, dpi))
        elif suffix == ".png":
            image.save(out, "PNG", dpi=(dpi, dpi))
        elif suffix == ".pdf":
            image.convert("RGB").save(out, "PDF", resolution=float(dpi))
        else:
            raise CollageError(
                f"Unsupported collage format '{suffix}'; use .jpg, .png or .pdf"
            )
    except CollageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CollageError(f"Could not save collage to '{out}': {exc}") from exc
    return out
