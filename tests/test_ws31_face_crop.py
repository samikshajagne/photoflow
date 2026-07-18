"""
WS 3.1 regression tests: faces must survive album cropping and rendering.

Covers three layers:
- :mod:`core.album.facecrop` — the shared face-safe cover-crop geometry.
- :class:`core.album.layout.AlbumLayoutEngine` — the crop it stores per placement.
- :func:`core.album.template.render_spread` / ``_fit`` — the crop actually applied
  when a spread is rendered (previously ignored the faces entirely).
"""

from __future__ import annotations

from PIL import Image

from core.album.facecrop import face_safe_cover_crop, pad_face_boxes
from core.album.layout import AlbumLayoutEngine, AlbumSpec, PhotoItem

_EPS = 1e-6


def _crop_contains(crop, box) -> bool:
    cx, cy, cw, ch = crop
    bx, by, bw, bh = box
    return (
        cx - _EPS <= bx
        and cy - _EPS <= by
        and bx + bw <= cx + cw + _EPS
        and by + bh <= cy + ch + _EPS
    )


# --------------------------------------------------------------------------- #
# facecrop geometry
# --------------------------------------------------------------------------- #
def test_no_faces_is_centered_crop():
    # Wide (2:1) photo into a square slot, no faces -> centered horizontal slice.
    crop = face_safe_cover_crop(2.0, 1.0, ())
    cx, cy, cw, ch = crop
    assert cw == 0.5  # frame_ar 1 / photo_ar 2
    assert ch == 1.0
    assert cx == 0.25  # centered
    assert cy == 0.0


def test_wide_photo_keeps_face_on_left():
    # 2:1 photo into square slot keeps a 0.5-wide window. A face at the far left
    # must not be cropped away by the default centered window (which starts at 0.25).
    face = (0.02, 0.4, 0.12, 0.2)  # near left edge
    crop = face_safe_cover_crop(2.0, 1.0, (face,))
    assert _crop_contains(crop, face)
    # A centered crop (x=0.25) would have clipped this face; face-safe shifts left.
    assert crop[0] < 0.25 + _EPS


def test_tall_photo_keeps_face_near_top():
    # 1:2 (tall) photo into square slot keeps a 0.5-tall window; a face near the
    # top must survive instead of being centered out.
    face = (0.4, 0.03, 0.2, 0.12)
    crop = face_safe_cover_crop(0.5, 1.0, (face,))
    assert _crop_contains(crop, face)
    assert crop[1] < 0.25 + _EPS


def test_padding_expands_but_stays_in_bounds():
    boxes = pad_face_boxes(((0.0, 0.0, 0.1, 0.1), (0.95, 0.95, 0.05, 0.05)))
    for x, y, w, h in boxes:
        assert -_EPS <= x and -_EPS <= y
        assert x + w <= 1.0 + _EPS and y + h <= 1.0 + _EPS


# --------------------------------------------------------------------------- #
# layout engine stores the face-safe crop on each placement
# --------------------------------------------------------------------------- #
def _square_spec() -> AlbumSpec:
    return AlbumSpec(
        page_width_in=10,
        page_height_in=10,
        dpi=100,
        margin_in=0.0,
        bleed_in=0.0,
        double_page_spread=False,
    )


def test_engine_crop_keeps_face_visible():
    engine = AlbumLayoutEngine()
    spec = _square_spec()
    face = (0.4, 0.02, 0.2, 0.12)  # near the top of a tall photo
    item = PhotoItem(path="tall.jpg", aspect_ratio=0.5, face_boxes=(face,))
    spread = engine.layout([item], spec)[0]
    crop = spread.placements[0].crop
    # The padded head-and-shoulders region must sit inside the crop window.
    padded = pad_face_boxes((face,))[0]
    assert _crop_contains(crop, padded)


def test_engine_without_faces_matches_centered():
    engine = AlbumLayoutEngine()
    spec = _square_spec()
    item = PhotoItem(path="tall.jpg", aspect_ratio=0.5)
    crop = engine.layout([item], spec)[0].placements[0].crop
    # Tall photo, no faces -> full width, centered vertical half.
    assert crop[0] == 0.0
    assert abs(crop[2] - 1.0) < _EPS
    assert abs(crop[3] - 0.5) < _EPS
    assert abs(crop[1] - 0.25) < _EPS
