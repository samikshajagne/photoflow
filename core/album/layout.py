"""
Album layout engine for PhotoFlow.

This module is **pure geometry**: it decides *where* photos go on printed
album spreads and *what region* of each source photo is shown, without ever
decoding an image. Inputs describe each photo abstractly (its path, its
aspect ratio, and any face boxes in relative coordinates); outputs describe
pixel rectangles on the spread and the relative source crop to draw there.

The pipeline is::

    items + spec  ->  chunks  ->  template (relative frames)
                  ->  absolute pixel frames (margins/bleed/gutter honored)
                  ->  cover-fit crop per frame (face-safe)  ->  Spread

Key geometric ideas:

- An :class:`AlbumSpec` describes the physical page (inches + DPI) and the
  printing furniture: ``bleed`` (extra ink past the trim edge), ``margin``
  (safe area kept clear of content), and ``gutter`` (the binding seam down
  the middle of a double-page spread). All sizes are validated up front.
- A *template* is a tuple of :class:`Frame` rectangles in relative ``[0, 1]``
  coordinates over the usable (inside-margins) area of the spread. Built-in
  templates exist for 1-4 photos; any other count falls back to an auto grid.
- Each frame is converted to absolute spread pixels, then a **cover-fit**
  crop is computed so the photo fills the frame with the overflow cropped
  away. The crop window is then shifted to keep face boxes visible and pull
  faces away from the gutter (binding) when there is slack.

Everything here is deterministic: the same inputs always yield byte-identical
:class:`Spread` outputs.

Scope: layout geometry only -- no image decoding, no I/O, no new pip deps.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Optional

from core.album.facecrop import face_safe_cover_crop
from core.album.pacing import PACING_UNIFORM, chunk_by_counts, pace_counts
from utils.logger import get_logger

logger = get_logger(__name__)

# A relative rectangle is (x, y, w, h) with each value in [0, 1].
RelRect = tuple[float, float, float, float]
# A pixel rectangle is (x, y, w, h) in integer spread pixels.
PixRect = tuple[int, int, int, int]

# Tolerance for floating-point relative-coordinate bounds checks.
_EPS: float = 1e-6


class AlbumLayoutError(Exception):
    """Raised when album layout inputs are invalid or cannot be laid out."""


@dataclasses.dataclass(frozen=True)
class AlbumSpec:
    """
    Physical specification of an album spread.

    Sizes are given in inches; pixel dimensions are derived via ``dpi``. A
    *spread* is the visible canvas laid out at once: two facing pages for a
    double-page spread, otherwise a single page.

    Attributes:
        page_width_in: Trim width of a single page, in inches (> 0).
        page_height_in: Trim height of a single page, in inches (> 0).
        dpi: Dots per inch used to rasterize the spread (integer >= 1).
        bleed_in: Extra ink area past the trim edge, in inches (>= 0).
        margin_in: Safe margin kept clear of content, in inches (>= 0).
        gutter_in: Width of the binding seam down the middle of a double-page
            spread, in inches (>= 0); ignored for single pages.
        double_page_spread: When ``True`` (default) a spread is two facing
            pages side by side; when ``False`` a spread is one page.

    Raises:
        AlbumLayoutError: if any dimension is non-positive, ``dpi`` < 1, or
            any of ``bleed``/``margin``/``gutter`` is negative.
    """

    page_width_in: float
    page_height_in: float
    dpi: int
    bleed_in: float = 0.125
    margin_in: float = 0.25
    gutter_in: float = 0.0
    double_page_spread: bool = True

    def __post_init__(self) -> None:
        if self.page_width_in <= 0:
            raise AlbumLayoutError(
                f"page_width_in must be > 0, got {self.page_width_in}"
            )
        if self.page_height_in <= 0:
            raise AlbumLayoutError(
                f"page_height_in must be > 0, got {self.page_height_in}"
            )
        if self.dpi < 1:
            raise AlbumLayoutError(f"dpi must be >= 1, got {self.dpi}")
        for name, value in (
            ("bleed_in", self.bleed_in),
            ("margin_in", self.margin_in),
            ("gutter_in", self.gutter_in),
        ):
            if value < 0:
                raise AlbumLayoutError(f"{name} must be >= 0, got {value}")

    @property
    def spread_width_in(self) -> float:
        """Width of the spread in inches (two pages wide if double-page)."""
        return self.page_width_in * 2 if self.double_page_spread else self.page_width_in

    @property
    def spread_height_in(self) -> float:
        """Height of the spread in inches (always one page tall)."""
        return self.page_height_in

    @property
    def spread_width_px(self) -> int:
        """Spread width in pixels, ``round(spread_width_in * dpi)``."""
        return round(self.spread_width_in * self.dpi)

    @property
    def spread_height_px(self) -> int:
        """Spread height in pixels, ``round(spread_height_in * dpi)``."""
        return round(self.spread_height_in * self.dpi)


@dataclasses.dataclass(frozen=True)
class PhotoItem:
    """
    An abstract photo to place: no pixels, just geometry.

    Attributes:
        path: Path/identifier of the source photo (opaque to this module).
        aspect_ratio: Source width / height, must be > 0.
        face_boxes: Relative ``(x, y, w, h)`` face rectangles over the source
            image, each value in ``[0, 1]`` (default: no faces).

    Raises:
        AlbumLayoutError: if ``aspect_ratio`` <= 0 or any face box is outside
            ``[0, 1]`` / has non-positive size.
    """

    path: str
    aspect_ratio: float
    face_boxes: tuple[RelRect, ...] = ()

    def __post_init__(self) -> None:
        if self.aspect_ratio <= 0:
            raise AlbumLayoutError(
                f"aspect_ratio must be > 0, got {self.aspect_ratio}"
            )
        for box in self.face_boxes:
            if len(box) != 4:
                raise AlbumLayoutError(
                    f"face box must be (x, y, w, h), got {box!r}"
                )
            x, y, w, h = box
            if w <= 0 or h <= 0:
                raise AlbumLayoutError(
                    f"face box must have positive width/height, got {box!r}"
                )
            if (
                x < -_EPS
                or y < -_EPS
                or x + w > 1 + _EPS
                or y + h > 1 + _EPS
            ):
                raise AlbumLayoutError(
                    f"face box must lie within [0, 1], got {box!r}"
                )


@dataclasses.dataclass(frozen=True)
class Frame:
    """
    A relative rectangle on the spread's usable area, in ``[0, 1]``.

    Coordinates are fractions of the inside-margins region of the spread,
    with the origin at its top-left.

    Attributes:
        x: Left edge (>= 0).
        y: Top edge (>= 0).
        w: Width (> 0); ``x + w`` must not exceed 1.
        h: Height (> 0); ``y + h`` must not exceed 1.

    Raises:
        AlbumLayoutError: if the rectangle is empty or escapes ``[0, 1]``.
    """

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        if self.w <= 0 or self.h <= 0:
            raise AlbumLayoutError(
                f"Frame must have positive width/height, got {self!r}"
            )
        if (
            self.x < -_EPS
            or self.y < -_EPS
            or self.x + self.w > 1 + _EPS
            or self.y + self.h > 1 + _EPS
        ):
            raise AlbumLayoutError(f"Frame must lie within [0, 1], got {self!r}")


# How a photo fills its frame.
FIT_COVER = "cover"      # scale to fill the frame, cropping the overflow
FIT_CONTAIN = "contain"  # scale to fit the whole photo inside, letterboxed


@dataclasses.dataclass(frozen=True)
class Placement:
    """
    A single photo placed on a spread.

    Attributes:
        path: The source photo's path/identifier.
        frame_px: ``(x, y, w, h)`` pixel rectangle on the spread the photo is
            drawn into.
        crop: ``(x, y, w, h)`` relative region of the *source* photo that is
            shown, each value in ``[0, 1]``. For ``fit == "cover"`` this is the
            face-safe cover crop; for ``"contain"`` it is the full image
            ``(0, 0, 1, 1)`` (nothing is cropped).
        fit: ``"cover"`` (fill the frame, cropping overflow — used for full-bleed
            heroes) or ``"contain"`` (fit the whole photo inside the frame on a
            background, so no one is cropped — used for collage cells).
    """

    path: str
    frame_px: PixRect
    crop: RelRect
    fit: str = FIT_COVER


@dataclasses.dataclass(frozen=True)
class Spread:
    """
    One laid-out spread.

    Attributes:
        index: Zero-based spread index in the album.
        width_px: Spread width in pixels.
        height_px: Spread height in pixels.
        placements: One :class:`Placement` per photo on this spread, in input
            order.
    """

    index: int
    width_px: int
    height_px: int
    placements: tuple[Placement, ...]


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
# Built-in templates for small photo counts. Each is a tuple of relative
# frames over the usable area. A small inner gap keeps neighboring frames from
# touching so they read as distinct photos. Frames never overlap and always
# stay within [0, 1].
_GAP: float = 0.02


def template_for(count: int) -> tuple[Frame, ...]:
    """
    Return relative frames for laying out ``count`` photos on a spread.

    Built-in arrangements:

    - ``1``: a single full-area frame.
    - ``2``: two side-by-side columns.
    - ``3``: one large frame on the left, two stacked on the right.
    - ``4``: a 2x2 grid.

    Any other positive count falls back to an automatic near-square ``N x M``
    grid sized to hold exactly ``count`` frames (filling row by row). The
    returned frames never overlap and all lie within ``[0, 1]``.

    Args:
        count: Number of photos to place (must be >= 1).

    Returns:
        A tuple of exactly ``count`` :class:`Frame` objects.

    Raises:
        AlbumLayoutError: if ``count`` < 1.
    """
    if count < 1:
        raise AlbumLayoutError(f"count must be >= 1, got {count}")

    half_gap = _GAP / 2.0

    if count == 1:
        return (Frame(0.0, 0.0, 1.0, 1.0),)

    if count == 2:
        return (
            Frame(0.0, 0.0, 0.5 - half_gap, 1.0),
            Frame(0.5 + half_gap, 0.0, 0.5 - half_gap, 1.0),
        )

    if count == 3:
        left_w = 0.5 - half_gap
        right_x = 0.5 + half_gap
        right_w = 0.5 - half_gap
        top_h = 0.5 - half_gap
        bottom_y = 0.5 + half_gap
        bottom_h = 0.5 - half_gap
        return (
            Frame(0.0, 0.0, left_w, 1.0),
            Frame(right_x, 0.0, right_w, top_h),
            Frame(right_x, bottom_y, right_w, bottom_h),
        )

    if count == 4:
        cell_w = 0.5 - half_gap
        cell_h = 0.5 - half_gap
        x1 = 0.5 + half_gap
        y1 = 0.5 + half_gap
        return (
            Frame(0.0, 0.0, cell_w, cell_h),
            Frame(x1, 0.0, cell_w, cell_h),
            Frame(0.0, y1, cell_w, cell_h),
            Frame(x1, y1, cell_w, cell_h),
        )

    return _grid_template(count)


def _grid_template(count: int) -> tuple[Frame, ...]:
    """
    Auto near-square grid holding exactly ``count`` frames, filled row by row.

    Columns = ceil(sqrt(count)); rows = ceil(count / columns). Cells are
    uniformly sized with a small inter-cell gap; the last (possibly partial)
    row is left-aligned. Only ``count`` frames are emitted.
    """
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)

    # Cell extent including the gap budget; subtract the gap so cells don't
    # touch. With ``cols`` cells there are ``cols`` slots of width 1/cols.
    cell_w = 1.0 / cols
    cell_h = 1.0 / rows
    inner_w = cell_w - _GAP if cell_w > _GAP else cell_w
    inner_h = cell_h - _GAP if cell_h > _GAP else cell_h

    frames: list[Frame] = []
    for i in range(count):
        r = i // cols
        c = i % cols
        x = c * cell_w
        y = r * cell_h
        frames.append(Frame(x, y, inner_w, inner_h))
    return tuple(frames)


# --------------------------------------------------------------------------- #
# Designed template library + orientation-aware selection
# --------------------------------------------------------------------------- #
# Each photo count maps to several *variant* arrangements. The engine picks the
# variant whose frame orientations best match the photos on the spread (and
# rotates among equally-good variants for visual variety), so albums read as
# designed spreads rather than uniform grids. Builders space frames with the
# shared ``_GAP`` so cells never touch and stay within ``[0, 1]``.
import math as _math  # local alias; module already imports math at top


def _row(n: int) -> tuple[Frame, ...]:
    """``n`` equal full-height columns."""
    g = _GAP
    w = (1.0 - g * (n - 1)) / n
    return tuple(Frame(i * (w + g), 0.0, w, 1.0) for i in range(n))


def _col(n: int) -> tuple[Frame, ...]:
    """``n`` equal full-width rows."""
    g = _GAP
    h = (1.0 - g * (n - 1)) / n
    return tuple(Frame(0.0, i * (h + g), 1.0, h) for i in range(n))


def _rows_split(per_row: tuple[int, ...]) -> tuple[Frame, ...]:
    """Stacked rows, each with its own number of equal columns."""
    g = _GAP
    rows = len(per_row)
    h = (1.0 - g * (rows - 1)) / rows
    frames: list[Frame] = []
    for r, cols in enumerate(per_row):
        y = r * (h + g)
        w = (1.0 - g * (cols - 1)) / cols
        for c in range(cols):
            frames.append(Frame(c * (w + g), y, w, h))
    return tuple(frames)


def _left_big_plus_col(k: int) -> tuple[Frame, ...]:
    """A big photo on the left half, ``k`` stacked on the right half."""
    g = _GAP
    hg = g / 2.0
    left = Frame(0.0, 0.0, 0.5 - hg, 1.0)
    rx, rw = 0.5 + hg, 0.5 - hg
    h = (1.0 - g * (k - 1)) / k
    rights = [Frame(rx, i * (h + g), rw, h) for i in range(k)]
    return (left, *rights)


def _top_big_plus_row(k: int) -> tuple[Frame, ...]:
    """A big photo across the top, ``k`` across the bottom."""
    g = _GAP
    hg = g / 2.0
    top = Frame(0.0, 0.0, 1.0, 0.5 - hg)
    by, bh = 0.5 + hg, 0.5 - hg
    w = (1.0 - g * (k - 1)) / k
    bottoms = [Frame(i * (w + g), by, w, bh) for i in range(k)]
    return (top, *bottoms)


def _left_big_plus_grid(rows: int, cols: int) -> tuple[Frame, ...]:
    """A big photo on the left half, a ``rows x cols`` grid on the right half."""
    g = _GAP
    hg = g / 2.0
    left = Frame(0.0, 0.0, 0.5 - hg, 1.0)
    rx, rtot = 0.5 + hg, 0.5 - hg
    w = (rtot - g * (cols - 1)) / cols
    h = (1.0 - g * (rows - 1)) / rows
    cells = [
        Frame(rx + c * (w + g), r * (h + g), w, h)
        for r in range(rows)
        for c in range(cols)
    ]
    return (left, *cells)


# Variant library. The first entry per count matches ``template_for`` for
# backward compatibility; the rest add designed alternatives.
_TEMPLATES: dict[int, list[tuple[Frame, ...]]] = {
    1: [template_for(1)],
    2: [_row(2), _col(2)],
    3: [template_for(3), _row(3), _col(3), _top_big_plus_row(2)],
    4: [template_for(4), _row(4), _col(4), _left_big_plus_col(3), _top_big_plus_row(3)],
    5: [_left_big_plus_grid(2, 2), _rows_split((2, 3)), _rows_split((3, 2)), _top_big_plus_row(4)],
    6: [_rows_split((3, 3)), _rows_split((2, 2, 2)), _top_big_plus_row(5)],
}


def _frame_aspect(frame: Frame, spec: AlbumSpec) -> float:
    """Width/height of a relative frame in actual spread pixels."""
    w = frame.w * spec.spread_width_px
    h = frame.h * spec.spread_height_px
    return (w / h) if h else 1.0


def _crosses_gutter(frame: Frame) -> bool:
    """True if a frame straddles the spread's vertical centre (the binding)."""
    return frame.x < 0.5 - _EPS and (frame.x + frame.w) > 0.5 + _EPS


def _variant_mismatch(
    frames: tuple[Frame, ...], photo_aspects: list[float], spec: AlbumSpec
) -> float:
    """
    Orientation mismatch between a template's frames and the photos.

    Both aspect lists are sorted and paired; the score is the summed absolute
    difference of log-aspects (so 2x too wide costs the same as 2x too tall).
    Lower is a better orientation fit.
    """
    fr = sorted(_frame_aspect(f, spec) for f in frames)
    ph = sorted(photo_aspects)
    return sum(
        abs(_math.log(max(a, 1e-3)) - _math.log(max(b, 1e-3))) for a, b in zip(fr, ph)
    )


def choose_template(
    photo_aspects: list[float],
    spec: AlbumSpec,
    variant_index: int = 0,
    avoid_gutter: bool = False,
) -> tuple[Frame, ...]:
    """
    Pick a designed template for these photos.

    Chooses the variant whose frame orientations best match the photos; among
    variants within a small tolerance of the best, ``variant_index`` (e.g. the
    spread index) rotates the choice so consecutive spreads vary. When
    ``avoid_gutter`` is set (double-page album with a binding gutter), variants
    whose frames cross the centre seam are excluded.
    """
    count = len(photo_aspects)
    variants = _TEMPLATES.get(count)
    if not variants:
        return template_for(count)

    candidates = variants
    if avoid_gutter:
        safe = [v for v in variants if not any(_crosses_gutter(f) for f in v)]
        if safe:
            candidates = safe

    scored = sorted(
        ((_variant_mismatch(v, photo_aspects, spec), i, v) for i, v in enumerate(candidates)),
        key=lambda t: (t[0], t[1]),
    )
    best = scored[0][0]
    close = [s for s in scored if s[0] <= best + 0.15] or scored
    return close[variant_index % len(close)][2]


# --------------------------------------------------------------------------- #
# Photo -> frame assignment
# --------------------------------------------------------------------------- #
# How much a crowded-photo-in-a-small-cell pairing costs, relative to
# orientation mismatch. Orientation mismatch is a log-ratio, so a frame twice
# the photo's aspect costs ~0.69; at 0.35 the crowding term can overturn a
# near-tie on orientation but never a clear one. Faces are worth a nudge here,
# not a veto -- the wrong-shaped cell is visible on every spread, whereas a
# slightly small group photo is only a missed opportunity.
_W_CROWD = 0.35

# Above this many photos on one spread, an exhaustive search costs more than the
# result is worth (8! = 40320 pairings) and the heuristic takes over.
_MAX_EXHAUSTIVE_ASSIGN = 7


def _rank_fractions(values: list[float]) -> list[float]:
    """
    Map values to ``[0, 1]`` by rank: smallest -> 0.0, largest -> 1.0.

    Rank rather than magnitude, so the result depends only on the ordering
    within this spread. That keeps the assignment cost scale-free: a spread of
    two- and three-face photos is graded the same way as one of five- and
    fifty-face photos. Equal values share the mean of the ranks they span, so
    ties never break arbitrarily. A uniform list maps entirely to 0.5.
    """
    n = len(values)
    if n <= 1:
        return [0.5] * n

    order = sorted(range(n), key=lambda i: (values[i], i))
    ranks = [0.0] * n
    pos = 0
    while pos < n:
        end = pos
        while end + 1 < n and values[order[end + 1]] == values[order[pos]]:
            end += 1
        shared = (pos + end) / 2.0 / (n - 1)
        for k in range(pos, end + 1):
            ranks[order[k]] = shared
        pos = end + 1
    return ranks


def _min_cost_assignment(cost: list[list[float]]) -> Optional[dict[int, int]]:
    """
    Optimal ``{row: column}`` assignment by brute force, or ``None`` if too big.

    ``cost[row][col]`` is the cost of that pairing; every row gets a distinct
    column. Spreads hold a handful of photos, so enumerating permutations is
    both exact and instant — and unlike a greedy match it cannot paint itself
    into a corner by taking a locally cheap pair first. Ties resolve to the
    lexicographically first permutation, keeping output deterministic.

    Returns ``None`` when the matrix is larger than
    :data:`_MAX_EXHAUSTIVE_ASSIGN`, so the caller can fall back.
    """
    n = len(cost)
    if n == 0:
        return {}
    if n > _MAX_EXHAUSTIVE_ASSIGN:
        return None

    best_total: Optional[float] = None
    best: tuple[int, ...] = ()
    for perm in itertools.permutations(range(n)):
        total = 0.0
        for row, col in enumerate(perm):
            total += cost[row][col]
            if best_total is not None and total >= best_total:
                break  # cannot beat the incumbent; abandon this permutation
        else:
            if best_total is None or total < best_total:
                best_total, best = total, perm
    return {row: col for row, col in enumerate(best)}


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class AlbumLayoutEngine:
    """
    Lays photos out into album spreads using built-in templates.

    Args:
        max_per_spread: Hard cap on photos per spread (>= 1). The per-spread
            count chosen by :meth:`layout` is always clamped to this.

    Raises:
        AlbumLayoutError: if ``max_per_spread`` < 1.
    """

    def __init__(self, max_per_spread: int = 4) -> None:
        if max_per_spread < 1:
            raise AlbumLayoutError(
                f"max_per_spread must be >= 1, got {max_per_spread}"
            )
        self.max_per_spread = max_per_spread

    def layout(
        self,
        items: list[PhotoItem],
        spec: AlbumSpec,
        per_spread: Optional[int] = None,
        pacing: str = PACING_UNIFORM,
    ) -> list[Spread]:
        """
        Place ``items`` into a deterministic list of spreads.

        Items are chunked in order (uniformly, or to a narrative rhythm — see
        ``pacing``), a template is chosen for each group's size, each relative
        frame is converted to absolute spread pixels honoring
        margins/bleed/gutter, and a face-safe cover-fit crop is computed for
        each photo.

        Args:
            items: Photos to lay out, in album order.
            spec: The physical spread specification.
            per_spread: Photos per spread. When ``None``, a heuristic picks a
                pleasing count (up to 4). The value is always clamped to
                ``[1, max_per_spread]``.
            pacing: Narrative rhythm name from :mod:`core.album.pacing`.
                The default ``"uniform"`` gives every spread the same count.
                A rhythm instead varies the count around ``per_spread`` —
                dense spreads followed by a single full-bleed image — without
                changing the total spread count, so the page budget is
                unaffected either way.

        Returns:
            A list of :class:`Spread` objects covering every item, in order.
            An empty ``items`` list yields an empty result.

        Raises:
            AlbumLayoutError: if ``per_spread`` is supplied and < 1.
        """
        if per_spread is not None and per_spread < 1:
            raise AlbumLayoutError(f"per_spread must be >= 1, got {per_spread}")

        if not items:
            return []

        chunk_size = self._resolve_per_spread(len(items), per_spread)
        counts = pace_counts(
            len(items), chunk_size, self.max_per_spread, rhythm=pacing
        )

        spreads: list[Spread] = []
        for index, chunk in enumerate(chunk_by_counts(items, counts)):
            # Pick a designed template that matches the photos' orientations,
            # varying the choice across spreads so the album doesn't look like a
            # uniform grid. Avoid gutter-crossing frames on bound double-pages.
            frames = choose_template(
                [it.aspect_ratio for it in chunk],
                spec,
                variant_index=index,
                avoid_gutter=spec.double_page_spread and spec.gutter_in > 0,
            )
            # A lone photo fills the spread (full-bleed hero); collage cells fit
            # the whole photo so nobody is cropped.
            fit = FIT_COVER if len(chunk) == 1 else FIT_CONTAIN
            # Match photos to frames so portraits land in tall cells, landscapes
            # in wide ones, and crowded photos in cells big enough to see faces in.
            pairs = self._assign_to_frames(chunk, frames, spec)
            placements = tuple(
                self._place(item, frame, spec, fit) for item, frame in pairs
            )
            spreads.append(
                Spread(
                    index=index,
                    width_px=spec.spread_width_px,
                    height_px=spec.spread_height_px,
                    placements=placements,
                )
            )

        logger.info(
            "Laid out %d photo(s) into %d spread(s), pacing=%s, counts=%s.",
            len(items),
            len(spreads),
            pacing,
            counts,
        )
        return spreads

    def _resolve_per_spread(self, total: int, per_spread: Optional[int]) -> int:
        """
        Choose the chunk size, clamped to ``[1, max_per_spread]``.

        When ``per_spread`` is explicit it is used (clamped). Otherwise a
        heuristic targets a small, balanced count: 1 photo -> 1, 2-4 -> the
        count itself, and 5+ -> 4, all capped at ``max_per_spread``.
        """
        if per_spread is not None:
            return max(1, min(per_spread, self.max_per_spread))

        if total <= 1:
            heuristic = 1
        elif total <= 4:
            heuristic = total
        else:
            heuristic = 4
        return max(1, min(heuristic, self.max_per_spread))

    def _place(
        self, item: PhotoItem, frame: Frame, spec: AlbumSpec, fit: str = FIT_COVER
    ) -> Placement:
        """Convert a relative frame to pixels and compute the crop for ``fit``."""
        frame_px = self._frame_to_pixels(frame, spec)
        if fit == FIT_CONTAIN:
            # The whole photo is shown (letterboxed by the renderer); no crop.
            return Placement(
                path=item.path, frame_px=frame_px, crop=(0.0, 0.0, 1.0, 1.0), fit=fit
            )
        crop = self._cover_crop(item, frame_px, spec)
        return Placement(path=item.path, frame_px=frame_px, crop=crop, fit=fit)

    @staticmethod
    def _frame_pixel_aspect(frame: Frame, spec: AlbumSpec) -> float:
        """Approximate width/height of a relative frame in actual pixels."""
        w = frame.w * spec.spread_width_px
        h = frame.h * spec.spread_height_px
        return (w / h) if h else 1.0

    def _assign_to_frames(
        self, items: list[PhotoItem], frames: tuple[Frame, ...], spec: AlbumSpec
    ) -> list[tuple[PhotoItem, Frame]]:
        """
        Pair photos to frames, trading off aspect fit against face visibility.

        Two things matter and they sometimes disagree:

        - **Orientation fit** — a portrait photo in a tall frame wastes less
          space than in a wide one. Scored as the absolute difference of
          log-aspects, so "twice too wide" costs the same as "twice too tall".
        - **Face visibility** — a fifteen-person family group dropped into the
          smallest cell on the spread renders every face too small to recognise.
          A template's cells differ substantially in area, so *which* cell a
          crowded photo gets is a real quality decision, not a detail.

        Note the face term is about *size*, not cropping: multi-photo spreads
        are laid out with :data:`FIT_CONTAIN`, so the whole photo is shown and
        no face can be sliced. (Cropping happens on single-photo hero spreads,
        where there is only one frame and nothing to assign, and in the designed
        -template renderer, which scores crop damage via
        :func:`core.album.facecrop.face_crop_loss`.)

        Both terms are rank-normalised within the spread, so the cost is
        scale-free: what counts is which photo is *most* crowded and which frame
        is *largest* here, not their absolute values.

        Frames and photos are matched to minimise total cost — exhaustively for
        the small counts a spread actually holds, and by the previous
        sort-and-zip heuristic beyond that. Results are emitted in the original
        frame order, so spread positions are preserved, and ties break by index
        so the output is deterministic.
        """
        n = min(len(items), len(frames))
        if n <= 1:
            return list(zip(items, frames))

        cost = self._assignment_costs(items[:n], frames[:n], spec)
        assignment = _min_cost_assignment(cost) or self._assign_by_aspect_rank(
            items, frames, spec, n
        )
        return [(items[assignment[j]], frames[j]) for j in range(n)]

    def _assignment_costs(
        self, items: list[PhotoItem], frames: tuple[Frame, ...], spec: AlbumSpec
    ) -> list[list[float]]:
        """Cost matrix ``[frame_index][item_index]`` for frame/photo pairing."""
        n = len(items)
        crowd_rank = _rank_fractions([len(it.face_boxes) for it in items])
        area_rank = _rank_fractions([f.w * f.h for f in frames])
        frame_aspects = [self._frame_pixel_aspect(f, spec) for f in frames]

        matrix: list[list[float]] = []
        for j in range(n):
            row: list[float] = []
            for i in range(n):
                orientation = abs(
                    math.log(max(frame_aspects[j], 1e-3))
                    - math.log(max(items[i].aspect_ratio, 1e-3))
                )
                # Crowded photo (high rank) in a small frame (low area rank).
                crowding = crowd_rank[i] * (1.0 - area_rank[j])
                row.append(orientation + _W_CROWD * crowding)
            matrix.append(row)
        return matrix

    def _assign_by_aspect_rank(
        self,
        items: list[PhotoItem],
        frames: tuple[Frame, ...],
        spec: AlbumSpec,
        n: int,
    ) -> dict[int, int]:
        """
        Orientation-only fallback: sort both by aspect ratio and zip.

        Used when a spread holds more photos than an exhaustive search can
        afford. Ignores crowding, but never mismatches orientation.
        """
        items_by_aspect = sorted(range(n), key=lambda i: (items[i].aspect_ratio, i))
        frames_by_aspect = sorted(
            range(n), key=lambda j: (self._frame_pixel_aspect(frames[j], spec), j)
        )
        return {
            frame_j: item_i
            for item_i, frame_j in zip(items_by_aspect, frames_by_aspect)
        }

    def _frame_to_pixels(self, frame: Frame, spec: AlbumSpec) -> PixRect:
        """
        Map a relative frame onto absolute spread pixels.

        The usable area is the spread inset by ``margin`` on every side (plus
        ``bleed`` on the outer edges, which lives outside the trim and is left
        to the renderer -- margins are measured from the trim edge). For a
        double-page spread the gutter splits the usable area into a left and a
        right half; a frame is mapped into whichever half its center falls in,
        so no frame ever straddles the binding seam.
        """
        margin_px = round(spec.margin_in * spec.dpi)
        usable_x = margin_px
        usable_y = margin_px
        usable_w = spec.spread_width_px - 2 * margin_px
        usable_h = spec.spread_height_px - 2 * margin_px
        usable_w = max(usable_w, 1)
        usable_h = max(usable_h, 1)

        if spec.double_page_spread and spec.gutter_in > 0:
            gutter_px = round(spec.gutter_in * spec.dpi)
            center_x = spec.spread_width_px / 2.0
            frame_center_rel = frame.x + frame.w / 2.0

            if frame_center_rel < 0.5:
                # Left page: usable area from left margin up to the gutter.
                page_x = usable_x
                page_w = max(int(round(center_x - gutter_px / 2.0)) - usable_x, 1)
                local_x = frame.x / 0.5
                local_w = frame.w / 0.5
            else:
                # Right page: from the gutter's right edge to the right margin.
                page_x = int(round(center_x + gutter_px / 2.0))
                page_right = spec.spread_width_px - margin_px
                page_w = max(page_right - page_x, 1)
                local_x = (frame.x - 0.5) / 0.5
                local_w = frame.w / 0.5

            local_x = max(0.0, min(local_x, 1.0))
            local_w = max(0.0, min(local_w, 1.0 - local_x))
            x = page_x + int(round(local_x * page_w))
            w = max(int(round(local_w * page_w)), 1)
            y = usable_y + int(round(frame.y * usable_h))
            h = max(int(round(frame.h * usable_h)), 1)
            return (x, y, w, h)

        x = usable_x + int(round(frame.x * usable_w))
        y = usable_y + int(round(frame.y * usable_h))
        w = max(int(round(frame.w * usable_w)), 1)
        h = max(int(round(frame.h * usable_h)), 1)
        return (x, y, w, h)

    def _cover_crop(
        self, item: PhotoItem, frame_px: PixRect, spec: AlbumSpec
    ) -> RelRect:
        """
        Cover-fit crop of the source photo for ``frame_px``, made face-safe.

        "Cover" means the photo fills the frame entirely, cropping whichever
        dimension overflows. The visible source window is a relative rectangle:
        a wide-vs-frame photo keeps full height and a horizontal slice; a tall
        photo keeps full width and a vertical slice.

        The crop window is then shifted (within ``[0, 1]``) to keep every face
        box visible when possible, and to pull faces *away* from the spread
        gutter (toward the outer page edge). If the faces don't all fit, the
        window centers on their bounding box, keeping as many visible as the
        crop size allows.
        """
        _, _, fw, fh = frame_px
        frame_ar = fw / fh if fh else 1.0

        # Shared face-safe cover geometry (see :mod:`core.album.facecrop`), with
        # the gutter-aware horizontal bias applied for double-page spreads so
        # faces are pulled toward the outer page edge, away from the binding.
        pull_outward = self._pulls_left(frame_px, spec)
        return face_safe_cover_crop(
            item.aspect_ratio, frame_ar, item.face_boxes, pull_low_x=pull_outward
        )

    @staticmethod
    def _pad_face_boxes(face_boxes: tuple[RelRect, ...]) -> tuple[RelRect, ...]:
        """
        Expand face boxes into a head-and-shoulders safe region.

        Adds headroom above each face and generous room below (torso) plus a
        little horizontal slack, all clamped to ``[0, 1]``. Keeping this larger
        region inside the crop avoids the classic "head/legs cut off" look.
        """
        # Fractions of the face box's own size.
        up, down, side = 0.45, 1.4, 0.25
        padded: list[RelRect] = []
        for x, y, w, h in face_boxes:
            nx = max(0.0, x - w * side)
            ny = max(0.0, y - h * up)
            nx2 = min(1.0, x + w * (1.0 + side))
            ny2 = min(1.0, y + h * (1.0 + down))
            padded.append((nx, ny, nx2 - nx, ny2 - ny))
        return tuple(padded)

    @staticmethod
    def _pulls_left(frame_px: PixRect, spec: AlbumSpec) -> Optional[bool]:
        """
        Direction to pull faces away from the gutter, or ``None`` if neutral.

        On the left page faces are pulled toward the left (outer) edge -> shift
        the crop window low; on the right page toward the right edge -> shift
        high. For single pages (or no gutter benefit) returns ``None``.
        """
        if not spec.double_page_spread:
            return None
        fx, _, fw, _ = frame_px
        frame_center = fx + fw / 2.0
        spread_center = spec.spread_width_px / 2.0
        if frame_center < spread_center:
            return True  # left page -> pull toward low x (outer/left edge)
        if frame_center > spread_center:
            return False  # right page -> pull toward high x (outer/right edge)
        return None

    @staticmethod
    def _face_safe_offset(
        default_offset: float,
        crop_size: float,
        face_boxes: tuple[RelRect, ...],
        axis: int,
        pull_low: Optional[bool],
    ) -> float:
        """
        Pick the crop-window offset on one axis so faces stay visible.

        Args:
            default_offset: The centered offset to use when there are no faces
                or no shifting is needed.
            crop_size: The crop window's extent on this axis, in ``[0, 1]``.
            face_boxes: Source-relative face rectangles.
            axis: 0 for x, 1 for y.
            pull_low: When ``True`` bias toward the low end (offset as small as
                allowed); ``False`` toward the high end; ``None`` to center on
                the faces' bounding span.

        Returns:
            An offset in ``[0, max(0, 1 - crop_size)]`` that contains the
            faces' span when it fits, else centers on it.
        """
        max_offset = max(0.0, 1.0 - crop_size)
        if not face_boxes or crop_size >= 1.0 - _EPS:
            return min(max(default_offset, 0.0), max_offset)

        # Faces' combined span on this axis: [lo, hi].
        starts = [box[axis] for box in face_boxes]
        ends = [box[axis] + box[axis + 2] for box in face_boxes]
        lo = min(starts)
        hi = max(ends)
        span = hi - lo

        if span <= crop_size + _EPS:
            # All faces fit: the window may sit anywhere keeping [lo, hi] inside,
            # i.e. offset in [hi - crop_size, lo]. Bias within that range.
            low_bound = max(0.0, hi - crop_size)
            high_bound = min(max_offset, lo)
            if low_bound > high_bound:
                # Numerical edge: fall back to clamping the centered offset.
                low_bound, high_bound = (
                    min(low_bound, high_bound),
                    max(low_bound, high_bound),
                )
            if pull_low is True:
                offset = low_bound
            elif pull_low is False:
                offset = high_bound
            else:
                offset = (low_bound + high_bound) / 2.0
        else:
            # Faces can't all fit: center the window on their bounding span.
            center = (lo + hi) / 2.0
            offset = center - crop_size / 2.0

        return min(max(offset, 0.0), max_offset)
