"""
Declarative spread templates + a programmatic renderer (Phase 3, B1).

The Phase 1/2 layout engine (``core.album.layout``) only knew about plain
rectangular frames. A designed wedding album spread is richer: photos sit in
shaped slots (rounded rectangles, circles, ovals, rotated diamonds) with
borders and drop shadows, over a coloured background — like the sample album's
spreads.

This module introduces that as **data**:

* :class:`TemplateSlot` — one shaped photo slot (relative rect + shape + border
  + shadow + rotation + fit).
* :class:`Background` — solid or auto-sampled-from-the-photos backdrop.
* :class:`SpreadTemplate` — a named, themed arrangement of slots.

Templates round-trip to/from JSON (the "author templates as data" model), and a
small built-in ``classic`` theme ships in code (and is written out as JSON under
``data/templates/``). :func:`render_spread` composites real photos into a
template with Pillow — no external art assets required (backgrounds are sampled
from the photos; frames/borders/shadows are drawn programmatically).

Deliberately self-contained: depends only on Pillow, NumPy, and
:class:`~core.album.layout.AlbumSpec`, so it renders (and is tested) without the
heavy detection backends.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter

from core.album.layout import AlbumSpec

PathLike = Union[str, Path]

# --- Slot shapes ------------------------------------------------------------ #
SHAPE_RECT = "rect"
SHAPE_ROUNDED = "rounded"
SHAPE_CIRCLE = "circle"   # true circle inscribed in the slot (uses min edge)
SHAPE_OVAL = "oval"       # ellipse filling the slot rect
SHAPE_DIAMOND = "diamond"  # 4-point polygon (rotated square feel)
SHAPES = frozenset({SHAPE_RECT, SHAPE_ROUNDED, SHAPE_CIRCLE, SHAPE_OVAL, SHAPE_DIAMOND})

# --- Fit modes -------------------------------------------------------------- #
FIT_COVER = "cover"      # fill the slot, cropping overflow (default)
FIT_CONTAIN = "contain"  # fit the whole photo inside the slot
FITS = frozenset({FIT_COVER, FIT_CONTAIN})

# --- Background kinds -------------------------------------------------------- #
BG_SOLID = "solid"
BG_SAMPLED = "sampled"   # dominant colour of the photos, lightened to a tint
BG_KINDS = frozenset({BG_SOLID, BG_SAMPLED})

DEFAULT_THEME = "classic"


class TemplateError(Exception):
    """Raised when a template is malformed."""


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class TemplateSlot:
    """
    One shaped photo slot on a spread.

    ``rect`` is ``(x, y, w, h)`` in ``[0, 1]`` of the spread's *usable* area
    (inside the safe margin). ``border`` and shadow sizes are given as a
    fraction of the spread's short edge so they are resolution-independent.
    """

    rect: tuple[float, float, float, float]
    shape: str = SHAPE_RECT
    corner_radius: float = 0.08   # rounded-rect radius, fraction of slot short edge
    border: float = 0.0           # border thickness, fraction of spread short edge
    border_color: str = "#FFFFFF"
    rotation_deg: float = 0.0
    shadow: bool = False
    fit: str = FIT_COVER

    def __post_init__(self) -> None:
        if len(self.rect) != 4:
            raise TemplateError(f"slot rect must be (x, y, w, h), got {self.rect!r}")
        x, y, w, h = self.rect
        if w <= 0 or h <= 0:
            raise TemplateError(f"slot must have positive size, got {self.rect!r}")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise TemplateError(f"slot origin must be in [0, 1], got {self.rect!r}")
        if self.shape not in SHAPES:
            raise TemplateError(f"unknown shape {self.shape!r}; expected one of {sorted(SHAPES)}")
        if self.fit not in FITS:
            raise TemplateError(f"unknown fit {self.fit!r}; expected one of {sorted(FITS)}")

    def to_dict(self) -> dict[str, Any]:
        return {"rect": list(self.rect), **{k: v for k, v in dataclasses.asdict(self).items() if k != "rect"}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemplateSlot":
        fields = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in data.items() if k in fields}
        if "rect" in kw:
            kw["rect"] = tuple(kw["rect"])
        return cls(**kw)


@dataclasses.dataclass(frozen=True)
class Background:
    """Spread backdrop: a solid colour, or one sampled from the photos."""

    type: str = BG_SAMPLED
    color: Optional[str] = None    # hex; used for solid, or as a tint override
    lighten: float = 0.66          # for sampled: blend toward white (0=raw, 1=white)

    def __post_init__(self) -> None:
        if self.type not in BG_KINDS:
            raise TemplateError(f"unknown background type {self.type!r}")
        if self.type == BG_SOLID and not self.color:
            raise TemplateError("solid background requires a color")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Background":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass(frozen=True)
class SpreadTemplate:
    """A named, themed arrangement of shaped slots over a background."""

    name: str
    theme: str
    slots: tuple[TemplateSlot, ...]
    background: Background = dataclasses.field(default_factory=Background)

    def __post_init__(self) -> None:
        if not self.slots:
            raise TemplateError(f"template {self.name!r} has no slots")

    @property
    def photo_count(self) -> int:
        return len(self.slots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "theme": self.theme,
            "slots": [s.to_dict() for s in self.slots],
            "background": self.background.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpreadTemplate":
        try:
            return cls(
                name=data["name"],
                theme=data["theme"],
                slots=tuple(TemplateSlot.from_dict(s) for s in data["slots"]),
                background=Background.from_dict(data.get("background", {})),
            )
        except (KeyError, TypeError) as exc:
            raise TemplateError(f"invalid template document: {exc}") from exc

    def to_json(self, path: PathLike) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return out

    @classmethod
    def from_json(cls, path: PathLike) -> "SpreadTemplate":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
Loader = Callable[[str], Image.Image]


def render_spread(
    template: SpreadTemplate,
    image_paths: Sequence[PathLike],
    spec: AlbumSpec,
    *,
    loader: Optional[Loader] = None,
) -> Image.Image:
    """
    Composite ``image_paths`` into ``template`` and return an RGB spread image.

    One image is placed per slot, in order; extra images/slots are ignored so a
    partially-filled template still renders. Backgrounds, shapes, borders and
    shadows are all drawn programmatically (no art assets needed).
    """
    open_image = loader or _default_loader
    width, height = spec.spread_width_px, spec.spread_height_px
    short_edge = min(width, height)

    images = [open_image(str(p)) for p in image_paths[: len(template.slots)]]

    canvas = Image.new("RGBA", (width, height), _background_rgba(template.background, images))

    margin = round(spec.margin_in * spec.dpi)
    ux, uy = margin, margin
    uw, uh = max(1, width - 2 * margin), max(1, height - 2 * margin)

    for slot, image in zip(template.slots, images):
        _place_slot(canvas, slot, image, (ux, uy, uw, uh), short_edge)

    return canvas.convert("RGB")


def _place_slot(
    canvas: Image.Image,
    slot: TemplateSlot,
    image: Image.Image,
    usable: tuple[int, int, int, int],
    short_edge: int,
) -> None:
    ux, uy, uw, uh = usable
    x = ux + round(slot.rect[0] * uw)
    y = uy + round(slot.rect[1] * uh)
    w = max(1, round(slot.rect[2] * uw))
    h = max(1, round(slot.rect[3] * uh))

    border_px = max(0, round(slot.border * short_edge))
    radius_px = max(0, round(slot.corner_radius * min(w, h)))

    fitted = _fit(image.convert("RGB"), w, h, slot.fit)
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    tile.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    tile.putalpha(_shape_mask((w, h), slot.shape, radius_px))
    if border_px:
        _draw_border(tile, slot.shape, border_px, slot.border_color, radius_px)

    if slot.rotation_deg:
        tile = tile.rotate(slot.rotation_deg, expand=True, resample=Image.BICUBIC)

    cx, cy = x + w // 2, y + h // 2
    px, py = cx - tile.width // 2, cy - tile.height // 2

    if slot.shadow:
        _paste_shadow(canvas, tile, (px, py), short_edge)
    canvas.alpha_composite(tile, (px, py))


def _fit(image: Image.Image, w: int, h: int, fit: str) -> Image.Image:
    """Cover-crop (fill + crop) or contain (fit whole) the image to ``w x h``."""
    iw, ih = image.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGB", (w, h), (230, 230, 230))
    if fit == FIT_CONTAIN:
        scale = min(w / iw, h / ih)
    else:
        scale = max(w / iw, h / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = image.resize((nw, nh), Image.LANCZOS)
    if fit == FIT_CONTAIN:
        out = Image.new("RGB", (w, h), (255, 255, 255))
        out.paste(resized, ((w - nw) // 2, (h - nh) // 2))
        return out
    left, top = (nw - w) // 2, (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _shape_mask(size: tuple[int, int], shape: str, radius_px: int) -> Image.Image:
    w, h = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if shape == SHAPE_ROUNDED:
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius_px, fill=255)
    elif shape == SHAPE_CIRCLE:
        diam = min(w, h)
        ox, oy = (w - diam) // 2, (h - diam) // 2
        draw.ellipse([ox, oy, ox + diam - 1, oy + diam - 1], fill=255)
    elif shape == SHAPE_OVAL:
        draw.ellipse([0, 0, w - 1, h - 1], fill=255)
    elif shape == SHAPE_DIAMOND:
        draw.polygon([(w // 2, 0), (w - 1, h // 2), (w // 2, h - 1), (0, h // 2)], fill=255)
    else:  # SHAPE_RECT
        draw.rectangle([0, 0, w - 1, h - 1], fill=255)
    return mask


def _draw_border(
    tile: Image.Image, shape: str, border_px: int, color: str, radius_px: int
) -> None:
    w, h = tile.size
    draw = ImageDraw.Draw(tile)
    off = border_px // 2
    box = [off, off, w - 1 - off, h - 1 - off]
    if shape == SHAPE_ROUNDED:
        draw.rounded_rectangle(box, radius=radius_px, outline=color, width=border_px)
    elif shape == SHAPE_CIRCLE:
        diam = min(w, h) - border_px
        ox, oy = (w - diam) // 2, (h - diam) // 2
        draw.ellipse([ox, oy, ox + diam, oy + diam], outline=color, width=border_px)
    elif shape == SHAPE_OVAL:
        draw.ellipse(box, outline=color, width=border_px)
    elif shape == SHAPE_DIAMOND:
        pts = [(w // 2, off), (w - 1 - off, h // 2), (w // 2, h - 1 - off), (off, h // 2)]
        draw.line(pts + [pts[0]], fill=color, width=border_px, joint="curve")
    else:  # SHAPE_RECT
        draw.rectangle(box, outline=color, width=border_px)


def _paste_shadow(
    canvas: Image.Image, tile: Image.Image, pos: tuple[int, int], short_edge: int
) -> None:
    """Draw a soft, subtle drop shadow from the tile's alpha, offset down-right."""
    blur = max(4, round(short_edge * 0.008))
    offset = max(2, round(short_edge * 0.0035))
    alpha = tile.split()[3].filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.30)))
    canvas.alpha_composite(shadow, (pos[0] + offset, pos[1] + offset))


def _background_rgba(background: Background, images: Sequence[Image.Image]) -> tuple[int, int, int, int]:
    if background.type == BG_SOLID and background.color:
        r, g, b = ImageColor.getrgb(background.color)[:3]
        return (r, g, b, 255)
    base = _sampled_color(images)
    if background.color:  # tint override for sampled
        base = ImageColor.getrgb(background.color)[:3]
    t = max(0.0, min(1.0, background.lighten))
    r, g, b = (round(c * (1 - t) + 255 * t) for c in base)
    return (r, g, b, 255)


def _sampled_color(images: Sequence[Image.Image]) -> tuple[int, int, int]:
    """Average colour across the fill images (small-thumbnail mean)."""
    acc, n = np.zeros(3, dtype=np.float64), 0
    for img in images:
        small = img.convert("RGB").resize((32, 32), Image.BILINEAR)
        acc += np.asarray(small, dtype=np.float64).reshape(-1, 3).mean(axis=0)
        n += 1
    if n == 0:
        return (210, 210, 210)
    r, g, b = (int(v) for v in (acc / n).round())
    return (r, g, b)


def _default_loader(path: str) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
        return img
    except Exception:  # noqa: BLE001 - a missing/unreadable photo becomes a placeholder
        return Image.new("RGB", (1000, 1000), (225, 225, 225))


# --------------------------------------------------------------------------- #
# Library + selection
# --------------------------------------------------------------------------- #
# Tuned styling for the built-in theme: a clean white medium border, soft
# shadows (see ``_paste_shadow``), a light sampled tint, and shapes used freely.
_BORDER = 0.008           # medium white frame (fraction of the spread short edge)
_WHITE = "#FFFFFF"
_BG = Background(type=BG_SAMPLED, lighten=0.66)


def _slot(rect, shape=SHAPE_RECT, **kw):
    """A framed, softly-shadowed slot in the default style."""
    kw.setdefault("border", _BORDER)
    kw.setdefault("border_color", _WHITE)
    kw.setdefault("shadow", True)
    return TemplateSlot(rect=rect, shape=shape, **kw)


def default_templates() -> list[SpreadTemplate]:
    """
    The built-in ``classic`` theme: arrangements for 1–6 photos that use
    circles, ovals, rounded rectangles and diamonds freely (per the chosen
    "shapes prominent" style), all with a white medium border and soft shadow
    over a light sampled-colour background.
    """
    return [
        # 1 — a single photo as a large rounded panel on a tinted page.
        SpreadTemplate(
            name="classic-1",
            theme=DEFAULT_THEME,
            slots=(_slot((0.05, 0.06, 0.90, 0.88), SHAPE_ROUNDED, corner_radius=0.06),),
            background=_BG,
        ),
        # 2 — a rounded panel beside a tall oval.
        SpreadTemplate(
            name="classic-2",
            theme=DEFAULT_THEME,
            slots=(
                _slot((0.03, 0.10, 0.45, 0.80), SHAPE_ROUNDED, corner_radius=0.05),
                _slot((0.53, 0.12, 0.44, 0.76), SHAPE_OVAL),
            ),
            background=_BG,
        ),
        # 3 — a hero rectangle with a circle and a diamond alongside.
        SpreadTemplate(
            name="classic-3",
            theme=DEFAULT_THEME,
            slots=(
                _slot((0.0, 0.0, 0.56, 1.0)),
                _slot((0.60, 0.04, 0.38, 0.44), SHAPE_CIRCLE),
                _slot((0.60, 0.52, 0.38, 0.44), SHAPE_DIAMOND),
            ),
            background=_BG,
        ),
        # 4 — hero rectangle + a stack of three shaped accents.
        SpreadTemplate(
            name="classic-4",
            theme=DEFAULT_THEME,
            slots=(
                _slot((0.0, 0.0, 0.5, 1.0)),
                _slot((0.54, 0.03, 0.44, 0.30), SHAPE_CIRCLE),
                _slot((0.54, 0.35, 0.44, 0.30), SHAPE_ROUNDED, corner_radius=0.10),
                _slot((0.54, 0.68, 0.44, 0.30), SHAPE_DIAMOND),
            ),
            background=_BG,
        ),
        # 5 — hero rectangle + a 2x2 grid of mixed shapes.
        SpreadTemplate(
            name="classic-5",
            theme=DEFAULT_THEME,
            slots=(
                _slot((0.0, 0.0, 0.48, 1.0)),
                _slot((0.52, 0.02, 0.22, 0.46), SHAPE_CIRCLE),
                _slot((0.76, 0.02, 0.22, 0.46), SHAPE_ROUNDED, corner_radius=0.12),
                _slot((0.52, 0.52, 0.22, 0.46), SHAPE_DIAMOND),
                _slot((0.76, 0.52, 0.22, 0.46), SHAPE_OVAL),
            ),
            background=_BG,
        ),
        # 6 — a 3x2 gallery alternating rounded panels with circles/ovals/diamond.
        SpreadTemplate(
            name="classic-6",
            theme=DEFAULT_THEME,
            slots=(
                _slot((0.00, 0.00, 0.30, 0.47), SHAPE_ROUNDED, corner_radius=0.10),
                _slot((0.35, 0.00, 0.30, 0.47), SHAPE_CIRCLE),
                _slot((0.70, 0.00, 0.30, 0.47), SHAPE_ROUNDED, corner_radius=0.10),
                _slot((0.00, 0.53, 0.30, 0.47), SHAPE_OVAL),
                _slot((0.35, 0.53, 0.30, 0.47), SHAPE_ROUNDED, corner_radius=0.10),
                _slot((0.70, 0.53, 0.30, 0.47), SHAPE_DIAMOND),
            ),
            background=_BG,
        ),
    ]


def load_templates(root: PathLike) -> list[SpreadTemplate]:
    """Load every ``*.json`` template under ``root`` (recursively)."""
    base = Path(root)
    if not base.is_dir():
        return []
    out: list[SpreadTemplate] = []
    for path in sorted(base.rglob("*.json")):
        out.append(SpreadTemplate.from_json(path))
    return out


def select_template(
    templates: Sequence[SpreadTemplate], count: int, theme: Optional[str] = None
) -> SpreadTemplate:
    """
    Pick a template for ``count`` photos, preferring ``theme``.

    Chooses an exact photo-count match within the theme; if none exists, falls
    back to an auto rectangular grid so any count still renders.
    """
    if count < 1:
        raise TemplateError(f"count must be >= 1, got {count}")
    pool = [t for t in templates if theme is None or t.theme == theme]
    exact = [t for t in pool if t.photo_count == count]
    if exact:
        return exact[0]
    return auto_grid_template(count, theme or DEFAULT_THEME)


def auto_grid_template(count: int, theme: str = DEFAULT_THEME) -> SpreadTemplate:
    """A near-square rectangular grid holding exactly ``count`` slots."""
    import math

    cols = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / cols))
    g = 0.015
    cell_w, cell_h = (1.0 - g * (cols - 1)) / cols, (1.0 - g * (rows - 1)) / rows
    slots: list[TemplateSlot] = []
    for i in range(count):
        r, c = divmod(i, cols)
        slots.append(
            TemplateSlot(
                rect=(c * (cell_w + g), r * (cell_h + g), cell_w, cell_h),
                border=0.005,
            )
        )
    return SpreadTemplate(name=f"grid-{count}", theme=theme, slots=tuple(slots),
                          background=Background(type=BG_SAMPLED))
