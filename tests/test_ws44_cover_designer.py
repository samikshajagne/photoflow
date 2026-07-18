"""WS 4.4 tests: album cover designer."""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.album.cover_designer import DEFAULT_SIZE, TAGLINES, generate_cover


def _hero(color=(60, 120, 200), size=(1200, 1600)):
    """A distinctly-coloured hero image, returned by a stub loader."""
    return Image.new("RGBA", size, (*color, 255))


def _loader(img):
    return lambda _path: img


def _nonuniform(img_region) -> bool:
    arr = np.asarray(img_region.convert("RGB"))
    return int(arr.std()) > 3  # something was drawn, not a flat fill


def test_generates_cover_at_requested_size():
    cover = generate_cover(
        "hero.jpg", "Aisha & Rohan", "12 December 2025",
        theme_color=(200, 60, 60), size=(1200, 800), loader=_loader(_hero()),
    )
    assert cover.mode == "RGB"
    assert cover.size == (1200, 800)


def test_hero_and_text_are_composited():
    size = (1000, 800)
    cover = generate_cover(
        "hero.jpg", "Aisha & Rohan", "2025",
        theme_color=(180, 50, 50), size=size, loader=_loader(_hero((0, 150, 0))),
    )
    w, h = size
    # Upper region should contain the hero (green) over the tinted background.
    upper = cover.crop((0, 0, w, int(h * 0.5)))
    assert _nonuniform(upper)
    # Lower region should contain the text plate (names/date/tagline).
    lower = cover.crop((0, int(h * 0.5), w, h))
    assert _nonuniform(lower)


def test_cutout_path_runs_with_face_boxes():
    # A central face box should drive the feathered cutout branch without error.
    cover = generate_cover(
        "hero.jpg", "A & R", "2025", size=(900, 700),
        face_boxes=((0.35, 0.2, 0.3, 0.3),), loader=_loader(_hero((200, 0, 0))),
    )
    assert cover.size == (900, 700)


def test_default_tagline_used_when_empty():
    # Should not raise and should pick a default tagline from the style set.
    cover = generate_cover(
        "hero.jpg", "A & R", size=(800, 640),
        tagline_style="elegant_western", loader=_loader(_hero()),
    )
    assert cover.size == (800, 640)
    assert TAGLINES["elegant_western"][0] == "Happily Ever After"


def test_default_size_constant():
    assert DEFAULT_SIZE == (5400, 3600)
