"""Tests for procedural spread decoration."""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.album.decor import apply_decorations


def _spread(color=(210, 190, 150), size=(1200, 600)):
    return Image.new("RGB", size, color)


def test_returns_same_size_rgb():
    out = apply_decorations(_spread(), accent=(150, 40, 40))
    assert out.mode == "RGB"
    assert out.size == (1200, 600)


def test_decoration_changes_the_border_region():
    base = _spread()
    out = apply_decorations(base, accent=(150, 40, 40), frame=True, corners=True)
    b = np.asarray(base)
    o = np.asarray(out)
    # The border band should differ (frame + corner ink drawn there).
    top_before = b[:20, :, :]
    top_after = o[:20, :, :]
    assert not np.array_equal(top_before, top_after)


def test_all_elements_off_is_noop_without_title():
    base = _spread()
    out = apply_decorations(base, frame=False, corners=False, divider=False)
    # No frame/corners/divider/title -> unchanged pixels.
    assert np.array_equal(np.asarray(base), np.asarray(out.convert("RGB")))


def test_title_is_drawn():
    base = _spread()
    plain = apply_decorations(base, frame=False, corners=False)
    titled = apply_decorations(base, frame=False, corners=False, title="Haldi")
    assert not np.array_equal(np.asarray(plain), np.asarray(titled))


def test_no_theme_assets_is_graceful():
    # A theme with no data/themes/<theme>/decorations folder must not error.
    out = apply_decorations(_spread(), theme="definitely_missing_theme")
    assert out.size == (1200, 600)
