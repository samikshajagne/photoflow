"""Tests for orientation-aware slot matching (Phase 6b)."""

from PIL import Image

from core.album.raster import _order_by_slot_aspect, _photo_aspect
from core.album.template import SHAPE_RECT, Background, SpreadTemplate, TemplateSlot


def test_photo_aspect_portrait_vs_landscape(tmp_path):
    port = tmp_path / "p.jpg"
    Image.new("RGB", (100, 200), (0, 0, 0)).save(port)
    land = tmp_path / "l.jpg"
    Image.new("RGB", (200, 100), (0, 0, 0)).save(land)
    assert _photo_aspect(str(port)) < 1.0 < _photo_aspect(str(land))


def test_order_puts_portrait_in_tall_slot(tmp_path):
    port = str(tmp_path / "port.jpg")
    Image.new("RGB", (100, 200), (0, 0, 0)).save(port)
    land = str(tmp_path / "land.jpg")
    Image.new("RGB", (200, 100), (0, 0, 0)).save(land)

    template = SpreadTemplate(
        name="t",
        theme="classic",
        slots=(
            TemplateSlot(rect=(0.0, 0.0, 0.6, 0.3), shape=SHAPE_RECT),  # wide slot
            TemplateSlot(rect=(0.7, 0.0, 0.3, 0.9), shape=SHAPE_RECT),  # tall slot
        ),
        background=Background(type="solid", color="#FFFFFF"),
    )
    # Passed in the "wrong" order; matching should send landscape->wide slot 0,
    # portrait->tall slot 1.
    out = _order_by_slot_aspect([port, land], template, 1000, 1000)
    assert out[0] == land
    assert out[1] == port


def test_order_single_photo_unchanged(tmp_path):
    p = str(tmp_path / "a.jpg")
    Image.new("RGB", (100, 100), (0, 0, 0)).save(p)
    template = SpreadTemplate(
        name="t", theme="classic",
        slots=(TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0), shape=SHAPE_RECT),),
        background=Background(type="solid", color="#FFFFFF"),
    )
    assert _order_by_slot_aspect([p], template, 800, 800) == [p]
