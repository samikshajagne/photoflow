"""Tests for the rembg subject-cutout path (with graceful ellipse fallback)."""

from __future__ import annotations

import numpy as np
from PIL import Image

import core.album.face_segmenter as fs


def _photo(size=(400, 400)):
    return Image.new("RGB", size, (120, 120, 120))


def _canned_cutout(size=(400, 400)):
    """An RGBA where a centre disc is opaque and the rest transparent."""
    rgba = Image.new("RGBA", size, (10, 150, 200, 0))
    mask = Image.new("L", size, 0)
    from PIL import ImageDraw

    ImageDraw.Draw(mask).ellipse([size[0] * 0.3, size[1] * 0.3, size[0] * 0.7, size[1] * 0.7], fill=255)
    rgba.putalpha(mask)
    return rgba


def test_subject_cutout_none_without_rembg(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: False)
    assert fs.subject_cutout(_photo()) is None


def test_subject_cutout_uses_rembg_when_available(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: True)
    monkeypatch.setattr(fs, "_rembg_remove", lambda img, model: _canned_cutout(img.size))
    out = fs.subject_cutout(_photo(), feather=0.0)
    assert out is not None and out.mode == "RGBA"
    a = np.asarray(out.getchannel("A"))
    assert a[200, 200] > 200      # centre kept
    assert a[5, 5] < 40           # corner removed


def test_subject_cutout_rejects_degenerate_matte(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: True)
    # All-opaque = nothing removed -> reject (None).
    monkeypatch.setattr(fs, "_rembg_remove", lambda img, model: Image.new("RGBA", img.size, (0, 0, 0, 255)))
    assert fs.subject_cutout(_photo()) is None


def test_subject_cutout_graceful_on_model_error(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: True)
    def boom(img, model):
        raise RuntimeError("model download failed")
    monkeypatch.setattr(fs, "_rembg_remove", boom)
    assert fs.subject_cutout(_photo()) is None


def test_cutout_from_faces_prefers_rembg(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: True)
    monkeypatch.setattr(fs, "_rembg_remove", lambda img, model: _canned_cutout(img.size))
    # Even with no face boxes, rembg path yields a cutout.
    out = fs.cutout_from_faces(_photo(), (), feather=0.0)
    assert out is not None and out.mode == "RGBA"


def test_cutout_from_faces_falls_back_to_ellipse(monkeypatch):
    monkeypatch.setattr(fs, "rembg_available", lambda: False)
    # No rembg -> ellipse path; needs a usable face box.
    out = fs.cutout_from_faces(_photo(), ((0.35, 0.2, 0.3, 0.3),))
    assert out is not None and out.mode == "RGBA"
    # No faces + no rembg -> None (renderer uses a shape clip).
    assert fs.cutout_from_faces(_photo(), (), use_rembg=True) is None
