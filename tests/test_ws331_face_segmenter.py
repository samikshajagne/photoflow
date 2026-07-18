"""WS 3.3.1 tests: head-and-shoulders cutout masks with graceful fallback."""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.album.face_segmenter import (
    apply_mask,
    cutout_from_faces,
    feather_mask,
    segment_face_region,
)


def test_mask_covers_face_center_and_excludes_far_corner():
    size = (400, 400)
    face = (0.4, 0.2, 0.2, 0.2)  # centered-ish face
    mask = segment_face_region(size, face, feather=0.0)
    assert mask is not None
    arr = np.asarray(mask)
    # Face centre is opaque.
    cx, cy = int((0.4 + 0.1) * 400), int((0.2 + 0.1) * 400)
    assert arr[cy, cx] > 200
    # A far corner is transparent.
    assert arr[5, 5] < 40


def test_small_face_falls_back_to_none():
    # A tiny face (< 1% of frame) is unreliable -> None so caller uses a shape clip.
    assert segment_face_region((400, 400), (0.5, 0.5, 0.05, 0.05)) is None


def test_feather_softens_edge():
    hard = Image.new("L", (100, 100), 0)
    from PIL import ImageDraw

    ImageDraw.Draw(hard).ellipse([30, 30, 70, 70], fill=255)
    soft = feather_mask(hard, feather=0.1)
    a = np.asarray(hard).astype(int)
    b = np.asarray(soft).astype(int)
    # Feathering introduces intermediate (grey) alpha values a hard mask lacks.
    mids = ((b > 20) & (b < 235)).sum()
    assert mids > ((a > 20) & (a < 235)).sum()


def test_apply_mask_sets_alpha():
    img = Image.new("RGB", (50, 50), (10, 120, 200))
    mask = Image.new("L", (50, 50), 0)
    mask.paste(255, (10, 10, 40, 40))
    out = apply_mask(img, mask)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0     # outside mask -> transparent
    assert out.getpixel((25, 25))[3] == 255  # inside mask -> opaque


def test_cutout_from_faces_unions_and_falls_back():
    img = Image.new("RGB", (400, 400), (100, 100, 100))
    two = ((0.2, 0.3, 0.15, 0.15), (0.65, 0.3, 0.15, 0.15))
    out = cutout_from_faces(img, two)
    assert out is not None and out.mode == "RGBA"
    # Both face centres are opaque (union of two masks).
    assert out.getpixel((int(0.275 * 400), int(0.375 * 400)))[3] > 200
    assert out.getpixel((int(0.725 * 400), int(0.375 * 400)))[3] > 200
    # No faces -> None (fallback).
    assert cutout_from_faces(img, ()) is None
