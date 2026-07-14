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
from PIL import Image

from core.album.template import (
    DEFAULT_THEME,
    default_templates,
    render_spread as _render_template,
    select_template,
)
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


def _default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2)))


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
    ``frame_px`` and ``crop`` keys, accepting both SpreadRecord (dict
    placements) and layout ``Spread`` (Placement dataclasses).
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
                }
            )
        else:  # layout.Placement dataclass
            out.append(
                {
                    "path": p.path,
                    "frame_px": tuple(p.frame_px),
                    "crop": tuple(p.crop),
                    "fit": getattr(p, "fit", "cover"),
                }
            )
    return out


def _load_rgb(source: Path, recipe: Optional[dict], apply_edits: bool) -> Image.Image:
    """Open ``source`` as an RGB Pillow image, optionally applying tonal edits."""
    img = Image.open(source)
    img = img.convert("RGB")
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
    for pl in _placements(spread):
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


def render_spread_template(
    project: Any,
    spread: Any,
    apply_edits: bool = True,
    background: tuple[int, int, int] = _WHITE,
    skipped: Optional[list] = None,
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
    """
    width, height = _spread_size(spread)
    paths = [pl["path"] for pl in _placements(spread)]
    if not paths:
        return Image.new("RGB", (width, height), background)

    spec = _album_spec_for(project, width, height)
    template = select_template(default_templates(), len(paths), _album_theme(project))

    def loader(path: str) -> Image.Image:
        source, recipe = _resolve_source(project, path)
        if not source.exists():
            logger.warning("Render(template): source missing, skipping: %s", source)
            _record_skip(skipped, path)
            return Image.new("RGB", (1200, 1200), background)
        try:
            return _load_rgb(source, recipe, apply_edits)
        except Exception as exc:  # noqa: BLE001 - one bad asset must not abort
            logger.warning("Render(template): failed on '%s': %s", source, exc)
            _record_skip(skipped, path)
            return Image.new("RGB", (1200, 1200), background)

    img = _render_template(template, paths, spec, loader=loader)
    if img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)
    return img


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
