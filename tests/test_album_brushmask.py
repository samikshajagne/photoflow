"""Tests for procedural brush/torn-edge masks (Phase 5). Pure Pillow/NumPy."""

import numpy as np

from core.album.brushmask import brush_mask


def test_brush_mask_size_and_mode():
    m = brush_mask((300, 200), seed=1)
    assert m.size == (300, 200) and m.mode == "L"


def test_brush_mask_has_feathered_edge():
    a = np.asarray(brush_mask((400, 300), seed=2))
    assert a.max() == 255 and a.min() == 0
    assert ((a > 10) & (a < 245)).sum() > 200  # partial-alpha pixels = feather


def test_brush_mask_interior_is_opaque():
    a = np.asarray(brush_mask((300, 300), seed=3))
    assert a[150, 150] == 255  # centre fully inside the shape


def test_brush_mask_is_deterministic_per_seed():
    a = np.asarray(brush_mask((200, 200), seed=5))
    b = np.asarray(brush_mask((200, 200), seed=5))
    assert np.array_equal(a, b)


def test_brush_mask_varies_with_seed():
    a = np.asarray(brush_mask((200, 200), seed=1))
    b = np.asarray(brush_mask((200, 200), seed=2))
    assert not np.array_equal(a, b)
