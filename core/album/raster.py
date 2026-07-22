"""
Direct raster + layered-PSD album exporter for PhotoFlow.

PhotoFlow's layout engine produces, per spread, a set of :class:`Placement`
records: an absolute pixel ``frame_px`` rectangle on the spread plus a relative
``crop`` over the *source* photo (cover-fit, face-safe). The Photoshop ``.jsx``
exporter (:mod:`core.album.photoshop_jsx`) turns that geometry into a layered
``.psd`` -- but only for photographers who own Photoshop.

This module renders the very same geometry directly, with no external editor:

- **PNG / JPG** -- one flattened image per spread.
- **PDF**       -- every spread as a page in a single document.
- **PSD**       -- one *layered* ``.psd`` per spread (a white background layer
  plus one pixel layer per placed photo, positioned in its frame), so the album
  stays editable for those who do have an editor, without scripting Photoshop.

Geometry mirrors the ``.jsx`` builder exactly: the visible region of each photo
is its relative ``crop`` mapped to source pixels, resized to the frame and drawn
at the frame's top-left. Tonal auto-edits (white balance / exposure / contrast)
from each photo's :class:`~core.auto_edit.EditRecipe` are applied when present;
the recipe's *geometric* parts (straighten / crop) are deliberately skipped
because the layout's crop is defined against the original, ungeometried source.

Public API:
- :func:`render_spread` -- one spread to a flattened ``RGB`` Pillow image.
- :func:`export_png`, :func:`export_jpg`, :func:`export_pdf`, :func:`export_psd`.
- :func:`export_renders` -- a dispatcher over a set of requested formats.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

import numpy as np
from PIL import Image, ImageOps

import dataclasses as _dataclasses

from core.album.template import (
    DEFAULT_THEME,
    default_templates,
    render_spread as _render_template,
    select_template,
)
from core.album.theming import dominant_color as _dominant_color, to_hex as _to_hex
from core.album import textlayer as _textlayer
from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Supported output formats (lower-case, no dot).
FORMAT_PNG = "png"
FORMAT_JPG = "jpg"
FORMAT_PDF = "pdf"
FORMAT_PSD = "psd"
SUPPORTED_FORMATS = (FORMAT_PNG, FORMAT_JPG, FORMAT_PDF, FORMAT_PSD)

DEFAULT_DPI = 300
DEFAULT_JPG_QUALITY = 92
_WHITE = (255, 255, 255)

# Spread rendering is dominated by native JPEG decode + resize, which release
# the GIL, so a thread pool gives a real speed-up. Capped so a huge album does
# not spawn an unreasonable number of workers.
ProgressCb = Optional[Callable[[int, int, str], None]]


# Source photos are decoded no larger than this on their long edge during
# render. Full 24MP decodes (×many photos ×many parallel spreads) can exhaust a
# laptop's RAM; a designed spread never needs more than a few thousand pixels
# per photo, so this bounds memory with no visible quality loss on album pages.
_MAX_SOURCE_EDGE_PX: int = 3000


def _default_workers() -> int:
    # Deliberately conservative. Rendering a spread holds a large canvas plus
    # several decoded photos in memory; running many in parallel (old default:
    # up to 8) can exhaust RAM or overheat a laptop and crash it. Two keeps the
    # UI responsive while bounding peak memory and CPU heat.
    return max(1, min(2, (os.cpu_count() or 2)))


class AlbumRenderError(Exception):
    """Raised when a spread cannot be rendered or written."""


class ExportCancelled(AlbumRenderError):
    """Raised to abort an export when the caller's cancel event is set."""


def _check_cancel(cancel_event: Any) -> None:
    """Raise :class:`ExportCancelled` if ``cancel_event`` is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled("Export cancelled.")


# --------------------------------------------------------------------------- #
# Tonal edit application (geometry-preserving subset of an EditRecipe)
# --------------------------------------------------------------------------- #
def _apply_tone(rgb: np.ndarray, recipe: Optional[dict]) -> np.ndarray:
    """
    Apply only the geometry-preserving parts of an edit recipe to an RGB array.

    White balance, exposure and contrast are applied (matching
    :meth:`core.auto_edit.AutoEditor.apply`); ``straighten`` and ``crop`` are
    intentionally ignored so the array's pixel grid still matches the layout's
    crop, which was computed against the original source. ``recipe`` is the dict
    form (``EditRecipe.as_dict()``); ``None`` or a malformed recipe is a no-op.
    """
    if not recipe:
        return rgb
    try:
        gains = recipe.get("white_balance_gains") or (1.0, 1.0, 1.0)
        r_gain, g_gain, b_gain = (float(g) for g in gains)
        exposure = float(recipe.get("exposure", 1.0))
        contrast = float(recipe.get("contrast", 1.0))
    except (TypeError, ValueError):
        return rgb

    out = rgb.astype(np.float32)
    out[:, :, 0] *= r_gain
    out[:, :, 1] *= g_gain
    out[:, :, 2] *= b_gain
    out *= exposure
    out = (out - 127.5) * contrast + 127.5
    return np.clip(out, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Per-placement rasterization
# --------------------------------------------------------------------------- #
def _spread_size(spread: Any) -> tuple[int, int]:
    """Return ``(width_px, height_px)`` for a SpreadRecord or Spread."""
    return int(spread.width_px), int(spread.height_px)


def _placements(spread: Any) -> list[dict]:
    """
    Normalize a spread's placements to a list of dicts with ``path``,
    ``frame_px``, ``crop``, and ``z_index`` keys, accepting both SpreadRecord
    (dict placements) and layout ``Spread`` (Placement dataclasses).
    """
    out: list[dict] = []
    for p in spread.placements:
        if isinstance(p, dict):
            out.append(
                {
                    "path": p["path"],
                    "frame_px": tuple(p["frame_px"]),
                    "crop": tuple(p["crop"]),
                    "fit": p.get("fit", "cover"),
                    "face_boxes": p.get("face_boxes", ()),
                    "z_index": int(p.get("z_index", 0)),
                }
            )
        else:  # layout.Placement dataclass
            out.append(
                {
                    "path": p.path,
                    "frame_px": tuple(p.frame_px),
                    "crop": tuple(p.crop),
                    "fit": getattr(p, "fit", "cover"),
                    "face_boxes": getattr(p, "face_boxes", ()),
                    "z_index": int(getattr(p, "z_index", 0)),
                }
            )
    return out


def _placement_face_boxes(placement: dict) -> tuple[tuple[float, float, float, float], ...]:
    """Relative face boxes stored on a placement, as a tuple of 4-float tuples."""
    raw = placement.get("face_boxes") or ()
    boxes: list[tuple[float, float, float, float]] = []
    for box in raw:
        try:
            x, y, w, h = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            boxes.append((x, y, w, h))
    return tuple(boxes)


def _load_rgb(source: Path, recipe: Optional[dict], apply_edits: bool) -> Image.Image:
    """
    Open ``source`` as an RGB Pillow image, downscaled to bound memory, with
    tonal edits optionally applied.

    ``draft`` gives the JPEG decoder a fast size hint; ``thumbnail`` then
    enforces the cap for any format. This keeps peak memory low so exports don't
    exhaust RAM on large shoots.
    """
    img = Image.open(source)
    try:
        img.draft("RGB", (_MAX_SOURCE_EDGE_PX, _MAX_SOURCE_EDGE_PX))  # fast JPEG downscale hint
    except Exception:  # noqa: BLE001 - draft is best-effort (non-JPEG etc.)
        pass
    img = ImageOps.exif_transpose(img)  # honor camera orientation (fix sideways photos)
    img = img.convert("RGB")
    if max(img.size) > _MAX_SOURCE_EDGE_PX:
        img.thumbnail((_MAX_SOURCE_EDGE_PX, _MAX_SOURCE_EDGE_PX), Image.LANCZOS)
    if apply_edits and recipe:
        arr = _apply_tone(np.asarray(img), recipe)
        img = Image.fromarray(arr, mode="RGB")
    return img


def _crop_to_frame(img: Image.Image, crop: tuple, frame_wh: tuple[int, int]) -> Image.Image:
    """
    Map a relative ``crop`` over ``img`` to source pixels and resize it to the
    frame size -- the exact visible region the ``.jsx`` builder would show.
    """
    src_w, src_h = img.size
    cx, cy, cw, ch = (float(v) for v in crop)
    left = int(round(_clamp01(cx) * src_w))
    top = int(round(_clamp01(cy) * src_h))
    right = int(round(_clamp01(cx + cw) * src_w))
    bottom = int(round(_clamp01(cy + ch) * src_h))
    right = max(right, left + 1)
    bottom = max(bottom, top + 1)
    region = img.crop((left, top, right, bottom))

    fw, fh = max(int(frame_wh[0]), 1), max(int(frame_wh[1]), 1)
    return region.resize((fw, fh), Image.LANCZOS)


def _render_tile(
    img: Image.Image, crop: tuple, frame_wh: tuple[int, int], fit: str
) -> tuple[Image.Image, int, int]:
    """
    Produce the pixels to paste for one placement and the offset within the frame.

    - ``cover``: crop to the frame's aspect and fill it exactly (offset 0,0).
    - ``contain``: scale the whole photo to fit inside the frame, centered, so
      nothing is cropped; the returned offset positions it and the surrounding
      area stays the spread background.
    """
    fw, fh = max(int(frame_wh[0]), 1), max(int(frame_wh[1]), 1)
    if fit == "contain":
        src_w, src_h = img.size
        scale = min(fw / src_w, fh / src_h)
        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))
        tile = img.resize((new_w, new_h), Image.LANCZOS)
        return tile, (fw - new_w) // 2, (fh - new_h) // 2
    return _crop_to_frame(img, crop, (fw, fh)), 0, 0


def _resolve_source(project: Any, path: str) -> tuple[Path, Optional[dict]]:
    """
    Resolve a placement path to ``(source_file, recipe_dict)``.

    Prefers the photo's ``linked_path`` (e.g. a retouched export from the
    round-trip) when present, so a finished album renders the retouched file
    rather than the original; otherwise falls back to the placement path. Uses
    the photo's stored ``edit_recipe`` when available.
    """
    recipe: Optional[dict] = None
    source = path
    record = None
    getter = getattr(project, "get", None)
    if callable(getter):
        record = getter(path)
    if record is not None:
        recipe = getattr(record, "edit_recipe", None)
        linked = getattr(record, "linked_path", None)
        if linked and Path(linked).exists():
            source = linked
    return Path(source), recipe


# --------------------------------------------------------------------------- #
# Public rendering API
# --------------------------------------------------------------------------- #
def render_spread(
    project: Any,
    spread: Any,
    apply_edits: bool = True,
    background: tuple[int, int, int] = _WHITE,
    skipped: Optional[list] = None,
) -> Image.Image:
    """
    Render one spread to a flattened ``RGB`` Pillow image.

    Missing or unreadable source photos are skipped (their frame is left as the
    background), mirroring the ``.jsx`` builder's behaviour, so a single bad
    asset never aborts the album. When a ``skipped`` list is supplied, the path
    of every skipped photo is appended to it so the caller can warn the user.
    """
    width, height = _spread_size(spread)
    canvas = Image.new("RGB", (width, height), background)
    # Sort placements by z_index: background layers drawn first, overlays on top.
    sorted_placements = sorted(_placements(spread), key=lambda p: p.get("z_index", 0))
    for pl in sorted_placements:
        fx, fy, fw, fh = (int(round(v)) for v in pl["frame_px"])
        source, recipe = _resolve_source(project, pl["path"])
        if not source.exists():
            logger.warning("Render: source missing, skipping frame: %s", source)
            _record_skip(skipped, pl["path"])
            continue
        try:
            img = _load_rgb(source, recipe, apply_edits)
            tile, ox, oy = _render_tile(img, pl["crop"], (fw, fh), pl["fit"])
        except Exception as exc:  # noqa: BLE001 - one bad asset must not abort
            logger.warning("Render: failed on '%s': %s", source, exc)
            _record_skip(skipped, pl["path"])
            continue
        canvas.paste(tile, (fx + ox, fy + oy))
    return canvas


def _album_spec_for(project: Any, width: int, height: int):
    """
    Rebuild the :class:`~core.album.layout.AlbumSpec` for a spread.

    Prefers the project's stored album spec when its pixel size matches the
    spread; otherwise synthesizes a single-page spec whose spread size equals
    ``(width, height)`` so the template renderer produces exactly that canvas.
    """
    import dataclasses as _dc

    from core.album.layout import AlbumSpec

    meta = getattr(project, "meta", None)
    raw = dict(getattr(meta, "album_spec", {}) or {})
    fields = {f.name for f in _dc.fields(AlbumSpec)}
    kw = {k: v for k, v in raw.items() if k in fields}
    try:
        spec = AlbumSpec(**kw)
        if (spec.spread_width_px, spec.spread_height_px) == (width, height):
            return spec
    except Exception:  # noqa: BLE001 - fall through to a synthesized spec
        pass
    dpi = _dpi(project)
    return AlbumSpec(
        page_width_in=max(width, 1) / dpi,
        page_height_in=max(height, 1) / dpi,
        dpi=dpi,
        double_page_spread=False,
    )


def _album_theme(project: Any) -> str:
    """The template theme to render with (stored on the project, or the default)."""
    meta = getattr(project, "meta", None)
    raw = dict(getattr(meta, "album_spec", {}) or {})
    return raw.get("theme") or DEFAULT_THEME


def _cover_meta(project: Any) -> tuple[str, str]:
    """The couple title + date for the cover (stored on the project meta)."""
    meta = getattr(project, "meta", None)
    raw = dict(getattr(meta, "album_spec", {}) or {})
    return str(raw.get("cover_title") or ""), str(raw.get("cover_date") or "")


def _album_flags(project: Any) -> tuple[bool, bool]:
    """
    Read WS 3.4.2 album-layout feature flags from the project's album_spec.

    Returns ``(smart_slot_ordering, use_cutouts)``.

    Both flags live in the ``album_spec`` meta dict — the same store as
    ``theme``, ``cover_title``, and ``cover_date`` — so they round-trip through
    the manifest without touching the strict YAML config validator.

    - ``smart_slot_ordering`` (default ``True``): enable WS 3.2 subject-aware
      photo→slot assignment.  Set to ``False`` to revert to the legacy
      aspect-ratio-only sort.
    - ``use_cutouts`` (default ``False``): enable WS 3.3.1 feathered face
      cutouts on slots authored with ``use_cutout=True``. Opt-in because the
      effect is prominent; the photographer turns it on when they want it.
    """
    try:
        meta = getattr(project, "meta", None)
        raw = dict(getattr(meta, "album_spec", {}) or {})
        smart = bool(raw.get("smart_slot_ordering", True))
        cutouts = bool(raw.get("use_cutouts", False))
        return smart, cutouts
    except Exception:  # noqa: BLE001 - a bad meta must never break rendering
        return True, False


def _flexible_flag(project: Any) -> bool:
    """
    WS 4.1 opt-in flag: when ``flexible_layout`` is set in the album_spec meta,
    each spread adapts its slot *types* to its photos (via
    :func:`core.album.flexible_render.flexible_template_for`) instead of using a
    fixed count-based template. Default ``False`` (fixed templates).
    """
    try:
        meta = getattr(project, "meta", None)
        raw = dict(getattr(meta, "album_spec", {}) or {})
        return bool(raw.get("flexible_layout", False))
    except Exception:  # noqa: BLE001 - a bad meta must never break rendering
        return False


def _designed_cover_flag(project: Any) -> bool:
    """
    WS 4.4 opt-in flag: when ``designed_cover`` is set, the Cover spread is
    composed by :func:`core.album.cover_designer.generate_cover` (hero cutout +
    names + date + tagline on a themed background) instead of a plain captioned
    photo. Default ``False``.
    """
    try:
        meta = getattr(project, "meta", None)
        raw = dict(getattr(meta, "album_spec", {}) or {})
        return bool(raw.get("designed_cover", False))
    except Exception:  # noqa: BLE001 - a bad meta must never break rendering
        return False


def _theme_backgrounds_flag(project: Any) -> bool:
    """
    WS 4.3.3 opt-in flag: when ``theme_backgrounds`` is set, a section whose
    photos classify as a known event (Haldi/Mehndi/Baraat/Reception) is given
    that event's canonical themed background tint + accent instead of the raw
    sampled colour. Default ``False``.
    """
    try:
        meta = getattr(project, "meta", None)
        raw = dict(getattr(meta, "album_spec", {}) or {})
        return bool(raw.get("theme_backgrounds", False))
    except Exception:  # noqa: BLE001 - a bad meta must never break rendering
        return False


def _section_theme_color(project: Any, spread: Any) -> Optional[tuple[int, int, int]]:
    """
    The mood colour for a spread's *section*, computed once and reused so every
    spread in the same section shares one background (a coherent per-event
    look). Returns ``None`` when the section has no readable photos.
    """
    section = getattr(spread, "section", None) or ""
    cache = getattr(project, "_section_theme_cache", None)
    if cache is None:
        cache = {}
        try:
            setattr(project, "_section_theme_cache", cache)
        except Exception:  # noqa: BLE001 - some projects may reject new attrs
            pass
    if section in cache:
        return cache[section]

    paths: list[str] = []
    for s in getattr(project, "spreads", []) or []:
        if (getattr(s, "section", None) or "") == section:
            paths.extend(pl["path"] for pl in _placements(s))

    def _open(path: str) -> Optional[Image.Image]:
        source, _recipe = _resolve_source(project, path)
        if not source.exists():
            return None
        try:
            img = Image.open(source)
            # Cap size to a small thumbnail -- dominant_color only needs 48×48.
            # Avoids loading full 24 MP photos just to compute a background tint.
            img.thumbnail((_MAX_SOURCE_EDGE_PX, _MAX_SOURCE_EDGE_PX), Image.BILINEAR)
            return ImageOps.exif_transpose(img).convert("RGB")
        except Exception:  # noqa: BLE001 - unreadable photo doesn't vote
            return None

    color = _dominant_color(paths, loader=_open) if paths else None
    cache[section] = color
    return color


def _photo_aspect(path: str) -> float:
    """Header-only width/height honoring EXIF orientation (1.0 if unreadable)."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            orientation = img.getexif().get(0x0112, 1)
        if orientation in (5, 6, 7, 8):  # 90°/270° rotations swap the axes
            w, h = h, w
        if w > 0 and h > 0:
            return float(w) / float(h)
    except Exception:  # noqa: BLE001 - unreadable -> treat as square
        pass
    return 1.0


def _order_by_slot_aspect(paths: list[str], template: Any, width: int, height: int) -> list[str]:
    """
    Reorder ``paths`` so each photo lands in the slot whose aspect best matches
    its orientation (portrait photo -> tall slot, landscape -> wide slot).

    Pairs the tallest photo with the tallest slot, next with next, and so on
    (sorting both by aspect and zipping), which minimizes how much any photo
    must be cropped to fill its slot. Extra photos beyond the slot count keep
    their order.

    This is the fallback when subject-aware matching is disabled or unavailable.
    """
    slots = template.slots
    n = min(len(paths), len(slots))
    if n <= 1:
        return paths
    slot_aspect = [
        (s.rect[2] * width) / max(1e-6, s.rect[3] * height) for s in slots[:n]
    ]
    photo_aspect = [_photo_aspect(p) for p in paths[:n]]
    slot_order = sorted(range(n), key=lambda i: slot_aspect[i])   # tall -> wide
    photo_order = sorted(range(n), key=lambda i: photo_aspect[i])
    result: list[Optional[str]] = [None] * n
    for k in range(n):
        result[slot_order[k]] = paths[photo_order[k]]
    return [p for p in result if p is not None] + list(paths[n:])


def _order_by_content(
    paths: list[str],
    template: Any,
    width: int,
    height: int,
    faces_by_path: dict,
) -> list[str]:
    """
    Subject-aware photo ordering: assign each photo to the slot that best fits
    its *composition* (portrait / group / detail / landscape) using WS 3.2's
    :func:`~core.album.slot_matcher.match_photos_to_slots`.

    Each photo is characterised by
    :func:`~core.content_analyzer.analyze` (face count, composition type,
    aspect) and each template slot by a :class:`~core.album.slot_matcher.SlotProfile`
    derived from its aspect and size. The Hungarian/greedy solver picks the
    global-optimum photo→slot assignment so a portrait photo (large face,
    tall frame) lands in the tall portrait slot and a detail close-up lands
    in the small accent slot.

    Falls back to :func:`_order_by_slot_aspect` on any import/solver failure.
    """
    n = min(len(paths), len(template.slots))
    if n <= 1:
        return paths
    try:
        from core.content_analyzer import analyze as _analyze
        from core.album.slot_matcher import SlotProfile, match_photos_to_slots

        # Build a SlotProfile for each template slot from its pixel aspect ratio.
        slot_profiles = []
        for s in template.slots[:n]:
            slot_w = s.rect[2] * width
            slot_h = s.rect[3] * height
            slot_ar = slot_w / max(1e-6, slot_h)
            # Infer the slot's preferred composition from its aspect ratio:
            # tall (< 0.8) -> portrait/full_body; wide (> 1.4) -> landscape/group;
            # square-ish -> detail/environmental.
            if slot_ar < 0.8:
                ideal = ("portrait", "full_body")
                face_range = (1, 2)
            elif slot_ar > 1.4:
                ideal = ("group", "landscape", "large_group")
                face_range = (0, 8)
            else:
                ideal = ("detail", "environmental", "group")
                face_range = (0, 4)
            slot_profiles.append(
                SlotProfile(
                    name=f"slot_{len(slot_profiles)}",
                    aspect_ratio=slot_ar,
                    ideal_composition=ideal,
                    ideal_face_count=face_range,
                )
            )

        # Build PhotoContent for each photo using its face boxes + aspect.
        contents = [
            _analyze(
                _photo_aspect(p),
                faces_by_path.get(p, ()),
            )
            for p in paths[:n]
        ]

        assignment = match_photos_to_slots(contents, slot_profiles)
        # assignment = {slot_index: photo_index}. Build the reordered path list.
        result: list[Optional[str]] = [None] * n
        for slot_i, photo_i in assignment.items():
            if 0 <= slot_i < n and 0 <= photo_i < n:
                result[slot_i] = paths[photo_i]
        # Fill any unassigned slots in order from the remaining photos.
        used = set(assignment.values())
        remaining = [paths[i] for i in range(n) if i not in used]
        for idx in range(n):
            if result[idx] is None and remaining:
                result[idx] = remaining.pop(0)
        ordered = [p for p in result if p is not None]
        if len(ordered) == n:
            return ordered + list(paths[n:])
    except Exception:  # noqa: BLE001 - matcher unavailable -> silent fallback
        pass
    return _order_by_slot_aspect(paths, template, width, height)


# Target long-edge (px) for on-screen preview spreads: big enough to judge the
# layout, small enough to render ~instantly and use little memory.
PREVIEW_LONG_EDGE_PX: int = 1100


def preview_spec(spec: Any, long_edge_px: int = PREVIEW_LONG_EDGE_PX) -> Any:
    """
    A low-resolution copy of ``spec`` (same page size/shape) for fast preview
    rendering. The album's photo grouping is resolution-independent, so a spread
    previewed with this spec looks like what will be exported at full DPI.
    """
    import dataclasses as _dc

    long_in = max(spec.spread_width_in, spec.spread_height_in) or 1.0
    dpi = max(24, min(int(spec.dpi), round(long_edge_px / long_in)))
    return _dc.replace(spec, dpi=dpi)


def _is_section_opener(project: Any, spread: Any) -> bool:
    """True if ``spread`` is the first (lowest-index) spread of its section."""
    section = getattr(spread, "section", None) or ""
    idx = getattr(spread, "index", None)
    if idx is None:
        return False
    indices = [
        getattr(s, "index", 0)
        for s in getattr(project, "spreads", []) or []
        if (getattr(s, "section", None) or "") == section
    ]
    return bool(indices) and idx == min(indices)


def render_spread_template(
    project: Any,
    spread: Any,
    apply_edits: bool = True,
    background: tuple[int, int, int] = _WHITE,
    skipped: Optional[list] = None,
    spec: Any = None,
) -> Image.Image:
    """
    Render one spread through the designed-template engine
    (:mod:`core.album.template`): shaped photo slots, borders, soft shadows and
    a sampled background, instead of the plain rectangular grid.

    The spread's placement list only supplies *which* photos (and their order)
    go on this spread; the template decides their shapes and arrangement. Tonal
    edits and the retouched-``linked_path`` round-trip are honoured via the same
    resolver as :func:`render_spread`. Missing/unreadable photos are recorded in
    ``skipped`` and rendered as the background colour (never abort the album).

    Pass ``spec`` (an :class:`~core.album.layout.AlbumSpec`) to render at that
    canvas size regardless of the spread's stored pixel size — used to render a
    fast, lower-resolution preview of the same content that will be exported.

    Two album-spec flags (WS 3.4.2) control advanced layout behaviour:

    - ``smart_slot_ordering`` (bool, default ``True``): when enabled, uses
      :mod:`core.album.slot_matcher` to assign each photo to the slot that
      best matches its composition (portrait -> tall slot, group -> wide slot,
      detail -> small square). Falls back to aspect-only sorting on failure.
    - ``use_cutouts`` (bool, default ``False``): when enabled, slots authored
      with ``use_cutout=True`` get a feathered head-and-shoulders alpha cutout
      (WS 3.3.1) instead of a hard shape mask, giving the editorial silhouette
      look. Requires a usable face box; slots fall back silently.
    """
    if spec is not None:
        width, height = spec.spread_width_px, spec.spread_height_px
    else:
        width, height = _spread_size(spread)
    placements = _placements(spread)
    paths = [pl["path"] for pl in placements]
    if not paths:
        return Image.new("RGB", (width, height), background)

    # Relative face boxes per photo (stored on the placement at layout time). Used
    # below to keep faces inside each cover-fit slot instead of centre-cropping.
    faces_by_path = {pl["path"]: _placement_face_boxes(pl) for pl in placements}

    if spec is None:
        spec = _album_spec_for(project, width, height)
    # Rotate template variants by spread index so consecutive spreads differ.
    variant = int(getattr(spread, "index", 0) or 0)
    theme = _album_theme(project)
    # Editorial theme: full-bleed hero + tight grid (no dead space). Its templates
    # live in a separate pool merged in here.
    if theme == "editorial":
        try:
            from core.album.editorial_templates import editorial_templates

            template = select_template(editorial_templates(), len(paths), "editorial", variant=variant)
        except Exception:  # noqa: BLE001 - fall back to the classic pool
            template = select_template(default_templates(), len(paths), theme, variant=variant)
    else:
        template = select_template(default_templates(), len(paths), theme, variant=variant)

    # WS 4.1: when flexible layouts are enabled, replace the fixed template with
    # one whose slot *types* were chosen to fit this spread's photos. Falls back
    # to the fixed template above when the flexible engine returns None.
    if _flexible_flag(project):
        from core.album.flexible_render import flexible_template_for

        flexible = flexible_template_for(
            paths, faces_by_path, _album_theme(project), aspect_fn=_photo_aspect
        )
        if flexible is not None:
            template = flexible

    # Read album-level behaviour flags (WS 3.4.2).
    smart_ordering, use_cutouts = _album_flags(project)

    # Order photos into slots: subject-aware when enabled (WS 3.2), else aspect-only.
    if smart_ordering:
        paths = _order_by_content(paths, template, width, height, faces_by_path)
    else:
        paths = _order_by_slot_aspect(paths, template, width, height)
    # Face boxes must follow the reordered paths so each slot gets its photo's faces.
    face_boxes_by_index = [faces_by_path.get(p, ()) for p in paths]

    # Give every spread in this section the same background, tinted from the
    # section's photos, for a coherent per-event colour mood.
    section_color = _section_theme_color(project, spread)
    theme_accent: Optional[tuple[int, int, int]] = None
    if section_color is not None:
        bg_color = section_color
        # WS 4.3.3: for a confidently-classified event (Haldi/Mehndi/Baraat/
        # Reception) use its canonical themed tint + accent instead of the raw
        # sampled colour. Falls back to the sampled tint otherwise.
        if _theme_backgrounds_flag(project):
            try:
                from core.album.event_theme import themed_background

                themed = themed_background(section_color)
                if themed is not None:
                    bg_color, theme_accent = themed
            except Exception:  # noqa: BLE001 - theming must never break rendering
                pass
        bg = _dataclasses.replace(template.background, color=_to_hex(bg_color))
        template = _dataclasses.replace(template, background=bg)

    # Auto black-and-white: on richer spreads, render one accent slot (the last)
    # in greyscale for contrast, like a designed album. Slots load in order, so
    # a per-call counter maps to the slot index.
    bw_index = len(paths) - 1 if len(paths) >= 3 else None
    state = {"i": -1}

    def loader(path: str) -> Image.Image:
        state["i"] += 1
        source, recipe = _resolve_source(project, path)
        if not source.exists():
            logger.warning("Render(template): source missing, skipping: %s", source)
            _record_skip(skipped, path)
            return Image.new("RGB", (1200, 1200), background)
        try:
            img = _load_rgb(source, recipe, apply_edits)
        except Exception as exc:  # noqa: BLE001 - one bad asset must not abort
            logger.warning("Render(template): failed on '%s': %s", source, exc)
            _record_skip(skipped, path)
            return Image.new("RGB", (1200, 1200), background)
        if state["i"] == bw_index:
            img = ImageOps.grayscale(img).convert("RGB")
        return img

    # WS 4.4: designed cover — compose the Cover spread (hero cutout + names +
    # date + tagline on a themed background) instead of a plain captioned photo.
    if _designed_cover_flag(project) and (getattr(spread, "section", "") or "").lower() == "cover":
        cover = _designed_cover(
            project, paths, faces_by_path, section_color, width, height, apply_edits
        )
        if cover is not None:
            return cover

    img = _render_template(
        template, paths, spec, loader=loader, face_boxes_by_index=face_boxes_by_index,
        use_cutout=use_cutouts,
    )
    if img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)

    # Caption the section's opening spread: the Cover gets the couple's names +
    # date; every other section gets its title + a curated quote.
    if _is_section_opener(project, spread):
        section = getattr(spread, "section", None) or ""
        # Prefer the themed accent (WS 4.3.3) when one was chosen for this section.
        if theme_accent is not None:
            accent = theme_accent
        elif section_color is not None:
            accent = tuple(max(0, int(c * 0.5)) for c in section_color)
        else:
            accent = (150, 40, 40)
        if section.lower() == "cover":
            title, date = _cover_meta(project)
            if title:  # only if the photographer supplied names
                img = _textlayer.draw_cover(
                    img, title, date, subtitle="A Successful Love Story", accent=accent
                )
        elif section:
            img = _textlayer.draw_caption(
                img,
                _textlayer.title_for_section(section),
                _textlayer.pick_quote(section),
                accent=accent,
            )

    # Editorial theme: overlay the hairline frame + corner flourishes so the
    # spread reads as designed rather than a bare collage.
    if theme == "editorial":
        try:
            from core.album.decor import apply_decorations

            if theme_accent is not None:
                deco_accent = theme_accent
            elif section_color is not None:
                deco_accent = tuple(max(0, int(c * 0.5)) for c in section_color)
            else:
                deco_accent = (150, 40, 40)
            img = apply_decorations(img, theme=theme, accent=deco_accent, frame=True, corners=True)
        except Exception:  # noqa: BLE001 - decoration must never break the render
            pass
    return img


def _designed_cover(
    project: Any,
    paths: list[str],
    faces_by_path: dict,
    section_color: Optional[tuple[int, int, int]],
    width: int,
    height: int,
    apply_edits: bool,
) -> Optional[Image.Image]:
    """
    Compose the Cover spread via :func:`core.album.cover_designer.generate_cover`,
    or ``None`` on any failure so the caller falls back to the normal render.
    """
    if not paths:
        return None
    try:
        from core.album.cover_designer import generate_cover

        title, date = _cover_meta(project)
        hero = paths[0]
        source, recipe = _resolve_source(project, hero)
        if not source.exists():
            return None

        def _loader(_p: str) -> Image.Image:
            return _load_rgb(source, recipe, apply_edits).convert("RGBA")

        theme = section_color or (150, 40, 40)
        return generate_cover(
            hero,
            title or "",
            date,
            theme_color=theme,
            size=(width, height),
            face_boxes=faces_by_path.get(hero, ()),
            loader=_loader,
        )
    except Exception as exc:  # noqa: BLE001 - never break the album on the cover
        logger.warning("Designed cover failed (%s); using standard cover.", exc)
        return None


def _record_skip(skipped: Optional[list], path: str) -> None:
    """Append ``path`` to the caller's ``skipped`` list (de-duplicated)."""
    if skipped is not None and path not in skipped:
        skipped.append(path)


def _spread_tiles(
    project: Any, spread: Any, apply_edits: bool, skipped: Optional[list] = None
) -> list[tuple[int, int, Image.Image, str]]:
    """Per-photo ``(left, top, RGBA tile, layer_name)`` for layered output.

    For ``contain`` placements the tile is the fitted (smaller) photo and the
    left/top already include the centering offset within the cell, so it sits
    correctly over the white background layer.
    """
    tiles = []
    for idx, pl in enumerate(_placements(spread)):
        fx, fy, fw, fh = (int(round(v)) for v in pl["frame_px"])
        source, recipe = _resolve_source(project, pl["path"])
        if not source.exists():
            logger.warning("PSD: source missing, skipping layer: %s", source)
            _record_skip(skipped, pl["path"])
            continue
        try:
            img = _load_rgb(source, recipe, apply_edits)
            tile_rgb, ox, oy = _render_tile(img, pl["crop"], (fw, fh), pl["fit"])
            tile = tile_rgb.convert("RGBA")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PSD: failed on '%s': %s", source, exc)
            _record_skip(skipped, pl["path"])
            continue
        name = f"{idx + 1:02d}_{Path(pl['path']).stem}"[:63]
        tiles.append((fx + ox, fy + oy, tile, name))
    return tiles


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _dpi(project: Any) -> int:
    spec = getattr(getattr(project, "meta", None), "album_spec", None) or {}
    try:
        return int(spec.get("dpi", DEFAULT_DPI))
    except (TypeError, ValueError):
        return DEFAULT_DPI


def _spreads(project: Any) -> list:
    spreads = list(getattr(project, "spreads", []) or [])
    if not spreads:
        raise AlbumRenderError("Album has no spreads to render.")
    return spreads


def _spread_name(spread: Any) -> str:
    return f"spread_{int(spread.index) + 1:02d}"


def _ensure_dir(out_dir: PathLike) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


# --------------------------------------------------------------------------- #
# Format exporters
# --------------------------------------------------------------------------- #
def _render_spread_files(
    project: Any,
    out_dir: PathLike,
    ext: str,
    *,
    apply_edits: bool,
    quality: int,
    skipped: Optional[list],
    cancel_event: Any,
    progress_cb: ProgressCb,
    max_workers: int,
    label: str,
) -> list[Path]:
    """
    Render every spread to an image file (``ext`` = ``"png"`` or ``"jpg"``),
    returning the paths in album order.

    Spreads are rendered concurrently (native decode/resize release the GIL),
    and progress is reported per spread so the UI shows real movement. A shared
    ``skipped`` list is updated under a lock. Cancellation is checked before each
    spread starts and surfaced as :class:`ExportCancelled`.
    """
    out = _ensure_dir(out_dir)
    dpi = _dpi(project)
    spreads = _spreads(project)
    total = len(spreads)
    logger.info("%s: rendering %d spread(s)…", label, total)
    workers = max(1, int(max_workers or 1))
    lock = threading.Lock()
    results: dict[int, Path] = {}

    def work(item: tuple[int, Any]) -> tuple[int, Path]:
        pos, spread = item
        if cancel_event is not None and cancel_event.is_set():
            raise ExportCancelled("Export cancelled.")
        local_skip: list = []
        img = render_spread_template(project, spread, apply_edits, skipped=local_skip)
        target = out / f"{_spread_name(spread)}.{ext}"
        try:
            if ext == FORMAT_PNG:
                img.save(target, "PNG", dpi=(dpi, dpi))
            else:
                img.save(target, "JPEG", quality=int(quality), dpi=(dpi, dpi))
        finally:
            img.close()
        if local_skip and skipped is not None:
            with lock:
                for p in local_skip:
                    if p not in skipped:
                        skipped.append(p)
        return pos, target

    items = list(enumerate(spreads))
    done = 0

    def _advance(target: Path, pos: int) -> None:
        nonlocal done
        results[pos] = target
        done += 1
        if progress_cb is not None:
            progress_cb(done, total, f"{label} {done}/{total}")
        if done == 1 or done % 5 == 0 or done == total:
            logger.info("%s %d/%d…", label, done, total)

    if workers <= 1:
        for item in items:
            pos, target = work(item)
            _advance(target, pos)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, item) for item in items]
            try:
                for fut in as_completed(futures):
                    pos, target = fut.result()
                    _advance(target, pos)
            except ExportCancelled:
                pool.shutdown(cancel_futures=True)
                raise

    return [results[i] for i in range(len(results))]


def export_png(
    out_dir: PathLike,
    project: Any,
    apply_edits: bool = True,
    skipped: Optional[list] = None,
    cancel_event: Any = None,
    progress_cb: ProgressCb = None,
    max_workers: Optional[int] = None,
) -> list[Path]:
    """Write one PNG per spread (rendered in parallel); paths in album order."""
    paths = _render_spread_files(
        project, out_dir, FORMAT_PNG,
        apply_edits=apply_edits, quality=DEFAULT_JPG_QUALITY, skipped=skipped,
        cancel_event=cancel_event, progress_cb=progress_cb,
        max_workers=max_workers or _default_workers(), label="Rendering PNG spread",
    )
    logger.info("Exported %d PNG spread(s) to '%s'.", len(paths), out_dir)
    return paths


def export_jpg(
    out_dir: PathLike,
    project: Any,
    apply_edits: bool = True,
    quality: int = DEFAULT_JPG_QUALITY,
    skipped: Optional[list] = None,
    cancel_event: Any = None,
    progress_cb: ProgressCb = None,
    max_workers: Optional[int] = None,
) -> list[Path]:
    """Write one JPEG per spread (rendered in parallel); paths in album order."""
    paths = _render_spread_files(
        project, out_dir, FORMAT_JPG,
        apply_edits=apply_edits, quality=quality, skipped=skipped,
        cancel_event=cancel_event, progress_cb=progress_cb,
        max_workers=max_workers or _default_workers(), label="Rendering JPG spread",
    )
    logger.info("Exported %d JPG spread(s) to '%s'.", len(paths), out_dir)
    return paths


def export_pdf(
    out_dir: PathLike,
    project: Any,
    apply_edits: bool = True,
    filename: str = "album.pdf",
    skipped: Optional[list] = None,
    cancel_event: Any = None,
    progress_cb: ProgressCb = None,
    max_workers: Optional[int] = None,
) -> Path:
    """
    Render every spread as a page of a single multi-page PDF.

    Spreads are rendered in parallel to temporary **JPEG** pages (far faster to
    encode than PNG for large 300 dpi spreads) and assembled lazily, so peak
    memory stays bounded while the export finishes in a fraction of the time.
    """
    import tempfile

    out = _ensure_dir(out_dir)
    dpi = _dpi(project)
    target = out / filename

    with tempfile.TemporaryDirectory(prefix="photoflow_pdf_") as tmp:
        page_files = _render_spread_files(
            project, tmp, FORMAT_JPG,
            apply_edits=apply_edits, quality=90, skipped=skipped,
            cancel_event=cancel_event, progress_cb=progress_cb,
            max_workers=max_workers or _default_workers(), label="Rendering PDF page",
        )
        handles = [Image.open(p) for p in page_files]
        try:
            handles[0].save(
                target,
                "PDF",
                resolution=float(dpi),
                save_all=True,
                append_images=handles[1:],
            )
        finally:
            for h in handles:
                h.close()

    logger.info("Exported %d-page album PDF to '%s'.", len(page_files), target)
    return target


def export_psd(
    out_dir: PathLike,
    project: Any,
    apply_edits: bool = True,
    skipped: Optional[list] = None,
    cancel_event: Any = None,
    progress_cb: ProgressCb = None,
    max_workers: Optional[int] = None,  # accepted for a uniform signature; PSD is sequential
) -> list[Path]:
    """
    Write one *layered* PSD per spread: a white background layer plus one pixel
    layer per placed photo, positioned in its frame. Requires ``psd-tools``.

    Rendered sequentially (psd-tools document building is not thread-safe) but
    reports per-spread progress.
    """
    try:
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise AlbumRenderError(
            "Layered PSD export needs the 'psd-tools' package (pip install psd-tools)."
        ) from exc

    out = _ensure_dir(out_dir)
    spreads = _spreads(project)
    total = len(spreads)
    written: list[Path] = []
    for done, spread in enumerate(spreads, start=1):
        _check_cancel(cancel_event)
        width, height = _spread_size(spread)
        psd = PSDImage.frompil(Image.new("RGBA", (width, height), (*_WHITE, 255)))
        bg = PixelLayer.frompil(
            Image.new("RGBA", (width, height), (*_WHITE, 255)), psd, "background", 0, 0
        )
        psd.append(bg)
        for left, top, tile, name in _spread_tiles(
            project, spread, apply_edits, skipped=skipped
        ):
            layer = PixelLayer.frompil(tile, psd, name, top=top, left=left)
            psd.append(layer)
        target = out / f"{_spread_name(spread)}.psd"
        psd.save(target)
        written.append(target)
        if progress_cb is not None:
            progress_cb(done, total, f"Writing PSD spread {done}/{total}")
    logger.info("Exported %d layered PSD spread(s) to '%s'.", len(written), out)
    return written


def export_renders(
    out_dir: PathLike,
    project: Any,
    formats: Iterable[str],
    apply_edits: bool = True,
    jpg_quality: int = DEFAULT_JPG_QUALITY,
    skipped: Optional[list] = None,
    progress_cb: Optional[Any] = None,
    cancel_event: Any = None,
) -> dict[str, Any]:
    """
    Export each requested format and return ``{format: path(s)}``.

    ``formats`` is any iterable of :data:`SUPPORTED_FORMATS` members (case
    -insensitive). Unknown formats raise :class:`AlbumRenderError`. When a
    ``skipped`` list is supplied, the paths of any photos that could not be
    rendered (missing/unreadable sources) are appended to it.

    ``progress_cb``, if given, is called as ``progress_cb(done, total, message)``
    where ``done``/``total`` count individual **spreads** across all requested
    formats, so the bar advances smoothly during a long render rather than
    sitting at 0% until a whole format finishes. ``cancel_event`` (anything with
    ``.is_set()``) is polled between spreads; setting it raises
    :class:`ExportCancelled`.
    """
    requested = [f.lower().lstrip(".") for f in formats]
    unknown = [f for f in requested if f not in SUPPORTED_FORMATS]
    if unknown:
        raise AlbumRenderError(
            f"Unsupported export format(s): {unknown}; "
            f"expected any of {list(SUPPORTED_FORMATS)}"
        )

    # Total work units = spreads * formats, so progress reflects real per-spread
    # movement and the offset accumulates as each format completes.
    n_spreads = len(_spreads(project))
    grand_total = max(1, n_spreads * len(requested))

    def make_cb(offset: int) -> ProgressCb:
        if progress_cb is None:
            return None

        def cb(done: int, _total: int, message: str) -> None:
            progress_cb(offset + done, grand_total, message)

        return cb

    results: dict[str, Any] = {}
    for fmt_index, fmt in enumerate(requested):
        _check_cancel(cancel_event)
        offset = fmt_index * n_spreads
        cb = make_cb(offset)
        if fmt == FORMAT_PNG:
            results[fmt] = export_png(
                out_dir, project, apply_edits, skipped=skipped,
                cancel_event=cancel_event, progress_cb=cb,
            )
        elif fmt == FORMAT_JPG:
            results[fmt] = export_jpg(
                out_dir, project, apply_edits, jpg_quality,
                skipped=skipped, cancel_event=cancel_event, progress_cb=cb,
            )
        elif fmt == FORMAT_PDF:
            results[fmt] = export_pdf(
                out_dir, project, apply_edits,
                skipped=skipped, cancel_event=cancel_event, progress_cb=cb,
            )
        elif fmt == FORMAT_PSD:
            results[fmt] = export_psd(
                out_dir, project, apply_edits,
                skipped=skipped, cancel_event=cancel_event, progress_cb=cb,
            )
    if progress_cb is not None:
        progress_cb(grand_total, grand_total, "Done")
    return results
